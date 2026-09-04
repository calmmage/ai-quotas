"""Join agentic_step jobs.jsonl to spend.jsonl; check thresholds."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ai_quotas.agentic_step import (
    DEFAULT_THRESHOLD_TOKENS,
    DEFAULT_THRESHOLD_USD,
    attribute_jobs,
    evaluate_check,
    load_jobs,
    parse_since,
    report_agentic_step,
    summarize_attribution,
)
from ai_quotas.paths import (
    DEFAULT_AGENTIC_STEP_JOBS,
    ENV_AGENTIC_STEP_JOBS,
    agentic_step_jobs_path,
)
from ai_quotas.spend import load_spend

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agentic_step"
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _report(**kwargs):
    return report_agentic_step(
        jobs_path=FIXTURES / "jobs.jsonl",
        spend=FIXTURES / "spend.jsonl",
        now=NOW,
        **kwargs,
    )


def test_jobs_path_default(monkeypatch):
    monkeypatch.delenv(ENV_AGENTIC_STEP_JOBS, raising=False)
    assert agentic_step_jobs_path() == DEFAULT_AGENTIC_STEP_JOBS


def test_jobs_path_env_and_override(monkeypatch, tmp_path: Path):
    env = tmp_path / "env-jobs.jsonl"
    monkeypatch.setenv(ENV_AGENTIC_STEP_JOBS, str(env))
    assert agentic_step_jobs_path() == env
    explicit = tmp_path / "explicit.jsonl"
    assert agentic_step_jobs_path(explicit) == explicit


def test_parse_since():
    cutoff = parse_since("7d", now=NOW)
    assert (NOW - cutoff).days == 7
    cutoff30 = parse_since("30d", now=NOW)
    assert (NOW - cutoff30).days == 30


def test_join_spend_wins_over_job_usage():
    summary = _report(since="7d")
    by_id = {r["job_id"]: r for r in summary["jobs"]}
    claude = by_id["job-claude-1"]
    assert claude["source"] == "spend"
    # spend has two turns 1250+625; job.usage was only 12 tokens
    assert claude["input_tokens"] == 1500
    assert claude["cached_tokens"] == 300
    assert claude["output_tokens"] == 75
    assert claude["total_tokens"] == 1875
    assert claude["turns"] == 2
    assert claude["cost_usd"] is None
    grok = by_id["job-grok-1"]
    assert grok["source"] == "spend"
    assert grok["total_tokens"] == 9400
    assert grok["cost_usd"] == 0.25


def test_unmatched_uses_job_usage():
    summary = _report(since="7d")
    by_id = {r["job_id"]: r for r in summary["jobs"]}
    orphan = by_id["job-orphan-1"]
    assert orphan["source"] == "job"
    assert orphan["input_tokens"] == 4000
    assert orphan["cached_tokens"] == 200
    assert orphan["output_tokens"] == 800
    assert orphan["cache_write_tokens"] == 50
    assert orphan["total_tokens"] == 4000 + 800 + 200 + 50
    assert any(r["job_id"] == "job-orphan-1" for r in summary["unmatched"])


def test_since_drops_old_job():
    summary = _report(since="7d")
    ids = {r["job_id"] for r in summary["jobs"]}
    assert "job-old-1" not in ids
    assert summary["n_skipped_since"] == 1
    all_time = _report(since=None)
    assert any(r["job_id"] == "job-old-1" for r in all_time["jobs"])


def test_unrelated_spend_session_not_attributed():
    summary = _report(since="7d")
    chats = {r["chat_id"] for r in summary["jobs"]}
    assert "sess-unrelated" not in chats


def test_filter_caller_and_task_slug():
    by_caller = _report(since="7d", caller="chat_tracking")
    assert {r["job_id"] for r in by_caller["jobs"]} == {
        "job-claude-1",
        "job-grok-1",
    }
    by_slug = _report(since="7d", task_slug="caption")
    assert {r["job_id"] for r in by_slug["jobs"]} == {"job-orphan-1"}


def test_totals_per_caller_provider():
    summary = _report(since="7d")
    callers = {r["caller"]: r for r in summary["by_caller"]}
    assert callers["chat_tracking"]["jobs"] == 2
    assert callers["chat_tracking"]["matched"] == 2
    assert callers["instagram_dl"]["unmatched"] == 1
    providers = {r["provider"]: r for r in summary["by_provider"]}
    assert providers["claude"]["jobs"] == 2  # matched + unmatched
    assert providers["grok"]["cost_usd"] == 0.25
    assert summary["totals"]["jobs"] == 3
    assert summary["totals"]["matched"] == 2
    assert summary["totals"]["unmatched"] == 1
    # 1875 + 9400 + 5050
    assert summary["totals"]["total_tokens"] == 1875 + 9400 + 5050


def test_check_ok_under_default_threshold():
    summary = _report(since="7d")
    verdict = evaluate_check(summary)
    assert verdict["verdict"] == "ok"
    assert verdict["total_tokens"] < DEFAULT_THRESHOLD_TOKENS
    assert verdict["threshold_tokens"] == DEFAULT_THRESHOLD_TOKENS
    assert verdict["threshold_usd"] == DEFAULT_THRESHOLD_USD


def test_check_substantial_on_tokens():
    summary = _report(since="7d")
    verdict = evaluate_check(summary, threshold_tokens=100, threshold_usd=999)
    assert verdict["verdict"] == "substantial"
    assert any("tokens" in r for r in verdict["reasons"])


def test_check_substantial_on_usd():
    summary = _report(since="7d")
    verdict = evaluate_check(summary, threshold_tokens=99_999_999, threshold_usd=0.10)
    assert verdict["verdict"] == "substantial"
    assert any("usd" in r for r in verdict["reasons"])


def test_cli_agentic_step_json():
    env = {
        **os.environ,
        "AGENTIC_STEP_JOBS": str(FIXTURES / "jobs.jsonl"),
        "AI_QUOTAS_SPEND": str(FIXTURES / "spend.jsonl"),
        "AI_QUOTAS_NOW": NOW.isoformat(),
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quotas",
            "spend",
            "--agentic-step",
            "--no-harvest",
            "--since",
            "7d",
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["totals"]["matched"] == 2
    assert data["totals"]["unmatched"] == 1
    sources = {r["job_id"]: r["source"] for r in data["jobs"]}
    assert sources["job-claude-1"] == "spend"
    assert sources["job-orphan-1"] == "job"


def test_cli_check_exit_codes():
    env = {
        **os.environ,
        "AGENTIC_STEP_JOBS": str(FIXTURES / "jobs.jsonl"),
        "AI_QUOTAS_SPEND": str(FIXTURES / "spend.jsonl"),
        "AI_QUOTAS_NOW": NOW.isoformat(),
    }
    ok = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quotas",
            "agentic-step-check",
            "--since",
            "7d",
            "--threshold-tokens",
            "99999999",
            "--threshold-usd",
            "999",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=30,
        check=False,
    )
    assert ok.returncode == 0, ok.stderr
    payload = json.loads(ok.stdout)
    assert payload["verdict"] == "ok"

    hot = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quotas",
            "agentic-step-check",
            "--since",
            "7d",
            "--threshold-tokens",
            "100",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=30,
        check=False,
    )
    assert hot.returncode == 1, hot.stderr
    hot_payload = json.loads(hot.stdout)
    assert hot_payload["verdict"] == "substantial"


def test_load_jobs_skips_bad_lines(tmp_path: Path):
    p = tmp_path / "jobs.jsonl"
    p.write_text("not json\n{\"job_id\": \"ok\", \"provider\": \"claude\"}\n", encoding="utf-8")
    rows = load_jobs(p)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "ok"


def test_attribute_empty_files(tmp_path: Path):
    jobs = tmp_path / "jobs.jsonl"
    spend = tmp_path / "spend.jsonl"
    jobs.write_text("", encoding="utf-8")
    spend.write_text("", encoding="utf-8")
    attributed = attribute_jobs(load_jobs(jobs), load_spend(spend), since="7d", now=NOW)
    summary = summarize_attribution(attributed)
    assert summary["totals"]["jobs"] == 0
    verdict = evaluate_check(summary)
    assert verdict["verdict"] == "ok"
