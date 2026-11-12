from pathlib import Path
import pytest
from ai_quotas import api_tracking


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(api_tracking, 'database_path', lambda: tmp_path / 'quota.sqlite3')
    monkeypatch.setattr(api_tracking, 'configured_key_names', lambda: [])
    return {('codex', 'personal@example.com'), ('codex', 'work@example.com')}


def panel(account):
    return {'provider':'codex','account':account,'source':{'account':account},'subscription':{'label':'Pro','monthly_usd':100}}


def test_shared_billing_organization_is_counted_once_and_grants_are_separate(ledger):
    for account in ['personal@example.com','work@example.com']:
        api_tracking.record({'kind':'balance','provider':'codex','account':account,'organization':'org-shared','amount_usd':75}, known_accounts=ledger)
    api_tracking.record({'kind':'grant','provider':'codex','account':'personal@example.com','organization':'org-shared','status':'issued','amount_usd':100,'remaining_usd':75,'expires_at':'2030-01-01T00:00:00Z'}, known_accounts=ledger)
    data=api_tracking.snapshot([panel(a) for _,a in ledger])
    assert len(data['organizations'])==1
    assert [a['balance']['amount_usd'] for a in data['accounts']]==[75,75]
    assert sum(r['amount_usd'] for r in data['organizations'])==75
    assert len(data['grants'])==1
    assert len(api_tracking.read_records())==3


def test_unobserved_balances_remain_unknown(ledger):
    data=api_tracking.snapshot([panel('personal@example.com')])
    assert data['accounts'][0]['balance'] is None
    assert data['accounts'][0]['grant'] is None
    assert not data['organizations']


@pytest.mark.parametrize('changes',[
    {'amount_usd':-1}, {'amount_usd':float('nan')}, {'account':'stranger@example.com'},
    {'kind':'key','vault_name':'sk-proj-this-is-a-secret'},
    {'kind':'key','vault_name':'OPENAI_API_KEY','fingerprint':'a-raw-key'},
    {'api_key':'never accept a key value'},
    {'kind':'grant','status':'issued','remaining_usd':200},
])
def test_invalid_or_secret_records_cannot_be_saved(ledger,changes):
    data={'kind':'balance','provider':'codex','account':'personal@example.com','organization':'org-test','amount_usd':10,**changes}
    if data['kind']=='key':
        data={k:v for k,v in data.items() if k not in {'organization','amount_usd'}}
    with pytest.raises(ValueError): api_tracking.record(data,known_accounts=ledger)
    assert api_tracking.read_records()==[]


def test_key_records_store_only_vault_reference_and_masked_fingerprint(ledger):
    api_tracking.record({'kind':'key','provider':'codex','account':'personal@example.com','vault_name':'OPENAI_API_KEY','fingerprint':'…1234'},known_accounts=ledger)
    key=api_tracking.snapshot([panel('personal@example.com')])['accounts'][0]['keys'][0]
    assert key['vault_name']=='OPENAI_API_KEY'
    assert key['fingerprint']=='…1234'
    assert 'value' not in key


def test_tracking_endpoint_requires_same_origin_token_and_persists(ledger,tmp_path):
    import json,threading,urllib.request,urllib.error
    from ai_quotas.plots.dash import make_server
    (tmp_path/'panels.json').write_text(json.dumps({'panels':[panel('personal@example.com')]}))
    server=make_server(tmp_path,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/api/tracking'
    def request(body=None,token=None,origin=None):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Quota-Token']=token
        if origin:headers['Origin']=origin
        req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    try:
        _,info=request();body={'kind':'balance','provider':'codex','account':'personal@example.com','organization':'org-test','amount_usd':15}
        assert request(body)[0]==403
        assert request(body,info['token'],'https://foreign.example')[0]==403
        assert api_tracking.read_records()==[]
        code,saved=request(body,info['token'])
        assert code==200
        assert saved['tracking']['accounts'][0]['balance']['amount_usd']==15
        assert request()[1]['tracking']['accounts'][0]['balance']['amount_usd']==15
        assert server.settings_changed.is_set()
    finally:
        server.shutdown();server.server_close();thread.join(3)
