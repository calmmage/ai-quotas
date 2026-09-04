"""Unit tests for plot prep: reset detection + money classification (shipped code)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

# pandas is a plot optional dep — skip whole module if missing
pytest.importorskip("pandas")

from ai_quotas.plots.prep import (  # noqa: E402
    RESET_ANNOTATE,
    annotates_reset,
    classify_money,
    daily_spend_for_vendor,
    fmt_tokens,
    is_reset,
    is_session_series,
    money_summary,
    prepare,
    tokens_per_percent,
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


def test_annotates_reset_skips_5h_session_windows():
    assert annotates_reset("Claude 5h") is False
    assert annotates_reset("Gemini Flash 5h") is False
    assert annotates_reset("Gemini Pro 5h") is False
    assert annotates_reset("Claude week") is True
    assert annotates_reset("Claude Fable") is True
    assert annotates_reset("Codex week") is True
    assert "Claude 5h" not in RESET_ANNOTATE
    assert "Claude week" in RESET_ANNOTATE


def test_prepare_does_not_annotate_5h_resets(tmp_path):
    import json

    def row(ts: str, window: str, used: float) -> str:
        return json.dumps(
            {
                "ts": ts,
                "provider": "claude",
                "window": window,
                "used_percent": used,
                "resets_at": None,
                "plan": None,
                "status": "ok",
                "reason": None,
                "limit": None,
                "used": None,
            }
        )

    samples = tmp_path / "samples.jsonl"
    samples.write_text(
        "\n".join(
            [
                row("2026-08-10T10:00:00+03:00", "5h", 40.0),
                row("2026-08-10T10:30:00+03:00", "5h", 0.5),
                row("2026-08-10T10:00:00+03:00", "week", 40.0),
                row("2026-08-10T10:30:00+03:00", "week", 0.5),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _df, resets, _cutoff = prepare(samples)
    series = {r.series for r in resets}
    assert "Claude 5h" not in series
    assert "Claude week" in series


def test_prepare_detects_weekly_reset_across_overnight_gap(tmp_path):
    """Remaining 63%→100% after a 9h hole is still a weekly reset.

    A sampling gap can hide extra burn; it cannot invent leftover quota.
    The live Claude week / Grok week refills on 13 Aug were dropped by
    the old MAX_SAMPLE_GAP skip.
    """
    import json

    def row(ts: str, provider: str, window: str, used: float) -> str:
        return json.dumps(
            {
                "ts": ts,
                "provider": provider,
                "window": window,
                "used_percent": used,
                "resets_at": None,
                "plan": None,
                "status": "ok",
                "reason": None,
                "limit": None,
                "used": None,
            }
        )

    samples = tmp_path / "samples.jsonl"
    samples.write_text(
        "\n".join(
            [
                row("2026-08-13T08:34:00+03:00", "claude", "week", 37.0),
                row("2026-08-13T18:15:00+03:00", "claude", "week", 0.0),
                row("2026-08-13T05:03:00+03:00", "grok", "week", 35.0),
                row("2026-08-13T20:46:00+03:00", "grok", "week", 2.0),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _df, resets, _cutoff = prepare(samples)
    by_series = {r.series: r for r in resets}
    assert "Claude week" in by_series
    assert by_series["Claude week"].remaining_before == 63.0
    assert by_series["Claude week"].remaining_after == 100.0
    assert "Grok week" in by_series
    assert by_series["Grok week"].remaining_before == 65.0
    assert by_series["Grok week"].remaining_after == 98.0


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


def test_session_series_and_fmt_tokens():
    assert is_session_series("Claude 5h") is True
    assert is_session_series("Claude week") is False
    assert fmt_tokens(None) == ""
    assert fmt_tokens(0) == ""
    assert "k tok" in fmt_tokens(12_000)
    assert "M tok" in fmt_tokens(2_400_000)


def test_daily_spend_for_vendor_buckets_local_days():
    from datetime import datetime, timezone

    rows = [
        {
            "provider": "claude",
            "ts": "2026-08-18T10:00:00+00:00",
            "total_tokens": 1000,
            "cost_usd": None,
        },
        {
            "provider": "claude",
            "ts": "2026-08-18T22:00:00+00:00",
            "total_tokens": 500,
            "cost_usd": None,
        },
        {
            "provider": "grok",
            "ts": "2026-08-18T10:00:00+00:00",
            "total_tokens": 99,
            "cost_usd": 0.5,
        },
    ]
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    claude = daily_spend_for_vendor(rows, "Claude", days=3, now=now)
    assert len(claude) == 3
    by_date = {d["date"]: d for d in claude}
    # 18 Aug 10:00 UTC and 22:00 UTC may split across local days; both
    # tokens still land in the 3-day window.
    assert sum(d["tokens"] for d in claude) == 1500
    grok = daily_spend_for_vendor(rows, "Grok", days=3, now=now)
    grok_tok = sum(d["tokens"] for d in grok)
    grok_usd = sum(d["cost_usd"] or 0 for d in grok)
    assert grok_tok == 99
    assert abs(grok_usd - 0.5) < 1e-6


def test_tokens_per_percent_needs_delta_and_spend():
    samples = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
    df, resets, _ = prepare(samples)
    assert tokens_per_percent(df, resets, [], "Claude") is None
    # plenty of tokens but if they fall outside the current period, still None
    old = [
        {
            "provider": "claude",
            "ts": "2020-01-01T00:00:00+00:00",
            "total_tokens": 1_000_000,
        }
    ]
    assert tokens_per_percent(df, resets, old, "Claude") is None


def test_samples_path_override_used_by_prepare(tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
    # ensure env does not steal resolution when override is passed
    monkeypatch.delenv("AI_QUOTAS_SAMPLES", raising=False)
    df, resets, cutoff = prepare(fixture)
    assert len(df) > 0
    # path resolution itself
    assert samples_path(fixture) == fixture.expanduser()
