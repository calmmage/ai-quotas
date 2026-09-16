"""Muse Code (Meta) quota adapter.

Source: Muse Session Protocol over `muse serve` stdio (NDJSON JSON-RPC).
`usage/read` returns the last-observed 5h-class window and weekly percent
without a model call. `account/read` (experimental) tells us whether a Meta
login exists.

stdlib only. snapshot(ts) never raises. Read-only — never starts login or
writes credentials.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

PROVIDER = "muse"
SERVE_TIMEOUT = 8


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


def _window_from_minutes(minutes: Any) -> str:
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return "5h"
    if m >= 1440:
        return "week"
    return "5h"


def _used_percent(block: dict[str, Any] | None) -> float | None:
    if not isinstance(block, dict):
        return None
    raw = block.get("usedPercent", block.get("used_percent"))
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _resets_at(block: dict[str, Any] | None) -> str | None:
    if not isinstance(block, dict):
        return None
    return _ms_to_iso(block.get("resetsAtMs", block.get("resets_at")))


class _Msp:
    """One-shot muse serve client. NDJSON JSON-RPC on stdio."""

    def __init__(self, binary: str):
        self.binary = binary
        self.proc: subprocess.Popen[bytes] | None = None
        self._buf = b""
        self._next_id = 1

    def __enter__(self) -> "_Msp":
        self.proc = subprocess.Popen(
            [self.binary, "serve", "--no-session-log", "--disable-sandbox"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, *exc: object) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass

    def _send(self, obj: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
        self.proc.stdin.flush()

    def _read_until_id(self, want: int, deadline: float) -> dict[str, Any]:
        assert self.proc is not None and self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        while True:
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"muse serve sent invalid JSON: {exc}") from exc
                if isinstance(msg, dict) and msg.get("id") == want:
                    return msg
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("muse serve timed out")
            if self.proc.poll() is not None:
                raise RuntimeError("muse serve exited")
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise TimeoutError("muse serve timed out")
            chunk = os.read(fd, 65536)
            if not chunk:
                raise RuntimeError("muse serve exited")
            self._buf += chunk

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        deadline = time.monotonic() + SERVE_TIMEOUT
        msg = self._read_until_id(req_id, deadline)
        if "error" in msg:
            err = msg["error"]
            text = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(text or "muse RPC error")
        result = msg.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def handshake(self) -> dict[str, Any]:
        result = self.call(
            "initialize",
            {
                "clientInfo": {"name": "ai_quotas", "version": "0"},
                "capabilities": {"experimentalApi": True, "userInputDialogs": False},
            },
        )
        self.notify("initialized")
        return result


def _probe() -> dict[str, Any]:
    binary = shutil.which("muse")
    if not binary:
        raise FileNotFoundError("muse CLI not on PATH")
    with _Msp(binary) as client:
        client.handshake()
        account: dict[str, Any] = {}
        try:
            account = client.call("account/read")
        except Exception:
            account = {}
        usage = client.call("usage/read")
        return {"account": account, "usage": usage}


def snapshot(ts: str) -> list[dict]:
    """Return Muse Code quota rows. Never raises."""
    try:
        payload = _probe()
    except FileNotFoundError:
        return []
    except Exception as exc:
        return [
            _row(
                ts,
                window="week",
                used_percent=None,
                status="unavailable",
                reason=f"muse: {exc}"[:200],
            )
        ]

    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    usage_wrap = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    usage = usage_wrap.get("usage")
    if not isinstance(usage, dict):
        usage = usage_wrap if any(k in usage_wrap for k in ("weekly", "window", "tier")) else None

    plan = None
    if isinstance(usage, dict) and isinstance(usage.get("tier"), str) and usage["tier"].strip():
        plan = usage["tier"].strip()
    elif isinstance(account.get("label"), str) and account["label"].strip():
        plan = account["label"].strip()

    rows: list[dict] = []
    if isinstance(usage, dict):
        weekly = usage.get("weekly") if isinstance(usage.get("weekly"), dict) else None
        window = usage.get("window") if isinstance(usage.get("window"), dict) else None
        week_used = _used_percent(weekly)
        if week_used is not None:
            rows.append(
                _row(
                    ts,
                    window="week",
                    used_percent=week_used,
                    resets_at=_resets_at(weekly),
                    plan=plan,
                    status="ok",
                )
            )
        win_used = _used_percent(window)
        if win_used is not None:
            rows.append(
                _row(
                    ts,
                    window=_window_from_minutes(window.get("windowDurationMins") if window else None),
                    used_percent=win_used,
                    resets_at=_resets_at(window),
                    plan=plan,
                    status="ok",
                )
            )
    # Logged-in with no observed usage yet: stay silent so the dash setup card
    # remains. Do not invent 0%.
    return rows


if __name__ == "__main__":
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for row in snapshot(now):
        print(json.dumps(row, ensure_ascii=False))
