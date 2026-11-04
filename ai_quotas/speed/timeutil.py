"""Timestamps from local chat logs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# Stored timestamps use one offset so a text compare matches time order.
# This Mac keeps Europe/Moscow, and the daily Speed buckets use that calendar.
MOSCOW = ZoneInfo("Europe/Moscow")


def parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return _from_unix(float(value))
    text = str(value).strip()
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        return _from_unix(float(text))
    raw = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _from_unix(n: float) -> datetime | None:
    if n > 1e14:  # microseconds
        n = n / 1_000_000.0
    elif n > 1e11:  # milliseconds
        n = n / 1_000.0
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(MOSCOW).isoformat(timespec="milliseconds")


def seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    if delta <= 0:
        return None
    return delta
