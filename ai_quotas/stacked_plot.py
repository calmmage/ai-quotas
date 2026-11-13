"""Stacked provider PNGs for Telegram: the dashboard's «Stack accounts» view. Stdlib only.

Petr, 12 Nov 2026: «Maybe like stack them? And have vertical lines be from one
vendor to another?» · «The 'stacked' versions should look exactly the same» ·
«KEEP THE VERTICAL BAR FEATURE! And have each stacked plot have ITS OWN VERTICAL
BARS with JUST ON ITS TERRITORY» · «GROK 'all-accounts' plot - which has only one
account - hence should be EXACTLY as the non-stacked version.»
Petr, 13 Nov 2026: «update the daily Telegram notifications to send stacked plots
instead of the single plots».

A port of ``quotaStackedStacks`` in ``plots/static/stacked.js`` over the same
``panels.json`` the dashboard renders, drawn with ``alert_plot.Canvas``:
weight = monthly USD / $100 (unknown or free → the provider's smallest known
weight, marked ``?``); stale layers dashed and faint; sampling gaps hatched;
consumption bars, budget guides and resets inside each account's own band.
Browser-local manual weights are not visible here; prices decide.
"""
from __future__ import annotations

import bisect
import json
import math
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

from ai_quotas.alert_plot import BG, GRID, PACE, SPAN, TEXT, Canvas, _rgb

# (provider, title, window): the providers stacked.js stacks, in its order.
STACKS = (
    ("claude", "Claude", "week"),
    ("codex", "Codex", "week"),
    ("agy", "Google", "week_gemini_pro"),
    ("grok", "Grok", "week"),
)
STALE_AFTER = 7200  # seconds behind the newest account, as in stacked.js
GAP = 43200  # a 12h hole between readings is bridged and hatched, not held
PANELS_MAX_AGE = timedelta(hours=6)
BAND_ALPHA, STALE_BAND_ALPHA = 0.32, 0.12  # uplot.html bands: mixHex(color, bg, .32 / .12)
BURN_ALPHA, BURN_MIN_PX = 0.55, 3  # plots/generate.py BURN_TICK_ALPHA; uplot.html minPx
LIGHT = "#e8e8e8"
WIDTH, HEIGHT = 800, 360


def panels_path() -> Path:
    from ai_quotas.paths import plots_dir

    return plots_dir() / "panels.json"


def load_panels(path: str | Path | None = None, *, now: datetime | None = None,
                max_age: timedelta = PANELS_MAX_AGE) -> dict[str, Any] | None:
    """The dashboard's panels.json when it is fresh; None when missing, broken or old."""
    try:
        doc = json.loads(Path(path or panels_path()).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("panels"), list):
        return None
    try:
        written = datetime.fromisoformat(str(doc.get("written_at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    written = written.replace(tzinfo=written.tzinfo or timezone.utc)
    # Written after ``now`` (a replayed report, clock skew) is not this morning's data either.
    return doc if -timedelta(minutes=10) <= moment - written <= max_age else None


def _mix(color: str, toward: str, ratio: float) -> str:
    a, b = _rgb(color), _rgb(toward)
    return "#" + "".join(f"{round(x * ratio + y * (1 - ratio)):02x}" for x, y in zip(a, b))


def _price(panel: dict[str, Any]) -> float | None:
    declared = panel.get("declared_subscription") or {}
    sub = panel.get("subscription") or {}
    price = declared.get("monthly_usd")
    if price is None:  # JS: declared?.monthly_usd ?? subscription?.monthly_usd
        price = sub.get("monthly_usd")
    # Starter access is not evidence of this Google account's subscription bill.
    if panel.get("provider") == "agy" and price == 0 and not declared and sub.get("source") != "configured":
        return None
    return float(price) if isinstance(price, (int, float)) and math.isfinite(price) and price >= 0 else None


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _sample(obs: list[tuple[float, float, int]], t: float, stale: bool) -> tuple[float | None, str]:
    """Value of one account at ``t``: measured / last reading / interpolated / stale."""
    lo = bisect.bisect_right([o[0] for o in obs], t)
    if lo == 0:
        return None, "not observed"
    left = obs[lo - 1]
    if left[0] == t:
        return left[1], "measured"
    if lo < len(obs):
        right = obs[lo]
        if right[2] - left[2] > 1 or right[0] - left[0] > GAP:
            return left[1] + (right[1] - left[1]) * (t - left[0]) / (right[0] - left[0]), "interpolated"
        return left[1], "last reading"
    return left[1], "stale" if stale else "last reading"


def build_stacks(doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """{provider: stack} for providers with 2+ accounts and at least one reading."""
    out: dict[str, dict[str, Any]] = {}
    panels = (doc or {}).get("panels") or []
    for provider, title, window in STACKS:
        unique: dict[str, dict[str, Any]] = {}
        for p in panels:
            if p.get("provider") != provider:
                continue
            series = next((s for s in p.get("series") or [] if s.get("window") == window), None)
            account = p.get("account") or (p.get("source") or {}).get("account")
            if not account or series is None:
                continue
            t, y = series.get("t") or [], series.get("y") or []
            obs = [(float(t[i]), float(y[i]), i) for i in range(min(len(t), len(y))) if _finite(y[i]) and _finite(t[i])]
            latest = obs[-1][0] if obs else 0
            if account not in unique or unique[account]["latest"] < latest:
                label = p.get("account_label") or ("personal" if not p.get("account") else account.split("@")[0])
                unique[account] = {"panel": p, "series": series, "account": account, "label": label,
                                   "latest": latest, "obs": obs}
        accounts = list(unique.values())
        known = [a for a in accounts if a["obs"]]
        if len(accounts) < 2 or not known:
            continue
        observed = max(a["latest"] for a in accounts)
        prices = [p for p in (_price(a["panel"]) for a in accounts) if p and p > 0]
        default = min(prices) / 100 if prices else 1.0
        for a in accounts:
            price = a["price"] = _price(a["panel"])
            a["assumed"] = not (price and price > 0)
            a["weight"] = price / 100 if price and price > 0 else default
            resets_at = a["series"].get("resets_at")
            a["stale"] = (not a["latest"] or observed - a["latest"] > STALE_AFTER
                          or (_finite(resets_at) and resets_at <= observed))
        for i, a in enumerate(known):
            base = a["series"].get("color") or "#8B8F98"
            a["color"] = base if i == 0 else _mix(base, LIGHT, max(0.35, 0.8 - i * 0.18))
            a["base_color"] = base
        capacity = sum(a["weight"] for a in known)
        observed_total = sum(a["obs"][-1][1] * a["weight"] / 100 for a in known if not a["stale"])
        out[provider] = {"provider": provider, "title": title, "window": window, "layers": known,
                         "capacity": capacity, "observed": observed, "observed_total": observed_total,
                         "stale": sum(a["stale"] for a in known), "missing": len(accounts) - len(known),
                         "assumed": sum(a["assumed"] for a in known)}
    return out


def _ticks(capacity: float) -> list[float]:
    rough = capacity / 5
    power = 10 ** math.floor(math.log10(rough))
    step = next(n * power for n in (1, 2, 5, 10) if n * power >= rough)
    ticks, n = [], 0.0
    while n <= capacity + 1e-9:
        ticks.append(round(n, 8))
        n += step
    if ticks[-1] < capacity - 1e-6:
        ticks.append(capacity)
    return ticks


def _unit(v: float) -> str:
    text = f"{v:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def render_stack(stack: dict[str, Any], *, now: datetime, tz: tzinfo | None = None,
                 width: int = WIDTH, height: int = HEIGHT) -> bytes | None:
    layers = stack["layers"]
    capacity = stack["capacity"]
    if not layers or capacity <= 0:
        return None
    tz = tz or timezone.utc
    now_s = now.astimezone(timezone.utc).timestamp()
    end = now_s + 2 * 3600
    start = end - SPAN.total_seconds() - 2 * 3600
    left, right, top, bottom = 56, width - 16, 48, height - 34
    c = Canvas(width, height, BG)

    def x_of(t: float) -> float:
        return left + (t - start) / (end - start) * (right - left)

    def t_of(x: float) -> float:
        return start + (x - left) / (right - left) * (end - start)

    def y_of(units: float) -> float:
        return bottom - units / capacity * (bottom - top)

    for v in _ticks(capacity):
        y = int(round(y_of(v)))
        c.hline(left, right, y, GRID, 1.0 if v == 0 else 0.7, dash=0 if v == 0 else 4)
        label = _unit(v)
        c.text(left - 10 - len(label) * 8, y - 5, label, TEXT)
    day = datetime.fromtimestamp(start, tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    while day.timestamp() < end:
        x = int(round(x_of(day.timestamp())))
        c.vline(x, top, bottom, GRID, 0.5, dash=3)
        label = str(day.day)
        c.text(x - len(label) * 4 + 1, bottom + 10, label, TEXT)
        day += timedelta(days=1)

    last = min(now_s, stack["observed"] or now_s)
    times = sorted({t for a in layers for t, _, _ in a["obs"] if start <= t <= last} | {start, last})
    # cum[i][j]: top of layer i at times[j], in weighted units; layer -1 is the zero baseline.
    raw = [[_sample(a["obs"], t, a["stale"])[0] for t in times] for a in layers]
    cum: list[list[float]] = []
    running = [0.0] * len(times)
    for i, a in enumerate(layers):
        running = [running[j] + (raw[i][j] or 0.0) / 100 * a["weight"] for j in range(len(times))]
        cum.append(running)

    def bounds(i: int, t: float) -> tuple[float, float] | None:
        j = bisect.bisect_right(times, t) - 1
        if j < 0 or raw[i][j] is None:
            return None
        return (cum[i - 1][j] if i else 0.0), cum[i][j]

    # Hatched sampling gaps (display bridge only), then filled bands per column.
    gaps = []
    for a in layers:
        for (t0, _, i0), (t1, _, i1) in zip(a["obs"], a["obs"][1:]):
            if (i1 - i0 > 1 or t1 - t0 > GAP) and t1 > start and t0 < last:
                gaps.append((max(t0, start), min(t1, last)))
    hatch = _rgb("#b6c3d2")
    for g0, g1 in gaps:
        for col in range(int(x_of(g0)), int(x_of(g1)) + 1):
            for row in range(top, bottom):
                if (col + row) % 14 == 0:
                    c.blend(col, row, hatch, 0.14)
    x_last = int(round(x_of(last)))
    prev_tops: list[int] = [-1] * len(layers)  # -1: no edge drawn in the previous column
    for col in range(left, min(right, x_last) + 1):
        t = t_of(col)
        j = bisect.bisect_right(times, t) - 1
        if j < 0:
            continue
        for i, a in enumerate(layers):
            if raw[i][j] is None:
                prev_tops[i] = -1
                continue
            lo, hi = (cum[i - 1][j] if i else 0.0), cum[i][j]
            y_lo, y_hi = int(round(y_of(lo))), int(round(y_of(hi)))
            color = _rgb(a["color"])
            alpha = STALE_BAND_ALPHA if a["stale"] else BAND_ALPHA
            for row in range(y_hi, y_lo):
                c.blend(col, row, color, alpha)
            # Layer edge: a 2px step line; stale layers dashed.
            if a["stale"] and (col // 5) % 2:
                prev_tops[i] = y_hi
                continue
            prev = prev_tops[i] if prev_tops[i] >= 0 else y_hi
            for row in range(min(prev, y_hi), max(prev, y_hi) + 2):
                c.blend(col, row, color, 1.0)
            prev_tops[i] = y_hi

    for i, a in enumerate(layers):
        p = a["panel"]
        color = _rgb(a["color"])
        # Consumption bars: from the layer's floor to its level, inside its band.
        last_x = -1e9
        for tick in p.get("burn_ticks") or []:
            if tick.get("color") != a["base_color"]:
                continue
            for t, y in tick.get("pts") or []:
                if not (start <= t <= last) or not _finite(y):
                    continue
                x = x_of(t)
                if x - last_x < BURN_MIN_PX:
                    continue
                b = bounds(i, t)
                if b is None:
                    continue
                last_x = x
                level = max(b[0], min(b[1], b[0] + y / 100 * a["weight"]))
                c.vline(int(round(x)), int(round(y_of(level))), int(round(y_of(b[0]))), color, BURN_ALPHA)
        # Budget (pace) guides mapped into the band.
        for guide in p.get("budget") or []:
            if guide.get("color") != a["base_color"]:
                continue
            for seg in guide.get("segs") or []:
                for (t0, y0), (t1, y1) in zip(seg, seg[1:]):
                    a0, a1 = max(t0, start), min(t1, last)
                    steps = max(1, int(x_of(a1) - x_of(a0)))
                    for k in range(steps + 1) if a1 > a0 else ():
                        t = a0 + (a1 - a0) * k / steps
                        b = bounds(i, t)
                        if b is None or (k // 4) % 2:
                            continue
                        pct = y0 + (y1 - y0) * (t - t0) / ((t1 - t0) or 1)
                        level = max(b[0], min(b[1], b[0] + pct / 100 * a["weight"]))
                        c.blend(int(round(x_of(t))), int(round(y_of(level))), PACE, 0.9)
        # Resets and unused-quota events: a line on this account's band only.
        for r in p.get("resets") or []:
            t = r.get("t")
            if not _finite(t) or not start <= t <= last:
                continue
            b = bounds(i, t)
            if b is None:
                continue
            edge = _rgb(r.get("edge_dark") or r.get("line_color") or a["color"])
            c.vline(int(round(x_of(t))), int(round(y_of(b[1]))), int(round(y_of(b[0]))), edge, 0.9)

    now_x = int(round(x_of(now_s)))
    c.vline(now_x, top, bottom, TEXT, 0.35)

    # Header: provider and its weighted total (as the dashboard header words it);
    # then each account in its colour with its own remaining % (? = assumed weight; faint = stale).
    c.text(left, 8, f"{stack['title']}  ALL ACCOUNTS".upper(), _rgb(layers[0]["base_color"]))
    summary = header_total(stack).upper()
    c.text(right - len(summary) * 8, 8, summary, TEXT)
    x = left
    for a in layers:
        name = f"{a['label']}{'?' if a['assumed'] else ''} {int(round(a['obs'][-1][1]))}%".upper()
        if x + len(name) * 8 > right:
            break
        faint = _mix(a["color"], "#16181d", 0.45)
        c.text(x, 24, name, _rgb(faint if a["stale"] else a["color"]))
        x += (len(name) + 2) * 8
    return c.png()


def header_total(stack: dict[str, Any]) -> str:
    """``4.48/6 left``, or ``0.4 left 3 stale`` when stale layers can't count (stacked.js header)."""
    if stack["stale"]:
        return f"{_unit(stack['observed_total'])} left {stack['stale']} stale"
    return f"{_unit(stack['observed_total'])}/{_unit(stack['capacity'])} left"


def left_percent(stack: dict[str, Any]) -> int | None:
    """Weighted % left over the accounts with a current reading (stale ones are not current)."""
    fresh = sum(a["weight"] for a in stack["layers"] if not a["stale"])
    return int(round(100 * stack["observed_total"] / fresh)) if fresh > 0 else None


def stack_png(stack: dict[str, Any], *, now: datetime, tz: tzinfo | None = None) -> bytes | None:
    """PNG bytes for one provider stack; None on any failure (the report still goes out)."""
    try:
        moment = now.replace(tzinfo=now.tzinfo or timezone.utc)
        return render_stack(stack, now=moment, tz=tz)
    except Exception:  # noqa: BLE001 — a plot failure must never stop the report
        return None
