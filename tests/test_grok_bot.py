"""Grok Bot is the Grok quota, not its own entity. Prepaid dollars are a balance, not a fake expiry."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_quotas import core
from ai_quotas.adapters import grok
from ai_quotas.cli import format_credit_balance_line

TS = "2026-10-09T09:40:00+00:00"
NOW = datetime(2026, 10, 9, 9, 40, tzinfo=timezone.utc)


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


def test_grok_bot_is_not_a_separate_entity(tmp_path):
    """Petr, 19 Oct 2026: "The grok bot quota == grok quota, so remove it"."""
    pytest.importorskip("pandas")
    from ai_quotas.collector import discover_adapters
    from ai_quotas.plots.prep import LABELS, PRIMARY_SERIES, VENDOR_SETUP, VENDORS
    from ai_quotas.storage import append_samples

    assert "grok_bot" not in discover_adapters()
    assert "Grok Bot" not in VENDORS and "Grok Bot" not in VENDOR_SETUP
    assert "grok-bot/week" not in LABELS and "Grok Bot" not in PRIMARY_SERIES
    db = tmp_path / "q.sqlite3"
    append_samples(db, [
        {"ts": TS, "provider": "grok-bot", "window": "week", "used_percent": 1.0, "status": "ok"},
        {"ts": TS, "provider": "grok", "window": "week", "used_percent": 9.0, "status": "ok"},
    ])
    assert {r["provider"] for r in core.load_samples(db)} == {"grok"}
