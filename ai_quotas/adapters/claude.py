"""Claude (Anthropic CLI) quota adapter.

Source:
  GET https://api.anthropic.com/api/oauth/usage
  Authorization: Bearer <claudeAiOauth.accessToken>
  anthropic-beta: oauth-2025-04-20

Primary signal is the ``limits`` array (session / weekly_all / weekly_scoped).
Legacy top-level buckets (five_hour, seven_day, …) are a fallback only when a
window is missing from limits. ``percent`` / ``utilization`` are percent USED
(never invert). Scoped weekly rows become ``week_<model_display_name>`` so Fable
shows as ``week_fable``. ``spend`` becomes an ``overage_credits`` row.

Creds, freshest-wins: macOS Keychain `Claude Code-credentials` (kept current by the
running CLI) OR ~/.claude/.credentials.json (may go stale) → claudeAiOauth.accessToken

READ-ONLY BY DESIGN — this adapter never refreshes and never writes credentials.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_quotas.reset_credits import error_row, unavailable_row

PROVIDER = "claude"
UA = "ai-quotas/claude"
CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
BETA_HEADER = "oauth-2025-04-20"

BUCKET_WINDOWS = (
    ("five_hour", "5h"),
    ("seven_day", "week"),
    ("seven_day_opus", "week_opus"),
    ("seven_day_sonnet", "week_sonnet"),
)


def _row(
    ts: str,
    *,
    window: str,
    used_percent: float | None,
    resets_at: str | None = None,
    plan: str | None = None,
    status: str = "ok",
    reason: str | None = None,
    limit: int | float | None = None,
    used: int | float | None = None,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "provider": PROVIDER,
        "window": window,
        "used_percent": used_percent,
        "resets_at": resets_at,
        "plan": plan,
        "status": status,
        "reason": reason,
        "limit": limit,
        "used": used,
    }


def _fail(ts: str, status: str, reason: str) -> list[dict[str, Any]]:
    return [
        _row(
            ts,
            window="unknown",
            used_percent=None,
            status=status,
            reason=reason,
        )
    ]


def _read_keychain_oauth() -> dict[str, Any] | None:
    """The live CLI keeps its FRESH credentials here; the file copy may go stale."""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        data = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return None
    oauth = data.get("claudeAiOauth")
    return oauth if isinstance(oauth, dict) else None


def _read_file_oauth() -> dict[str, Any] | None:
    if not CREDS_PATH.exists():
        return None
    try:
        raw = json.loads(CREDS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    oauth = raw.get("claudeAiOauth") if isinstance(raw, dict) else None
    return oauth if isinstance(oauth, dict) else None


def _load_oauth() -> dict[str, Any]:
    """Prefer whichever store holds the fresher token."""
    kc, fl = _read_keychain_oauth(), _read_file_oauth()
    if kc and fl:
        return kc if (kc.get("expiresAt") or 0) >= (fl.get("expiresAt") or 0) else fl
    if kc:
        return kc
    if fl:
        return fl
    raise RuntimeError(
        f"no Claude OAuth creds (file {CREDS_PATH}, keychain {KEYCHAIN_SERVICE!r})"
    )


def _plan_label(oauth: dict[str, Any]) -> str | None:
    sub = oauth.get("subscriptionType")
    tier = oauth.get("rateLimitTier")
    parts = [str(p) for p in (sub, tier) if p]
    return "+".join(parts) if parts else None


def _token_expired(oauth: dict[str, Any]) -> bool:
    exp = oauth.get("expiresAt")
    if exp is None:
        return False
    try:
        exp_ms = float(exp)
        if exp_ms < 1e12:
            exp_ms *= 1000.0
        return datetime.now(timezone.utc).timestamp() * 1000 >= exp_ms - 60_000
    except (TypeError, ValueError):
        return False


def _get_access_token() -> tuple[str, str | None]:
    """Read-only: use the token the live CLI already keeps fresh. NEVER refresh.

    Refresh tokens ROTATE. This adapter cannot persist a rotated token (it must not write
    shared credentials), so refreshing here would burn the refresh token that the real
    ``claude`` CLI depends on. Keychain is kept current by the running CLI.

    If the freshest token we can see is still expired, that is an honest ``unavailable``.
    """
    oauth = _load_oauth()
    plan = _plan_label(oauth)
    access = oauth.get("accessToken")
    if not access:
        raise RuntimeError(
            "no accessToken in claudeAiOauth (refresh is deliberately not attempted)"
        )
    if _token_expired(oauth):
        raise RuntimeError(
            "freshest available access token is expired; not refreshing (rotation would "
            "break the CLI). Run any `claude` command to refresh it."
        )
    return str(access), plan


def _fetch_usage(token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        USAGE_URL,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA_HEADER,
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "scoped"


def _limit_window(entry: dict[str, Any]) -> str | None:
    kind = str(entry.get("kind") or "").lower()
    if kind == "session":
        return "5h"
    if kind == "weekly_all":
        return "week"
    if kind == "weekly_scoped":
        scope = entry.get("scope") if isinstance(entry.get("scope"), dict) else {}
        model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
        display = model.get("display_name") or model.get("id") or "scoped"
        return f"week_{_slug(str(display))}"
    if kind:
        return _slug(kind)
    return None


def _as_used_percent(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _limits_rows(ts: str, data: dict[str, Any], plan: str | None) -> list[dict[str, Any]]:
    limits = data.get("limits")
    if not isinstance(limits, list) or not limits:
        return []

    rows: list[dict[str, Any]] = []
    for entry in limits:
        if not isinstance(entry, dict):
            continue
        window = _limit_window(entry)
        if not window:
            continue
        pct = _as_used_percent(entry.get("percent"))
        if pct is None:
            continue
        resets = entry.get("resets_at")
        rows.append(
            _row(
                ts,
                window=window,
                used_percent=pct,
                resets_at=str(resets) if resets else None,
                plan=plan,
                status="ok",
            )
        )
    return rows


def _legacy_bucket_rows(
    ts: str,
    data: dict[str, Any],
    plan: str | None,
    *,
    skip_windows: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field, window in BUCKET_WINDOWS:
        if window in skip_windows:
            continue
        bucket = data.get(field)
        if bucket is None:
            continue
        if not isinstance(bucket, dict):
            rows.append(
                _row(
                    ts,
                    window=window,
                    used_percent=None,
                    plan=plan,
                    status="error",
                    reason=f"{field} is not an object",
                )
            )
            continue
        util = bucket.get("utilization")
        if util is None:
            continue
        used_percent = _as_used_percent(util)
        if used_percent is None:
            rows.append(
                _row(
                    ts,
                    window=window,
                    used_percent=None,
                    plan=plan,
                    status="error",
                    reason=f"{field}.utilization non-numeric: {util!r}",
                )
            )
            continue
        resets = bucket.get("resets_at")
        rows.append(
            _row(
                ts,
                window=window,
                used_percent=used_percent,
                resets_at=str(resets) if resets else None,
                plan=plan,
                status="ok",
            )
        )
    return rows


def _overage_row(ts: str, data: dict[str, Any], plan: str | None) -> dict[str, Any] | None:
    spend = data.get("spend")
    if isinstance(spend, dict):
        pct = _as_used_percent(spend.get("percent"))
        used_obj = spend.get("used") if isinstance(spend.get("used"), dict) else {}
        limit_obj = spend.get("limit") if isinstance(spend.get("limit"), dict) else {}
        used_minor = used_obj.get("amount_minor")
        limit_minor = limit_obj.get("amount_minor")
        try:
            used_v = int(used_minor) if used_minor is not None else None
        except (TypeError, ValueError):
            used_v = None
        try:
            limit_v = int(limit_minor) if limit_minor is not None else None
        except (TypeError, ValueError):
            limit_v = None
        if pct is None and used_v is not None and limit_v and limit_v > 0:
            pct = (used_v / limit_v) * 100.0
        if pct is None:
            return None
        reason = None
        if spend.get("enabled") is False:
            reason = str(spend.get("disabled_reason") or "overage_disabled")
        return _row(
            ts,
            window="overage_credits",
            used_percent=pct,
            plan=plan,
            status="ok",
            reason=reason,
            limit=limit_v,
            used=used_v,
        )

    extra = data.get("extra_usage")
    if isinstance(extra, dict):
        pct = _as_used_percent(extra.get("utilization"))
        used_v = extra.get("used_credits")
        limit_v = extra.get("monthly_limit")
        try:
            used_f = float(used_v) if used_v is not None else None
        except (TypeError, ValueError):
            used_f = None
        try:
            limit_f = float(limit_v) if limit_v is not None else None
        except (TypeError, ValueError):
            limit_f = None
        if pct is None and used_f is not None and limit_f and limit_f > 0:
            pct = (used_f / limit_f) * 100.0
        if pct is None:
            return None
        reason = None
        if extra.get("is_enabled") is False:
            reason = str(extra.get("disabled_reason") or "overage_disabled")
        return _row(
            ts,
            window="overage_credits",
            used_percent=pct,
            plan=plan,
            status="ok",
            reason=reason,
            limit=int(limit_f) if limit_f is not None else None,
            used=int(used_f) if used_f is not None else None,
        )
    return None


def _usage_rows(ts: str, data: dict[str, Any], plan: str | None) -> list[dict[str, Any]]:
    primary = _limits_rows(ts, data, plan)
    seen = {str(r["window"]) for r in primary if r.get("status") == "ok"}
    legacy = _legacy_bucket_rows(ts, data, plan, skip_windows=seen)
    rows = primary + legacy
    overage = _overage_row(ts, data, plan)
    if overage is not None:
        rows.append(overage)
    return rows


RESET_CREDIT_REASON = (
    "claude exposes no rate-limit reset credit (04 Sep 2026: oauth/usage, "
    "claude.ai settings and the CLI bundle only know overage credits, guest "
    "passes and temporary limit boosts)"
)


def _reset_credit_row(ts: str, data: dict[str, Any]) -> dict[str, Any]:
    """Claude has no reset credit today; record an explicit 'unavailable' so
    the dashboard says *not exposed* rather than *never checked*. If a future
    payload grows a reset-shaped field, surface it as an error to look at."""
    for key in ("reset_credits", "rate_limit_reset_credits", "limit_resets"):
        if key in data:
            return error_row(ts, PROVIDER, f"unexpected reset field {key!r} in usage payload")
    return unavailable_row(ts, PROVIDER, RESET_CREDIT_REASON)


def snapshot(ts: str) -> list[dict]:
    """Return claude quota rows. Never raises."""
    try:
        # Keychain alone is enough on macOS; file is optional fallback.
        if not CREDS_PATH.exists() and _read_keychain_oauth() is None:
            return _fail(
                ts,
                "unavailable",
                f"missing credentials: {CREDS_PATH} (and no keychain entry)",
            )
        try:
            token, plan = _get_access_token()
        except Exception as exc:
            return _fail(ts, "error", f"auth/token: {exc}")

        try:
            data = _fetch_usage(token)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return _fail(
                    ts,
                    "unavailable",
                    "usage HTTP 401 — stored token rejected. Not refreshing (rotation "
                    "would break the CLI). Run any `claude` command to refresh.",
                )
            body = exc.read(200).decode("utf-8", "replace")
            return _fail(ts, "error", f"usage HTTP {exc.code}: {body[:160]}")
        except Exception as exc:
            return _fail(ts, "error", f"usage request: {exc}")

        if not isinstance(data, dict):
            return _fail(ts, "error", "usage response is not an object")

        rows = _usage_rows(ts, data, plan)
        if not rows:
            return _fail(
                ts,
                "unavailable",
                "usage response had no limits[] rows and no legacy utilization buckets",
            )
        rows.append(_reset_credit_row(ts, data))
        return rows
    except Exception as exc:
        return _fail(ts, "error", f"unexpected: {exc}")


if __name__ == "__main__":
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for row in snapshot(now):
        print(json.dumps(row, ensure_ascii=False))
