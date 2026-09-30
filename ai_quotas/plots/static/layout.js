// Shared preferences keep day/night layouts consistent. Old column settings
// are used once when no shared preference has been saved yet.
function quotaLayoutRead(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}
function quotaLayoutSave(key, value) {
  try { localStorage.setItem(key, String(value)); } catch (_) { /* session only */ }
}
function quotaLayoutNumber(key, fallback, min, max) {
  const raw = quotaLayoutRead(key);
  const n = raw == null ? NaN : Number(raw);
  return Number.isFinite(n) && n >= min && n <= max ? n : fallback;
}
function createQuotaLayout(engine, resize) {
  const legacy = quotaLayoutNumber(`quota-${engine}-cols`, 2, 1, 4);
  const state = {
    cols: Math.round(quotaLayoutNumber('quota-layout-cols', legacy, 1, 4)),
    width: quotaLayoutNumber('quota-layout-width', 100, 50, 100),
    height: quotaLayoutNumber('quota-layout-height', 0, 0, 600),
    tokens: ['hidden', 'background', 'strip'].includes(quotaLayoutRead('quota-layout-tokens')) ? quotaLayoutRead('quota-layout-tokens') : 'hidden',
  };
  if (state.height && state.height < 180) state.height = 0;
  const host = document.querySelector('.layout-controls');
  function apply() {
    document.documentElement.style.setProperty('--dashboard-width', state.width + '%');
    if (!host) return;
    host.querySelector('[data-layout-width]').value = state.width;
    host.querySelector('[data-layout-width-output]').textContent = state.width + '%';
    const auto = host.querySelector('[data-layout-auto]');
    const height = host.querySelector('[data-layout-height]');
    auto.checked = !state.height;
    height.disabled = !state.height;
    height.value = state.height || 320;
    host.querySelector('[data-layout-tokens]').value = state.tokens;
    host.querySelector('[data-layout-height-output]').textContent = state.height ? state.height + 'px' : 'Auto';
    host.querySelectorAll('[data-cols]').forEach(b => {
      const on = Number(b.dataset.cols) === state.cols;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', String(on));
    });
  }
  function change(key, value) {
    state[key] = value;
    quotaLayoutSave('quota-layout-' + key, value);
    apply();
    resize();
  }
  if (host) {
    host.querySelector('[data-layout-width]').addEventListener('input', e => change('width', Number(e.target.value)));
    host.querySelector('[data-layout-height]').addEventListener('input', e => change('height', Number(e.target.value)));
    host.querySelector('[data-layout-auto]').addEventListener('change', e => change('height', e.target.checked ? 0 : 320));
    host.querySelector('[data-layout-tokens]').addEventListener('change', e => change('tokens', e.target.value));
    host.querySelector('[data-layout-reset]').addEventListener('click', () => {
      Object.assign(state, { cols: 2, width: 100, height: 0, tokens: 'hidden' });
      Object.entries(state).forEach(([key, value]) => quotaLayoutSave('quota-layout-' + key, value));
      apply();
      resize();
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && host.open) { host.open = false; host.querySelector('summary').focus(); }
    });
    document.addEventListener('click', e => { if (!host.contains(e.target)) host.open = false; });
  }
  apply();
  return {
    preferredCols: () => state.cols,
    setCols: cols => change('cols', cols),
    height: automatic => state.height || automatic,
    tokens: () => state.tokens,
    // Preserve the preference while reducing columns on small screens.
    columns: (count, width) => Math.max(1, Math.min(state.cols, count || state.cols, Math.floor((width - 16) / 292))),
  };
}

function quotaOrderedPanels(panels) {
  const all = panels || [];
  return [...all.filter(isConfiguredPanel), ...all.filter(p => !isConfiguredPanel(p))];
}

function quotaOrderCharts(charts, panels, grid) {
  const rank = new Map(panels.map((p, i) => [p.vendor, i]));
  charts.sort((a, b) => rank.get(a.vendor) - rank.get(b.vendor));
  let chartIndex = 0, setupIndex = 0;
  const setupGrid = document.getElementById('setup-grid');
  charts.forEach((c, i) => {
    c.section.style.setProperty('--i', String(i));
    const target = c.kind === 'setup' ? setupGrid : grid;
    const position = c.kind === 'setup' ? setupIndex++ : chartIndex++;
    // Move only misplaced elements: keep chart instances, focus and details.
    if (target.children[position] !== c.section) target.insertBefore(c.section, target.children[position] || null);
  });
  const drawer = document.getElementById('provider-setup');
  drawer.hidden = setupIndex === 0;
  drawer.querySelector('summary').textContent = `Add providers (${setupIndex})`;
  if (!chartIndex) drawer.open = true;
}

function quotaUpdatePeriod(range) {
  const el = document.getElementById('view-period');
  const [a, b] = range;
  el.textContent = Number.isFinite(a) && Number.isFinite(b) ? `${quotaDate(a)} – ${quotaDate(b)}` : '';
}

function quotaTokenDays(panel, start, end) {
  return (panel.spend || []).filter(d => d.date).map(d => {
    const t = new Date(d.date + 'T00:00:00').getTime() / 1000;
    const next = new Date(d.date + 'T00:00:00');
    next.setDate(next.getDate() + 1);
    return { ...d, t, end: next.getTime() / 1000 };
  }).filter(d => d.t < end && d.end > start);
}

function quotaSpendStrip(panel, range) {
  if (layout.tokens() !== 'strip') return '';
  const rows = quotaTokenDays(panel, ...range);
  if (!rows.some(d => d.tokens)) return '<div class="token-empty">No recorded token activity in these dates</div>';
  const max = Math.max(...rows.map(d => d.tokens || 0), 1);
  return `<div class="spend-strip" aria-label="Daily recorded token totals, scaled to the busiest visible day">` + rows.map(d =>
    `<span title="${quotaEscape(d.date)}: ${(d.tokens || 0).toLocaleString()} recorded tokens"><i style="height:${3 + 22 * (d.tokens || 0) / max}px"></i></span>`
  ).join('') + '<em title="Recorded tokens per calendar day. Bar heights are relative to the busiest visible day; these are not quota percentages.">Tokens / day</em></div>';
}

function quotaUpdateSpendStrip(c, before, range) {
  const html = quotaSpendStrip(c.panel, range);
  if (c.strip && c.strip.outerHTML === html) return;
  if (c.strip) { c.strip.remove(); c.strip = null; }
  if (html) {
    const holder = document.createElement('div');
    holder.innerHTML = html;
    c.strip = holder.firstElementChild;
    c.section.insertBefore(c.strip, before);
  }
}
