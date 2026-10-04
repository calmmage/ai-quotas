"""Path resolution — single module for reader and writer."""

from __future__ import annotations

from pathlib import Path

from ai_quotas.paths import (
    DEFAULT_AGENTIC_STEP_JOBS,
    DEFAULT_DATABASE_NAME,
    DEFAULT_DATA_DIR,
    ENV_AGENTIC_STEP_JOBS,
    ENV_DATABASE,
    ENV_DATA_DIR,
    ENV_EXTRA_ADAPTERS,
    ENV_SAMPLES,
    ENV_SPEND,
    agentic_step_jobs_path,
    data_dir,
    database_path,
    doctor_report,
    samples_path,
    spend_path,
)


def test_default_database_path(monkeypatch):
    monkeypatch.delenv(ENV_SAMPLES, raising=False)
    monkeypatch.delenv(ENV_DATABASE, raising=False)
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    assert database_path() == DEFAULT_DATA_DIR / DEFAULT_DATABASE_NAME
    assert samples_path() == DEFAULT_DATA_DIR / DEFAULT_DATABASE_NAME


def test_env_samples_file_wins(monkeypatch, tmp_path: Path):
    target = tmp_path / "custom.jsonl"
    monkeypatch.setenv(ENV_SAMPLES, str(target))
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "other"))
    assert samples_path() == target


def test_env_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(ENV_SAMPLES, raising=False)
    monkeypatch.delenv(ENV_DATABASE, raising=False)
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "quota-data"))
    assert data_dir() == tmp_path / "quota-data"
    assert samples_path() == tmp_path / "quota-data" / DEFAULT_DATABASE_NAME


def test_env_database_wins(monkeypatch, tmp_path: Path):
    target = tmp_path / "custom.sqlite3"
    monkeypatch.setenv(ENV_DATABASE, str(target))
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "other"))
    assert database_path() == target
    assert samples_path() == target


def test_explicit_override_wins(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(ENV_SAMPLES, str(tmp_path / "env.jsonl"))
    explicit = tmp_path / "explicit.jsonl"
    assert samples_path(explicit) == explicit


def test_spend_is_sibling_of_samples(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(ENV_SPEND, raising=False)
    samples = tmp_path / "quota" / "samples.jsonl"
    monkeypatch.setenv(ENV_SAMPLES, str(samples))
    assert spend_path() == tmp_path / "quota" / "spend.jsonl"


def test_env_spend_wins(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(ENV_SPEND, str(tmp_path / "custom-spend.jsonl"))
    monkeypatch.setenv(ENV_SAMPLES, str(tmp_path / "samples.jsonl"))
    assert spend_path() == tmp_path / "custom-spend.jsonl"


def test_agentic_step_jobs_default(monkeypatch):
    monkeypatch.delenv(ENV_AGENTIC_STEP_JOBS, raising=False)
    assert agentic_step_jobs_path() == DEFAULT_AGENTIC_STEP_JOBS


def test_agentic_step_jobs_env(monkeypatch, tmp_path: Path):
    target = tmp_path / "jobs.jsonl"
    monkeypatch.setenv(ENV_AGENTIC_STEP_JOBS, str(target))
    assert agentic_step_jobs_path() == target


def test_doctor_report_shows_unset_and_set_env(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(ENV_SAMPLES, raising=False)
    monkeypatch.delenv(ENV_DATABASE, raising=False)
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    monkeypatch.delenv(ENV_SPEND, raising=False)
    monkeypatch.delenv(ENV_EXTRA_ADAPTERS, raising=False)
    monkeypatch.delenv(ENV_AGENTIC_STEP_JOBS, raising=False)
    text = doctor_report()
    assert f"{ENV_DATABASE}=(unset)" in text
    assert "resolved:" in text
    assert "database" in text
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "q"))
    text2 = doctor_report()
    assert str(tmp_path / "q") in text2
    assert f"{ENV_DATA_DIR}=" in text2
