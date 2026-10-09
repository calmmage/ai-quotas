"""Core math: metrics, burn/need, noise guards, verdicts, history."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_quotas import core


def _load(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def test_window_hours_week_and_5h():
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    assert core.window_hours("week", now) == 168.0
    assert core.window_hours("week_fable", now) == 168.0
    assert core.window_hours("5h", now) == 5.0
    month_h = core.window_hours("month", now)
    assert month_h == 31 * 24  # July


def test_metrics_need_avg_and_need_rem(multi_samples):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    row = next(
        r
        for r in multi_samples
        if r["provider"] == "claude" and r["window"] == "week"
    )
    m = core.metrics_for_row(multi_samples, row, now)
    assert m["window_hours"] == 168.0
    assert m["used_percent"] == 26.0
    # need avg = 100/168 %/h
    bn = core.burn_metrics(row, m)
    assert bn["need_avg"] is not None
    assert abs(bn["need_avg"] - 100.0 / 168.0) < 1e-9
    assert bn["need_rem"] is not None
    assert bn["need_rem"] > 0
    assert bn["unit"] == "%/d"
    assert bn["scale"] == 24.0


def test_trend_24h_from_series(fixtures_dir: Path):
    samples = _load(fixtures_dir / "trend_series.jsonl")
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    current = samples[-1]
    trend, basis = core.trend_from_samples(
        samples, "claude", "week", current, now
    )
    assert trend is not None
    # 22 - 10 over 12h = 1.0 %/h
    assert abs(trend - 1.0) < 1e-9
    assert basis["interval_hours"] == 12.0
    assert basis["quantized"] is False


def test_quantized_noise_guard(fixtures_dir: Path):
    samples = _load(fixtures_dir / "quantized_noise.jsonl")
    now = datetime(2026, 7, 28, 11, 0, tzinfo=timezone.utc)
    current = samples[-1]
    trend, basis = core.trend_from_samples(
        samples, "claude", "week", current, now
    )
    # Δ=1 within <2h → null burn
    assert trend is None
    assert basis["quantized"] is True


def _reset_sample(ts, used, reset):
    return {"provider": "claude", "window": "week", "status": "ok",
            "ts": ts, "used_percent": used, "resets_at": reset}


@pytest.mark.parametrize("baseline_reset", [
    "2026-10-15T08:00:00Z",
    "2026-10-15T07:59:59.997223+00:00",
    "2026-10-15T07:59:00+00:00",
    "2026-10-15T11:00:00+03:00",
    "2026-10-15T08:00:00",  # legacy naive timestamps mean UTC
])
def test_burn_uses_current_cycle_despite_reset_timestamp_rounding(baseline_reset):
    old = _reset_sample("2026-10-08T04:00:00Z", 54, "2026-10-08T08:00:00Z")
    baseline = _reset_sample("2026-10-08T12:00:00Z", 3, baseline_reset)
    current = _reset_sample("2026-10-09T00:00:00Z", 15, "2026-10-15T08:00:00.422356Z")
    metrics = core.metrics_for_row([old, baseline, current], current, core.parse_ts(current["ts"]))
    assert metrics["burn_per_hour"] == pytest.approx(1.0)
    assert metrics["basis"]["baseline_ts"] == baseline["ts"]
    assert metrics["projected_final"] > current["used_percent"]


@pytest.mark.parametrize("recent_minutes, used", [(20, 4), (60, 1)])
def test_old_cycle_cannot_supply_interval_for_insufficient_new_cycle(recent_minutes, used):
    from datetime import timedelta
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    reset = "2026-10-15T08:00:00Z"
    old = _reset_sample("2026-10-08T04:00:00Z", 54, "2026-10-08T08:00:00Z")
    recent = _reset_sample((now - timedelta(minutes=recent_minutes)).isoformat(), 0, reset)
    current = _reset_sample(now.isoformat(), used, reset)
    metrics = core.metrics_for_row([old, recent, current], current, now)
    assert metrics["burn_per_hour"] is None
    assert metrics["projected_final"] is None
    assert metrics["basis"]["baseline_ts"] == recent["ts"]
    assert metrics["basis"]["quantized"] is (recent_minutes == 60)


@pytest.mark.parametrize("baseline_reset, current_reset", [
    ("2026-10-08T08:00:00Z", "2026-10-15T08:00:00Z"),
    (None, "2026-10-15T08:00:00Z"),
    ("2026-10-15T08:00:00Z", None),
])
def test_burn_without_comparable_cycle_is_unknown(baseline_reset, current_reset):
    baseline = _reset_sample("2026-10-08T12:00:00Z", 54, baseline_reset)
    current = _reset_sample("2026-10-09T00:00:00Z", 15, current_reset)
    rate, basis = core.trend_from_samples([baseline, current], "claude", "week", current,
                                         core.parse_ts(current["ts"]))
    assert rate is None
    assert basis["baseline_ts"] is None


def test_burn_preserves_series_without_reset_metadata():
    baseline = _reset_sample("2026-10-08T12:00:00Z", 3, None)
    current = _reset_sample("2026-10-09T00:00:00Z", 15, None)
    rate, _ = core.trend_from_samples([baseline, current], "claude", "week", current,
                                     core.parse_ts(current["ts"]))
    assert rate == pytest.approx(1.0)


def test_null_burn_never_stop_from_projection():
    # High used but null burn → no projection STOP
    v = core.verdict_for(
        used_percent=50.0,
        burn_per_hour=None,
        projected_final=None,
        hours_left=100.0,
        runway_hours_24h=None,
    )
    assert v == "OK"

    # Projection STOP only when burn measurable
    v2 = core.verdict_for(
        used_percent=50.0,
        burn_per_hour=2.0,
        projected_final=150.0,
        hours_left=50.0,
        runway_hours_24h=25.0,
    )
    assert v2 == "STOP"


def test_stop_from_used_with_time_left():
    v = core.verdict_for(
        used_percent=90.0,
        burn_per_hour=None,
        projected_final=None,
        hours_left=48.0,
    )
    assert v == "STOP"


def test_warn_threshold():
    v = core.verdict_for(
        used_percent=65.0,
        burn_per_hour=None,
        projected_final=None,
        hours_left=72.0,
    )
    assert v == "WARN"


def test_pick_harness_skips_stop_and_warn_prefers_grok():
    result = {
        "ts": "2026-09-16T19:00:00+03:00",
        "windows": [
            {"provider": "codex", "window": "week", "verdict": "STOP", "used_percent": 100.0},
            {"provider": "codex", "window": "5h", "verdict": "OK", "used_percent": 0.0},
            {"provider": "claude", "window": "week", "verdict": "OK", "used_percent": 57.0},
            {"provider": "claude", "window": "week_fable", "verdict": "STOP", "used_percent": 99.0},
            {"provider": "grok", "window": "week", "verdict": "OK", "used_percent": 51.0},
            {"provider": "grok", "window": "month", "verdict": "OK", "used_percent": 10.7},
        ],
    }
    picked = core.pick_harness(result)
    assert picked["harness"] == "grok"
    skipped = {row["harness"]: row["verdict"] for row in picked["skipped"]}
    assert skipped["codex"] == "STOP"
    assert skipped["claude"] == "STOP"  # week_fable, not the green 5h/week
    prefer_codex = core.pick_harness(result, prefer="codex")
    assert prefer_codex["harness"] == "grok"  # prefer is ignored when STOP


def test_pick_harness_prefer_when_ok():
    result = {
        "windows": [
            {"provider": "grok", "window": "week", "verdict": "OK", "used_percent": 10.0},
            {"provider": "claude", "window": "week", "verdict": "OK", "used_percent": 20.0},
            {"provider": "codex", "window": "week", "verdict": "OK", "used_percent": 30.0},
        ]
    }
    assert core.pick_harness(result)["harness"] == "grok"
    assert core.pick_harness(result, prefer="codex")["harness"] == "codex"


def test_pick_harness_none_when_all_blocked():
    result = {
        "windows": [
            {"provider": "grok", "window": "week", "verdict": "WARN", "used_percent": 65.0},
            {"provider": "claude", "window": "week", "verdict": "STOP", "used_percent": 90.0},
            {"provider": "codex", "window": "week", "verdict": "STOP", "used_percent": 100.0},
        ]
    }
    picked = core.pick_harness(result)
    assert picked["harness"] is None
    assert len(picked["skipped"]) == 3


def test_evaluate_and_exit_code(multi_samples):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    result = core.evaluate(multi_samples, now=now)
    assert "verdicts" in result
    assert "claude" in result["verdicts"]
    assert result["verdicts"]["claude"]["window"] == "week"
    # grok month is retired; verdict follows the weekly window
    assert result["verdicts"]["grok"]["window"] != "month"
    code = core.exit_code(result)
    assert code in (0, 1, 2)


def test_retired_grok_month_is_dropped_on_load(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    rows = [
        {"ts": "2026-09-29T12:00:00+03:00", "provider": "grok", "window": w,
         "used_percent": 0.0, "resets_at": "2026-10-01T00:00:00+00:00", "status": "ok"}
        for w in ("month", "week")
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert [r["window"] for r in core.load_samples(path)] == ["week"]


def test_history_sparse_flagging(fixtures_dir: Path):
    samples = _load(fixtures_dir / "trend_series.jsonl")
    hist = core.history_from_samples(samples, sparse_below=5)
    assert len(hist["periods"]) == 1
    p = hist["periods"][0]
    assert p["samples_n"] == 3
    assert p["sparse"] is True
    assert p["peak_used_percent"] == 22.0


def test_latest_by_key(multi_samples):
    by = core.latest_by_key(multi_samples)
    assert ("claude", "week") in by
    assert by[("claude", "week")]["used_percent"] == 26.0


def test_load_samples_path(multi_path: Path):
    rows = core.load_samples(multi_path)
    assert len(rows) >= 5
    assert all("provider" in r for r in rows)


def test_genuine_zero_ok():
    """status=ok with used_percent=0 is legitimate — not a failure fabrication."""
    row = {
        "ts": "2026-07-28T12:00:00+00:00",
        "provider": "claude",
        "window": "5h",
        "used_percent": 0.0,
        "resets_at": "2026-07-28T17:00:00+00:00",
        "status": "ok",
    }
    assert core.ok_numeric(row) is True
