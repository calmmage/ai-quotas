"""Attribute session spend to labeled agentic_step jobs.

Join key: (provider, spend.session_id == job.chat_id).
Jobs with no spend harvest (harness/model spend.py does not read) fall back
to the job's own ``usage`` blob, marked ``source=job``.
"""

from __future__ import annotations

import os

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ai_quotas.core import parse_ts
from ai_quotas.paths import agentic_step_jobs_path, spend_path
from ai_quotas.spend import (
    _fmt_int,
    _fmt_usd,
    load_spend,
    session_rollups,
)

# One place for check + alert wiring. Tune here, not in the CLI / LaunchAgent.
DEFAULT_SINCE = "7d"
DEFAULT_THRESHOLD_TOKENS = 1_000_000
DEFAULT_THRESHOLD_USD = 5.0

_SINCE_RE = re.compile(r"^(\d+)\s*([dhm])$", re.I)


def _now() -> datetime:
    """Current time; ``AI_QUOTAS_NOW`` (ISO-8601) overrides for fixtures/tests."""
    raw = os.environ.get("AI_QUOTAS_NOW")
    if raw and raw.strip():
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def parse_since(spec: str, *, now: datetime | None = None) -> datetime:
    """``7d`` / ``30d`` / ``24h`` / ``90m`` → cutoff datetime (inclusive)."""
    text = (spec or "").strip().lower()
    m = _SINCE_RE.match(text)
    if not m:
        raise ValueError(f"bad --since {spec!r} (want 7d, 30d, 24h)")
    n = int(m.group(1))
    unit = m.group(2)
    now = now or _now()
    if unit == "d":
        delta = timedelta(days=n)
    elif unit == "h":
        delta = timedelta(hours=n)
    else:
        delta = timedelta(minutes=n)
    return now - delta


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _ts_ok(value: Any, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    if not isinstance(value, str) or not value.strip():
        # Jobs without a parseable ts stay in (don't hide unlabeled burn).
        return True
    dt = parse_ts(value)
    if dt is None:
        return True
    return _aware(dt) >= _aware(cutoff)


def load_jobs(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = agentic_step_jobs_path(path)
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


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


def _usage_from_job(job: dict[str, Any]) -> dict[str, Any]:
    """Map job.usage → spend-like token fields."""
    usage = job.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    inp = _int(usage.get("input_tokens"))
    out = _int(usage.get("output_tokens"))
    cached = _int(usage.get("cache_read_tokens") or usage.get("cached_tokens"))
    cache_write = _int(usage.get("cache_write_tokens"))
    parts = [v for v in (inp, out, cached, cache_write) if v is not None]
    total = sum(parts) if parts else None
    return {
        "input_tokens": inp,
        "cached_tokens": cached,
        "output_tokens": out,
        "cache_write_tokens": cache_write,
        "reasoning_tokens": None,
        "total_tokens": total,
        "cost_usd": _float(usage.get("cost_usd")),
        "turns": 0,
        "sessions": 1,
        "model": job.get("model"),
    }


def _tokens_from_rollup(rollup: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_tokens": rollup.get("input_tokens") or 0,
        "cached_tokens": rollup.get("cached_tokens") or 0,
        "output_tokens": rollup.get("output_tokens") or 0,
        "cache_write_tokens": None,
        "reasoning_tokens": rollup.get("reasoning_tokens") or 0,
        "total_tokens": rollup.get("total_tokens") or 0,
        "cost_usd": rollup.get("cost_usd"),
        "turns": int(rollup.get("turns") or 0),
        "sessions": int(rollup.get("sessions") or 1),
        "model": rollup.get("model") or None,
    }


def _join_key(provider: Any, session_id: Any) -> tuple[str, str] | None:
    p = str(provider or "").strip()
    s = str(session_id or "").strip()
    if not p or not s:
        return None
    return (p, s)


def attribute_jobs(
    jobs: list[dict[str, Any]],
    spend_rows: list[dict[str, Any]],
    *,
    caller: str | None = None,
    task_slug: str | None = None,
    since: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Join jobs to spend session rollups. One output row per job in the window."""
    now = now or _now()
    cutoff = parse_since(since, now=now) if since else None
    want_caller = caller.strip() if caller else None
    want_slug = task_slug.strip() if task_slug else None

    rollups = session_rollups(spend_rows)
    by_session: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rollups:
        key = _join_key(r.get("provider"), r.get("session_id"))
        if key is not None:
            by_session[key] = r

    attributed: list[dict[str, Any]] = []
    skipped = 0
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if want_caller and str(job.get("caller") or "") != want_caller:
            continue
        if want_slug and str(job.get("task_slug") or "") != want_slug:
            continue
        if not _ts_ok(job.get("ts"), cutoff):
            skipped += 1
            continue
        provider = str(job.get("provider") or "")
        chat_id = str(job.get("chat_id") or "")
        key = _join_key(provider, chat_id)
        rollup = by_session.get(key) if key is not None else None
        if rollup is not None:
            tokens = _tokens_from_rollup(rollup)
            source = "spend"
        else:
            tokens = _usage_from_job(job)
            source = "job"
        model = tokens.get("model") or job.get("model")
        attributed.append(
            {
                "job_id": job.get("job_id"),
                "ts": job.get("ts"),
                "backend": job.get("backend"),
                "harness": job.get("harness"),
                "provider": provider or None,
                "model": model,
                "chat_id": chat_id or None,
                "caller": job.get("caller"),
                "task_slug": job.get("task_slug"),
                "item_ref": job.get("item_ref"),
                "duration_ms": job.get("duration_ms"),
                "source": source,
                **{
                    k: tokens.get(k)
                    for k in (
                        "input_tokens",
                        "cached_tokens",
                        "output_tokens",
                        "cache_write_tokens",
                        "reasoning_tokens",
                        "total_tokens",
                        "cost_usd",
                        "turns",
                    )
                },
            }
        )

    return {
        "ts": now.isoformat(timespec="seconds"),
        "since": since,
        "cutoff": cutoff.isoformat(timespec="seconds") if cutoff else None,
        "jobs": attributed,
        "n_jobs": len(attributed),
        "n_skipped_since": skipped,
        "n_matched": sum(1 for r in attributed if r["source"] == "spend"),
        "n_unmatched": sum(1 for r in attributed if r["source"] == "job"),
    }


def _empty_tot() -> dict[str, Any]:
    return {
        "jobs": 0,
        "matched": 0,
        "unmatched": 0,
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cost_n": 0,
        "turns": 0,
    }


def _add_tot(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    bucket["jobs"] += 1
    if row.get("source") == "spend":
        bucket["matched"] += 1
    else:
        bucket["unmatched"] += 1
    for field in (
        "input_tokens",
        "cached_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "turns",
    ):
        v = row.get(field)
        if isinstance(v, (int, float)):
            bucket[field] += int(v)
    cost = row.get("cost_usd")
    if isinstance(cost, (int, float)):
        bucket["cost_usd"] += float(cost)
        bucket["cost_n"] += 1


def _freeze_tot(bucket: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "jobs": bucket["jobs"],
        "matched": bucket["matched"],
        "unmatched": bucket["unmatched"],
        "input_tokens": bucket["input_tokens"],
        "cached_tokens": bucket["cached_tokens"],
        "output_tokens": bucket["output_tokens"],
        "reasoning_tokens": bucket["reasoning_tokens"],
        "total_tokens": bucket["total_tokens"],
        "turns": bucket["turns"],
        "cost_usd": bucket["cost_usd"] if bucket["cost_n"] else None,
    }
    if extra:
        out.update(extra)
    return out


def summarize_attribution(attributed: dict[str, Any]) -> dict[str, Any]:
    rows = attributed.get("jobs") or []
    totals = _empty_tot()
    by_caller: dict[str, dict[str, Any]] = defaultdict(_empty_tot)
    by_slug: dict[str, dict[str, Any]] = defaultdict(_empty_tot)
    by_provider: dict[str, dict[str, Any]] = defaultdict(_empty_tot)
    for row in rows:
        _add_tot(totals, row)
        _add_tot(by_caller[str(row.get("caller") or "?")], row)
        _add_tot(by_slug[str(row.get("task_slug") or "?")], row)
        _add_tot(by_provider[str(row.get("provider") or "?")], row)

    unmatched = [r for r in rows if r.get("source") == "job"]
    return {
        "ts": attributed.get("ts"),
        "since": attributed.get("since"),
        "cutoff": attributed.get("cutoff"),
        "totals": _freeze_tot(totals),
        "by_caller": [
            _freeze_tot(by_caller[k], extra={"caller": k}) for k in sorted(by_caller)
        ],
        "by_task_slug": [
            _freeze_tot(by_slug[k], extra={"task_slug": k}) for k in sorted(by_slug)
        ],
        "by_provider": [
            _freeze_tot(by_provider[k], extra={"provider": k})
            for k in sorted(by_provider)
        ],
        "unmatched": unmatched,
        "jobs": rows,
        "n_skipped_since": attributed.get("n_skipped_since", 0),
    }


def report_agentic_step(
    *,
    jobs_path: str | Path | None = None,
    spend: str | Path | None = None,
    caller: str | None = None,
    task_slug: str | None = None,
    since: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    jobs = load_jobs(jobs_path)
    rows = load_spend(spend)
    attributed = attribute_jobs(
        jobs, rows, caller=caller, task_slug=task_slug, since=since, now=now
    )
    summary = summarize_attribution(attributed)
    summary["jobs_file"] = str(agentic_step_jobs_path(jobs_path))
    summary["spend_file"] = str(spend_path(spend))
    return summary


def evaluate_check(
    summary: dict[str, Any],
    *,
    threshold_tokens: int = DEFAULT_THRESHOLD_TOKENS,
    threshold_usd: float = DEFAULT_THRESHOLD_USD,
) -> dict[str, Any]:
    totals = summary.get("totals") or {}
    tokens = int(totals.get("total_tokens") or 0)
    cost = totals.get("cost_usd")
    reasons: list[str] = []
    if tokens >= int(threshold_tokens):
        reasons.append(f"tokens {tokens} >= {int(threshold_tokens)}")
    if isinstance(cost, (int, float)) and float(cost) >= float(threshold_usd):
        reasons.append(f"usd {float(cost):.4f} >= {float(threshold_usd)}")
    substantial = bool(reasons)
    return {
        "verdict": "substantial" if substantial else "ok",
        "since": summary.get("since") or DEFAULT_SINCE,
        "cutoff": summary.get("cutoff"),
        "threshold_tokens": int(threshold_tokens),
        "threshold_usd": float(threshold_usd),
        "total_tokens": tokens,
        "cost_usd": cost,
        "jobs": int(totals.get("jobs") or 0),
        "matched": int(totals.get("matched") or 0),
        "unmatched": int(totals.get("unmatched") or 0),
        "reasons": reasons,
        "by_caller": summary.get("by_caller") or [],
        "by_task_slug": summary.get("by_task_slug") or [],
        "by_provider": summary.get("by_provider") or [],
        "jobs_file": summary.get("jobs_file"),
        "spend_file": summary.get("spend_file"),
        "ts": summary.get("ts"),
    }


def print_agentic_step(summary: dict[str, Any]) -> None:
    ts = summary.get("ts") or ""
    since = summary.get("since") or "all"
    print(f"\n  AGENTIC_STEP BURN  (since {since}, as of {ts})\n")
    totals = summary.get("totals") or {}
    print(
        f"  totals   jobs {int(totals.get('jobs') or 0)}"
        f"  matched {int(totals.get('matched') or 0)}"
        f"  unmatched {int(totals.get('unmatched') or 0)}"
        f"  tokens {_fmt_int(int(totals.get('total_tokens') or 0))}"
        f"  {_fmt_usd(totals.get('cost_usd'))}"
    )
    print()

    def _block(title: str, rows: list[dict[str, Any]], label_key: str) -> None:
        print(f"  {title}")
        if not rows:
            print("    (none)")
            print()
            return
        print(
            f"    {label_key:<22} {'jobs':>5} {'match':>5} "
            f"{'in':>8} {'cached':>8} {'out':>8} {'$':>8}"
        )
        for r in rows:
            label = str(r.get(label_key) or "?")
            if len(label) > 22:
                label = label[:21] + "…"
            print(
                f"    {label:<22} "
                f"{int(r.get('jobs') or 0):>5} "
                f"{int(r.get('matched') or 0):>5} "
                f"{_fmt_int(int(r.get('input_tokens') or 0)):>8} "
                f"{_fmt_int(int(r.get('cached_tokens') or 0)):>8} "
                f"{_fmt_int(int(r.get('output_tokens') or 0)):>8} "
                f"{_fmt_usd(r.get('cost_usd')):>8}"
            )
        print()

    _block("by caller", summary.get("by_caller") or [], "caller")
    _block("by task_slug", summary.get("by_task_slug") or [], "task_slug")
    _block("by provider", summary.get("by_provider") or [], "provider")

    unmatched = summary.get("unmatched") or []
    print("  unmatched (source=job — spend.py does not harvest this harness/model)")
    if not unmatched:
        print("    (none)")
        print()
    else:
        print(
            f"    {'provider':<10} {'model':<18} {'tokens':>8} {'$':>8}  "
            f"caller / task_slug / job"
        )
        for r in unmatched:
            mid = str(r.get("model") or "—")
            if len(mid) > 18:
                mid = mid[:17] + "…"
            jid = str(r.get("job_id") or "")
            if len(jid) > 12:
                jid = jid[:8] + "…"
            print(
                f"    {str(r.get('provider') or '?'):<10} "
                f"{mid:<18} "
                f"{_fmt_int(int(r.get('total_tokens') or 0)):>8} "
                f"{_fmt_usd(r.get('cost_usd')):>8}  "
                f"{r.get('caller') or '—'} / {r.get('task_slug') or '—'} / {jid}"
            )
        print()

    jobs_file = summary.get("jobs_file")
    spend_file = summary.get("spend_file")
    if jobs_file:
        print(f"  jobs:  {jobs_file}")
    if spend_file:
        print(f"  spend: {spend_file}")
    print()
