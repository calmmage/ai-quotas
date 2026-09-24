function quotaEscape(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function quotaDate(t) {
  const d = new Date(t * 1000);
  return `${String(d.getDate()).padStart(2,'0')} ${d.toLocaleString('en', {month:'short'})} ${d.getFullYear()}`;
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
    <p class="value-line"></p><p class="period-line"></p>
    <p class="sample-status" role="status" hidden></p>
    ${missingPrice ? settingsButton : ''}
    <details class="value-details"><summary>Details & settings</summary>
      ${missingPrice ? '' : settingsButton}
      <p>${quotaEscape(p.subscription.basis)}</p><p class="value-breakdown"></p>
      <p>Subscription-value estimate, not a cash charge. "Above reset pace" is what is still left beyond the line that hits zero at the next reset — spendable, and wiped if you don't. "Wiped at reset" is unused quota at a scheduled renewal, plus expired reset credits. A refill well before that deadline is a bonus: only the slice already used counts, and it reduces the wiped total. A redeemed included credit counts once. Sampling gaps may hide usage or additional resets.</p>
    </details></div><aside class="reset-reserve" aria-label="Available quota resets"></aside>`;
}
// Dollars still above the line that reaches 0 at the next scheduled reset,
// at the last sample inside the visible range. Null when the series has no price.
function quotaAbovePace(p, end) {
  const series = (p.series || []).find(s => s.focus && s.window_usd);
  if (!series) return null;
  let y = null, t = null;
  for (let i = 0; i < series.t.length; i++) {
    if (series.t[i] > end) break;
    if (series.y[i] != null) { t = series.t[i]; y = series.y[i]; }
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
function updateQuotaHeader(section, p, range) {
  section.querySelector('.subscription-edit').onclick = () => openSubscriptionSettings(p);
  const sampled = p.sampled_at;
  const stale = Number.isFinite(sampled) && Date.now() / 1000 - sampled > 2 * 3600;
  const sampleStatus = section.querySelector('.sample-status');
  sampleStatus.hidden = !stale;
  sampleStatus.textContent = stale ? `Data stale · last sample ${quotaDate(sampled)} ${new Date(sampled * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}` : '';
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
  const gap = quotaAbovePace(p, b);
  const priced = p.subscription.monthly_usd != null;
  const value = section.querySelector('.value-line');
  const bits = [];
  if (gap != null && gap >= 0.5) bits.push(`$${gap.toFixed(0)} above reset pace`);
  if (!missing && total >= 0.5) bits.push(`$${total.toFixed(0)} wiped at reset`);
  if (missing) bits.push('reset price unknown');
  if (!bits.length && (priced || events.length)) bits.push('$0 underutilised');
  quotaSetHtml(value, bits.length ? bits.join(' <small>·</small> ') + ' <small>· estimated</small>' : 'Underutilised value unknown');
  section.querySelector('.period-line').textContent = `${quotaDate(a)} – ${quotaDate(b)}`;
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
