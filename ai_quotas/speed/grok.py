"""Grok CLI. Usage is one blob per user turn, on updates.jsonl turn_completed."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ai_quotas.speed.rows import as_int, make_row, tier_from_model
from ai_quotas.speed.timeutil import iso, parse_dt


def interesting_update(line: str) -> bool:
    return (
        "turn_completed" in line
        or "user_message_chunk" in line
        or '"tool_call"' in line
        or "auto_compact" in line
    )


def interesting_event(line: str) -> bool:
    return (
        "auto_compact_started" in line
        or "auto_compact_completed" in line
        or "compaction_checkpoint" in line
        or "turn_started" in line
    )


class GrokFeed:
    """``modelCalls > 1`` is several loops summed into one blob.

    Those rows stay in the table and are marked so the speed stats skip them.
    There is no per-loop token split to recover.
    """

    def __init__(self, session_id: str, cwd: str | None) -> None:
        self.session_id = session_id
        self.cwd = cwd
        self.model: str | None = None
        self.effort: str | None = None
        self.anchor: datetime | None = None
        self.tools = 0
        self.pending_compaction = 0

    def load(self, state: dict | None) -> None:
        state = state or {}
        self.model = state.get("model")
        self.effort = state.get("effort")
        self.anchor = parse_dt(state.get("anchor"))
        self.tools = int(state.get("tools") or 0)
        self.pending_compaction = int(state.get("pending_compaction") or 0)
        if isinstance(state.get("session_id"), str):
            self.session_id = state["session_id"]
        if isinstance(state.get("cwd"), str):
            self.cwd = state["cwd"]

    def dump(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "model": self.model,
            "effort": self.effort,
            "anchor": iso(self.anchor),
            "tools": self.tools,
            "pending_compaction": self.pending_compaction,
        }

    def feed(self, obj: dict) -> list[dict]:
        if obj.get("_kind") == "event" or (
            isinstance(obj.get("type"), str) and "params" not in obj
        ):
            return self._event(obj)
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        name = update.get("sessionUpdate")
        stamp = parse_dt(obj.get("timestamp"))
        if isinstance(params.get("sessionId"), str):
            self.session_id = params["sessionId"]
        if name == "user_message_chunk":
            if stamp:
                self.anchor = stamp
            self.tools = 0
            meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
            model = meta.get("modelId")
            if isinstance(model, str) and model:
                self.model = model
            return []
        if name == "tool_call":
            self.tools += 1
            return []
        if name == "turn_completed":
            return self._completed(update, stamp)
        return []

    def provisional(self) -> list[dict]:
        return []

    def _event(self, obj: dict) -> list[dict]:
        kind = obj.get("type")
        if kind in {"auto_compact_started", "auto_compact_completed", "compaction_checkpoint"}:
            self.pending_compaction = 1
            return []
        if kind == "turn_started":
            model = obj.get("model_id")
            if isinstance(model, str) and model:
                self.model = model
            if isinstance(obj.get("session_id"), str):
                self.session_id = obj["session_id"]
        return []

    def _completed(self, update: dict, stamp: datetime | None) -> list[dict]:
        usage = update.get("usage") if isinstance(update.get("usage"), dict) else None
        if not isinstance(usage, dict) or stamp is None:
            return []
        models = usage.get("modelUsage") if isinstance(usage.get("modelUsage"), dict) else {}
        if not models:
            models = {self.model or "unknown": usage}
        prompt_id = update.get("prompt_id") or update.get("promptId") or iso(stamp)
        calls = as_int(usage.get("modelCalls")) or 1
        elapsed_ms = as_int(update.get("elapsed_ms"))
        seconds = None
        if self.anchor is not None and stamp > self.anchor:
            seconds = (stamp - self.anchor).total_seconds()
        elif elapsed_ms is not None and elapsed_ms > 0:
            seconds = elapsed_ms / 1000.0
        parallel = 1 if calls > 1 or self.tools >= 2 else 0
        compaction = self.pending_compaction
        rows: list[dict] = []
        for model, part in models.items():
            block = part if isinstance(part, dict) else usage
            output = as_int(block.get("outputTokens"))
            if output is None:
                output = as_int(usage.get("outputTokens"))
            reasoning = as_int(block.get("reasoningTokens"))
            if reasoning is None:
                reasoning = as_int(usage.get("reasoningTokens")) or 0
            part_calls = as_int(block.get("modelCalls")) or calls
            row = make_row(
                response_key=f"grok:{self.session_id}:{prompt_id}:{model}",
                ts=stamp,
                harness="grok",
                provider="xai",
                model=str(model),
                tier=tier_from_model(str(model)),
                effort=self.effort,
                output_tokens=output,
                reasoning_tokens=reasoning or 0,
                seconds=seconds,
                session_id=self.session_id,
                cwd=self.cwd,
                parallel_tools=1 if parallel or part_calls > 1 else 0,
                compaction=compaction,
            )
            if row:
                rows.append(row)
        self.anchor = stamp
        self.tools = 0
        self.pending_compaction = 0
        return rows
