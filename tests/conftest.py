"""Shared fixtures for offline ai-quotas tests. No network, no real vendor dirs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def isolate_subscription_config(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AI_QUOTAS_SUBSCRIPTIONS", str(tmp_path / "subscriptions.json"))
    monkeypatch.delenv("AI_QUOTAS_SUBSCRIPTIONS_JSON", raising=False)


@pytest.fixture(autouse=True)
def isolate_accounts(tmp_path: Path, monkeypatch):
    """No real extra logins (Orca account dirs), pins, or hostname in tests."""
    import os

    for name in list(os.environ):
        if name.startswith("AI_QUOTAS_ACCOUNT_"):
            monkeypatch.delenv(name, raising=False)
    for name in ("AI_QUOTAS_CODEX_HOMES", "AI_QUOTAS_CLAUDE_HOMES", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_QUOTAS_READ_DOTENV", "0")
    monkeypatch.setenv("AI_QUOTAS_DEVICE", "example-mac")
    from ai_quotas.adapters import claude, codex

    monkeypatch.setattr(codex, "ORCA_CODEX_ACCOUNTS", tmp_path / "no-orca-codex")
    monkeypatch.setattr(claude, "ORCA_CLAUDE_ACCOUNTS", tmp_path / "no-orca-claude", raising=False)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def multi_samples(fixtures_dir: Path) -> list[dict]:
    path = fixtures_dir / "multi.jsonl"
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


@pytest.fixture
def multi_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "multi.jsonl"


@pytest.fixture
def tmp_samples(tmp_path: Path) -> Path:
    p = tmp_path / "samples.jsonl"
    p.touch()
    return p
