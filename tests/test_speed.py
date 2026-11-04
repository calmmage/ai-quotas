"""Tokens/sec collector: merge, tiers, noise, and resume."""

from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ai_quotas.plots.dash import make_server
from ai_quotas.speed.scan import scan
from ai_quotas.speed.stats import summary
from ai_quotas.speed.store import connect, load_file_state, load_rows
from ai_quotas.storage import SCHEMA_VERSION, ensure_database, schema_version

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)


def _line(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":")) + "\n"


def _claude(message_id: str, output: int, *, seconds: float, tier: str = "standard", tools: int = 0) -> str:
    user = {
        "type": "user",
        "timestamp": "2026-11-04T12:00:00+03:00",
        "sessionId": "s1",
        "cwd": "/tmp/proj",
        "message": {"role": "user", "content": "hi"},
    }
    end = f"2026-11-04T12:00:{int(seconds):02d}+03:00"
    lines = [_line(user)]
    for index in range(max(tools, 1)):
        content = [{"type": "tool_use"}] if tools else [{"type": "text"}]
        # The last line carries the final usage. Earlier lines stay lower.
        tokens = output if index == max(tools, 1) - 1 else max(1, output // 2)
        lines.append(
            _line(
                {
                    "type": "assistant",
                    "timestamp": end,
                    "sessionId": "s1",
                    "cwd": "/tmp/proj",
                    "effort": "high",
                    "message": {
                        "id": message_id,
                        "model": "claude-opus-5",
                        "content": content,
                        "usage": {
                            "output_tokens": tokens,
                            "output_tokens_details": {"thinking_tokens": 4},
                            "speed": tier,
                            "service_tier": "standard",
                        },
                    },
                }
            )
        )
    return "".join(lines)


def test_claude_multiline_message_merges_usage_once(tmp_path: Path):
    log = tmp_path / "claude.jsonl"
    user = _line(
        {
            "type": "user",
            "timestamp": "2026-11-04T12:00:00+03:00",
            "sessionId": "s1",
            "cwd": "/tmp/proj",
            "message": {"role": "user", "content": "please-do-not-store-this-sentence"},
        }
    )
    parts = []
    for stamp, tokens, thinking, blocks in (
        ("2026-11-04T12:00:02+03:00", 30, 5, ["thinking"]),
        ("2026-11-04T12:00:05+03:00", 80, 20, ["text"]),
        ("2026-11-04T12:00:06+03:00", 120, 40, ["tool_use"]),
    ):
        parts.append(
            _line(
                {
                    "type": "assistant",
                    "timestamp": stamp,
                    "sessionId": "s1",
                    "cwd": "/tmp/proj",
                    "effort": "high",
                    "message": {
                        "id": "m1",
                        "model": "claude-opus-5",
                        "content": [{"type": block} for block in blocks],
                        "usage": {
                            "output_tokens": tokens,
                            "output_tokens_details": {"thinking_tokens": thinking},
                            "speed": "fast",
                            "service_tier": "standard",
                        },
                    },
                }
            )
        )
    log.write_text(user + "".join(parts))
    db = tmp_path / "ai-quotas.sqlite3"
    ensure_database(db)
    scan(db, files=[("claude", log)], progress=False, node="test-mac")
    assert schema_version(db) == SCHEMA_VERSION == 4
    with connect(db) as conn:
        rows = load_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["response_key"] == "claude:m1"
    assert row["output_tokens"] == 120
    assert row["reasoning_tokens"] == 40
    assert row["tier"] == "fast"
    assert row["effort"] == "high"
    assert row["provider"] == "anthropic"
    assert row["node"] == "test-mac"
    assert row["seconds"] == 6
    assert row["cwd"] == "/tmp/proj"
    assert "please-do-not-store-this-sentence" not in json.dumps(row)


def test_codex_tier_detection_default_priority_ultrafast(tmp_path: Path):
    log = tmp_path / "rollout-2026-11-04T12-00-00-11111111-1111-1111-1111-111111111111.jsonl"
    lines = [
        _line({"timestamp": "2026-11-04T09:00:00.000Z", "type": "session_meta", "payload": {"id": "sess", "cwd": "/work", "model_provider": "openai"}}),
    ]
    for tier, response_id, minute in (("default", "r-default", 1), ("priority", "r-priority", 2), ("ultrafast", "r-ultra", 3)):
        lines.append(
            _line(
                {
                    "timestamp": f"2026-11-04T09:0{minute}:00.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "thread_settings_applied",
                        "thread_settings": {
                            "model": "gpt-6.1-sol",
                            "service_tier": tier,
                            "reasoning_effort": "high",
                        },
                    },
                }
            )
        )
        lines.append(
            _line(
                {
                    "timestamp": f"2026-11-04T09:0{minute}:01.000Z",
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user"},
                }
            )
        )
        lines.append(
            _line(
                {
                    "timestamp": f"2026-11-04T09:0{minute}:04.000Z",
                    "type": "token_usage_record",
                    "payload": {
                        "response_id": response_id,
                        "usage": {"output_tokens": 200, "reasoning_output_tokens": 50, "input_tokens": 10, "total_tokens": 210},
                    },
                }
            )
        )
        lines.append(
            _line(
                {
                    "timestamp": f"2026-11-04T09:0{minute}:04.010Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {"output_tokens": 200, "reasoning_output_tokens": 50},
                            "last_usage_includes_reasoning": True,
                        },
                    },
                }
            )
        )
    log.write_text("".join(lines))
    db = tmp_path / "ai-quotas.sqlite3"
    scan(db, files=[("codex", log)], progress=False)
    with connect(db) as conn:
        rows = load_rows(conn)
    assert [row["tier"] for row in rows] == ["standard", "priority", "ultrafast"]
    assert [row["response_key"] for row in rows] == ["codex:r-default", "codex:r-priority", "codex:r-ultra"]
    assert all(row["model"] == "gpt-6.1-sol" and row["effort"] == "high" for row in rows)
    assert all(row["output_tokens"] == 200 and row["reasoning_tokens"] == 50 for row in rows)
    assert rows[0]["seconds"] == 3


def test_noise_filter_keeps_rows_out_of_stats(tmp_path: Path):
    log = tmp_path / "claude.jsonl"
    short_tokens = _claude("tiny", 10, seconds=5)
    short_time = _claude("blink", 100, seconds=0)
    # 0 seconds is not a valid clock; use an explicit 0-second-looking pair via raw lines.
    blink = _line(
        {
            "type": "user",
            "timestamp": "2026-11-04T12:00:00.000+03:00",
            "sessionId": "s1",
            "message": {"role": "user", "content": "x"},
        }
    ) + _line(
        {
            "type": "assistant",
            "timestamp": "2026-11-04T12:00:00.200+03:00",
            "sessionId": "s1",
            "message": {
                "id": "blink",
                "model": "claude-opus-5",
                "usage": {"output_tokens": 100, "speed": "standard"},
            },
        }
    )
    good = _claude("good", 100, seconds=5)
    # Separate files so message merges do not collide on one anchor stream.
    files = []
    for name, text in (("a.jsonl", short_tokens), ("b.jsonl", blink), ("c.jsonl", good)):
        path = tmp_path / name
        path.write_text(text)
        files.append(("claude", path))
    del short_time
    db = tmp_path / "ai-quotas.sqlite3"
    scan(db, files=files, progress=False)
    with connect(db) as conn:
        stored = load_rows(conn)
    assert {row["response_key"] for row in stored} == {"claude:tiny", "claude:blink", "claude:good"}
    body = summary(db, now=NOW)["windows"]["24h"]
    assert [(row["model"], row["n"]) for row in body["rows"]] == [("claude-opus-5", 1)]
    assert body["rows"][0]["p50"] == 20.0  # 100 tokens / 5s


def test_incremental_offset_resume(tmp_path: Path):
    log = tmp_path / "claude.jsonl"
    db = tmp_path / "ai-quotas.sqlite3"
    first = _claude("one", 100, seconds=4)
    log.write_text(first)
    scan(db, files=[("claude", log)], progress=False)
    with connect(db) as conn:
        cursor = load_file_state(conn, str(log))
        assert cursor is not None
        assert cursor["offset"] == len(first.encode())
        assert len(load_rows(conn)) == 1
    again = scan(db, files=[("claude", log)], progress=False)
    assert again["skipped_unchanged"] == 1
    assert again["scanned_files"] == 0
    second_user = _line(
        {
            "type": "user",
            "timestamp": "2026-11-04T13:00:00+03:00",
            "sessionId": "s1",
            "message": {"role": "user", "content": "next"},
        }
    )
    second_assistant = _line(
        {
            "type": "assistant",
            "timestamp": "2026-11-04T13:00:02+03:00",
            "sessionId": "s1",
            "message": {
                "id": "two",
                "model": "claude-opus-5",
                "usage": {"output_tokens": 80, "speed": "standard"},
            },
        }
    )
    log.write_text(first + second_user + second_assistant)
    scan(db, files=[("claude", log)], progress=False)
    with connect(db) as conn:
        rows = load_rows(conn)
        cursor = load_file_state(conn, str(log))
    assert [row["response_key"] for row in rows] == ["claude:one", "claude:two"]
    assert rows[0]["output_tokens"] == 100
    assert cursor["offset"] == log.stat().st_size


def test_parallel_and_compaction_are_marked_and_skipped(tmp_path: Path):
    log = tmp_path / "claude.jsonl"
    lines = [
        _line({"type": "user", "timestamp": "2026-11-04T12:00:00+03:00", "sessionId": "s1", "message": {"role": "user", "content": "go"}}),
        _line({"type": "assistant", "timestamp": "2026-11-04T12:00:02+03:00", "sessionId": "s1", "message": {"id": "tools", "model": "claude-opus-5", "content": [{"type": "tool_use"}], "usage": {"output_tokens": 40, "speed": "standard"}}}),
        _line({"type": "assistant", "timestamp": "2026-11-04T12:00:02+03:00", "sessionId": "s1", "message": {"id": "tools", "model": "claude-opus-5", "content": [{"type": "tool_use"}], "usage": {"output_tokens": 40, "speed": "standard"}}}),
        _line({"type": "user", "timestamp": "2026-11-04T12:00:03+03:00", "sessionId": "s1", "message": {"role": "user", "content": [{"type": "tool_result"}]}}),
        _line({"type": "user", "timestamp": "2026-11-04T12:00:03.500+03:00", "sessionId": "s1", "message": {"role": "user", "content": [{"type": "tool_result"}]}}),
        _line({"type": "assistant", "timestamp": "2026-11-04T12:00:05+03:00", "sessionId": "s1", "message": {"id": "after", "model": "claude-opus-5", "usage": {"output_tokens": 50, "speed": "standard"}}}),
        _line({"type": "user", "timestamp": "2026-11-04T12:10:00+03:00", "sessionId": "s1", "isCompactSummary": True, "message": {"role": "user", "content": "summary"}}),
        _line({"type": "assistant", "timestamp": "2026-11-04T12:10:03+03:00", "sessionId": "s1", "message": {"id": "sum", "model": "claude-opus-5", "usage": {"output_tokens": 60, "speed": "standard"}}}),
    ]
    log.write_text("".join(lines))
    db = tmp_path / "speed.sqlite3"
    scan(db, files=[("claude", log)], progress=False)
    with connect(db) as conn:
        rows = {row["response_key"]: row for row in load_rows(conn)}
    assert rows["claude:tools"]["parallel_tools"] == 0
    assert rows["claude:after"]["parallel_tools"] == 1
    assert rows["claude:sum"]["compaction"] == 1
    counted = summary(db, now=NOW)["windows"]["24h"]["rows"]
    assert counted == [] or all(row["n"] == 1 for row in counted)
    # The issuing response is eligible. The follow-ups are marked.
    assert any(row["model"] == "claude-opus-5" and row["n"] == 1 for row in counted)


def test_speed_page_serves_the_table(tmp_path: Path):
    log = tmp_path / "claude.jsonl"
    log.write_text(_claude("m", 100, seconds=5, tier="fast"))
    db = tmp_path / "ai-quotas.sqlite3"
    scan(db, files=[("claude", log)], progress=False)
    plots = tmp_path / "plots"
    plots.mkdir()
    server = make_server(plots, 0)
    server.speed_database = db
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/speed", timeout=5).read().decode()
        assert "End-to-end" in page
        assert ">Speed<" in page
        payload = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/speed?window=7d", timeout=5))
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert payload["rows"][0]["tier"] == "fast"
    assert payload["rows"][0]["p50"] == 20.0
    assert payload["compare"][0]["fast"]["n"] == 1
