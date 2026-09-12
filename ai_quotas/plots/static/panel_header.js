function quotaEscape(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function quotaDate(t) {
  const d = new Date(t * 1000);
  return `${String(d.getDate()).padStart(2,'0')} ${d.toLocaleString('en', {month:'short'})} ${d.getFullYear()}`;
}
function quotaHeader(p) {
  return `<div class="value-summary"><h2>${quotaEscape(p.vendor)}</h2>
    <p class="value-line"></p><p class="period-line"></p>
    <p class="sample-status" role="status" hidden></p>
    <details class="value-details"><summary>Details & settings</summary>
      <button type="button" class="subscription-edit" data-provider="${quotaEscape(p.subscription.provider)}">Subscription settings</button>
      <p>${quotaEscape(p.subscription.basis)}</p><p class="value-breakdown"></p>
      <p>Subscription-value estimate, not a cash charge. Unused quota at scheduled renewals and expired reset credits adds to it. Used quota from extra bonus refills reduces it. A redeemed included credit counts once: its unused portion is underutilised. Current quota and available resets are still spendable, so excluded. Bonus refills are inferred from early renewals; sampling gaps may hide usage or additional resets.</p>
    </details></div><aside class="reset-reserve" aria-label="Available quota resets"></aside>`;
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
  const value = section.querySelector('.value-line');
  if (missing || (!events.length && p.subscription.monthly_usd == null)) {
    value.textContent = 'Underutilised value unknown';
  } else {
    value.innerHTML = `$${total.toFixed(0)} underutilised <small>· estimated</small>`;
  }
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
  card.className = 'reset-reserve' + (count ? '' : ' empty') + (count && days <= 7 ? ' expiring' : '');
  card.innerHTML = `<strong>${count == null ? '—' : count}</strong>
    <span class="reset-label">${count === 1 ? 'reset available' : 'resets available'}</span>
    <span class="reset-expiry">${quotaEscape(expiryText)}</span>` +
    (count && credit.relaxation ? '<span class="reset-policy">Burn alerts relaxed</span>' : '');
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
        if (meta.generated_at && meta.generated_at !== before.generated_at) { location.reload(); return; }
      }
      status.textContent = 'Saved. The renderer has not finished updating. Reload this dashboard shortly.';
    } catch (error) {
      status.textContent = error.message;
      save.disabled = false;
    } finally { window.quotaSettingsPending = false; }
  };
  // Keep the reported plan from the server but preserve edits during loading.
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
