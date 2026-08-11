"""ai-quotas — subscription quota table (canonical human view).

One table of used% + burn/need rates across vendors and time horizons.
Soft-collects when the latest sample is older than 5 minutes. ``--refresh`` /
``-r`` forces a collect; ``--no-refresh`` always uses the cache.

Columns: quota | used | resets | burn 24h | need rem | burn tot | need avg.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_quotas import core
from ai_quotas.collector import sample_now
from ai_quotas.paths import samples_path

ORDER = {"claude": 0, "codex": 1, "grok": 2, "openrouter": 3, "agy": 4}
ADVISORY_VENDORS = ("claude", "codex", "grok", "agy")
VENDOR_SHORT = {
    "claude": "Claude",
    "codex": "Codex",
    "grok": "Grok",
    "agy": "Gemini",
    "openrouter": "OpenRouter",
}
DEFAULT_ALL_PROVIDERS = frozenset({"claude", "codex", "grok"})
AGY_DEFAULT_WINDOWS = frozenset(
    {
        "5h_gemini_flash",
        "5h_gemini_pro",
        "week_gemini_flash",
        "week_gemini_pro",
    }
)
TITLES: dict[tuple[str, str], str] = {
    ("claude", "5h"): "Claude 5h",
    ("claude", "week"): "Claude week",
    ("claude", "week_fable"): "Claude Fable",
    ("codex", "week"): "Codex week",
    ("grok", "month"): "Grok month",
    ("grok", "week"): "Grok week",
    ("agy", "5h_gemini_flash"): "Gemini Flash 5h",
    ("agy", "5h_gemini_pro"): "Gemini Pro 5h",
    ("agy", "week_gemini_flash"): "Gemini Flash week",
    ("agy", "week_gemini_pro"): "Gemini Pro week",
    ("agy", "week_gpt"): "GPT week",
    ("agy", "week_opus"): "Opus week",
    ("agy", "week_sonnet"): "Sonnet week",
    ("agy", "worst"): "Agy worst",
    ("openrouter", "credits"): "OpenRouter",
    ("openrouter", "unknown"): "OpenRouter",
    ("openrouter", "free_daily"): "OpenRouter free",
}
_ANSI_DIM = "\033[2m"
VENDOR_COLOR: dict[str, str] = {
    "claude": "\033[38;5;180m",
    "codex": "\033[38;5;110m",
    "grok": "\033[38;5;114m",
    "agy": "\033[38;5;176m",
    "openrouter": "\033[38;5;245m",
}

_INDENT = 2
_GAP = 2

REFRESH_MIN_AGE_SECONDS = 5 * 60

_ANSI_RESET = "\033[0m"
_ANSI_RED = "\033[91m"
_ANSI_ORANGE = "\033[33m"
_ANSI_GREEN = "\033[32m"
_ANSI_CYAN = "\033[36m"
_ANSI_BLUE = "\033[94m"
_ANSI_SOFT_AMBER = "\033[38;5;180m"
_RESET_RED_FRAC = 1.0 / 7.0
_RESET_ORANGE_FRAC = 1.0 / 3.0
_RATE_GREEN_MAX = 1.5
_RATE_GREEN_MIN = 1.0 / _RATE_GREEN_MAX
_RATE_ORANGE_MAX = 2.0
_RATE_ORANGE_MIN = 1.0 / _RATE_ORANGE_MAX
_NEED_REM_SOFT = 1.5
_NEED_REM_STRONG = 2.0

PERIOD_RANK = {"month": 0, "week": 1, "5h": 2}
PERIOD_RANK_OTHER = 9


@dataclass(frozen=True)
class TableLayout:
    title: int
    used: int
    resets: int
    rate: int
    show_need: bool
    note: int


@dataclass
class DisplayRow:
    """Structured display model for ``--json`` (not raw sample rows)."""

    provider: str
    window: str
    title: str
    used_percent: float | None
    resets_at: str | None
    hours_to_reset: float | None
    window_hours: float | None
    burn_24h: float | None
    need_rem: float | None
    burn_tot: float | None
    need_avg: float | None
    unit: str
    scale: float
    colors: dict[str, str]
    status: str
    ts: str | None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "window": self.window,
            "title": self.title,
            "used_percent": self.used_percent,
            "resets_at": self.resets_at,
            "hours_to_reset": self.hours_to_reset,
            "window_hours": self.window_hours,
            "burn_24h": self.burn_24h,
            "need_rem": self.need_rem,
            "burn_tot": self.burn_tot,
            "need_avg": self.need_avg,
            "unit": self.unit,
            "scale": self.scale,
            "colors": self.colors,
            "status": self.status,
            "ts": self.ts,
            "note": self.note,
        }


def terminal_columns() -> int:
    raw = os.environ.get("COLUMNS")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return shutil.get_terminal_size(fallback=(80, 24)).columns


def _table_width(layout: TableLayout) -> int:
    n_rates = 4 if layout.show_need else 2
    w = (
        _INDENT
        + layout.title
        + 1
        + layout.used
        + _GAP
        + layout.resets
        + n_rates * (_GAP + layout.rate)
    )
    if layout.note > 0:
        w += _GAP + layout.note
    return w


def compute_layout(*, full: bool = False, columns: int | None = None) -> TableLayout:
    term = columns if columns is not None else terminal_columns()
    budget = max(40, term - 1)

    def shrink(
        title: int,
        used: int,
        resets: int,
        rate: int,
        *,
        show_need: bool,
        note: int,
        note_floor: int = 0,
    ) -> TableLayout:
        floors = {
            "title": 12,
            "used": 4,
            "resets": 7,
            "rate": 7,
            "note": note_floor,
        }
        cur = TableLayout(
            title=title,
            used=used,
            resets=resets,
            rate=rate,
            show_need=show_need,
            note=note,
        )
        order = ("title", "rate", "resets", "used", "note")
        for _ in range(200):
            if _table_width(cur) <= budget:
                return cur
            progressed = False
            for attr in order:
                val = getattr(cur, attr)
                floor = floors[attr]
                if val <= floor:
                    continue
                kwargs = {
                    "title": cur.title,
                    "used": cur.used,
                    "resets": cur.resets,
                    "rate": cur.rate,
                    "show_need": cur.show_need,
                    "note": cur.note,
                }
                kwargs[attr] = val - 1
                cur = TableLayout(**kwargs)
                progressed = True
                break
            if not progressed:
                return cur
        return cur

    lay = shrink(
        18, 5, 8, 8, show_need=True, note=18 if full else 0, note_floor=8 if full else 0
    )
    if _table_width(lay) <= budget:
        return lay
    lay = shrink(
        lay.title,
        lay.used,
        lay.resets,
        lay.rate,
        show_need=False,
        note=lay.note,
        note_floor=8 if full and lay.note else 0,
    )
    if _table_width(lay) <= budget or not lay.note:
        return lay
    return shrink(
        lay.title, lay.used, lay.resets, lay.rate, show_need=lay.show_need, note=0
    )


def rate_headers(rate_w: int) -> tuple[str, str, str, str]:
    if rate_w >= 8:
        return "burn 24h", "need rem", "burn tot", "need avg"
    return "b24h", "need", "btot", "navg"


def _fit_title(title: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(title) <= width:
        return f"{title:<{width}}"
    if width <= 1:
        return title[:width]
    return title[: width - 1] + "…"


def load_latest(path: Path | None = None) -> tuple[list[dict], str | None]:
    samples = core.load_samples(path)
    latest = core.latest_by_key(samples)
    newest = None
    for row in samples:
        ts = row.get("ts")
        if isinstance(ts, str) and (newest is None or ts > newest):
            newest = ts
    return list(latest.values()), newest


def human_delta(resets_at: str | None) -> str:
    if not resets_at:
        return "—"
    try:
        dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    secs = (dt - datetime.now(timezone.utc)).total_seconds()
    if secs < 0:
        return "due"
    hours = int(secs) // 3600
    d, h = divmod(hours, 24)
    if d:
        return f"{d}d {h}h"
    return f"{h}h"


def color_resets_in(
    text: str,
    *,
    hours_to_reset: float | None,
    window_hours: float | None,
    width: int = 8,
) -> str:
    padded = f"{text:>{width}}"
    if text in {"—", "?"} or window_hours is None or window_hours <= 0:
        return padded
    if text == "due" or hours_to_reset is None or hours_to_reset <= 0:
        return f"{_ANSI_RED}{padded}{_ANSI_RESET}"
    frac = hours_to_reset / window_hours
    if frac <= _RESET_RED_FRAC:
        return f"{_ANSI_RED}{padded}{_ANSI_RESET}"
    if frac <= _RESET_ORANGE_FRAC:
        return f"{_ANSI_ORANGE}{padded}{_ANSI_RESET}"
    return padded


def color_enum_vs_ref(rate: float | None, ref: float | None) -> str:
    """Color enum for JSON display model."""
    if rate is None or ref is None or ref <= 0:
        return "neutral"
    if rate <= 0:
        return "under_strong"
    ratio = rate / ref
    if ratio >= _RATE_ORANGE_MAX:
        return "over_strong"
    if ratio > _RATE_GREEN_MAX:
        return "over_mild"
    if ratio <= _RATE_ORANGE_MIN:
        return "under_strong"
    if ratio < _RATE_GREEN_MIN:
        return "under_mild"
    return "on_target"


def color_enum_need_rem(need_rem: float | None, need_avg: float | None) -> str:
    if need_rem is None or need_avg is None or need_avg <= 0:
        return "neutral"
    ratio = need_rem / need_avg
    if ratio >= _NEED_REM_STRONG:
        return "pressure_strong"
    if ratio >= _NEED_REM_SOFT:
        return "pressure_soft"
    return "neutral"


def color_enum_resets(
    hours_to_reset: float | None, window_hours: float | None
) -> str:
    if window_hours is None or window_hours <= 0:
        return "neutral"
    if hours_to_reset is None or hours_to_reset <= 0:
        return "due"
    frac = hours_to_reset / window_hours
    if frac <= _RESET_RED_FRAC:
        return "soon"
    if frac <= _RESET_ORANGE_FRAC:
        return "approaching"
    return "ok"


def color_vs_ref(text: str, rate: float | None, ref: float | None) -> str:
    if rate is None or ref is None or ref <= 0:
        return text
    if rate <= 0:
        return f"{_ANSI_BLUE}{text}{_ANSI_RESET}"
    ratio = rate / ref
    if ratio >= _RATE_ORANGE_MAX:
        return f"{_ANSI_RED}{text}{_ANSI_RESET}"
    if ratio > _RATE_GREEN_MAX:
        return f"{_ANSI_ORANGE}{text}{_ANSI_RESET}"
    if ratio <= _RATE_ORANGE_MIN:
        return f"{_ANSI_BLUE}{text}{_ANSI_RESET}"
    if ratio < _RATE_GREEN_MIN:
        return f"{_ANSI_CYAN}{text}{_ANSI_RESET}"
    return f"{_ANSI_GREEN}{text}{_ANSI_RESET}"


def color_need_rem_pressure(
    text: str,
    need_rem: float | None,
    need_avg: float | None,
) -> str:
    if need_rem is None or need_avg is None or need_avg <= 0:
        return text
    ratio = need_rem / need_avg
    if ratio >= _NEED_REM_STRONG:
        return f"{_ANSI_ORANGE}{text}{_ANSI_RESET}"
    if ratio >= _NEED_REM_SOFT:
        return f"{_ANSI_SOFT_AMBER}{text}{_ANSI_RESET}"
    return text


def fmt_rate_value(rate: float | None, *, scale: float) -> str:
    if rate is None:
        return "—"
    v = rate * scale
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    if abs(v) >= 1:
        return f"{v:.1f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def human_ago(ts: str) -> str:
    try:
        secs = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    except ValueError:
        return "?"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h{mins % 60:02d}m"


def used_cell(row: dict) -> str:
    pct = row.get("used_percent")
    if pct is not None:
        return f"{float(pct):.0f}%"
    return "—"


def format_rate_cell(
    rate: float | None,
    *,
    scale: float,
    unit: str,
    width: int = 8,
    ref: float | None = None,
    color: bool = False,
) -> str:
    raw = fmt_rate_value(rate, scale=scale)
    if raw == "—":
        body = "—"
    else:
        body = f"{raw}{unit}"
    padded = f"{body:>{width}}"
    if not color:
        return padded
    pad = max(0, width - len(body))
    colored = color_vs_ref(body, rate, ref)
    return " " * pad + colored


def filter_default_rows(rows: list[dict], *, full: bool = False) -> list[dict]:
    if full:
        return list(rows)
    out: list[dict] = []
    for r in rows:
        provider = str(r.get("provider") or "?")
        window = str(r.get("window") or "")
        if provider in DEFAULT_ALL_PROVIDERS:
            out.append(r)
        elif provider == "agy" and window in AGY_DEFAULT_WINDOWS:
            out.append(r)
    return out


def row_title(provider: str, window: str) -> str:
    return TITLES.get((provider, window), f"{provider} {window}")


def color_title(provider: str, title: str, *, width: int = 18) -> str:
    padded = _fit_title(title, width)
    color = VENDOR_COLOR.get(provider)
    if not color:
        return padded
    return f"{color}{padded}{_ANSI_RESET}"


def period_rank(window: str) -> int:
    w = (window or "").lower()
    if w == "month" or w.startswith("month"):
        return PERIOD_RANK["month"]
    if w == "week" or w.startswith("week"):
        return PERIOD_RANK["week"]
    if w == "5h" or w.startswith("5h"):
        return PERIOD_RANK["5h"]
    return PERIOD_RANK_OTHER


def sort_rows(rows: list[dict], *, by: str = "period") -> list[dict]:
    def vendor_key(r: dict) -> tuple:
        provider = str(r.get("provider") or "?")
        window = str(r.get("window") or "?")
        return (
            ORDER.get(provider, 9),
            period_rank(window),
            row_title(provider, window),
        )

    def period_key(r: dict) -> tuple:
        provider = str(r.get("provider") or "?")
        window = str(r.get("window") or "?")
        return (
            period_rank(window),
            ORDER.get(provider, 9),
            row_title(provider, window),
        )

    key = vendor_key if by == "vendor" else period_key
    return sorted(rows, key=key)


LEGEND_LINES = (
    "used      = used% of quota (all vendors; no absolute tokens/units for now)",
    "burn 24h  ↔ need rem   recent rate vs rate to finish remaining by reset",
    "burn tot  ↔ need avg   period-average vs even-spend over full window",
    "units     = %/h (5h windows) or %/d (week/month) — share of quota",
    "lanes     = month / week / 5h swimlanes (period sort)",
    "colors    = green on target · warm=OVER (orange→red) · cool=UNDER (cyan→blue)",
    "need rem  = soft amber/orange when ≫ need avg (must accelerate to finish quota)",
    "resets    = red ≤1/7 of window left · orange ≤1/3 · white otherwise",
    "default   = all claude/codex/grok; private agy gemini flash+pro only (--full for rest)",
    "refresh   = soft-collect if sample older than 5m; -r forces; --no-refresh skips",
)


def print_legend() -> None:
    print()
    for line in LEGEND_LINES:
        if line.startswith("colors"):
            print(
                "  colors    = "
                f"{_ANSI_GREEN}green{_ANSI_RESET} on target · "
                f"OVER {_ANSI_ORANGE}orange{_ANSI_RESET}→{_ANSI_RED}red{_ANSI_RESET} · "
                f"UNDER {_ANSI_CYAN}cyan{_ANSI_RESET}→{_ANSI_BLUE}blue{_ANSI_RESET}"
            )
        elif line.startswith("need rem"):
            print(
                "  need rem  = "
                f"{_ANSI_SOFT_AMBER}amber{_ANSI_RESET}/"
                f"{_ANSI_ORANGE}orange{_ANSI_RESET} when ≫ need avg "
                "(accelerate to finish quota)"
            )
        elif line.startswith("resets"):
            print(
                "  resets    = "
                f"{_ANSI_RED}red{_ANSI_RESET} ≤1/7 of window left · "
                f"{_ANSI_ORANGE}orange{_ANSI_RESET} ≤1/3 · white otherwise"
            )
        else:
            print(f"  {line}")
    print()


def _pick_primary_row(rows: list[dict], provider: str) -> dict | None:
    group = [r for r in rows if str(r.get("provider")) == provider]
    if not group:
        return None
    by_win = {str(r.get("window") or ""): r for r in group}
    for key in ("week", "week_fable", "month"):
        if key in by_win:
            return by_win[key]
    weekish = [
        r
        for r in group
        if str(r.get("window") or "").startswith("week")
        and str(r.get("window")) != "worst"
    ]
    if weekish:
        return max(weekish, key=lambda r: float(r.get("used_percent") or 0))
    five = [r for r in group if str(r.get("window") or "").startswith("5h")]
    if five:
        return max(five, key=lambda r: float(r.get("used_percent") or 0))
    return group[0]


def _fmt_left(hours: float | None) -> str:
    if hours is None:
        return "?"
    if hours < 0:
        return "due"
    if hours < 24:
        return f"{int(hours)}h left"
    d, h = divmod(int(hours), 24)
    return f"{d}d {h}h left" if h else f"{d}d left"


def corrective_advisory(
    rows: list[dict],
    metrics_by_key: dict[tuple[str, str], dict[str, Any]],
) -> list[str]:
    snaps: list[dict[str, Any]] = []
    for provider in ADVISORY_VENDORS:
        r = _pick_primary_row(rows, provider)
        if r is None:
            continue
        used = float(r["used_percent"])
        window = str(r.get("window") or "?")
        m = metrics_by_key.get((provider, window)) or {}
        htr = m.get("hours_to_reset")
        wh = m.get("window_hours")
        hours = float(htr) if htr is not None else None
        win_h = float(wh) if wh is not None and float(wh) > 0 else None
        burn_tot = m.get("trend_total_pct_per_hour")
        burn_24h = m.get("trend_24h_pct_per_hour")
        need_avg = (100.0 / win_h) if win_h else None
        rem = max(0.0, 100.0 - used)
        need_rem = (rem / hours) if hours is not None and hours > 0 else None

        frac_left: float | None = None
        if hours is not None and win_h:
            frac_left = max(0.0, min(1.0, hours / win_h))
        elapsed_frac = (1.0 - frac_left) if frac_left is not None else None

        less = 0.0
        reason_less = ""
        if used >= 85:
            less, reason_less = 100 + used, "near cap"
        elif used >= 70 and frac_left is not None and frac_left > 0.25:
            less, reason_less = 70 + used, "heavy use"
        if (
            hours is not None
            and hours > 0
            and burn_tot is not None
            and need_avg is not None
            and need_avg > 0
            and float(burn_tot) >= 1.5 * need_avg
            and used >= 40
        ):
            score = 60 + float(burn_tot) / need_avg * 10
            if score > less:
                less, reason_less = score, "burn ≫ pace"
        if (
            hours is not None
            and hours > 0
            and burn_24h is not None
            and need_rem is not None
            and need_rem > 0
            and float(burn_24h) >= 1.5 * need_rem
            and rem <= 40
        ):
            score = 85 + float(burn_24h) / need_rem * 10
            if score > less:
                less, reason_less = score, "24h will exhaust"

        more = 0.0
        reason_more = ""
        deep_into = elapsed_frac is not None and elapsed_frac >= 0.40
        if deep_into and used <= 8:
            more, reason_more = 90 - used, "barely touched"
        elif (
            deep_into
            and elapsed_frac is not None
            and used < 0.35 * (elapsed_frac * 100)
            and used <= 25
        ):
            more, reason_more = 70 + (elapsed_frac * 100 - used), "far behind clock"
        elif (
            deep_into
            and burn_tot is not None
            and need_avg is not None
            and need_avg > 0
            and float(burn_tot) <= 0.30 * need_avg
            and used <= 15
        ):
            more, reason_more = 65, "burn ≪ pace"
        if hours is not None and hours > 0:
            if hours <= 24 and rem >= 30:
                score = 80 + rem * 0.2
                if score > more:
                    more, reason_more = score, "reset soon · burn remaining"
            elif hours <= 48 and rem >= 50:
                score = 65 + rem * 0.15
                if score > more:
                    more, reason_more = score, "half left · <2d"
        if more > 0:
            more += (4 - ORDER.get(provider, 4)) * 2

        snaps.append(
            {
                "provider": provider,
                "window": window,
                "used": used,
                "hours": hours,
                "less": less,
                "more": more,
                "reason_less": reason_less,
                "reason_more": reason_more,
                "name": VENDOR_SHORT.get(provider, provider),
            }
        )

    if not snaps:
        return []

    HEAVY = 60.0
    lines: list[str] = []

    throttle = max(snaps, key=lambda s: (s["less"], -ORDER.get(s["provider"], 9)))
    throttled = throttle["less"] >= HEAVY
    if throttled:
        why = f" · {throttle['reason_less']}" if throttle["reason_less"] else ""
        lines.append(
            f"use less  {throttle['name']}  "
            f"({throttle['used']:.0f}% · {_fmt_left(throttle['hours'])}{why})"
        )

    boost = max(snaps, key=lambda s: (s["more"], -ORDER.get(s["provider"], 9)))
    boost_ok = boost["more"] >= HEAVY and (
        not throttled or boost["provider"] != throttle["provider"]
    )
    if boost_ok:
        why = f" · {boost['reason_more']}" if boost["reason_more"] else ""
        lines.append(
            f"use more  {boost['name']}  "
            f"({boost['used']:.0f}% · {_fmt_left(boost['hours'])}{why})"
        )

    if throttled:
        free = [
            s
            for s in snaps
            if ORDER.get(s["provider"], 9) > ORDER.get(throttle["provider"], 0)
            and s["used"] <= 12
            and (s["hours"] is None or s["hours"] >= 48)
        ]
        free.sort(key=lambda s: ORDER.get(s["provider"], 9))
        if free:
            names = " / ".join(s["name"] for s in free[:2])
            lines.append(f"spill     {names}  (free)")

    return lines[:3]


def print_corrective(lines: list[str]) -> None:
    if not lines:
        return
    print(f"  {_ANSI_DIM}corrective{_ANSI_RESET}")
    for line in lines:
        if line.startswith("use less"):
            print(f"  {_ANSI_ORANGE}→{_ANSI_RESET} {line}")
        elif line.startswith("use more"):
            print(f"  {_ANSI_GREEN}→{_ANSI_RESET} {line}")
        else:
            print(f"  {_ANSI_DIM}→{_ANSI_RESET} {line}")
    print()


def prepare_rows(
    rows: list[dict],
    newest: str | None,
    *,
    full: bool,
    sort_by: str = "period",
) -> tuple[list[dict], dict[tuple[str, str], dict[str, Any]], list[dict]]:
    """Filter/sort rows and compute metrics. Returns (rows, metrics, stale)."""
    fresh_providers = {r.get("provider") for r in rows if r.get("ts") == newest}
    rows = [
        r
        for r in rows
        if not (
            r.get("ts") != newest
            and r.get("status") != "ok"
            and r.get("provider") in fresh_providers
        )
    ]
    rows = [
        r
        for r in rows
        if r.get("status") == "ok"
        and r.get("used_percent") is not None
        and str(r.get("window") or "") != "overage_credits"
    ]
    rows = filter_default_rows(rows, full=full)
    rows = sort_rows(rows, by=sort_by)

    samples = core.load_samples()
    # Prefer path that matches what load_latest used — caller sets env/path
    metrics_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    now = datetime.now(timezone.utc).astimezone()
    for r in rows:
        if r.get("status") != "ok" or r.get("used_percent") is None:
            continue
        key = (str(r.get("provider")), str(r.get("window")))
        metrics_by_key[key] = core.metrics_for_row(samples, r, now)

    stale: list[dict] = []
    for r in rows:
        if r.get("ts") and newest and r["ts"] != newest:
            stale.append(r)
    return rows, metrics_by_key, stale


def table_rows(
    *,
    full: bool = False,
    sort_by: str = "period",
    percent_only: bool = True,
    path: str | Path | None = None,
    samples: list[dict] | None = None,
) -> list[DisplayRow]:
    """Lib surface: build display-model rows (metrics + burn pairs + color enums)."""
    del percent_only  # always %-only by contract
    if samples is None:
        samples = core.load_samples(path)
    latest = core.latest_by_key(samples)
    rows = list(latest.values())
    newest = None
    for row in samples:
        ts = row.get("ts")
        if isinstance(ts, str) and (newest is None or ts > newest):
            newest = ts
    rows, metrics_by_key, _stale = prepare_rows(
        rows, newest, full=full, sort_by=sort_by
    )
    # Recompute metrics from the samples we already have
    now = datetime.now(timezone.utc).astimezone()
    metrics_by_key = {}
    for r in rows:
        key = (str(r.get("provider")), str(r.get("window")))
        metrics_by_key[key] = core.metrics_for_row(samples, r, now)

    out: list[DisplayRow] = []
    for r in rows:
        provider = str(r.get("provider") or "?")
        window = str(r.get("window") or "?")
        m = metrics_by_key.get((provider, window))
        bm = core.burn_metrics(r, m)
        note = None
        if r.get("ts") and newest and r["ts"] != newest:
            note = f"stale {human_ago(r['ts'])}"
        out.append(
            DisplayRow(
                provider=provider,
                window=window,
                title=row_title(provider, window),
                used_percent=float(r["used_percent"])
                if r.get("used_percent") is not None
                else None,
                resets_at=r.get("resets_at") if isinstance(r.get("resets_at"), str) else None,
                hours_to_reset=bm["hours_to_reset"],
                window_hours=bm["window_hours"],
                burn_24h=bm["burn_24h"],
                need_rem=bm["need_rem"],
                burn_tot=bm["burn_tot"],
                need_avg=bm["need_avg"],
                unit=str(bm["unit"]),
                scale=float(bm["scale"]),
                colors={
                    "burn_24h": color_enum_vs_ref(bm["burn_24h"], bm["need_rem"]),
                    "burn_tot": color_enum_vs_ref(bm["burn_tot"], bm["need_avg"]),
                    "need_rem": color_enum_need_rem(bm["need_rem"], bm["need_avg"]),
                    "resets": color_enum_resets(
                        bm["hours_to_reset"], bm["window_hours"]
                    ),
                },
                status=str(r.get("status") or "ok"),
                ts=r.get("ts") if isinstance(r.get("ts"), str) else None,
                note=note,
            )
        )
    return out


def print_live_table(
    rows: list[dict],
    newest: str | None,
    *,
    full: bool,
    sort_by: str = "period",
    legend: bool = False,
    advisory: bool = True,
    samples_path_override: Path | None = None,
) -> int:
    # Load samples for metrics through the same path resolution
    if samples_path_override is not None:
        samples = core.load_samples(samples_path_override)
    else:
        samples = core.load_samples()

    fresh_providers = {r.get("provider") for r in rows if r.get("ts") == newest}
    rows = [
        r
        for r in rows
        if not (
            r.get("ts") != newest
            and r.get("status") != "ok"
            and r.get("provider") in fresh_providers
        )
    ]
    rows = [
        r
        for r in rows
        if r.get("status") == "ok"
        and r.get("used_percent") is not None
        and str(r.get("window") or "") != "overage_credits"
    ]
    rows = filter_default_rows(rows, full=full)
    rows = sort_rows(rows, by=sort_by)

    metrics_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    now = datetime.now(timezone.utc).astimezone()
    for r in rows:
        if r.get("status") != "ok" or r.get("used_percent") is None:
            continue
        key = (str(r.get("provider")), str(r.get("window")))
        metrics_by_key[key] = core.metrics_for_row(samples, r, now)

    age = ""
    if newest:
        try:
            delta = datetime.now(timezone.utc) - datetime.fromisoformat(newest)
            mins = int(delta.total_seconds() // 60)
            age = f"  (as of {mins}m ago)" if mins else "  (just now)"
        except ValueError:
            pass

    lay = compute_layout(full=full)
    h_b24, h_need, h_btot, h_navg = rate_headers(lay.rate)
    mode = "" if full else "  [default; --full for all + notes]"
    print(f"\n  AI SUBSCRIPTION QUOTA{age}{mode}\n")
    gap = " " * _GAP
    header = (
        f"{' ' * _INDENT}{'quota':<{lay.title}} {'used':>{lay.used}}"
        f"{gap}{'resets':>{lay.resets}}"
        f"{gap}{h_b24:>{lay.rate}}"
    )
    rule = (
        f"{' ' * _INDENT}{'─' * lay.title} {'─' * lay.used}"
        f"{gap}{'─' * lay.resets}"
        f"{gap}{'─' * lay.rate}"
    )
    if lay.show_need:
        header += f"{gap}{h_need:>{lay.rate}}"
        rule += f"{gap}{'─' * lay.rate}"
    header += f"{gap}{h_btot:>{lay.rate}}"
    rule += f"{gap}{'─' * lay.rate}"
    if lay.show_need:
        header += f"{gap}{h_navg:>{lay.rate}}"
        rule += f"{gap}{'─' * lay.rate}"
    if lay.note > 0:
        header += f"{gap}{'note':<{lay.note}}"
        rule += f"{gap}{'─' * lay.note}"
    print(header)
    print(rule)

    def period_lane_name(window: str) -> str:
        rank = period_rank(window)
        return {0: "month", 1: "week", 2: "5h"}.get(rank, "other")

    def print_swimlane(name: str) -> None:
        print(f"  {_ANSI_DIM}· · · {name} · · ·{_ANSI_RESET}")

    stale: list[dict] = []
    last_lane: str | None = None
    for r in rows:
        provider = str(r.get("provider", "?"))
        raw_window = str(r.get("window") or "?")
        title = row_title(provider, raw_window)
        m = metrics_by_key.get((provider, raw_window))
        bm = core.burn_metrics(r, m)
        scale = float(bm["scale"])
        unit = str(bm["unit"])

        if sort_by == "period":
            lane = period_lane_name(raw_window)
            if lane != last_lane:
                print_swimlane(lane)
                last_lane = lane

        used_txt = f"{used_cell(r):>{lay.used}}"
        resets_col = color_resets_in(
            human_delta(r.get("resets_at")),
            hours_to_reset=bm["hours_to_reset"],
            window_hours=bm["window_hours"],
            width=lay.resets,
        )
        burn_24h_txt = format_rate_cell(
            bm["burn_24h"],
            scale=scale,
            unit=unit,
            width=lay.rate,
            ref=bm["need_rem"],
            color=True,
        )
        burn_tot_txt = format_rate_cell(
            bm["burn_tot"],
            scale=scale,
            unit=unit,
            width=lay.rate,
            ref=bm["need_avg"],
            color=True,
        )

        title_txt = color_title(provider, title, width=lay.title)
        line = (
            f"{' ' * _INDENT}{title_txt} {used_txt}"
            f"{gap}{resets_col}"
            f"{gap}{burn_24h_txt}"
        )
        if lay.show_need:
            need_rem_txt = format_rate_cell(
                bm["need_rem"], scale=scale, unit=unit, width=lay.rate
            )
            need_rem_txt = color_need_rem_pressure(
                need_rem_txt, bm["need_rem"], bm["need_avg"]
            )
            need_avg_txt = format_rate_cell(
                bm["need_avg"], scale=scale, unit=unit, width=lay.rate
            )
            line += f"{gap}{need_rem_txt}"
        line += f"{gap}{burn_tot_txt}"
        if lay.show_need:
            line += f"{gap}{need_avg_txt}"

        if r.get("ts") and newest and r["ts"] != newest:
            stale.append(r)
            if lay.note > 0:
                note = f"stale {human_ago(r['ts'])}"
                line += f"{gap}{note:<{lay.note}}"
        elif lay.note > 0:
            line += f"{gap}{'':<{lay.note}}"
        print(line)

    if stale:
        names = ", ".join(f"{r['provider']}/{r['window']}" for r in stale)
        print(f"\n  ⚠  {len(stale)} row(s) carried over from an earlier tick: {names}")
        print(
            "     The last collection did not refresh them — treat as indicative, not current."
        )
    print()
    if advisory:
        print_corrective(corrective_advisory(rows, metrics_by_key))
    if legend:
        print_legend()
    return 0


def sample_age_seconds(newest: str | None) -> float | None:
    if not newest:
        return None
    try:
        ts = datetime.fromisoformat(newest.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()


def should_soft_refresh(
    newest: str | None, *, min_age: float = REFRESH_MIN_AGE_SECONDS
) -> bool:
    age = sample_age_seconds(newest)
    if age is None:
        return True
    return age >= min_age


def _cmd_table(args: argparse.Namespace, path: Path) -> int:
    _, newest_before = load_latest(path)
    if args.no_refresh:
        pass
    elif args.refresh:
        sample_now(path=path)
    elif should_soft_refresh(newest_before):
        sample_now(path=path)

    rows, newest = load_latest(path)
    if not rows:
        print(
            "no samples yet — collect failed or samples path empty",
            file=sys.stderr,
        )
        return 1

    if args.json:
        display = table_rows(
            full=args.full,
            sort_by=args.sort,
            path=path,
        )
        payload = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "newest_sample_ts": newest,
            "rows": [d.as_dict() for d in display],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    return print_live_table(
        rows,
        newest,
        full=args.full,
        sort_by=args.sort,
        legend=args.legend,
        advisory=args.advisory,
        samples_path_override=path,
    )


def _cmd_sample(args: argparse.Namespace, path: Path) -> int:
    rows = sample_now(path=path, append=not args.no_append)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print(f"sampled {len(rows)} row(s) → {path}")
        for r in rows:
            status = r.get("status")
            pct = r.get("used_percent")
            pct_s = f"{float(pct):.0f}%" if pct is not None else "—"
            print(f"  {r.get('provider')}/{r.get('window')}: {status} {pct_s}")
    return 0


def _cmd_verdicts(args: argparse.Namespace, path: Path) -> int:
    if not args.no_refresh and (args.refresh or should_soft_refresh(load_latest(path)[1])):
        if not args.no_sample:
            sample_now(path=path)
    samples = core.load_samples(path)
    result = core.evaluate(samples)
    if args.pretty:
        core.print_pretty(result)
    else:
        print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False))
    return core.exit_code(result)


def _cmd_history(args: argparse.Namespace, path: Path) -> int:
    samples = core.load_samples(path)
    history = core.history_from_samples(samples)
    history["ts"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if args.json:
        print(json.dumps(history, indent=2, ensure_ascii=False))
    else:
        core.print_history(history)
    return 0


def _cmd_legend(_args: argparse.Namespace, _path: Path) -> int:
    print_legend()
    return 0



def _cmd_plot(args: argparse.Namespace, path: Path) -> int:
    """Generate plot dashboards under plots_dir (or --out)."""
    try:
        from ai_quotas.plots.generate import generate_plots
        from ai_quotas.plots.prep import format_money_report, prepare
    except ImportError as e:
        print(
            "plot extras missing — install with: uv sync --extra plot\n"
            f"  ({e})",
            file=sys.stderr,
        )
        return 2

    # subcommand --samples wins over root --samples
    plot_samples = getattr(args, "samples", None)
    if plot_samples:
        path = samples_path(plot_samples)

    engines: tuple[str, ...]
    if args.engine == "all":
        engines = ("plotly", "uplot")
    else:
        engines = (args.engine,)

    out = Path(args.out).expanduser() if args.out else None
    try:
        result = generate_plots(samples=path, out_dir=out, engines=engines)
    except FileNotFoundError as e:
        print(f"no samples: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"plot failed: {e}", file=sys.stderr)
        return 1

    print(f"INDEX {result['index']}")
    print(f"OUT   {result['out_dir']}")
    print(f"rows={result['n_rows']} resets={result['n_resets']}")
    if args.money:
        _, resets, _ = prepare(path)
        print()
        print(format_money_report(resets))

    if args.open:
        index = result["index"]
        opener = shutil.which("open") or shutil.which("xdg-open")
        if opener:
            import subprocess

            subprocess.run([opener, str(index)], check=False)
        else:
            print(f"(no open/xdg-open — open manually: {index})", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    epilog = "column legend (also: ai-quotas legend):\n" + "\n".join(
        f"  {line}" for line in LEGEND_LINES
    )
    ap = argparse.ArgumentParser(
        prog="ai-quotas",
        description=__doc__,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--samples",
        type=str,
        default=None,
        help="Path to samples.jsonl (env AI_QUOTAS_SAMPLES / AI_QUOTAS_DATA_DIR also work)",
    )

    sub = ap.add_subparsers(dest="command")

    # Default table flags also available on root for back-compat
    refresh_g = ap.add_mutually_exclusive_group()
    refresh_g.add_argument(
        "--refresh",
        "-r",
        action="store_true",
        help="force a fresh collect now (ignore 5-minute soft cache)",
    )
    refresh_g.add_argument(
        "--no-refresh",
        action="store_true",
        help="never collect; use cached samples only",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="machine-readable display model (metrics + burn pairs + color enums)",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="show every provider window + note column",
    )
    ap.add_argument(
        "--sort",
        choices=("period", "vendor"),
        default="period",
        help="row order: period (default) or vendor",
    )
    ap.add_argument(
        "--legend",
        action="store_true",
        help="print column/color legend after the table",
    )
    ap.add_argument(
        "--advisory",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="2–3 use-more/use-less lines after the table (default: on)",
    )
    ap.add_argument(
        "--history",
        action="store_true",
        help="peak used%% per reset period (shorthand for: ai-quotas history)",
    )

    p_sample = sub.add_parser("sample", help="run adapters and append samples")
    p_sample.add_argument(
        "--no-append",
        action="store_true",
        help="probe only; do not write samples.jsonl",
    )
    p_sample.add_argument("--json", action="store_true")

    p_verdicts = sub.add_parser("verdicts", help="STOP/WARN/OK JSON (collector dialect)")
    p_verdicts.add_argument("--json", action="store_true", default=True)
    p_verdicts.add_argument("--pretty", action="store_true")
    p_verdicts.add_argument("--no-sample", action="store_true")
    p_verdicts.add_argument("--refresh", "-r", action="store_true")
    p_verdicts.add_argument("--no-refresh", action="store_true")

    p_history = sub.add_parser("history", help="peak used%% per reset period")
    p_history.add_argument("--json", action="store_true")

    sub.add_parser("legend", help="print column/color legend")

    p_plot = sub.add_parser(
        "plot",
        help="generate multi-vendor plot dashboards (needs ai-quotas[plot])",
    )
    p_plot.add_argument(
        "--samples",
        type=str,
        default=None,
        help="Path to samples.jsonl (overrides root --samples / env)",
    )
    p_plot.add_argument(
        "--out",
        type=str,
        default=None,
        help="output directory (default: <data_dir>/plots)",
    )
    p_plot.add_argument(
        "--engine",
        choices=("plotly", "uplot", "all"),
        default="all",
        help="which dashboard engine(s) to write (default: all)",
    )
    p_plot.add_argument(
        "--open",
        action="store_true",
        help="open the index HTML after generation (macOS open / xdg-open)",
    )
    p_plot.add_argument(
        "--money",
        action="store_true",
        help="print money report to stdout after generation",
    )

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    path = samples_path(args.samples)

    # Back-compat: --history on root
    if getattr(args, "history", False) and not args.command:
        return _cmd_history(args, path)

    cmd = args.command
    if cmd == "sample":
        return _cmd_sample(args, path)
    if cmd == "verdicts":
        return _cmd_verdicts(args, path)
    if cmd == "history":
        return _cmd_history(args, path)
    if cmd == "legend":
        return _cmd_legend(args, path)
    if cmd == "plot":
        return _cmd_plot(args, path)

    # Default: table
    return _cmd_table(args, path)


if __name__ == "__main__":
    sys.exit(main())
