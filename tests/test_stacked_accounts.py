"""The shipped stack must include accounts and bridge marked display gaps."""
import json
import shutil
import subprocess
from importlib.resources import files
import pytest


def test_real_stack_function_keeps_work_and_all_google_accounts_without_erasing_history():
    if not shutil.which('node'):
        pytest.skip('node not installed')
    t=1900000000
    def panel(provider,account,price,label,stale=False):
        at=t-14*86400 if stale else t
        return {'provider':provider,'vendor':provider+' · '+account,'account':account,'account_label':label,
                'source':{'account':account},'subscription':{'monthly_usd':price,'source':'configured' if price else 'unknown'},
                'series':[{'window':'week_gemini_pro' if provider=='agy' else 'week','label':'week','color':'#5B9BD5','t':[at,at+3600,at+72000],'y':[100,None,50],'resets_at':at+7*86400}],
                'burn_ticks':[],'resets':[],'budget':[],'inferred':[]}
    raw={'panels':[]}
    for provider in ['claude','codex','agy']:
        for i,label in enumerate(['personal','shared','work']):
            raw['panels'].append(panel(provider,label+'@example.com',500 if provider=='codex' and i==0 else 100 if i==1 else None,label))
    for i in range(3):raw['panels'].append(panel('agy',f'extra{i}@example.com',20,f'extra{i}',True))
    source=files('ai_quotas.plots.static').joinpath('stacked.js').read_text()
    script="""
const raw=JSON.parse(require('fs').readFileSync(0,'utf8'));
const original=JSON.stringify(raw);
let quotaLayoutRead=()=>null,quotaDisplayTitle=p=>p.title,quotaSeriesLegend=(p,s)=>s,quotaHeader=()=>'',fillPanel=()=>{},updateQuotaHeader=()=>{};
const quotaLastNumber=ys=>ys.findLast(Number.isFinite),window={addEventListener(){}};
"""+source+"""
const p=quotaStackedStacks(raw),google=p.find(p=>p.provider==='agy'),codex=p.find(p=>p.provider==='codex');
console.log(JSON.stringify({work:p.every(p=>p.series.some(s=>s.stack_account==='work@example.com')),google:google.series.length-1,
 holes:p.some(p=>p.series.at(-1).y.some(v=>v==null)),marked:p.some(p=>p.series.some(s=>s.stack_state?.includes('interpolated'))),
 stale:google.stack_stale,untouched:JSON.stringify(raw)===original,weights:codex.series.slice(1).map(s=>s.stack_weight)}));
"""
    result=subprocess.run(['node','-e',script],input=json.dumps(raw),capture_output=True,text=True,check=False)
    assert result.returncode==0, result.stderr
    data=json.loads(result.stdout)
    assert data['work'] and data['google']==6
    assert not data['holes'] and data['marked']
    assert data['stale']==3 and data['untouched']
    assert data['weights'][:2]==[5,1]
