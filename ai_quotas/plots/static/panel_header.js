function quotaEscape(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function quotaDate(t) {
  const d = new Date(t * 1000);
  return `${String(d.getDate()).padStart(2,'0')} ${d.toLocaleString('en', {month:'short'})} ${d.getFullYear()}`;
}
// Same clocks as ai_quotas/alerts.py spare reminders. Yellow = WARN, red = STOP.
// A primary window at or under 5% remaining is used up (at the cap, or near zero).
function quotaLimits() {
  return { staleS: 2 * 3600, spare2dH: 48, spare1dH: 24, half: 50, quarter: 25, nearZero: 5 };
}
function quotaShellQuote(s) {
  return `'${String(s).replace(/'/g, `'\\''`)}'`;
}
function quotaShellWord(s) {
  return /^[A-Za-z0-9_./:-]+$/.test(s) ? s : quotaShellQuote(s);
}
function quotaStamp(sec) {
  const d = new Date(sec * 1000);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function quotaLastNumber(values) {
  if (!values) return null;
  for (let i = values.length - 1; i >= 0; i--) {
    if (typeof values[i] === 'number' && Number.isFinite(values[i])) return values[i];
  }
  return null;
}
function quotaFocusSeries(p) {
  return (p.series || []).filter(s => s && s.focus && !s.dim);
}
function quotaFixCommand(p) {
  const provider = (p.subscription && p.subscription.provider) || '';
  const hints = {
    claude: 'The Claude CLI access token often expires. Run any claude command to refresh it, then uv run ai-quotas sample.',
    codex: 'Codex samples come from the codex CLI or codexbar. If codexbar timed out, find why, then uv run ai-quotas sample.',
    grok: 'If Grok sampling fails with HTTP 400, run grok login and make grok-fix, then uv run ai-quotas sample.',
  };
  const when = Number.isFinite(p.sampled_at) ? quotaStamp(p.sampled_at) : 'unknown';
  const hint = hints[provider] || 'Run uv run ai-quotas sample and confirm a fresh row.';
  const prompt = `${p.vendor} quota data is stale. Last sample: ${when}. Database: ~/.local/share/ai-quotas/ai-quotas.sqlite3. ${hint} Fix sampling and confirm a fresh ${provider} row. Do not print tokens or secrets.`;
  const bin = quotaShellWord(p.troubleshoot_bin || 'grok');
  const cd = p.checkout ? `cd ${quotaShellQuote(p.checkout)} && ` : '';
  return `${cd}${bin} ${quotaShellQuote(prompt)}`;
}
function quotaSpareSeverity(p, nowSec) {
  const lim = quotaLimits();
  let warn = false;
  let stop = false;
  for (const s of quotaFocusSeries(p)) {
    const rem = quotaLastNumber(s.y);
    const at = s.resets_at;
    if (typeof rem !== 'number' || !Number.isFinite(at)) continue;
    const hours = (at - nowSec) / 3600;
    if (!(rem > 0 && rem <= 100) || !(hours > 0)) continue;
    if (hours <= lim.spare1dH && rem > lim.half) stop = true;
    else if (hours <= lim.spare1dH && rem > lim.quarter) warn = true;
    else if (hours <= lim.spare2dH && rem > lim.half) warn = true;
  }
  if (stop) return 'stop';
  if (warn) return 'warn';
  return null;
}
function quotaExhausted(p, nowSec) {
  const series = quotaFocusSeries(p);
  if (!series.length) return null;
  const readings = series.map(s => ({ label: s.label, rem: quotaLastNumber(s.y), at: s.resets_at }));
  if (readings.some(r => r.rem == null) || !readings.every(r => r.rem <= quotaLimits().nearZero)) return null;
  const future = readings.filter(r => Number.isFinite(r.at) && r.at > nowSec).sort((a, b) => a.at - b.at);
  const past = readings.filter(r => Number.isFinite(r.at)).sort((a, b) => b.at - a.at);
  const pick = future[0] || past[0] || null;
  return { at: pick ? pick.at : null, label: pick ? pick.label : '' };
}
function quotaCountdownText(at, nowMs) {
  if (!Number.isFinite(at)) return 'Reset time not reported';
  let sec = Math.floor(at - nowMs / 1000);
  if (sec <= 0) return 'Reset overdue';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = n => String(n).padStart(2, '0');
  if (d > 0) return `Resets in ${d}d ${pad(h)}:${pad(m)}:${pad(s)}`;
  return `Resets in ${pad(h)}:${pad(m)}:${pad(s)}`;
}
// Stale Claude / Codex / Grok get a shell command that launches the troubleshooting agent.
function quotaPanelState(p, nowMs) {
  const nowSec = nowMs / 1000;
  const provider = (p.subscription && p.subscription.provider) || '';
  const stale = Number.isFinite(p.sampled_at) && nowSec - p.sampled_at > quotaLimits().staleS;
  const fixable = stale && ['claude', 'codex', 'grok'].includes(provider);
  const exhausted = quotaExhausted(p, nowSec);
  return {
    stale,
    fix: fixable ? quotaFixCommand(p) : '',
    spare: exhausted ? null : quotaSpareSeverity(p, nowSec),
    exhausted,
  };
}
function quotaSampleCommand(p) {
  const provider = (p.subscription && p.subscription.provider) || '';
  const cd = p.checkout ? `cd ${quotaShellQuote(p.checkout)} && ` : '';
  return `${cd}uv run ai-quotas sample --provider ${quotaShellWord(provider)}`;
}
function quotaSampleSummary(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const ok = list.filter(r => r && r.status === 'ok' && typeof r.used_percent === 'number');
  if (!ok.length) {
    const reason = list.map(r => r && r.reason).find(Boolean);
    return reason ? `Check failed · ${reason}` : 'Check failed';
  }
  const week = ok.find(r => r.window === 'week') || ok[0];
  const left = Math.max(0, Math.round(100 - week.used_percent));
  const label = week.window || 'quota';
  return `Checked · ${label} ${left}% remaining`;
}
function quotaCopyCommand(text, button, restoreLabel) {
  const label = restoreLabel || 'Copy fix command';
  const done = ok => {
    if (!button) return;
    button.textContent = ok ? 'Copied' : 'Copy failed';
    setTimeout(() => { button.textContent = label; }, 1600);
  };
  const fallback = () => {
    try {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.append(area);
      area.select();
      const ok = document.execCommand('copy');
      area.remove();
      return ok;
    } catch (_) {
      return false;
    }
  };
  const show = () => {
    const dialog = document.createElement('dialog');
    dialog.className = 'subscription-dialog';
    dialog.innerHTML = `<form>
      <h2>Troubleshooting command</h2>
      <p>Clipboard is blocked on this page. Select the command and paste it into a terminal.</p>
      <pre class="setup-cmd"></pre>
      <div class="subscription-actions"><button type="submit">Done</button></div>
    </form>`;
    dialog.querySelector('pre').textContent = text;
    document.body.append(dialog);
    dialog.querySelector('form').addEventListener('submit', event => {
      event.preventDefault();
      dialog.close();
    });
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
  };
  if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => done(true), () => {
      if (fallback()) done(true);
      else { done(false); show(); }
    });
    return;
  }
  if (fallback()) done(true);
  else { done(false); show(); }
}
async function quotaCheckNow(section, p, button) {
  if (!button || button.disabled) return;
  const status = section.querySelector('.sample-status');
  button.disabled = true;
  button.textContent = 'Checking…';
  const restore = () => {
    button.disabled = false;
    setTimeout(() => { if (!button.disabled) button.textContent = 'Check now'; }, 2200);
  };
  try {
    const endpoint = new URL('../api/sample', location.href);
    let api;
    try {
      const meta = await fetch(endpoint, {cache: 'no-store', signal: AbortSignal.timeout(4000)});
      if (!meta.ok) throw Error('offline');
      api = await meta.json();
      if (!api || !api.token) throw Error('offline');
    } catch (_) {
      quotaCopyCommand(quotaSampleCommand(p), button, 'Check now');
      return;
    }
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Quota-Token': api.token},
      body: JSON.stringify({provider: p.subscription.provider}),
      signal: AbortSignal.timeout(45000),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(result.error || 'Usage check failed');
    if (status) {
      status.hidden = false;
      status.textContent = quotaSampleSummary(result.rows);
    }
    button.textContent = 'Checked';
    const metaUrl = new URL('../meta.json', location.href);
    const before = await fetch(metaUrl, {cache: 'no-store'}).then(r => r.json()).catch(() => ({}));
    for (let attempt = 0; attempt < 40; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const next = await fetch(metaUrl, {cache: 'no-store'}).then(r => r.json()).catch(() => ({}));
      if (next.generated_at && next.generated_at !== before.generated_at) {
        if (window.quotaRefresh) await window.quotaRefresh();
        break;
      }
    }
  } catch (error) {
    if (status) {
      status.hidden = false;
      status.textContent = error && error.message ? error.message : 'Usage check failed';
    }
    button.textContent = 'Check failed';
  } finally {
    restore();
  }
}
function quotaEnsureCountdown() {
  if (quotaEnsureCountdown.started || typeof document === 'undefined') return;
  quotaEnsureCountdown.started = true;
  setInterval(() => {
    document.querySelectorAll('.reset-countdown[data-at]').forEach(el => {
      const text = quotaCountdownText(Number(el.dataset.at), Date.now());
      if (el.textContent !== text) el.textContent = text;
    });
  }, 1000);
}
function quotaApplyPanelChrome(section, p, nowMs) {
  if (!section || !p) return;
  const now = nowMs == null ? Date.now() : nowMs;
  const state = quotaPanelState(p, now);
  section.classList.toggle('spare-warn', state.spare === 'warn');
  section.classList.toggle('spare-stop', state.spare === 'stop');
  section.classList.toggle('exhausted', !!state.exhausted);
  if (state.spare) section.dataset.spare = state.spare;
  else delete section.dataset.spare;
  if (state.exhausted) section.dataset.exhausted = '1';
  else delete section.dataset.exhausted;
  const spareTitle = {
    warn: 'Unused quota expires soon',
    stop: 'Most of this quota is still unused and resets within a day',
  };
  section.title = state.spare ? spareTitle[state.spare] : '';
  const status = section.querySelector('.sample-status');
  if (status) {
    status.hidden = !state.stale;
    status.textContent = state.stale
      ? `Data stale · last sample ${quotaDate(p.sampled_at)} ${new Date(p.sampled_at * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}`
      : '';
  }
  const actions = section.querySelector('.stale-actions');
  if (actions) actions.hidden = !state.fix;
  const check = section.querySelector('.check-now');
  if (check && !check.disabled) {
    check.hidden = !state.fix;
    check.setAttribute('aria-label', `Check ${p.vendor} usage now`);
    check.onclick = state.fix ? () => { quotaCheckNow(section, p, check); } : null;
  }
  const fix = section.querySelector('.stale-fix');
  if (fix) {
    fix.hidden = !state.fix;
    fix.title = state.fix || '';
    fix.setAttribute('aria-label', state.fix ? `Copy shell command to troubleshoot stale ${p.vendor} data` : 'Copy fix command');
    fix.onclick = state.fix ? () => quotaCopyCommand(state.fix, fix) : null;
  }
  const timer = section.querySelector('.reset-countdown');
  if (timer) {
    if (!state.exhausted) {
      timer.hidden = true;
      delete timer.dataset.at;
      timer.textContent = '';
    } else {
      timer.hidden = false;
      const at = state.exhausted.at;
      if (Number.isFinite(at)) timer.dataset.at = String(at);
      else delete timer.dataset.at;
      timer.textContent = quotaCountdownText(at, now);
    }
  }
  quotaEnsureCountdown();
}
// Swap text only when it changed; after the first paint, a change gets a short
// opacity dip (.tick, panel_header.css) so a refreshed number is noticeable
// without a page reload.
function quotaSetHtml(el, html) {
  if (el.innerHTML === html) return false;
  el.innerHTML = html;
  if (el.dataset.ready) { el.classList.remove('tick'); void el.offsetWidth; el.classList.add('tick'); }
  el.dataset.ready = '1';
  return true;
}
function quotaHeader(p) {
  const missingPrice = p.subscription.monthly_usd == null;
  const settingsButton = `<button type="button" class="subscription-edit" data-provider="${quotaEscape(p.subscription.provider)}">${missingPrice ? 'Set subscription cost' : 'Subscription settings'}</button>`;
  return `<div class="value-summary"><h2>${quotaEscape(p.vendor)}</h2>
    <p class="reset-countdown" role="timer" hidden></p>
    <p class="value-line"></p><p class="usage-line"></p>
    <p class="sample-status" role="status" hidden></p>
    <div class="stale-actions" hidden>
      <button type="button" class="check-now" hidden>Check now</button>
      <button type="button" class="stale-fix" hidden>Copy fix command</button>
    </div>
    ${missingPrice ? settingsButton : ''}
    <details class="value-details"><summary>Details & settings</summary>
      ${missingPrice ? '' : settingsButton}
      <p>${quotaEscape(p.subscription.basis)}</p><p class="value-breakdown"></p>
      <p class="pace-detail"></p>
      <p>Dollar amounts estimate subscription value. Lost means unused quota at renewal or expired reset credits, offset by inferred bonus refills. Quota you can still spend is excluded.</p>
      <p>Plan utilisation averages the consumed percentage of observed quota periods ending in this view, expired unused credits (0% used), and the active period when its latest reading is in view. It includes each period's full usage, even if that period started before the view. Used effectively values that consumed quota at its allocation price; it does not measure output quality or cash savings. Allocation prices weight utilisation when known; unpriced or free periods use equal weights. Unspent spare resets are excluded. Sampling gaps may hide additional periods.</p>
    </details></div><aside class="reset-reserve" aria-label="Available quota resets"></aside>`;
}
// Dollars still above the line that reaches 0 at the next scheduled reset,
// at the last sample inside the visible range. Null when the series has no price.
function quotaAbovePace(p, start, end) {
  const series = (p.series || []).find(s => s.focus && s.window_usd);
  if (!series) return null;
  let y = null, t = null;
  for (let i = 0; i < series.t.length; i++) {
    if (series.t[i] > end) break;
    if (series.t[i] >= start && series.y[i] != null) { t = series.t[i]; y = series.y[i]; }
  }
  if (y == null || t == null) return null;
  let budget = null;
  for (const band of (p.budget || [])) {
    for (const seg of (band.segs || [])) {
      if (!seg || seg.length < 2) continue;
      const t0 = seg[0][0], y0 = seg[0][1];
      const t1 = seg[seg.length - 1][0], y1 = seg[seg.length - 1][1];
      if (t < t0 || t > t1) continue;
      const span = t1 - t0;
      const f = span > 0 ? (t - t0) / span : 1;
      budget = y0 + (y1 - y0) * f;
    }
  }
  if (budget == null) return null;
  return Math.max(0, y - budget) / 100 * series.window_usd;
}
function quotaUsageSummary(p, start, end) {
  const periods = (p.usage_periods || []).filter(x => x.t >= start && x.t <= end);
  if (!periods.length) return { percent: null, usd: null, periods: 0 };
  const priced = periods.every(x => typeof x.allocation_usd === 'number' && Number.isFinite(x.allocation_usd));
  const budget = priced ? periods.reduce((sum, x) => sum + x.allocation_usd, 0) : 0;
  const usd = priced ? periods.reduce((sum, x) => sum + x.used_pct / 100 * x.allocation_usd, 0) : null;
  // Weight by allocation value when prices are known; free/unpriced periods
  // use equal weights and still report a percentage without inventing dollars.
  const percent = budget > 0 ? usd / budget * 100 : periods.reduce((sum, x) => sum + x.used_pct, 0) / periods.length;
  return { percent, usd, periods: periods.length };
}
function updateQuotaHeader(section, p, range) {
  section.querySelector('.subscription-edit').onclick = () => openSubscriptionSettings(p);
  quotaApplyPanelChrome(section, p);
  // Plotly paints after reflow, which can happen after window.load. Open a
  // deep link only once this provider's button and panel are actually ready.
  const wanted = new URLSearchParams(location.hash.slice(1)).get('subscription');
  if (wanted === p.subscription.provider) {
    history.replaceState(null, '', location.pathname + location.search);
    queueMicrotask(() => openSubscriptionSettings(p));
  }
  const [a,b] = range;
  const events = (p.underutilised.events || []).filter(e => e.t >= a && e.t <= b);
  const known = events.filter(e => e.usd != null);
  const total = Math.max(0, known.reduce((sum,e) => sum + e.usd, 0));
  const missing = events.length - known.length;
  const gap = quotaAbovePace(p, a, b);
  const value = section.querySelector('.value-line');
  const priced = p.subscription.monthly_usd != null;
  quotaSetHtml(value, !missing && priced ? `$${total.toFixed(0)} lost <small>· estimated</small>` : 'Loss value unknown');
  value.title = 'Estimated value of quota lost at resets in the selected dates. Current unused quota remains spendable.';
  const usage = quotaUsageSummary(p, a, b);
  const positive = section.querySelector('.usage-line');
  quotaSetHtml(positive, usage.percent == null ? 'Utilisation unavailable for these dates' :
    `${usage.percent.toFixed(0)}% plan utilisation` + (usage.usd == null ? '' : ` <small>·</small> $${usage.usd.toFixed(0)} used effectively`));
  positive.title = 'Observed quota periods ending in view plus the active period. Dollars value quota consumed, not output quality or cash savings. See Details for calculation.';
  section.querySelector('.pace-detail').textContent = gap != null && gap >= 5 ? `$${gap.toFixed(0)} of spendable quota is above budget pace. Use it before reset.` : '';
  const expired = events.filter(e => e.kind === 'expired_reset').length;
  const bonus = events.filter(e => e.kind === 'bonus_refill').length;
  section.querySelector('.value-breakdown').textContent =
    `${events.length - expired - bonus} quota renewals · ${bonus} inferred bonus refills · ${expired} expired reset credits in view.` +
    (missing ? ` ${missing} event(s) have unknown value, so a full estimate is unavailable.` : '');
  const credit = p.reset_credits || {};
  const card = section.querySelector('.reset-reserve');
  const checked = Date.parse(credit.checked_at || '');
  const fresh = Number.isFinite(checked) && Date.now() - checked <= 2 * 3600 * 1000;
  const knownCredits = fresh && ['available','none'].includes(credit.status);
  const count = knownCredits ? credit.available : null;
  const expiry = Date.parse(credit.next_expiry || '');
  const days = (expiry - Date.now()) / 86400000;
  let expiryText = count ? 'Expiry not reported' : (knownCredits ? 'No spare quota resets' : 'Availability unverified');
  if (count && Number.isFinite(expiry)) {
    const left = days >= 2 ? `${Math.ceil(days)}d left` : `${Math.max(0,Math.ceil(days*24))}h left`;
    expiryText = `Expires ${quotaDate(expiry / 1000)} · ${left}`;
  }
  if (credit.status === 'unavailable') expiryText = 'Provider does not report resets';
  const cardClass = 'reset-reserve' + (count ? '' : ' empty') + (count && days <= 7 ? ' expiring' : '');
  if (card.className.replace(' tick', '') !== cardClass) card.className = cardClass;
  quotaSetHtml(card, `<strong>${count == null ? '—' : count}</strong>
    <span class="reset-label">${count === 1 ? 'reset available' : 'resets available'}</span>
    <span class="reset-expiry">${quotaEscape(expiryText)}</span>` +
    (count && credit.relaxation ? '<span class="reset-policy">Burn alerts relaxed</span>' : ''));
}

async function openSubscriptionSettings(panel) {
  const sub = panel.subscription;
  const dialog = document.createElement('dialog');
  dialog.className = 'subscription-dialog';
  dialog.innerHTML = `<form>
    <h2>${quotaEscape(panel.vendor)} subscription</h2>
    <p>Enter what you pay. This is saved for your dashboard; nothing is sent to the provider.</p>
    <p class="subscription-plan"></p>
    <label>Monthly cost (USD)<input name="monthly_usd" type="number" min="0" step="0.01" required placeholder="e.g. 30"></label>
    <small>For annual billing, divide your yearly total by 12. Enter 0 for a free plan.</small>
    <details><summary>Quota calculation</summary>
      <label>Regular quota allocations per month<input name="regular_allocations" type="number" min="0.000001" step="any" required></label>
      <small>Default: 30 days divided by the quota window. Change this if your subscription includes a fixed number.</small>
      <label>Extra full resets included per month<input name="included_resets" type="number" min="0" step="1" required></label>
      <small>Included allowance, not your current number of available resets.</small>
    </details>
    <p class="subscription-preview" aria-live="polite"></p>
    <p class="subscription-status" role="status">Loading saved settings…</p>
    <div class="subscription-actions"><button type="button" class="subscription-cancel">Cancel</button><button type="submit" disabled>Save subscription</button></div>
  </form>`;
  document.body.append(dialog);
  const form = dialog.querySelector('form');
  form.addEventListener('submit', event => event.preventDefault());
  const status = dialog.querySelector('.subscription-status');
  const save = form.querySelector('[type=submit]');
  const field = name => form.elements.namedItem(name);
  const preview = () => {
    const cost = Number(field('monthly_usd').value);
    const regular = Number(field('regular_allocations').value);
    const extra = Number(field('included_resets').value);
    dialog.querySelector('.subscription-preview').textContent = field('monthly_usd').value !== '' && regular > 0 && extra >= 0 && cost >= 0
      ? `$${cost.toFixed(2)} ÷ (${regular.toFixed(2).replace(/\.00$/, '')} regular + ${extra} extra) = $${(cost / (regular + extra)).toFixed(2)} per full allocation`
      : 'Enter your monthly cost to preview the calculation.';
  };
  let current = sub, api;
  function fill() {
    field('monthly_usd').value = current.monthly_usd == null ? '' : current.monthly_usd;
    field('regular_allocations').value = current.regular_allocations || 720 / (current.window_hours || sub.window_hours);
    field('included_resets').value = current.included_resets || 0;
    dialog.querySelector('.subscription-plan').textContent = current.plan ? `Reported plan: ${current.plan}. These settings apply to this plan.` : 'The provider does not report a plan. Update this setting if you change subscriptions.';
    preview();
  }
  fill();
  form.addEventListener('input', preview);
  dialog.querySelector('.subscription-cancel').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dialog.remove());
  dialog.showModal();
  field('monthly_usd').focus();
  const endpoint = new URL('../api/subscriptions', location.href);
  try {
    const response = await fetch(endpoint, {cache:'no-store', signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw Error('unavailable');
    api = await response.json();
    if (!api.providers || !api.token || !api.providers[sub.provider]) throw Error('unavailable');
    current = api.providers[sub.provider];
    // Do not overwrite input typed while the request was in flight.
    if (!form.dataset.edited) fill();
    if (!api.writable) {
      status.textContent = 'Settings are supplied by the server environment. Update AI_QUOTAS_SUBSCRIPTIONS_JSON there to change the price.';
    } else {
      status.textContent = current.monthly_usd == null ? 'Price not reported. Enter your subscription cost above.' : 'Saved for this provider and reported plan.';
      save.disabled = false;
    }
  } catch (_) {
    status.textContent = 'This copy cannot save settings. Open the live dashboard to configure your subscription.';
    try {
      const target = new URL(sub.settings_url);
      if (['http:','https:'].includes(target.protocol)) {
        target.hash = 'subscription=' + encodeURIComponent(sub.provider);
        const link = document.createElement('a');
        link.href = target.href;
        link.target = '_blank'; link.rel = 'noopener';
        link.textContent = 'Open live subscription settings';
        status.append(document.createElement('br'), link);
      }
    } catch (_) {
      status.append(' Start ai-quotas dash on the computer holding your quota data.');
    }
  }
  form.onsubmit = async event => {
    event.preventDefault();
    if (save.disabled || !form.reportValidity()) return;
    save.disabled = true;
    const payload = {provider:sub.provider, plan:current.plan || '', expected:api.config[sub.provider] || null};
    for (const name of ['monthly_usd','regular_allocations','included_resets']) payload[name] = Number(field(name).value);
    status.textContent = 'Saving…';
    try {
      const metaUrl = new URL('../meta.json', location.href);
      const before = await fetch(metaUrl, {cache:'no-store'}).then(r=>r.json()).catch(()=>({}));
      const response = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json', 'X-Quota-Token':api.token}, body:JSON.stringify(payload), signal:AbortSignal.timeout(10000)});
      const result = await response.json();
      if (!response.ok || !result.saved) throw Error(result.error || 'Save failed. Please try again.');
      status.textContent = 'Saved. Updating the plots…';
      window.quotaSettingsPending = true;
      for (let attempt=0; attempt<30; attempt++) {
        await new Promise(resolve=>setTimeout(resolve,1000));
        const meta = await fetch(metaUrl, {cache:'no-store'}).then(r=>r.json()).catch(()=>({}));
        if (meta.generated_at && meta.generated_at !== before.generated_at) {
          // the page refreshes its data in place; a reload is only the fallback
          if (window.quotaRefresh) { await window.quotaRefresh(); dialog.close(); } else { location.reload(); }
          return;
        }
      }
      status.textContent = 'Saved. The renderer has not finished updating. Reload this dashboard shortly.';
    } catch (error) {
      status.textContent = error.message;
      save.disabled = false;
    } finally { window.quotaSettingsPending = false; }
  };
  // Keep the reported plan from the server but preserve edits during loading.
}
function openVendorSetup(panel) {
  const setup = panel.setup || {};
  const dialog = document.createElement('dialog');
  dialog.className = 'subscription-dialog';
  const viaOrca = setup.source === 'orca';
  dialog.innerHTML = `<form>
    <h2>Set up ${quotaEscape(panel.vendor)}</h2>
    <p>${quotaEscape(setup.body || 'This vendor is not configured.')}</p>
    ${viaOrca ? '<p>Orca already tracks this quota. After you sign in there, collect a sample.</p>' : ''}
    ${setup.command ? `<pre class="setup-cmd">${quotaEscape(setup.command)}</pre>` : ''}
    <div class="subscription-actions"><button type="submit">Done</button></div>
  </form>`;
  document.body.append(dialog);
  dialog.querySelector('form').addEventListener('submit', event => {
    event.preventDefault();
    dialog.close();
  });
  dialog.addEventListener('close', () => dialog.remove());
  dialog.showModal();
}

if (typeof document !== 'undefined') {
document.addEventListener('input', event => {
  const form = event.target.closest('.subscription-dialog form');
  if (form) form.dataset.edited = '1';
});
window.addEventListener('hashchange', () => {
  const wanted = new URLSearchParams(location.hash.slice(1)).get('subscription');
  const button = [...document.querySelectorAll('.subscription-edit')].find(b => b.dataset.provider === wanted);
  if (button && button.onclick) {
    history.replaceState(null, '', location.pathname + location.search);
    button.click();
  }
});
}
