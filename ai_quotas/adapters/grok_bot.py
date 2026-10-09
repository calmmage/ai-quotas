"""Native Grok Bot weekly allowance, via CodexBar's Cursor session.

``codexbar usage --provider cursor --format json`` includes an extra window
``cursor-grok-bot`` (title "Grok Bot"). That pool is the Electron app
Grok Bot, billed through Cursor. It is not the Grok CLI week
(``adapters/grok.py``) and not Cursor's own plan bars. Those plan bars are
ignored: a 0% Cursor IDE window is not the Bot.

Contract: snapshot(ts) never raises; never fabricate used_percent 0 on failure.
A genuine 0% from the Bot window is status=ok.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_quotas.accounts import normalize_account

PROVIDER = "grok-bot"
WINDOW = "week"
BOT_IDS = {"cursor-grok-bot"}
BOT_TITLES = {"grok bot"}
CODEXBAR_TIMEOUT_S = 25.0
# Prefer CODEXBAR_BIN → PATH → common Homebrew location. Same binary the
# Codex adapter uses; this probe asks it for the Cursor session only.
DEFAULT_CODEXBAR = (
    os.environ.get("CODEXBAR_BIN")
    or shutil.which("codexbar")
    or "/opt/homebrew/bin/codexbar"
)


def _row(
    ts: str,
    *,
    used_percent: float | None,
    resets_at: str | None = None,
    status: str = "ok",
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "provider": PROVIDER,
        "window": WINDOW if status == "ok" else "unknown",
        "used_percent": used_percent,
        "resets_at": resets_at,
        "plan": None,
        "status": status,
        "reason": reason,
        "limit": None,
        "used": None,
    }


def _fail(ts: str, status: str, reason: str) -> list[dict[str, Any]]:
    return [_row(ts, used_percent=None, status=status, reason=reason)]


def _resets_at_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value.endswith("Z"):
            return value[:-1] + "+00:00"
        return value
    try:
        stamp = float(value)
        if stamp > 1e12:
            stamp /= 1000.0
        return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _account_email(usage: dict[str, Any]) -> str | None:
    ident = usage.get("identity") if isinstance(usage.get("identity"), dict) else {}
    for value in (ident.get("accountEmail"), ident.get("email"), usage.get("accountEmail")):
        email = normalize_account(value)
        if email:
            return email
    return None


def _is_bot_extra(extra: dict[str, Any]) -> bool:
    ident = str(extra.get("id") or "").strip().lower()
    title = str(extra.get("title") or "").strip().lower()
    return ident in BOT_IDS or title in BOT_TITLES


def _bot_window(usage: dict[str, Any]) -> dict[str, Any] | None:
    extras = usage.get("extraRateWindows") or []
    if not isinstance(extras, list):
        return None
    for extra in extras:
        if not isinstance(extra, dict) or not _is_bot_extra(extra):
            continue
        window = extra.get("window")
        return window if isinstance(window, dict) else extra
    return None


def _error_message(entry: dict[str, Any]) -> str | None:
    err = entry.get("error")
    if isinstance(err, dict):
        text = err.get("message") or err.get("kind") or err.get("code")
        return str(text) if text else "codexbar cursor error"
    if isinstance(err, str) and err.strip():
        return err.strip()
    return None


def _entries(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return None


def _parse_payload(ts: str, payload: Any) -> list[dict[str, Any]]:
    entries = _entries(payload)
    if entries is None:
        return _fail(ts, "error", "codexbar cursor JSON was not an object")
    if not entries:
        return _fail(ts, "unavailable", "codexbar cursor JSON was empty")

    rows: list[dict[str, Any]] = []
    saw_usage = False
    for entry in entries:
        message = _error_message(entry)
        usage = entry.get("usage")
        if not isinstance(usage, dict):
            if message and not saw_usage and not rows:
                return _fail(ts, "error", f"codexbar cursor: {message[:200]}")
            continue
        saw_usage = True
        account = _account_email(usage)
        window = _bot_window(usage)
        if window is None:
            continue
        percent = window.get("usedPercent", window.get("used_percent"))
        source = entry.get("source") or "codexbar"
        reason = f"live via codexbar cursor extra cursor-grok-bot ({source})"
        if percent is None:
            row = _row(
                ts,
                used_percent=None,
                status="unavailable",
                reason="Grok Bot window has no usedPercent",
            )
        else:
            try:
                used = float(percent)
            except (TypeError, ValueError):
                row = _row(
                    ts,
                    used_percent=None,
                    status="error",
                    reason=f"non-numeric Grok Bot usedPercent: {percent!r}",
                )
            else:
                # Keep the vendor figure. 0.644955 is under 1%, not 0 and not 1.
                row = _row(
                    ts,
                    used_percent=used,
                    resets_at=_resets_at_iso(window.get("resetsAt", window.get("resets_at"))),
                    status="ok",
                    reason=reason,
                )
                row["window"] = WINDOW
        row["account"] = account
        rows.append(row)

    if rows:
        return rows
    if not saw_usage:
        return _fail(ts, "unavailable", "codexbar cursor payload has no usage")
    return _fail(ts, "unavailable", "cursor payload has no Grok Bot window")


def snapshot(
    ts: str,
    *,
    codexbar_bin: str | None = None,
    codexbar_json: str | bytes | None = None,
) -> list[dict]:
    """Return Grok Bot quota rows. Never raises.

    ``codexbar_json`` is a test fixture. The collector calls ``snapshot(ts)``.
    """
    try:
        if codexbar_json is not None:
            try:
                payload = json.loads(codexbar_json)
            except (TypeError, json.JSONDecodeError) as exc:
                return _fail(ts, "error", f"codexbar fixture JSON invalid: {exc}")
            return _parse_payload(ts, payload)

        bin_path = codexbar_bin or DEFAULT_CODEXBAR
        if not bin_path or not Path(bin_path).exists():
            return _fail(ts, "unavailable", f"codexbar not found: {bin_path}")
        try:
            proc = subprocess.run(
                [bin_path, "usage", "--provider", "cursor", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=CODEXBAR_TIMEOUT_S,
                check=False,
            )
        except FileNotFoundError:
            return _fail(ts, "unavailable", f"codexbar not found: {bin_path}")
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
        return _parse_payload(ts, payload)
    except Exception as exc:
        return _fail(ts, "error", f"unexpected: {exc}")
