"""Current Argon is its own measured pool; a log rejection is not a balance."""
import json
from datetime import datetime, timedelta, timezone

from ai_quotas.agy_limits import complete_quota_rows, fetch_complete_quota
from ai_quotas.storage import append_samples


def summary():
    reset = (datetime.now(timezone.utc)+timedelta(days=4)).isoformat()
    return {"groups": [
        {"displayName": "Gemini Models", "buckets": [dict(window="weekly", bucketId="gemini-weekly", remainingFraction=.8, resetTime=reset)]},
        {"displayName": "Gemini 4 Argon", "buckets": [dict(window="weekly", bucketId="gemini-argon-wtu-weekly", remainingFraction=.4090528, resetTime=reset)]},
    ]}


def test_partial_success_does_not_stop_before_production_summary():
    calls=[]
    def fetch(urls, token, body):
        calls.append(urls)
        if 'cloudcode-pa.googleapis.com' in urls[0] and 'daily-' not in urls[0]:
            return summary(), []
        return {"groups":[{"displayName":"Claude and GPT models", "buckets":[]}]}, []
    payload=fetch_complete_quota('unused',load=lambda _:('project','Google AI Pro'),fetch=fetch,
        summary_urls=['https://daily-cloudcode-pa.googleapis.com/quota','https://cloudcode-pa.googleapis.com/quota'],quota_urls=['legacy'])
    assert any(g['displayName']=='Gemini 4 Argon' for g in payload['groups'])


def test_argon_summary_does_not_overwrite_shared_gemini():
    seen=[]
    def shared(ts,payload):
        seen.extend(g['displayName'] for g in payload['groups'])
        return [dict(ts=ts,provider='agy',window='week_gemini_pro',used_percent=20,status='ok')]
    rows=complete_quota_rows(datetime.now(timezone.utc).isoformat(),summary(),summary_parser=shared,bucket_parser=lambda *_:[])
    by={r['window']:r for r in rows}
    assert 'Gemini 4 Argon' not in seen
    assert abs(by['week_gemini_argon']['used_percent']-59.09472)<.00001
    assert by['week_gemini_argon'].get('quota_source')=='retrieveUserQuotaSummary'
    assert not by['week_gemini_argon'].get('individual_cap')
    assert by['week_gemini_pro']['used_percent']==20


def test_dashboard_uses_real_argon_sample_and_not_old_cap_zero(tmp_path):
    from ai_quotas.plots.prep import prepare
    from ai_quotas.plots.generate import _vendor_panel_payload
    now=datetime.now(timezone.utc)
    reset=(now+timedelta(days=4)).isoformat()
    common=dict(provider='agy',account='owner@example.test',status='ok',resets_at=reset)
    db=tmp_path/'q.sqlite3'
    append_samples(db,[
        dict(common,ts=(now-timedelta(hours=3)).isoformat(),window='week_gemini_argon',used_percent=100,individual_cap=True),
        dict(common,ts=now.isoformat(),window='week_gemini_argon',used_percent=59.09472,quota_source='retrieveUserQuotaSummary'),
        dict(common,ts=now.isoformat(),window='week_gemini_pro',used_percent=20),
    ])
    df,resets,_=prepare(db);p=_vendor_panel_payload(df,resets,'Gemini')
    series=next(s for s in p['series'] if s['label']=='Argon week')
    assert series['y']==[40.91]
    assert not series.get('observed_cap')
    assert abs(p['argon_quota']['used_percent']-59.09472)<.00001
    import subprocess
    code=(__import__('pathlib').Path(__file__).parents[1]/'ai_quotas/plots/static/panel_header.js').read_text()
    code+='\nconsole.log(JSON.stringify(quotaPanelState('+json.dumps(p)+','+str(now.timestamp()*1000)+')));'
    state=json.loads(subprocess.check_output(['node','-e',code],text=True))
    assert state['modelLimit'].startswith('Argon · 41% remaining')
    assert state['modelLimitUsable']


def test_paid_subscription_does_not_invent_entitlement_or_argon(tmp_path, monkeypatch):
    from ai_quotas.core import load_samples
    from ai_quotas.plots.prep import prepare, panel_vendors
    from ai_quotas.plots.generate import _vendor_panel_payload
    config = tmp_path/'config.toml'
    config.write_text('[subscription_plans.agy]\n"new@example.test" = "Google AI Pro"\n')
    monkeypatch.setenv('AI_QUOTAS_CONFIG', str(config))
    now = datetime.now(timezone.utc)
    db = tmp_path/'q.sqlite3'
    append_samples(db, [dict(ts=now.isoformat(), provider='agy', account=account,
        status='ok', window='week_gemini_pro', used_percent=0,
        resets_at=(now+timedelta(days=7)).isoformat(), plan='Antigravity Starter Quota')
        for account in ('old@example.test', 'new@example.test')])
    df, resets, _ = prepare(db)
    panels = [_vendor_panel_payload(df, resets, v) for v in panel_vendors(df)
              if v.startswith('Gemini')]
    paid = next(p for p in panels if (p.get('source') or {}).get('account') == 'new@example.test')
    other = next(p for p in panels if (p.get('source') or {}).get('account') == 'old@example.test')
    assert paid['title'] == 'Gemini Pro'
    assert paid['declared_subscription']['mismatch']
    assert paid['declared_subscription']['source'] == 'user'
    assert paid['subscription']['plan'] == 'Antigravity Starter Quota'
    assert paid['argon_quota'] is None
    assert not any(s['label'].startswith('Argon') for s in paid['series'])
    assert other['title'] == 'Gemini Free'
    assert other['declared_subscription'] is None
    assert all(r['plan'] == 'Antigravity Starter Quota' for r in load_samples(db))
    import subprocess
    code = (__import__('pathlib').Path(__file__).parents[1]/'ai_quotas/plots/static/panel_header.js').read_text()
    code += '\nconst p='+json.dumps(paid)+'; console.log(JSON.stringify({title:quotaDisplayTitle(p),argon:quotaArgonLimit(p,'+str(now.timestamp())+')}));'
    state = json.loads(subprocess.check_output(['node','-e',code],text=True))
    assert state == {'title': 'Gemini Pro', 'argon': 'Argon · not measured'}


def test_google_view_combines_pools_without_crossing_accounts():
    import subprocess
    from pathlib import Path
    code=(Path(__file__).parents[1]/'ai_quotas/plots/static/panel_header.js').read_text()
    code+=r"""
const quotaLayoutRead = () => 'second@example.test';
function panel(vendor, account, label, used) {
  return {vendor, configured:true, source:{account}, subscription:{provider:'agy',plan:'Google AI Pro'},
    series:[{label,y:[100-used],t:[1000]}], budget:[],resets:[],
    usage_periods:[{t:1000,used_pct:used,allocation_usd:2}],underutilised:{events:[]}};
}
const raw=[panel('Gemini','first@example.test','Gemini week',20),
 panel('Antigravity','first@example.test','Claude+GPT week',60),
 panel('Gemini · second@example.test','second@example.test','Gemini week · second@example.test',30),
 panel('Antigravity · second@example.test','second@example.test','Claude+GPT week · second@example.test',80),
 {vendor:'Codex',configured:true,subscription:{provider:'codex'}}];
const before=JSON.stringify(raw), out=quotaGooglePanels(raw);
console.log(JSON.stringify({before,after:JSON.stringify(raw),out}));
"""
    got=json.loads(subprocess.check_output(['node','-e',code],text=True))
    assert got['before']==got['after']
    assert len(got['out'])==2
    google=got['out'][0]
    assert google['vendor']=='Google'
    assert google['google_account']=='second@example.test'
    assert [s['y'] for s in google['series']]==[[70],[20]]
    assert len(google['google_accounts'])==2
    assert google['title']=='Google Pro'
    assert len(google['usage_periods'])==2
    assert got['out'][1]['vendor']=='Codex'
