"""Harvest per-turn token/$ from local agent session files.

This is a different grain from quota samples, stored in a separate SQLite table.

Sources (read-only, already on disk):
  grok   ~/.grok/sessions/**/updates.jsonl  → turn_completed.usage (+ costUsdTicks)
  claude ~/.claude/projects/**/*.jsonl      → message.usage (tokens; $ usually absent)
  codex  ~/.codex/sessions/**/rollout-*.jsonl → token_count.last_token_usage

Rows are keyed by (provider, session_id, turn_id). Incremental file state lives
in SQLite too. Explicit JSONL destinations remain supported for compatibility.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ai_quotas.core import parse_ts
from ai_quotas.paths import spend_cursor_path, spend_path
from ai_quotas.storage import (
    append_spend,
    load_harvest_cursor,
    load_spend_keys,
    load_spend as load_stored_spend,
    save_harvest_cursor,
)

# Headless docs: total_cost_usd_ticks / 1e10 = USD.
GROK_TICKS_PER_USD = 10_000_000_000.0

GROK_SESSIONS = Path.home() / ".grok" / "sessions"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"

_CODEX_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _iso_from_unix(value: Any) -> str | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    # ms vs seconds
    if n > 1e12:
        n = n / 1000.0
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).astimezone().isoformat(
            timespec="seconds"
        )
    except (OverflowError, OSError, ValueError):
        return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _iso_from_unix(value)
    text = str(value).strip()
    if not text:
        return None
    dt = parse_ts(text)
    if dt is None:
        return _iso_from_unix(text) if text.isdigit() else text
    return dt.isoformat(timespec="seconds")


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def turn_key(row: dict[str, Any]) -> str:
    return f"{row.get('provider')}\0{row.get('session_id')}\0{row.get('turn_id')}"


def _row(
    *,
    provider: str,
    session_id: str,
    turn_id: str,
    ts: str | None,
    input_tokens: int | None,
    cached_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None,
    total_tokens: int | None,
    model_calls: int | None,
    api_ms: int | None,
    cost_usd: float | None,
    model: str | None = None,
    source: str,
) -> dict[str, Any]:
    return {
        "kind": "turn",
        "provider": provider,
        "session_id": session_id,
        "turn_id": turn_id,
        "ts": ts,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
        "model_calls": model_calls,
        "api_ms": api_ms,
        "cost_usd": cost_usd,
        "model": model,
        "source": source,
    }


def load_spend(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = spend_path(path)
    return load_stored_spend(p)


def load_seen_keys(path: str | Path | None = None) -> set[str]:
    return load_spend_keys(spend_path(path))


def _load_cursor(path: Path) -> dict[str, Any]:
    raw = load_harvest_cursor(path)
    files = raw.get("files")
    if not isinstance(files, dict):
        raw["files"] = {}
    return raw


def _save_cursor(path: Path, cursor: dict[str, Any]) -> None:
    save_harvest_cursor(path, cursor)


def _file_sig(path: Path) -> dict[str, int]:
    st = path.stat()
    return {"mtime_ns": int(st.st_mtime_ns), "size": int(st.st_size)}


def _unchanged(cursor: dict[str, Any], path: Path) -> bool:
    prev = (cursor.get("files") or {}).get(str(path))
    if not isinstance(prev, dict):
        return False
    try:
        sig = _file_sig(path)
    except OSError:
        return False
    return prev.get("mtime_ns") == sig["mtime_ns"] and prev.get("size") == sig["size"]


def _mark(cursor: dict[str, Any], path: Path, n_new: int) -> None:
    files = cursor.setdefault("files", {})
    try:
        sig = _file_sig(path)
    except OSError:
        return
    files[str(path)] = {**sig, "n_new": n_new}


def _append_rows(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    return append_spend(path, rows)


def parse_grok_updates(path: Path, session_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with fh:
        for line in fh:
            if "turn_completed" not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            upd = ((obj.get("params") or {}).get("update")) or {}
            if upd.get("sessionUpdate") != "turn_completed":
                continue
            usage = upd.get("usage")
            if not isinstance(usage, dict):
                continue
            sid = (
                (obj.get("params") or {}).get("sessionId")
                or session_id
            )
            prompt_id = upd.get("prompt_id") or upd.get("promptId")
            turn_id = str(prompt_id or obj.get("timestamp") or "")
            if not turn_id:
                continue
            ticks = _int(usage.get("costUsdTicks"))
            cost = (ticks / GROK_TICKS_PER_USD) if ticks is not None else None
            models = usage.get("modelUsage")
            model = None
            if isinstance(models, dict) and models:
                model = next(iter(models))
            rows.append(
                _row(
                    provider="grok",
                    session_id=str(sid),
                    turn_id=turn_id,
                    ts=_iso(obj.get("timestamp")),
                    input_tokens=_int(usage.get("inputTokens")),
                    cached_tokens=_int(usage.get("cachedReadTokens")),
                    output_tokens=_int(usage.get("outputTokens")),
                    reasoning_tokens=_int(usage.get("reasoningTokens")),
                    total_tokens=_int(usage.get("totalTokens")),
                    model_calls=_int(usage.get("modelCalls")),
                    api_ms=_int(usage.get("apiDurationMs")),
                    cost_usd=cost,
                    model=model,
                    source="grok/updates.jsonl#turn_completed",
                )
            )
    return rows


def parse_claude_transcript(path: Path, session_id: str) -> list[dict[str, Any]]:
    """Last usage blob per message.id (same id repeats on thinking/text/tool)."""
    by_id: dict[str, dict[str, Any]] = {}
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            mid = msg.get("id")
            if not mid:
                continue
            by_id[str(mid)] = _row(
                provider="claude",
                session_id=session_id,
                turn_id=str(mid),
                ts=_iso(obj.get("timestamp") or obj.get("isoTimestamp")),
                input_tokens=_int(usage.get("input_tokens")),
                cached_tokens=_int(
                    usage.get("cache_read_input_tokens")
                    or usage.get("cache_read_tokens")
                ),
                output_tokens=_int(usage.get("output_tokens")),
                reasoning_tokens=None,
                total_tokens=None,
                model_calls=1,
                api_ms=None,
                cost_usd=_float(usage.get("costUSD") or usage.get("cost_usd")),
                model=msg.get("model") if isinstance(msg.get("model"), str) else None,
                source="claude/projects.jsonl#message.usage",
            )
    for row in by_id.values():
        inp = row.get("input_tokens") or 0
        cached = row.get("cached_tokens") or 0
        out = row.get("output_tokens") or 0
        row["total_tokens"] = inp + cached + out
        # Subscription plans often report costUSD=0 — treat as unknown, not free.
        if row.get("cost_usd") == 0:
            row["cost_usd"] = None
    return list(by_id.values())


def _codex_session_id(path: Path) -> str:
    m = _CODEX_ID_RE.search(path.name)
    return m.group(1) if m else path.stem


def parse_codex_rollout(path: Path, session_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with fh:
        for i, line in enumerate(fh):
            if "token_count" not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            last = info.get("last_token_usage") if isinstance(info, dict) else None
            if not isinstance(last, dict):
                continue
            ts = obj.get("timestamp")
            turn_id = f"{ts or i}"
            rows.append(
                _row(
                    provider="codex",
                    session_id=session_id,
                    turn_id=turn_id,
                    ts=_iso(ts),
                    input_tokens=_int(last.get("input_tokens")),
                    cached_tokens=_int(last.get("cached_input_tokens")),
                    output_tokens=_int(last.get("output_tokens")),
                    reasoning_tokens=_int(last.get("reasoning_output_tokens")),
                    total_tokens=_int(last.get("total_tokens")),
                    model_calls=1,
                    api_ms=None,
                    cost_usd=None,
                    model=None,
                    source="codex/rollout.jsonl#token_count",
                )
            )
    return rows


def _iter_files(root: Path, pattern: str) -> list[Path]:
    if not root.is_dir():
        return []
    found = [p for p in root.rglob(pattern) if p.is_file()]
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return found


def harvest(
    *,
    dest: str | Path | None = None,
    grok_root: Path | None = None,
    claude_root: Path | None = None,
    codex_root: Path | None = None,
    max_seconds: float | None = None,
    providers: tuple[str, ...] = ("grok", "claude", "codex"),
) -> dict[str, Any]:
    """Scan local session files; append unseen turns. Never raises."""
    started = time.monotonic()
    dest_p = spend_path(dest)
    cursor_p = spend_cursor_path(dest_p)
    cursor = _load_cursor(cursor_p)
    seen = load_seen_keys(dest_p)
    new_rows: list[dict[str, Any]] = []
    scanned = 0
    skipped = 0
    timed_out = False

    jobs: list[tuple[str, Path, Any]] = []
    if "grok" in providers:
        for p in _iter_files(grok_root or GROK_SESSIONS, "updates.jsonl"):
            jobs.append(("grok", p, parse_grok_updates))
    if "claude" in providers:
        root = claude_root or CLAUDE_PROJECTS
        for p in _iter_files(root, "*.jsonl"):
            jobs.append(("claude", p, parse_claude_transcript))
    if "codex" in providers:
        for p in _iter_files(codex_root or CODEX_SESSIONS, "rollout-*.jsonl"):
            jobs.append(("codex", p, parse_codex_rollout))

    try:
        for provider, path, parser in jobs:
            if max_seconds is not None and (time.monotonic() - started) >= max_seconds:
                timed_out = True
                break
            if _unchanged(cursor, path):
                skipped += 1
                continue
            scanned += 1
            if provider == "grok":
                sid = path.parent.name
            elif provider == "codex":
                sid = _codex_session_id(path)
            else:
                sid = path.stem
            try:
                parsed = parser(path, sid)
            except Exception:
                _mark(cursor, path, 0)
                continue
            added = 0
            batch: list[dict[str, Any]] = []
            for row in parsed:
                k = turn_key(row)
                if k in seen:
                    continue
                seen.add(k)
                batch.append(row)
                added += 1
            if batch:
                _append_rows(dest_p, batch)
                new_rows.extend(batch)
            _mark(cursor, path, added)
    finally:
        cursor["updated_at"] = _now().isoformat(timespec="seconds")
        cursor["dest"] = str(dest_p)
        _save_cursor(cursor_p, cursor)

    return {
        "dest": str(dest_p),
        "new": len(new_rows),
        "scanned_files": scanned,
        "skipped_unchanged": skipped,
        "timed_out": timed_out,
        "elapsed_s": round(time.monotonic() - started, 3),
        "rows": new_rows,
    }


def _ts_ok(row: dict[str, Any], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    dt = parse_ts(row.get("ts") if isinstance(row.get("ts"), str) else None)
    if dt is None:
        return False
    return dt >= cutoff


def _add(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    bucket["turns"] += 1
    bucket["sessions"].add(row.get("session_id"))
    for field in (
        "input_tokens",
        "cached_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "model_calls",
        "api_ms",
    ):
        v = row.get(field)
        if isinstance(v, (int, float)):
            bucket[field] += int(v)
    cost = row.get("cost_usd")
    if isinstance(cost, (int, float)):
        bucket["cost_usd"] += float(cost)
        bucket["cost_n"] += 1


def _empty_bucket() -> dict[str, Any]:
    return {
        "turns": 0,
        "sessions": set(),
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "model_calls": 0,
        "api_ms": 0,
        "cost_usd": 0.0,
        "cost_n": 0,
    }


def _freeze(bucket: dict[str, Any], provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "sessions": len(bucket["sessions"]),
        "turns": bucket["turns"],
        "input_tokens": bucket["input_tokens"],
        "cached_tokens": bucket["cached_tokens"],
        "output_tokens": bucket["output_tokens"],
        "reasoning_tokens": bucket["reasoning_tokens"],
        "total_tokens": bucket["total_tokens"],
        "model_calls": bucket["model_calls"],
        "api_ms": bucket["api_ms"],
        "cost_usd": bucket["cost_usd"] if bucket["cost_n"] else None,
    }


def summarize(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    windows = {
        "24h": now - timedelta(hours=24),
        "7d": now - timedelta(days=7),
        "all": None,
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for name, cutoff in windows.items():
        by: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
        for row in rows:
            if row.get("kind") not in (None, "turn"):
                continue
            if not _ts_ok(row, cutoff):
                continue
            provider = str(row.get("provider") or "?")
            _add(by[provider], row)
        out[name] = [_freeze(by[p], p) for p in sorted(by)]
    return {"ts": now.isoformat(timespec="seconds"), "windows": out}


def session_rollups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") not in (None, "turn"):
            continue
        provider = str(row.get("provider") or "?")
        sid = str(row.get("session_id") or "?")
        key = (provider, sid)
        bucket = by.get(key)
        if bucket is None:
            bucket = _empty_bucket()
            bucket["first_ts"] = row.get("ts")
            bucket["last_ts"] = row.get("ts")
            bucket["model"] = row.get("model")
            by[key] = bucket
        _add(bucket, row)
        ts = row.get("ts")
        if isinstance(ts, str):
            if bucket["first_ts"] is None or ts < str(bucket["first_ts"]):
                bucket["first_ts"] = ts
            if bucket["last_ts"] is None or ts > str(bucket["last_ts"]):
                bucket["last_ts"] = ts
        if row.get("model") and not bucket.get("model"):
            bucket["model"] = row.get("model")
    out = []
    for (provider, sid), bucket in by.items():
        item = _freeze(bucket, provider)
        item["session_id"] = sid
        item["first_ts"] = bucket.get("first_ts")
        item["last_ts"] = bucket.get("last_ts")
        item["model"] = bucket.get("model")
        out.append(item)
    out.sort(key=lambda r: str(r.get("last_ts") or ""), reverse=True)
    return out


def _fmt_int(n: int | None) -> str:
    if n is None:
        return "—"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 10_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:,}"


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1:
        return f"${v:.2f}"
    return f"${v:.4f}"


def print_spend(
    summary: dict[str, Any],
    *,
    dest: Path | None = None,
    harvest_info: dict[str, Any] | None = None,
    sessions: list[dict[str, Any]] | None = None,
    session_limit: int = 12,
) -> None:
    ts = summary.get("ts") or ""
    print(f"\n  AI SESSION SPEND  (as of {ts})\n")
    if harvest_info:
        extra = ""
        if harvest_info.get("timed_out"):
            extra = "  (partial — ran out of time; re-run to continue)"
        print(
            f"  harvested +{harvest_info.get('new', 0)} turns"
            f"  scanned {harvest_info.get('scanned_files', 0)} files"
            f"  skipped {harvest_info.get('skipped_unchanged', 0)}"
            f"  {harvest_info.get('elapsed_s', 0)}s{extra}"
        )
        print()
    windows = summary.get("windows") or {}
    for label in ("24h", "7d", "all"):
        rows = windows.get(label) or []
        title = {"24h": "last 24h", "7d": "last 7d", "all": "all harvested"}[label]
        print(f"  {title}")
        if not rows:
            print("    (none)")
            print()
            continue
        print(
            f"    {'vendor':<8} {'sess':>5} {'turns':>6} "
            f"{'input':>8} {'cached':>8} {'out':>8} {'reason':>8} "
            f"{'calls':>6} {'$':>8}"
        )
        for r in rows:
            print(
                f"    {str(r.get('provider')):<8} "
                f"{int(r.get('sessions') or 0):>5} "
                f"{int(r.get('turns') or 0):>6} "
                f"{_fmt_int(r.get('input_tokens')):>8} "
                f"{_fmt_int(r.get('cached_tokens')):>8} "
                f"{_fmt_int(r.get('output_tokens')):>8} "
                f"{_fmt_int(r.get('reasoning_tokens')):>8} "
                f"{_fmt_int(r.get('model_calls')):>6} "
                f"{_fmt_usd(r.get('cost_usd')):>8}"
            )
        print()
    if sessions:
        print(f"  recent sessions (top {min(session_limit, len(sessions))})")
        print(
            f"    {'vendor':<8} {'$':>8} {'in':>8} {'out':>7} "
            f"{'turns':>5}  last                 session"
        )
        for r in sessions[:session_limit]:
            sid = str(r.get("session_id") or "")
            if len(sid) > 12:
                sid = sid[:8] + "…"
            last = str(r.get("last_ts") or "—")
            if len(last) > 19:
                last = last[:19]
            print(
                f"    {str(r.get('provider')):<8} "
                f"{_fmt_usd(r.get('cost_usd')):>8} "
                f"{_fmt_int(r.get('input_tokens')):>8} "
                f"{_fmt_int(r.get('output_tokens')):>7} "
                f"{int(r.get('turns') or 0):>5}  "
                f"{last:<19} {sid}"
            )
        print()
    if dest is not None:
        print(f"  file: {dest}")
        print(
            "  grok $ is the TUI estimate (costUsdTicks). "
            "claude/codex subscription $ is unknown — tokens only."
        )
        print()
