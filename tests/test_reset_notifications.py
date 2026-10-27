"""Exercise real storage -> alert delivery, across accounts and quota cycles."""
from datetime import datetime, timedelta, timezone

import pytest

from ai_quotas.alerts import run_alerts
from ai_quotas.storage import append_samples
from ai_quotas import core

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def reading(account, used, ts, reset, window="week", **extra):
    return dict(provider="claude", account=account, window=window,
                ts=ts.isoformat(), resets_at=reset.isoformat(),
                status="ok", used_percent=used, **extra)


def test_reset_notifications_reach_each_account_once_and_retry(tmp_path, monkeypatch):
    db, state = tmp_path / "q.sqlite3", tmp_path / "state.json"
    monkeypatch.setattr("ai_quotas.alerts.send_email", lambda _: "skip")
    old_reset = NOW - timedelta(minutes=15)
    for account in ["first@example.test", "second@example.test"]:
        append_samples(db, [
            reading(account, 98, NOW-timedelta(hours=1), old_reset),
            reading(account, 3, NOW, old_reset+timedelta(days=7)),
        ])
    sent = []
    def run(status="sent", **kw):
        return run_alerts(path=db, state_file=state, now=NOW,
                          sender=lambda message: sent.append(message) or status, **kw)
    preview = run(dry_run=True)
    assert len([i for i in preview["items"] if i["kind"] == "reset"]) == 2
    assert not state.exists()
    assert run("error:offline")["delivery"] == "error:offline"
    delivered = run()
    assert delivered["delivery"] == "sent"
    assert all(account in delivered["reset_message"] for account in
               ["first@example.test", "second@example.test"])
    assert run()["new"] == 0


@pytest.mark.parametrize("window", ["5h", "5h_gemini_pro", "day", "free_daily", "2h"])
def test_short_window_reset_never_notifies_and_remains_stored(tmp_path, window):
    db = tmp_path / "q.sqlite3"
    append_samples(db, [reading("a", 100, NOW-timedelta(hours=1), NOW-timedelta(minutes=5), window),
                        reading("a", 0, NOW, NOW+timedelta(hours=5), window)])
    report = run_alerts(path=db, state_file=tmp_path/"s.json", now=NOW, dry_run=True)
    assert not [i for i in report["items"] if i["kind"] == "reset"]
    assert len(core.load_samples(db, all_accounts=True)) == 2


@pytest.mark.parametrize("window,period", [("week", 7), ("week_fable", 7), ("month", 31)])
@pytest.mark.parametrize("used,color", [(100, "🟢"), (80, "🟢"), (79.99, "🟡"),
                                       (50, "🟡"), (49.99, "🔴"), (1, "🔴"), (0, "🔴")])
def test_reset_color_is_previous_period_consumption(tmp_path, window, period, used, color):
    db = tmp_path/"q.sqlite3"
    old_reset = NOW-timedelta(minutes=5)
    append_samples(db, [reading("owner@example.test", used, NOW-timedelta(hours=1), old_reset, window),
                        reading("owner@example.test", 0, NOW, old_reset+timedelta(days=period), window)])
    report = run_alerts(path=db, state_file=tmp_path/"s.json", now=NOW, dry_run=True)
    item = next(i for i in report["items"] if i["kind"] == "reset")
    assert item["used_before_reset"] == used
    assert item["color"] == color
    assert color in report["reset_message"]
    assert "used before reset" in report["reset_message"]


def test_different_account_colors_remain_visible_in_one_message(tmp_path):
    db = tmp_path/"q.sqlite3"
    for account, used in [("green@example.test", 90), ("yellow@example.test", 60), ("red@example.test", 20)]:
        append_samples(db, [reading(account, used, NOW-timedelta(hours=1), NOW-timedelta(minutes=5)),
                            reading(account, 0, NOW, NOW+timedelta(days=7))])
    report = run_alerts(path=db, state_file=tmp_path/"s.json", now=NOW, dry_run=True)
    visible = report["reset_message"].split('<blockquote')[0]
    assert "🟢 claude · green@example.test" in visible
    assert "🟡 claude · yellow@example.test" in visible
    assert "🔴 claude · red@example.test" in visible


def test_no_reset_from_jitter_expired_error_missing_data_or_old_history(tmp_path):
    db = tmp_path / "q.sqlite3"
    future = NOW+timedelta(days=2)
    append_samples(db, [reading("a", 50, NOW-timedelta(hours=1), future),
                        reading("a", 49.5, NOW, future+timedelta(seconds=1)),
                        reading("b", 100, NOW-timedelta(days=6), NOW-timedelta(days=2), individual_cap=True),
                        {**reading("b", 0, NOW, future), "used_percent": None, "status": "unavailable"},
                        reading("c", 90, NOW-timedelta(days=3), NOW-timedelta(days=2)),
                        reading("c", 0, NOW-timedelta(days=2), future)])
    report = run_alerts(path=db, state_file=tmp_path/"s.json", now=NOW, dry_run=True)
    assert not [i for i in report["items"] if i["kind"] == "reset"]


def test_retired_grok_bot_cannot_reappear_via_any_window(tmp_path):
    db = tmp_path / "q.sqlite3"
    append_samples(db, [{**reading("a", 100, NOW, NOW+timedelta(days=2)),
                         "provider": "grok-bot", "window": w}
                        for w in ["week", "unknown", "5h", "credit_grant"]])
    assert core.load_samples(db, all_accounts=True) == []


def test_reset_dedupe_survives_deadline_and_batch_failure_retries_only_unsent(tmp_path, monkeypatch):
    db, state = tmp_path/"q.sqlite3", tmp_path/"s.json"
    monkeypatch.setattr("ai_quotas.alerts.send_email", lambda _: "skip")
    for i in range(4):
        append_samples(db, [reading(f"a{i}@example.test", 99, NOW-timedelta(hours=1), NOW-timedelta(minutes=5)),
                            reading(f"a{i}@example.test", 0, NOW, NOW+timedelta(days=7))])
    calls = []
    def send(message):
        calls.append(message)
        return "sent" if len(calls) == 1 else "error:offline"
    run_alerts(path=db, state_file=state, now=NOW, sender=send)
    assert len(calls) == 2
    retried = run_alerts(path=db, state_file=state, now=NOW, sender=lambda _: "sent")
    assert len(retried["items"]) == 1
    assert retried["items"][0]["account"] == "a3@example.test"
    assert run_alerts(path=db, state_file=state, now=NOW+timedelta(minutes=1), dry_run=True)["new"] == 0


def test_all_reported_short_values_are_stored_and_codex_5h_reaches_chart(tmp_path):
    from ai_quotas.collector import sample_now
    from ai_quotas.plots.prep import prepare
    from ai_quotas.plots.generate import _vendor_panel_payload
    db = tmp_path/"q.sqlite3"
    sample_now(path=db, ts=NOW.isoformat(), adapters={"codex": lambda ts: [
        {**reading("a@example.test", 45, NOW, NOW+timedelta(hours=5), "5h"), "provider": "codex"},
        {**reading("a@example.test", 60, NOW, NOW+timedelta(days=7)), "provider": "codex"},
    ]})
    assert {r["window"] for r in core.load_samples(db)} == {"week", "5h"}
    df, resets, _ = prepare(db)
    panel = _vendor_panel_payload(df, resets, "Codex")
    short = next(s for s in panel["series"] if s["label"] == "Codex 5h")
    assert short["y"] == [55]
    assert short["dim"] and short["window_usd"] == 0


def test_expired_argon_has_no_current_zero_percent_point(tmp_path):
    from ai_quotas.plots.prep import prepare
    from ai_quotas.plots.generate import _vendor_panel_payload
    db = tmp_path/"q.sqlite3"
    current = datetime.now(timezone.utc)
    append_samples(db, [{**reading("a@example.test", 100, current-timedelta(hours=2), current-timedelta(hours=1)),
                         "provider": "agy", "window": "week_gemini_argon", "individual_cap": True},
                        {**reading("a@example.test", 0, current, current+timedelta(hours=5)),
                         "provider": "agy", "window": "5h_gemini_pro"}])
    df, resets, _ = prepare(db)
    panel = _vendor_panel_payload(df, resets, "Gemini")
    cap = next(s for s in panel["series"] if s["label"] == "Argon week")
    assert cap["y"][-1] is None
    assert panel["model_limits"][0]["used_percent"] is None
    # An active cap's future deadline is not a new sample in the future.
    append_samples(db, [{**reading("a@example.test", 100, current, current+timedelta(days=7)),
                         "provider": "agy", "window": "week_gemini_argon", "individual_cap": True}])
    df, resets, _ = prepare(db)
    live = next(s for s in _vendor_panel_payload(df, resets, "Gemini")["series"] if s["label"] == "Argon week")
    assert live["t"][-1] <= current.timestamp()


def test_successful_argon_response_supersedes_old_rejection_without_faking_balance(tmp_path):
    from ai_quotas.agy_limits import argon_success_observation
    from ai_quotas.plots.prep import prepare
    from ai_quotas.plots.generate import _vendor_panel_payload
    db = tmp_path/"q.sqlite3"
    success = argon_success_observation(NOW.isoformat(), account="owner@example.test",
                                        model="gemini-4-argon-low", evidence_file="/logs/cli-example.log")
    append_samples(db, [{**reading("owner@example.test", 100, NOW-timedelta(hours=1), NOW+timedelta(hours=5)),
                         "provider": "agy", "window": "week_gemini_argon", "individual_cap": True}, success])
    result = core.evaluate(core.load_samples(db), now=NOW)
    argon = next(r for r in result["windows"] if r["window"] == "week_gemini_argon")
    assert argon["used_percent"] is None and argon["verdict"] == "UNKNOWN"
    df, resets, _ = prepare(db)
    panel = _vendor_panel_payload(df, resets, "Gemini")
    assert panel["model_limits"][0]["availability"] == "usable"
    assert success["evidence_file"] == "cli-example.log"
    # The sampler reuses CLI errors every tick. That does not make an old
    # rejection newer evidence than the successful response.
    old = {**reading("owner@example.test", 100, NOW+timedelta(minutes=30), NOW+timedelta(hours=5)),
           "provider": "agy", "window": "week_gemini_argon", "individual_cap": True,
           "observed_at": (NOW-timedelta(hours=1)).isoformat()}
    append_samples(db, [old])
    assert core.latest_by_key(core.load_samples(db))[("agy", "week_gemini_argon")]["availability"] == "usable"
