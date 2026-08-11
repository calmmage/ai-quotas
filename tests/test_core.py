"""Core math: metrics, burn/need, noise guards, verdicts, history."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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


def test_evaluate_and_exit_code(multi_samples):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    result = core.evaluate(multi_samples, now=now)
    assert "verdicts" in result
    assert "claude" in result["verdicts"]
    assert result["verdicts"]["claude"]["window"] == "week"
    # grok prefers month
    assert result["verdicts"]["grok"]["window"] == "month"
    code = core.exit_code(result)
    assert code in (0, 1, 2)


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
