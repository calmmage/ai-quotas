"""Recent burn must stay visible as the quota archive grows."""

import pytest

pd = pytest.importorskip("pandas")

from ai_quotas.plots.generate import _burn_density_ticks


def frame(used, start="2026-09-01", freq="30min"):
    return pd.DataFrame({
        "ts_local": pd.date_range(start, periods=len(used), freq=freq, tz="UTC"),
        "used_percent": used,
    })


def test_old_reset_periods_do_not_thin_recent_bars():
    old = frame([0, 25, 50, 75, 100, 0] * 8)
    recent = frame([2, 3, 4, 5, 6], start="2026-09-20")
    alone = _burn_density_ticks(recent)
    combined = _burn_density_ticks(pd.concat([old, recent]))
    assert [p for p in combined if p[0] >= recent.ts_local.iloc[0]] == alone
    assert len(alone) >= 20
    assert {round(y) for _, y in alone} >= {94, 95, 96, 97}


def test_bars_interpolate_small_gaps_and_stop_at_long_outages():
    small = _burn_density_ticks(frame([20, 24], freq="4h"))
    assert small
    assert all(76 <= y <= 80 for _, y in small)
    assert _burn_density_ticks(frame([20, 24], freq="13h")) == []


def test_reset_jump_and_flat_usage_have_no_burn_bars():
    assert _burn_density_ticks(frame([95, 0, 0])) == []
    ticks = _burn_density_ticks(frame([20, 21]))
    assert ticks[-1][1] == pytest.approx(79)
