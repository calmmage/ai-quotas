// One navigation surface; quota charts stay mounted when another tab is open.
let quotaNavData=null,quotaNavTracking=null,quotaNavToken=null,quotaNavScope='all';
const quotaNavTabs=[['quotas','Quotas'],['api','API balances'],['credits','Credits to claim'],['keys','API keys']];
const quotaApiProviders={claude:['Claude','https://platform.claude.com/settings/keys','https://platform.claude.com/settings/billing'],codex:['OpenAI','https://platform.openai.com/api-keys','https://platform.openai.com/settings/organization/billing/overview'],agy:['Google','https://aistudio.google.com/api-keys','https://console.cloud.google.com/billing'],grok:['xAI','https://console.x.ai/','https://console.x.ai/'],openrouter:['OpenRouter','https://openrouter.ai/settings/keys','https://openrouter.ai/settings/credits']};
const quotaApiEscape=v=>quotaEscape(String(v??''));
const quotaApiMoney=v=>Number.isFinite(v)?'$'+v.toFixed(2):'Unverified';
const quotaApiDate=v=>v?new Date(v).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'}):'Unknown';
function quotaApiInfo(provider){return quotaApiProviders[provider]||[provider,null,null]}
function quotaApiSettings(){return quotaNavData?.panels.find(p=>p.subscription?.settings_url)?.subscription.settings_url||null}
function quotaApiHere(){const url=quotaApiSettings();return ['localhost','127.0.0.1','[::1]'].includes(location.hostname)||(url&&new URL(url,location.href).origin===location.origin)}
function quotaApiRows(){return (quotaNavTracking?.accounts||[]).filter(a=>quotaNavScope==='all'||a.account===quotaNavScope)}
function quotaNavigationUpdate(data){
 quotaNavData=data;if(data.api_tracking&&(!quotaNavTracking||Date.parse(data.api_tracking.updated_at||'1970-01-01')>=Date.parse(quotaNavTracking.updated_at||'1970-01-01')))quotaNavTracking=data.api_tracking;
 if(document.querySelector('.quota-api-page')){
  quotaNavigationRender();
  // A direct HTTPS editor link learns its trusted origin from panels.json,
  // after DOMContentLoaded. Retry then rather than waiting for another click.
  if(document.body.dataset.quotaTab!=='quotas'&&!quotaNavToken&&quotaApiHere())quotaApiRefresh();
 }
}
function quotaNavigationSelect(tab){
 if(!quotaNavTabs.some(t=>t[0]===tab))tab='quotas';
 document.body.dataset.quotaTab=tab;
 document.querySelectorAll('[data-quota-nav]').forEach(b=>{if(b.dataset.quotaNav===tab)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')});
 const page=document.querySelector('.quota-api-page');if(page)page.hidden=tab==='quotas';
 if(location.hash.slice(1)!==tab)history.replaceState(null,'','#'+tab);
 if(tab==='quotas'){if(window._quotaRawData&&typeof setCols==='function')setCols(null,false)}
 else {quotaNavigationRender();if(quotaApiHere())quotaApiRefresh()}
}
async function quotaApiRefresh(){
 try{const res=await fetch(new URL('../api/tracking',location.href),{cache:'no-store'});if(!res.ok)return;
  const payload=await res.json();quotaNavToken=payload.token;quotaNavTracking=payload.tracking;quotaNavigationRender();
  const q=new URLSearchParams(location.search);
  if(q.has('edit')&&!quotaApiRefresh.opened){quotaApiRefresh.opened=true;const a=quotaNavTracking.accounts.find(a=>a.provider===q.get('provider')&&a.account===q.get('account'));if(a)quotaApiEdit(q.get('edit'),a)}
 }catch(_){/* Published snapshots remain readable while the editor is unavailable. */}
}
function quotaNavigationRender(){
 const host=document.querySelector('.quota-api-page'),tab=document.body.dataset.quotaTab;if(!host||tab==='quotas')return;
 const title=quotaNavTabs.find(t=>t[0]===tab)?.[1]||'API balances';
 const all=quotaNavTracking?.accounts||[],rows=quotaApiRows();
 const scopes=[...new Map(all.map(a=>[a.account,a.label+' · '+a.account])).entries()];
 let summary='';
 if(tab==='api'){
  const unique=new Map(rows.filter(a=>a.balance&&(!a.balance.expires_at||Date.parse(a.balance.expires_at)>Date.now())).map(a=>[a.provider+'|'+a.balance.organization,a.balance]));
  const amount=[...unique.values()].reduce((n,b)=>n+(Number.isFinite(b.amount_usd)?b.amount_usd:0),0);
  summary=unique.size?'<div class="quota-api-total" title="Recorded observations, counted once per provider billing organization. Grants are not added a second time.">Last recorded '+quotaApiMoney(amount)+' · '+unique.size+' billing '+(unique.size===1?'organization':'organizations')+'</div>':'';
 }
 const headings=tab==='api'?['Provider / account','API balance','Expires','Organization','']:tab==='credits'?['Provider / account','Claim status','Recorded grant','Expires','']:['Provider / account','Vault reference','Fingerprint','Source',''];
 host.innerHTML='<div class="quota-api-title"><h1>'+title+'</h1><select aria-label="API account filter"><option value="all">All accounts</option>'+scopes.map(([id,label])=>'<option value="'+quotaApiEscape(id)+'"'+(quotaNavScope===id?' selected':'')+'>'+quotaApiEscape(label)+'</option>').join('')+'</select></div>'+summary+
  (quotaNavTracking?.unavailable?'<p class="quota-api-notice">Saved API records are temporarily unavailable.</p>':'')+
  '<div class="quota-api-scroll"><table class="quota-api-table"><thead><tr>'+headings.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map((a,i)=>{
   const [provider,keys,billing]=quotaApiInfo(a.provider),r=tab==='api'?a.balance:tab==='credits'?a.grant:null;
   const identity='<td>'+quotaApiEscape(provider)+' · '+quotaApiEscape(a.label)+'<small title="'+quotaApiEscape(a.plan)+'">'+quotaApiEscape(a.account)+'</small></td>';
   let cells='';
   if(tab==='api')cells='<td class="quota-api-number" title="'+quotaApiEscape(r?'User-recorded observation · '+r.observed_at:'No verified API billing observation for this account')+'">'+quotaApiMoney(r?.amount_usd)+(r?.expires_at&&Date.parse(r.expires_at)<=Date.now()?'<small>Expired snapshot</small>':'')+'</td><td>'+quotaApiDate(r?.expires_at)+'</td><td>'+quotaApiEscape(r?.organization||'Not linked')+'</td>';
   if(tab==='credits')cells='<td>'+quotaApiEscape(r?.status?.replaceAll('_',' ')||'Not checked')+'</td><td class="quota-api-number" title="Grant records are separate from subscription resets and are not added to API balances">'+quotaApiMoney(r?.amount_usd)+'</td><td>'+quotaApiDate(r?.expires_at)+'</td>';
   if(tab==='keys')cells='<td>'+((a.keys||[]).map(k=>'<code>'+quotaApiEscape(k.vault_name)+'</code>').join('<br>')||'<span class="quota-api-muted">Not linked</span>')+'</td><td>'+((a.keys||[]).map(k=>quotaApiEscape(k.fingerprint||'Not recorded')).join('<br>')||'—')+'</td><td title="Vault references only; this page never reads or reveals key values">Existing vault</td>';
   const link=tab==='credits'&&a.provider==='codex'?'https://developers.openai.com/api/docs/guides/chatgpt-api-credits':tab==='credits'&&a.provider==='claude'?'https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers':tab==='keys'?keys:billing,kind=tab==='api'?'balance':tab==='credits'?'grant':'key';
   return '<tr>'+identity+cells+'<td><div class="quota-api-actions"><button type="button" data-api-edit="'+i+'" title="'+(quotaApiHere()?'Record a verified observation':'Open this account in the local editor')+'">'+(tab==='keys'?'Link reference':'Record')+'</button>'+(link?'<a href="'+link+'" target="_blank" rel="noopener">Manage ↗</a>':'')+'<button type="button" data-api-quota="'+i+'" title="Return to this provider’s quota plot">↩</button></div></td></tr>';
  }).join('')+'</tbody></table></div>'+
  (tab==='keys'&&quotaNavTracking?.configured_key_names?.length?'<details class="quota-api-notice"><summary>Configured key names without a verified account link</summary>'+quotaNavTracking.configured_key_names.map(k=>'<p>'+quotaApiEscape(quotaApiInfo(k.provider)[0])+' · <code>'+quotaApiEscape(k.vault_name)+'</code></p>').join('')+'</details>':'')+(!all.length?'<p class="quota-api-notice">'+(quotaNavData?'No named accounts have been observed yet.':'Loading account observations…')+'</p>':'');
 host.querySelector('select').onchange=e=>{quotaNavScope=e.target.value;quotaNavigationRender()};
 host.querySelectorAll('[data-api-edit]').forEach(b=>b.onclick=()=>quotaApiEdit(tab==='api'?'balance':tab==='credits'?'grant':'key',rows[Number(b.dataset.apiEdit)]));
 host.querySelectorAll('[data-api-quota]').forEach(b=>b.onclick=()=>{
  const a=rows[Number(b.dataset.apiQuota)];if(a.provider==='agy'){quotaLayoutSave('quota-google-account',a.account);if(!quotaStackedShowStacks)render(window._quotaRawData)}
  quotaNavigationSelect('quotas');requestAnimationFrame(()=>{
   const panel=window._quotaPanels.find(p=>p.provider===a.provider&&(p.stacked||p.account===a.account||p.source?.account===a.account));
   const section=[...document.querySelectorAll('#grid>.panel')].find(el=>el.dataset.vendor===panel?.vendor||el.dataset.stacked===a.provider);section?.scrollIntoView({block:'start'});
  });
 });
}
async function quotaApiEdit(kind,a){
 if(!quotaNavToken){
  if(quotaApiHere())await quotaApiRefresh();
  if(!quotaNavToken){const settings=quotaApiSettings();if(!settings){alert('The local API editor is unavailable.');return}
   const url=new URL('10_uplot/index.html',settings.endsWith('/')?settings:settings+'/');url.hash=kind==='balance'?'api':kind==='grant'?'credits':'keys';
   url.search=new URLSearchParams({edit:kind,provider:a.provider,account:a.account});window.open(url,'_blank','noopener');return;
  }
 }
 document.querySelector('.quota-api-dialog')?.remove();const d=document.createElement('dialog');d.className='quota-api-dialog';
 const current=kind==='balance'?a.balance:kind==='grant'?a.grant:a.keys?.[0];
 const field=(name,label,type='text',value='',required=false)=>'<label>'+label+'<input name="'+name+'" type="'+type+'" value="'+quotaApiEscape(value??'')+'"'+(required?' required':'')+(type==='number'?' min="0" step="0.01"':'')+'></label>';
 d.innerHTML='<form><h2>'+(kind==='balance'?'Record API balance':kind==='grant'?'Record credit claim':'Link vault reference')+'</h2><p>'+quotaApiEscape(quotaApiInfo(a.provider)[0]+' · '+a.account)+'</p>'+
  (kind==='key'?field('vault_name','Vault variable name','text',current?.vault_name,true)+field('fingerprint','Masked fingerprint','text',current?.fingerprint):
   field('organization','Billing organization ID','text',current?.organization,kind==='balance')+
   (kind==='grant'?'<label>Claim status<select name="status">'+['not_checked','eligible','linked','issued','exhausted','expired','ineligible'].map(s=>'<option value="'+s+'"'+(s===(current?.status||'not_checked')?' selected':'')+'>'+s.replaceAll('_',' ')+'</option>').join('')+'</select></label>':'')+
   field('amount_usd',kind==='balance'?'Observed balance (USD)':'Issued grant (USD)','number',current?.amount_usd,kind==='balance')+
   (kind==='grant'?field('remaining_usd','Grant remaining (USD)','number',current?.remaining_usd):'')+
   field('expires_at','Expires (leave blank if unreported)','datetime-local',current?.expires_at?new Date(new Date(current.expires_at).getTime()-new Date(current.expires_at).getTimezoneOffset()*60000).toISOString().slice(0,16):'')+
   field('observed_at','Observed at','datetime-local',new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16),true))+
  '<p class="quota-api-error" role="alert"></p><footer><button type="button" data-cancel>Cancel</button><button type="submit">Save</button></footer></form>';
 document.body.append(d);d.querySelector('[data-cancel]').onclick=()=>d.close();d.addEventListener('close',()=>d.remove(),{once:true});
 d.querySelector('form').onsubmit=async e=>{e.preventDefault();const body={kind,provider:a.provider,account:a.account};
  for(const [key,value]of new FormData(e.target)){body[key]=key.endsWith('_at')&&value?new Date(value).toISOString():value}
  const save=d.querySelector('[type=submit]');save.disabled=true;
  try{const res=await fetch(new URL('../api/tracking',location.href),{method:'POST',headers:{'Content-Type':'application/json','X-Quota-Token':quotaNavToken},body:JSON.stringify(body)});const payload=await res.json();if(!res.ok)throw new Error(payload.error||'Could not save record');quotaNavTracking=payload.tracking;d.close();quotaNavigationRender()}
  catch(err){d.querySelector('[role=alert]').textContent=err.message;save.disabled=false}
 };d.showModal();
}
function quotaNavigationMount(){
 const bar=document.createElement('div');bar.className='quota-main-nav';bar.innerHTML='<strong>AI quotas</strong><nav aria-label="Main navigation">'+quotaNavTabs.map(([key,label])=>'<button type="button" data-quota-nav="'+key+'">'+label+'</button>').join('')+'</nav>';
 document.body.prepend(bar);const host=document.createElement('main');host.className='quota-api-page';host.hidden=true;document.body.append(host);
 bar.querySelectorAll('button').forEach(b=>b.onclick=()=>quotaNavigationSelect(b.dataset.quotaNav));
 const size=()=>{document.documentElement.style.setProperty('--quota-main-nav-height',bar.getBoundingClientRect().height+'px');if(window._quotaRawData&&document.body.dataset.quotaTab==='quotas')setCols(null,false)};
 new ResizeObserver(size).observe(bar);size();quotaNavigationSelect(location.hash.slice(1)||'quotas');
}
window.addEventListener('DOMContentLoaded',quotaNavigationMount,{once:true});
