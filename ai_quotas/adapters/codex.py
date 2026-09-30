"""Codex (OpenAI CLI) quota adapter — live via codexbar, offline fallback.

Primary source (08 Aug 2026):
  `codexbar usage --provider codex --format json`  (OAuth web dashboard)

Fallback (legacy, only as fresh as last codex turn):
  last object containing `rate_limits` in the newest
  ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl

Accounts (30 Sep 2026): codexbar follows ``CODEX_HOME``. Orca terminals export
a managed home logged in as a second subscription, so a probe that inherited
the caller's env reported another login. The adapter never inherits
``CODEX_HOME``: it runs codexbar once per configured home with ``CODEX_HOME``
set explicitly. Homes: ``AI_QUOTAS_CODEX_HOMES`` (os.pathsep-separated; the
first is the default login), else ``~/.codex`` plus every Orca
``codex-accounts/*/home`` with an auth.json. Duplicate logins are probed once.
Every row carries ``account`` (codexbar identity email) and
``account_default`` (True for the first home).

Offline past-reset guard: if the rollout snapshot's resets_at is already past,
emit status=unavailable instead of a confident stale used% (that false alarm
produced "use less Codex · reset soon" after weekly rollover).

Contract: snapshot(ts) never raises; never fabricate used_percent: 0 on failure.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_quotas.accounts import normalize_account
from ai_quotas.notify import env_or_dotenv
from ai_quotas.reset_credits import credit_row, none_row, unavailable_row

PROVIDER = "codex"
DEFAULT_HOME = Path.home() / ".codex"
SESSIONS_ROOT = DEFAULT_HOME / "sessions"
HOMES_ENV = "AI_QUOTAS_CODEX_HOMES"
ORCA_CODEX_ACCOUNTS = Path.home() / "Library" / "Application Support" / "orca" / "codex-accounts"
CREDITS_WINDOW = "credits_balance"
CODEXBAR_TIMEOUT_S = 8.0
# Prefer CODEXBAR_BIN → PATH → common Homebrew location.
DEFAULT_CODEXBAR = (
    os.environ.get("CODEXBAR_BIN")
    or shutil.which("codexbar")
    or "/opt/homebrew/bin/codexbar"
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
        "limit": None,
        "used": None,
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


def _window_name(window_minutes: Any) -> str:
    try:
        minutes = int(window_minutes)
    except (TypeError, ValueError):
        return "unknown"
    if minutes == 300 or 240 <= minutes <= 360:
        return "5h"
    if minutes == 10080 or 9000 <= minutes <= 11000:
        return "week"
    if minutes % (24 * 60) == 0 and minutes > 0:
        days = minutes // (24 * 60)
        return f"{days}d" if days != 7 else "week"
    if minutes % 60 == 0 and minutes > 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _resets_at_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        # Normalize Z → +00:00 so downstream parsers are consistent.
        if value.endswith("Z"):
            return value[:-1] + "+00:00"
        return value
    try:
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _is_past(resets_at: str | None, *, now: datetime | None = None) -> bool:
    if not resets_at:
        return False
    try:
        dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return dt < ref


# ---------------------------------------------------------------------------
# Live: codexbar
# ---------------------------------------------------------------------------


def _bucket_from_codexbar_window(
    ts: str,
    bucket: dict[str, Any] | None,
    *,
    plan: str | None,
    fallback_window: str,
    reason: str,
) -> dict[str, Any] | None:
    """Map one codexbar window object (primary/secondary/extra) → contract row."""
    if not isinstance(bucket, dict):
        return None
    # codexbar uses camelCase; tolerate snake_case too.
    percent = bucket.get("usedPercent", bucket.get("used_percent"))
    if percent is None:
        return None
    try:
        used_percent = float(percent)
    except (TypeError, ValueError):
        return _row(
            ts,
            window=fallback_window,
            used_percent=None,
            plan=plan,
            status="error",
            reason=f"non-numeric usedPercent: {percent!r}",
        )
    mins = bucket.get("windowMinutes", bucket.get("window_minutes"))
    window = _window_name(mins) if mins is not None else fallback_window
    resets = _resets_at_iso(bucket.get("resetsAt", bucket.get("resets_at")))
    return _row(
        ts,
        window=window,
        used_percent=used_percent,
        resets_at=resets,
        plan=plan,
        status="ok",
        reason=reason,
    )


def _parse_codexbar_payload(ts: str, payload: Any) -> list[dict[str, Any]] | None:
    """Return contract rows from codexbar JSON, or None if unusable."""
    if isinstance(payload, list):
        if not payload:
            return None
        entry = payload[0]
    elif isinstance(payload, dict):
        entry = payload
    else:
        return None
    if not isinstance(entry, dict):
        return None
    usage = entry.get("usage")
    if not isinstance(usage, dict):
        return None

    plan = usage.get("loginMethod") or (usage.get("identity") or {}).get("loginMethod")
    if plan is not None:
        plan = str(plan)
    source = entry.get("source") or "codexbar"
    reason = f"live via codexbar ({source})"

    rows: list[dict[str, Any]] = []
    for key, fallback in (("primary", "5h"), ("secondary", "week"), ("tertiary", "tertiary")):
        row = _bucket_from_codexbar_window(
            ts, usage.get(key), plan=plan, fallback_window=fallback, reason=reason
        )
        if row is not None and row.get("status") == "ok":
            rows.append(row)

    # Extra named windows (e.g. Codex Spark Weekly) — skip if same window already present.
    seen = {(r["window"], r["used_percent"]) for r in rows}
    extras = usage.get("extraRateWindows") or []
    if isinstance(extras, list):
        for extra in extras:
            if not isinstance(extra, dict):
                continue
            win_obj = extra.get("window") if isinstance(extra.get("window"), dict) else extra
            title = str(extra.get("id") or extra.get("title") or "extra")
            row = _bucket_from_codexbar_window(
                ts,
                win_obj,
                plan=plan,
                fallback_window=title,
                reason=reason,
            )
            if row is None or row.get("status") != "ok":
                continue
            key = (row["window"], row["used_percent"])
            # Avoid duplicate week row when spark weekly mirrors secondary.
            if any(r["window"] == row["window"] for r in rows):
                continue
            if key in seen:
                continue
            rows.append(row)

    if rows:
        balance = _credits_balance_row(ts, entry, plan=plan)
        if balance is not None:
            rows.append(balance)
        rows.extend(_reset_credit_rows(ts, usage))
        account = _account_email(usage)
        for row in rows:
            row["account"] = account
    return rows or None


def _account_email(usage: dict[str, Any]) -> str | None:
    """CodexBar identity email, so a probe of another login is not a redemption."""
    ident = usage.get("identity") if isinstance(usage.get("identity"), dict) else {}
    for value in (ident.get("accountEmail"), ident.get("email"), usage.get("accountEmail")):
        email = normalize_account(value)
        if email:
            return email
    return None


def _credits_balance_row(ts: str, entry: dict[str, Any], *, plan: str | None) -> dict[str, Any] | None:
    """codexbar ``credits.remaining`` → one ``credits_balance`` row (no $: OpenAI
    prices credits per token by model, so a fixed rate would be invented)."""
    block = entry.get("credits")
    if not isinstance(block, dict) or block.get("balanceReadSucceeded") is False:
        return None
    try:
        remaining = float(block.get("remaining"))
    except (TypeError, ValueError):
        return None
    row = _row(ts, window=CREDITS_WINDOW, used_percent=None, plan=plan,
               reason="codexbar credits balance")
    row["remaining"] = remaining
    row["unit"] = "credits"
    if block.get("balanceIsWorkspace") is not None:
        row["workspace_balance"] = bool(block.get("balanceIsWorkspace"))
    return row


def _reset_credit_rows(ts: str, usage: dict[str, Any]) -> list[dict[str, Any]]:
    """codexbar ``usage.codexResetCredits`` → reset-credit rows.

    Shape (codexbar 04 Sep 2026)::

        {"credits": [{"id", "title", "description", "reset_type",
                      "status": "available", "granted_at", "expires_at"}],
         "availableCount": 1, "updatedAt": ...}
    """
    account = _account_email(usage)
    block = usage.get("codexResetCredits", usage.get("codex_reset_credits"))
    if not isinstance(block, dict):
        return [unavailable_row(ts, PROVIDER, "codexbar payload has no codexResetCredits")]
    items = block.get("credits")
    if not isinstance(items, list):
        return [none_row(ts, PROVIDER, "codexResetCredits.credits missing", account=account)]
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if not isinstance(cid, str) or not cid:
            continue
        status = str(item.get("status") or "available").lower()
        if status != "available":
            # codexbar only lists redeemable credits today; keep others visible
            # as non-available answers without inventing an id-less row.
            continue
        out.append(
            credit_row(
                ts,
                PROVIDER,
                credit_id=cid,
                title=str(item.get("title") or "Full reset"),
                granted_at=_resets_at_iso(item.get("granted_at")),
                expires_at=_resets_at_iso(item.get("expires_at")),
                status="available",
                reason=str(item.get("reset_type") or "codex_rate_limits"),
                scope="week",
                account=account,
            )
        )
    return out or [none_row(ts, PROVIDER, "codexbar lists no available reset credit", account=account)]


def _snapshot_codexbar(
    ts: str,
    *,
    codexbar_bin: str | None = None,
    codexbar_json: str | bytes | None = None,
    home: Path | None = None,
) -> list[dict[str, Any]] | None:
    """Live snapshot. Returns None to signal caller should try offline fallback."""
    if codexbar_json is not None:
        try:
            payload = json.loads(codexbar_json)
        except (TypeError, json.JSONDecodeError) as exc:
            return _fail(ts, "error", f"codexbar fixture JSON invalid: {exc}")
        rows = _parse_codexbar_payload(ts, payload)
        return rows if rows is not None else _fail(
            ts, "unavailable", "codexbar fixture had no usable windows"
        )

    bin_path = codexbar_bin or DEFAULT_CODEXBAR
    if not bin_path or not Path(bin_path).exists():
        return None  # soft miss → offline
    # Never inherit the caller's CODEX_HOME (an Orca terminal points it at
    # another login); always name the home being probed.
    env = dict(os.environ)
    env["CODEX_HOME"] = str(home if home is not None else DEFAULT_HOME)
    try:
        proc = subprocess.run(
            [bin_path, "usage", "--provider", "codex", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=CODEXBAR_TIMEOUT_S,
            check=False,
            env=env,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return _fail(ts, "error", f"codexbar timed out after {CODEXBAR_TIMEOUT_S}s")
    except OSError as exc:
        return _fail(ts, "error", f"codexbar spawn failed: {exc}")

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        return _fail(
            ts,
            "error",
            f"codexbar exit {proc.returncode}" + (f": {err}" if err else ""),
        )
    raw = (proc.stdout or "").strip()
    if not raw:
        return _fail(ts, "unavailable", "codexbar returned empty stdout")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _fail(ts, "error", f"codexbar JSON invalid: {exc}")

    rows = _parse_codexbar_payload(ts, payload)
    if rows is None:
        return _fail(ts, "unavailable", "codexbar JSON had no usable rate windows")
    return rows


# ---------------------------------------------------------------------------
# Offline: session rollout jsonl
# ---------------------------------------------------------------------------


def _newest_rollout(sessions_root: Path) -> Path | None:
    if not sessions_root.is_dir():
        return None
    newest: Path | None = None
    newest_mtime = -1.0
    for path in sessions_root.rglob("rollout-*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest_mtime = mtime
            newest = path
    return newest


def _extract_rate_limits(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        if isinstance(obj.get("rate_limits"), dict):
            return obj["rate_limits"]
        for v in obj.values():
            found = _extract_rate_limits(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _extract_rate_limits(item)
            if found is not None:
                return found
    return None


def _last_rate_limits(path: Path) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "rate_limits" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                found = _extract_rate_limits(obj)
                if found is not None:
                    last = found
    except OSError:
        return None
    return last


def _bucket_row_offline(
    ts: str,
    bucket: dict[str, Any] | None,
    *,
    plan: str | None,
    fallback_window: str,
    reason: str,
) -> dict[str, Any] | None:
    if not isinstance(bucket, dict):
        return None
    percent = bucket.get("used_percent")
    if percent is None:
        return None
    try:
        used_percent = float(percent)
    except (TypeError, ValueError):
        return _row(
            ts,
            window=fallback_window,
            used_percent=None,
            plan=plan,
            status="error",
            reason=f"non-numeric used_percent: {percent!r}",
        )
    window = (
        _window_name(bucket.get("window_minutes"))
        if bucket.get("window_minutes") is not None
        else fallback_window
    )
    return _row(
        ts,
        window=window,
        used_percent=used_percent,
        resets_at=_resets_at_iso(bucket.get("resets_at")),
        plan=plan,
        status="ok",
        reason=reason,
    )


def _snapshot_offline(
    ts: str,
    *,
    sessions_root: Path | None = None,
) -> list[dict[str, Any]]:
    root = sessions_root if sessions_root is not None else SESSIONS_ROOT
    if not root.is_dir():
        return _fail(ts, "unavailable", f"missing sessions dir: {root}")
    newest = _newest_rollout(root)
    if newest is None:
        return _fail(ts, "unavailable", f"no rollout-*.jsonl under {root}")
    rate = _last_rate_limits(newest)
    if rate is None:
        return _fail(
            ts,
            "unavailable",
            f"no rate_limits line in newest rollout: {newest.name}",
        )
    plan = rate.get("plan_type")
    if plan is not None:
        plan = str(plan)
    reason = (
        f"offline rollout {newest.name}; only as fresh as last codex turn"
    )
    rows: list[dict[str, Any]] = []
    for key, fallback in (("primary", "primary"), ("secondary", "secondary")):
        row = _bucket_row_offline(
            ts, rate.get(key), plan=plan, fallback_window=fallback, reason=reason
        )
        if row is not None:
            rows.append(row)

    if not rows:
        return _fail(
            ts,
            "unavailable",
            "rate_limits present but primary/secondary missing used_percent",
        )

    # Stale-guard: do not report ok used% for a window whose reset already passed.
    live_rows: list[dict[str, Any]] = []
    stale_notes: list[str] = []
    for row in rows:
        if row.get("status") == "ok" and _is_past(row.get("resets_at")):
            stale_notes.append(
                f"{row.get('window')}@{row.get('used_percent')}% resets_at past"
            )
            continue
        live_rows.append(row)

    if live_rows:
        return live_rows
    detail = "; ".join(stale_notes) if stale_notes else "past reset"
    return _fail(
        ts,
        "unavailable",
        f"offline snapshot past reset ({detail}) — codexbar failed or missing; "
        "run a codex turn or fix codexbar",
    )


def codex_homes() -> list[Path]:
    """Configured Codex homes; the first one is the default login."""
    raw = env_or_dotenv(HOMES_ENV)
    if raw:
        homes = [Path(p.strip()).expanduser() for p in raw.split(os.pathsep) if p.strip()]
    else:
        homes = [DEFAULT_HOME]
        try:
            homes += sorted(
                d for d in ORCA_CODEX_ACCOUNTS.glob("*/home") if (d / "auth.json").is_file()
            )
        except OSError:
            pass
    out: list[Path] = []
    seen: set[str] = set()
    for home in homes:
        key = str(home.resolve()) if home.exists() else str(home)
        if key not in seen:
            seen.add(key)
            out.append(home)
    return out


def home_email(home: Path) -> str | None:
    """Email claim of the login stored in ``<home>/auth.json`` (id_token payload).

    Only the unsigned claims are decoded; no token leaves this function.
    """
    try:
        raw = json.loads((home / "auth.json").read_text())
        token = (raw.get("tokens") or {}).get("id_token")
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    return normalize_account(claims.get("email")) if isinstance(claims, dict) else None


def _mark(rows: list[dict[str, Any]], *, account: str | None, default: bool) -> list[dict[str, Any]]:
    for row in rows:
        if account and not row.get("account"):
            row["account"] = account
        row["account_default"] = default
    return rows


def _snapshot_default(
    ts: str,
    *,
    home: Path,
    sessions_dir: Path | None,
    codexbar_bin: str | None,
    codexbar_json: str | bytes | None,
    use_codexbar: bool,
) -> list[dict[str, Any]]:
    """The default login: live first, offline rollout fallback."""
    sessions = sessions_dir
    if sessions is None:
        sessions = SESSIONS_ROOT if home == DEFAULT_HOME else home / "sessions"
    if use_codexbar:
        live = _snapshot_codexbar(
            ts, codexbar_bin=codexbar_bin, codexbar_json=codexbar_json, home=home
        )
        if live is not None:
            # Prefer ok rows; if live returned only errors, still try offline
            # so a bad codexbar doesn't blank quota entirely.
            if any(r.get("status") == "ok" for r in live):
                return live
            offline = _snapshot_offline(ts, sessions_root=sessions)
            if any(r.get("status") == "ok" for r in offline):
                return offline
            # Prefer the live error reason (more actionable) if offline also failed.
            return live

    offline = _snapshot_offline(ts, sessions_root=sessions)
    if any(r.get("status") == "ok" for r in offline):
        offline.append(
            unavailable_row(
                ts, PROVIDER, "offline rollout snapshot carries no reset credits (codexbar needed)"
            )
        )
    return offline


def snapshot(
    ts: str,
    *,
    sessions_dir: Path | None = None,
    codexbar_bin: str | None = None,
    codexbar_json: str | bytes | None = None,
    use_codexbar: bool | None = None,
    homes: list[Path] | None = None,
) -> list[dict]:
    """Return codex quota rows for every configured login. Never raises.

    kwargs (test / override hooks; production callers use defaults):
      sessions_dir   — override ~/.codex/sessions (also forces offline-only when set
                       unless codexbar_json / use_codexbar=True is explicit)
      codexbar_bin   — path to codexbar binary
      codexbar_json  — inject raw codexbar JSON for the default login (skip subprocess;
                       extra homes are not probed)
      use_codexbar   — force live on/off; default: on unless sessions_dir alone
      homes          — Codex homes to probe (default: :func:`codex_homes`)
    """
    try:
        # Default: live first. Explicit sessions_dir for tests → offline only,
        # unless caller also injects codexbar_json or sets use_codexbar=True.
        if use_codexbar is None:
            use_codexbar = codexbar_json is not None or sessions_dir is None
        injected = codexbar_json is not None or sessions_dir is not None
        all_homes = list(homes) if homes is not None else ([DEFAULT_HOME] if injected else codex_homes())
        if not all_homes:
            all_homes = [DEFAULT_HOME]

        default_home = all_homes[0]
        rows = _snapshot_default(
            ts,
            home=default_home,
            sessions_dir=sessions_dir,
            codexbar_bin=codexbar_bin,
            codexbar_json=codexbar_json,
            use_codexbar=use_codexbar,
        )
        seen = {a for a in (_rows_account(rows), home_email(default_home)) if a}
        _mark(rows, account=_rows_account(rows), default=True)
        if not use_codexbar or injected:
            return rows
        for home in all_homes[1:]:
            stored = home_email(home)
            if stored and stored in seen:
                continue  # same login as a home already probed
            live = _snapshot_codexbar(ts, codexbar_bin=codexbar_bin, home=home)
            if live is None:
                continue  # no codexbar binary: nothing to probe extra homes with
            account = _rows_account(live) or stored
            if not account or account in seen:
                # Unattributable rows would land on the default login's series.
                continue
            rows.extend(_mark(live, account=account, default=False))
            seen.add(account)
        return rows
    except Exception as exc:
        return _fail(ts, "error", f"unexpected: {exc}")


def _rows_account(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        acct = normalize_account(row.get("account"))
        if acct:
            return acct
    return None


if __name__ == "__main__":
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for row in snapshot(now):
        print(json.dumps(row, ensure_ascii=False))
