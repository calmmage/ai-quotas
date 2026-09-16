const RANGE_SPAN_KEY = 'quota-span';
const RANGE_DEFAULT_SPAN = 7;
const RANGE_MIN_WINDOW = 3 * 3600; // 3h — tighter than this is unreadable
const RANGE_ZOOM_OUT = 1.18;
const RANGE_ZOOM_IN = 1 / 1.18;

function isConfiguredPanel(p) {
  return (p.series || []).some(s => (s.t || []).length && (s.y || []).some(v => v != null));
}

function boundsFromPanels(panels) {
  const samples = [];
  const extra = [];
  const now = Date.now() / 1000;
  (panels || []).forEach(p => {
    (p.series || []).forEach(s => {
      if (s.t && s.t.length) samples.push(s.t[0], s.t[s.t.length - 1]);
    });
    (p.inferred || []).forEach(g => (g.segs || []).forEach(seg => seg.forEach(pt => extra.push(pt[0]))));
    (p.budget || []).forEach(g => (g.segs || []).forEach(seg => seg.forEach(pt => extra.push(pt[0]))));
  });
  if (!samples.length && !extra.length) {
    return { firstT: now - RANGE_DEFAULT_SPAN * 86400, lastT: now + 86400, viewEnd: now + 86400 };
  }
  const firstT = Math.min.apply(null, samples.concat(extra));
  const lastSample = samples.length ? Math.max.apply(null, samples.concat([now])) : now;
  const lastT = Math.max.apply(null, extra.concat([lastSample])) + 86400;
  const viewEnd = lastSample + 86400;
  return { firstT, lastT, viewEnd };
}

function clampWindow(min, max, floor, ceil) {
  let nmin = min, nmax = max;
  if (!(nmax > nmin)) nmax = nmin + RANGE_MIN_WINDOW;
  if (nmax - nmin < RANGE_MIN_WINDOW) nmax = nmin + RANGE_MIN_WINDOW;
  if (nmin < floor) { nmax += floor - nmin; nmin = floor; }
  if (nmax > ceil) { nmin -= nmax - ceil; nmax = ceil; }
  nmin = Math.max(floor, nmin);
  nmax = Math.min(ceil, Math.max(nmin + RANGE_MIN_WINDOW, nmax));
  if (nmax > ceil) { nmax = ceil; nmin = Math.max(floor, nmax - RANGE_MIN_WINDOW); }
  return [nmin, nmax];
}

function zoomWindow(min, max, cursorT, factor, floor, ceil) {
  const t = Math.min(max, Math.max(min, cursorT));
  const nmin = t - (t - min) * factor;
  const nmax = t + (max - t) * factor;
  return clampWindow(nmin, nmax, floor, ceil);
}

function loadSpanDays() {
  const stored = localStorage.getItem(RANGE_SPAN_KEY);
  if (stored == null || stored === '') return RANGE_DEFAULT_SPAN;
  const n = Number(stored);
  if (!Number.isFinite(n) || n < 0) return RANGE_DEFAULT_SPAN;
  return n;
}

function createRangeController() {
  let spanDays = loadSpanDays();
  let customRange = null;
  let firstT = 0, lastT = 1, viewEnd = 1;
  const listeners = [];
  function emit() { listeners.forEach(fn => fn()); }
  const api = {
    get spanDays() { return spanDays; },
    get customRange() { return customRange; },
    get firstT() { return firstT; },
    get lastT() { return lastT; },
    get viewEnd() { return viewEnd; },
    setBounds(b) { firstT = b.firstT; lastT = b.lastT; viewEnd = b.viewEnd || b.lastT; },
    subscribe(fn) { listeners.push(fn); return () => {
      const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1);
    }; },
    viewMinMax() {
      if (!lastT || !isFinite(firstT)) return [0, 1];
      const end = viewEnd || lastT;
      const start = spanDays ? Math.max(firstT, end - spanDays * 86400) : firstT;
      return [start, Math.max(end, start + RANGE_MIN_WINDOW)];
    },
    axisMinMax() {
      if (customRange && customRange.length === 2 && customRange[1] > customRange[0]) return customRange;
      return api.viewMinMax();
    },
    visibleDays() {
      const [a, b] = api.axisMinMax();
      return (b - a) / 86400;
    },
    setSpan(days) {
      spanDays = Number(days);
      customRange = null;
      localStorage.setItem(RANGE_SPAN_KEY, String(spanDays));
      emit();
    },
    setCustom(min, max) {
      customRange = clampWindow(min, max, firstT, lastT);
      emit();
    },
    snap() {
      customRange = null;
      emit();
    },
    markButtons() {
      const custom = !!(customRange && customRange.length === 2);
      document.querySelectorAll('.controls button[data-span]').forEach(o => {
        o.classList.toggle('active', !custom && Number(o.dataset.span) === spanDays);
      });
    },
  };
  return api;
}

function emptyStateCopy(panels) {
  const names = (panels || []).map(p => p.vendor).filter(Boolean);
  const who = names.length ? names.join(', ').replace(/, ([^,]*)$/, ' or $1') : 'any vendor';
  return {
    title: 'No quota samples yet',
    body: 'Log into the vendor CLIs you use, then collect a sample. Remaining percent plots here as it burns.',
    cmd: 'make sample',
    vendors: names.length ? `Waiting on ${who}. Unconfigured vendors stay hidden.` : '',
  };
}

function renderEmptyState(el, panels) {
  if (!el) return;
  const copy = emptyStateCopy(panels);
  el.innerHTML = `<h2>${copy.title}</h2><p>${copy.body}</p><pre>${copy.cmd}</pre>` +
    (copy.vendors ? `<p class="empty-vendors">${copy.vendors}</p>` : '');
}

function fmtNavWhen(t) {
  const d = new Date(t * 1000);
  const dd = String(d.getDate()).padStart(2, '0');
  const mon = d.toLocaleString(undefined, { month: 'short' });
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${dd} ${mon} ${hh}:${mm}`;
}

function mountRangeNav(host, controller, opts) {
  opts = opts || {};
  host.classList.toggle('night', !!opts.night);
  host.innerHTML = `<div class="range-label"><span>window</span><b></b><span></span></div>
    <div class="range-body">
      <canvas width="600" height="44"></canvas>
      <div class="range-window" role="slider" aria-label="Visible time window">
        <button type="button" class="range-handle left" aria-label="Window start"></button>
        <button type="button" class="range-handle right" aria-label="Window end"></button>
      </div>
    </div>`;
  const canvas = host.querySelector('canvas');
  const win = host.querySelector('.range-window');
  const labelLeft = host.querySelector('.range-label span');
  const labelMid = host.querySelector('.range-label b');
  const labelRight = host.querySelector('.range-label span:last-child');
  const body = host.querySelector('.range-body');

  function xOf(t, width) {
    const span = controller.lastT - controller.firstT;
    if (!(span > 0)) return 0;
    return (t - controller.firstT) / span * width;
  }
  function tOf(px, width) {
    const span = controller.lastT - controller.firstT;
    if (!(width > 0)) return controller.firstT;
    return controller.firstT + (px / width) * span;
  }

  function paint() {
    const [a, b] = controller.axisMinMax();
    canvas.style.width = '100%';
    canvas.style.height = '44px';
    const rect = canvas.getBoundingClientRect();
    const cssW = Math.max(40, Math.floor(rect.width || host.clientWidth || 600));
    const cssH = 44;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    const night = host.classList.contains('night');
    const axis = (typeof timeAxis === 'function')
      ? timeAxis(controller.firstT, controller.lastT, cssW)
      : { majors: [], minors: [] };
    ctx.strokeStyle = night ? '#2a3140' : '#d8d8d0';
    ctx.lineWidth = 1;
    (axis.minors || []).forEach(t => {
      const x = xOf(t, cssW);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, cssH); ctx.stroke();
    });
    ctx.strokeStyle = night ? '#3a4354' : '#c4c4ba';
    (axis.majors || []).forEach(t => {
      const x = xOf(t, cssW);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, cssH); ctx.stroke();
    });
    const panels = (opts.getPanels && opts.getPanels()) || [];
    panels.forEach(p => {
      (p.series || []).filter(s => !s.dim && s.t && s.t.length > 1).forEach(s => {
        ctx.beginPath();
        ctx.strokeStyle = s.color || '#888';
        ctx.globalAlpha = 0.85;
        ctx.lineWidth = 1.2;
        let started = false;
        for (let i = 0; i < s.t.length; i++) {
          const y = s.y[i];
          if (y == null) { started = false; continue; }
          const x = xOf(s.t[i], cssW);
          const py = cssH - 4 - (Math.max(0, Math.min(100, y)) / 100) * (cssH - 8);
          if (!started) { ctx.moveTo(x, py); started = true; }
          else ctx.lineTo(x, py);
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
      });
      (p.inferred || []).forEach(g => (g.segs || []).forEach(seg => {
        if (seg.length < 2) return;
        ctx.beginPath();
        ctx.setLineDash([2, 3]);
        ctx.strokeStyle = g.color || '#888';
        ctx.globalAlpha = 0.7;
        ctx.lineWidth = 1;
        seg.forEach((pt, i) => {
          const x = xOf(pt[0], cssW);
          const py = cssH - 4 - (Math.max(0, Math.min(100, pt[1])) / 100) * (cssH - 8);
          if (i === 0) ctx.moveTo(x, py); else ctx.lineTo(x, py);
        });
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
      }));
    });
    const left = xOf(a, cssW);
    const right = xOf(b, cssW);
    win.style.left = Math.max(0, left) + 'px';
    win.style.width = Math.max(12, right - left) + 'px';
    labelLeft.textContent = fmtNavWhen(controller.firstT);
    labelMid.textContent = `${fmtNavWhen(a)} – ${fmtNavWhen(b)}`;
    labelRight.textContent = fmtNavWhen(controller.lastT);
  }

  let drag = null;
  function posToT(ev) {
    const r = body.getBoundingClientRect();
    return tOf(ev.clientX - r.left, r.width);
  }
  function startDrag(kind, ev) {
    ev.preventDefault();
    const [a, b] = controller.axisMinMax();
    drag = { kind, a, b, t0: posToT(ev) };
    if (ev.target && ev.target.setPointerCapture && ev.pointerId != null) {
      try { ev.target.setPointerCapture(ev.pointerId); } catch (_) { /* ignore */ }
    }
  }
  win.addEventListener('pointerdown', ev => {
    if (ev.target.closest('.range-handle')) return;
    startDrag('move', ev);
  });
  host.querySelector('.range-handle.left').addEventListener('pointerdown', ev => startDrag('left', ev));
  host.querySelector('.range-handle.right').addEventListener('pointerdown', ev => startDrag('right', ev));
  window.addEventListener('pointermove', ev => {
    if (!drag) return;
    const t = posToT(ev);
    const dt = t - drag.t0;
    if (drag.kind === 'move') controller.setCustom(drag.a + dt, drag.b + dt);
    else if (drag.kind === 'left') controller.setCustom(Math.min(drag.b - RANGE_MIN_WINDOW, drag.a + dt), drag.b);
    else controller.setCustom(drag.a, Math.max(drag.a + RANGE_MIN_WINDOW, drag.b + dt));
  });
  window.addEventListener('pointerup', () => { drag = null; });
  window.addEventListener('pointercancel', () => { drag = null; });
  body.addEventListener('pointerdown', ev => {
    if (ev.target.closest('.range-window')) return;
    const width = controller.axisMinMax()[1] - controller.axisMinMax()[0];
    const t = posToT(ev);
    controller.setCustom(t - width / 2, t + width / 2);
  });
  host.addEventListener('wheel', ev => {
    ev.preventDefault();
    const [a, b] = controller.axisMinMax();
    const factor = ev.deltaY > 0 ? RANGE_ZOOM_OUT : RANGE_ZOOM_IN;
    const t = posToT(ev);
    const next = zoomWindow(a, b, t, factor, controller.firstT, controller.lastT);
    controller.setCustom(next[0], next[1]);
  }, { passive: false });

  controller.subscribe(paint);
  window.addEventListener('resize', paint);
  paint();
  return { paint };
}
