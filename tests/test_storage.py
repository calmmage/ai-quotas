"""SQLite storage, compatibility, and idempotent migration."""

from __future__ import annotations

import json
from pathlib import Path

from ai_quotas.storage import (
    append_samples,
    append_spend,
    import_jsonl,
    integrity_check,
    load_harvest_cursor,
    load_samples,
    load_spend,
    row_counts,
    save_harvest_cursor,
    schema_version,
)


def _sample(ts: str, used: float) -> dict:
    return {
        "ts": ts,
        "provider": "codex",
        "window": "week",
        "used_percent": used,
        "resets_at": None,
        "plan": None,
        "status": "ok",
        "reason": None,
        "limit": None,
        "used": None,
        "extra": {"lossless": True},
    }


def _spend(turn_id: str) -> dict:
    return {
        "kind": "turn",
        "provider": "codex",
        "session_id": "session-1",
        "turn_id": turn_id,
        "ts": "2026-08-23T00:00:00+00:00",
        "total_tokens": 42,
        "cost_usd": None,
    }


def test_sqlite_round_trip_and_cursor(tmp_path: Path):
    db = tmp_path / "ai-quotas.sqlite3"
    samples = [_sample("2026-08-23T00:00:00+00:00", 10.0)]
    assert append_samples(db, samples) == 1
    assert append_spend(db, [_spend("turn-1")]) == 1
    assert append_spend(db, [_spend("turn-1")]) == 0
    save_harvest_cursor(
        db,
        {
            "updated_at": "2026-08-23T00:01:00+00:00",
            "files": {"/tmp/source.jsonl": {"mtime_ns": 7, "size": 9, "n_new": 1}},
        },
    )

    assert load_samples(db) == samples
    assert load_spend(db) == [_spend("turn-1")]
    assert load_harvest_cursor(db)["files"]["/tmp/source.jsonl"]["size"] == 9
    assert row_counts(db) == {"samples": 1, "spend": 1, "harvest_files": 1}
    assert schema_version(db) == 1
    assert integrity_check(db) == "ok"


def test_jsonl_compatibility(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    rows = [_sample("2026-08-23T00:00:00+00:00", 10.0)]
    assert append_samples(path, rows) == 1
    assert load_samples(path) == rows


def test_legacy_import_is_incremental_and_idempotent(tmp_path: Path):
    source = tmp_path / "samples.jsonl"
    db = tmp_path / "ai-quotas.sqlite3"
    first = _sample("2026-08-23T00:00:00+00:00", 10.0)
    second = _sample("2026-08-23T01:00:00+00:00", 11.0)
    source.write_text(json.dumps(first) + "\n", encoding="utf-8")

    report1 = import_jsonl(db, source, kind="samples")
    report2 = import_jsonl(db, source, kind="samples")
    with source.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(second) + "\n")
    report3 = import_jsonl(db, source, kind="samples")

    assert report1["imported"] == 1
    assert report2["skipped"] == 1
    assert report3["imported"] == 1
    assert report3["skipped"] == 1
    assert load_samples(db) == [first, second]


def test_legacy_import_rejects_changed_line(tmp_path: Path):
    source = tmp_path / "samples.jsonl"
    db = tmp_path / "ai-quotas.sqlite3"
    source.write_text(json.dumps(_sample("2026-08-23T00:00:00+00:00", 10.0)) + "\n")
    import_jsonl(db, source, kind="samples")
    source.write_text(json.dumps(_sample("2026-08-23T00:00:00+00:00", 99.0)) + "\n")
    report = import_jsonl(db, source, kind="samples")
    assert report["rejected"] == 1
    assert row_counts(db)["samples"] == 1
