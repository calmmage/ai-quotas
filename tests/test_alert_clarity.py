"""Petr, 3 Nov 2026: action · app · login first, one event per message,
scheduled / unexpected / manual refresh, no repeated warning, a plot per alert,
one morning report. Real storage → run_alerts → the text that is sent."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_quotas import alerts, morning
from ai_quotas.alert_plot import plot_png
from ai_quotas.alerts import run_alerts, run_after_sample
from ai_quotas.reset_credits import credit_row, none_row
from ai_quotas.storage import append_reset_credits, append_samples

NOW = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)  # 08:00 Moscow
CHECK = Path.home() / ".claude/skills/write:telegram-formatting/check.py"


def reading(provider, account, used, ts, reset, window="week", **extra):
    return dict(provider=provider, account=account, window=window, ts=ts.isoformat(),
                resets_at=reset.isoformat(), status="ok", used_percent=used, **extra)


def run(db, tmp_path, **kw):
    kw.setdefault("now", NOW)
    return run_alerts(path=db, state_file=tmp_path / "state.json", dry_run=True, **kw)


def heads(report):
    return [m.split("\n")[0] for m in report["messages"]]


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    monkeypatch.setattr("ai_quotas.alerts.send_email", lambda _: "skip")
    monkeypatch.setenv("AI_QUOTAS_CONFIG", str(tmp_path / "none.toml"))


def test_scheduled_refresh_is_one_claude_message_for_week_and_fable(tmp_path):
    db = tmp_path / "q.sqlite3"
    deadline = NOW - timedelta(minutes=20)
    for window, used in [("week", 48), ("week_fable", 0)]:
        append_samples(db, [
            reading("claude", "calmmage.shared@gmail.com", used, NOW - timedelta(minutes=40), deadline, window),
            reading("claude", "calmmage.shared@gmail.com", 0, NOW - timedelta(minutes=10), deadline + timedelta(days=7), window),
        ])
    report = run(db, tmp_path)
    assert {i["refresh"] for i in report["items"]} == {"scheduled"}
    assert heads(report) == ["scheduled quota refresh · Claude"]
    text = report["messages"][0]
    assert text.split("\n")[1].endswith("48% used before refresh")
    assert "Fable week refreshed too" in text.split("<blockquote expandable>")[1]
    assert "Fable" not in text.split("<blockquote")[0]


def test_fable_refresh_after_the_week_alert_is_not_a_second_message(tmp_path):
    db, state = tmp_path / "q.sqlite3", tmp_path / "state.json"
    deadline = NOW - timedelta(minutes=50)
    append_samples(db, [
        reading("claude", "a@example.test", 60, NOW - timedelta(minutes=70), deadline),
        reading("claude", "a@example.test", 0, NOW - timedelta(minutes=40), deadline + timedelta(days=7)),
    ])
    sent = []
    run_alerts(path=db, state_file=state, now=NOW, sender=lambda m: sent.append(m) or "sent")
    assert len(sent) == 1
    append_samples(db, [
        reading("claude", "a@example.test", 10, NOW - timedelta(minutes=70), deadline, "week_fable"),
        reading("claude", "a@example.test", 0, NOW - timedelta(minutes=5), deadline + timedelta(days=7), "week_fable"),
    ])
    later = run_alerts(path=db, state_file=state, now=NOW, sender=lambda m: sent.append(m) or "sent")
    assert later["new"] == 1 and len(sent) == 1
    assert run_alerts(path=db, state_file=state, now=NOW, dry_run=True)["new"] == 0


def early(db, provider, account, *, confirm=True, snap_back=False):
    """A refill a day and a half before the deadline."""
    deadline = NOW + timedelta(hours=36)
    rows = [reading(provider, account, 70, NOW - timedelta(hours=1), deadline),
            reading(provider, account, 0, NOW - timedelta(minutes=30), NOW + timedelta(days=7))]
    if confirm:
        rows.append(reading(provider, account, 70, NOW, deadline) if snap_back
                    else reading(provider, account, 1, NOW, NOW + timedelta(days=7)))
    append_samples(db, rows)


def test_unexpected_refresh_names_the_early_jump(tmp_path):
    db = tmp_path / "q.sqlite3"
    early(db, "codex", "p@example.test")
    report = run(db, tmp_path)
    assert heads(report) == ["unexpected quota refresh · Codex"]
    fold = report["messages"][0].split("<blockquote expandable>")[1]
    assert "came early" in fold


def test_manual_refresh_when_a_banked_reset_left_the_listing(tmp_path):
    db = tmp_path / "q.sqlite3"
    early(db, "codex", "p@example.test")
    expires = (NOW + timedelta(days=9)).isoformat()
    append_reset_credits(db, [
        credit_row((NOW - timedelta(hours=1)).isoformat(), "codex", credit_id="c1",
                   title="Weekly reset", expires_at=expires, account="p@example.test"),
        none_row((NOW - timedelta(minutes=30)).isoformat(), "codex", account="p@example.test"),
    ])
    report = run(db, tmp_path)
    assert heads(report) == ["manual quota refresh · Codex"]
    assert "banked reset used · Weekly reset" in report["messages"][0]


def test_banked_reset_without_a_credit_listing_reads_as_unexpected(tmp_path):
    """The case that cannot be told apart: no credit probe, no proof of a redemption."""
    db = tmp_path / "q.sqlite3"
    early(db, "codex", "p@example.test")
    assert heads(run(db, tmp_path)) == ["unexpected quota refresh · Codex"]


def test_early_refill_waits_for_a_second_reading_and_a_glitch_never_alerts(tmp_path):
    pending = tmp_path / "pending.sqlite3"
    early(pending, "grok", "p@example.test", confirm=False)
    assert run(pending, tmp_path)["messages"] == []
    glitch = tmp_path / "glitch.sqlite3"
    early(glitch, "grok", "p@example.test", snap_back=True)  # Grok, 2 Nov 10:12
    assert run(glitch, tmp_path)["messages"] == []


def spare_rows(provider="grok", account="p@example.test", now=NOW, deadline=None):
    deadline = deadline or now + timedelta(hours=23)
    return [reading(provider, account, 14, now - timedelta(hours=1), deadline),
            reading(provider, account, 14, now, deadline + timedelta(seconds=20))]


def test_use_it_before_reset_goes_out_once_per_cycle(tmp_path):
    db, state = tmp_path / "q.sqlite3", tmp_path / "state.json"
    append_samples(db, spare_rows())
    sent = []
    send = lambda m: sent.append(m) or "sent"
    first = run_alerts(path=db, state_file=state, now=NOW, include_reset_soon=True, sender=send)
    assert [m.split("\n")[0] for m in sent] == ["use it before reset · Grok"]
    assert first["urgent_message"] == "use it before reset · Grok\n86% left · refresh in 23h"
    # 31 minutes later, a deadline that jittered by seconds: nothing new (23:14 / 23:45).
    later = NOW + timedelta(minutes=31)
    append_samples(db, spare_rows(now=later, deadline=NOW + timedelta(hours=23, seconds=40)))
    run_alerts(path=db, state_file=state, now=later, include_reset_soon=True, sender=send)
    assert len(sent) == 1


def test_a_window_that_just_refreshed_gets_no_warning_for_the_old_cycle(tmp_path):
    from ai_quotas.alerts import _drop_old_cycle

    old = {"kind": "spare", "provider": "grok", "window": "week",
           "resets_at": (NOW + timedelta(hours=5)).isoformat()}
    new_cycle = {"kind": "reset", "provider": "grok", "window": "week",
                 "resets_at": (NOW + timedelta(days=7)).isoformat()}
    assert _drop_old_cycle([old], [new_cycle]) == []
    assert _drop_old_cycle([old], []) == [old]


def test_burn_first_line_and_folded_numbers(tmp_path):
    from ai_quotas.alerts import format_message

    item = {"kind": "burn", "provider": "claude", "window": "week", "severity": "STOP",
            "account": "calmmage.shared@gmail.com", "remaining": 27.0, "hours_to_reset": 104.0,
            "pace": "🔴 558% of quota pace", "projected_final": 420.0,
            "resets_at": "2026-11-05T08:00:00+00:00", "resets_available": 0}
    logins = {"claude": {"calmmage.shared@gmail.com", "petr.b.lavrov@gmail.com"}}
    text = format_message([item], logins=logins)
    assert text.split("\n")[:2] == ["burn stop · Claude · calmmage.shared", "27% left · refresh in 4d 8h"]
    assert "558% of quota pace" in text.split("<blockquote")[1]


@pytest.mark.skipif(not CHECK.is_file(), reason="write:telegram-formatting check.py not installed")
def test_every_new_shape_passes_the_telegram_check(tmp_path):
    spec = importlib.util.spec_from_file_location("tg_check", CHECK)
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    db = tmp_path / "q.sqlite3"
    deadline = NOW - timedelta(minutes=20)
    append_samples(db, [
        reading("claude", "a@example.test", 48, NOW - timedelta(minutes=40), deadline),
        reading("claude", "a@example.test", 0, NOW - timedelta(minutes=10), deadline + timedelta(days=7)),
        *spare_rows(),
    ])
    early(db, "codex", "p@example.test")
    report = run(db, tmp_path, include_reset_soon=True)
    burn = alerts.format_message([{"kind": "burn", "provider": "claude", "window": "week", "severity": "WARN",
                                   "remaining": 20.0, "hours_to_reset": 80.0, "pace": "🔴 300% of quota pace",
                                   "projected_final": 110.0, "resets_at": NOW.isoformat()}])
    texts = report["messages"] + [burn]
    assert len(texts) == 4
    for text in texts:
        assert check.problems(text, caption=True, lines=3) == [], text
    caption = morning.format_caption(today=NOW, left="Claude 98% · Codex 91%", tz=timezone.utc,
                                     alerts=[{"ts": NOW.isoformat(), "head": h} for h in heads(report)])
    assert check.problems(caption, caption=True, lines=3) == []


def test_each_live_alert_carries_its_plot_and_text_survives_a_photo_failure(tmp_path, monkeypatch):
    db, state = tmp_path / "q.sqlite3", tmp_path / "state.json"
    append_samples(db, spare_rows())
    photos, texts = [], []
    monkeypatch.setattr(alerts, "send_telegram_photo",
                        lambda png, caption, parse_mode: photos.append((png, caption)) or "error:400")
    monkeypatch.setattr(alerts, "send_telegram", lambda text, parse_mode=None: texts.append(text) or "sent")
    monkeypatch.setattr(alerts, "send_urgent", lambda text: "skip")
    report = run_alerts(path=db, state_file=state, now=NOW, include_reset_soon=True)
    assert report["delivery"] == "sent"
    assert len(photos) == 1 and photos[0][0].startswith(b"\x89PNG")
    assert photos[0][1] == texts[0]  # the plot failed; the same text still went out
    log = [json.loads(line) for line in (tmp_path / "alert-log.jsonl").read_text().splitlines()]
    assert [e["head"] for e in log] == ["use it before reset · Grok"]


def test_plot_png_is_a_png_and_none_without_data():
    rows = spare_rows()
    png = plot_png(rows, "grok", "p@example.test", now=NOW)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000
    assert plot_png([], "grok", None, now=NOW) is None


def test_morning_report_once_a_day_with_plots_and_yesterdays_alerts(tmp_path):
    db, state = tmp_path / "q.sqlite3", tmp_path / "state.json"
    append_samples(db, spare_rows() + spare_rows("codex", "c@example.test"))
    alerts.append_log([
        {"ts": (NOW - timedelta(days=2)).isoformat(), "head": "too old"},
        {"ts": (NOW - timedelta(hours=6)).isoformat(), "head": "use it before reset · Grok"},
    ], state)
    sent = []
    deliver = lambda caption, photos: sent.append((caption, photos)) or "sent"
    early_morning = NOW - timedelta(hours=2)  # 06:00 Moscow: not yet
    assert morning.run_morning(path=db, state_file=state, now=early_morning, sender=deliver)["delivery"] == "not-due"
    report = morning.run_morning(path=db, state_file=state, now=NOW, sender=deliver)
    assert report["delivery"] == "sent"
    caption, photos = sent[0]
    assert caption.split("\n")[:3] == ["morning quotas · Tue 3 Nov", "Codex 86% · Grok 86%", "1 alert since yesterday"]
    assert "use it before reset · Grok" in caption and "too old" not in caption
    assert len(photos) == 2 and all(p.startswith(b"\x89PNG") for p in photos)
    assert morning.run_morning(path=db, state_file=state, now=NOW + timedelta(hours=1),
                               sender=deliver)["delivery"] == "not-due"
    assert len(sent) == 1
    # The alert dedupe state keeps the report date; a manual preview does not.
    assert json.loads(state.read_text())["morning"] == "2026-11-03"
    fresh = tmp_path / "fresh.json"
    morning.run_morning(path=db, state_file=fresh, now=NOW, force=True, record=False, sender=deliver)
    assert "morning" not in alerts.load_state(fresh)


def test_caption_stays_under_the_telegram_limit():
    many = [{"ts": NOW.isoformat(), "head": f"use it before reset · Grok · login{i}"} for i in range(80)]
    caption = morning.format_caption(today=NOW, left="Claude 1%", alerts=many, tz=timezone.utc)
    from ai_quotas.alert_text import plain

    assert len(plain(caption)) <= 1024 and "more" in caption


def test_a_node_with_alerts_off_samples_without_sending(tmp_path, monkeypatch):
    from ai_quotas.notify import alerts_enabled

    monkeypatch.setenv("AI_QUOTAS_ALERTS", "off")
    assert alerts_enabled() is False
    monkeypatch.setenv("AI_QUOTAS_ALERTS", "on")
    assert alerts_enabled() is True
    calls = {}
    monkeypatch.setattr(alerts, "run_alerts", lambda **kw: calls.setdefault("send", kw["send"]) and {})
    monkeypatch.setattr(alerts, "ping_role", lambda role: "ok")
    out = run_after_sample(path=tmp_path / "q.sqlite3", send=False)
    assert calls["send"] is False and out["morning"] is None and out["healthchecks"] == "ok"
