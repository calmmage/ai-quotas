"""Orca rate-limit adapter.

Orca is not a quota vendor of its own. `orca account list --json` already
reads weekly (and sometimes session/monthly) rate limits for the CLIs it
manages. Native adapters still own Claude / Codex / Grok / Gemini. This
adapter only publishes the extras Orca can see that we do not sample
ourselves: Kimi, MiniMax, OpenCode Go, Antigravity.

stdlib only. snapshot(ts) never raises.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

PROVIDER_MAP = {
    "kimi": "kimi",
    "minimax": "minimax",
    "opencodeGo": "opencode",
    "antigravity": "antigravity",
}


def _row(
    ts: str,
    *,
    provider: str,
    window: str,
    used_percent: float | None,
    resets_at: str | None = None,
    plan: str | None = None,
    status: str = "ok",
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "provider": provider,
        "window": window,
        "used_percent": used_percent,
        "resets_at": resets_at,
        "plan": plan,
        "status": status,
        "reason": reason,
        "limit": None,
        "used": None,
    }


def _ms_to_iso(ms: Any) -> str | None:
    try:
        value = float(ms)
    except (TypeError, ValueError):
        return None
    if value > 1e12:
        value /= 1000.0
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _window_name(minutes: Any) -> str:
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return "week"
    if m >= 20000:
        return "month"
    if m >= 1440:
        return "week"
    return "session"


def _orca_payload() -> dict[str, Any]:
    binary = shutil.which("orca")
    if not binary:
        raise FileNotFoundError("orca CLI not on PATH")
    proc = subprocess.run(
        [binary, "account", "list", "--json"],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "orca account list failed").strip()
        raise RuntimeError(err[:200])
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("orca account list did not return an object")
    return data.get("result", data) if isinstance(data.get("result"), dict) else data


def snapshot(ts: str) -> list[dict]:
    """Return quota rows for Orca-tracked extras. Never raises."""
    try:
        payload = _orca_payload()
    except FileNotFoundError:
        return []
    except Exception as exc:
        return [
            _row(
                ts,
                provider=name,
                window="week",
                used_percent=None,
                status="unavailable",
                reason=f"orca: {exc}",
            )
            for name in ("kimi", "minimax", "opencode", "antigravity")
        ]
    limits = payload.get("rateLimits")
    if not isinstance(limits, dict):
        return []
    rows: list[dict] = []
    for orca_key, provider in PROVIDER_MAP.items():
        block = limits.get(orca_key)
        if not isinstance(block, dict):
            continue
        weekly = block.get("weekly") if isinstance(block.get("weekly"), dict) else None
        status = str(block.get("status") or "")
        if weekly and weekly.get("usedPercent") is not None and status == "ok":
            try:
                used = float(weekly["usedPercent"])
            except (TypeError, ValueError):
                used = None
            if used is None:
                continue
            rows.append(
                _row(
                    ts,
                    provider=provider,
                    window=_window_name(weekly.get("windowMinutes")),
                    used_percent=used,
                    resets_at=_ms_to_iso(weekly.get("resetsAt")),
                    plan=None,
                    status="ok",
                )
            )
            continue
        # Do not emit unavailable rows — the dash still shows a setup card
        # from the vendor catalog without polluting the sample log.
    return rows


if __name__ == "__main__":
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for row in snapshot(now):
        print(json.dumps(row, ensure_ascii=False))
