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


# Same slack the plots use (plots/prep.py SCHEDULED_SLACK_HOURS): a refill this
# close to the published deadline is the scheduled reset.
SCHEDULED_SLACK = timedelta(hours=12)
# A banked reset whose listing vanished this close to the refill was used for it
# (plots/prep.py CREDIT_MATCH_WINDOW, the plot's "Reset used" pill).
CREDIT_MATCH = timedelta(hours=3)


def _credit_used_near(credit_rows, provider, account, at):
    """Title of a banked reset of this login that stopped being listed near ``at``.

    The refill itself confirms the redemption, so this does not wait out
    ``DISAPPEAR_GRACE`` the way the dashboard's lifecycle does.
    """
    from ai_quotas.reset_credits import DISAPPEAR_GRACE, credit_states

    if not credit_rows:
        return None
    rows = [r for r in credit_rows if r.get("provider") == provider]
    for state in credit_states(rows, now=at + CREDIT_MATCH + DISAPPEAR_GRACE):
        if state.get("status") != "consumed":
            continue
        owner = normalize_account(state.get("account"))
        if owner and account and owner != account:
            continue
        ended = _time(state.get("ended_at"))
        if ended and abs(ended - at) <= CREDIT_MATCH:
            return str(state.get("title") or "banked reset")
    return None


def refresh_kind(old_reset, old_ts, ts, credit_title):
    """scheduled | manual | unexpected, the same split the plot draws.

    scheduled: the reading crossed the published deadline, or came within
    the plot's 12 h slack of it. manual: a banked reset of this login was
    used for it ("Reset used" on the plot). unexpected: anything else.
    """
    if old_reset and (old_ts < old_reset <= ts or ts >= old_reset - SCHEDULED_SLACK):
        return "scheduled"
    return "manual" if credit_title else "unexpected"


def _snapped_back(after, old, reset, old_reset) -> bool:
    """The reading after an early refill is back on the old cycle."""
    tolerance = core.RESET_TIMESTAMP_TOLERANCE
    after_reset = _time(after.get("resets_at"))
    if after_reset and reset and abs(after_reset - reset) <= tolerance:
        return False  # still on the new deadline: confirmed, however much was used since
    if after_reset and old_reset and abs(after_reset - old_reset) <= tolerance:
        return True
    return after["used_percent"] >= old["used_percent"] - 2.5


def reset_items(samples: list[dict], *, now: datetime, credit_rows: list[dict] | None = None) -> list[dict]:
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
        valid = []
        for ts, row in readings:
            used = row.get("used_percent")
            if (row.get("status") == "ok" and isinstance(used, (float, int))
                    and math.isfinite(used) and 0 <= used <= 100):
                valid.append((ts, row))
        event = None
        for index in range(1, len(valid)):
            old_ts, old = valid[index - 1]
            ts, row = valid[index]
            used = row["used_percent"]
            reset = _time(row.get("resets_at"))
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
            if not (same_plan and refilled and timedelta(0) < ts-old_ts <= core.FALLBACK_OK
                    and reset and reset > ts and now-ts <= timedelta(hours=24)):
                continue
            at = old_reset if crossed else ts
            credit = _credit_used_near(credit_rows, provider, account, ts) if not crossed else None
            kind = refresh_kind(old_reset, old_ts, ts, credit)
            if kind != "scheduled":
                # An early refill must hold on the next reading. One reading at
                # 0% that snaps back to the old cycle is a probe glitch (Grok
                # week, 2 Nov 2026 10:12: 14% → 0% → 14%, same old deadline).
                if index + 1 >= len(valid):
                    continue
                _, after = valid[index + 1]
                if _snapped_back(after, old, reset, old_reset):
                    continue
            cycle = int(at.timestamp() // 60)
            event = dict(kind="reset", refresh=kind, provider=provider, account=account,
                         window=window, severity="INFO", remaining=100-used,
                         used_percent=used, observed_at=ts.isoformat(),
                         used_before_reset=old["used_percent"],
                         color=reset_color(old["used_percent"]),
                         reset_at=at.isoformat(), resets_at=reset.isoformat(),
                         previous_resets_at=old_reset.isoformat() if old_reset else None,
                         credit_title=credit,
                         dedupe_until=(ts+timedelta(hours=48)).isoformat(),
                         fingerprint=f"reset:{provider}:{account}:{window}:{cycle}")
        if event:
            items.append(event)
    return items
