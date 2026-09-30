"""Where a row came from: vendor ``account`` (login email) and ``device``.

Every quota sample and reset-credit row carries both (docs/CONTRACT.md →
"Source"). ``account`` is the lower-cased email the vendor reported for the
login that was probed, or null when the vendor does not expose it — never
guessed. ``device`` is the short hostname of the machine that sampled.

One provider can have several logins (a second subscription). Each
(provider, account) is its own series: rows of different accounts are never
drawn on one line or counted into one reset-credit total.

The *primary* account of a provider is, in order:

1. the pin ``AI_QUOTAS_ACCOUNT_<PROVIDER>`` (e.g. ``AI_QUOTAS_ACCOUNT_CODEX``);
2. the account of the newest ok row flagged ``account_default`` (the vendor
   CLI's own default login on this device, e.g. ``~/.codex``);
3. the account of the newest ok row that has one.

Rows with a null account (legacy rows written before accounts were recorded)
belong to the primary account.
"""

from __future__ import annotations

import os
import re
import socket
from datetime import datetime, timezone
from typing import Any, Iterable

DEVICE_ENV = "AI_QUOTAS_DEVICE"
PIN_ENV_PREFIX = "AI_QUOTAS_ACCOUNT_"


def device_id() -> str:
    """Stable id of this machine: ``AI_QUOTAS_DEVICE`` or the short hostname."""
    override = (os.environ.get(DEVICE_ENV) or "").strip()
    if override:
        return override
    try:
        host = socket.gethostname() or ""
    except OSError:
        host = ""
    host = host.strip()
    if host.lower().endswith(".local"):
        host = host[: -len(".local")]
    host = host.split(".")[0].strip().lower()
    return host or "unknown"


def normalize_account(value: Any) -> str | None:
    """Lower-cased email, or None when ``value`` is not an email."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text or " " in text or "@" not in text:
        return None
    local, _, domain = text.partition("@")
    if not local or not domain:
        return None
    return text


def account_of(row: dict[str, Any]) -> str | None:
    return normalize_account(row.get("account"))


def pin_env_name(provider: str) -> str:
    return PIN_ENV_PREFIX + re.sub(r"[^A-Z0-9]", "_", str(provider).upper())


def pinned_account(provider: str) -> str | None:
    """``AI_QUOTAS_ACCOUNT_<PROVIDER>`` from the environment or the repo .env."""
    from ai_quotas.notify import env_or_dotenv

    return normalize_account(env_or_dotenv(pin_env_name(provider)))


def _ts(row: dict[str, Any]) -> datetime | None:
    raw = row.get("ts")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _answered(row: dict[str, Any]) -> bool:
    """A row where the vendor actually answered for this login."""
    status = row.get("status")
    if row.get("kind") == "reset_credit":
        return status in {"available", "none"}
    return status == "ok"


def primary_accounts(rows: Iterable[dict[str, Any]]) -> dict[str, str | None]:
    """provider → primary account (see module docstring). None when unknown."""
    flagged: dict[str, tuple[datetime, str]] = {}
    newest: dict[str, tuple[datetime, str]] = {}
    providers: set[str] = set()
    for row in rows:
        provider = row.get("provider")
        if not isinstance(provider, str):
            continue
        providers.add(provider)
        acct = account_of(row)
        if acct is None or not _answered(row):
            continue
        ts = _ts(row)
        if ts is None:
            continue
        if row.get("account_default") is True:
            prev = flagged.get(provider)
            if prev is None or ts >= prev[0]:
                flagged[provider] = (ts, acct)
        prev = newest.get(provider)
        if prev is None or ts >= prev[0]:
            newest[provider] = (ts, acct)
    out: dict[str, str | None] = {}
    for provider in providers:
        pin = pinned_account(provider)
        if pin:
            out[provider] = pin
        elif provider in flagged:
            out[provider] = flagged[provider][1]
        elif provider in newest:
            out[provider] = newest[provider][1]
        else:
            out[provider] = None
    return out


def account_key(row: dict[str, Any], primaries: dict[str, str | None]) -> str | None:
    """None for the primary account (including legacy null rows), else the email."""
    acct = account_of(row)
    if acct is None:
        return None
    primary = primaries.get(str(row.get("provider")))
    if primary is None and pinned_account(str(row.get("provider"))) is None:
        # No answered row names an account: nothing to split against.
        return None
    return None if acct == primary else acct


def split_accounts(
    rows: Iterable[dict[str, Any]],
    primaries: dict[str, str | None] | None = None,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], list[dict[str, Any]]]]:
    """(primary rows, {(provider, other account): rows})."""
    materialized = list(rows)
    if primaries is None:
        primaries = primary_accounts(materialized)
    primary: list[dict[str, Any]] = []
    others: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in materialized:
        key = account_key(row, primaries)
        if key is None:
            primary.append(row)
        else:
            others.setdefault((str(row.get("provider")), key), []).append(row)
    return primary, others


def stamp(rows: Iterable[dict[str, Any]], *, device: str | None = None) -> None:
    """Set ``device`` on every row and normalise ``account`` in place."""
    dev = device or device_id()
    for row in rows:
        if not isinstance(row, dict):
            continue
        row["account"] = account_of(row)
        if not row.get("device"):
            row["device"] = dev


def latest_source(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """{account, device, ts} of the newest answered row, else of the newest row."""
    best: tuple[datetime, dict[str, Any]] | None = None
    best_any: tuple[datetime, dict[str, Any]] | None = None
    for row in rows:
        ts = _ts(row)
        if ts is None:
            continue
        if best_any is None or ts >= best_any[0]:
            best_any = (ts, row)
        if _answered(row) and (best is None or ts >= best[0]):
            best = (ts, row)
    pick = best or best_any
    if pick is None:
        return None
    row = pick[1]
    return {"account": account_of(row), "device": row.get("device"), "ts": row.get("ts")}


def source_text(source: dict[str, Any] | None) -> str:
    """``alice@example.com · example-mac``; missing parts are left out."""
    if not source:
        return ""
    parts = [p for p in (source.get("account"), source.get("device")) if p]
    return " · ".join(str(p) for p in parts)


def primary_only(
    rows: Iterable[dict[str, Any]], reference: Iterable[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """Rows of each provider's primary account; ``reference`` (e.g. the quota
    samples) helps pick the primary for reset-credit rows."""
    materialized = list(rows)
    primaries = primary_accounts([*reference, *materialized])
    return split_accounts(materialized, primaries)[0]
