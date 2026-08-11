"""OpenRouter quota adapter.

Source:
  GET https://openrouter.ai/api/v1/key
  Authorization: Bearer <OPENROUTER_API_KEY>

Returns credit usage/limit and free-tier flags. Key discovery (in order):
  1. env OPENROUTER_API_KEY or OPENROUTER_KEY
  2. plain KEY=value parse of ~/.env (and ./.env if present)

stdlib only. snapshot(ts) never raises.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROVIDER = "openrouter"
UA = "ai-quotas/openrouter"
KEY_URL = "https://openrouter.ai/api/v1/key"
ENV_NAMES = ("OPENROUTER_API_KEY", "OPENROUTER_KEY")


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


def _parse_dotenv(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, val = s.split("=", 1)
        key = key.strip()
        if key not in ENV_NAMES:
            continue
        val = val.strip().strip("'").strip('"')
        if val:
            return val
    return None


def _discover_key() -> tuple[str | None, str | None]:
    """Return (api_key|None, discovery_note|None)."""
    for name in ENV_NAMES:
        v = os.environ.get(name)
        if v and v.strip():
            return v.strip(), "env"

    for path in (
        Path.home() / ".env",
        Path.cwd() / ".env",
    ):
        v = _parse_dotenv(path)
        if v:
            return v, f"dotenv:{path.name}"

    return None, None


def _http_get_json(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows_from_payload(ts: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return _fail(ts, "error", "key response missing data object")

    usage = _float_or_none(
        data.get("usage") if data.get("usage") is not None else data.get("usage_daily")
    )
    limit = _float_or_none(
        data.get("limit")
        if data.get("limit") is not None
        else data.get("credit_limit")
        if data.get("credit_limit") is not None
        else data.get("hard_limit_usd")
    )
    limit_remaining = _float_or_none(data.get("limit_remaining"))
    is_free = bool(data.get("is_free_tier"))
    label = data.get("label")
    plan = "free_tier" if is_free else (str(label) if label else None)

    rows: list[dict[str, Any]] = []

    if limit is not None and limit > 0 and usage is not None:
        used_percent = min(100.0, max(0.0, (usage / limit) * 100.0))
        rows.append(
            _row(
                ts,
                window="credits",
                used_percent=used_percent,
                plan=plan,
                status="ok",
                limit=limit,
                used=usage,
            )
        )
    elif limit is not None and limit > 0 and limit_remaining is not None:
        used = max(0.0, limit - limit_remaining)
        used_percent = min(100.0, max(0.0, (used / limit) * 100.0))
        rows.append(
            _row(
                ts,
                window="credits",
                used_percent=used_percent,
                plan=plan,
                status="ok",
                limit=limit,
                used=used,
            )
        )
    elif usage is not None and (limit is None or limit == 0):
        rows.append(
            _row(
                ts,
                window="credits",
                used_percent=None,
                plan=plan,
                status="unavailable",
                reason=f"key has no credit limit (usage={usage}); percent not computable",
                limit=None,
                used=usage,
            )
        )

    free_used = _float_or_none(
        data.get("free_requests_used")
        or data.get("daily_requests")
        or data.get("requests_today")
    )
    free_limit = _float_or_none(
        data.get("free_requests_limit") or data.get("daily_request_limit")
    )
    if free_used is not None and free_limit is not None and free_limit > 0:
        rows.append(
            _row(
                ts,
                window="free_daily",
                used_percent=min(100.0, max(0.0, (free_used / free_limit) * 100.0)),
                plan=plan,
                status="ok",
                limit=int(free_limit),
                used=int(free_used),
            )
        )

    if not rows:
        return _fail(
            ts,
            "unavailable",
            "key endpoint returned no usage/limit fields to map to used_percent",
        )
    return rows


def snapshot(ts: str) -> list[dict]:
    """Return openrouter quota rows. Never raises."""
    try:
        key, _note = _discover_key()
        if not key:
            return _fail(ts, "unavailable", "no OPENROUTER_API_KEY configured")

        try:
            payload = _http_get_json(KEY_URL, key)
        except urllib.error.HTTPError as exc:
            body = exc.read(200).decode("utf-8", "replace")
            if exc.code in (401, 403):
                return _fail(ts, "unavailable", f"key HTTP {exc.code}: {body[:120]}")
            return _fail(ts, "error", f"key HTTP {exc.code}: {body[:120]}")
        except Exception as exc:
            return _fail(ts, "error", f"key request: {exc}")

        if not isinstance(payload, dict):
            return _fail(ts, "error", "key response is not an object")
        return _rows_from_payload(ts, payload)
    except Exception as exc:
        return _fail(ts, "error", f"unexpected: {exc}")


if __name__ == "__main__":
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for row in snapshot(now):
        print(json.dumps(row, ensure_ascii=False))
