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
    assert event.label == "Plan change: pro → Pro Max $500"
    assert event.money_usd == 0.0 and event.window_usd == 0.0
    # Not lost value, not a quota renewal.
    assert underutilised_events(resets, [], "Codex", "Codex week") == []
    markers = _reset_plot_markers(resets, [], "Codex", color_map(["Codex week"]))
    assert [(m["kind"], m["pill"]) for m in markers] == [("plan_change", "Plan change")]
    assert "pro → Pro Max $500" in markers[0]["tooltip"]
    assert "%%" not in markers[0]["tooltip"]


def test_plan_change_names_the_plans_and_fits_a_phone_hover(tmp_path):
    """Claude reports plans as raw ids. The hover shows "Max 20x → Max 5x".

    19 Oct review: the hover read "Plan change: max+default_claude_max_20x →
    max+default_claude_max_5x · Claude week · 59%% left before", one line wider
    than a phone.
    """
    rows = []
    for i, (used, plan) in enumerate([(30.0, "max+default_claude_max_20x"), (41.0, "max+default_claude_max_20x"),
                                      (41.0, "max+default_claude_max_5x"), (42.0, "max+default_claude_max_5x")]):
        rows.append({"ts": (NOW + timedelta(hours=i)).isoformat(), "provider": "claude", "window": "week",
                     "used_percent": used, "resets_at": (NOW + timedelta(days=3)).isoformat(), "plan": plan,
                     "status": "ok", "account": "alice@example.com", "account_default": True,
                     "device": "example-mac"})
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    df, _ = load_long(path)
    changes = [e for e in detect_resets(df) if e.kind == PLAN_CHANGE]
    assert [e.label for e in changes] == ["Plan change: Max 20x → Max 5x"]
    markers = _reset_plot_markers(changes, [], "Claude", color_map([changes[0].series]))
    tip = markers[0]["tooltip"]
    assert "Max 20x → Max 5x · 59% left before" in tip
    assert "max+default" not in tip and "%%" not in tip
    assert len(tip) <= 50, tip


def _claude_row(t, used, plan, account, default):
    return {
        "ts": t.isoformat(), "provider": "claude", "window": "week", "used_percent": used,
        "resets_at": (NOW + timedelta(days=3)).isoformat(), "plan": plan, "status": "ok",
        "account": account, "account_default": default, "device": "example-mac",
    }


def test_one_sample_plan_flap_is_not_another_change(tmp_path):
    """Sanitized shape of one login's week, 16–17 Oct 2026.

    Held Max 5x, held Max 20x, held Max 5x, one Max 20x sample, held Max 5x.
    The single sample is not a second upgrade. The two held switches stay.
    Another login's own held upgrade stays on that login.
    """
    five = "max+default_claude_max_5x"
    twenty = "max+default_claude_max_20x"
    primary = "shared@example.com"
    other = "other@example.com"
    rows = []
    t = NOW
    runs = (
        [(40.0, five)] * 3
        + [(55.0, twenty)] * 2
        + [(47.0, five)] * 2
        + [(69.0, twenty)]
        + [(69.0, five)] * 2
    )
    for used, plan in runs:
        rows.append(_claude_row(t, used, plan, primary, True))
        t += timedelta(minutes=30)
    for used, plan in ((20.0, five), (21.0, five), (4.0, twenty), (5.0, twenty)):
        rows.append(_claude_row(t, used, plan, other, False))
        t += timedelta(minutes=30)
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    df, _ = load_long(path)
    changes = [e for e in detect_resets(df) if e.kind == PLAN_CHANGE]
    primary_changes = [e for e in changes if other not in e.series]
    other_changes = [e for e in changes if other in e.series]
    assert [e.label for e in primary_changes] == [
        "Plan change: Max 5x → Max 20x",
        "Plan change: Max 20x → Max 5x",
    ]
    assert [e.label for e in other_changes] == ["Plan change: Max 5x → Max 20x"]
    assert all(other not in e.series for e in primary_changes)
    markers = _reset_plot_markers(primary_changes, [], "Claude", color_map([primary_changes[0].series]))
    assert [m["kind"] for m in markers] == ["plan_change", "plan_change"]
    assert "Max 5x → Max 20x" in markers[0]["tooltip"]
    assert "Max 20x → Max 5x" in markers[1]["tooltip"]
    assert markers[0]["tooltip"].count("Max 5x → Max 20x") == 1


def test_a_plan_held_to_the_last_sample_still_counts(tmp_path):
    """One new plan on the last sample is the only observation of that change."""
    rows = []
    for i, plan in enumerate(["max+default_claude_max_5x"] * 3 + ["max+default_claude_max_20x"]):
        rows.append(_claude_row(NOW + timedelta(hours=i), 30.0 + i, plan, "alice@example.com", True))
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    df, _ = load_long(path)
    changes = [e for e in detect_resets(df) if e.kind == PLAN_CHANGE]
    assert [e.label for e in changes] == ["Plan change: Max 5x → Max 20x"]


def test_points_are_priced_by_their_own_plan(upgrade_samples):
    df, _ = load_long(upgrade_samples)
    payload = _vendor_panel_payload(df, detect_resets(df), "Codex")
    week = next(s for s in payload["series"] if s["label"] == "Codex week")
    before, after = week["point_usd"][0], week["point_usd"][-1]
    assert after == pytest.approx(window_usd_value("Codex week", "Codex", plan="promax", config={})[0], abs=0.01)
    assert after > 0 and before == 0.0  # plain "pro" stays unpriced without a saved subscription
    assert not [p for p in payload["usage_periods"] if not p["active"]]
