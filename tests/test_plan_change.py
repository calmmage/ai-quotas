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


def _claude_row(t, used, plan, account, default, deadline=None):
    return {
        "ts": t.isoformat(), "provider": "claude", "window": "week", "used_percent": used,
        "resets_at": (deadline or (NOW + timedelta(days=3))).isoformat(), "plan": plan,
        "status": "ok", "account": account, "account_default": default, "device": "example-mac",
    }


def _instant(value):
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def test_one_sample_plan_observation_stays_on_its_login(tmp_path):
    """Sanitized shape of one login's week, 16–17 Oct 2026.

    Held Max 5x, held Max 20x, held Max 5x, one Max 20x sample, held Max 5x.
    One sample does not prove the transition was false, so both edges stay.
    Another login's own held upgrade stays on that login. Used% rises, so the
    used% glitch filter does not delete a row before the plan is read. A plan
    edge with a stable deadline is the plan change, not a second reset.
    """
    five = "max+default_claude_max_5x"
    twenty = "max+default_claude_max_20x"
    primary = "shared@example.com"
    other = "other@example.com"
    rows = []
    t = NOW
    runs = (
        [(40.0, five), (41.0, five), (42.0, five)]
        + [(43.0, twenty), (44.0, twenty)]
        + [(45.0, five), (46.0, five)]
        + [(47.0, twenty)]
        + [(48.0, five), (49.0, five)]
    )
    for used, plan in runs:
        rows.append(_claude_row(t, used, plan, primary, True))
        t += timedelta(minutes=30)
    for used, plan in ((20.0, five), (21.0, five), (22.0, twenty), (23.0, twenty)):
        rows.append(_claude_row(t, used, plan, other, False))
        t += timedelta(minutes=30)
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    df, _ = load_long(path)
    events = detect_resets(df)
    primary_events = [e for e in events if other not in e.series]
    other_events = [e for e in events if other in e.series]
    assert [e.label for e in primary_events] == [
        "Plan change: Max 5x → Max 20x",
        "Plan change: Max 20x → Max 5x",
        "Plan change: Max 5x → Max 20x",
        "Plan change: Max 20x → Max 5x",
    ]
    assert [e.kind for e in primary_events] == [PLAN_CHANGE] * 4
    assert [e.label for e in other_events] == ["Plan change: Max 5x → Max 20x"]
    assert other_events[0].kind == PLAN_CHANGE
    assert all(other not in e.series for e in primary_events)
    markers = _reset_plot_markers(primary_events, [], "Claude", color_map([primary_events[0].series]))
    assert [m["kind"] for m in markers] == ["plan_change"] * 4
    assert markers[0]["tooltip"].count("Max 5x → Max 20x") == 1
    assert "Max 20x → Max 5x" in markers[1]["tooltip"]
    assert "Max 5x → Max 20x" in markers[2]["tooltip"]
    assert "Max 20x → Max 5x" in markers[3]["tooltip"]


def test_deadline_rollover_on_a_one_sample_plan_edge_stays(tmp_path):
    """A one-sample plan and a published deadline can share one pair.

    Both stay. The plan edge is a plan change. The deadline opening is a
    reset. A later same-plan rollover on that login is a reset too. The
    other login's plan change is not that reset.
    """
    five = "max+default_claude_max_5x"
    twenty = "max+default_claude_max_20x"
    primary = "shared@example.com"
    other = "other@example.com"
    # Sample times stay inside one day. A gap of 24h would drop the earlier
    # island, and the deadline still jumps by more than the slack.
    opened = NOW
    previous = opened
    nxt = opened + timedelta(hours=13)
    after = nxt + timedelta(hours=13)
    rows = [
        _claude_row(opened - timedelta(hours=2), 40.0, five, primary, True, previous),
        _claude_row(opened - timedelta(hours=1), 42.0, five, primary, True, previous),
        _claude_row(opened - timedelta(minutes=30), 44.0, five, primary, True, previous),
        _claude_row(opened, 0.0, twenty, primary, True, nxt),
        _claude_row(opened + timedelta(minutes=20), 1.0, five, primary, True, nxt),
        _claude_row(opened + timedelta(minutes=40), 3.0, five, primary, True, nxt),
        _claude_row(nxt - timedelta(minutes=20), 25.0, five, primary, True, nxt),
        _claude_row(nxt + timedelta(minutes=5), 0.0, five, primary, True, after),
    ]
    t = opened - timedelta(hours=4)
    stable = opened + timedelta(days=3)
    for used, plan in ((20.0, five), (21.0, five), (22.0, twenty), (23.0, twenty)):
        rows.append(_claude_row(t, used, plan, other, False, stable))
        t += timedelta(minutes=30)
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    df, _ = load_long(path)
    events = detect_resets(df)
    primary = [e for e in events if other not in e.series]
    other_events = [e for e in events if other in e.series]
    changes = [e for e in primary if e.kind == PLAN_CHANGE]
    resets = [e for e in primary if e.kind != PLAN_CHANGE]
    assert [e.label for e in changes] == [
        "Plan change: Max 5x → Max 20x",
        "Plan change: Max 20x → Max 5x",
    ]
    assert _instant(changes[0].at) == opened.replace(microsecond=0)
    assert (changes[0].used_before, changes[0].used_after) == (44.0, 0.0)
    assert [(_instant(e.at), e.used_before, e.used_after) for e in resets] == [
        (opened.replace(microsecond=0), 44.0, 0.0),
        ((nxt + timedelta(minutes=5)).replace(microsecond=0), 25.0, 0.0),
    ]
    assert [e.label for e in other_events] == ["Plan change: Max 5x → Max 20x"]
    assert other_events[0].kind == PLAN_CHANGE


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
