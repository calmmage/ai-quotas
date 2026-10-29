"""Regressions for the 29 Oct anonymous fallback / delayed reset incident."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_quotas.adapters import codex, orca
from ai_quotas.alerts import apply_dedupe
from ai_quotas.reset_notifications import reset_items
from ai_quotas import core
from ai_quotas.storage import append_samples, load_samples

NOW = datetime(2026, 10, 29, 0, tzinfo=timezone.utc)
ACCOUNT = "owner@example.test"


def row(used, at, reset, **extra):
    return dict(provider="codex", account=ACCOUNT, window="week", status="ok",
                ts=at.isoformat(), used_percent=used, resets_at=reset.isoformat(), **extra)


def rollout(tmp_path, observed=NOW-timedelta(minutes=20)):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    event = dict(type="event_msg", timestamp=observed.isoformat() if observed else None,
                 payload={"rate_limits": {"plan_type": "promax", "primary": {
                     "used_percent": 51, "window_minutes": 10080,
                     "resets_at": (NOW+timedelta(days=6)).timestamp()}}})
    (sessions / "rollout-example.jsonl").write_text(json.dumps(event)+"\n")
    return sessions


def test_failed_live_probe_keeps_home_identity_and_real_observation(tmp_path, monkeypatch):
    sessions = rollout(tmp_path)
    monkeypatch.setattr(codex, "home_email", lambda home: ACCOUNT)
    monkeypatch.setattr(codex, "_snapshot_codexbar", lambda *a, **kw: codex._fail(a[0], "error", "live timeout"))
    rows = codex.snapshot(NOW.isoformat(), sessions_dir=sessions, use_codexbar=True, homes=[tmp_path])
    week = next(r for r in rows if r.get("window") == "week")
    assert week["account"] == ACCOUNT
    assert week["observed_at"] == (NOW-timedelta(minutes=20)).isoformat()
    assert week["ts"] == NOW.isoformat()
    assert week["used_percent"] == 51
    assert week["fallback_reason"] == "live timeout"


@pytest.mark.parametrize("observed", [None, NOW-timedelta(hours=3), NOW+timedelta(minutes=1)])
def test_old_or_undated_rollout_cannot_become_fresh_by_polling(tmp_path, observed):
    rows = codex._snapshot_offline(NOW.isoformat(), sessions_root=rollout(tmp_path, observed))
    assert all(r["status"] == "unavailable" and r["used_percent"] is None for r in rows)


def test_cached_measurement_does_not_replace_newer_live_value_or_move_plot(tmp_path):
    db = tmp_path / "samples.sqlite3"
    end = NOW+timedelta(days=7)
    observed = NOW-timedelta(minutes=50)
    append_samples(db, [row(53, NOW-timedelta(minutes=10), end),
                        row(51, NOW, end, observed_at=observed.isoformat(), quota_source="codex_rollout")])
    samples = core.load_samples(db, all_accounts=True)
    assert core.latest_by_key(samples)[("codex", "week")]["used_percent"] == 53
    cached = next(r for r in samples if r.get("quota_source") == "codex_rollout")
    assert cached["ts"] == observed.isoformat() and cached["collected_at"] == NOW.isoformat()
    assert load_samples(db)[-1]["ts"] == NOW.isoformat()  # original receipt retained


def test_real_incident_anonymous_fallback_does_not_reannounce_named_reset():
    start = NOW-timedelta(hours=18)
    before = start-timedelta(minutes=30)
    end = start+timedelta(days=7)
    samples = [row(83, before, start+timedelta(days=5)), row(1, start, end), row(50, NOW-timedelta(minutes=30), end)]
    samples += [dict(row(87, before, start+timedelta(days=5)), account=None),
                dict(row(51, NOW, end), account=None, reason="offline rollout example.jsonl")]
    items = reset_items(samples, now=NOW)
    assert len(items) == 1
    assert items[0]["account"] == ACCOUNT and items[0]["used_before_reset"] == 83
    assert items[0]["observed_at"] == start.isoformat()


def test_long_gap_between_observations_is_not_a_new_reset():
    assert not reset_items([row(87, NOW-timedelta(hours=17), NOW+timedelta(days=4)),
                            row(51, NOW, NOW+timedelta(days=6))], now=NOW)


def test_cached_sample_uses_source_clock_and_cannot_replay_an_old_reset():
    samples = [row(90, NOW-timedelta(hours=4), NOW-timedelta(hours=3)),
               row(1, NOW, NOW+timedelta(days=7), quota_source="codex_rollout",
                   observed_at=(NOW-timedelta(hours=3)).isoformat())]
    assert not reset_items(samples, now=NOW)


def test_same_cycle_percentage_correction_is_not_a_refill():
    end = NOW+timedelta(days=5)
    assert not reset_items([row(90, NOW-timedelta(minutes=30), end), row(51, NOW, end)], now=NOW)


def test_real_manual_reset_and_new_cycle_after_it_still_notify():
    samples = [row(90, NOW-timedelta(minutes=30), NOW+timedelta(days=4)),
               row(0, NOW, NOW+timedelta(days=7))]
    first = reset_items(samples, now=NOW)
    assert len(first) == 1
    _, _, state = apply_dedupe(first, {}, now=NOW)
    later = NOW+timedelta(hours=2)
    samples += [row(85, later-timedelta(minutes=30), NOW+timedelta(days=7)),
                row(0, later, later+timedelta(days=7))]
    second = reset_items(samples, now=later)
    assert len(apply_dedupe(second, state, now=later)[0]) == 1


def test_fallback_observation_of_delivered_cycle_dedupes_legacy_fingerprint():
    samples = [row(90, NOW-timedelta(minutes=30), NOW+timedelta(days=4)),
               row(0, NOW, NOW+timedelta(days=7))]
    items = reset_items(samples, now=NOW)
    old = {"sent": {f"reset:codex:{ACCOUNT}:week:123": dict(kind="reset", severity="INFO",
            resets_at=(NOW+timedelta(days=7, seconds=1)).isoformat(),
            dedupe_until=(NOW+timedelta(days=2)).isoformat())}}
    assert apply_dedupe(items, old, now=NOW)[0] == []


def test_orca_aggregate_does_not_duplicate_detailed_agy_reset():
    samples = [dict(row(100, NOW-timedelta(minutes=30), NOW-timedelta(minutes=5)), provider="antigravity"),
               dict(row(0, NOW, NOW+timedelta(days=7)), provider="antigravity"),
               dict(row(0, NOW, NOW+timedelta(days=7)), provider="agy", window="week_gemini")]
    assert not reset_items(samples, now=NOW)
    # The aggregate remains useful on installations with no detailed adapter.
    assert len(reset_items(samples[:2], now=NOW)) == 1


def test_orca_reading_preserves_upstream_identity_and_cache_time(monkeypatch):
    observed = NOW-timedelta(minutes=20)
    monkeypatch.setattr(orca, "_orca_payload", lambda: {"rateLimits": {"antigravity": {
        "status": "ok", "updatedAt": observed.timestamp()*1000,
        "usageMetadata": {"authProvenance": ACCOUNT},
        "weekly": {"usedPercent": 25, "windowMinutes": 10080,
                   "resetsAt": (NOW+timedelta(days=7)).timestamp()*1000}}}})
    rows = orca.snapshot(NOW.isoformat())
    assert rows[0]["account"] == ACCOUNT
    assert rows[0]["observed_at"] == observed.isoformat()
    assert rows[0]["used_percent"] == 25
