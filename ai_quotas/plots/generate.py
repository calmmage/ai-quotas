"""Generate multi-vendor plot dashboards (plotly + uplot).

HTML/JS templates live in ``ai_quotas/plots/static/`` and ship in the wheel.
Filled pages are runtime output only (not committed). Requires optional plot
deps: ``pip install 'ai-quotas[plot]'`` or ``uv sync --extra plot``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from importlib.resources import files
from pathlib import Path

import pandas as pd

from ai_quotas.boosts import boost_badge, boost_states
from ai_quotas.paths import samples_path
from ai_quotas.storage import load_boosts
from ai_quotas.plots.prep import (
    VENDORS,
    PRIMARY_SERIES,
    CREDIT_MATCH_WINDOW,
    annotate_reset_tokens,
    annotates_reset,
    budget_line,
    color_map,
    cumulative_burn,
    daily_spend_for_vendor,
    format_money_report,
    credit_badge,
    load_credit_events,
    fmt_delta,
    is_session_series,
    local_tz,
    money_summary,
    plot_series_for_vendor,
    prepare,
    series_order_for_vendor,
    subtitle_resets,
    sustainable_rate,
    title_vendor,
    tokens_per_percent,
    window_usd_value,
    default_plots_dir,
)

RESULTS: list[tuple[str, Path, str]] = []


def _static(name: str) -> str:
    return files("ai_quotas.plots.static").joinpath(name).read_text(encoding="utf-8")


_PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]+__")


def _fill(template: str, mapping: dict[str, str]) -> str:
    for key, value in mapping.items():
        template = template.replace(key, value)
    leftover = sorted(set(_PLACEHOLDER.findall(template)))
    if leftover:
        raise ValueError(f"unfilled plot template placeholders: {leftover}")
    return template


# Shared by plotly + uPlot. Loaded from static/ so `{` in the JS stays literal.
# Ticks follow the *visible* span, aligned to local midnights / Mondays.
TIME_AXIS_JS = _static("time_axis.js")
THEME_JS = _static("theme.js")


def _local(ts: datetime) -> datetime:
    return ts.astimezone(local_tz())


def _register(name: str, path: Path, note: str) -> None:
    RESULTS.append((name, path, note))
    print(f"  ok  {name:36} → {path}")


def _vendor_resets(resets, vendor, series=None):
    """Resets for a vendor, optionally scoped to the series actually plotted.

    Without the series filter a single-series plot still receives resets from
    the vendor's other windows, and `colors[r.series]` then raises KeyError.
    """
    return [
        r
        for r in resets
        if r.vendor == vendor and (series is None or r.series in series)
    ]


def _pill_usd(usd: float) -> str:
    if abs(usd) < 0.05:
        return "$0"
    sign = "+" if usd > 0 else "-"
    return f"{sign}${abs(usd):.0f}"


def _marker_colors(kind: str, series_color: str) -> dict:
    if kind in {"burn", "credit_expired"}:
        return {
            "edge": "#B00020",
            "face": "rgba(253,236,234,0.95)",
            "edge_dark": "#ff5c72",
            "face_dark": "rgba(176,0,32,0.22)",
        }
    if kind in {"free", "credit_used"}:
        return {
            "edge": "#1B7A3D",
            "face": "rgba(232,248,238,0.95)",
            "edge_dark": "#3ddc84",
            "face_dark": "rgba(27,122,61,0.22)",
        }
    return {
        "edge": series_color,
        "face": "rgba(255,255,255,0.92)",
        "edge_dark": series_color,
        "face_dark": "rgba(255,255,255,0.08)",
    }


def _plot_marker(at, *, line_color: str, kind: str, pill: str, tooltip: str, series_color: str) -> dict:
    return {
        "t": int(_local(at).timestamp()),
        "kind": kind,
        "pill": pill,
        "tooltip": tooltip,
        "label": pill,
        "line_color": line_color,
        **_marker_colors(kind, series_color),
    }


def _token_bit(label: str) -> str | None:
    if "tok" not in label:
        return None
    tail = label.rsplit("·", 1)[-1].strip()
    return tail if "tok" in tail else None


def _reset_plot_markers(resets, credits, vendor, colors: dict) -> list[dict]:
    """Concise on-plot pills; full copy lives in `tooltip` for hover."""
    vendor_resets = [
        r for r in _vendor_resets(resets, vendor) if annotates_reset(r.series)
    ]
    consumed = [e for e in credits if e.vendor == vendor and e.status == "consumed"]
    expired = [
        e for e in credits if e.vendor == vendor and e.status == "expired" and e.expires_at
    ]
    claimed: set[int] = set()
    out: list[dict] = []

    for e in consumed:
        when = e.ended_at or e.expires_at
        if when is None:
            continue
        near = [r for r in vendor_resets if abs(r.at - when) <= CREDIT_MATCH_WINDOW]
        matched = min(near, key=lambda r: abs(r.at - when)) if near else None
        if matched is not None:
            claimed.add(id(matched))
        bits = ["Quota reset used", e.title or "reset"]
        if e.money_label:
            bits.append(e.money_label)
        if matched is not None:
            bits.append(matched.series)
            if matched.money_label:
                bits.append(f"leftover {matched.money_label}")
            bits.append(fmt_delta(matched.period_before) if matched.period_before else "first")
            tok = _token_bit(matched.label)
            if tok:
                bits.append(tok)
        color = colors.get(matched.series, "#1B7A3D") if matched is not None else "#1B7A3D"
        out.append(
            _plot_marker(
                when,
                line_color=color,
                kind="credit_used",
                pill="Reset used",
                tooltip=" · ".join(bits),
                series_color=color,
            )
        )

    for r in vendor_resets:
        if id(r) in claimed:
            continue
        if r.kind == "burn" or r.money_usd < 0:
            kind, title = "burn", "Lost unused"
        elif r.kind == "free" or r.money_usd > 0:
            kind, title = "free", "Gained free"
        else:
            kind, title = "reset", "Reset"
        pill = _pill_usd(r.money_usd) if r.money_label else "Reset"
        bits = [title, r.series]
        if r.money_label:
            bits.append(r.money_label)
        bits.append(fmt_delta(r.period_before) if r.period_before is not None else "first")
        bits.append(f"{r.remaining_before:.0f}% leftover")
        tok = _token_bit(r.label)
        if tok:
            bits.append(tok)
        out.append(
            _plot_marker(
                r.at,
                line_color=colors[r.series],
                kind=kind,
                pill=pill,
                tooltip=" · ".join(bits),
                series_color=colors[r.series],
            )
        )

    for e in expired:
        bits = ["Quota reset expired unused", e.title or "reset"]
        if e.money_label:
            bits.append(e.money_label)
        out.append(
            _plot_marker(
                e.expires_at,
                line_color="#B00020",
                kind="credit_expired",
                pill="Reset expired",
                tooltip=" · ".join(bits),
                series_color="#B00020",
            )
        )

    out.sort(key=lambda m: m["t"])
    return out


_BURN_STEPS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0)


BURN_TICK_ALPHA = 0.55  # tune density visibility here


BURN_TICK_WIDTH = 1.1  # matplotlib lw / plotly line width


def _burn_density_ticks(g, target_ticks: int = 140):
    """(x, y_top) where cumulative used% crosses an adaptive step.

    Reset/gap semantics come from `cumulative_burn`, so ticks and the rate line
    always agree. Step size adapts to total counted burn so marks stay
    dense-but-legible regardless of how fast the series burns.

    y_top is read off the curve at the crossing — `used[i-1] + burn-into-this-step`
    — which is exact because remaining = 100 - used. Deriving it from the step
    level alone would assume used% starts at 0 and float the ticks far above the
    line for any series that starts mid-window (Codex week starts at 47%).
    """
    w = cumulative_burn(g)
    if len(w.ts) < 2:
        return []
    total = sum(w.inc)
    if total <= 0:
        return []
    step = next((s for s in _BURN_STEPS if s >= total / target_ticks), _BURN_STEPS[-1])

    ticks: list = []
    next_level = step
    for i in range(1, len(w.ts)):
        if w.seg[i] != w.seg[i - 1]:
            next_level = step  # reset or sampling gap → restart the tick phase
            continue
        d = w.inc[i]
        if d <= 0:
            continue
        local_start = w.cum[i] - d
        t0, t1 = w.ts[i - 1], w.ts[i]
        while next_level <= w.cum[i]:
            into = next_level - local_start
            ticks.append((t0 + (t1 - t0) * (into / d), 100.0 - (w.used[i - 1] + into)))
            next_level += step
    return ticks


def _load_spend_rows(samples: Path | None) -> list[dict]:
    """Load spend next to the samples source. Empty when there is no harvest."""
    from ai_quotas.spend import load_spend

    try:
        if samples is not None:
            p = Path(samples)
            if p.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                return load_spend(p)
            sibling = p.with_name("spend.jsonl")
            if sibling.is_file():
                return load_spend(sibling)
            return []
        return load_spend()
    except OSError:
        return []


_VENDOR_PROVIDER = {"Claude": "claude", "Codex": "codex", "Grok": "grok", "Gemini": "agy"}


def _load_boost_states(samples: Path | None) -> list[dict]:
    try:
        rows = load_boosts(samples_path(samples))
    except Exception:
        rows = []
    return boost_states(rows)


def _vendor_panel_payload(
    df,
    resets,
    vendor,
    *,
    spend_rows: list | None = None,
    credits: list | None = None,
    boosts: list | None = None,
) -> dict:
    """Shared JSON payload for one vendor panel (plotly / uplot dashboards).

    All of the vendor's series are drawn, but the burn visuals (density ticks
    and the constant-pace line) are computed for the primary window only —
    stacking ticks from three series made them unreadable.
    """
    order = series_order_for_vendor(df, vendor)
    focus = plot_series_for_vendor(df, vendor)
    colors = color_map(order)
    sub = df[df["vendor"] == vendor]
    spend_rows = spend_rows or []
    tpp = tokens_per_percent(
        df, resets, spend_rows, vendor, PRIMARY_SERIES.get(vendor)
    )
    series_payload = []
    tick_payload = []
    rate_payload = []
    for s in order:
        g = sub[sub["series"] == s].dropna(subset=["remaining_percent"]).sort_values("ts_local")
        session = is_session_series(s)
        win_usd = 0.0 if session else window_usd_value(s, vendor)[0]
        series_payload.append(
            {
                "label": s,
                "color": colors[s],
                "focus": s in focus,
                "dim": session,
                "window_usd": round(win_usd, 4),
                "tokens_per_pct": None if session or tpp is None else round(tpp, 4),
                "t": [int(ts.timestamp()) for ts in g["ts_local"]],
                "y": [None if pd.isna(v) else float(v) for v in g["remaining_percent"]],
            }
        )
        if s not in focus:
            continue
        tick_payload.append(
            {
                "color": colors[s],
                "pts": [[int(t.timestamp()), float(y)] for t, y in _burn_density_ticks(g)],
            }
        )
        # Constant-pace depletion line, in the same % space as the data.
        rate_payload.append(
            {
                "label": f"{s} · pace {sustainable_rate(s):.2f} %/h",
                "color": colors[s],
                "pace": round(sustainable_rate(s), 4),
                "segs": [
                    [[int(t.timestamp()), round(y, 3)] for t, y in seg]
                    for seg in budget_line(g, s)
                ],
            }
        )
    credits = credits or []
    rlist = _reset_plot_markers(resets, credits, vendor, colors)
    badge = credit_badge(credits, vendor)
    subtitle = subtitle_resets([r for r in resets if annotates_reset(r.series)], vendor)
    if badge:
        subtitle = f"{subtitle}  ·  {badge}"
    boost_states_list = boosts or []
    provider = _VENDOR_PROVIDER.get(vendor, vendor.lower())
    b_badge = boost_badge(boost_states_list, provider)
    if b_badge:
        subtitle = f"{subtitle}  ·  {b_badge}" if subtitle else b_badge
    return {
        "vendor": vendor,
        "title": title_vendor(vendor, df),
        "subtitle": subtitle,
        "boosts": {
            "badge": b_badge,
            "items": [s for s in boost_states_list if s.get("provider") == provider],
        },
        "reset_credits": {
            "available": sum(1 for e in credits if e.vendor == vendor and e.status == "available"),
            "badge": badge,
            "items": [
                {
                    "credit_id": e.credit_id,
                    "title": e.title,
                    "status": e.status,
                    "expires_at": e.expires_at.isoformat() if e.expires_at else None,
                    "money_usd": round(e.money_usd, 2),
                    "label": e.money_label,
                }
                for e in credits
                if e.vendor == vendor
            ],
        },
        "series": series_payload,
        "burn_ticks": tick_payload,
        "budget": rate_payload,
        "resets": rlist,
        "spend": daily_spend_for_vendor(spend_rows, vendor),
    }


def plot_plotly(
    df,
    resets,
    cutoff,
    out_root: Path,
    spend_rows: list | None = None,
    credits: list | None = None,
    boosts: list | None = None,
) -> None:
    """Single page: all 4 vendors, plots-per-row control, auto-scale on resize."""
    d = out_root / "03_plotly"
    d.mkdir(parents=True, exist_ok=True)
    panels = [
        _vendor_panel_payload(
            df, resets, v, spend_rows=spend_rows, credits=credits, boosts=boosts
        )
        for v in VENDORS
    ]
    # drop stale per-vendor pages from the old layout
    for stale in d.glob("*.html"):
        if stale.name != "index.html":
            stale.unlink(missing_ok=True)
    path = d / "index.html"
    path.write_text(
        _fill(
            _static("plotly.html"),
            {
                "__CUTOFF__": cutoff.isoformat(),
                "__PANELS__": json.dumps(panels),
                "__BURN_W__": str(BURN_TICK_WIDTH),
                "__BURN_A__": str(BURN_TICK_ALPHA),
                "__TIME_AXIS_JS__": TIME_AXIS_JS,
                "__THEME_JS__": THEME_JS,
            },
        ),
        encoding="utf-8",
    )
    _register("03 plotly", path, "day · light Plotly · 4 vendors")


def plot_uplot(
    df,
    resets,
    cutoff,
    out_root: Path,
    spend_rows: list | None = None,
    credits: list | None = None,
    boosts: list | None = None,
) -> None:
    """Single page: all 4 vendors, plots-per-row control, auto-scale on resize."""
    d = out_root / "10_uplot"
    d.mkdir(parents=True, exist_ok=True)
    panels = [
        _vendor_panel_payload(
            df, resets, v, spend_rows=spend_rows, credits=credits, boosts=boosts
        )
        for v in VENDORS
    ]
    # drop stale per-vendor pages from the old layout
    for stale in d.glob("*.html"):
        if stale.name != "index.html":
            stale.unlink(missing_ok=True)
    path = d / "index.html"
    path.write_text(
        _fill(
            _static("uplot.html"),
            {
                "__CUTOFF__": cutoff.isoformat(),
                "__PANELS__": json.dumps(panels),
                "__BURN_W__": str(BURN_TICK_WIDTH),
                "__BURN_A__": str(BURN_TICK_ALPHA),
                "__TIME_AXIS_JS__": TIME_AXIS_JS,
                "__THEME_JS__": THEME_JS,
            },
        ),
        encoding="utf-8",
    )
    _register("10 uplot", path, "night · dark uPlot · 4 vendors")


def _spend_index_rows(strips: dict) -> str:
    """HTML rows: one per day, tokens (and $ when known) per vendor."""
    if not strips:
        return "<tr><td colspan='5'>no spend harvest</td></tr>"
    days = [d["date"] for d in next(iter(strips.values()), [])]
    if not days:
        return "<tr><td colspan='5'>no spend harvest</td></tr>"
    by_v = {v: {d["date"]: d for d in rows} for v, rows in strips.items()}
    out = []
    any_tok = False
    for date in days:
        label = next(
            (by_v[v][date]["day"] for v in VENDORS if date in by_v.get(v, {})),
            date,
        )
        cells = [f"<td>{label}</td>"]
        for v in VENDORS:
            d = by_v.get(v, {}).get(date) or {}
            tok = int(d.get("tokens") or 0)
            if tok:
                any_tok = True
            cost = d.get("cost_usd")
            if tok == 0 and cost is None:
                cells.append("<td>—</td>")
            elif cost is None:
                cells.append(f"<td>{tok:,}</td>")
            else:
                cells.append(f"<td>{tok:,} · ${cost:.2f}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    if not any_tok:
        return "<tr><td colspan='5'>no spend harvest</td></tr>"
    return "".join(out)


def _credit_index_rows(credits: list) -> str:
    if not credits:
        return "<tr><td colspan='5'>none seen yet</td></tr>"
    out = []
    for e in credits:
        exp = _local(e.expires_at).strftime("%d %b %H:%M") if e.expires_at else "?"
        ended = _local(e.ended_at).strftime("%d %b %H:%M") if e.ended_at else ""
        cls = "free" if e.money_usd > 0 else ("burn" if e.money_usd < 0 else "")
        out.append(
            f"<tr><td>{e.vendor}</td><td>{e.title} <code>{e.credit_id}</code></td>"
            f"<td>{e.status}{(' · ' + ended) if ended else ''}</td><td>{exp}</td>"
            f"<td class='{cls}'>{e.money_label}</td></tr>"
        )
    return "".join(out)


def write_index(
    resets, cutoff, out_root: Path, strips: dict | None = None, credits: list | None = None
) -> Path:
    credits = credits or []
    rows = []
    for name, path, note in RESULTS:
        rel = path.relative_to(out_root).as_posix()
        rows.append(
            f"<tr><td><b>{name}</b></td><td><a href='{rel}'>{rel}</a></td><td>{note}</td></tr>"
        )
    reset_lines = "".join(
        f"<li><b>{r.vendor}</b> {r.series} @ {_local(r.at).strftime('%d %b %H:%M')} "
        f"rem {r.remaining_before:.0f}% · {r.label}</li>"
        for r in resets
    )
    summary = money_summary(resets)
    money_rows = "".join(
        f"<tr><td>{v}</td><td>+${summary[v]['free']:.1f}</td>"
        f"<td>−${summary[v]['burn']:.1f}</td>"
        f"<td>{summary[v]['net']:+.1f}</td><td>{int(summary[v]['events'])}</td></tr>"
        for v in [*VENDORS, "TOTAL"]
    )
    spend_rows_html = _spend_index_rows(strips or {})
    html = _fill(
        _static("index.html"),
        {
            "__CUTOFF__": cutoff.isoformat(),
            "__MONEY_ROWS__": money_rows,
            "__RESET_LINES__": reset_lines or "<li>none</li>",
            "__DASHBOARD_ROWS__": "".join(rows),
            "__SPEND_ROWS__": spend_rows_html,
            "__CREDIT_ROWS__": _credit_index_rows(credits),
        },
    )
    path = out_root / "00_INDEX.html"
    path.write_text(html, encoding="utf-8")
    (out_root / "money.txt").write_text(
        format_money_report(resets, credits) + "\n", encoding="utf-8"
    )
    return path


def generate_plots(
    *,
    samples: Path | None = None,
    out_dir: Path | None = None,
    engines: tuple[str, ...] = ("plotly", "uplot"),
) -> dict:
    """Prepare data and write dashboards. Returns paths dict.

    HTML uses CDN for plotly/uplot. ``pandas`` is required (``ai-quotas[plot]``).
    """
    global RESULTS
    RESULTS = []
    out_root = Path(out_dir) if out_dir is not None else default_plots_dir()
    out_root.mkdir(parents=True, exist_ok=True)
    df, resets, cutoff = prepare(samples, out_dir=out_root)
    spend_rows = _load_spend_rows(samples)
    resets = annotate_reset_tokens(resets, df, spend_rows)
    credits = load_credit_events(samples, resets=resets)
    boosts = _load_boost_states(samples)
    strips = {v: daily_spend_for_vendor(spend_rows, v) for v in VENDORS}
    if "plotly" in engines:
        plot_plotly(df, resets, cutoff, out_root, spend_rows, credits, boosts)
    if "uplot" in engines:
        plot_uplot(df, resets, cutoff, out_root, spend_rows, credits, boosts)
    index = write_index(resets, cutoff, out_root, strips, credits)
    return {
        "out_dir": out_root,
        "index": index,
        "money": out_root / "money.txt",
        "cutoff": cutoff,
        "n_resets": len(resets),
        "n_reset_credits": len(credits),
        "n_rows": len(df),
        "dashboards": [p for _, p, _ in RESULTS],
    }
