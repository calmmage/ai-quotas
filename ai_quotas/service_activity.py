"""Daily evidence for the compact service activity timeline. All dates are UTC."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

DAYS = 90
# Only compare adjacent observations on the same UTC day. A debit across a
# collection gap cannot reliably be placed on a calendar date.
COUNTERS = {
    "meshy": ("Credits available", -1),
    "deepseek": ("USD available", -1),
    "elevenlabs": ("Characters used this cycle", 1),
}


def observed_activity(rows, today=None):
    end = today or datetime.now(timezone.utc).date()
    first = (end - timedelta(days=DAYS - 1)).isoformat()
    reported, samples = defaultdict(dict), defaultdict(list)
    for row in rows:
        provider = row.get("provider")
        if provider not in {*COUNTERS, "openrouter", "higgsfield"}:
            continue
        stamp = datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).astimezone(timezone.utc)
        day = stamp.date().isoformat()
        if day < first or day > end.isoformat():
            continue
        if row.get("window") == "service_usage":
            for d, amount in (row.get("activity_daily") or row.get("daily") or {}).items():
                if first <= d <= day and isinstance(amount, (int, float)):
                    previous = reported[provider].get(d)
                    if previous is None or stamp >= previous[0]:
                        reported[provider][d] = (stamp, amount)
            if provider in COUNTERS:
                value = row.get("metrics", {}).get(COUNTERS[provider][0])
                if value is not None:
                    samples[provider].append((stamp, value))
        elif provider == "openrouter" and row.get("window") == "credits":
            if row.get("used_today") is not None:
                old = reported[provider].get(day)
                if old is None or stamp >= old[0]:
                    reported[provider][day] = (stamp, row["used_today"])
            if row.get("used") is not None:
                samples[provider].append((stamp, row["used"]))
    activity = {}
    for provider in set(samples) | set(reported):
        days = {}
        previous = None
        sign = COUNTERS.get(provider, ("", 1))[1]
        for stamp, value in sorted(samples[provider]):
            if previous:
                delta = stamp - previous[0]
                if timedelta(0) < delta <= timedelta(hours=2) and stamp.date() == previous[0].date():
                    d = stamp.date().isoformat()
                    days[d] = round(days.get(d, 0) + max(0, sign * (value - previous[1])), 6)
            previous = (stamp, value)
        # Native daily totals take precedence over changes seen between samples.
        days.update({d: value for d, (_, value) in reported[provider].items()})
        activity[provider] = days
    return activity


def muse_session_days(path=None, today=None):
    """Count local original sessions that contain a prompt, grouped by start day."""
    index = path or Path.home() / ".local/share/muse/session-index.db"
    if not index.is_file():
        return {}
    end = today or datetime.now(timezone.utc).date()
    first = end - timedelta(days=DAYS - 1)
    try:
        with sqlite3.connect(index.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            rows = db.execute("""
                SELECT date(created_at_us / 1000000, 'unixepoch') AS day, count(*)
                FROM sessions WHERE prompt_count > 0 AND msp_fork_source_session_id IS NULL
                GROUP BY day HAVING day BETWEEN ? AND ? ORDER BY day
            """, (first.isoformat(), end.isoformat())).fetchall()
    except sqlite3.Error:
        return {}
    return dict(rows)
