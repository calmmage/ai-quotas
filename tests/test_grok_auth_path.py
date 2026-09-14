"""Configured CLI homes must work without copying authentication files."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ai_quotas.adapters import grok


def test_explicit_auth_file_overrides_cli_home(tmp_path, monkeypatch):
    explicit = tmp_path / 'auth.json'
    explicit.write_text(json.dumps({'issuer': {'key': 'fixture-only'}}))
    monkeypatch.setenv('AI_QUOTAS_GROK_AUTH_FILE', str(explicit))
    monkeypatch.setenv('GROK_HOME', str(tmp_path / 'other'))
    assert grok.auth_path() == explicit
    assert grok._load_auth_entry()['key'] == 'fixture-only'


def test_cli_home_is_used_when_auth_file_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv('AI_QUOTAS_GROK_AUTH_FILE', raising=False)
    monkeypatch.setenv('AI_QUOTAS_READ_DOTENV', '0')
    monkeypatch.setenv('GROK_HOME', str(tmp_path))
    assert grok.auth_path() == tmp_path / 'auth.json'


def test_default_path_is_preserved(tmp_path, monkeypatch):
    for name in ('AI_QUOTAS_GROK_AUTH_FILE', 'GROK_HOME'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AI_QUOTAS_READ_DOTENV', '0')
    monkeypatch.setattr(grok, 'AUTH_PATH', tmp_path / 'default.json')
    assert grok.auth_path() == tmp_path / 'default.json'


def test_missing_explicit_path_never_falls_back_to_another_account(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_QUOTAS_GROK_AUTH_FILE', str(tmp_path / 'missing.json'))
    monkeypatch.setattr(grok, '_get_access_token', lambda: (_ for _ in ()).throw(AssertionError('must not load a different login')))
    rows = grok.snapshot('2026-09-12T12:00:00Z')
    assert rows[0]['status'] == 'unavailable'
    assert str(tmp_path / 'missing.json') in rows[0]['reason']


def _entry(user, *, expired, key='old-key'):
    exp = datetime.now(timezone.utc) + (timedelta(hours=-2) if expired else timedelta(hours=4))
    return {
        'https://auth.x.ai::client': {
            'key': key,
            'refresh_token': 'refresh-old',
            'oidc_client_id': 'client',
            'expires_at': exp.isoformat().replace('+00:00', 'Z'),
            'user_id': user,
            'principal_id': user,
            'email': f'{user}@example.com',
        }
    }


def test_refresh_is_persisted_into_the_configured_file(tmp_path, monkeypatch):
    path = tmp_path / 'auth.json'
    path.write_text(json.dumps(_entry('user-a', expired=True)))
    monkeypatch.setenv('AI_QUOTAS_GROK_AUTH_FILE', str(path))
    monkeypatch.setattr(grok, 'LIVE_CLI_AUTH', tmp_path / 'missing-live.json')

    class _Resp:
        def read(self):
            return json.dumps({
                'access_token': 'new-key',
                'refresh_token': 'refresh-new',
                'expires_in': 3600,
            }).encode()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    with patch('urllib.request.urlopen', return_value=_Resp()):
        token = grok._get_access_token()
    assert token == 'new-key'
    saved = json.loads(path.read_text())
    entry = next(iter(saved.values()))
    assert entry['key'] == 'new-key'
    assert entry['refresh_token'] == 'refresh-new'


def test_stale_same_account_copy_is_healed_from_live_cli(tmp_path, monkeypatch):
    configured = tmp_path / 'kit' / 'auth.json'
    live = tmp_path / 'live' / 'auth.json'
    configured.parent.mkdir()
    live.parent.mkdir()
    configured.write_text(json.dumps(_entry('user-a', expired=True, key='stale-key')))
    live.write_text(json.dumps(_entry('user-a', expired=False, key='live-key')))
    monkeypatch.setenv('AI_QUOTAS_GROK_AUTH_FILE', str(configured))
    monkeypatch.setattr(grok, 'LIVE_CLI_AUTH', live)

    def _boom(_entry):
        raise RuntimeError('OIDC refresh HTTP 400: invalid_grant')

    monkeypatch.setattr(grok, '_refresh_access_token', _boom)
    token = grok._get_access_token()
    assert token == 'live-key'
    saved = json.loads(configured.read_text())
    assert next(iter(saved.values()))['key'] == 'live-key'


def test_different_account_is_never_copied(tmp_path, monkeypatch):
    configured = tmp_path / 'kit' / 'auth.json'
    live = tmp_path / 'live' / 'auth.json'
    configured.parent.mkdir()
    live.parent.mkdir()
    configured.write_text(json.dumps(_entry('user-a', expired=True, key='stale-key')))
    live.write_text(json.dumps(_entry('user-b', expired=False, key='other-key')))
    monkeypatch.setenv('AI_QUOTAS_GROK_AUTH_FILE', str(configured))
    monkeypatch.setattr(grok, 'LIVE_CLI_AUTH', live)

    def _boom(_entry):
        raise RuntimeError('OIDC refresh HTTP 400: invalid_grant')

    monkeypatch.setattr(grok, '_refresh_access_token', _boom)
    try:
        grok._get_access_token()
        raise AssertionError('should not succeed with a foreign account')
    except RuntimeError as exc:
        assert 'invalid_grant' in str(exc)
        assert 'grok login' in str(exc)
    assert json.loads(configured.read_text())['https://auth.x.ai::client']['key'] == 'stale-key'
