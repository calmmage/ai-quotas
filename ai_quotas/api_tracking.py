"""API billing observations and vault references in the existing quota database.

These are explicit records, never inferred from subscription quota percentages.
Key values are neither accepted nor returned. Provider organizations own balances.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path

from ai_quotas.paths import database_path

KINDS = {"balance", "grant", "key"}
GRANT_STATES = {"not_checked", "eligible", "linked", "issued", "exhausted", "expired", "ineligible"}


def _time(value=None):
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        raise ValueError("Use an ISO date with a timezone") from None


def _amount(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Amount must be a nonnegative number")
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError("Amount must be a nonnegative number") from None
    if not math.isfinite(n) or n < 0:
        raise ValueError("Amount must be a nonnegative number")
    return n


def read_records():
    path = database_path()
    if not path.exists():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='quota_api_records'").fetchone():
            return []
        return [json.loads(r[0]) for r in db.execute("SELECT payload FROM quota_api_records ORDER BY recorded_at,id")]


def record(data: dict, *, known_accounts: set[tuple[str, str]]) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Expected a record")
    kind, provider, account = (data.get(k) for k in ("kind", "provider", "account"))
    if kind not in KINDS or not isinstance(account, str) or not account or (provider, account) not in known_accounts:
        raise ValueError("Select a known provider and account")
    allowed = {"kind", "provider", "account", "observed_at"} | ({"vault_name", "fingerprint"} if kind == "key" else {"organization", "amount_usd", "expires_at", "status", "remaining_usd"})
    if set(data) - allowed:
        raise ValueError("Unsupported fields; key values are never accepted")
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    row = {"id": uuid4().hex, "kind": kind, "provider": provider, "account": account,
           "recorded_at": now, "observed_at": _time(data.get("observed_at")) or now, "source": "user"}
    if kind == "key":
        name = data.get("vault_name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name):
            raise ValueError("Enter a vault variable name, never a key value")
        fingerprint = data.get("fingerprint") or ""
        if not isinstance(fingerprint, str) or (fingerprint and not re.fullmatch(r"(?:sk-|sk-ant-|sk-proj-|AIza)?(?:\.\.\.|…|\*{2,})[A-Za-z0-9_-]{0,8}", fingerprint)):
            raise ValueError("Fingerprint must be masked, for example …1234")
        row.update(vault_name=name, fingerprint=fingerprint)
    else:
        organization = data.get("organization") or ""
        if not isinstance(organization, str) or len(organization) > 180:
            raise ValueError("Invalid organization identifier")
        if kind == "balance" and not organization.strip():
            raise ValueError("Enter the actual billing organization ID to avoid counting its balance twice")
        amount = _amount(data.get("amount_usd"))
        if kind == "balance" and amount is None:
            raise ValueError("Enter the observed USD balance")
        row.update(organization=organization.strip(), amount_usd=amount, expires_at=_time(data.get("expires_at")))
        if kind == "grant":
            status = data.get("status", "not_checked")
            if status not in GRANT_STATES:
                raise ValueError("Invalid claim status")
            remaining = _amount(data.get("remaining_usd"))
            if remaining is not None and (amount is None or remaining > amount):
                raise ValueError("Remaining grant cannot exceed its issued amount")
            if status == "issued" and (amount is None or not organization.strip()):
                raise ValueError("An issued grant needs its amount and receiving organization")
            row.update(status=status, remaining_usd=remaining)
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS quota_api_records (id TEXT PRIMARY KEY,recorded_at TEXT NOT NULL,payload TEXT NOT NULL)")
        db.execute("INSERT INTO quota_api_records VALUES(?,?,?)", (row["id"], now, json.dumps(row)))
    return row


def snapshot(panels: list[dict]) -> dict:
    accounts = {}
    for p in panels:
        account = p.get("account") or (p.get("source") or {}).get("account")
        provider = p.get("provider")
        if not account or not provider:
            continue
        key = (provider, account)
        sub = p.get("subscription") or {}
        declared = p.get("declared_subscription") or {}
        if key in accounts and not declared:
            continue
        price = declared.get("monthly_usd", sub.get("monthly_usd"))
        if provider == "agy" and price == 0 and not declared and sub.get("source") != "configured":
            price = None
        accounts[key] = {"provider": provider, "account": account,
                         "label": p.get("account_label") or account.split("@")[0],
                         "plan": declared.get("plan") or sub.get("label"), "monthly_usd": price,
                         "balance": None, "grant": None, "keys": []}
    try:
        records = read_records()
        unavailable = False
    except (OSError, sqlite3.Error, ValueError):
        records, unavailable = [], True
    balances, links, grants, keys = {}, {}, {}, {}
    for r in records:
        key = (r["provider"], r["account"])
        if r["kind"] == "balance":
            org = (r["provider"], r["organization"])
            balances[org], links[key] = r, org
        elif r["kind"] == "grant":
            grants[key] = r
        elif r["kind"] == "key":
            keys[(key, r["vault_name"])] = r
    for key, a in accounts.items():
        a["balance"] = balances.get(links.get(key))
        a["grant"] = grants.get(key)
        a["keys"] = [r for (k, _), r in keys.items() if k == key]
    return {"accounts": list(accounts.values()), "organizations": list(balances.values()),
            "grants": [r for r in records if r["kind"] == "grant"], "unavailable": unavailable,
            "updated_at": max((r["recorded_at"] for r in records), default=None),
            "configured_key_names": configured_key_names()}


def configured_key_names():
    """Names only, without resolving secrets or inferring which login owns them."""
    names = set(os.environ)
    for path in (Path.home() / ".env", Path.cwd() / ".env"):
        try:
            names.update(line.strip().removeprefix("export ").partition("=")[0].strip() for line in path.read_text().splitlines() if "=" in line)
        except OSError:
            pass
    result = []
    for name in sorted(names):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or "AZURE" in name:
            continue
        for provider, token in (("codex", "OPENAI"), ("claude", "ANTHROPIC"), ("agy", "GEMINI"), ("grok", "XAI"), ("openrouter", "OPENROUTER")):
            if token in name.split("_") and (name.endswith("_API_KEY") or name.endswith("_KEY")):
                result.append({"provider": provider, "vault_name": name, "account": None})
                break
    return result
