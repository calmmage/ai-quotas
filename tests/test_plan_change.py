"""A plan change (e.g. an upgrade pro → promax) is labelled, never a reset."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_quotas import subscriptions

pytest.importorskip("pandas")

from ai_quotas.plots.generate import _reset_plot_markers, _vendor_panel_payload  # noqa: E402
from ai_quotas.plots.prep import (  # noqa: E402
    PLAN_CHANGE,
    color_map,
    detect_resets,
    load_long,
    underutilised_events,
    window_usd_value,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _row(t, used, plan):
    return {"ts": t.isoformat(), "provider": "codex", "window": "week", "used_percent": used,
            "resets_at": (NOW + timedelta(days=3)).isoformat(), "plan": plan, "status": "ok",
            "account": "alice@example.com", "account_default": True, "device": "example-mac"}


@pytest.fixture
def upgrade_samples(tmp_path):
    rows = [_row(NOW - timedelta(hours=6 - i), 45.0 + i, "pro") for i in range(6)]
    rows += [_row(NOW + timedelta(hours=i), 0.0 + i, "promax") for i in range(3)]
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_promax_is_the_500_tier():
    sub = subscriptions.resolve("codex", "promax", {})
    assert sub["monthly_usd"] == 500.0
    assert "Pro" in sub["label"]


def test_upgrade_drop_is_a_plan_change_not_a_reset(upgrade_samples):
    df, _ = load_long(upgrade_samples)
    resets = detect_resets(df)
    assert len(resets) == 1
    event = resets[0]
    assert event.kind == PLAN_CHANGE
    assert event.label == "Plan change: pro → promax"
    assert event.money_usd == 0.0 and event.window_usd == 0.0
    # Not lost value, not a quota renewal.
    assert underutilised_events(resets, [], "Codex", "Codex week") == []
    markers = _reset_plot_markers(resets, [], "Codex", color_map(["Codex week"]))
    assert [(m["kind"], m["pill"]) for m in markers] == [("plan_change", "Plan change")]
    assert "pro → promax" in markers[0]["tooltip"]


def test_points_are_priced_by_their_own_plan(upgrade_samples):
    df, _ = load_long(upgrade_samples)
    payload = _vendor_panel_payload(df, detect_resets(df), "Codex")
    week = next(s for s in payload["series"] if s["label"] == "Codex week")
    before, after = week["point_usd"][0], week["point_usd"][-1]
    assert after == pytest.approx(window_usd_value("Codex week", "Codex", plan="promax", config={})[0], abs=0.01)
    assert after > 0 and before == 0.0  # plain "pro" stays unpriced without a saved subscription
    assert not [p for p in payload["usage_periods"] if not p["active"]]
