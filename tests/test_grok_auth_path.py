"""Configured CLI homes must work without copying authentication files."""
import json
from pathlib import Path

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
