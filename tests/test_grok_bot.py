"""Grok Bot is its own week. Prepaid dollars are a balance, not a fake expiry."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_quotas import core
from ai_quotas.adapters import grok, grok_bot
from ai_quotas.cli import format_credit_balance_line

TS = "2026-10-09T09:40:00+00:00"
NOW = datetime(2026, 10, 9, 9, 40, tzinfo=timezone.utc)


def _cursor_payload(*, bot=True, used=0.644955, account="petr.b.lavrov@gmail.com"):
    usage = {
        "accountEmail": account,
        "identity": {"accountEmail": account},
        "primary": {
            "usedPercent": 0,
            "windowMinutes": 43200,
            "resetsAt": "2026-10-14T22:01:55Z",
        },
        "extraRateWindows": [],
    }
    if bot:
        usage["extraRateWindows"].append({
            "id": "cursor-grok-bot",
            "title": "Grok Bot",
            "window": {
                "usedPercent": used,
                "resetsAt": "2026-10-09T22:04:21Z",
                "windowMinutes": 10080,
            },
        })
    return [{"provider": "cursor", "source": "cursor", "usage": usage}]


def test_bot_window_is_kept_and_cursor_plan_bars_are_not():
    rows = grok_bot.snapshot(TS, codexbar_json=json.dumps(_cursor_payload()))
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "grok-bot"
    assert row["window"] == "week"
    assert row["status"] == "ok"
    assert row["used_percent"] == 0.644955
    assert row["used_percent"] not in (0, 1)
    assert row["resets_at"] == "2026-10-09T22:04:21+00:00"
    assert row["account"] == "petr.b.lavrov@gmail.com"
    assert row.get("kind") != "reset_credit"


def test_real_zero_on_the_bot_window_stays_zero():
    rows = grok_bot.snapshot(TS, codexbar_json=json.dumps(_cursor_payload(used=0)))
    assert rows[0]["status"] == "ok"
    assert rows[0]["used_percent"] == 0.0


def test_missing_bot_window_is_unknown_not_the_cursor_zero():
    rows = grok_bot.snapshot(TS, codexbar_json=json.dumps(_cursor_payload(bot=False)))
    assert len(rows) == 1
    assert rows[0]["status"] == "unavailable"
    assert rows[0]["used_percent"] is None
    assert rows[0]["provider"] == "grok-bot"
    assert "no Grok Bot window" in rows[0]["reason"]


def test_cursor_error_payload_is_an_error():
    payload = {"error": {"code": 1, "message": "No available fetch strategy for xai."}}
    rows = grok_bot.snapshot(TS, codexbar_json=json.dumps(payload))
    assert rows[0]["status"] == "error"
    assert rows[0]["used_percent"] is None
    assert "fetch strategy" in rows[0]["reason"]


def test_invalid_fixture_and_missing_binary_do_not_invent_zero(tmp_path):
    bad = grok_bot.snapshot(TS, codexbar_json="{")
    assert bad[0]["status"] == "error"
    assert bad[0]["used_percent"] is None
    missing = grok_bot.snapshot(TS, codexbar_bin=str(tmp_path / "no-codexbar"))
    assert missing[0]["status"] == "unavailable"
    assert missing[0]["used_percent"] is None
    assert missing[0]["provider"] == "grok-bot"


def test_timeout_is_an_error_not_zero(tmp_path, monkeypatch):
    binary = tmp_path / "codexbar"
    binary.write_text("#!/bin/sh\nsleep 5\n")
    binary.chmod(0o755)
    monkeypatch.setattr(grok_bot, "CODEXBAR_TIMEOUT_S", 0.2)
    rows = grok_bot.snapshot(TS, codexbar_bin=str(binary))
    assert rows[0]["status"] == "error"
    assert rows[0]["used_percent"] is None
    assert "timed out" in rows[0]["reason"]


def test_prepaid_cents_become_dollars_and_a_missing_key_is_not_zero():
    row = grok._prepaid_balance_row(TS, {"prepaidBalance": {"val": 1000}})
    assert row["window"] == "credits_balance"
    assert row["status"] == "ok"
    assert row["used_percent"] is None
    assert row["remaining"] == 10
    assert row["unit"] == "usd"
    assert row["expiry"] == "unknown"
    assert "expires_at" not in row
    assert grok._prepaid_balance_row(TS, {"creditUsagePercent": 8}) is None
    empty = grok._prepaid_balance_row(TS, {"prepaidBalance": {}})
    assert empty["status"] == "ok"
    assert empty["remaining"] == 0


def test_grant_list_copies_expiry_and_an_absent_list_emits_nothing():
    cfg = {
        "prepaidGrants": [{
            "id": "grant-1",
            "title": "Top up",
            "amount": {"val": 2500},
            "grantedAt": "2026-10-01T00:00:00Z",
            "expiresAt": "2026-10-16T00:00:00Z",
        }]
    }
    rows = grok._credit_grant_rows(TS, cfg)
    assert len(rows) == 1
    row = rows[0]
    assert row["window"] == "credit_grant"
    assert row["used_percent"] is None
    assert row["remaining"] == 25
    assert row["unit"] == "usd"
    assert row["credit_id"] == "grant-1"
    assert row["expires_at"] == "2026-10-16T00:00:00Z"
    assert row["resets_at"] == "2026-10-16T00:00:00Z"
    assert row.get("kind") != "reset_credit"
    assert grok._credit_grant_rows(TS, {"prepaidBalance": {"val": 1000}}) == []
    assert grok._credit_grant_rows(TS, {"grants": []}) == []


def test_snapshot_records_prepaid_and_grants(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    cfg = {
        "creditUsagePercent": 8,
        "currentPeriod": {"end": "2026-10-15T02:23:40+00:00"},
        "prepaidBalance": {"val": 1000},
        "creditGrants": [{
            "credit_id": "g9",
            "remaining": {"val": 500},
            "expires_at": "2026-10-12T00:00:00Z",
        }],
    }
    monkeypatch.setattr(grok, "auth_path", lambda: auth)
    monkeypatch.setattr(grok, "_get_access_token", lambda: "fixture-token")
    monkeypatch.setattr(grok, "_http_get_json_retry", lambda url, token: (token, {"config": cfg}))
    monkeypatch.setattr(grok, "_reset_credit_rows", lambda ts, token: [])
    monkeypatch.setattr(grok, "_auth_account", lambda: "petr.b.lavrov@gmail.com")
    rows = grok.snapshot(TS)
    by_window = {row["window"]: row for row in rows}
    assert by_window["week"]["used_percent"] == 8
    assert by_window["credits_balance"]["remaining"] == 10
    assert by_window["credits_balance"]["expiry"] == "unknown"
    assert by_window["credit_grant"]["credit_id"] == "g9"
    assert by_window["credit_grant"]["remaining"] == 5
    assert all(row["account"] == "petr.b.lavrov@gmail.com" for row in rows)
    assert all(row["provider"] == "grok" for row in rows)


def test_credit_balance_line_names_dollars_and_a_grant_date():
    rows = [
        {
            "ts": TS, "provider": "grok", "window": "credits_balance", "status": "ok",
            "remaining": 10, "unit": "usd", "expiry": "unknown",
        },
        {
            "ts": TS, "provider": "grok", "window": "credit_grant", "status": "ok",
            "remaining": 5, "expires_at": "2026-10-12T12:00:00+00:00", "credit_id": "g9",
        },
        {
            "ts": TS, "provider": "codex", "window": "credits_balance", "status": "ok",
            "remaining": 62500, "unit": "credits",
        },
    ]
    line = format_credit_balance_line(rows, now=NOW)
    assert line.startswith("credits: ")
    assert "codex 62,500 credits" in line
    assert "grok $10 (exp 12 Oct, 3d)" in line
    assert "no expiry published" not in line


def test_pick_ignores_grok_bot_even_when_it_is_the_only_open_pool():
    result = {"windows": [
        {"provider": "grok-bot", "window": "week", "verdict": "OK", "used_percent": 1},
        {"provider": "grok", "window": "week", "verdict": "STOP", "used_percent": 90},
        {"provider": "claude", "window": "week", "verdict": "STOP", "used_percent": 90},
        {"provider": "codex", "window": "week", "verdict": "STOP", "used_percent": 90},
    ]}
    assert "grok-bot" not in core.DEFAULT_PICK_CANDIDATES
    picked = core.pick_harness(result)
    assert picked["harness"] is None
    assert all(row["harness"] != "grok-bot" for row in picked["ok"] + picked["skipped"])


def test_grok_bot_is_its_own_panel_and_is_not_priced_as_grok():
    pytest.importorskip("pandas")
    from ai_quotas.plots.prep import (
        LABELS, PRIMARY_SERIES, VENDOR_OF, VENDORS, window_usd_value,
    )

    assert LABELS["grok-bot/week"] == "Grok Bot week"
    assert VENDOR_OF["Grok Bot week"] == "Grok Bot"
    assert VENDOR_OF["Grok week"] == "Grok"
    assert PRIMARY_SERIES["Grok Bot"] == "Grok Bot week"
    assert PRIMARY_SERIES["Grok"] == "Grok week"
    assert VENDORS.index("Grok Bot") == VENDORS.index("Grok") + 1
    assert window_usd_value("Grok Bot week", "Grok Bot", plan="ultra")[0] == 0
