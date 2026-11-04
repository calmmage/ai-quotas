"""p50 / p90 tokens per second. Noisy, parallel, and compaction rows stay stored."""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_quotas.speed.rows import MIN_OUTPUT_TOKENS, MIN_SECONDS
from ai_quotas.speed.store import connect
from ai_quotas.speed.timeutil import MOSCOW

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
_FAST_TIERS = ("fast", "priority", "ultrafast")


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, math.ceil(p * len(ordered)))
    return ordered[rank - 1]


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 1)


def _table_ready(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'response_speed'"
    ).fetchone()
    return row is not None


def _eligible(conn: sqlite3.Connection, since: datetime) -> list[sqlite3.Row]:
    if not _table_ready(conn):
        return []
    # Rows are stored as Europe/Moscow timestamps with milliseconds, so the
    # text order matches time order. The partial index covers this predicate.
    bound = since.astimezone(MOSCOW).isoformat(timespec="milliseconds")
    return conn.execute(
        """
        SELECT provider, model, tier, ts, tokens_per_second
        FROM response_speed
        WHERE ts >= ?
          AND output_tokens >= ?
          AND seconds >= ?
          AND parallel_tools = 0
          AND compaction = 0
          AND tokens_per_second IS NOT NULL
        """,
        (bound, MIN_OUTPUT_TOKENS, MIN_SECONDS),
    ).fetchall()


def _group(rows: list[sqlite3.Row]) -> list[dict]:
    buckets: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        rate = row["tokens_per_second"]
        if rate is None:
            continue
        buckets[(row["provider"], row["model"], row["tier"])].append(float(rate))
    table = []
    for (provider, model, tier), rates in buckets.items():
        table.append(
            {
                "provider": provider,
                "model": model,
                "tier": tier,
                "p50": _round(percentile(rates, 0.50)),
                "p90": _round(percentile(rates, 0.90)),
                "n": len(rates),
            }
        )
    table.sort(key=lambda item: (-item["n"], item["provider"], item["model"], item["tier"]))
    return table


def _compare(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["provider"], row["model"])
        slot = grouped.setdefault(
            key,
            {
                "provider": row["provider"],
                "model": row["model"],
                "standard": None,
                "fast": None,
                "priority": None,
                "ultrafast": None,
            },
        )
        if row["tier"] in slot:
            slot[row["tier"]] = {"p50": row["p50"], "p90": row["p90"], "n": row["n"]}
    ordered = list(grouped.values())
    ordered.sort(
        key=lambda item: -(
            sum((item[name] or {}).get("n") or 0 for name in ("standard", *_FAST_TIERS))
        )
    )
    return ordered


def _daily(rows: list[sqlite3.Row]) -> list[dict]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row["tokens_per_second"] is None:
            continue
        # ts is Europe/Moscow, so the date prefix is that calendar day.
        day = str(row["ts"])[:10]
        buckets[day].append(float(row["tokens_per_second"]))
    points = []
    for day in sorted(buckets):
        rates = buckets[day]
        points.append({"date": day, "p50": _round(percentile(rates, 0.50)), "n": len(rates)})
    return points


def window_summary(conn: sqlite3.Connection, window: str, *, now: datetime | None = None) -> dict:
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {', '.join(WINDOWS)}")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    since = current - WINDOWS[window]
    kept = _eligible(conn, since)
    rows = _group(kept)
    return {
        "window": window,
        "since": since.isoformat(timespec="seconds"),
        "rows": rows,
        "compare": _compare(rows),
        "daily": _daily(kept),
    }


def summary(database: str | Path, *, now: datetime | None = None) -> dict:
    path = Path(database).expanduser()
    with connect(path) as conn:
        windows = {name: window_summary(conn, name, now=now) for name in WINDOWS}
    return {
        "database": str(path),
        "min_output_tokens": MIN_OUTPUT_TOKENS,
        "min_seconds": MIN_SECONDS,
        "seconds": "end-to-end: response end minus the previous user message or tool result",
        "excluded_from_stats": [
            "output_tokens < 20",
            "seconds < 0.3",
            "parallel tool batch",
            "compaction or summary turn",
            "grok turn with more than one model call (no per-call split)",
        ],
        "windows": windows,
    }


def speed_payload(database: str | Path | None, window: str, *, now: datetime | None = None) -> dict:
    if window not in WINDOWS:
        window = "7d"
    if database is None or not Path(database).expanduser().is_file():
        return {"window": window, "rows": [], "compare": [], "daily": [], "empty": True}
    path = Path(database).expanduser()
    with connect(path) as conn:
        body = window_summary(conn, window, now=now)
    body["empty"] = not body["rows"]
    body["min_output_tokens"] = MIN_OUTPUT_TOKENS
    body["min_seconds"] = MIN_SECONDS
    return body


def format_table(body: dict) -> str:
    rows = body.get("rows") or []
    header = (
        f"{body.get('window', '')}  end-to-end tok/s"
        "  (keeps every row; stats skip parallel, compaction, <20 tokens, <0.3s)"
    )
    if not rows:
        return header + "\n  no eligible responses"
    lines = [header, f"{'provider':<12} {'model':<28} {'tier':<10} {'p50':>8} {'p90':>8} {'n':>7}"]
    for row in rows:
        lines.append(
            f"{row['provider']:<12} {row['model']:<28} {row['tier']:<10} "
            f"{row['p50']:>8} {row['p90']:>8} {row['n']:>7}"
        )
    return "\n".join(lines)
