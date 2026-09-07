"""Subscription form uses persistent settings; rejects unsafe or stale writes."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from ai_quotas import subscriptions
from ai_quotas.plots.dash import make_server


@pytest.fixture
def settings_server(tmp_path):
    (tmp_path / 'subscriptions-view.json').write_text(json.dumps({
        'grok': {**subscriptions.resolve('grok', None), 'window_hours': 24},
        'codex': {**subscriptions.resolve('codex', 'pro'), 'window_hours': 168},
    }))
    server = make_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f'http://127.0.0.1:{server.server_port}/api/subscriptions'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def request(url, payload=None, token=None, origin=None):
    headers = {}
    if token is not None:
        headers['X-Quota-Token'] = token
    if origin:
        headers['Origin'] = origin
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def payload(**overrides):
    return dict(provider='grok', plan='', expected=None, monthly_usd=30, regular_allocations=30, included_resets=0, **overrides)


def test_save_unknown_plan_persists_without_changing_other_provider(settings_server):
    server, url = settings_server
    subscriptions.configure('codex', 'pro', 200, 4, 1)
    _, info = request(url)
    assert info['providers']['grok']['monthly_usd'] is None
    code, result = request(url, payload(), info['token'])
    assert code == 200 and result['saved']
    assert server.settings_changed.is_set()
    assert subscriptions.resolve('grok', None)['monthly_usd'] == 30
    assert subscriptions.allocation_value(subscriptions.resolve('codex', 'pro'), 168) == 40
    # A subsequent settings read sees the persisted value, not just browser state.
    assert request(url)[1]['config']['grok']['monthly_usd'] == 30
    assert request(url)[1]['providers']['grok']['monthly_usd'] == 30
    assert request(url, payload(), info['token'])[0] == 409


@pytest.mark.parametrize('change', [
    {'monthly_usd': -1}, {'monthly_usd': float('nan')}, {'monthly_usd': True},
    {'regular_allocations': 0}, {'included_resets': -1}, {'provider': '../bad'},
])
def test_invalid_settings_do_not_write(settings_server, change):
    _, url = settings_server
    body = payload(); body.update(change)
    assert request(url, body, request(url)[1]['token'])[0] == 400
    assert subscriptions.load_config() == {}


def test_requires_live_token_and_same_origin(settings_server):
    _, url = settings_server
    assert request(url, payload())[0] == 403
    assert request(url, payload(), request(url)[1]['token'], 'https://foreign.example')[0] == 403
    assert subscriptions.load_config() == {}


def test_plan_change_is_not_silently_overwritten(settings_server):
    _, url = settings_server
    body = payload(); body['plan'] = 'old-plan'
    assert request(url, body, request(url)[1]['token'])[0] == 409
    assert subscriptions.load_config() == {}


def test_environment_override_is_explicitly_read_only(settings_server, monkeypatch):
    _, url = settings_server
    monkeypatch.setenv(subscriptions.ENV_JSON, '{"providers":{}}')
    _, info = request(url)
    assert info['writable'] is False
    assert request(url, payload(), info['token'])[0] == 400
    assert not subscriptions.config_path().exists()
