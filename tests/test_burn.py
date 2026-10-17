"""Quota burner dry-run and the opt-in tier map. Default pick stays the same."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ai_quotas import core
from ai_quotas.burn import assign_runs, format_log, run_dry
from ai_quotas.tiers import EXTRA_PICK_HARNESSES, candidates_for


def _window(provider, window, used, verdict="OK"):
    return {
        "provider": provider,
        "window": window,
        "verdict": verdict,
        "used_percent": used,
    }


def _result():
    return {
        "ts": "2026-10-17T04:00:00+03:00",
        "verdicts": {},
        "windows": [
            _window("codex", "week", 10.0),
            _window("agy", "week_gemini_pro", 40.0),
            _window("agy", "week_gemini_flash", 5.0),
            _window("agy", "5h_gemini_flash", 0.0),
            _window("claude", "week", 30.0),
            _window("claude", "week_fable", 99.0, "STOP"),
            _window("muse", "week", 15.0),
            _window("grok", "week", 50.0),
        ],
    }


def test_default_pick_ignores_the_new_harnesses():
    result = _result()
    picked = core.pick_harness(result)
    assert picked["harness"] == "codex"  # 10% used, lower than grok 50% and claude 30%
    assert "muse" not in {row["harness"] for row in picked["ok"]}


def test_pick_harness_still_skips_warn_and_prefers_named_default():
    """The September callers pass grok,claude,codex and a prefer."""
    result = {
        "windows": [
            _window("grok", "week", 10.0),
            _window("claude", "week", 20.0),
            _window("codex", "week", 30.0),
        ]
    }
    assert core.pick_harness(result)["harness"] == "grok"
    assert core.pick_harness(result, prefer="codex")["harness"] == "codex"


def test_gemini_flash_does_not_read_the_pro_bucket():
    result = _result()
    stopped = dict(result)
    stopped["windows"] = [
        _window("agy", "week_gemini_pro", 95.0, "STOP"),
        _window("agy", "week_gemini_flash", 5.0),
        _window("agy", "5h_gemini_flash", 0.0),
    ]
    assert core.pick_harness(stopped, candidates=("gemini-argon",))["harness"] is None
    assert core.pick_harness(stopped, candidates=("gemini-flash",))["harness"] == "gemini-flash"
    assert core.pick_harness(stopped, candidates=("agy",))["harness"] is None


def test_opus_is_not_blocked_by_a_fable_stop():
    result = _result()
    assert core.pick_harness(result, candidates=("claude",))["harness"] is None
    assert core.pick_harness(result, candidates=("claude-opus",))["harness"] == "claude-opus"
    assert core.pick_harness(result, candidates=("claude-fable",))["harness"] is None


def test_smart_tier_omits_fable_unless_planning():
    assert "claude-fable" not in candidates_for("smart")
    assert candidates_for("smart", role="plan") == ("claude-fable",)
    assert set(EXTRA_PICK_HARNESSES) >= {"muse", "agy", "gemini-flash", "gemini-argon"}


def test_burn_proposes_rows_and_never_assigns_fable_to_build():
    items = [
        {"id": "vision-board", "title": "Vision board", "kind": "stalled", "tier": "smart", "quote": "stalled"},
        {"id": "v1", "title": "Partial", "kind": "vision", "tier": "cheap", "quote": "not live"},
    ]
    runs = assign_runs(_result(), items)
    assert len(runs) == 2
    smart, cheap = runs
    assert smart["tier"] == "smart" and smart["harness"] == "codex"
    assert smart["harness"] != "claude-fable"
    assert smart["status"] == "proposed" and smart["ran"] is False
    assert cheap["tier"] == "cheap" and cheap["harness"] == "gemini-flash"
    assert cheap["spare_percent"] == 95.0
    assert "proposed" in smart["status"]
    text = format_log({
        "source": "given",
        "queue": "given",
        "spare": [{"provider": "codex", "window": "week", "used_percent": 10, "spare_percent": 90, "verdict": "OK"}],
        "runs": runs,
        "candidates": 2,
        "selected": 2,
    })
    assert "dispatch_calls=0" in text
    assert "status=proposed, not run" in text
    assert "tier=smart" in text
    assert "Vision board" in text


def test_middle_grok_when_the_tier_is_blocked():
    result = {
        "windows": [
            _window("codex", "week", 99.0, "STOP"),
            _window("agy", "week_gemini_pro", 99.0, "STOP"),
            _window("claude", "week", 99.0, "STOP"),
            _window("grok", "week", 20.0),
        ]
    }
    runs = assign_runs(result, [
        {"id": "vision-board", "title": "Vision board", "kind": "stalled", "tier": "smart", "quote": ""},
    ])
    assert runs[0]["harness"] == "grok"
    assert runs[0]["tier"] == "middle"
    assert "Grok in the middle" in runs[0]["reason"]
    assert "Fable NEVER builds" in runs[0]["reason"]


def test_run_dry_reads_jsonl_and_counts_zero_launches(tmp_path: Path):
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    samples = [
        {
            "ts": "2026-10-17T10:00:00+00:00",
            "provider": "grok",
            "window": "week",
            "used_percent": 12.0,
            "resets_at": "2026-10-20T12:00:00+00:00",
            "plan": None,
            "status": "ok",
        }
    ]
    path = tmp_path / "samples.jsonl"
    path.write_text(json.dumps(samples[0]) + "\n", encoding="utf-8")
    report = run_dry(path, now=now, items=[
        {"id": "instagram", "title": "instagram", "kind": "stalled", "tier": "smart", "quote": "stalled"},
    ])
    assert report["dispatch_calls"] == 0
    assert report["dry_run"] is True
    assert report["source"] == "jsonl"
    assert report["runs"][0]["harness"] == "grok"
    assert report["runs"][0]["tier"] == "middle"
    grok = next(row for row in report["spare"] if row["provider"] == "grok" and row["window"] == "week")
    assert grok["spare_percent"] == 88.0


def test_burn_source_has_no_launch_path():
    text = Path(__file__).resolve().parents[1].joinpath("ai_quotas", "burn.py").read_text(encoding="utf-8")
    for banned in ("subprocess", "os.system", "Popen", "sample_now", "codex exec"):
        assert banned not in text
