// Account stacks share the native renderer; ordinary panels pass through unchanged.
let quotaStackedShowStacks = quotaLayoutRead('quota-stack-accounts') === 'true';
function quotaStackedMix(color,bg,ratio) {
  const rgb = h => {const n=parseInt(h.replace('#',''),16);return [n>>16&255,n>>8&255,n&255]};
  const a=rgb(color),b=rgb(bg);
  return '#'+a.map((v,i)=>Math.round(v*ratio+b[i]*(1-ratio)).toString(16).padStart(2,'0')).join('');
}
const quotaStackedNames = {};
// Price is an explicit, inspectable proxy. It is not a measured token limit.
function quotaStackedPrice(panel) {
  const price=panel.declared_subscription?.monthly_usd ?? panel.subscription?.monthly_usd;
  // Starter access is not evidence of this Google account's subscription bill.
  if(panel.provider==='agy'&&price===0&&!panel.declared_subscription&&panel.subscription?.source!=='configured')return null;
  return Number.isFinite(price) && price>=0 ? price : null;
}
let quotaStackedWeights={};
try{quotaStackedWeights=JSON.parse(quotaLayoutRead('quota-account-weights')||'{}')}catch{}
function quotaStackedWeightKey(provider,account,panel){
  return [provider,account,panel.declared_subscription?.plan||panel.subscription?.plan||''].join('|');
}
function quotaStackedObservations(s){
  return s.t.flatMap((t,i)=>Number.isFinite(s.y[i])?[{t,y:s.y[i],i}]:[]);
}
function quotaStackedSample(a,t){
  const rows=a.observations;
  let lo=0,hi=rows.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(rows[mid].t<=t)lo=mid+1;else hi=mid}
  const left=rows[lo-1],right=rows[lo];
  if(!left)return {value:null,state:'not observed',at:null};
  if(left.t===t)return {value:left.y,state:'measured',at:left.t};
  if(right){
    const gap=right.i-left.i>1||right.t-left.t>43200;
    return {value:gap?left.y+(right.y-left.y)*(t-left.t)/(right.t-left.t):left.y,
      state:gap?'interpolated':'last reading',at:left.t};
  }
  return {value:left.y,state:a.stale?'stale':'last reading',at:left.t};
}
// Locate the filled account territory at a time; do not carry across gaps.
function quotaStackedTerritory(p,layer,t) {
  const s=p.series[layer+1];
  if(!s)return null;
  let lo=0,hi=s.t.length;
  while(lo<hi){const m=(lo+hi)>>1;if(s.t[m]<=t)lo=m+1;else hi=m}
  const i=lo-1;
  if(i<0||t-s.t[i]>43200||!Number.isFinite(s.y[i]))return null;
  const base=p.series[layer].y[i];
  return Number.isFinite(base)?[base,s.y[i]]:null;
}
function quotaStackedGuides(p,layer,groups) {
  const weight=p.series[layer+1].stack_weight;
  return groups.map(g=>({...g,stack_layer:layer,segs:g.segs.flatMap(seg=>{
    const pieces=[];let piece=[];
    for(let i=0;i+1<seg.length;i++){
      const [t0,y0]=seg[i],[t1,y1]=seg[i+1];
      const times=[t0,...p.series[0].t.filter(t=>t>t0&&t<t1),t1];
      for(const t of times){
        const bounds=quotaStackedTerritory(p,layer,t);
        if(!bounds){if(piece.length>1)pieces.push(piece);piece=[];continue}
        const y=y0+(y1-y0)*(t-t0)/(t1-t0||1);
        piece.push([t,Math.max(bounds[0],Math.min(bounds[1],bounds[0]+y*weight))]);
      }
    }
    if(piece.length>1)pieces.push(piece);return pieces;
  })})).filter(g=>g.segs.length);
}
function quotaStackedTicks(p) {
  const rough=p.stack_capacity/5;
  const power=10**Math.floor(Math.log10(rough));
  const step=[1,2,5,10].map(n=>n*power).find(n=>n>=rough);
  const ticks=[];
  for(let n=0;n<=p.stack_capacity+1e-9;n+=step) ticks.push(Math.round(n*1e8)/1e8*100);
  if(ticks.at(-1)<p.stack_capacity*100-1e-6) ticks.push(p.stack_capacity*100);
  return ticks;
}
function quotaStackedUnit(v) {return Number((v/100).toFixed(2)).toString()}
function quotaStackedWhen(t){
  return Number.isFinite(t)?new Date(t*1000).toLocaleString(undefined,{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):'unavailable';
}
function quotaStackedHover(s,y,t) {
  const parts=[];
  const i=s.t.indexOf(t),state=s.stack_state?.[i];
  if(state==='not observed')return ' · not observed yet';
  if(state==='stale')return ' · last seen '+quotaStackedWhen(s.stack_observed[i])+' · stale';
  if(state==='interpolated')parts.push('interpolated sampling gap');
  if(s.stack_weight_source==='assumed')parts.push('assumed weight '+s.stack_weight);
  parts.push(s.tokens_per_pct ? fmtTok(y*s.tokens_per_pct) : 'tokens unmeasured');
  const usd=s.window_usd;
  parts.push(Number.isFinite(usd) ? '~$'+(y/100*usd*(s.share??1)).toFixed(2) : 'USD unknown');
  return ' · '+parts.join(' · ');
}
function quotaStackedDrawGaps(u,p){
  if(!p.stacked)return;
  const ctx=u.ctx,{left,top,width,height}=u.bbox;
  const dpr=u.pxRatio||1;
  for(const [a,b]of p.stack_gaps){
    if(b<u.scales.x.min||a>u.scales.x.max)continue;
    const x0=Math.max(left,u.valToPos(a,'x',true)),x1=Math.min(left+width,u.valToPos(b,'x',true));
    ctx.save();ctx.beginPath();ctx.rect(x0,top,x1-x0,height);ctx.clip();
    ctx.strokeStyle='#b6c3d2';ctx.globalAlpha=.14;ctx.lineWidth=dpr;
    for(let x=x0-height;x<x1;x+=14*dpr){ctx.beginPath();ctx.moveTo(x,top+height);ctx.lineTo(x+height,top);ctx.stroke()}
    ctx.restore();
  }
}
function quotaStackedStacks(raw) {
  const result = [];
  for (const [provider, title, windowName] of [
    ['claude','Claude','week'], ['codex','Codex','week'],
    ['agy','Google','week_gemini_pro'], ['grok','Grok','week'],
  ]) {
    const candidates = raw.panels.filter(p => p.provider === provider &&
      (p.series || []).some(s => s.window === windowName));
    const unique = new Map();
    for (const p of candidates) {
      const s = p.series.find(s => s.window === windowName);
      const account = p.account || (p.source || {}).account;
      if (!account || !s) continue;
      quotaStackedNames[account]=p.account_label||(!p.account?'personal':account.split('@')[0]);
      const i = s.y.findLastIndex(v => Number.isFinite(v));
      const latest = i < 0 ? 0 : s.t[i];
      if (!unique.has(account) || unique.get(account).latest < latest)
        unique.set(account, {panel:p, series:s, account, latest,observations:quotaStackedObservations(s)});
    }
    const all = [...unique.values()];
    const observed = Math.max(0, ...all.map(a => a.latest));
    const reference=Math.min(...all.map(a=>quotaStackedPrice(a.panel)).filter(p=>p>0).map(p=>p/100));
    const defaultWeight=Number.isFinite(reference)?reference:1;
    all.forEach(a=>{
      a.price=quotaStackedPrice(a.panel);
      a.weightKey=quotaStackedWeightKey(provider,a.account,a.panel);
      const manual=quotaStackedWeights[a.weightKey];
      a.weight=Number.isFinite(manual)&&manual>0?manual:a.price>0?a.price/100:defaultWeight;
      a.weightSource=Number.isFinite(manual)&&manual>0?'manual':a.price>0?'price':'assumed';
      a.stale=!a.latest||observed-a.latest>7200||!!(a.series.resets_at&&a.series.resets_at<=observed);
    });
    const known = all.filter(a=>a.observations.length);
    if (all.length<2 || !known.length) continue;
    const capacity=known.reduce((n,a)=>n+a.weight,0);
    const xs = [...new Set(known.flatMap(a => a.series.t).concat(observed))].sort((a,b) => a-b);
    const samples=known.map(a=>xs.map(t=>quotaStackedSample(a,t)));
    const rawValues=samples.map(rows=>rows.map(r=>r.value));
    // Missing accounts never erase other accounts. Display interpolation is
    // explicitly marked; original samples are untouched. Not-yet-observed
    // layers have no height and no numeric tooltip reading.
    const sum=xs.map(()=>0);
    const first = known[0].panel;
    const series = [{label:'Stack baseline', window:'stack_baseline', color:'transparent',
      focus:false, dim:false, t:xs, y:[...sum], resets_at:null}];
    known.forEach((a,i) => {
      const baseColor = a.series.color;
      const color = i === 0 ? baseColor : quotaStackedMix(baseColor, '#e8e8e8', Math.max(.35, .8-i*.18));
      const y = xs.map((_,j) => (sum[j] += (rawValues[i][j]??0)*a.weight));
      series.push({label:quotaStackedNames[a.account] || a.account.split('@')[0],
        window:'stack_'+i, color, focus:false, dim:false, t:xs, y,
        stack_raw:rawValues[i], stack_weight:a.weight, stack_price:a.price,
        stack_account:a.account,stack_weight_source:a.weightSource,stack_stale:a.stale,
        stack_state:samples[i].map(r=>r.state),stack_observed:samples[i].map(r=>r.at),
        resets_at:a.series.resets_at,
        window_usd:a.price==null?null:a.price/((a.panel.subscription.regular_allocations||720/168)+(a.panel.subscription.included_resets||0)),
        share:a.series.share??1,
        point_usd:[], tokens_per_pct:a.series.tokens_per_pct??null});
    });
    const last = sum.findLastIndex(v => Number.isFinite(v));
    const panel={...first, vendor:'Stacked '+title, title:title+' · all accounts',
      stacked:true, stack_count:known.length, stack_capacity:capacity,
      stack_total:last < 0 ? null : sum[last]/100,
      stack_observed_total:known.reduce((n,a)=>n+(a.stale?0:quotaLastNumber(a.series.y)*a.weight/100),0),
      stack_stale:known.filter(a=>a.stale).length,
      stack_assumed:known.filter(a=>a.weightSource==='assumed').length,
      stack_gaps:known.flatMap(a=>a.observations.slice(1).flatMap((r,i)=>{
        const l=a.observations[i];return r.i-l.i>1||r.t-l.t>43200?[[l.t,r.t]]:[];
      })),
      stack_missing:all.length-known.length,
      stack_accounts:all.map(a=>({label:quotaStackedNames[a.account]||a.account.split('@')[0],account:a.account,weight:a.weight,
        plan:a.panel.subscription?.label,remaining:quotaLastNumber(a.series.y),included:known.includes(a),
        reason:a.stale?'stale':a.weightSource==='assumed'?'assumed weight':'',latest:a.latest,
        stale:a.stale,weightSource:a.weightSource,weightKey:a.weightKey,price:a.price})),
      account:null,
      source:{account:null,device:first.source && first.source.device,ts:first.source && first.source.ts},
      subscription:{...first.subscription,plan:'',label:title+' · all accounts',monthly_usd:null,
        basis:'All accounts are plotted. Known prices use monthly USD / $100 as a weight proxy. Unknown or free-plan capacity uses the smallest known positive weight for this provider until edited here; ? marks that assumed weight, not a claimed price or verified token allowance. Stale layers show the last observation as a dashed ghost and are not included in the observed current subtotal. Hatched intervals bridge sampling gaps for display only; hover identifies interpolation. Original measurements stay unchanged.'},
      declared_subscription:null, google_account:null, google_accounts:null,
      model_limits:[], argon_quota:null, missing_windows:[], short_reading:null,
      credits_balance:null, reset_credits:{status:'unavailable',checked_at:null,available:null,credits:[]},
      underutilised:[],usage_periods:[],boosts:[],series,burn_ticks:[],budget:[],inferred:[],spend:[],
      resets:[],
      distinguish:'All '+title+' accounts; ? = assumed weight, dashed = stale observation.',configured:true};
    panel.stack_gaps=panel.stack_gaps.sort((a,b)=>a[0]-b[0]).reduce((ranges,r)=>{
      const last=ranges.at(-1);if(last&&r[0]<=last[1])last[1]=Math.max(last[1],r[1]);else ranges.push([...r]);return ranges;
    },[]);
    known.forEach((a,layer)=>{
      const color=series[layer+1].color;
      for(const b of a.panel.burn_ticks||[]){
        if(b.color!==a.series.color)continue;
        const pts=b.pts.flatMap(([t,y])=>{
          const bounds=quotaStackedTerritory(panel,layer,t);
          return bounds?[[t,Math.max(bounds[0],Math.min(bounds[1],bounds[0]+y*a.weight)),bounds[0]]]:[];
        });
        panel.burn_ticks.push({color,pts,stack_layer:layer});
      }
      panel.budget.push(...quotaStackedGuides(panel,layer,(a.panel.budget||[]).filter(b=>b.color===a.series.color)));
      panel.inferred.push(...quotaStackedGuides(panel,layer,(a.panel.inferred||[]).filter(b=>b.color===a.series.color)));
      for(const r of a.panel.resets||[]){
        const bounds=quotaStackedTerritory(panel,layer,r.t);
        if(!bounds)continue;
        panel.resets.push({...r,stack_layer:layer,stack_bounds:bounds,
          tooltip:(quotaStackedNames[a.account]||a.account)+' · '+(r.tooltip||r.label||'')});
      }
    });
    result.push(panel);
  }
  return result;
}
function quotaStackedPanels(data) {
  const originals = quotaGooglePanels(data.panels);
  if(!quotaStackedShowStacks)return originals;
  const stacks=quotaStackedStacks(data),byProvider=new Map(stacks.map(p=>[p.provider,p]));
  const emitted=new Set();
  return originals.flatMap(p=>{
    const stack=byProvider.get(p.provider);
    if(!stack)return [p];
    if(emitted.has(p.provider))return [];
    emitted.add(p.provider);return [stack];
  });
}
const quotaStackedOriginalTitle = quotaDisplayTitle;
quotaDisplayTitle = function(p) {
  return p.stacked ? p.title : quotaStackedOriginalTitle(p);
};
const quotaStackedOriginalLegend=quotaSeriesLegend;
quotaSeriesLegend=function(p,label){
  if(!p.stacked)return quotaStackedOriginalLegend(p,label);
  const s=p.series.find(s=>s.label===label);
  return label+(s?.stack_weight_source==='assumed'?' ?':'')+(s?.stack_stale?' · stale':'');
};
const quotaStackedOriginalHeader = quotaHeader;
quotaHeader = function(p) {
  const html = quotaStackedOriginalHeader(p);
  if (!p.stacked) return html;
  const box = document.createElement('div');
  box.innerHTML = html;
  box.querySelectorAll('.account-name,.subscription-edit,.subscription-note').forEach(el => el.remove());
  return box.innerHTML;
};
const quotaStackedOriginalFill = fillPanel;
fillPanel = function(c,p) {
  quotaStackedOriginalFill(c,p);
  if(p.stacked && c.legend) c.legend.querySelector('span')?.remove();
};
const quotaStackedOriginalUpdate = updateQuotaHeader;
updateQuotaHeader = function(section,p,range) {
  if (!p.stacked) return quotaStackedOriginalUpdate(section,p,range);
  section.dataset.stacked = p.provider;
  section.querySelector('.quota-reset-summary').textContent =
    p.stack_stale?`${p.stack_observed_total.toFixed(2)} observed units · ${p.stack_stale} stale`:
    `${p.stack_total == null ? 'Unknown' : p.stack_total.toFixed(2)} / ${Number(p.stack_capacity.toFixed(2))} weighted quota units`;
  section.querySelector('.usage-line').textContent = 'Weighted estimate'+(p.stack_assumed?` · ${p.stack_assumed} assumed ${p.stack_assumed===1?'weight':'weights'} ?`:'');
  section.querySelector('.usage-line').title=p.subscription.basis;
  section.querySelector('.value-line').textContent = '';
  section.querySelectorAll('.reset-reserve,.credit-balance').forEach(el => {el.hidden=true});
  section.querySelector('.stack-account-status')?.remove();
  const details = section.querySelector('.value-details');
  details.innerHTML = '<summary>Details & settings</summary><p>'+quotaEscape(p.subscription.basis)+'</p><button type="button" data-reset-stack-weights aria-label="Reset weights" title="Restore automatic weights for these accounts">↺</button>'+p.stack_accounts.map(a=>
    '<label style="display:flex;align-items:center;gap:8px;margin:6px 0" title="'+quotaEscape(a.account+' · '+(a.stale?'last seen '+quotaStackedWhen(a.latest):a.plan||'Plan unknown'))+'">'+
    '<span style="min-width:90px">'+quotaEscape(a.label)+'</span><input aria-label="'+quotaEscape(a.label)+' weight" data-stack-weight="'+quotaEscape(a.weightKey)+'" type="number" min="0.01" step="0.1" value="'+Number(a.weight.toFixed(2))+'" style="width:70px">'+
    '<small>'+quotaEscape(a.weightSource==='price'?'$'+a.price+'/month':a.weightSource==='assumed'?'assumed reference weight':'manual weight')+' · '+(a.remaining??'?')+'%'+(a.stale?' · stale':'')+'</small></label>').join('');
  details.querySelectorAll('[data-stack-weight]').forEach(input=>input.onchange=()=>{
    const value=Number(input.value);if(!(value>0&&Number.isFinite(value)))return;
    quotaStackedWeights[input.dataset.stackWeight]=value;
    quotaLayoutSave('quota-account-weights',JSON.stringify(quotaStackedWeights));
    render(window._quotaRawData);
    const next=document.querySelector('[data-stacked="'+p.provider+'"] .value-details');
    if(next)next.open=true;
  });
  details.querySelector('[data-reset-stack-weights]').onclick=()=>{
    p.stack_accounts.forEach(a=>delete quotaStackedWeights[a.weightKey]);
    quotaLayoutSave('quota-account-weights',JSON.stringify(quotaStackedWeights));
    render(window._quotaRawData);
    const next=document.querySelector('[data-stacked="'+p.provider+'"] .value-details');if(next)next.open=true;
  };
  section.querySelectorAll('.legend span').forEach((el,i)=>{
    const s=p.series[i+1];if(s)el.title=s.label+' · '+s.stack_weight+' units · '+s.stack_weight_source+' weight'+(s.stack_stale?' · last seen '+quotaStackedWhen(quotaLastNumber(s.stack_observed)):'');
  });
  const toggle = section.querySelector('.details-toggle');
  toggle.onclick = () => {details.open=!details.open};
  details.ontoggle = () => toggle.setAttribute('aria-expanded',String(details.open));
};
function quotaStackedControls() {
  const controls = document.querySelector('header .controls');
  const label = document.createElement('label');
  label.style.cssText='display:inline-flex;align-items:center;gap:5px;white-space:nowrap';
  label.title='Switch between account plots and stacked provider plots. Single-account providers retain their original plot.';
  label.innerHTML = '<input type="checkbox" data-show-stacked>Stack accounts';
  label.querySelector('input').checked=quotaStackedShowStacks;
  label.querySelector('input').onchange = e => {
    quotaStackedShowStacks=e.target.checked;
    quotaLayoutSave('quota-stack-accounts',String(quotaStackedShowStacks));
    render(window._quotaRawData);
  };
  controls.insertBefore(label,controls.querySelector('.layout-controls'));
}
window.addEventListener('DOMContentLoaded',quotaStackedControls,{once:true});
