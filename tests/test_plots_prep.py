"""Unit tests for plot prep: reset detection + money classification (shipped code)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

# pandas is a plot optional dep — skip whole module if missing
pytest.importorskip("pandas")

from ai_quotas.plots.prep import (  # noqa: E402
    classify_money,
    is_reset,
    money_summary,
    prepare,
    ResetEvent,
)
from ai_quotas.paths import samples_path  # noqa: E402


def test_is_reset_to_zero_from_real_use():
    assert is_reset(40.0, 0.5) is True


def test_is_reset_significant_absolute_drop():
    assert is_reset(50.0, 40.0) is True  # 10pp >= SIG_ABS 5


def test_is_reset_ignores_1pp_noise():
    assert is_reset(10.0, 9.0) is False


def test_is_reset_ignores_quantization_to_zero_from_tiny():
    # prior < TO_ZERO_MIN_PRIOR (3) and small drop
    assert is_reset(1.0, 0.0) is False


def test_is_reset_relative_drop():
    # 20% relative drop from 20 → 15 is 25% relative, prior >= 3
    assert is_reset(20.0, 15.0) is True


def test_classify_money_first_reset_is_burn():
    kind, usd, win, hours, label = classify_money(
        "Codex week", "Codex", remaining_before=50.0,
        period_since_last_burn=None, is_first_reset=True,
    )
    assert kind == "burn"
    assert usd < 0
    assert label.startswith("−$") or label == "$0"
    assert hours == 7 * 24
    assert win > 0


def test_classify_money_early_reset_is_free():
    kind, usd, win, hours, label = classify_money(
        "Codex week", "Codex", remaining_before=90.0,
        period_since_last_burn=timedelta(hours=12), is_first_reset=False,
    )
    assert kind == "free"
    assert usd > 0
    assert label.startswith("+$")


def test_classify_money_after_full_window_is_burn():
    kind, usd, *_ = classify_money(
        "Codex week", "Codex", remaining_before=10.0,
        period_since_last_burn=timedelta(days=8), is_first_reset=False,
    )
    assert kind == "burn"
    assert usd < 0


def test_classify_money_5h_unpriced():
    kind, usd, win, hours, label = classify_money(
        "Claude 5h", "Claude", remaining_before=50.0,
        period_since_last_burn=None, is_first_reset=True,
    )
    assert kind == "reset"
    assert usd == 0.0
    assert label == ""
    assert hours == 5


def test_money_summary_excludes_unpriced():
    events = [
        ResetEvent(
            series="Claude 5h", vendor="Claude",
            at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            used_before=50, used_after=0, remaining_before=50, remaining_after=100,
            period_before=None, label="reset · first", kind="reset",
            money_usd=0.0, window_usd=1.0, expected_hours=5.0, money_label="",
        ),
        ResetEvent(
            series="Codex week", vendor="Codex",
            at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            used_before=50, used_after=0, remaining_before=50, remaining_after=100,
            period_before=None, label="−$20 · first", kind="burn",
            money_usd=-20.0, window_usd=46.0, expected_hours=168.0, money_label="−$20",
        ),
    ]
    s = money_summary(events)
    assert s["Claude"]["events"] == 0
    assert s["Codex"]["burn"] == 20.0
    assert s["TOTAL"]["events"] == 1


def test_prepare_fixture_samples():
    fixture = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
    assert fixture.is_file()
    df, resets, cutoff = prepare(fixture)
    assert len(df) > 0
    assert "remaining_percent" in df.columns
    assert cutoff is not None
    # remaining = 100 - used when not nan
    sample = df.dropna(subset=["used_percent"]).iloc[0]
    assert abs(sample["remaining_percent"] + sample["used_percent"] - 100.0) < 1e-6


def test_samples_path_override_used_by_prepare(tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
    # ensure env does not steal resolution when override is passed
    monkeypatch.delenv("AI_QUOTAS_SAMPLES", raising=False)
    df, resets, cutoff = prepare(fixture)
    assert len(df) > 0
    # path resolution itself
    assert samples_path(fixture) == fixture.expanduser()
