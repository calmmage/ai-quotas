"""Speed page. One line per model, extra numbers on hover, no transcript text."""

from __future__ import annotations

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="color-scheme" content="light"/>
<title>Speed</title>
<link rel="icon" href="data:,">
<style>
:root { color-scheme: light; --line: #e5e5e0; --muted: #6b6b66; --up: #2f7a4a; --down: #b42318; }
* { box-sizing: border-box; }
body { margin: 0; background: #fafaf8; color: #222; font: 13px/1.35 system-ui, sans-serif; }
header {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 8px 14px; background: #fff; border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 1;
}
h1 { font-size: 16px; font-weight: 650; margin: 0; }
a { color: inherit; }
.nav, .windows button {
  height: 2rem; padding: 0 10px; border: 1px solid #ccc; background: #fff;
  border-radius: 6px; font: inherit; font-weight: 600; cursor: pointer;
  text-decoration: none; display: inline-flex; align-items: center;
}
.windows { display: flex; gap: 6px; }
.windows button.active { background: #1a1a1a; color: #fff; border-color: #1a1a1a; }
.info {
  display: inline-block; width: 14px; height: 14px; line-height: 14px; margin-left: 6px;
  border: 1px solid #4a4a4a; border-radius: 50%; font-size: 10px; font-weight: 600;
  text-align: center; vertical-align: 2px; cursor: help;
}
main { padding: 10px 14px 28px; display: grid; gap: 10px; max-width: 980px; }
.card { background: #fff; border: 1px solid var(--line); border-radius: 10px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 5px 8px; text-align: right; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { font-weight: 600; color: #444; cursor: pointer; border-bottom: 1px solid var(--line); user-select: none; }
th.sorted::after { content: attr(data-dir); color: var(--muted); margin-left: 3px; }
tr:hover td { background: #f6f6f2; }
td { font-variant-numeric: tabular-nums; }
td .n { color: var(--muted); font-size: 11px; margin-left: 4px; }
td.low { color: #b5b5ae; }
td.low .n { color: #c9c9c2; }
.x { font-weight: 600; }
.x.up { color: var(--up); } .x.down { color: var(--down); }
.empty { padding: 12px; color: #555; }
.trend { padding: 8px 10px; display: grid; gap: 6px; }
.trend .row { display: grid; grid-template-columns: minmax(0, 1fr) 120px 3.5em; gap: 8px; align-items: center; }
.trend .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trend .label small { color: var(--muted); }
.trend svg { width: 120px; height: 26px; display: block; overflow: visible; }
.trend .last { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
.trend .axis { color: var(--muted); font-size: 11px; display: flex; justify-content: space-between; }
.trend .axisrow { display: grid; grid-template-columns: minmax(0, 1fr) 120px 3.5em; gap: 8px; }
details > summary { padding: 8px 10px; cursor: pointer; color: #444; font-weight: 600; }
.foot { color: var(--muted); padding: 0 2px; }
@media (max-width: 520px) {
  header { padding: 8px 10px; gap: 6px; } main { padding: 8px 8px 24px; }
  .nav, .windows button { padding: 0 8px; }
  th, td { padding: 5px 6px; }
  td .n { display: none; }
}
</style>
</head>
<body>
<header>
  <h1>Speed <span class="info" tabindex="0" title="End-to-end tokens/sec: response end minus the previous user message or tool result, including time to first token. Numbers are medians (p50). Stats skip parallel tool batches, compaction, under 20 output tokens, and under 0.3s. Rows are kept.">i</span></h1>
  <div class="windows" id="windows">
    <button type="button" data-window="24h">24h</button>
    <button type="button" data-window="7d" class="active">7d</button>
    <button type="button" data-window="30d">30d</button>
  </div>
  <a class="nav" href="/live.html">Quota</a>
</header>
<main>
  <section class="card" id="compare-card"></section>
  <section class="card trend" id="trend-card"></section>
  <details class="card" id="table-fold"><summary>All rows: p90, response count, unknown tier</summary><div id="table-card"></div></details>
  <p class="foot">Median tok/s. Grey: under 30 responses. × = fastest mode vs standard, same model.</p>
</main>
<script>
const LOW = 30;
function esc(value) {
  return String(value).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}
const windows = document.getElementById("windows");
let current = "7d";
let payload = {compare: [], rows: [], trend: []};
const sorts = {compare: {key: "total", dir: -1}, table: {key: "n", dir: -1}};

function fastOf(row) { return row.fast || row.priority || null; }
function speedup(row) {
  const base = row.standard;
  if (!base || base.n < LOW || !base.p50) return null;
  const best = [fastOf(row), row.ultrafast].filter(v => v && v.n >= LOW)
    .sort((a, b) => b.p50 - a.p50)[0];
  return best ? Math.round(best.p50 / base.p50 * 10) / 10 : null;
}
function cell(value, note) {
  if (!value) return '<td data-v="-1">—</td>';
  const low = value.n < LOW;
  const tip = (note ? note + " · " : "") + "p50 " + value.p50 + " · p90 " + value.p90 + " · " + value.n + " responses" + (low ? " (too few to trust)" : "");
  return '<td data-v="' + value.p50 + '" class="' + (low ? "low" : "") + '" title="' + esc(tip) + '">' + value.p50 + '<span class="n">' + value.n + "</span></td>";
}
function sortRows(rows, state, pick) {
  return rows.slice().sort((a, b) => {
    const av = pick(a, state.key), bv = pick(b, state.key);
    if (av === bv) return 0;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * state.dir;
    return String(av).localeCompare(String(bv)) * state.dir;
  });
}
function heads(cols, state, which) {
  return cols.map(([key, label, tip]) => {
    const on = state.key === key;
    return '<th data-k="' + key + '" data-t="' + which + '"' + (tip ? ' title="' + esc(tip) + '"' : "") +
      (on ? ' class="sorted" data-dir="' + (state.dir > 0 ? "↑" : "↓") + '"' : "") + ">" + label + "</th>";
  }).join("");
}
function bindSort(host, which, draw) {
  host.querySelectorAll("th[data-t='" + which + "']").forEach(th => th.addEventListener("click", () => {
    const key = th.getAttribute("data-k"), state = sorts[which];
    state.dir = state.key === key ? -state.dir : (["model", "provider", "tier"].includes(key) ? 1 : -1);
    state.key = key;
    draw();
  }));
}
function compareValue(row, key) {
  if (key === "model") return row.model;
  if (key === "x") return speedup(row) ?? -1;
  if (key === "total") return ["standard", "fast", "priority", "ultrafast"].reduce((s, t) => s + ((row[t] || {}).n || 0), 0);
  const v = key === "fast" ? fastOf(row) : row[key];
  return v ? v.p50 : -1;
}
function drawCompare() {
  const host = document.getElementById("compare-card");
  const rows = sortRows((payload.compare || []).filter(r => r.standard || fastOf(r) || r.ultrafast), sorts.compare, compareValue);
  if (!rows.length) {
    host.innerHTML = '<p class="empty">No responses in this window yet. The scan runs every 10 minutes.</p>';
    return;
  }
  const cols = [["model", "model"], ["standard", "standard"],
    ["fast", "fast", "Claude and Grok fast mode; Codex calls it priority"], ["ultrafast", "ultrafast"],
    ["x", "×", "Fastest mode vs standard, same model (both 30+ responses)"]];
  const body = rows.map(row => {
    const x = speedup(row);
    const xs = x == null ? '<td data-v="-1"></td>' :
      '<td><span class="x ' + (x >= 1.15 ? "up" : x < 0.95 ? "down" : "") + '">×' + x.toFixed(1) + "</span></td>";
    return '<tr><td title="' + esc(row.provider) + '">' + esc(row.model) + "</td>" + cell(row.standard) +
      cell(fastOf(row), row.priority && !row.fast ? "Codex priority" : "") + cell(row.ultrafast) + xs + "</tr>";
  }).join("");
  host.innerHTML = "<table><thead><tr>" + heads(cols, sorts.compare, "compare") + "</tr></thead><tbody>" + body + "</tbody></table>";
  bindSort(host, "compare", drawCompare);
}
function drawTrend() {
  const host = document.getElementById("trend-card");
  const series = payload.trend || [];
  const dates = [...new Set(series.flatMap(s => s.points.map(p => p.date)))].sort();
  if (!series.length || dates.length < 2) { host.innerHTML = ""; host.hidden = true; return; }
  host.hidden = false;
  const max = Math.max(...series.flatMap(s => s.points.map(p => p.p50 || 0)), 1);
  const xAt = d => dates.indexOf(d) / (dates.length - 1) * 116 + 2;
  const yAt = v => 24 - (v / max) * 22;
  const fmt = d => new Date(d + "T12:00:00").toLocaleDateString(undefined, {day: "numeric", month: "short"});
  const lines = series.map(s => {
    const pts = s.points.filter(p => p.p50 != null);
    const path = pts.map((p, i) => (i ? "L" : "M") + xAt(p.date).toFixed(1) + " " + yAt(p.p50).toFixed(1)).join(" ");
    const dots = pts.map(p => '<circle cx="' + xAt(p.date).toFixed(1) + '" cy="' + yAt(p.p50).toFixed(1) +
      '" r="2" fill="#555"><title>' + fmt(p.date) + " · " + p.p50 + " tok/s · " + p.n + " responses</title></circle>").join("");
    const last = pts[pts.length - 1];
    return '<div class="row"><div class="label">' + esc(s.model) + " <small>" + esc(s.tier) + "</small></div>" +
      '<svg viewBox="0 0 120 26" aria-label="daily median"><path d="' + path + '" fill="none" stroke="#999" stroke-width="1.2"/>' + dots + "</svg>" +
      '<div class="last" title="' + (last ? fmt(last.date) : "") + '">' + (last ? last.p50 : "") + "</div></div>";
  }).join("");
  host.innerHTML = lines + '<div class="axisrow"><span class="axis">Daily median, busiest 4</span><span class="axis"><span>' +
    fmt(dates[0]) + "</span><span>" + fmt(dates[dates.length - 1]) + "</span></span><span></span></div>";
}
function drawTable() {
  const host = document.getElementById("table-card");
  const rows = sortRows(payload.rows || [], sorts.table, (row, key) => row[key]);
  if (!rows.length) { host.innerHTML = ""; return; }
  const cols = ["provider", "model", "tier", "p50", "p90", "n"].map(k => [k, k]);
  const body = rows.map(row =>
    "<tr><td>" + esc(row.provider) + "</td><td>" + esc(row.model) + "</td><td>" + esc(row.tier) +
    "</td><td>" + row.p50 + "</td><td>" + row.p90 + "</td><td>" + row.n + "</td></tr>"
  ).join("");
  host.innerHTML = "<table><thead><tr>" + heads(cols, sorts.table, "table") + "</tr></thead><tbody>" + body + "</tbody></table>";
  host.querySelectorAll("td:nth-child(2), td:nth-child(3), th:nth-child(2), th:nth-child(3)").forEach(el => el.style.textAlign = "left");
  bindSort(host, "table", drawTable);
}
async function load(windowName) {
  current = windowName;
  windows.querySelectorAll("button").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-window") === windowName);
  });
  const response = await fetch("/api/speed?window=" + windowName, {cache: "no-store"});
  payload = await response.json();
  drawCompare();
  drawTrend();
  drawTable();
}
windows.addEventListener("click", event => {
  const btn = event.target.closest("button");
  if (btn) load(btn.getAttribute("data-window"));
});
load(current);
</script>
</body>
</html>
"""


def render_speed_page() -> str:
    return PAGE
