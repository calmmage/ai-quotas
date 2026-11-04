"""Codex rollout jsonl. One row per model response, not per thread total."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ai_quotas.speed.rows import as_int, make_row, normalize_tier
from ai_quotas.speed.timeutil import iso, parse_dt

_TOOL_CALLS = {"function_call", "custom_tool_call"}
_TOOL_RESULTS = {"function_call_output", "custom_tool_call_output"}


def interesting(line: str) -> bool:
    return (
        "token_count" in line
        or "token_usage_record" in line
        or "turn_context" in line
        or "thread_settings_applied" in line
        or "session_meta" in line
        or "context_compacted" in line
        or '"type":"compacted"' in line
        or '"type": "compacted"' in line
        or "function_call" in line
        or "custom_tool_call" in line
        or '"role":"user"' in line
        or '"role": "user"' in line
        or '"type":"reasoning"' in line
        or '"type": "reasoning"' in line
    )


class CodexFeed:
    """``token_usage_record`` wins over the paired ``token_count`` line.

    A count-only file (older rollouts) still emits one row per
    ``last_token_usage``. The count is held until the next user boundary so a
    following usage record for the same response can drop it.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.model = "unknown"
        self.tier = "unknown"
        self.effort: str | None = None
        self.cwd: str | None = None
        self.provider = "openai"
        self.anchor: datetime | None = None
        self.inside = False
        self.tools = 0
        self.open_anchor: datetime | None = None
        self.last_tool_out: datetime | None = None
        self.pending_parallel = 0
        self.pending_compaction = 0
        self.mode: str | None = None
        self.buffered: dict[str, Any] | None = None

    def load(self, state: dict | None) -> None:
        state = state or {}
        self.model = state.get("model") or "unknown"
        self.tier = state.get("tier") or "unknown"
        self.effort = state.get("effort")
        self.cwd = state.get("cwd")
        self.provider = state.get("provider") or "openai"
        self.anchor = parse_dt(state.get("anchor"))
        self.inside = bool(state.get("inside"))
        self.tools = int(state.get("tools") or 0)
        self.open_anchor = parse_dt(state.get("open_anchor"))
        self.last_tool_out = parse_dt(state.get("last_tool_out"))
        self.pending_parallel = int(state.get("pending_parallel") or 0)
        self.pending_compaction = int(state.get("pending_compaction") or 0)
        self.mode = state.get("mode")
        self.buffered = state.get("buffered") if isinstance(state.get("buffered"), dict) else None
        if isinstance(state.get("session_id"), str):
            self.session_id = state["session_id"]

    def dump(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "model": self.model,
            "tier": self.tier,
            "effort": self.effort,
            "cwd": self.cwd,
            "provider": self.provider,
            "anchor": iso(self.anchor),
            "inside": self.inside,
            "tools": self.tools,
            "open_anchor": iso(self.open_anchor),
            "last_tool_out": iso(self.last_tool_out),
            "pending_parallel": self.pending_parallel,
            "pending_compaction": self.pending_compaction,
            "mode": self.mode,
            "buffered": self.buffered,
        }

    def feed(self, obj: dict) -> list[dict]:
        kind = obj.get("type")
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        stamp = parse_dt(obj.get("timestamp"))
        if kind == "session_meta":
            self._session(payload)
            return []
        if kind == "compacted" or (
            kind == "event_msg" and payload.get("type") == "context_compacted"
        ):
            self.pending_compaction = 1
            return []
        if kind == "turn_context":
            self._context(payload)
            return []
        if kind == "event_msg" and payload.get("type") == "thread_settings_applied":
            settings = payload.get("thread_settings")
            if isinstance(settings, dict):
                self._settings(settings)
            return []
        if kind == "response_item":
            return self._item(payload, stamp)
        if kind == "token_usage_record":
            return self._record(payload, stamp)
        if kind == "event_msg" and payload.get("type") == "token_count":
            return self._count(payload, stamp)
        return []

    def provisional(self) -> list[dict]:
        return self._flush_buffer()

    def _session(self, payload: dict) -> None:
        if isinstance(payload.get("cwd"), str):
            self.cwd = payload["cwd"]
        if isinstance(payload.get("id"), str):
            self.session_id = payload["id"]
        provider = payload.get("model_provider")
        if isinstance(provider, str) and provider.strip():
            self.provider = provider.strip()

    def _context(self, payload: dict) -> None:
        model = payload.get("model")
        if isinstance(model, str) and model:
            self.model = model
        effort = payload.get("effort") or payload.get("reasoning_effort")
        if isinstance(effort, str) and effort:
            self.effort = effort
        if isinstance(payload.get("cwd"), str):
            self.cwd = payload["cwd"]
        tier = normalize_tier(payload.get("service_tier"))
        if tier != "unknown":
            self.tier = tier

    def _settings(self, settings: dict) -> None:
        model = settings.get("model")
        if isinstance(model, str) and model:
            self.model = model
        effort = settings.get("reasoning_effort")
        if isinstance(effort, str) and effort:
            self.effort = effort
        tier = normalize_tier(settings.get("service_tier"))
        if tier != "unknown":
            self.tier = tier
        provider = settings.get("model_provider_id")
        if isinstance(provider, str) and provider.strip():
            self.provider = provider.strip()

    def _open(self, stamp: datetime | None) -> None:
        if self.inside:
            return
        self.inside = True
        self.tools = 0
        self.open_anchor = self.anchor
        self.last_tool_out = None
        del stamp

    def _item(self, payload: dict, stamp: datetime | None) -> list[dict]:
        kind = payload.get("type")
        role = payload.get("role")
        if kind == "message" and role == "user":
            rows = self._flush_buffer()
            self.inside = False
            self.tools = 0
            self.open_anchor = None
            if stamp:
                self.anchor = stamp
            return rows
        if kind in _TOOL_CALLS or kind == "reasoning" or (kind == "message" and role == "assistant"):
            self._open(stamp)
            if kind in _TOOL_CALLS:
                self.tools += 1
            return []
        if kind in _TOOL_RESULTS:
            if self.inside:
                if stamp:
                    self.last_tool_out = stamp
                return []
            rows = self._flush_buffer()
            if stamp:
                self.anchor = stamp
            return rows
        return []

    def _record(self, payload: dict, stamp: datetime | None) -> list[dict]:
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
        if not isinstance(usage, dict) or stamp is None:
            return []
        self.mode = "record"
        self.buffered = None
        self._open(stamp)
        response_id = payload.get("response_id") or payload.get("id")
        if not response_id:
            response_id = f"{self.session_id}:{iso(stamp)}:{usage.get('output_tokens')}"
        row = self._snapshot(
            key=f"codex:{response_id}",
            stamp=stamp,
            output=as_int(usage.get("output_tokens")),
            reasoning=as_int(usage.get("reasoning_output_tokens")) or 0,
        )
        self._commit(row)
        built = row.get("row")
        return [built] if built else []

    def _count(self, payload: dict, stamp: datetime | None) -> list[dict]:
        if self.mode == "record" or stamp is None:
            return []
        info = payload.get("info") if isinstance(payload.get("info"), dict) else None
        if not isinstance(info, dict):
            return []
        last = info.get("last_token_usage")
        if not isinstance(last, dict):
            return []
        self._open(stamp)
        output = as_int(last.get("output_tokens"))
        reasoning = as_int(last.get("reasoning_output_tokens")) or 0
        self.buffered = self._snapshot(
            key=f"codex:{self.session_id}:{iso(stamp)}:{output}:{reasoning}",
            stamp=stamp,
            output=output,
            reasoning=reasoning,
        )
        return []

    def _snapshot(
        self,
        *,
        key: str,
        stamp: datetime,
        output: int | None,
        reasoning: int,
    ) -> dict[str, Any]:
        start = self.open_anchor if self.open_anchor is not None else self.anchor
        seconds = None
        if start is not None and stamp > start:
            seconds = (stamp - start).total_seconds()
        parallel = 1 if self.pending_parallel or self.tools >= 2 else 0
        row = make_row(
            response_key=key,
            ts=stamp,
            harness="codex",
            provider=self.provider,
            model=self.model,
            tier=self.tier,
            effort=self.effort,
            output_tokens=output,
            reasoning_tokens=reasoning,
            seconds=seconds,
            session_id=self.session_id,
            cwd=self.cwd,
            parallel_tools=parallel,
            compaction=self.pending_compaction,
        )
        return {
            "row": row,
            "tools": self.tools,
            "end": iso(stamp),
            "tool_out": iso(self.last_tool_out),
        }

    def _commit(self, snap: dict[str, Any]) -> None:
        tools = int(snap.get("tools") or 0)
        self.pending_parallel = 1 if tools >= 2 else 0
        self.pending_compaction = 0
        end = parse_dt(snap.get("end"))
        tool_out = parse_dt(snap.get("tool_out"))
        nxt = end
        if tool_out and (nxt is None or tool_out > nxt):
            nxt = tool_out
        if nxt:
            self.anchor = nxt
        self.inside = False
        self.tools = 0
        self.open_anchor = None
        self.last_tool_out = None

    def _flush_buffer(self) -> list[dict]:
        snap = self.buffered
        self.buffered = None
        if self.mode == "record" or not snap:
            return []
        self.mode = "count"
        self._commit(snap)
        row = snap.get("row")
        return [row] if row else []
