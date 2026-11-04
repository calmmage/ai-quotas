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
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #fafaf8; color: #222; font: 13px/1.35 system-ui, sans-serif; }
header {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 8px 14px; background: #fff; border-bottom: 1px solid #e5e5e0;
  position: sticky; top: 0;
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
main { padding: 10px 14px 28px; display: grid; gap: 10px; }
.card { background: #fff; border: 1px solid #e5e5e0; border-radius: 10px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 4px 8px; text-align: right; white-space: nowrap; }
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align: left; }
th { font-weight: 600; color: #444; cursor: pointer; border-bottom: 1px solid #e5e5e0; }
tr:hover td { background: #f6f6f2; }
td.num { font-variant-numeric: tabular-nums; }
.empty { padding: 12px; color: #555; }
svg { display: block; width: 100%; height: 72px; }
.trend { padding: 8px 8px 2px; }
.trend p { margin: 0 8px 4px; color: #444; }
</style>
</head>
<body>
<header>
  <h1>Speed <span class="info" tabindex="0" title="End-to-end tokens/sec: response end minus the previous user message or tool result, including time to first token. Stats skip parallel tool batches, compaction, under 20 output tokens, and under 0.3s. Rows are kept.">i</span></h1>
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
  <section class="card" id="table-card"></section>
</main>
<script>
function esc(value) {
  return String(value).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}
const windows = document.getElementById("windows");
let current = "7d";
let sortKey = "n";
let sortDir = -1;
let payload = {compare: [], rows: [], daily: []};

function cell(value) {
  if (!value) return "—";
  const tip = "p90 " + value.p90 + " · n " + value.n;
  return '<span title="' + tip + '">' + value.p50 + "</span>";
}
function drawCompare() {
  const host = document.getElementById("compare-card");
  const rows = payload.compare || [];
  if (!rows.length) {
    host.innerHTML = '<p class="empty">No responses in this window yet.</p>';
    return;
  }
  const body = rows.map(row =>
    "<tr><td>" + esc(row.provider) + "</td><td>" + esc(row.model) + "</td><td class=num>" +
    cell(row.standard) + "</td><td class=num>" + cell(row.fast) + "</td><td class=num>" +
    cell(row.priority) + "</td><td class=num>" + cell(row.ultrafast) + "</td></tr>"
  ).join("");
  host.innerHTML = "<table><thead><tr><th>provider</th><th>model</th><th>standard</th><th>fast</th><th>priority</th><th>ultrafast</th></tr></thead><tbody>" + body + "</tbody></table>";
}
function drawTrend() {
  const host = document.getElementById("trend-card");
  const days = payload.daily || [];
  if (!days.length) { host.innerHTML = ""; return; }
  const max = Math.max(...days.map(d => d.p50 || 0), 1);
  const gap = 100 / days.length;
  const bars = days.map((d, i) => {
    const h = Math.max(2, (d.p50 || 0) / max * 64);
    const x = (i * gap).toFixed(3);
    const y = (70 - h).toFixed(2);
    const tip = d.date + " · p50 " + d.p50 + " · n " + d.n;
    return '<rect x="' + x + '" y="' + y + '" width="' + (gap * 0.72).toFixed(3) +
      '" height="' + h.toFixed(2) + '" fill="#c9c4b8"><title>' + tip + "</title></rect>";
  }).join("");
  host.innerHTML = '<p>Daily p50, Europe/Moscow</p><svg viewBox="0 0 100 72" preserveAspectRatio="none">' + bars + "</svg>";
}
function drawTable() {
  const host = document.getElementById("table-card");
  const rows = (payload.rows || []).slice().sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (av === bv) return 0;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * sortDir;
    return String(av).localeCompare(String(bv)) * sortDir;
  });
  if (!rows.length) { host.innerHTML = ""; return; }
  const heads = ["provider", "model", "tier", "p50", "p90", "n"].map(key =>
    '<th data-sort="' + key + '">' + key + "</th>"
  ).join("");
  const body = rows.map(row =>
    '<tr title="' + row.n + ' responses"><td>' + esc(row.provider) + "</td><td>" + esc(row.model) +
    "</td><td>" + esc(row.tier) + "</td><td class=num>" + row.p50 + "</td><td class=num>" +
    row.p90 + "</td><td class=num>" + row.n + "</td></tr>"
  ).join("");
  host.innerHTML = "<table><thead><tr>" + heads + "</tr></thead><tbody>" + body + "</tbody></table>";
  host.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
    const key = th.getAttribute("data-sort");
    sortDir = sortKey === key ? -sortDir : (key === "provider" || key === "model" || key === "tier" ? 1 : -1);
    sortKey = key;
    drawTable();
  }));
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
