"""Muse session.jsonl. One row per model_completed. Seconds are duration_ms."""

from __future__ import annotations

from typing import Any

from ai_quotas.speed.rows import as_int, make_row, tier_from_model
from ai_quotas.speed.timeutil import iso, parse_dt


def interesting(line: str) -> bool:
    return (
        "model_completed" in line
        or "tool_result_batch_committed" in line
        or "assistant_tool_calls_committed" in line
    )


class MuseFeed:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.tools = 0

    def load(self, state: dict | None) -> None:
        state = state or {}
        self.tools = int(state.get("tools") or 0)
        if isinstance(state.get("session_id"), str):
            self.session_id = state["session_id"]

    def dump(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "tools": self.tools}

    def feed(self, obj: dict) -> list[dict]:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        kind = event.get("kind")
        if kind in {"tool_result_batch_committed", "assistant_tool_calls_committed"}:
            self.tools += 1
            return []
        if kind != "model_completed":
            return []
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        stamp = parse_dt(obj.get("recorded_at"))
        duration = event.get("duration_ms")
        seconds = None
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0:
            seconds = float(duration) / 1000.0
        model = event.get("model") if isinstance(event.get("model"), str) else None
        identity = obj.get("id") or obj.get("sequence") or iso(stamp)
        parallel = 1 if self.tools >= 2 else 0
        self.tools = 0
        row = make_row(
            response_key=f"muse:{identity}",
            ts=stamp,
            harness="muse",
            provider="muse",
            model=model,
            tier=tier_from_model(model) if model and "-fast" in model.lower() else "unknown",
            effort=None,
            output_tokens=as_int(usage.get("output_tokens")),
            reasoning_tokens=as_int(usage.get("reasoning_tokens")) or 0,
            seconds=seconds,
            session_id=self.session_id,
            cwd=None,
            parallel_tools=parallel,
            compaction=0,
        )
        return [row] if row else []

    def provisional(self) -> list[dict]:
        return []
