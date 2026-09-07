import json
from pathlib import Path
from ai_quotas.plots.generate import generate_plots
from ai_quotas.plots.dash import _stamp


def test_regenerating_old_samples_does_not_make_collection_look_fresh(tmp_path):
    fixture = Path(__file__).parent/'fixtures/multi.jsonl'
    result = generate_plots(samples=fixture, out_dir=tmp_path, engines=('plotly',))
    _stamp(tmp_path,30,result['sampled_at'])
    meta = json.loads((tmp_path/'meta.json').read_text())
    assert meta['sampled_at'] == result['sampled_at']
    assert meta['sampled_at'] != meta['generated_at']
    assert meta['generated_at'] in (tmp_path/'live.html').read_text()
