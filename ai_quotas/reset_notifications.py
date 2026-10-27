"""Observed refills, per login and window. A deadline alone proves no refill."""
from __future__ import annotations

import html
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ai_quotas import core
from ai_quotas.accounts import normalize_account


def _time(value):
    dt = core.parse_ts(value)
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc) if dt else None


def is_reset_notification_window(window: str) -> bool:
    """Only weekly/monthly quotas notify; shorter windows still get stored."""
    return window.startswith("week") or window == "month" or window.startswith("month_")


def reset_color(used_before_reset: float) -> str:
    if used_before_reset >= 80:
        return "🟢"
    if used_before_reset >= 50:
        return "🟡"
    return "🔴"


def reset_items(samples: list[dict], *, now: datetime) -> list[dict]:
    groups = defaultdict(list)
    for row in samples:
        window = str(row.get("window") or "")
        if (not is_reset_notification_window(window)
                or core.is_retired(row) or row.get("individual_cap")):
            continue
        ts = _time(row.get("observed_at") or row.get("ts"))
        if ts and ts <= now:
            groups[(str(row.get("provider")), normalize_account(row.get("account")), window)].append((ts, row))
    items = []
    for (provider, account, window), readings in sorted(groups.items(), key=lambda entry: str(entry[0])):
        readings.sort(key=lambda x: x[0])
        last_ts, latest = readings[-1]
        if now-last_ts > core.FALLBACK_OK or latest.get("status") != "ok":
            continue
        previous = None
        event = None
        for ts, row in readings:
            used = row.get("used_percent")
            if (row.get("status") != "ok" or not isinstance(used, (float, int))
                    or not math.isfinite(used) or not 0 <= used <= 100):
                continue
            reset = _time(row.get("resets_at"))
            if previous:
                old_ts, old = previous
                old_reset = _time(old.get("resets_at"))
                drop = old["used_percent"] - used
                same_plan = not (old.get("plan") and row.get("plan") and old["plan"] != row["plan"])
                crossed = (old_reset and old_ts < old_reset <= ts and reset
                           and reset-old_reset > core.RESET_TIMESTAMP_TOLERANCE)
                # Small corrections and continuously sliding idle deadlines
                # are not refills. Unknown/missing readings cannot prove one.
                refilled = drop >= 5 or (crossed and drop >= 0)
                if (same_plan and refilled and old_ts < ts and reset and reset > ts
                        and now-ts <= timedelta(hours=24)):
                    at = old_reset if crossed else ts
                    cycle = int(at.timestamp() // 60)
                    event = dict(kind="reset", provider=provider, account=account,
                                 window=window, severity="INFO", remaining=100-used,
                                 used_percent=used, observed_at=ts.isoformat(),
                                 used_before_reset=old["used_percent"],
                                 color=reset_color(old["used_percent"]),
                                 reset_at=at.isoformat(), resets_at=reset.isoformat(),
                                 dedupe_until=(ts+timedelta(hours=48)).isoformat(),
                                 fingerprint=f"reset:{provider}:{account}:{window}:{cycle}")
            previous = ts, row
        if event:
            items.append(event)
    return items


def format_reset_message(items: list[dict]) -> str:
    """Colour each period by consumption before reset; fold the evidence."""
    if not items:
        return ""
    escape = html.escape
    lines = ["<b>Quota reset</b>"]
    details = []
    for index, item in enumerate(items):
        # Flooring keeps 79.999% yellow and 49.999% red in the visible text.
        used = item["used_before_reset"]
        used_text = f"{math.floor(used * 100) / 100:g}"
        account = item.get("account") or "login not reported"
        summary = (f"{reset_color(used)} {item['provider']} · {account} · {item['window']}"
                   f" · {used_text}% used before reset")
        if index < 3:
            lines.append(escape(summary))
        else:
            details.append(summary)
        details.append(f"{item['provider']} · {account} · {item['window']}"
                       f" · {item['remaining']:.0f}% remaining after reset"
                       f" · observed {item['observed_at']}")
    lines.append('<blockquote expandable>'+escape("\n".join(details))+"</blockquote>")
    return "\n".join(lines)


def reset_batches(items: list[dict]) -> list[list[dict]]:
    """Bound each message; never truncate windows or lose successful delivery."""
    batches, batch = [], []
    for item in items:
        candidate = [*batch, item]
        if batch and (len(candidate) > 3 or len(format_reset_message(candidate)) > 3800):
            batches.append(batch)
            batch = []
        batch.append(item)
    if batch:
        batches.append(batch)
    return batches
