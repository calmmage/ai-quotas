"""Vendor *rate-limit reset credits* — the "you may reset your weekly limit
once" tokens Codex and Grok grant (Claude exposes none as of 04 Sep 2026).

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
) -> dict[str, Any]:
    return {
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


def none_row(ts: str, provider: str, reason: str | None = None) -> dict[str, Any]:
    return credit_row(ts, provider, credit_id=None, status="none", reason=reason, scope=None)


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


def credit_states(
    rows: list[dict[str, Any]], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Derive one state per (provider, credit_id) from the sample history.

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
        # tick → set of ids listed at that tick
        ticks: list[tuple[datetime, set[str]]] = []
        info: dict[str, dict[str, Any]] = {}
        for ts, row in items:
            if not ticks or ticks[-1][0] != ts:
                ticks.append((ts, set()))
            cid = row.get("credit_id")
            if row.get("status") == "available" and isinstance(cid, str) and cid:
                ticks[-1][1].add(cid)
                meta = info.setdefault(
                    cid,
                    {
                        "provider": prov,
                        "credit_id": cid,
                        "title": row.get("title"),
                        "granted_at": row.get("granted_at"),
                        "expires_at": row.get("expires_at"),
                        "scope": row.get("scope"),
                        "first_seen": ts,
                        "last_seen": ts,
                    },
                )
                meta["last_seen"] = ts
                if row.get("expires_at"):
                    meta["expires_at"] = row.get("expires_at")
                if row.get("title"):
                    meta["title"] = row.get("title")
        for cid, meta in info.items():
            exp = parse_ts(meta.get("expires_at"))
            last_seen: datetime = meta["last_seen"]
            # first tick after last_seen where the id is absent
            gone_at: datetime | None = None
            for ts, ids in ticks:
                if ts > last_seen and cid not in ids:
                    gone_at = ts
                    break
            status = "available"
            ended_at: datetime | None = None
            if gone_at is not None and (gone_at - last_seen) <= DISAPPEAR_GRACE * 3:
                if exp is not None and gone_at >= exp:
                    status, ended_at = "expired", exp
                else:
                    status, ended_at = "consumed", gone_at
            elif gone_at is not None:
                # long blind gap: cannot tell consumed from expired reliably
                if exp is not None and gone_at >= exp:
                    status, ended_at = "expired", exp
                else:
                    status, ended_at = "consumed", gone_at
            elif exp is not None and now_dt >= exp:
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
    return out


def remaining_total(used_percent: float | None, available: int) -> float | None:
    """% remaining including redeemable resets (each = +100% of the window)."""
    if used_percent is None:
        return None
    return round(max(0.0, 100.0 - float(used_percent)) + 100.0 * int(available), 2)
