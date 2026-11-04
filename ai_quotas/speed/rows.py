"""One speed row. Transcript text never enters these fields."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ai_quotas.speed.timeutil import iso, seconds_between

MIN_OUTPUT_TOKENS = 20
MIN_SECONDS = 0.3
TIERS = ("standard", "fast", "priority", "ultrafast", "unknown")


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_tier(raw: Any) -> str:
    if raw is None:
        return "unknown"
    text = str(raw).strip().lower()
    if text in {"default", "standard", "std"}:
        return "standard"
    if text in {"fast", "priority", "ultrafast"}:
        return text
    return "unknown"


def tier_from_model(model: str | None) -> str:
    """Grok has no service_tier. Fast mode is a model-id suffix."""
    if not model:
        return "unknown"
    text = model.lower()
    if "ultrafast" in text or "ultra-fast" in text:
        return "ultrafast"
    if "-fast" in text:
        return "fast"
    return "standard"


def make_row(
    *,
    response_key: str,
    ts: datetime | None,
    harness: str,
    provider: str,
    model: str | None,
    tier: str,
    effort: str | None,
    output_tokens: int | None,
    reasoning_tokens: int | None,
    seconds: float | None,
    session_id: str | None,
    cwd: str | None,
    parallel_tools: int,
    compaction: int,
) -> dict[str, Any] | None:
    if ts is None or output_tokens is None:
        return None
    if seconds is not None and seconds > 0:
        rate = output_tokens / seconds
    else:
        seconds = None
        rate = None
    effort_text = str(effort).strip() if effort else None
    return {
        "response_key": response_key,
        "ts": iso(ts),
        "harness": harness,
        "provider": provider,
        "model": model or "unknown",
        "tier": tier if tier in TIERS else "unknown",
        "effort": effort_text or None,
        "output_tokens": int(output_tokens),
        "reasoning_tokens": int(reasoning_tokens or 0),
        "seconds": seconds,
        "tokens_per_second": rate,
        "session_id": session_id,
        "cwd": cwd,
        "parallel_tools": 1 if parallel_tools else 0,
        "compaction": 1 if compaction else 0,
    }


def measured_seconds(start: datetime | None, end: datetime | None) -> float | None:
    return seconds_between(start, end)
