"""Morning report: every app's plot plus the alerts since yesterday, once a day.

Petr, 3 Nov 2026: «And i guess send daily a full report - snapshots of all
plots (as well as night/yesterday alerts digest?) in the morning?»

Runs on the job that already sends alerts (``ai-quotas sample`` →
``run_after_sample``), at the first sample after MORNING_HOUR local time and
before MORNING_LAST_HOUR. One album: plots, caption on the first photo.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ai_quotas import core
from ai_quotas.accounts import primary_accounts, primary_only
from ai_quotas.alert_plot import PLOT_WINDOWS, plot_png
from ai_quotas.alert_text import app_name, local_tz, login_tag, logins_by_app
from ai_quotas.alerts import load_state, read_log, save_state
from ai_quotas.notify import CAPTION_LIMIT, send_telegram_album
from ai_quotas.paths import samples_path

MORNING_HOUR = 8
MORNING_LAST_HOUR = 13
ORDER = ("claude", "codex", "grok", "agy", "muse")
MAX_PHOTOS = 10


def _hour(name: str, default: int) -> int:
    from ai_quotas.config import section

    value = section("telegram").get(name)
    return value if isinstance(value, int) and 0 <= value <= 23 else default


def _panels(all_samples: list[dict[str, Any]], now: datetime) -> list[tuple[str, str | None]]:
    """(app, login) for every login with a recent reading, primary first."""
    logins = logins_by_app(all_samples, now)
    primaries = primary_accounts(all_samples)
    out: list[tuple[str, str | None]] = []
    for provider in ORDER:
        if provider not in PLOT_WINDOWS:
            continue
        accounts = sorted(logins.get(provider, ()), key=lambda a: (a != primaries.get(provider), a))
        if not accounts and any(r.get("provider") == provider for r in all_samples):
            accounts = [primaries.get(provider)]  # type: ignore[list-item]
        out.extend((provider, account) for account in accounts)
    return out[:MAX_PHOTOS]


def _left_line(result: dict[str, Any]) -> str:
    """``Claude 98% · Codex 91% · Grok 86%`` from the primary verdicts."""
    verdicts = result.get("verdicts") or {}
    bits = []
    for provider in ORDER:
        row = verdicts.get(provider)
        used = row.get("used_percent") if isinstance(row, dict) else None
        if isinstance(used, (int, float)):
            bits.append(f"{app_name(provider)} {max(0, min(100, int(100 - used)))}%")
    return " · ".join(bits)


def format_caption(*, today: datetime, left: str, alerts: list[dict[str, Any]], tz: Any) -> str:
    """Telegram HTML; the alert lines sit in one fold and stay under the caption limit."""
    head = f"morning quotas · {today:%a} {today.day} {today:%b}"
    count = len(alerts)
    second = left or ""
    lines = []
    for entry in alerts:
        ts = core.parse_ts(entry.get("ts"))
        stamp = ts.replace(tzinfo=ts.tzinfo or timezone.utc).astimezone(tz) if ts else None
        prefix = f"{stamp:%a %H:%M} " if stamp else ""
        lines.append(prefix + str(entry.get("head") or entry.get("kind") or ""))
    summary = (f"{count} alert since yesterday" if count == 1
               else f"{count} alerts since yesterday" if count else "no alerts since yesterday")
    visible = "\n".join(html.escape(x) for x in (head, second, summary) if x)
    if not lines:
        return visible
    budget = CAPTION_LIMIT - len(head) - len(second) - len(summary) - 8
    kept = list(lines)
    while kept and len("\n".join(kept + [f"+{count} more"])) > budget:
        kept.pop()
    if len(kept) < count:
        kept.append(f"+{count - len(kept)} more")
    return visible + "\n<blockquote expandable>" + html.escape("\n".join(kept)) + "</blockquote>"


def build(*, path: str | Path | None = None, state_file: str | Path | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    moment = moment.replace(tzinfo=moment.tzinfo or timezone.utc)
    tz = local_tz()
    local = moment.astimezone(tz)
    all_samples = core.load_samples(path if path is not None else samples_path(), all_accounts=True)
    result = core.evaluate(primary_only(all_samples, all_samples), now=moment)
    since = (local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1))
    alerts = read_log(state_file, since=since.astimezone(timezone.utc))
    panels = _panels(all_samples, moment)
    photos, shown = [], []
    for provider, account in panels:
        png = plot_png(all_samples, provider, account, now=moment, tz=tz)
        if png:
            photos.append(png)
            shown.append(f"{app_name(provider)} {login_tag(account)}".strip())
    caption = format_caption(today=local, left=_left_line(result), alerts=alerts, tz=tz)
    return {"date": local.date().isoformat(), "caption": caption, "photos": photos,
            "panels": shown, "alerts": len(alerts)}


def due(state: dict[str, Any], now: datetime) -> bool:
    local = now.astimezone(local_tz())
    first, last = _hour("morning_hour", MORNING_HOUR), _hour("morning_last_hour", MORNING_LAST_HOUR)
    return first <= local.hour < last and state.get("morning") != local.date().isoformat()


def run_morning(*, path: str | Path | None = None, state_file: str | Path | None = None,
                now: datetime | None = None, send: bool = True, force: bool = False,
                record: bool = True, sender: Callable[[str, list[bytes]], str] | None = None) -> dict[str, Any]:
    """Send the report once per local day. Returns {'delivery', ...}.

    ``record=False`` (the manual preview) leaves today's scheduled report due.
    """
    moment = now or datetime.now(timezone.utc)
    moment = moment.replace(tzinfo=moment.tzinfo or timezone.utc)
    state = load_state(state_file)
    if not force and not due(state, moment):
        return {"delivery": "not-due"}
    report = build(path=path, state_file=state_file, now=moment)
    if not send:
        return {**report, "delivery": "not-sent"}
    deliver = sender or (lambda caption, photos: send_telegram_album(photos, caption=caption, parse_mode="HTML"))
    status = deliver(report["caption"], report["photos"])
    if record and status in {"sent", "skip"}:
        save_state({**load_state(state_file), "morning": report["date"]}, state_file)
    return {**report, "delivery": status}
