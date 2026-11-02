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
    sent.clear()
    delivered = run()
    assert delivered["delivery"] == "sent"
    # One message per login; the login is named because two are connected.
    assert [m.split("\n")[0] for m in sent] == [
        "scheduled quota refresh · Claude · first", "scheduled quota refresh · Claude · second"]
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
    assert "used before refresh" in report["reset_message"]
    assert report["reset_message"].startswith("scheduled quota refresh · Claude\n")


def test_different_account_colors_remain_visible_in_one_message(tmp_path):
    db = tmp_path/"q.sqlite3"
    for account, used in [("green@example.test", 90), ("yellow@example.test", 60), ("red@example.test", 20)]:
        append_samples(db, [reading(account, used, NOW-timedelta(hours=1), NOW-timedelta(minutes=5)),
                            reading(account, 0, NOW, NOW+timedelta(days=7))])
    report = run_alerts(path=db, state_file=tmp_path/"s.json", now=NOW, dry_run=True)
    messages = report["messages"]
    assert len(messages) == 3
    for login, line in [("green", "🟢 90% used"), ("yellow", "🟡 60% used"), ("red", "🔴 20% used")]:
        message = next(m for m in messages if m.startswith(f"scheduled quota refresh · Claude · {login}\n"))
        assert message.split("\n")[1].startswith(line)


@pytest.mark.parametrize("used,emoji_id,fallback", [
    (80, "5449380056201697322", "💚"),
    (50, "5449366943666543715", "💛"),
    (49, "5449505950283078474", "❤️"),
])
def test_custom_emoji_ids_wrap_correct_fallback_without_escaping_tags(tmp_path, monkeypatch, used, emoji_id, fallback):
    from ai_quotas.alert_text import format_refresh
    from ai_quotas.reset_notifications import reset_marker
    from zoneinfo import ZoneInfo
    path = tmp_path/"config.toml"
    path.write_text('[telegram.reset_emoji]\ngreen="5449380056201697322"\nyellow="5449366943666543715"\nred="5449505950283078474"\n')
    monkeypatch.setenv("AI_QUOTAS_CONFIG", str(path))
    logins = {"claude": {"a&b@example.test", "other@example.test"}}
    text = format_refresh([dict(provider="claude", account="a&b@example.test", window="week", refresh="scheduled",
                                used_before_reset=used, remaining=100, observed_at=NOW.isoformat())],
                          logins=logins, tz=ZoneInfo("Europe/Moscow"), marker=reset_marker(used))
    assert f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>' in text
    assert text.startswith('scheduled quota refresh · Claude · a&amp;b\n')
    assert '&lt;tg-emoji' not in text


def test_bad_custom_emoji_config_preserves_notification(tmp_path, monkeypatch):
    from ai_quotas.reset_notifications import reset_marker
    path = tmp_path/"config.toml"
    path.write_text('[telegram.reset_emoji]\ngreen="not-an-id"\nyellow="9999999999999999999999999"\n')
    monkeypatch.setenv("AI_QUOTAS_CONFIG", str(path))
    assert reset_marker(90) == "🟢"
    assert reset_marker(60) == "🟡"
    assert reset_marker(20) == "🔴"


@pytest.mark.parametrize("used,band", [(80, "green"), (79.99, "yellow"), (50, "yellow"), (49.99, "red")])
def test_random_marker_draws_fresh_from_entire_matching_color_pool(tmp_path, monkeypatch, used, band):
    from ai_quotas import reset_notifications as resets

    config = tmp_path / "config.toml"
    config.write_text('[telegram.reset_emoji]\ngreen="random"\nyellow="random"\nred="random"\n')
    monkeypatch.setenv("AI_QUOTAS_CONFIG", str(config))
    pool = resets._reset_emoji_pool(band)
    assert len(pool) >= 128  # Do not regress the shipped catalogue to a tiny rotation.
    draws = []

    def choose(candidates):
        assert candidates == pool
        selected = candidates[0] if not draws else candidates[-1]
        draws.append(selected)
        return selected

    monkeypatch.setattr(resets, "choice", choose)
    first, second = resets.reset_marker(used), resets.reset_marker(used)
    assert first != second
    for marker, (emoji_id, fallback) in zip([first, second], draws):
        assert marker == f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def test_random_catalogue_has_unique_ids_and_original_fallbacks():
    from ai_quotas.reset_notifications import _reset_emoji_pool

    seen = set()
    for band in ["green", "yellow", "red"]:
        entries = _reset_emoji_pool(band)
        assert len(entries) >= 128
        for emoji_id, fallback in entries:
            assert emoji_id not in seen
            seen.add(emoji_id)
            assert fallback and not any(c in fallback for c in '<>&"')


def test_random_marker_with_unavailable_catalogue_preserves_alert(tmp_path, monkeypatch):
    from ai_quotas import reset_notifications as resets

    config = tmp_path / "config.toml"
    config.write_text('[telegram.reset_emoji]\ngreen="random"\n')
    monkeypatch.setenv("AI_QUOTAS_CONFIG", str(config))
    monkeypatch.setattr(resets, "_reset_emoji_pool", lambda _: ())
    assert resets.reset_marker(80) == "🟢"


def test_random_alert_receipt_matches_delivered_emoji_and_does_not_renotify(tmp_path, monkeypatch):
    from ai_quotas import reset_notifications as resets

    config = tmp_path / "config.toml"
    config.write_text('[telegram.reset_emoji]\ngreen="random"\n')
    monkeypatch.setenv("AI_QUOTAS_CONFIG", str(config))
    monkeypatch.setattr("ai_quotas.alerts.send_email", lambda _: "skip")
    selected = []

    def choose(pool):
        entry = pool[len(selected) % len(pool)]
        selected.append(entry)
        return entry

    monkeypatch.setattr(resets, "choice", choose)
    db, state = tmp_path / "q.sqlite3", tmp_path / "state.json"
    for i in range(4):
        append_samples(db, [
            reading(f"a{i}@example.test", 99, NOW-timedelta(hours=1), NOW-timedelta(minutes=5)),
            reading(f"a{i}@example.test", 0, NOW, NOW+timedelta(days=7)),
        ])
    delivered = []
    sender = lambda message: delivered.append(message) or "sent"
    report = run_alerts(path=db, state_file=state, now=NOW, sender=sender)
    assert len(delivered) == 4  # one login, one message
    assert report["reset_message"] == "\n\n".join(delivered)
    assert all(message.count("<tg-emoji ") == 1 for message in delivered)
    assert run_alerts(path=db, state_file=state, now=NOW, sender=sender)["new"] == 0
    assert len(delivered) == 4


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
    assert len(calls) == 2  # the first error stops this run
    retried = run_alerts(path=db, state_file=state, now=NOW, sender=lambda _: "sent")
    assert sorted(i["account"] for i in retried["items"]) == [f"a{i}@example.test" for i in (1, 2, 3)]
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
    assert not any(s["label"] == "Argon week" for s in panel["series"])
    assert panel["model_limits"][0]["used_percent"] is None
    # An active cap's future deadline is not a new sample in the future.
    append_samples(db, [{**reading("a@example.test", 100, current, current+timedelta(days=7)),
                         "provider": "agy", "window": "week_gemini_argon", "individual_cap": True}])
    df, resets, _ = prepare(db)
    live = _vendor_panel_payload(df, resets, "Gemini")
    assert not any(s["label"] == "Argon week" for s in live["series"])
    assert live["model_limits"][0]["used_percent"] == 100


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
