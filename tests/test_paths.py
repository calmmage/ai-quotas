"""Path resolution — single module for reader and writer."""

from __future__ import annotations

from pathlib import Path

from ai_quotas.paths import (
    DEFAULT_DATA_DIR,
    ENV_DATA_DIR,
    ENV_SAMPLES,
    data_dir,
    samples_path,
)


def test_default_samples_path(monkeypatch):
    monkeypatch.delenv(ENV_SAMPLES, raising=False)
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    assert samples_path() == DEFAULT_DATA_DIR / "samples.jsonl"


def test_env_samples_file_wins(monkeypatch, tmp_path: Path):
    target = tmp_path / "custom.jsonl"
    monkeypatch.setenv(ENV_SAMPLES, str(target))
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "other"))
    assert samples_path() == target


def test_env_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(ENV_SAMPLES, raising=False)
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "quota-data"))
    assert data_dir() == tmp_path / "quota-data"
    assert samples_path() == tmp_path / "quota-data" / "samples.jsonl"


def test_explicit_override_wins(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(ENV_SAMPLES, str(tmp_path / "env.jsonl"))
    explicit = tmp_path / "explicit.jsonl"
    assert samples_path(explicit) == explicit
