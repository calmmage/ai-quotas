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


def reset_items(samples: list[dict], *, now: datetime) -> list[dict]:
    groups = defaultdict(list)
    for row in samples:
        window = str(row.get("window") or "")
        if (core.is_retired(row) or row.get("individual_cap")
                or window == "worst" or core.window_hours(window, now) is None
                or window in core._NON_QUOTA_WINDOWS or window == "credits"):
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
                refilled = drop >= 5 or (crossed and drop > 0)
                if (same_plan and refilled and old_ts < ts and reset and reset > ts
                        and now-ts <= timedelta(hours=24)):
                    at = old_reset if crossed else ts
                    cycle = int(at.timestamp() // 60)
                    event = dict(kind="reset", provider=provider, account=account,
                                 window=window, severity="INFO", remaining=100-used,
                                 used_percent=used, observed_at=ts.isoformat(),
                                 reset_at=at.isoformat(), resets_at=reset.isoformat(),
                                 dedupe_until=(ts+timedelta(hours=48)).isoformat(),
                                 fingerprint=f"reset:{provider}:{account}:{window}:{cycle}")
            previous = ts, row
        if event:
            items.append(event)
    return items


def format_reset_message(items: list[dict]) -> str:
    """Short account summaries, with all per-window evidence folded below."""
    if not items:
        return ""
    groups = defaultdict(list)
    for item in items:
        groups[(item["provider"], item.get("account") or "login not reported")].append(item)
    escape = html.escape
    lines = ["<b>Quota reset</b>"]
    details = []
    for index, ((provider, account), rows) in enumerate(groups.items()):
        summary = f"{provider} · {account}"
        if len(rows) == 1:
            summary += f" · {rows[0]['window']} · reset to {rows[0]['remaining']:.0f}%"
        else:
            summary += f" · {len(rows)} windows refilled"
        if index < 3:
            lines.append(escape(summary))
        else:
            details.append(summary)
        details.extend(f"{r['window']} · {r['remaining']:.0f}% at reset · observed {r['observed_at']}" for r in rows)
    lines.append('<blockquote expandable>'+escape("\n".join(details))+"</blockquote>")
    return "\n".join(lines)


def reset_batches(items: list[dict]) -> list[list[dict]]:
    """Bound each message; never truncate windows or lose successful delivery."""
    batches, batch, accounts = [], [], set()
    for item in items:
        account = item["provider"], item.get("account")
        candidate = [*batch, item]
        if batch and (len(accounts | {account}) > 3 or len(format_reset_message(candidate)) > 3800):
            batches.append(batch)
            batch, accounts = [], set()
        batch.append(item)
        accounts.add(account)
    if batch:
        batches.append(batch)
    return batches
