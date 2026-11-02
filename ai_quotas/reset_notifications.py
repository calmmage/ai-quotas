"""Observed refills, per login and window. A deadline alone proves no refill."""
from __future__ import annotations

import html
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib.resources import files
from secrets import choice

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


def _valid_emoji_id(value: object) -> bool:
    return (isinstance(value, str) and value.isascii() and value.isdigit()
            and len(value) <= 19 and 0 < int(value) < 2**63)


@lru_cache(maxsize=3)
def _reset_emoji_pool(band: str) -> tuple[tuple[str, str], ...]:
    """Verified, colour-filtered assets bundled with the package; no network."""
    try:
        data = json.loads(files("ai_quotas").joinpath("reset_emoji_pool.json").read_text())
        entries = data["colors"][band]
        return tuple(dict.fromkeys(
            (entry["id"], entry["emoji"]) for entry in entries
            if isinstance(entry, dict) and _valid_emoji_id(entry.get("id"))
            and isinstance(entry.get("emoji"), str) and 0 < len(entry["emoji"]) <= 16
        ))
    except (OSError, ValueError, KeyError, TypeError):
        return ()


def reset_marker(used_before_reset: float) -> str:
    """Fresh uniform draw per line when configured as random; Unicode fallback."""
    from ai_quotas.config import section

    settings = section("telegram").get("reset_emoji", {})
    if not isinstance(settings, dict):
        return reset_color(used_before_reset)
    band = "green" if used_before_reset >= 80 else "yellow" if used_before_reset >= 50 else "red"
    setting = settings.get(band)
    if setting == "random":
        pool = _reset_emoji_pool(band)
        if not pool:
            return reset_color(used_before_reset)
        emoji_id, fallback = choice(pool)
    else:
        # Existing fixed-ID installations continue to work.
        emoji_id = str(setting or "")
        if not _valid_emoji_id(emoji_id):
            return reset_color(used_before_reset)
        fallback = {"green": "💚", "yellow": "💛", "red": "❤️"}[band]
    return f'<tg-emoji emoji-id="{emoji_id}">{html.escape(fallback)}</tg-emoji>'


def reset_items(samples: list[dict], *, now: datetime) -> list[dict]:
    groups = defaultdict(list)
    # The same detailed AGY readings already replace this aggregate in the
    # dashboard/verdicts. Keep sampling it; don't alert on the duplicate pool.
    for row in core.without_orca_antigravity(samples):
        window = str(row.get("window") or "")
        account = normalize_account(row.get("account"))
        if (not is_reset_notification_window(window)
                or not account or core.is_retired(row) or row.get("individual_cap")):
            continue
        cached = row.get("quota_source") in {"codex_rollout", "orca_cache"} or str(row.get("reason") or "").startswith("offline rollout")
        if cached and not _time(row.get("observed_at")):
            continue  # Old cache rows were stamped with collection time.
        ts = _time(row.get("observed_at") or row.get("ts"))
        if ts and ts <= now:
            groups[(str(row.get("provider")), account, window)].append((ts, row))
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
                changed_cycle = (old_reset and reset
                                 and abs(reset-old_reset) > core.RESET_TIMESTAMP_TOLERANCE)
                # Small corrections and continuously sliding idle deadlines
                # are not refills. Unknown/missing readings cannot prove one.
                refilled = (changed_cycle and drop >= 5) or (crossed and drop >= 0)
                if (same_plan and refilled and timedelta(0) < ts-old_ts <= core.FALLBACK_OK
                        and reset and reset > ts
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
        summary = (f"{item['provider']} · {account} · {item['window']}"
                   f" · {used_text}% used before reset")
        if index < 3:
            lines.append(f"{reset_marker(used)} {escape(summary)}")
        else:
            details.append(summary)
        details.append(f"{item['provider']} · {account} · {item['window']}"
                       f" · {item['remaining']:.0f}% remaining after reset"
                       + (f" · reset {item['reset_at']}" if item.get("reset_at") else "")
                       + f" · observed {item['observed_at']}")
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
