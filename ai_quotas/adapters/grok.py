"""Grok (xAI Build CLI) quota adapter.

Source:
  GET https://cli-chat-proxy.grok.com/v1/billing?format=credits  → weekly %
  GET https://cli-chat-proxy.grok.com/v1/billing?format=full     → monthly limit/used

Auth: ~/.grok/auth.json OIDC entry `key` (Bearer). If expired, refresh via
https://auth.x.ai/oauth2/token with the stored refresh_token + oidc_client_id.
Refresh is in-memory only — this adapter never writes credentials.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUTH_PATH = Path.home() / ".grok" / "auth.json"
BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
PROVIDER = "grok"
UA = "ai-quotas/grok"


def _row(
    ts: str,
    *,
    window: str,
    used_percent: float | None,
    resets_at: str | None = None,
    plan: str | None = None,
    status: str = "ok",
    reason: str | None = None,
    limit: int | None = None,
    used: int | None = None,
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


def _parse_expires_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_auth_entry() -> dict[str, Any]:
    raw = json.loads(AUTH_PATH.read_text())
    if not isinstance(raw, dict) or not raw:
        raise RuntimeError(f"empty or invalid auth file: {AUTH_PATH}")
    # Single OIDC entry keyed by issuer::client_id
    entry = next(iter(raw.values()))
    if not isinstance(entry, dict):
        raise RuntimeError("auth entry is not an object")
    return entry


def _refresh_access_token(entry: dict[str, Any]) -> str:
    refresh = entry.get("refresh_token")
    client_id = entry.get("oidc_client_id")
    if not refresh or not client_id:
        raise RuntimeError("missing refresh_token or oidc_client_id for grok OIDC refresh")
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    token = data.get("access_token")
    if not token:
        raise RuntimeError("OIDC refresh returned no access_token")
    return token


def _get_access_token() -> str:
    entry = _load_auth_entry()
    token = entry.get("key")
    if not token:
        raise RuntimeError("no access token (`key`) in ~/.grok/auth.json")
    exp = _parse_expires_at(entry.get("expires_at"))
    # Refresh ~2 minutes early
    if exp is not None and exp <= datetime.now(timezone.utc):
        return _refresh_access_token(entry)
    return token


def _http_get_json(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": UA,
            "x-grok-client-mode": "cli",
            "x-grok-client-version": "ai-quotas",
            "x-grok-client-identifier": "ai-quotas",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _unwrap_val(obj: Any) -> Any:
    if isinstance(obj, dict) and "val" in obj:
        return obj["val"]
    return obj


def _to_local_iso(value: str | None) -> str | None:
    """Pass through server ISO timestamps; leave null if unparsable."""
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return value


def _week_row(ts: str, config: dict[str, Any]) -> dict[str, Any]:
    # Prefer product-specific GrokBuild %, fall back to top-level creditUsagePercent.
    percent = config.get("creditUsagePercent")
    for product in config.get("productUsage") or []:
        if isinstance(product, dict) and product.get("product") == "GrokBuild":
            if product.get("usagePercent") is not None:
                percent = product["usagePercent"]
            break
    if percent is None:
        return _row(
            ts,
            window="week",
            used_percent=None,
            status="unavailable",
            reason="billing credits response missing creditUsagePercent",
        )
    period = config.get("currentPeriod") or {}
    resets_at = _to_local_iso(period.get("end") or config.get("billingPeriodEnd"))
    return _row(
        ts,
        window="week",
        used_percent=float(percent),
        resets_at=resets_at,
        plan=None,
        status="ok",
        reason=None,
    )


def _month_row(ts: str, config: dict[str, Any]) -> dict[str, Any]:
    limit = _unwrap_val(config.get("monthlyLimit"))
    used = _unwrap_val(config.get("used"))
    if limit is None or used is None:
        return _row(
            ts,
            window="month",
            used_percent=None,
            status="unavailable",
            reason="billing full response missing monthlyLimit/used",
        )
    try:
        limit_i = int(limit)
        used_i = int(used)
    except (TypeError, ValueError):
        return _row(
            ts,
            window="month",
            used_percent=None,
            status="error",
            reason=f"non-numeric monthlyLimit/used: limit={limit!r} used={used!r}",
        )
    if limit_i <= 0:
        return _row(
            ts,
            window="month",
            used_percent=None,
            limit=limit_i,
            used=used_i,
            status="unavailable",
            reason=f"monthlyLimit is non-positive ({limit_i})",
        )
    used_percent = (used_i / limit_i) * 100.0
    return _row(
        ts,
        window="month",
        used_percent=used_percent,
        resets_at=_to_local_iso(config.get("billingPeriodEnd")),
        plan=None,
        status="ok",
        reason=None,
        # Contract says limit/used stay null today for vendors that only expose
        # percent — but Grok actually returns absolute monthly counts. Populate.
        limit=limit_i,
        used=used_i,
    )


def snapshot(ts: str) -> list[dict]:
    """Return grok quota rows. Never raises."""
    try:
        if not AUTH_PATH.exists():
            return _fail(ts, "unavailable", f"missing auth file: {AUTH_PATH}")
        try:
            token = _get_access_token()
        except Exception as exc:
            return _fail(ts, "error", f"auth/token: {exc}")

        rows: list[dict[str, Any]] = []
        try:
            credits = _http_get_json(f"{BILLING_URL}?format=credits", token)
            cfg = credits.get("config") if isinstance(credits, dict) else None
            if not isinstance(cfg, dict):
                rows.append(
                    _row(
                        ts,
                        window="week",
                        used_percent=None,
                        status="unavailable",
                        reason="billing credits response missing config",
                    )
                )
            else:
                rows.append(_week_row(ts, cfg))
        except urllib.error.HTTPError as exc:
            body = exc.read(200).decode("utf-8", "replace")
            rows.append(
                _row(
                    ts,
                    window="week",
                    used_percent=None,
                    status="error",
                    reason=f"billing credits HTTP {exc.code}: {body[:160]}",
                )
            )
        except Exception as exc:
            rows.append(
                _row(
                    ts,
                    window="week",
                    used_percent=None,
                    status="error",
                    reason=f"billing credits: {exc}",
                )
            )

        try:
            full = _http_get_json(f"{BILLING_URL}?format=full", token)
            cfg = full.get("config") if isinstance(full, dict) else None
            if not isinstance(cfg, dict):
                rows.append(
                    _row(
                        ts,
                        window="month",
                        used_percent=None,
                        status="unavailable",
                        reason="billing full response missing config",
                    )
                )
            else:
                rows.append(_month_row(ts, cfg))
        except urllib.error.HTTPError as exc:
            body = exc.read(200).decode("utf-8", "replace")
            rows.append(
                _row(
                    ts,
                    window="month",
                    used_percent=None,
                    status="error",
                    reason=f"billing full HTTP {exc.code}: {body[:160]}",
                )
            )
        except Exception as exc:
            rows.append(
                _row(
                    ts,
                    window="month",
                    used_percent=None,
                    status="error",
                    reason=f"billing full: {exc}",
                )
            )

        return rows or _fail(ts, "unavailable", "no billing rows produced")
    except Exception as exc:
        return _fail(ts, "error", f"unexpected: {exc}")


if __name__ == "__main__":
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for row in snapshot(now):
        # Never print tokens; rows are safe.
        print(json.dumps(row, ensure_ascii=False))
