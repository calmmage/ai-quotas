"""Grok (xAI Build CLI) quota adapter.

Source:
  GET https://cli-chat-proxy.grok.com/v1/billing?format=credits  → weekly %
  GET https://cli-chat-proxy.grok.com/v1/billing?format=full     → monthly limit/used

Auth: AI_QUOTAS_GROK_AUTH_FILE, GROK_HOME/auth.json, or ~/.grok/auth.json.
Uses the OIDC entry `key` (Bearer). If expired, refresh via
https://auth.x.ai/oauth2/token with the stored refresh_token + oidc_client_id.
A successful refresh is written back to the same auth file (refresh tokens
rotate). If refresh fails, a stale same-account copy is healed from the live
CLI file ~/.grok/auth.json. Different accounts are never mixed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ai_quotas.notify import env_or_dotenv
from ai_quotas.reset_credits import credit_row, error_row, none_row

AUTH_PATH = Path.home() / ".grok" / "auth.json"
BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
PROVIDER = "grok"
UA = "ai-quotas/grok"
# Live Grok CLI login. `grok login` writes here even when the sampler is
# pointed at a copied GROK_HOME / AI_QUOTAS_GROK_AUTH_FILE.
LIVE_CLI_AUTH = Path.home() / ".grok" / "auth.json"
HEAL_TIP = (
    "Grok auth is stale. In a terminal run: grok login   "
    "then: uv run ai-quotas grok-fix   "
    "(login refreshes ~/.grok/auth.json; grok-fix copies that login into "
    "AI_QUOTAS_GROK_AUTH_FILE when it is a same-account stale copy)"
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


def auth_path() -> Path:
    """Honor an explicit auth file or the CLI's configured home; never copy keys."""
    override = env_or_dotenv("AI_QUOTAS_GROK_AUTH_FILE")
    if override:
        return Path(override).expanduser()
    home = env_or_dotenv("GROK_HOME")
    return Path(home).expanduser() / "auth.json" if home else AUTH_PATH


def _refresh_access_token(entry: dict[str, Any]) -> dict[str, Any]:
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
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read(200).decode("utf-8", "replace")
        raise RuntimeError(f"OIDC refresh HTTP {exc.code}: {detail[:160]}") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError("OIDC refresh returned no access_token")
    return data


def _auth_identity(entry: dict[str, Any]) -> tuple[Any, Any, Any]:
    return entry.get("user_id"), entry.get("principal_id"), entry.get("email")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _load_auth_file(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or not raw:
        raise RuntimeError(f"empty or invalid auth file: {path}")
    return raw


def _entry_key_and_value(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    key, entry = next(iter(raw.items()))
    if not isinstance(entry, dict):
        raise RuntimeError("auth entry is not an object")
    return str(key), entry


def _persist_refresh(path: Path, raw: dict[str, Any], entry_key: str, payload: dict[str, Any]) -> None:
    """Write rotated OIDC tokens back. Not doing this burns the grok CLI login."""
    entry = dict(raw[entry_key])
    entry["key"] = payload["access_token"]
    if payload.get("refresh_token"):
        entry["refresh_token"] = payload["refresh_token"]
    expires_in = payload.get("expires_in")
    if expires_in is not None:
        try:
            seconds = int(expires_in)
        except (TypeError, ValueError):
            seconds = None
        if seconds and seconds > 0:
            entry["expires_at"] = (
                datetime.now(timezone.utc) + timedelta(seconds=seconds)
            ).isoformat().replace("+00:00", "Z")
    raw = dict(raw)
    raw[entry_key] = entry
    _atomic_write_bytes(path, (json.dumps(raw, indent=2) + "\n").encode())


def _maybe_heal_from_live_cli(configured: Path) -> str | None:
    """Copy ~/.grok/auth.json onto a stale same-account copy. Never mix accounts.

    Returns a short reason when a copy happened, else None.
    """
    live = LIVE_CLI_AUTH
    try:
        if configured.resolve() == live.resolve():
            return None
    except OSError:
        return None
    if not live.is_file():
        return None
    try:
        configured_raw = _load_auth_file(configured)
        _, configured_entry = _entry_key_and_value(configured_raw)
        live_raw = _load_auth_file(live)
        _, live_entry = _entry_key_and_value(live_raw)
    except Exception:
        return None
    if _auth_identity(configured_entry) != _auth_identity(live_entry):
        return None
    if not all(_auth_identity(configured_entry)):
        return None
    live_exp = _parse_expires_at(live_entry.get("expires_at"))
    configured_exp = _parse_expires_at(configured_entry.get("expires_at"))
    now = datetime.now(timezone.utc)
    live_fresh = live_exp is None or live_exp > now
    configured_stale = configured_exp is not None and configured_exp <= now
    live_newer = (
        live_exp is not None
        and configured_exp is not None
        and live_exp > configured_exp
    )
    if not (live_fresh and (configured_stale or live_newer)):
        return None
    _atomic_write_bytes(configured, live.read_bytes())
    return f"copied live CLI auth ({live}) onto stale same-account copy ({configured})"


def _load_auth_entry() -> dict[str, Any]:
    path = auth_path()
    raw = _load_auth_file(path)
    _, entry = _entry_key_and_value(raw)
    return entry


def _get_access_token(*, _healed: bool = False) -> str:
    path = auth_path()
    raw = _load_auth_file(path)
    entry_key, entry = _entry_key_and_value(raw)
    token = entry.get("key")
    if not token:
        raise RuntimeError("no access token (`key`) in configured Grok auth file")
    exp = _parse_expires_at(entry.get("expires_at"))
    now = datetime.now(timezone.utc)
    # Refresh ~2 minutes early
    if exp is None or exp > now + timedelta(minutes=2):
        return str(token)
    try:
        payload = _refresh_access_token(entry)
        _persist_refresh(path, raw, entry_key, payload)
        return str(payload["access_token"])
    except Exception as refresh_exc:
        if not _healed:
            copied = _maybe_heal_from_live_cli(path)
            if copied:
                return _get_access_token(_healed=True)
        raise RuntimeError(f"{refresh_exc}. {HEAL_TIP}") from refresh_exc


def auth_status() -> dict[str, Any]:
    """Offline diagnosis. Never prints tokens."""
    path = auth_path()
    info: dict[str, Any] = {
        "auth_file": str(path),
        "exists": path.is_file(),
        "live_cli_auth": str(LIVE_CLI_AUTH),
        "live_cli_exists": LIVE_CLI_AUTH.is_file(),
        "expired": None,
        "expires_at": None,
        "heal_tip": HEAL_TIP,
        "ok": False,
        "reason": None,
    }
    if not path.is_file():
        info["reason"] = f"missing auth file: {path}"
        return info
    try:
        raw = _load_auth_file(path)
        _, entry = _entry_key_and_value(raw)
    except Exception as exc:
        info["reason"] = str(exc)
        return info
    exp = _parse_expires_at(entry.get("expires_at"))
    info["expires_at"] = entry.get("expires_at")
    info["expired"] = exp is not None and exp <= datetime.now(timezone.utc)
    info["has_key"] = bool(entry.get("key"))
    info["has_refresh"] = bool(entry.get("refresh_token"))
    if not entry.get("key"):
        info["reason"] = "no access token (`key`)"
        return info
    if info["expired"]:
        info["reason"] = "access token expired"
        return info
    info["ok"] = True
    return info


def diagnose_and_heal() -> dict[str, Any]:
    """Heal if possible, then report. Network only for OIDC refresh / billing."""
    report = auth_status()
    path = auth_path()
    heals: list[str] = []
    if path.is_file():
        copied = _maybe_heal_from_live_cli(path)
        if copied:
            heals.append(copied)
            report = auth_status()
    report["heals"] = heals
    if report.get("expired") and path.is_file():
        try:
            raw = _load_auth_file(path)
            entry_key, entry = _entry_key_and_value(raw)
            payload = _refresh_access_token(entry)
            _persist_refresh(path, raw, entry_key, payload)
            heals.append("persisted OIDC refresh into configured auth file")
            report = auth_status()
            report["heals"] = heals
        except Exception as exc:
            report["ok"] = False
            report["reason"] = str(exc)
            report["heals"] = heals
            report["heal_tip"] = HEAL_TIP
            return report
    if report.get("ok"):
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        rows = snapshot(ts)
        report["sample"] = [
            {k: r.get(k) for k in ("window", "status", "reason", "used_percent")}
            for r in rows
        ]
        report["ok"] = any(r.get("status") == "ok" for r in rows)
        if not report["ok"]:
            reasons = [r.get("reason") for r in rows if r.get("reason")]
            report["reason"] = "; ".join(str(x) for x in reasons if x) or "sample produced no ok rows"
            report["heal_tip"] = HEAL_TIP
    return report


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


def _force_refresh_token() -> str:
    path = auth_path()
    raw = _load_auth_file(path)
    entry_key, entry = _entry_key_and_value(raw)
    payload = _refresh_access_token(entry)
    _persist_refresh(path, raw, entry_key, payload)
    return str(payload["access_token"])


def _http_get_json_retry(url: str, token: str) -> tuple[str, dict[str, Any]]:
    try:
        return token, _http_get_json(url, token)
    except urllib.error.HTTPError as exc:
        if exc.code not in (401, 403):
            raise
        token = _force_refresh_token()
        return token, _http_get_json(url, token)


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


# ---------------------------------------------------------------------------
# Usage-limit reset credits (grok.com settings → Usage → "Reset Available")
#
# Connect/grpc-web RPC on the *web* host, accepted with the CLI OAuth bearer
# (verified 04 Sep 2026):
#   POST https://grok.com/prod_mc_billing.ConsumerUiSvc/GetRemainingResets
#   content-type: application/grpc-web+proto · body = empty message frame
# Response (ConsumerGetRemainingResetsResp), decoded by hand — no protobuf dep:
#   field 10 (repeated message)  reset token
#       field 10 string     id        e.g. "restok_vpYDqo"
#       field 20 Timestamp  granted   {1: seconds}
#       field 30 Timestamp  expires   {1: seconds}
# Field numbers are inferred from a live payload; anything else is kept in
# ``extra`` so a schema change shows up as data, not as a crash.
# ---------------------------------------------------------------------------

RESETS_URL = "https://grok.com/prod_mc_billing.ConsumerUiSvc/GetRemainingResets"
GRPC_WEB_EMPTY = b"\x00\x00\x00\x00\x00"


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, i


def _proto_fields(buf: bytes) -> list[tuple[int, int, Any]]:
    """Flat wire-format walk: [(field, wire_type, value)]; bytes stay bytes."""
    out: list[tuple[int, int, Any]] = []
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _varint(buf, i)
        elif wt == 1:
            val, i = buf[i : i + 8], i + 8
        elif wt == 2:
            ln, i = _varint(buf, i)
            val, i = buf[i : i + ln], i + ln
        elif wt == 5:
            val, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
        out.append((field, wt, val))
    return out


def _timestamp_seconds(blob: bytes) -> int | None:
    for field, wt, val in _proto_fields(blob):
        if field == 1 and wt == 0:
            return int(val)
    return None


def _grpc_web_frames(body: bytes) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    i = 0
    while i + 5 <= len(body):
        flag = body[i]
        ln = int.from_bytes(body[i + 1 : i + 5], "big")
        frames.append((flag, body[i + 5 : i + 5 + ln]))
        i += 5 + ln
    return frames


def parse_remaining_resets(body: bytes) -> tuple[list[dict[str, Any]], str | None]:
    """grpc-web body → ([{id, granted_at, expires_at, extra}], grpc_status)."""
    tokens: list[dict[str, Any]] = []
    grpc_status: str | None = None
    for flag, payload in _grpc_web_frames(body):
        if flag & 0x80:  # trailers
            text = payload.decode("utf-8", "replace")
            for line in text.splitlines():
                if line.lower().startswith("grpc-status:"):
                    grpc_status = line.split(":", 1)[1].strip()
            continue
        for field, wt, val in _proto_fields(payload):
            if field != 10 or wt != 2:
                continue
            tok: dict[str, Any] = {"id": None, "granted_at": None, "expires_at": None, "extra": {}}
            for f2, wt2, v2 in _proto_fields(val):
                if f2 == 10 and wt2 == 2:
                    tok["id"] = v2.decode("utf-8", "replace")
                elif f2 == 20 and wt2 == 2:
                    tok["granted_at"] = _iso_from_seconds(_timestamp_seconds(v2))
                elif f2 == 30 and wt2 == 2:
                    tok["expires_at"] = _iso_from_seconds(_timestamp_seconds(v2))
                else:
                    tok["extra"][str(f2)] = v2.hex() if isinstance(v2, bytes) else v2
            tokens.append(tok)
    return tokens, grpc_status


def _iso_from_seconds(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc).isoformat(timespec="seconds")


def _fetch_remaining_resets(token: str) -> bytes:
    req = urllib.request.Request(
        RESETS_URL,
        data=GRPC_WEB_EMPTY,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def _reset_credit_rows(ts: str, token: str) -> list[dict[str, Any]]:
    """Never raises."""
    try:
        body = _fetch_remaining_resets(token)
    except urllib.error.HTTPError as exc:
        detail = exc.read(200).decode("utf-8", "replace")
        return [error_row(ts, PROVIDER, f"GetRemainingResets HTTP {exc.code}: {detail[:160]}")]
    except Exception as exc:
        return [error_row(ts, PROVIDER, f"GetRemainingResets: {exc}")]
    try:
        tokens, grpc_status = parse_remaining_resets(body)
    except Exception as exc:
        return [error_row(ts, PROVIDER, f"GetRemainingResets decode: {exc}")]
    if grpc_status not in (None, "0"):
        return [error_row(ts, PROVIDER, f"GetRemainingResets grpc-status {grpc_status}")]
    rows: list[dict[str, Any]] = []
    for tok in tokens:
        if not tok.get("id"):
            continue
        rows.append(
            credit_row(
                ts,
                PROVIDER,
                credit_id=str(tok["id"]),
                title="Usage limit reset",
                granted_at=tok.get("granted_at"),
                expires_at=tok.get("expires_at"),
                status="available",
                reason="grok.com ConsumerUiSvc/GetRemainingResets",
                scope="week",
            )
        )
    return rows or [none_row(ts, PROVIDER, "GetRemainingResets lists no reset token")]


def snapshot(ts: str) -> list[dict]:
    """Return grok quota rows. Never raises."""
    try:
        path = auth_path()
        if not path.exists():
            return _fail(ts, "unavailable", f"missing auth file: {path}")
        try:
            token = _get_access_token()
        except Exception as exc:
            return _fail(ts, "error", f"auth/token: {exc}")

        rows: list[dict[str, Any]] = []
        try:
            token, credits = _http_get_json_retry(f"{BILLING_URL}?format=credits", token)
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
            token, full = _http_get_json_retry(f"{BILLING_URL}?format=full", token)
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

        if not rows:
            return _fail(ts, "unavailable", "no billing rows produced")
        rows.extend(_reset_credit_rows(ts, token))
        return rows
    except Exception as exc:
        return _fail(ts, "error", f"unexpected: {exc}")


if __name__ == "__main__":
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for row in snapshot(now):
        # Never print tokens; rows are safe.
        print(json.dumps(row, ensure_ascii=False))
