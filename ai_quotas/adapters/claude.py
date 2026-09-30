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

Accounts (30 Sep 2026): the probed login's email comes from
GET /api/oauth/profile with the same token (null when that call fails). The
default login above is ``account_default``. Extra logins are homes listed in
``AI_QUOTAS_CLAUDE_HOMES`` (os.pathsep-separated), else every Orca-managed
``claude-accounts/*/auth`` dir. An Orca home keeps its credentials in the
Keychain item ``Orca Claude Code Managed Credentials`` (account = the dir's
account id); any other home is a ``CLAUDE_CONFIG_DIR`` (Keychain
``Claude Code-credentials-<sha256(dir)[:8]>`` or ``<dir>/.credentials.json``).
The caller's ``CLAUDE_CONFIG_DIR`` is never inherited. An expired stored
token is reported ``unavailable``: refreshing would rotate the refresh token
Orca or the CLI still holds and log it out.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_quotas.accounts import normalize_account
from ai_quotas.boosts import boost_row, extract_boosts
from ai_quotas.notify import env_or_dotenv
from ai_quotas.reset_credits import credit_row, error_row, unavailable_row

PROVIDER = "claude"
UA = "ai-quotas/claude"
CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
KEYCHAIN_SERVICE = "Claude Code-credentials"
HOMES_ENV = "AI_QUOTAS_CLAUDE_HOMES"
ORCA_CLAUDE_ACCOUNTS = Path.home() / "Library" / "Application Support" / "orca" / "claude-accounts"
ORCA_KEYCHAIN_SERVICE = "Orca Claude Code Managed Credentials"
ORCA_MARKER = ".orca-managed-claude-auth"
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


def _read_keychain_oauth(
    service: str = KEYCHAIN_SERVICE, account: str | None = None
) -> dict[str, Any] | None:
    """The live CLI keeps its FRESH credentials here; the file copy may go stale."""
    cmd = ["security", "find-generic-password", "-s", service]
    if account:
        cmd += ["-a", account]
    try:
        proc = subprocess.run(
            cmd + ["-w"],
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


def _read_file_oauth(path: Path | None = None) -> dict[str, Any] | None:
    path = path or CREDS_PATH
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
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


def _fetch_usage(token: str, url: str = USAGE_URL) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
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


def _profile_email(token: str) -> str | None:
    """Email of the login this token belongs to; None when the call fails."""
    try:
        data = _fetch_usage(token, PROFILE_URL)
    except Exception:
        return None
    account = data.get("account") if isinstance(data, dict) else None
    return normalize_account(account.get("email")) if isinstance(account, dict) else None


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
    "oauth/usage does not list a rate-limit reset (24 Sep 2026: "
    "omelette_promotional and the other codename buckets were null)"
)

# The Claude client shows this banner. It is not in the usage payload, so a
# one-shot database row would be hidden by the next "unavailable" probe.
# Re-emit it on every sample until it expires. If the API grows a real reset
# field, that wins and this list stays quiet so the two are not double-counted.
_KNOWN_RESETS = (
    {
        "credit_id": "claude-opus-5-5-free-reset-2026-10-22",
        "title": "Reset for free · Opus 5.5",
        "expires_at": "2026-10-22T21:59:59+00:00",  # end of 22 Oct, Europe/Zurich (CEST)
        "scope": "week",
        "reason": "Claude client banner, not in oauth/usage: extra reset to explore Opus 5.5, expires Oct 22",
    },
)


def _sample_time(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _reset_credit_rows(ts: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Known client-banner grants, or an explicit unavailable/error row.

    A future usage payload that grows a reset-shaped field is an error to
    look at, and the hardcoded grant is not added beside it.
    """
    for key in ("reset_credits", "rate_limit_reset_credits", "limit_resets"):
        if key in data:
            return [error_row(ts, PROVIDER, f"unexpected reset field {key!r} in usage payload")]
    when = _sample_time(ts)
    rows: list[dict[str, Any]] = []
    for grant in _KNOWN_RESETS:
        expires = datetime.fromisoformat(grant["expires_at"])
        if when >= expires:
            continue
        rows.append(
            credit_row(
                ts,
                PROVIDER,
                credit_id=grant["credit_id"],
                title=grant["title"],
                expires_at=grant["expires_at"],
                reason=grant["reason"],
                scope=grant["scope"],
            )
        )
    if rows:
        return rows
    return [unavailable_row(ts, PROVIDER, RESET_CREDIT_REASON)]


def _boost_rows(ts: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Yield kind=boost rows when the usage payload carries a temporary perk.

    The live GET /api/oauth/usage schema (CLI 2.1.260) has no boost field —
    the +50% through 13 Sep copy lives on claude.ai/settings/usage. If a
    future payload grows one, we keep it. Silence when nothing is there.
    """
    rows: list[dict[str, Any]] = []
    for item in extract_boosts(data):
        rows.append(
            boost_row(
                ts,
                PROVIDER,
                window=str(item.get("window") or "week"),
                percent=float(item["percent"]),
                ends_at=item.get("ends_at"),
                raw_text=item.get("raw_text"),
            )
        )
    return rows


def _snapshot_token(
    ts: str, token: str, plan: str | None, *, known_resets: bool
) -> tuple[list[dict[str, Any]], str | None]:
    """Usage rows for one token, plus the login email (profile endpoint)."""
    try:
        data = _fetch_usage(token)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return _fail(
                ts,
                "unavailable",
                "usage HTTP 401 — stored token rejected. Not refreshing (rotation "
                "would break the CLI). Run any `claude` command to refresh.",
            ), None
        body = exc.read(200).decode("utf-8", "replace")
        return _fail(ts, "error", f"usage HTTP {exc.code}: {body[:160]}"), None
    except Exception as exc:
        return _fail(ts, "error", f"usage request: {exc}"), None

    if not isinstance(data, dict):
        return _fail(ts, "error", "usage response is not an object"), None

    rows = _usage_rows(ts, data, plan)
    if not rows:
        return _fail(
            ts,
            "unavailable",
            "usage response had no limits[] rows and no legacy utilization buckets",
        ), None
    account = _profile_email(token)
    if known_resets:
        rows.extend(_reset_credit_rows(ts, data))
        rows.extend(_boost_rows(ts, data))
    else:
        # The client-banner grant belongs to the default login only.
        rows.append(unavailable_row(ts, PROVIDER, RESET_CREDIT_REASON))
    return rows, account


def _mark(rows: list[dict[str, Any]], *, account: str | None, default: bool) -> list[dict[str, Any]]:
    for row in rows:
        if account:
            row["account"] = account
        row["account_default"] = default
    return rows


def claude_homes() -> list[Path]:
    """Extra Claude logins (the default login is not listed here)."""
    raw = env_or_dotenv(HOMES_ENV)
    if raw:
        return [Path(p.strip()).expanduser() for p in raw.split(os.pathsep) if p.strip()]
    try:
        return sorted(d for d in ORCA_CLAUDE_ACCOUNTS.glob("*/auth") if (d / ORCA_MARKER).is_file())
    except OSError:
        return []


def home_email(home: Path) -> str | None:
    """Email recorded for the login stored in ``home`` (no network)."""
    for name, key in (("oauth-account.json", None), (".claude.json", "oauthAccount")):
        try:
            raw = json.loads((home / name).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        block = raw.get(key) if key else raw
        if isinstance(block, dict):
            email = normalize_account(block.get("emailAddress") or block.get("email"))
            if email:
                return email
    return None


def _home_oauth(home: Path) -> dict[str, Any] | None:
    """Stored OAuth for an extra home. Read-only; never refreshed."""
    marker = home / ORCA_MARKER
    if marker.is_file():
        try:
            account_id = marker.read_text().strip() or home.parent.name
        except OSError:
            account_id = home.parent.name
        return _read_keychain_oauth(ORCA_KEYCHAIN_SERVICE, account_id) or _read_file_oauth(
            home / ".credentials.json"
        )
    digest = hashlib.sha256(str(home).encode()).hexdigest()[:8]
    return _read_keychain_oauth(f"{KEYCHAIN_SERVICE}-{digest}") or _read_file_oauth(
        home / ".credentials.json"
    )


def _expired_at(oauth: dict[str, Any]) -> str:
    try:
        exp = float(oauth.get("expiresAt"))
        if exp > 1e12:
            exp /= 1000.0
        return datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown time"


def _snapshot_home(ts: str, home: Path, stored: str | None) -> list[dict[str, Any]]:
    """One extra login. Failures carry the login recorded in ``home``."""
    oauth = _home_oauth(home)
    where = "Orca" if (home / ORCA_MARKER).is_file() else "its CLI"
    if not oauth or not oauth.get("accessToken"):
        rows = _fail(ts, "unavailable", f"no stored credentials for this login ({home.name})")
        return _mark(rows, account=stored, default=False)
    if _token_expired(oauth):
        rows = _fail(
            ts,
            "unavailable",
            f"stored token for this login expired {_expired_at(oauth)}; not refreshing "
            f"(rotation would log {where} out). Use this login once in {where} to refresh it.",
        )
        return _mark(rows, account=stored, default=False)
    rows, account = _snapshot_token(ts, str(oauth["accessToken"]), _plan_label(oauth), known_resets=False)
    return _mark(rows, account=account or stored, default=False)


def _snapshot_default(ts: str) -> list[dict[str, Any]]:
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
        return _mark(_fail(ts, "error", f"auth/token: {exc}"), account=None, default=True)
    rows, account = _snapshot_token(ts, token, plan, known_resets=True)
    return _mark(rows, account=account, default=True)


def snapshot(ts: str, *, homes: list[Path] | None = None) -> list[dict]:
    """Return claude quota rows for the default login and every extra home. Never raises."""
    try:
        rows = _snapshot_default(ts)
        seen = {normalize_account(r.get("account")) for r in rows} - {None}
        for home in homes if homes is not None else claude_homes():
            try:
                stored = home_email(home)
                if stored and stored in seen:
                    continue  # same login as one already probed
                extra = _snapshot_home(ts, home, stored)
                account = next((normalize_account(r.get("account")) for r in extra if r.get("account")), None)
                if not account or account in seen:
                    # Unattributable rows would land on the default login's series.
                    continue
                rows.extend(extra)
                seen.add(account)
            except Exception as exc:
                account = home_email(home)
                if account and account not in seen:
                    rows.extend(_mark(_fail(ts, "error", f"extra login: {exc}"),
                                      account=account, default=False))
        return rows
    except Exception as exc:
        return _fail(ts, "error", f"unexpected: {exc}")


if __name__ == "__main__":
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for row in snapshot(now):
        print(json.dumps(row, ensure_ascii=False))
