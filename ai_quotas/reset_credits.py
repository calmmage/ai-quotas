"""Vendor *rate-limit reset credits* — the "you may reset your weekly limit
once" tokens Codex and Grok grant. Claude's usage API does not list them;
the adapter re-emits a known client-banner grant until it expires.

Separate grain from quota samples (docs/CONTRACT.md → "Reset credits"):

    {"kind": "reset_credit", "ts", "provider", "credit_id", "title",
     "granted_at", "expires_at", "status", "reason", "scope"}

``status`` at sample time:
  available    — vendor lists the credit as redeemable
  none         — vendor answered, zero credits
  unavailable  — vendor exposes no such thing / not reachable (reason says why)
  error        — probe failed (reason)

Lifecycle is *derived* from the sample history (``credit_states``):
  available → consumed  (id disappears before ``expires_at`` — a reset was used)
  available → expired   (id still listed past ``expires_at``, or disappears after it)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

KIND = "reset_credit"

# How long a credit may vanish from the vendor listing before we call it
# consumed (one missed tick is noise, not a redemption).
DISAPPEAR_GRACE = timedelta(hours=2)
CREDIT_FRESHNESS = timedelta(hours=2)
EXPIRING_SOON = timedelta(days=7)


def _account(row: dict[str, Any]) -> str | None:
    """Login the probe was for. Empty when older rows did not record one."""
    value = row.get("account")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def credit_row(
    ts: str,
    provider: str,
    *,
    credit_id: str | None,
    title: str | None = None,
    granted_at: str | None = None,
    expires_at: str | None = None,
    status: str = "available",
    reason: str | None = None,
    scope: str | None = "week",
    account: str | None = None,
) -> dict[str, Any]:
    row = {
        "kind": KIND,
        "ts": ts,
        "provider": provider,
        "credit_id": credit_id,
        "title": title,
        "granted_at": granted_at,
        "expires_at": expires_at,
        "status": status,
        "reason": reason,
        "scope": scope,
    }
    if isinstance(account, str) and account.strip():
        row["account"] = account.strip().lower()
    return row


def none_row(
    ts: str,
    provider: str,
    reason: str | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    return credit_row(
        ts, provider, credit_id=None, status="none", reason=reason, scope=None, account=account
    )


def unavailable_row(ts: str, provider: str, reason: str) -> dict[str, Any]:
    return credit_row(
        ts, provider, credit_id=None, status="unavailable", reason=reason, scope=None
    )


def error_row(ts: str, provider: str, reason: str) -> dict[str, Any]:
    return credit_row(ts, provider, credit_id=None, status="error", reason=reason, scope=None)


def is_reset_credit_row(row: dict[str, Any]) -> bool:
    return isinstance(row, dict) and row.get("kind") == KIND


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _grant_second(value: Any) -> str | None:
    dt = parse_ts(value)
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _identities(items: list[tuple[datetime, dict[str, Any]]]) -> dict[str, str]:
    """credit_id → identity, so two samplers' listings of one credit are one.

    CodexBar rewrites vendor ids (``codex-reset-credit-v1-<hash>``) while an
    owned sampler sees the raw id. Ids of one source never merge. Across
    sources, ids with the same login, ``granted_at`` and ``expires_at`` (to
    the second) are one credit, but only when each source lists exactly one
    such id: two credits granted together stay two.
    """
    parent: dict[str, str] = {}
    accounts: dict[str, set[str]] = {}

    def find(cid: str) -> str:
        while parent[cid] != cid:
            parent[cid] = parent[parent[cid]]
            cid = parent[cid]
        return cid

    listed: list[tuple[str, dict[str, Any]]] = []
    for _, row in items:
        cid = row.get("credit_id")
        if row.get("status") != "available" or not isinstance(cid, str) or not cid:
            continue
        parent.setdefault(cid, cid)
        listed.append((cid, row))
        acct = _account(row)
        if acct:
            accounts.setdefault(cid, set()).add(acct)
    groups: dict[tuple[str, str | None, str], dict[str, set[str]]] = {}
    for cid, row in listed:
        granted = _grant_second(row.get("granted_at"))
        known = accounts.get(cid) or set()
        acct = _account(row) or (next(iter(known)) if len(known) == 1 else None)
        if not granted or not acct:
            continue
        key = (granted, _grant_second(row.get("expires_at")), acct)
        source = str(row.get("source") or "borrowed")
        groups.setdefault(key, {}).setdefault(source, set()).add(cid)
    for by_source in groups.values():
        if len(by_source) < 2 or any(len(ids) != 1 for ids in by_source.values()):
            continue
        first, *rest = (next(iter(ids)) for ids in by_source.values())
        for other in rest:
            parent[find(other)] = find(first)
    return {cid: find(cid) for cid in parent}


# A banner grant (typed in from the vendor client, never listed by its API)
# cannot vanish when redeemed. Its only evidence is the window refilling to
# ~0 well before the scheduled deadline (Petr, 19 Oct 2026: "The reset on
# main account was already used, why does the ai-quotas still showing it?").
REFILL_SLACK = timedelta(hours=12)
REFILL_TO = 1.0
REFILL_FROM = 3.0


def is_assumed(row: dict[str, Any]) -> bool:
    """A grant the adapter re-emits from a client banner, not a vendor listing."""
    if row.get("assumed"):
        return True
    return "not in oauth/usage" in str(row.get("reason") or "") and bool(row.get("credit_id"))


def unscheduled_refills(
    quota_rows: list[dict[str, Any]], provider: str, window: str = "week",
) -> list[tuple[datetime, str | None]]:
    """(time, account) where ``window`` refilled to ~0 before its deadline.

    A refill that snaps back on the next sample is a glitch, not a reset.
    """
    series: dict[str | None, list[tuple[datetime, float, datetime | None]]] = {}
    for row in quota_rows:
        if row.get("provider") != provider or row.get("window") != window:
            continue
        if row.get("status") != "ok" or row.get("used_percent") is None:
            continue
        ts = parse_ts(row.get("ts"))
        if ts is None:
            continue
        series.setdefault(_account(row), []).append(
            (ts, float(row["used_percent"]), parse_ts(row.get("resets_at"))))
    out: list[tuple[datetime, str | None]] = []
    for account, pts in series.items():
        pts.sort(key=lambda p: p[0])
        for k in range(1, len(pts)):
            (_, before, deadline), (at, after, _) = pts[k - 1], pts[k]
            if before < REFILL_FROM or after > REFILL_TO or deadline is None:
                continue
            if at >= deadline - REFILL_SLACK:
                continue  # the scheduled reset, not a redeemed credit
            if k + 1 < len(pts) and pts[k + 1][1] > before / 2:
                continue
            out.append((at, account))
    out.sort(key=lambda r: r[0])
    return out


def retire_redeemed(
    rows: list[dict[str, Any]], quota_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Banner grants answer "none" from the first unscheduled refill after they
    appeared, so ``credit_states`` reads them as consumed at that moment."""
    assumed = [r for r in rows if is_reset_credit_row(r) and r.get("status") == "available" and is_assumed(r)]
    if not assumed:
        return rows
    first_seen: dict[tuple[Any, Any], datetime] = {}
    for row in assumed:
        ts = parse_ts(row.get("ts"))
        key = (row.get("provider"), row.get("credit_id"))
        if ts is not None and (key not in first_seen or ts < first_seen[key]):
            first_seen[key] = ts
    refills: dict[tuple[str, str], list[tuple[datetime, str | None]]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        if not (is_reset_credit_row(row) and row.get("status") == "available" and is_assumed(row)):
            out.append(row)
            continue
        prov, scope = str(row.get("provider")), str(row.get("scope") or "week")
        if (prov, scope) not in refills:
            refills[(prov, scope)] = unscheduled_refills(quota_rows, prov, scope)
        ts, start = parse_ts(row.get("ts")), first_seen.get((row.get("provider"), row.get("credit_id")))
        acct = _account(row)
        used = ts is not None and start is not None and any(
            start <= at <= ts and (acct is None or who == acct)
            for at, who in refills[(prov, scope)])
        if used:
            out.append({**none_row(str(row.get("ts")), prov,
                                   reason="banner grant redeemed: window refilled before its deadline",
                                   account=acct), "device": row.get("device")})
        else:
            out.append(row)
    return out


def credit_states(
    rows: list[dict[str, Any]], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Derive one state per credit from the sample history.

    A CodexBar listing and a direct API listing of one credit are one credit
    (see ``_identities``); the reported ``credit_id`` is the latest seen.

    Returns dicts sorted by first_seen:
      provider, credit_id, title, granted_at, expires_at, scope,
      first_seen, last_seen, status (available|consumed|expired), ended_at
    ``ended_at`` = the first probe tick where the credit was no longer listed
    (consumed) or ``expires_at`` (expired). Only rows with ``status`` in
    {available, none} count as *answers*; unavailable/error ticks are ignored
    so an outage never fakes a redemption.
    """
    now_dt = _now(now)
    answers: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for row in rows:
        if not is_reset_credit_row(row):
            continue
        if row.get("status") not in {"available", "none"}:
            continue
        ts = parse_ts(row.get("ts"))
        prov = row.get("provider")
        if ts is None or not isinstance(prov, str):
            continue
        answers.setdefault(prov, []).append((ts, row))

    states: list[dict[str, Any]] = []
    for prov, items in answers.items():
        items.sort(key=lambda it: it[0])
        # tick → (account, ids listed). A different login's empty answer is not
        # an absence: CodexBar can be pointed at another account for a probe.
        ticks: list[tuple[datetime, str | None, set[str]]] = []
        info: dict[str, dict[str, Any]] = {}
        identity = _identities(items)
        for ts, row in items:
            acct = _account(row)
            if not ticks or ticks[-1][0] != ts or ticks[-1][1] != acct:
                ticks.append((ts, acct, set()))
            cid = row.get("credit_id")
            key = identity.get(cid) if isinstance(cid, str) else None
            if key is not None and isinstance(cid, str) and row.get("status") == "available":
                ticks[-1][2].add(key)
                meta = info.setdefault(
                    key,
                    {
                        "provider": prov,
                        "credit_id": cid,
                        "title": row.get("title"),
                        "granted_at": row.get("granted_at"),
                        "expires_at": row.get("expires_at"),
                        "scope": row.get("scope"),
                        "account": acct,
                        "first_seen": ts,
                        "last_seen": ts,
                    },
                )
                meta["last_seen"] = ts
                meta["credit_id"] = cid
                if acct:
                    meta["account"] = acct
                if row.get("expires_at"):
                    meta["expires_at"] = row.get("expires_at")
                if row.get("title"):
                    meta["title"] = row.get("title")
        for key, meta in info.items():
            exp = parse_ts(meta.get("expires_at"))
            last_seen: datetime = meta["last_seen"]
            credit_account = meta.get("account")
            # first same-account tick after last_seen where the credit is absent
            gone_at: datetime | None = None
            for ts, acct, ids in ticks:
                if ts > last_seen and acct == credit_account and key not in ids:
                    gone_at = ts
                    break
            status = "available"
            ended_at: datetime | None = None
            # A credit that vanishes and is listed again stays available: last_seen
            # moves forward, so gone_at is only an absence that is still the latest
            # answer. One empty probe is noise until it has lasted DISAPPEAR_GRACE.
            if gone_at is not None and exp is not None and gone_at >= exp:
                status, ended_at = "expired", exp
            elif gone_at is not None and now_dt - gone_at >= DISAPPEAR_GRACE:
                status, ended_at = "consumed", gone_at
            elif gone_at is None and exp is not None and now_dt >= exp:
                status, ended_at = "expired", exp
            states.append(
                {
                    **meta,
                    "first_seen": meta["first_seen"].isoformat(timespec="seconds"),
                    "last_seen": last_seen.isoformat(timespec="seconds"),
                    "status": status,
                    "ended_at": ended_at.isoformat(timespec="seconds") if ended_at else None,
                }
            )
    states.sort(key=lambda s: (s["first_seen"], s["provider"], s["credit_id"]))
    return states


def latest_probe(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Newest reset-credit row per provider (any status) — the 'what did the
    vendor say last time' view for the CLI / API."""
    best: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for row in rows:
        if not is_reset_credit_row(row):
            continue
        ts = parse_ts(row.get("ts"))
        prov = row.get("provider")
        if ts is None or not isinstance(prov, str):
            continue
        prev = best.get(prov)
        if prev is None or ts >= prev[0]:
            best[prov] = (ts, row)
    return {prov: row for prov, (_, row) in best.items()}


def summarize(
    rows: list[dict[str, Any]], *, now: datetime | None = None
) -> dict[str, dict[str, Any]]:
    """Per-provider API block:

      {"status": available|none|unavailable|error, "reason", "checked_at",
       "available": n, "credits": [{credit_id, title, granted_at, expires_at,
                                     expires_in_hours}],
       "consumed": n, "expired": n}
    """
    now_dt = _now(now)
    states = credit_states(rows, now=now)
    probes = latest_probe(rows)
    out: dict[str, dict[str, Any]] = {}
    for prov, probe in probes.items():
        out[prov] = {
            "status": probe.get("status"),
            "reason": probe.get("reason"),
            "checked_at": probe.get("ts"),
            "available": 0,
            "credits": [],
            "consumed": 0,
            "expired": 0,
        }
    for st in states:
        block = out.setdefault(
            st["provider"],
            {
                "status": None,
                "reason": None,
                "checked_at": None,
                "available": 0,
                "credits": [],
                "consumed": 0,
                "expired": 0,
            },
        )
        if st["status"] == "available":
            exp = parse_ts(st.get("expires_at"))
            hours = None
            if exp is not None:
                hours = round((exp - now_dt).total_seconds() / 3600.0, 1)
            block["available"] += 1
            block["credits"].append(
                {
                    "credit_id": st["credit_id"],
                    "title": st.get("title"),
                    "granted_at": st.get("granted_at"),
                    "expires_at": st.get("expires_at"),
                    "expires_in_hours": hours,
                    "scope": st.get("scope"),
                }
            )
        elif st["status"] == "consumed":
            block["consumed"] += 1
        elif st["status"] == "expired":
            block["expired"] += 1
    for block in out.values():
        block["credits"].sort(key=lambda c: c.get("expires_at") or "")
        # Latest probe said "none", but the credits were listed inside the
        # grace window. Keep them available so one empty answer does not
        # zero the card or draw a redemption.
        if block["available"] and block.get("status") == "none":
            block["status"] = "available"
            block["reason"] = None
    return out


def remaining_total(used_percent: float | None, available: int) -> float | None:
    """% remaining including redeemable resets (each = +100% of the window)."""
    if used_percent is None:
        return None
    return round(max(0.0, 100.0 - float(used_percent)) + 100.0 * int(available), 2)


def usable_credits(
    rows: list[dict[str, Any]], provider: str, window: str, *, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Only fresh, successful, currently redeemable credits for this window.

    An old cached listing, a probe error, or another window's credits must not
    silence alerts. Normalise aliases without treating scoped quotas as totals.
    """
    now_dt = _now(now)
    rows = [r for r in rows if r.get("provider") == provider
            and (ts := parse_ts(r.get("ts"))) is not None and ts <= now_dt]
    block = summarize(rows, now=now_dt).get(provider, {})
    checked = parse_ts(block.get("checked_at"))
    if block.get("status") != "available" or checked is None or now_dt - checked > CREDIT_FRESHNESS:
        return []
    aliases = {"7d": "week", "168h": "week"}
    target = aliases.get(window, window)
    return [c for c in block.get("credits", [])
            if aliases.get(c.get("scope"), c.get("scope")) == target
            and ((exp := parse_ts(c.get("expires_at"))) is None or exp > now_dt)]


def burn_relaxation(credits: list[dict[str, Any]], *, now: datetime | None = None) -> str | None:
    if len(credits) >= 2:
        return "2+ resets available"
    now_dt = _now(now)
    if any((exp := parse_ts(c.get("expires_at"))) is not None
           and timedelta(0) < exp - now_dt <= EXPIRING_SOON for c in credits):
        return "reset expires within 7 days"
    return None
