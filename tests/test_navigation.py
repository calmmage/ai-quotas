"""Direct editor links must become writable when their origin metadata arrives."""
import json,shutil,subprocess
from importlib.resources import files
import pytest


@pytest.mark.parametrize('origin,expected',[('https://mac.example.test',1),('https://cloud.example.test',0)])
def test_api_editor_bootstraps_after_account_metadata_arrives(origin,expected):
    if not shutil.which('node'):pytest.skip('node not installed')
    source=files('ai_quotas.plots.static').joinpath('navigation.js').read_text()
    script="""
const host={hidden:true};
const document={body:{dataset:{}},querySelector:()=>host,querySelectorAll:()=>[]};
const location={hostname:new URL(ORIGIN).hostname,origin:ORIGIN,hash:'#api'};
const history={replaceState(){}},window={addEventListener(){}};
""".replace('ORIGIN',json.dumps(origin))+source+"""
let calls=0;
quotaNavigationRender=()=>{};
quotaApiRefresh=()=>{calls++};
quotaNavigationSelect('api');
const before=calls;
quotaNavigationUpdate({panels:[{subscription:{settings_url:'https://mac.example.test/quotas/'}}],api_tracking:{accounts:[],updated_at:null}});
console.log(JSON.stringify({before,after:calls}));
"""
    p=subprocess.run(['node','-e',script],capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)=={'before':0,'after':expected}
