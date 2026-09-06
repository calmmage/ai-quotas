"""Temporary limit boosts: parser, storage upsert, lifecycle, CLI --json."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_quotas.adapters import claude as claude_adapter
from ai_quotas.boosts import (
    boost_badge,
    boost_row,
    boost_states,
    extract_boosts,
    format_boost_line,
    parse_boost_text,
    visible_boosts,
)
from ai_quotas.collector import sample_all_split
from ai_quotas.storage import (
    append_samples,
    load_boosts,
    row_counts,
    schema_version,
    upsert_boosts,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------- parser


def test_parse_boost_text_percent_and_through_date():
    got = parse_boost_text(
        "limits temporarily boosted +50% through 13 Sep", now=NOW
    )
    assert got is not None
    assert got["percent"] == 50.0
    assert got["window"] == "week"
    ends = datetime.fromisoformat(got["ends_at"])
    assert ends.year == 2026 and ends.month == 9 and ends.day == 14
    # 13 Sep 23:59:59 PT = 14 Sep 06:59:59 UTC (PDT)


def test_extract_boosts_from_structured_payload():
    data = {
        "limits": [{"kind": "weekly_all", "percent": 12, "resets_at": "x"}],
        "boost": {
            "percent": 50,
            "through": "13 Sep",
            "window": "week",
            "text": "limits temporarily boosted +50% through 13 Sep",
        },
    }
    found = extract_boosts(data, now=NOW)
    assert len(found) == 1
    assert found[0]["percent"] == 50.0
    assert found[0]["raw_text"].startswith("limits temporarily")


def test_extract_boosts_from_notice_text_only():
    data = {
        "notices": [{"text": "Your limits temporarily boosted 50% through 13 Sep"}]
    }
    found = extract_boosts(data, now=NOW)
    assert len(found) == 1
    assert found[0]["percent"] == 50.0


def test_extract_boosts_empty_on_oauth_usage_shape():
    data = {
        "five_hour": {"utilization": 10, "resets_at": "x"},
        "seven_day": {"utilization": 20, "resets_at": "y"},
        "limits": [
            {"kind": "session", "percent": 10, "resets_at": "x"},
            {"kind": "weekly_all", "percent": 20, "resets_at": "y"},
        ],
        "extra_usage": {"is_enabled": False, "utilization": 0},
    }
    assert extract_boosts(data, now=NOW) == []


def test_claude_adapter_emits_boost_when_payload_has_perk():
    ts = _ts(NOW)
    rows = claude_adapter._boost_rows(
        ts,
        {"notices": [{"text": "limits temporarily boosted +50% through 13 Sep"}]},
    )
    assert len(rows) == 1
    assert rows[0]["kind"] == "boost"
    assert rows[0]["provider"] == "claude"
    assert rows[0]["percent"] == 50.0
    assert rows[0]["window"] == "week"


def test_claude_adapter_silent_when_no_perk():
    assert claude_adapter._boost_rows(_ts(NOW), {"limits": []}) == []


# ---------------------------------------------------------------- storage + lifecycle


def test_upsert_extends_last_seen_without_duplicate(tmp_path: Path):
    db = tmp_path / "q.sqlite3"
    t0 = NOW - timedelta(hours=3)
    t1 = NOW - timedelta(hours=1)
    row0 = boost_row(
        _ts(t0),
        "claude",
        percent=50,
        ends_at="2026-09-14T06:59:59+00:00",
        raw_text="limits temporarily boosted +50% through 13 Sep",
    )
    row1 = boost_row(
        _ts(t1),
        "claude",
        percent=50,
        ends_at="2026-09-14T06:59:59+00:00",
        raw_text="limits temporarily boosted +50% through 13 Sep",
    )
    assert upsert_boosts(db, [row0]) == 1
    assert upsert_boosts(db, [row1]) == 1
    assert schema_version(db) == 3
    assert row_counts(db)["boosts"] == 1
    stored = load_boosts(db)
    assert len(stored) == 1
    assert stored[0]["first_seen_ts"] == _ts(t0)
    assert stored[0]["last_seen_ts"] == _ts(t1)


def test_lifecycle_active_ended_vanished(tmp_path: Path):
    db = tmp_path / "q.sqlite3"
    ends = NOW + timedelta(days=9)
    active_row = boost_row(
        _ts(NOW - timedelta(minutes=10)),
        "claude",
        percent=50,
        ends_at=_ts(ends),
        raw_text="limits temporarily boosted +50% through 13 Sep",
    )
    upsert_boosts(db, [active_row])
    stored = load_boosts(db)
    states = boost_states(stored, now=NOW, checked_at=NOW)
    assert states[0]["status"] == "active"
    assert visible_boosts(stored, now=NOW)
    assert format_boost_line(visible_boosts(stored, now=NOW)).startswith("boosts: claude")
    assert "+50%" in boost_badge(states, "claude")

    ended_row = boost_row(
        _ts(NOW - timedelta(days=2)),
        "claude",
        window="week",
        percent=25,
        ends_at=_ts(NOW - timedelta(days=1)),
        raw_text="boosted +25% through 03 Sep",
    )
    upsert_boosts(db, [ended_row])
    stored = load_boosts(db)
    by_pct = {s["percent"]: s for s in boost_states(stored, now=NOW, checked_at=NOW)}
    assert by_pct[25.0]["status"] == "ended"
    vis = visible_boosts(stored, now=NOW)
    assert any(s["percent"] == 25.0 for s in vis)

    vanished_row = boost_row(
        _ts(NOW - timedelta(hours=6)),
        "claude",
        percent=10,
        ends_at=_ts(NOW + timedelta(days=5)),
        raw_text="boosted +10% through 09 Sep",
    )
    upsert_boosts(db, [vanished_row])
    stored = load_boosts(db)
    by_pct = {s["percent"]: s for s in boost_states(stored, now=NOW, checked_at=NOW)}
    assert by_pct[10.0]["status"] == "vanished"
    vis = visible_boosts(stored, now=NOW)
    assert all(s["percent"] != 10.0 for s in vis)


def test_collector_splits_boost_rows(tmp_path: Path):
    def fake(ts: str) -> list[dict]:
        return [
            {"provider": "claude", "window": "week", "used_percent": 10.0, "status": "ok"},
            boost_row(ts, "claude", percent=50, ends_at="2026-09-14T06:59:59+00:00"),
        ]

    quota, credits, boosts = sample_all_split(_ts(NOW), adapters={"claude": fake})
    assert len(quota) == 1 and credits == [] and len(boosts) == 1
    db = tmp_path / "q.sqlite3"
    upsert_boosts(db, boosts)
    assert load_boosts(db)[0]["percent"] == 50.0


# ---------------------------------------------------------------- CLI --json


def test_cli_json_exposes_boosts(tmp_path: Path):
    db = tmp_path / "q.sqlite3"
    wall = datetime.now(timezone.utc)
    ts = _ts(wall - timedelta(minutes=5))
    append_samples(
        db,
        [
            {
                "ts": ts,
                "provider": "claude",
                "window": "week",
                "used_percent": 12.0,
                "resets_at": _ts(wall + timedelta(days=3)),
                "status": "ok",
                "reason": None,
            }
        ],
    )
    upsert_boosts(
        db,
        [
            boost_row(
                ts,
                "claude",
                percent=50,
                ends_at=_ts(wall + timedelta(days=9)),
                raw_text="limits temporarily boosted +50% through 13 Sep",
            )
        ],
    )
    env = {**os.environ, "AI_QUOTAS_DATABASE": str(db)}
    proc = subprocess.run(
        [sys.executable, "-m", "ai_quotas", "--json", "--no-refresh"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert "boosts" in data
    assert len(data["boosts"]) == 1
    assert data["boosts"][0]["provider"] == "claude"
    assert data["boosts"][0]["percent"] == 50.0
    assert data["boosts"][0]["status"] == "active"
