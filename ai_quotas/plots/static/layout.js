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
// Fit mode stops shrinking plots here; below it the page scrolls instead.
const QUOTA_FIT_MIN = 150;
const QUOTA_ORDER_KEY = 'quota-layout-order';
function quotaSavedOrder() {
  try {
    const v = JSON.parse(quotaLayoutRead(QUOTA_ORDER_KEY) || '[]');
    return Array.isArray(v) ? v.map(String) : [];
  } catch (_) { return []; }
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
    host.querySelector('[data-layout-height-output]').textContent = state.height ? state.height + 'px' : 'Fit';
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
      quotaLayoutSave(QUOTA_ORDER_KEY, '[]');
      const last = quotaOrderCharts.last;
      if (last && window._quotaPanels) quotaOrderCharts(last.charts, quotaOrderedPanels(window._quotaPanels), last.grid);
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
    // Charts flex to fill their cell; this sets the cell. Fit (no manual
    // height, 2+ columns) splits the viewport below the sticky header between
    // the rows so every plot is on screen. Otherwise rows size to content and
    // each chart is at least the manual or engine-automatic height.
    size: (grid, count, cols, automatic) => {
      let rowH = 0, chartMin = state.height || automatic;
      if (!state.height && cols > 1 && count > 0 && grid) {
        const rows = Math.ceil(count / cols);
        const cs = getComputedStyle(grid);
        const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
        const gap = parseFloat(cs.rowGap) || 0;
        rowH = Math.floor((window.innerHeight - grid.offsetTop - pad - gap * (rows - 1)) / rows);
        chartMin = QUOTA_FIT_MIN;
      }
      document.documentElement.style.setProperty('--row-h', rowH + 'px');
      document.documentElement.style.setProperty('--chart-min', Math.round(chartMin) + 'px');
    },
    // Preserve the preference while reducing columns on small screens.
    columns: (count, width) => Math.max(1, Math.min(state.cols, count || state.cols, Math.floor((width - 16) / 292))),
  };
}

// Configured panels follow the order the viewer dragged them into; vendors not
// in that order yet keep the default order after them. Setup cards come last.
function quotaOrderedPanels(panels) {
  const all = panels || [];
  const saved = quotaSavedOrder();
  const rank = p => { const i = saved.indexOf(p.vendor); return i < 0 ? saved.length : i; };
  const configured = all.filter(isConfiguredPanel)
    .map((p, i) => ({ p, i }))
    .sort((a, b) => rank(a.p) - rank(b.p) || a.i - b.i)
    .map(x => x.p);
  return [...configured, ...all.filter(p => !isConfiguredPanel(p))];
}

function quotaOrderCharts(charts, panels, grid) {
  quotaOrderCharts.last = { charts, grid };
  const rank = new Map(panels.map((p, i) => [p.vendor, i]));
  charts.sort((a, b) => rank.get(a.vendor) - rank.get(b.vendor));
  let chartIndex = 0, setupIndex = 0;
  const setupGrid = document.getElementById('setup-grid');
  // A refresh during a drag must not yank panels back; the drop saves the order.
  const dragging = document.documentElement.classList.contains('quota-dragging');
  charts.forEach((c, i) => {
    c.section.style.setProperty('--i', String(i));
    c.section.dataset.vendor = c.vendor;
    const target = c.kind === 'setup' ? setupGrid : grid;
    const position = c.kind === 'setup' ? setupIndex++ : chartIndex++;
    // Move only misplaced elements: keep chart instances, focus and details.
    if (!dragging && target.children[position] !== c.section) target.insertBefore(c.section, target.children[position] || null);
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

// Drag a panel by its header to reorder; the order is saved on drop. Mouse and
// pen start after a few pixels of movement. Touch needs a long press, so a
// swipe over a header still scrolls the page. Esc puts the panel back.
function quotaEnableReorder(grid) {
  if (!grid || grid.dataset.reorder) return;
  grid.dataset.reorder = '1';
  const SKIP = 'button, a, input, select, textarea, summary, .value-details';
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const moving = new Set();
  let drag = null;
  const panels = () => [...grid.children].filter(el => el.classList.contains('panel'));

  // Animate the panels that change slots (FLIP) so a reorder reads as motion.
  function flip(skip, move) {
    const others = panels().filter(el => el !== skip);
    const before = others.map(el => el.getBoundingClientRect());
    move();
    if (reduce) return;
    others.forEach((el, i) => {
      const n = el.getBoundingClientRect();
      const dx = before[i].left - n.left, dy = before[i].top - n.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      moving.add(el);
      const a = el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'translate(0, 0)' }],
        { duration: 200, easing: 'cubic-bezier(.2,.7,.2,1)' });
      a.onfinish = a.oncancel = () => moving.delete(el);
    });
  }
  // The dragged panel keeps its slot in the grid and is drawn under the pointer.
  function follow() {
    const s = drag.section, g = grid.getBoundingClientRect();
    s.style.transform = `translate(${drag.x - drag.grab.x - g.left - s.offsetLeft}px, ${drag.y - drag.grab.y - g.top - s.offsetTop}px)`;
  }
  function reorderAt(x, y) {
    const hit = document.elementFromPoint(x, y);
    const over = hit && hit.closest('.panel');
    // A panel still sliding away would bounce the dragged one straight back.
    if (!over || over === drag.section || over.parentElement !== grid || moving.has(over)) return;
    const list = panels();
    const after = list.indexOf(drag.section) < list.indexOf(over);
    flip(drag.section, () => grid.insertBefore(drag.section, after ? over.nextSibling : over));
    follow();
  }
  function autoscroll() {
    if (!drag || !drag.active) return;
    const head = document.querySelector('header');
    const top = (head ? head.getBoundingClientRect().bottom : 0) + 40;
    const dy = drag.y < top ? -12 : drag.y > window.innerHeight - 40 ? 12 : 0;
    if (dy) { window.scrollBy(0, dy); follow(); reorderAt(drag.x, drag.y); }
    requestAnimationFrame(autoscroll);
  }
  function begin() {
    if (!drag || drag.active) return;
    const r = drag.section.getBoundingClientRect();
    drag.active = true;
    // Grab point = where the press landed; the pointer has moved since then.
    drag.grab = { x: drag.x0 - r.left, y: drag.y0 - r.top };
    drag.origin = drag.section.nextSibling;
    drag.section.classList.add('dragging');
    document.documentElement.classList.add('quota-dragging');
    try { window.getSelection().removeAllRanges(); } catch (_) { /* nothing selected */ }
    follow();
    requestAnimationFrame(autoscroll);
  }
  function end(commit) {
    const d = drag;
    drag = null;
    if (!d) return;
    clearTimeout(d.timer);
    if (!d.active) return;
    const s = d.section;
    if (!commit) flip(s, () => grid.insertBefore(s, d.origin));
    const from = s.style.transform;
    s.style.transform = '';
    s.classList.remove('dragging');
    document.documentElement.classList.remove('quota-dragging');
    if (from && !reduce) {
      s.animate([{ transform: from }, { transform: 'translate(0, 0)' }], { duration: 180, easing: 'cubic-bezier(.2,.7,.2,1)' });
    }
    // The click that ends a drag must not land on whatever is under it.
    const swallow = e => { e.stopPropagation(); e.preventDefault(); };
    window.addEventListener('click', swallow, { capture: true, once: true });
    setTimeout(() => window.removeEventListener('click', swallow, { capture: true }), 0);
    if (!commit) return;
    const order = panels().map(el => el.dataset.vendor).filter(Boolean);
    // Vendors hidden right now keep their saved place after the visible ones.
    quotaSavedOrder().forEach(v => { if (!order.includes(v)) order.push(v); });
    quotaLayoutSave(QUOTA_ORDER_KEY, JSON.stringify(order));
  }

  grid.addEventListener('pointerdown', e => {
    if (drag || e.button !== 0) return;
    const section = e.target.closest('.phead') && e.target.closest('.panel');
    if (!section || section.parentElement !== grid || e.target.closest(SKIP)) return;
    drag = { section, pointerId: e.pointerId, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY,
      active: false, touch: e.pointerType === 'touch' };
    if (drag.touch) drag.timer = setTimeout(begin, 380);
    else e.preventDefault(); // no text selection while a header is dragged
  });
  window.addEventListener('pointermove', e => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    drag.x = e.clientX; drag.y = e.clientY;
    if (!drag.active) {
      const far = Math.hypot(drag.x - drag.x0, drag.y - drag.y0) > 6;
      if (drag.touch) { if (far) end(false); return; } // a swipe: let it scroll
      if (!far) return;
      begin();
    }
    follow();
    reorderAt(drag.x, drag.y);
  });
  // Touch: once the long press picked the panel up, stop the page scrolling.
  grid.addEventListener('touchmove', e => { if (drag && drag.active) e.preventDefault(); }, { passive: false });
  grid.addEventListener('contextmenu', e => { if (drag) e.preventDefault(); });
  window.addEventListener('pointerup', e => { if (drag && e.pointerId === drag.pointerId) end(true); });
  window.addEventListener('pointercancel', e => { if (drag && e.pointerId === drag.pointerId) end(false); });
  window.addEventListener('keydown', e => { if (e.key === 'Escape' && drag && drag.active) end(false); });
}
