"""Generate multi-vendor plot dashboards (plotly + uplot).

Runtime output only — not committed. Requires optional plot deps:
``pip install 'ai-quotas[plot]'`` or ``uv sync --extra plot``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from ai_quotas.plots.prep import (
    VENDORS,
    budget_line,
    color_map,
    cumulative_burn,
    format_money_report,
    local_tz,
    money_summary,
    plot_series_for_vendor,
    prepare,
    series_order_for_vendor,
    subtitle_resets,
    sustainable_rate,
    title_vendor,
    default_plots_dir,
)

RESULTS: list[tuple[str, Path, str]] = []


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


def _vendor_panel_payload(df, resets, vendor) -> dict:
    """Shared JSON payload for one vendor panel (plotly / uplot dashboards).

    All of the vendor's series are drawn, but the burn visuals (density ticks
    and the constant-pace line) are computed for the primary window only —
    stacking ticks from three series made them unreadable.
    """
    order = series_order_for_vendor(df, vendor)
    focus = plot_series_for_vendor(df, vendor)
    colors = color_map(order)
    sub = df[df["vendor"] == vendor]
    series_payload = []
    tick_payload = []
    rate_payload = []
    for s in order:
        g = sub[sub["series"] == s].dropna(subset=["remaining_percent"]).sort_values("ts_local")
        series_payload.append(
            {
                "label": s,
                "color": colors[s],
                "focus": s in focus,
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
    rlist = []
    for r in _vendor_resets(resets, vendor):
        if r.money_usd > 0:
            edge, face = "#1B7A3D", "rgba(232,248,238,0.95)"
            edge_dark, face_dark = "#3ddc84", "rgba(27,122,61,0.22)"
        elif r.money_usd < 0:
            edge, face = "#B00020", "rgba(253,236,234,0.95)"
            edge_dark, face_dark = "#ff5c72", "rgba(176,0,32,0.22)"
        else:
            edge, face = colors[r.series], "rgba(255,255,255,0.92)"
            edge_dark, face_dark = colors[r.series], "rgba(255,255,255,0.08)"
        rlist.append(
            {
                "t": int(_local(r.at).timestamp()),
                "line_color": colors[r.series],
                "edge": edge,
                "face": face,
                "edge_dark": edge_dark,
                "face_dark": face_dark,
                "label": f"{r.series} {r.label}",
            }
        )
    return {
        "vendor": vendor,
        "title": title_vendor(vendor, df),
        "subtitle": subtitle_resets(resets, vendor),
        "series": series_payload,
        "burn_ticks": tick_payload,
        "budget": rate_payload,
        "resets": rlist,
    }


def plot_plotly(df, resets, cutoff, out_root: Path) -> None:
    """Single page: all 4 vendors, plots-per-row control, auto-scale on resize."""
    d = out_root / "03_plotly"
    d.mkdir(parents=True, exist_ok=True)
    panels = [_vendor_panel_payload(df, resets, v) for v in VENDORS]
    # drop stale per-vendor pages from the old layout
    for stale in d.glob("*.html"):
        if stale.name != "index.html":
            stale.unlink(missing_ok=True)
    path = d / "index.html"
    path.write_text(
        f"""<!DOCTYPE html><html><head><meta charset="utf-8"/>
<title>Quota · Plotly</title>
<!-- this page is designed light; without this Chrome's auto-dark repaints the
     background dark and the SVG plot becomes invisible -->
<meta name="color-scheme" content="light">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
:root {{ --gap: 12px; --pad: 14px; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:#fafaf8; color:#222; font-family:system-ui,sans-serif; }}
header {{
  display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between;
  gap:12px; padding:16px 18px 10px; border-bottom:1px solid #e5e5e0; background:#fff;
  position:sticky; top:0; z-index:5;
}}
header h1 {{ font-size:16px; margin:0 0 2px; font-weight:650; }}
header .hint {{ margin:0; color:#666; font-size:12px; max-width:52ch; }}
.controls {{ display:flex; align-items:center; gap:8px; font-size:13px; }}
.controls label {{ color:#555; }}
.controls button {{
  min-width:2.2rem; height:2rem; border:1px solid #ccc; background:#fff; border-radius:6px;
  cursor:pointer; font:inherit; font-weight:600;
}}
.controls button.active {{ background:#1a1a1a; color:#fff; border-color:#1a1a1a; }}
#grid {{
  display:grid; gap:var(--gap); padding:var(--pad);
  grid-template-columns: repeat(var(--cols, 2), minmax(0, 1fr));
}}
.panel {{
  background:#fff; border:1px solid #e5e5e0; border-radius:10px; overflow:hidden;
  display:flex; flex-direction:column; min-width:0;
}}
.panel header.phead {{
  position:static; border:0; background:transparent; padding:10px 12px 0;
  display:block;
}}
.panel h2 {{ font-size:13px; margin:0 0 2px; font-weight:650; }}
.panel .sub {{ margin:0 0 4px; color:#777; font-size:11px; }}
.chart {{ width:100%; min-height:200px; }}
/* Plotly defaults to ~700px; force fill of the grid cell */
.chart .js-plotly-plot, .chart .plot-container, .chart .svg-container {{
  width:100% !important;
}}
</style></head><body>
<header>
  <div>
    <h1>Quota remaining · Plotly</h1>
    <p class="hint">All 4 vendors on one page · burn ticks + constant-pace line on the primary window · cutoff <code>{cutoff.isoformat()}</code></p>
  </div>
  <div class="controls">
    <label>range</label>
    <button type="button" data-span="7">1w</button>
    <button type="button" data-span="30">1m</button>
    <button type="button" data-span="90">1q</button>
    <button type="button" data-span="0" class="active span-active">all</button>
    <label>plots / row</label>
    <button type="button" data-cols="1">1</button>
    <button type="button" data-cols="2" class="active">2</button>
    <button type="button" data-cols="3">3</button>
    <button type="button" data-cols="4">4</button>
  </div>
</header>
<div id="grid"></div>
<script>
const panels = {json.dumps(panels)};
const BURN_W = {BURN_TICK_WIDTH};
const BURN_A = {BURN_TICK_ALPHA};
const grid = document.getElementById('grid');
const plots = []; // {{div, panel}}
let spanDays = Number(localStorage.getItem('quota-plotly-span') || 0);

// latest timestamp across every series, so all panels share one window
const LAST_T = Math.max(0, ...panels.flatMap(p => p.series.map(s => s.t[s.t.length-1] || 0)));
function xRange() {{
  if (!spanDays || !LAST_T) return null;
  return [new Date((LAST_T - spanDays*86400) * 1000), new Date(LAST_T * 1000)];
}}

function currentCols() {{
  return Number(getComputedStyle(document.documentElement).getPropertyValue('--cols')) || 2;
}}

function panelHeight(cols) {{
  const w = grid.clientWidth || window.innerWidth;
  const cell = Math.max(280, (w - 28 - (cols - 1) * 12) / cols);
  // taller when full-width (1 col)
  if (cols === 1) return Math.min(560, Math.max(380, cell * 0.32));
  if (cols === 2) return Math.min(420, Math.max(280, cell * 0.48));
  if (cols === 3) return Math.min(340, Math.max(240, cell * 0.55));
  return Math.min(280, Math.max(200, cell * 0.62));
}}

function buildFigure(p, width, height) {{
  const traces = p.series.map(s => ({{
    x: s.t.map(t => new Date(t * 1000)),
    y: s.y,
    mode: 'lines',
    name: s.label,
    line: {{ color: s.color, width: 2.4 }},
    connectgaps: false,
    hovertemplate: `<b>${{s.label}}</b><br>%{{x}}<br>remaining %{{y:.1f}}%<extra></extra>`,
  }}));
  // constant-pace depletion line: where quota would go if burn held at the
  // rate the subscription is priced for. Above it = under-spending.
  p.budget.forEach(b => {{
    b.segs.forEach((seg, i) => {{
      traces.push({{
        x: seg.map(pt => new Date(pt[0] * 1000)),
        y: seg.map(pt => pt[1]),
        mode: 'lines',
        name: b.label,
        showlegend: i === 0,
        legendgroup: 'budget',
        line: {{ color: '#8a8a8a', width: 1.4, dash: 'longdash' }},
        hovertemplate: `<b>${{b.label}}</b><br>%{{y:.1f}}%<extra></extra>`,
      }});
    }});
  }});

  const shapes = [];
  const annotations = [];
  p.burn_ticks.forEach(bt => {{
    bt.pts.forEach(([t, yTop]) => {{
      shapes.push({{
        type: 'line',
        x0: new Date(t * 1000), x1: new Date(t * 1000),
        y0: 0, y1: yTop, yref: 'y',
        line: {{ color: bt.color, width: BURN_W }},
        opacity: BURN_A, layer: 'below',
      }});
    }});
  }});
  p.resets.forEach((r, i) => {{
    const x = new Date(r.t * 1000);
    shapes.push({{
      type: 'line', x0: x, x1: x, y0: 0, y1: 1, yref: 'paper',
      line: {{ color: r.line_color, width: 1.8, dash: 'dash' }},
    }});
    annotations.push({{
      x, y: 0.02 + (i % 4) * 0.07, yref: 'paper',
      text: r.label, showarrow: false,
      font: {{ size: 10, color: r.edge }},
      bgcolor: r.face, bordercolor: r.edge, borderwidth: 1,
      xanchor: 'left', yanchor: 'bottom',
    }});
  }});
  return {{
    data: traces,
    layout: {{
      title: {{ text: '', pad: {{ t: 0 }} }},
      autosize: false,
      width,
      height,
      margin: {{ t: 28, l: 48, r: 14, b: 36 }},
      yaxis: {{
        title: {{ text: '% remaining', font: {{ size: 11 }} }},
        range: [-2, 102],
        showgrid: true, gridcolor: '#D6D6CE', gridwidth: 0.8, dtick: 20,
        minor: {{ showgrid: true, gridcolor: '#E8E8E2', gridwidth: 0.5, dtick: 5 }},
        zeroline: false,
      }},
      xaxis: Object.assign({{
        showgrid: true, gridcolor: '#A8A89C', gridwidth: 1.2,
        dtick: 24 * 60 * 60 * 1000, tickformat: '%a %d %b',
        minor: {{ showgrid: true, gridcolor: '#E8E8E2', gridwidth: 0.5, dtick: 6 * 60 * 60 * 1000 }},
      }}, xRange() ? {{ range: xRange() }} : {{}}),
      plot_bgcolor: '#FAFAF8', paper_bgcolor: '#fff',
      legend: {{ orientation: 'h', y: 1.12, font: {{ size: 10 }} }},
      shapes, annotations,
      hovermode: 'x unified',
    }},
    config: {{ responsive: false, displayModeBar: false }},
  }};
}}

function paintAll() {{
  const cols = currentCols();
  const h = panelHeight(cols);
  plots.forEach(({{ div, panel }}) => {{
    // measure AFTER CSS grid has applied --cols
    const w = Math.max(200, Math.floor(div.clientWidth || div.parentElement.clientWidth || grid.clientWidth));
    const fig = buildFigure(panel, w, h);
    if (div.data) {{
      Plotly.react(div, fig.data, fig.layout, fig.config);
    }} else {{
      Plotly.newPlot(div, fig.data, fig.layout, fig.config);
    }}
  }});
}}

// Wait for grid reflow so clientWidth is full width at cols=1. rAF alone is
// not enough: it never fires while the tab is in the background, which would
// leave every panel blank until the tab is focused.
function afterReflow(fn) {{
  let done = false;
  const run = () => {{ if (!done) {{ done = true; fn(); }} }};
  requestAnimationFrame(() => requestAnimationFrame(run));
  setTimeout(run, 120);
}}

function setCols(cols) {{
  document.documentElement.style.setProperty('--cols', String(cols));
  document.querySelectorAll('.controls button').forEach(b => {{
    b.classList.toggle('active', Number(b.dataset.cols) === cols);
  }});
  localStorage.setItem('quota-plotly-cols', String(cols));
  afterReflow(paintAll);
}}

function boot() {{
  panels.forEach(p => {{
    const section = document.createElement('section');
    section.className = 'panel';
    section.innerHTML = `<header class="phead"><h2>${{p.title}}</h2><p class="sub">${{p.subtitle}}</p></header>`;
    const div = document.createElement('div');
    div.className = 'chart';
    section.appendChild(div);
    grid.appendChild(section);
    plots.push({{ div, panel: p }});
  }});
  const saved = Number(localStorage.getItem('quota-plotly-cols') || 2);
  const cols = [1,2,3,4].includes(saved) ? saved : 2;
  document.documentElement.style.setProperty('--cols', String(cols));
  document.querySelectorAll('.controls button[data-cols]').forEach(b => {{
    b.classList.toggle('active', Number(b.dataset.cols) === cols);
    b.addEventListener('click', () => setCols(Number(b.dataset.cols)));
  }});
  document.querySelectorAll('.controls button[data-span]').forEach(b => {{
    b.classList.toggle('active', Number(b.dataset.span) === spanDays);
    b.addEventListener('click', () => {{
      spanDays = Number(b.dataset.span);
      localStorage.setItem('quota-plotly-span', String(spanDays));
      document.querySelectorAll('.controls button[data-span]').forEach(o =>
        o.classList.toggle('active', Number(o.dataset.span) === spanDays));
      paintAll();
    }});
  }});
  afterReflow(paintAll);
  let t = null;
  window.addEventListener('resize', () => {{
    clearTimeout(t);
    t = setTimeout(paintAll, 100);
  }});
}}
boot();
</script></body></html>""",
        encoding="utf-8",
    )
    _register("03 plotly", path, "single page · 4 vendors · cols control")


def plot_uplot(df, resets, cutoff, out_root: Path) -> None:
    """Single page: all 4 vendors, plots-per-row control, auto-scale on resize."""
    d = out_root / "10_uplot"
    d.mkdir(parents=True, exist_ok=True)
    panels = [_vendor_panel_payload(df, resets, v) for v in VENDORS]
    # drop stale per-vendor pages from the old layout
    for stale in d.glob("*.html"):
        if stale.name != "index.html":
            stale.unlink(missing_ok=True)
    path = d / "index.html"
    path.write_text(
        f"""<!DOCTYPE html><html><head><meta charset="utf-8"/>
<title>Quota · uPlot</title>
<meta name="color-scheme" content="dark">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css">
<script src="https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.min.js"></script>
<style>
:root {{ --gap: 12px; --pad: 14px; --cols: 2; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:#111318; color:#e8e8e8; font-family:system-ui,sans-serif; }}
header.top {{
  display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between;
  gap:12px; padding:16px 18px 10px; border-bottom:1px solid #1e222b;
  background:#0d0f14; position:sticky; top:0; z-index:5;
}}
header.top h1 {{ font-size:16px; margin:0 0 2px; font-weight:650; color:#eee; }}
header.top .hint {{ margin:0; color:#8a8f98; font-size:12px; max-width:56ch; }}
.controls {{ display:flex; align-items:center; gap:8px; font-size:13px; }}
.controls label {{ color:#8a8f98; }}
.controls button {{
  min-width:2.2rem; height:2rem; border:1px solid #333843; background:#1a1e27; color:#e8e8e8;
  border-radius:6px; cursor:pointer; font:inherit; font-weight:600;
}}
.controls button.active {{ background:#e8e8e8; color:#111; border-color:#e8e8e8; }}
#grid {{
  display:grid; gap:var(--gap); padding:var(--pad);
  grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
}}
.panel {{
  background:#151821; border:1px solid #22262e; border-radius:10px; overflow:hidden;
  display:flex; flex-direction:column; min-width:0;
}}
.panel .phead {{ padding:10px 12px 4px; }}
.panel h2 {{ font-size:13px; margin:0 0 2px; font-weight:650; color:#eee; }}
.panel .sub {{ margin:0 0 4px; color:#8a8f98; font-size:11px; }}
.panel .legend {{
  display:flex; flex-wrap:wrap; gap:8px 12px; padding:0 12px 4px; font-size:11px; color:#b0b4bc;
}}
.panel .legend span i {{
  display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:middle;
}}
.chart {{ width:100%; padding:0 6px 8px; min-height:180px; }}
.uplot {{ margin:0 auto; }}
.u-legend {{ display:none !important; }}
</style></head><body>
<header class="top">
  <div>
    <h1>Quota remaining · uPlot</h1>
    <p class="hint">All 4 vendors on one page · burn ticks + constant-pace line on the primary window · cutoff <code>{cutoff.isoformat()}</code></p>
  </div>
  <div class="controls">
    <label>range</label>
    <button type="button" data-span="7">1w</button>
    <button type="button" data-span="30">1m</button>
    <button type="button" data-span="90">1q</button>
    <button type="button" data-span="0" class="active">all</button>
    <label>plots / row</label>
    <button type="button" data-cols="1">1</button>
    <button type="button" data-cols="2" class="active">2</button>
    <button type="button" data-cols="3">3</button>
    <button type="button" data-cols="4">4</button>
  </div>
</header>
<div id="grid"></div>
<script>
const panels = {json.dumps(panels)};
const BURN_W = {BURN_TICK_WIDTH};
const BURN_A = {BURN_TICK_ALPHA};
const grid = document.getElementById('grid');
const charts = [];
let spanDays = Number(localStorage.getItem('quota-uplot-span') || 0);

// latest timestamp across every series, so all panels share one window
const LAST_T = Math.max(0, ...panels.flatMap(p => p.series.map(s => s.t[s.t.length-1] || 0)));
function applySpan() {{
  charts.forEach(c => {{
    if (!c.u) return;
    if (spanDays && LAST_T) c.u.setScale('x', {{ min: LAST_T - spanDays*86400, max: LAST_T }});
    else {{
      const xs = c.u.data[0];
      if (xs && xs.length) c.u.setScale('x', {{ min: xs[0], max: xs[xs.length-1] }});
    }}
  }});
}}

const WD=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'], MO=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function fmtDay(d){{ return `${{WD[d.getDay()]}} ${{String(d.getDate()).padStart(2,'0')}} ${{MO[d.getMonth()]}}`; }}
function dayTicks(minSec, maxSec){{
  if (!isFinite(minSec) || !isFinite(maxSec) || maxSec <= minSec) return [];
  const out=[]; const d=new Date(minSec*1000); d.setHours(0,0,0,0);
  let guard=0;
  while (d.getTime()/1000 <= maxSec && guard++ < 400) {{ out.push(Math.floor(d.getTime()/1000)); d.setDate(d.getDate()+1); }}
  return out;
}}
function sixHourTicks(minSec, maxSec){{
  if (!isFinite(minSec) || !isFinite(maxSec) || maxSec <= minSec) return [];
  const out=[]; const d=new Date(minSec*1000); d.setMinutes(0,0,0); d.setHours(Math.floor(d.getHours()/6)*6);
  let guard=0;
  while (d.getTime()/1000 <= maxSec && guard++ < 1600) {{ out.push(Math.floor(d.getTime()/1000)); d.setHours(d.getHours()+6); }}
  return out;
}}

function makeDraws(panel) {{
  const burnTicks = panel.burn_ticks;
  const budget = panel.budget;
  const resets = panel.resets.map(r => ({{
    t: r.t, line_color: r.line_color,
    edge: r.edge_dark || r.edge, face: r.face_dark || r.face,
    label: r.label,
  }}));
  function drawGrid(u){{
    const ctx=u.ctx, {{left,top,width,height}}=u.bbox;
    const vline=(t,color,lw)=>{{ const x=u.valToPos(t,'x',true); if (x<left||x>left+width) return;
      ctx.strokeStyle=color; ctx.lineWidth=lw; ctx.beginPath(); ctx.moveTo(x,top); ctx.lineTo(x,top+height); ctx.stroke(); }};
    const hline=(v,color,lw)=>{{ const y=u.valToPos(v,'y',true);
      ctx.strokeStyle=color; ctx.lineWidth=lw; ctx.beginPath(); ctx.moveTo(left,y); ctx.lineTo(left+width,y); ctx.stroke(); }};
    ctx.save();
    for (let v=5; v<100; v+=5) {{ if (v%20===0) continue; hline(v, '#242832', 0.5); }}
    for (let v=0; v<=100; v+=20) {{ hline(v, '#343a46', 0.8); }}
    const [xmin,xmax]=[u.scales.x.min, u.scales.x.max];
    sixHourTicks(xmin, xmax).forEach(t => vline(t, '#242832', 0.5));
    dayTicks(xmin, xmax).forEach(t => vline(t, '#565f70', 1.4));
    ctx.restore();
  }}
  function drawBurnTicks(u){{
    const ctx=u.ctx, {{left,top,width,height}}=u.bbox;
    const DPR=u.pxRatio||window.devicePixelRatio||1;
    ctx.save(); ctx.beginPath(); ctx.rect(left,top,width,height); ctx.clip();
    const y0=u.valToPos(0,'y',true);
    ctx.lineWidth=BURN_W*DPR; ctx.globalAlpha=BURN_A;
    burnTicks.forEach(s => {{
      ctx.strokeStyle=s.color;
      s.pts.forEach(([t,yTop]) => {{
        const x=u.valToPos(t,'x',true);
        if (x<left||x>left+width) return;
        const y1=u.valToPos(yTop,'y',true);
        ctx.beginPath(); ctx.moveTo(x,y0); ctx.lineTo(x,y1); ctx.stroke();
      }});
    }});
    ctx.globalAlpha=1; ctx.restore();
  }}
  // constant-pace depletion line, drawn in the same % space as the data
  function drawBudget(u){{
    const ctx=u.ctx, {{left,top,width,height}}=u.bbox;
    const DPR=u.pxRatio||window.devicePixelRatio||1;
    ctx.save();
    ctx.beginPath(); ctx.rect(left,top,width,height); ctx.clip();
    ctx.strokeStyle='#7c838f'; ctx.lineWidth=1.4*DPR;
    ctx.setLineDash([7*DPR,5*DPR]);
    budget.forEach(b => {{
      b.segs.forEach(seg => {{
        if (seg.length < 2) return;
        ctx.beginPath();
        seg.forEach(([t,y], i) => {{
          const px=u.valToPos(t,'x',true), py=u.valToPos(y,'y',true);
          if (i===0) ctx.moveTo(px,py); else ctx.lineTo(px,py);
        }});
        ctx.stroke();
      }});
    }});
    ctx.setLineDash([]);
    ctx.restore();
  }}
  function drawResets(u){{
    const ctx=u.ctx, {{left,top,width,height}}=u.bbox, yb=top+height;
    // canvas hooks work in device pixels — scale every hardcoded px or the
    // labels render at half size on retina
    const DPR=u.pxRatio||window.devicePixelRatio||1;
    resets.forEach((r,idx) => {{
      const x=u.valToPos(r.t,'x',true);
      if (x < left || x > left+width) return;
      ctx.save();
      ctx.beginPath(); ctx.rect(left,top,width,height); ctx.clip();
      ctx.strokeStyle=r.line_color; ctx.setLineDash([5*DPR,4*DPR]); ctx.lineWidth=1.6*DPR; ctx.globalAlpha=0.95;
      ctx.beginPath(); ctx.moveTo(x,top); ctx.lineTo(x,yb); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha=1;
      ctx.font=`600 ${{Math.round(11*DPR)}}px system-ui`;
      const padX=6*DPR, padY=4*DPR, rowH=22*DPR, boxH=14*DPR+padY*2;
      const boxW=ctx.measureText(r.label).width+padX*2;
      const boxTop=yb-6*DPR-(idx%3)*rowH-boxH;
      // flip the box to the left of the marker rather than let it run off
      let bx=x+4*DPR;
      if (bx+boxW > left+width) bx=x-4*DPR-boxW;
      ctx.fillStyle=r.face; ctx.strokeStyle=r.edge; ctx.lineWidth=1*DPR;
      ctx.fillRect(bx, boxTop, boxW, boxH);
      ctx.strokeRect(bx, boxTop, boxW, boxH);
      ctx.fillStyle=r.edge; ctx.textAlign='left'; ctx.textBaseline='middle';
      ctx.fillText(r.label, bx+padX, boxTop+boxH/2);
      ctx.restore();
    }});
  }}
  return {{ drawGrid, drawBurnTicks, drawBudget, drawResets }};
}}

function alignData(seriesData) {{
  const tset = new Set();
  seriesData.forEach(s => s.t.forEach(t => tset.add(t)));
  const xs = Array.from(tset).sort((a,b)=>a-b);
  const align = s => {{ const m=new Map(s.t.map((t,i)=>[t,s.y[i]])); return xs.map(t => m.has(t)?m.get(t):null); }};
  return [xs, ...seriesData.map(align)];
}}

function panelSize(cols) {{
  // Prefer measured panel chart width (full cell) over math that can lag CSS
  const sample = grid.querySelector('.chart');
  let cellW;
  if (sample && sample.clientWidth > 40) {{
    cellW = sample.clientWidth;
  }} else {{
    const w = grid.clientWidth || window.innerWidth;
    const gap = 12, pad = 28;
    cellW = Math.max(240, (w - pad - (cols - 1) * gap) / cols);
  }}
  let h;
  if (cols === 1) h = Math.min(520, Math.max(360, cellW * 0.32));
  else if (cols === 2) h = Math.min(380, Math.max(260, cellW * 0.48));
  else if (cols === 3) h = Math.min(300, Math.max(220, cellW * 0.55));
  else h = Math.min(250, Math.max(180, cellW * 0.62));
  return {{ width: Math.floor(cellW), height: Math.floor(h) }};
}}

function makePlot(wrap, panel, width, height) {{
  const seriesData = panel.series;
  const data = alignData(seriesData);
  const draws = makeDraws(panel);
  const opts = {{
    width, height,
    // NB: cursor.points.show:true throws inside uPlot 1.6.31 — leave the
    // default (a factory fn) alone; points still render.
    cursor: {{ show: true }},
    legend: {{ show: false }},
    series: [{{}}, ...seriesData.map(s => ({{ label:s.label, stroke:s.color, width:2.3, spanGaps:false }}))],
    axes: [
      {{ stroke:'#8a8f98', grid:{{show:false}},
        splits:(u,axisIdx,min,max)=>dayTicks(min,max),
        values:(u,vals)=>vals.map(v=>fmtDay(new Date(v*1000))) }},
      {{ stroke:'#8a8f98', grid:{{show:false}}, label:'% remaining',
        splits:(u,axisIdx,min,max)=>[0,20,40,60,80,100] }},
    ],
    scales: {{ y: {{ range: [-2, 102] }} }},
    hooks: {{
      drawClear: [draws.drawGrid, draws.drawBurnTicks, draws.drawBudget],
      draw: [draws.drawResets],
    }},
  }};
  return new uPlot(opts, data, wrap);
}}

// Wait for grid reflow so each .chart is full width at cols=1. rAF alone is
// not enough: it never fires while the tab is in the background, which would
// leave every panel unsized until the tab is focused.
function afterReflow(fn) {{
  let done = false;
  const run = () => {{ if (!done) {{ done = true; fn(); }} }};
  requestAnimationFrame(() => requestAnimationFrame(run));
  setTimeout(run, 120);
}}

function setCols(cols) {{
  document.documentElement.style.setProperty('--cols', String(cols));
  document.querySelectorAll('.controls button').forEach(b => {{
    b.classList.toggle('active', Number(b.dataset.cols) === cols);
  }});
  localStorage.setItem('quota-uplot-cols', String(cols));
  afterReflow(() => {{
    charts.forEach(c => {{
      if (!c.u) return;
      const colsNow = Number(getComputedStyle(document.documentElement).getPropertyValue('--cols')) || cols;
      // measure THIS chart's container (handles full-width 1-col correctly)
      const w = Math.max(200, c.wrap.clientWidth);
      const h = panelSize(colsNow).height;
      c.u.setSize({{ width: w, height: h }});
    }});
  }});
}}

function boot() {{
  panels.forEach(p => {{
    const section = document.createElement('section');
    section.className = 'panel';
    const head = document.createElement('div');
    head.className = 'phead';
    head.innerHTML = `<h2>${{p.title}}</h2><p class="sub">${{p.subtitle}}</p>`;
    const legend = document.createElement('div');
    legend.className = 'legend';
    p.series.forEach(s => {{
      const el = document.createElement('span');
      el.innerHTML = `<i style="background:${{s.color}}"></i>${{s.label}}`;
      legend.appendChild(el);
    }});
    p.budget.forEach(s => {{
      const el = document.createElement('span');
      el.innerHTML = `<i style="background:repeating-linear-gradient(90deg,#7c838f 0 4px,transparent 4px 7px)"></i>${{s.label}}`;
      legend.appendChild(el);
    }});
    const wrap = document.createElement('div');
    wrap.className = 'chart';
    section.appendChild(head);
    section.appendChild(legend);
    section.appendChild(wrap);
    grid.appendChild(section);
    charts.push({{ u: null, wrap, panel: p }});
  }});
  const saved = Number(localStorage.getItem('quota-uplot-cols') || 2);
  const cols = [1,2,3,4].includes(saved) ? saved : 2;
  document.documentElement.style.setProperty('--cols', cols);
  afterReflow(() => {{
    const {{ height }} = panelSize(cols);
    // measure each wrap itself — a shared estimate overshoots before the CSS
    // grid has settled, leaving the canvas wider than its panel (clipped)
    charts.forEach(c => {{
      const w = Math.max(200, c.wrap.clientWidth || panelSize(cols).width);
      c.u = makePlot(c.wrap, c.panel, w, height);
    }});
    document.querySelectorAll('.controls button[data-cols]').forEach(b => {{
      b.classList.toggle('active', Number(b.dataset.cols) === cols);
      b.addEventListener('click', () => setCols(Number(b.dataset.cols)));
    }});
    document.querySelectorAll('.controls button[data-span]').forEach(b => {{
      b.classList.toggle('active', Number(b.dataset.span) === spanDays);
      b.addEventListener('click', () => {{
        spanDays = Number(b.dataset.span);
        localStorage.setItem('quota-uplot-span', String(spanDays));
        document.querySelectorAll('.controls button[data-span]').forEach(o =>
          o.classList.toggle('active', Number(o.dataset.span) === spanDays));
        applySpan();
      }});
    }});
    applySpan();
  }});
  let t = null;
  window.addEventListener('resize', () => {{
    clearTimeout(t);
    t = setTimeout(() => {{
      const c = Number(getComputedStyle(document.documentElement).getPropertyValue('--cols')) || 2;
      setCols(c);
    }}, 120);
  }});
}}
boot();
</script></body></html>""",
        encoding="utf-8",
    )
    _register("10 uplot", path, "single page · 4 vendors · cols control")



def write_index(resets, cutoff, out_root: Path) -> Path:
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
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><title>ai-quotas plots</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:960px;margin:36px auto;padding:0 16px;background:#fafafa;color:#222}}
h1{{font-size:20px}} table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left;font-size:13px}}
.hint{{color:#555;font-size:13px}} code{{background:#eee;padding:1px 4px;border-radius:3px}}
.free{{color:#1B7A3D;font-weight:600}} .burn{{color:#B00020;font-weight:600}}
</style></head><body>
<h1>ai-quotas · plots</h1>
<p class="hint">% remaining · family colors · money burn-first / free-within-window<br>
cutoff: <code>{cutoff.isoformat()}</code><br>
Each dashboard = all 4 vendors · plots/row control (1–4) · auto-scale</p>
<h3>Money</h3>
<table><tr><th>vendor</th><th>free</th><th>burn</th><th>net</th><th>n</th></tr>
{money_rows}
</table>
<h3>Resets</h3><ul>{reset_lines or '<li>none</li>'}</ul>
<h3>Dashboards</h3>
<table><tr><th>variant</th><th>file</th><th>notes</th></tr>
{''.join(rows)}
</table>
</body></html>"""
    path = out_root / "00_INDEX.html"
    path.write_text(html, encoding="utf-8")
    (out_root / "money.txt").write_text(format_money_report(resets) + "\n", encoding="utf-8")
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
    if "plotly" in engines:
        plot_plotly(df, resets, cutoff, out_root)
    if "uplot" in engines:
        plot_uplot(df, resets, cutoff, out_root)
    index = write_index(resets, cutoff, out_root)
    return {
        "out_dir": out_root,
        "index": index,
        "money": out_root / "money.txt",
        "cutoff": cutoff,
        "n_resets": len(resets),
        "n_rows": len(df),
        "dashboards": [p for _, p, _ in RESULTS],
    }
