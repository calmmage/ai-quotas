"""Session spend harvest — grok/claude/codex parsers, incremental, summarize."""

from __future__ import annotations

import json
from pathlib import Path

from ai_quotas.spend import (
    GROK_TICKS_PER_USD,
    harvest,
    load_spend,
    parse_claude_transcript,
    parse_codex_rollout,
    parse_grok_updates,
    session_rollups,
    summarize,
    turn_key,
)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_grok_turn_completed_cost(tmp_path: Path):
    updates = tmp_path / "sess-a" / "updates.jsonl"
    ticks = 1_456_000_000  # 0.1456 USD at GROK_TICKS_PER_USD
    _write(
        updates,
        [
            {
                "timestamp": 1786819170,
                "params": {
                    "sessionId": "sess-a",
                    "update": {
                        "sessionUpdate": "agent_message",
                        "text": "ignore me",
                    },
                },
            },
            {
                "timestamp": 1786819170,
                "params": {
                    "sessionId": "sess-a",
                    "update": {
                        "sessionUpdate": "turn_completed",
                        "prompt_id": "p1",
                        "usage": {
                            "inputTokens": 1263971,
                            "cachedReadTokens": 1145344,
                            "outputTokens": 7790,
                            "reasoningTokens": 5766,
                            "totalTokens": 1271761,
                            "modelCalls": 15,
                            "apiDurationMs": 137000,
                            "costUsdTicks": ticks,
                            "modelUsage": {"grok-4.6-build": {}},
                        },
                    },
                },
            },
        ],
    )
    rows = parse_grok_updates(updates, "sess-a")
    assert len(rows) == 1
    r = rows[0]
    assert r["provider"] == "grok"
    assert r["session_id"] == "sess-a"
    assert r["turn_id"] == "p1"
    assert r["input_tokens"] == 1263971
    assert r["cached_tokens"] == 1145344
    assert r["output_tokens"] == 7790
    assert r["reasoning_tokens"] == 5766
    assert r["model_calls"] == 15
    assert r["cost_usd"] is not None
    assert abs(r["cost_usd"] - 0.1456) < 1e-9
    assert r["model"] == "grok-4.6-build"


def test_claude_dedupes_same_message_id(tmp_path: Path):
    transcript = tmp_path / "abc.jsonl"
    usage = {
        "input_tokens": 10,
        "cache_read_input_tokens": 100,
        "output_tokens": 20,
        "costUSD": 0,
    }
    _write(
        transcript,
        [
            {
                "timestamp": "2026-08-16T12:00:00Z",
                "message": {"id": "msg_1", "model": "claude-opus", "usage": usage},
            },
            {
                "timestamp": "2026-08-16T12:00:01Z",
                "message": {"id": "msg_1", "model": "claude-opus", "usage": usage},
            },
            {
                "timestamp": "2026-08-16T12:01:00Z",
                "message": {
                    "id": "msg_2",
                    "model": "claude-opus",
                    "usage": {
                        "input_tokens": 5,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 7,
                    },
                },
            },
        ],
    )
    rows = parse_claude_transcript(transcript, "abc")
    assert {r["turn_id"] for r in rows} == {"msg_1", "msg_2"}
    msg1 = next(r for r in rows if r["turn_id"] == "msg_1")
    assert msg1["cached_tokens"] == 100
    assert msg1["total_tokens"] == 130
    assert msg1["cost_usd"] is None  # 0 on subscription → unknown


def test_codex_last_token_usage(tmp_path: Path):
    rollout = (
        tmp_path
        / "rollout-2026-08-13T10-34-02-019ffa0a-d9b8-7163-aa6d-c9238dd86b91.jsonl"
    )
    _write(
        rollout,
        [
            {
                "timestamp": "2026-08-13T07:34:10.686Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 67821,
                            "cached_input_tokens": 9728,
                            "output_tokens": 334,
                            "reasoning_output_tokens": 200,
                            "total_tokens": 68155,
                        },
                        "last_token_usage": {
                            "input_tokens": 20727,
                            "cached_input_tokens": 4864,
                            "output_tokens": 112,
                            "reasoning_output_tokens": 49,
                            "total_tokens": 20839,
                        },
                    },
                },
            }
        ],
    )
    rows = parse_codex_rollout(rollout, "ignored")
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 20727
    assert rows[0]["reasoning_tokens"] == 49
    assert rows[0]["cost_usd"] is None


def test_harvest_incremental(tmp_path: Path):
    grok = tmp_path / "grok" / "sess-a" / "updates.jsonl"
    _write(
        grok,
        [
            {
                "timestamp": 1786819170,
                "params": {
                    "sessionId": "sess-a",
                    "update": {
                        "sessionUpdate": "turn_completed",
                        "prompt_id": "p1",
                        "usage": {
                            "inputTokens": 10,
                            "outputTokens": 2,
                            "totalTokens": 12,
                            "cachedReadTokens": 0,
                            "reasoningTokens": 0,
                            "modelCalls": 1,
                            "apiDurationMs": 10,
                            "costUsdTicks": 100000000,
                        },
                    },
                },
            }
        ],
    )
    dest = tmp_path / "data" / "spend.jsonl"
    first = harvest(
        dest=dest,
        grok_root=tmp_path / "grok",
        claude_root=tmp_path / "claude-empty",
        codex_root=tmp_path / "codex-empty",
    )
    assert first["new"] == 1
    second = harvest(
        dest=dest,
        grok_root=tmp_path / "grok",
        claude_root=tmp_path / "claude-empty",
        codex_root=tmp_path / "codex-empty",
    )
    assert second["new"] == 0
    assert second["skipped_unchanged"] >= 1
    rows = load_spend(dest)
    assert len(rows) == 1
    assert turn_key(rows[0]).startswith("grok")


def test_summarize_windows():
    rows = [
        {
            "kind": "turn",
            "provider": "grok",
            "session_id": "s1",
            "turn_id": "a",
            "ts": "2026-08-17T10:00:00+00:00",
            "input_tokens": 100,
            "cached_tokens": 80,
            "output_tokens": 10,
            "reasoning_tokens": 5,
            "total_tokens": 110,
            "model_calls": 2,
            "cost_usd": 0.1,
        },
        {
            "kind": "turn",
            "provider": "claude",
            "session_id": "s2",
            "turn_id": "b",
            "ts": "2026-08-01T10:00:00+00:00",
            "input_tokens": 50,
            "cached_tokens": 0,
            "output_tokens": 5,
            "reasoning_tokens": None,
            "total_tokens": 55,
            "model_calls": 1,
            "cost_usd": None,
        },
    ]
    from datetime import datetime, timezone

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    summary = summarize(rows, now=now)
    w24 = {r["provider"]: r for r in summary["windows"]["24h"]}
    assert "grok" in w24
    assert "claude" not in w24
    assert w24["grok"]["cost_usd"] == 0.1
    wall = {r["provider"]: r for r in summary["windows"]["all"]}
    assert wall["claude"]["cost_usd"] is None
    sessions = session_rollups(rows)
    assert len(sessions) == 2
