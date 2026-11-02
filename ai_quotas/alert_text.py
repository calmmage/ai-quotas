"""Telegram text for live alerts: one event per message, action first.

Petr, 3 Nov 2026: «Put Action, then App (Claude / Grok / Codex etc) then
account (if multiple connected) FIRST IN THE MESSAGE. DO NOT BATCH, IT'S
USUALLY ABOUT A SINGLE THING ANYWAYS. SEPRATE FABLE AND OPUS ALERTS ARE STUPID»

First line: ``<action> · <App>[ · <login>]``. One short visible line under
it; every secondary number goes in one ``<blockquote expandable>``
(write:telegram-formatting).
"""
from __future__ import annotations

import html
import math
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ai_quotas import core
from ai_quotas.accounts import normalize_account

APP = {
    "claude": "Claude", "codex": "Codex", "grok": "Grok", "agy": "Antigravity",
    "openrouter": "OpenRouter", "muse": "Muse", "kimi": "Kimi", "minimax": "MiniMax",
    "opencode": "OpenCode", "antigravity": "Antigravity",
}
# Windows nested inside the app's main window. They are one alert with it.
WINDOW_NAMES = {
    "week": "week", "month": "month", "week_fable": "Fable week",
    "week_gemini_pro": "Gemini week", "week_opus": "Claude+GPT week",
    "week_gemini_argon": "Argon week", "credit_grant": "prepaid grant",
}
REFRESH_ACTION = {
    "scheduled": "scheduled quota refresh",
    "unexpected": "unexpected quota refresh",
    "manual": "manual quota refresh",
}
DEFAULT_TZ = "Europe/Moscow"
LOGIN_RECENT = timedelta(days=14)


def local_tz() -> tzinfo:
    from ai_quotas.config import section

    name = section("telegram").get("timezone") or DEFAULT_TZ
    try:
        return ZoneInfo(str(name))
    except Exception:  # noqa: BLE001 — a bad name keeps the default
        return ZoneInfo(DEFAULT_TZ)


def app_name(provider: str) -> str:
    return APP.get(provider, provider.capitalize() if provider else "quota")


def login_tag(account: str | None) -> str:
    """``petr.b.lavrov@gmail.com`` → ``petr.b.lavrov``."""
    value = normalize_account(account) or (account or "").strip()
    return value.split("@", 1)[0]


def window_name(window: str) -> str:
    return WINDOW_NAMES.get(window, window.replace("_", " "))


def logins_by_app(samples: Iterable[dict[str, Any]], now: datetime) -> dict[str, set[str]]:
    """Logins of each app with an ok reading in the last two weeks."""
    out: dict[str, set[str]] = {}
    for row in samples:
        account = normalize_account(row.get("account"))
        provider = row.get("provider")
        if not account or not isinstance(provider, str) or row.get("status") != "ok":
            continue
        if core.is_retired(row) or row.get("individual_cap"):
            continue
        ts = core.parse_ts(row.get("ts"))
        if ts is None:
            continue
        ts = ts.replace(tzinfo=ts.tzinfo or timezone.utc)
        if now - ts <= LOGIN_RECENT:
            out.setdefault(provider, set()).add(account)
    return out


def head(action: str, provider: str, account: str | None, logins: dict[str, set[str]]) -> str:
    parts = [action, app_name(provider)]
    if account and len(logins.get(provider, ())) > 1:
        parts.append(login_tag(account))
    return " · ".join(parts)


def when(value: Any, tz: tzinfo) -> str:
    """``Thu 5 Nov 11:00`` in the reader's zone."""
    dt = value if isinstance(value, datetime) else core.parse_ts(value) if isinstance(value, str) else None
    if dt is None:
        return ""
    dt = dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(tz)
    return f"{dt:%a} {dt.day} {dt:%b %H:%M}"


def pct(value: Any) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return ""
    # Floor: 79.99% used must not read as 80% (the colour bands use the same edge).
    return f"{max(0, min(100, math.floor(float(value))))}%"


def _fold(lines: list[str]) -> str:
    lines = [line for line in lines if line]
    if not lines:
        return ""
    return "\n<blockquote expandable>" + html.escape("\n".join(lines)) + "</blockquote>"


def _refresh_in(item: dict[str, Any]) -> str:
    hours = item.get("hours_to_reset")
    if not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours <= 0:
        return ""
    return f"refresh in {core.format_duration_hours(hours)}"


def _pace_text(pace: Any) -> str:
    text = str(pace or "").strip()
    if not text or text == "—":
        return ""
    # The verdict pace carries a colour dot first; the fold keeps words only.
    return text.split(" ", 1)[1] if not text[0].isalnum() and " " in text else text


def format_alert(item: dict[str, Any], *, logins: dict[str, set[str]], tz: tzinfo,
                 marker: str = "") -> str:
    """HTML for one burn / spare / expiring-credit alert."""
    provider = str(item.get("provider") or "")
    account = item.get("account")
    window = str(item.get("window") or "week")
    kind = item.get("kind")
    other_window = window_name(window) if window not in {"week", "credit_grant"} else ""
    if kind == "burn":
        action = "burn stop" if item.get("severity") == "STOP" else "burn warning"
        visible = " · ".join(x for x in (f"{pct(item.get('remaining'))} left", _refresh_in(item)) if x)
        projected = item.get("projected_final")
        fold = [
            other_window,
            _pace_text(item.get("pace")),
            f"on track for {projected:.0f}% by refresh" if isinstance(projected, (int, float)) else "",
            f"refresh {when(item.get('resets_at'), tz)}" if item.get("resets_at") else "",
            (f"{item['resets_available']} banked resets to use" if item.get("resets_available", 0) > 1
             else "1 banked reset to use" if item.get("resets_available") else ""),
        ]
    elif kind in {"spare", "spare_urgent", "reset_soon"}:
        action = "use it before reset"
        visible = " · ".join(x for x in (f"{pct(item.get('remaining'))} left", _refresh_in(item)) if x)
        fold = [
            other_window,
            f"refresh {when(item.get('resets_at'), tz)}" if item.get("resets_at") else "",
            "unused quota is lost at refresh",
        ]
    elif kind == "credit_expiring":
        action = "banked reset expires" if window != "credit_grant" else "prepaid grant expires"
        hours = item.get("hours_to_reset")
        lead = (f"{round(hours / 24)}d" if isinstance(hours, (int, float)) and hours >= 24
                else core.format_duration_hours(hours))
        visible = f"expires in {lead}"
        fold = [str(item.get("title") or ""), f"expires {when(item.get('expires_at'), tz)}"]
    else:
        action = str(kind or "quota alert").replace("_", " ")
        visible, fold = "", []
    first = html.escape(head(action, provider, account, logins))
    second = (f"{marker} " if marker else "") + html.escape(visible)
    return first + ("\n" + second if second.strip() else "") + _fold(fold)


def format_refresh(items: list[dict[str, Any]], *, logins: dict[str, set[str]], tz: tzinfo,
                   marker: str = "") -> str:
    """HTML for one app/login refresh; nested windows fold into one line."""
    items = sorted(items, key=lambda it: (it.get("window") != "week", str(it.get("window"))))
    main = items[0]
    provider = str(main.get("provider") or "")
    kinds = [it.get("refresh") for it in items]
    kind = next((k for k in ("manual", "unexpected", "scheduled") if k in kinds), "scheduled")
    used = main.get("used_before_reset")
    visible = f"{pct(used)} used before refresh" if isinstance(used, (int, float)) else ""
    others = [window_name(str(it.get("window"))) for it in items[1:]]
    main_window = str(main.get("window") or "week")
    fold = [
        window_name(main_window) if main_window not in {"week", "month"} else "",
        f"{', '.join(others)} refreshed too" if others else "",
        f"{pct(main.get('remaining'))} left now" if main.get("remaining") is not None else "",
        (f"banked reset used · {main['credit_title']}" if main.get("credit_title")
         else "banked reset used" if kind == "manual" else ""),
        (f"due {when(main.get('previous_resets_at'), tz)}, came early"
         if kind != "scheduled" and main.get("previous_resets_at") else ""),
        f"next refresh {when(main.get('resets_at'), tz)}" if main.get("resets_at") else "",
        f"seen {when(main.get('observed_at'), tz)}" if main.get("observed_at") else "",
    ]
    first = html.escape(head(REFRESH_ACTION[kind], provider, main.get("account"), logins))
    second = (f"{marker} " if marker else "") + html.escape(visible)
    return first + ("\n" + second if second.strip() else "") + _fold(fold)


def plain(text: str) -> str:
    """Telegram HTML → plain text for the urgent DM, email and the log."""
    import re

    out = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text, flags=re.S)
    out = re.sub(r"<blockquote[^>]*>", "\n", out)
    out = re.sub(r"<[^>]+>", "", out)
    return html.unescape(out).strip()


def first_line(text: str) -> str:
    return plain(text).splitlines()[0] if plain(text) else ""
