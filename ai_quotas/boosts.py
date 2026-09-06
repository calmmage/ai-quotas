"""Vendor *temporary limit boosts* — e.g. Claude "limits temporarily boosted
+50% through 13 Sep". History only (no money math).

Separate grain from quota samples and reset credits (docs/CONTRACT.md):

    {"kind": "boost", "ts", "provider", "window", "percent",
     "starts_at", "ends_at", "raw_text"}

The sampler upserts on ``(provider, window, percent, ends_at)`` — extend
``last_seen_ts``, do not duplicate.

Lifecycle is derived from the stored row (``boost_states``):
  active    — last seen recently and still before ``ends_at``
  ended     — ``ends_at`` has passed
  vanished  — disappeared from answering ticks before ``ends_at``
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

KIND = "boost"
DISPLAY_ENDED_WINDOW = timedelta(days=7)
DISAPPEAR_GRACE = timedelta(hours=2)
_PT = ZoneInfo("America/Los_Angeles")

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

# "limits temporarily boosted +50% through 13 Sep"
_BOOST_TEXT_RE = re.compile(
    r"(?:limits?\s+)?(?:temporarily\s+)?boosted\s+\+?(?P<percent>\d+(?:\.\d+)?)\s*%"
    r"(?:\s+through\s+(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9}))?",
    re.IGNORECASE,
)

_STRUCTURED_KEYS = (
    "boost",
    "boosts",
    "temporary_boost",
    "limit_boost",
    "rate_limit_boost",
    "promo",
    "promos",
    "promotions",
    "perks",
    "notices",
    "rate_limit_promo_notices",
)


def boost_row(
    ts: str,
    provider: str,
    *,
    window: str = "week",
    percent: float,
    ends_at: str | None = None,
    starts_at: str | None = None,
    raw_text: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": KIND,
        "ts": ts,
        "provider": provider,
        "window": window,
        "percent": float(percent),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "raw_text": raw_text,
    }


def is_boost_row(row: dict[str, Any]) -> bool:
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


def parse_through_date(day: int, month_name: str, *, now: datetime | None = None) -> str:
    """Vendor 'through 13 Sep' → end of that day in America/Los_Angeles, ISO-8601 UTC."""
    now_dt = _now(now)
    month = _MONTHS.get(month_name.strip().lower())
    if month is None:
        raise ValueError(f"unknown month {month_name!r}")
    year = now_dt.astimezone(_PT).year
    local = datetime(year, month, int(day), 23, 59, 59, tzinfo=_PT)
    if local < now_dt - timedelta(days=60):
        local = datetime(year + 1, month, int(day), 23, 59, 59, tzinfo=_PT)
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_boost_text(text: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Parse a vendor perk string. Returns percent / ends_at / raw_text or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    match = _BOOST_TEXT_RE.search(text)
    if not match:
        return None
    percent = float(match.group("percent"))
    ends_at = None
    if match.group("day") and match.group("month"):
        try:
            ends_at = parse_through_date(int(match.group("day")), match.group("month"), now=now)
        except ValueError:
            ends_at = None
    return {"percent": percent, "ends_at": ends_at, "raw_text": text.strip(), "window": "week"}


def _as_percent(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip().rstrip("%")
        try:
            return float(stripped)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_ends_at(value: Any, *, now: datetime | None = None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    parsed = parse_ts(raw)
    if parsed is not None:
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})", raw)
    if match:
        try:
            return parse_through_date(int(match.group(1)), match.group(2), now=now)
        except ValueError:
            return None
    return None


def _from_mapping(obj: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    text_bits = [
        str(obj[k])
        for k in ("raw_text", "text", "bar", "message", "label", "title")
        if isinstance(obj.get(k), str)
    ]
    parsed_text = None
    for bit in text_bits:
        parsed_text = parse_boost_text(bit, now=now)
        if parsed_text:
            break
    percent = _as_percent(
        obj.get("percent")
        if obj.get("percent") is not None
        else obj.get("boost_percent")
        if obj.get("boost_percent") is not None
        else obj.get("boosted_percent")
    )
    if percent is None and parsed_text is not None:
        percent = parsed_text["percent"]
    if percent is None:
        return None
    ends_at = _as_ends_at(
        obj.get("ends_at")
        or obj.get("through")
        or obj.get("boost_ends_at")
        or obj.get("until"),
        now=now,
    )
    if ends_at is None and parsed_text is not None:
        ends_at = parsed_text.get("ends_at")
    window = obj.get("window") or obj.get("quota_window") or obj.get("scope") or "week"
    if not isinstance(window, str) or not window.strip():
        window = "week"
    raw_text = None
    if text_bits:
        raw_text = text_bits[0]
    elif parsed_text is not None:
        raw_text = parsed_text.get("raw_text")
    return {
        "percent": float(percent),
        "ends_at": ends_at,
        "raw_text": raw_text,
        "window": window.strip(),
    }


def extract_boosts(data: Any, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Pull boost dicts out of a vendor usage payload (no extra HTTP)."""
    found: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def add(item: dict[str, Any] | None) -> None:
        if not item:
            return
        key = (item.get("window"), item.get("percent"), item.get("ends_at"))
        if key in seen:
            return
        seen.add(key)
        found.append(item)

    def walk(node: Any, *, structured: bool = False) -> None:
        if isinstance(node, str):
            add(parse_boost_text(node, now=now))
            return
        if isinstance(node, list):
            for item in node:
                walk(item, structured=structured)
            return
        if not isinstance(node, dict):
            return
        if structured:
            add(_from_mapping(node, now=now))
        for key, value in node.items():
            if isinstance(value, str):
                add(parse_boost_text(value, now=now))
            if key in _STRUCTURED_KEYS or key in {"boost_percent", "boosted_percent"}:
                if key in {"boost_percent", "boosted_percent"} and not isinstance(value, (dict, list)):
                    add(_from_mapping({**node, "percent": value}, now=now))
                else:
                    walk(value, structured=True)
            elif isinstance(value, (dict, list)) and key in {"limits", "extra_usage"}:
                walk(value, structured=False)

    walk(data)
    return found


def identity_key(row: dict[str, Any]) -> str:
    window = row.get("window") or row.get("quota_window") or ""
    percent = row.get("percent")
    ends = row.get("ends_at") or ""
    provider = row.get("provider") or ""
    return f"{provider}\0{window}\0{percent}\0{ends}"


def boost_states(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    checked_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """One derived state per stored boost row.

    ``checked_at`` is the latest answering sample tick for that provider
    (so a missed collect does not mark vanished; a collect that no longer
    lists the perk does).
    """
    now_dt = _now(now)
    states: list[dict[str, Any]] = []
    for row in rows:
        if not is_boost_row(row) and "percent" not in row:
            continue
        provider = row.get("provider")
        if not isinstance(provider, str) or not provider:
            continue
        last_seen = parse_ts(row.get("last_seen_ts") or row.get("ts"))
        first_seen = parse_ts(row.get("first_seen_ts") or row.get("starts_at") or row.get("ts"))
        ends = parse_ts(row.get("ends_at"))
        status = "active"
        if ends is not None and now_dt >= ends:
            status = "ended"
        else:
            reference = checked_at or now_dt
            if last_seen is not None and reference - last_seen > DISAPPEAR_GRACE:
                status = "vanished"
        states.append(
            {
                "provider": provider,
                "window": row.get("window") or row.get("quota_window") or "week",
                "percent": row.get("percent"),
                "starts_at": row.get("starts_at")
                or (first_seen.isoformat(timespec="seconds") if first_seen else None),
                "ends_at": row.get("ends_at"),
                "first_seen_ts": row.get("first_seen_ts")
                or (first_seen.isoformat(timespec="seconds") if first_seen else None),
                "last_seen_ts": row.get("last_seen_ts")
                or (last_seen.isoformat(timespec="seconds") if last_seen else None),
                "raw_text": row.get("raw_text"),
                "status": status,
            }
        )
    states.sort(key=lambda s: (s.get("ends_at") or "", s["provider"], str(s.get("percent"))))
    return states


def visible_boosts(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    checked_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Boosts that belong on the table line: active, or ended in the last 7 days."""
    now_dt = _now(now)
    out: list[dict[str, Any]] = []
    for st in boost_states(rows, now=now, checked_at=checked_at):
        if st["status"] == "active":
            out.append(st)
            continue
        if st["status"] == "ended":
            ended = parse_ts(st.get("ends_at"))
            if ended is not None and now_dt - ended <= DISPLAY_ENDED_WINDOW:
                out.append(st)
    return out


def format_boost_line(states: list[dict[str, Any]]) -> str | None:
    """One-line human summary, or None when nothing to show."""
    if not states:
        return None
    parts: list[str] = []
    for st in states:
        percent = st.get("percent")
        try:
            pct = f"+{float(percent):.0f}%"
        except (TypeError, ValueError):
            pct = "+?"
        window = st.get("window") or "week"
        ends = parse_ts(st.get("ends_at"))
        when = ends.astimezone().strftime("%d %b") if ends else "?"
        if st.get("status") == "ended":
            parts.append(f"{st['provider']} {window} {pct} ended {when}")
        else:
            parts.append(f"{st['provider']} {window} {pct} through {when}")
    return "boosts: " + " · ".join(parts)


def boost_badge(states: list[dict[str, Any]], provider: str) -> str:
    """Plot subtitle fragment while a boost is active: `+50% through 13 Sep`."""
    active = [
        s
        for s in states
        if s.get("provider") == provider and s.get("status") == "active"
    ]
    if not active:
        return ""
    st = min(active, key=lambda s: s.get("ends_at") or "9999")
    percent = st.get("percent")
    try:
        pct = f"+{float(percent):.0f}%"
    except (TypeError, ValueError):
        pct = "+?"
    ends = parse_ts(st.get("ends_at"))
    when = ends.astimezone().strftime("%d %b") if ends else "?"
    return f"{pct} through {when}"


def as_json_list(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "provider": s["provider"],
            "window": s.get("window"),
            "percent": s.get("percent"),
            "starts_at": s.get("starts_at"),
            "ends_at": s.get("ends_at"),
            "status": s.get("status"),
            "raw_text": s.get("raw_text"),
            "first_seen_ts": s.get("first_seen_ts"),
            "last_seen_ts": s.get("last_seen_ts"),
        }
        for s in states
    ]


def dump_row(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))
