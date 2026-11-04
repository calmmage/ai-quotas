"""Claude Code jsonl: several lines share one message.id. Count usage once."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ai_quotas.speed.rows import as_int, make_row, normalize_tier
from ai_quotas.speed.timeutil import iso, parse_dt


def interesting(line: str) -> bool:
    return (
        '"type":"assistant"' in line
        or '"type": "assistant"' in line
        or '"type":"user"' in line
        or '"type": "user"' in line
        or "compact_boundary" in line
        or "isCompactSummary" in line
    )


def _blocks(message: dict) -> list[dict]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _tool_uses(message: dict) -> int:
    return sum(1 for block in _blocks(message) if block.get("type") == "tool_use")


def _tool_result(message: dict) -> bool:
    return any(block.get("type") == "tool_result" for block in _blocks(message))


def _tier(usage: dict) -> str:
    """``speed`` is the fast-mode flag. ``service_tier`` is the fallback."""
    ranked = [normalize_tier(usage.get("speed")), normalize_tier(usage.get("service_tier"))]
    for tier in ranked:
        if tier not in {"unknown", "standard"}:
            return tier
    for tier in ranked:
        if tier == "standard":
            return "standard"
    return "unknown"


class ClaudeFeed:
    def __init__(self) -> None:
        self.anchor: datetime | None = None
        self.pending_parallel = 0
        self.pending_compaction = 0
        self.tool_results = 0
        self.open: dict[str, Any] | None = None

    def load(self, state: dict | None) -> None:
        state = state or {}
        self.anchor = parse_dt(state.get("anchor"))
        self.pending_parallel = int(state.get("pending_parallel") or 0)
        self.pending_compaction = int(state.get("pending_compaction") or 0)
        self.tool_results = int(state.get("tool_results") or 0)
        open_row = state.get("open")
        self.open = open_row if isinstance(open_row, dict) else None
        if self.open and self.open.get("anchor"):
            self.open["anchor_dt"] = parse_dt(self.open.get("anchor"))
        if self.open and self.open.get("ts"):
            self.open["ts_dt"] = parse_dt(self.open.get("ts"))

    def dump(self) -> dict[str, Any]:
        open_row = None
        if self.open:
            open_row = {
                key: value
                for key, value in self.open.items()
                if key not in {"anchor_dt", "ts_dt"}
            }
        return {
            "anchor": iso(self.anchor),
            "pending_parallel": self.pending_parallel,
            "pending_compaction": self.pending_compaction,
            "tool_results": self.tool_results,
            "open": open_row,
        }

    def feed(self, obj: dict) -> list[dict]:
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "compact_boundary":
            self.pending_compaction = 1
            if self.open is not None:
                self.open["compaction"] = 1
            return []
        if kind == "user":
            rows = self._flush()
            self._user(obj)
            return rows
        if kind == "assistant":
            return self._assistant(obj)
        return []

    def provisional(self) -> list[dict]:
        row = self._row(clear=False)
        return [row] if row else []

    def _user(self, obj: dict) -> None:
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        stamp = parse_dt(obj.get("timestamp"))
        compact = bool(obj.get("isCompactSummary"))
        if compact:
            self.pending_compaction = 1
        if _tool_result(message) and not compact:
            self.tool_results += 1
            if self.tool_results >= 2:
                self.pending_parallel = 1
            if stamp:
                self.anchor = stamp
            return
        if stamp:
            self.anchor = stamp
        self.tool_results = 0
        if not compact:
            self.pending_parallel = 0

    def _assistant(self, obj: dict) -> list[dict]:
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
        message_id = message.get("id")
        if not message_id or usage is None:
            return []
        rows: list[dict] = []
        if self.open and self.open.get("id") != message_id:
            rows.extend(self._flush())
        if self.open is None:
            self.open = {
                "id": str(message_id),
                "anchor": iso(self.anchor),
                "anchor_dt": self.anchor,
                "ts": iso(parse_dt(obj.get("timestamp"))),
                "ts_dt": parse_dt(obj.get("timestamp")),
                "output": 0,
                "reasoning": 0,
                "model": message.get("model") if isinstance(message.get("model"), str) else "unknown",
                "tier": _tier(usage),
                "effort": obj.get("effort") or obj.get("perTurnEffort"),
                "session": obj.get("sessionId") or obj.get("session_id"),
                "cwd": obj.get("cwd") if isinstance(obj.get("cwd"), str) else None,
                "tools": 0,
                "parallel": self.pending_parallel,
                "compaction": self.pending_compaction,
            }
            self.pending_parallel = 0
            self.pending_compaction = 0
            self.tool_results = 0
        current = self.open
        output = as_int(usage.get("output_tokens")) or 0
        details = usage.get("output_tokens_details")
        reasoning = 0
        if isinstance(details, dict):
            reasoning = as_int(details.get("thinking_tokens")) or 0
        if output >= int(current.get("output") or 0):
            current["output"] = output
            current["reasoning"] = reasoning
        stamp = parse_dt(obj.get("timestamp"))
        if stamp:
            current["ts_dt"] = stamp
            current["ts"] = iso(stamp)
        if isinstance(message.get("model"), str):
            current["model"] = message["model"]
        tier = _tier(usage)
        if tier != "unknown":
            current["tier"] = tier
        if obj.get("effort") or obj.get("perTurnEffort"):
            current["effort"] = obj.get("effort") or obj.get("perTurnEffort")
        if isinstance(obj.get("cwd"), str):
            current["cwd"] = obj["cwd"]
        current["tools"] = int(current.get("tools") or 0) + _tool_uses(message)
        return rows

    def _flush(self) -> list[dict]:
        row = self._row(clear=True)
        return [row] if row else []

    def _row(self, *, clear: bool) -> dict | None:
        current = self.open
        if not current:
            return None
        if clear:
            self.open = None
            if int(current.get("tools") or 0) >= 2:
                self.pending_parallel = 1
        anchor = current.get("anchor_dt") or parse_dt(current.get("anchor"))
        end = current.get("ts_dt") or parse_dt(current.get("ts"))
        return make_row(
            response_key=f"claude:{current.get('id')}",
            ts=end if isinstance(end, datetime) else None,
            harness="claude",
            provider="anthropic",
            model=current.get("model"),
            tier=str(current.get("tier") or "unknown"),
            effort=current.get("effort") if isinstance(current.get("effort"), str) else None,
            output_tokens=as_int(current.get("output")),
            reasoning_tokens=as_int(current.get("reasoning")) or 0,
            seconds=None
            if not isinstance(anchor, datetime) or not isinstance(end, datetime)
            else (end - anchor).total_seconds() if end > anchor else None,
            session_id=str(current["session"]) if current.get("session") else None,
            cwd=current.get("cwd") if isinstance(current.get("cwd"), str) else None,
            parallel_tools=int(current.get("parallel") or 0),
            compaction=int(current.get("compaction") or 0),
        )
