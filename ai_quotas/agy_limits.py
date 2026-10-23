"""Credential-free helpers for the optional private Antigravity adapter.

Quota RPCs can omit Gemini. Individual model caps are separate observations,
reused from real CLI runs: this module never launches a model or reads auth.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


def summary_has_gemini(payload: dict) -> bool:
    wrapped = payload.get("quotaSummary")
    groups = payload.get("groups")
    if not isinstance(groups, list):
        groups = wrapped.get("groups", []) if isinstance(wrapped, dict) else []
    return any("gemini" in f"{g.get('displayName', '')} {g.get('description', '')}".lower()
               for g in groups if isinstance(g, dict))


def fetch_complete_quota(token, *, load, fetch, summary_urls, quota_urls) -> dict:
    """Existing private RPC/auth callbacks; at most one legacy fallback group.

    The summary remains authoritative for Claude/GPT. An omitted Gemini group
    needs the legacy endpoint even though the summary request succeeded.
    """
    project, plan = load(token)
    errors = []
    for body in ([{"project": project}, {}] if project else [{}]):
        payload, failed = fetch(summary_urls, token, body)
        errors.extend(failed)
        if payload is None:
            continue
        payload = {**payload, "_agy_plan": plan}
        if not summary_has_gemini(payload):
            legacy, _ = fetch(quota_urls, token, {})
            payload["_gemini_fallback"] = {**(legacy or {}), "_agy_plan": plan}
        return payload
    payload, failed = fetch(quota_urls, token, {})
    if payload is not None:
        return {**payload, "_agy_plan": plan}
    raise RuntimeError("; ".join([*errors, *failed]) or "all quota endpoints failed")


def complete_quota_rows(ts, payload, *, summary_parser, bucket_parser) -> list[dict]:
    if "groups" in payload or "quotaSummary" in payload:
        rows = summary_parser(ts, payload)
        fallback = bucket_parser(ts, payload["_gemini_fallback"]) if payload.get("_gemini_fallback") else []
    else:
        rows, fallback = bucket_parser(ts, payload), []
    return supplement_gemini_rows(ts, rows, fallback)


def supplement_gemini_rows(ts: str, rows: list[dict], fallback: list[dict]) -> list[dict]:
    """Keep the summary, supplement only absent Gemini windows, mark omissions.

    Never turn the legacy 5h pool into a weekly or an Argon allowance.
    """
    result = list(rows)
    present = {r.get("window") for r in rows}
    for row in fallback:
        window = str(row.get("window", ""))
        if "gemini" in window and window not in present:
            result.append(row)
            present.add(window)
    plan = next((r.get("plan") for r in rows if r.get("plan")), None)
    for horizon in ("week", "5h"):
        for family in ("pro", "flash"):
            window = f"{horizon}_gemini_{family}"
            if window not in present:
                result.append(dict(ts=ts, provider="agy", window=window,
                                   status="unavailable", used_percent=None,
                                   resets_at=None, plan=plan, limit=None, used=None, window_missing=True,
                                   reason=f"Gemini {horizon} not reported by either quota endpoint"))
    return result


_STAMP = re.compile(r"^[IWEF](\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})")
_MODEL = re.compile(r'(?:Model ID |Resolving model |model=")([a-z0-9.-]+)')
_ACCOUNT = re.compile(r"applyAuthResult: email=([^,\s]+)")
_RESET = re.compile(r"Individual quota reached\..*?Resets in\s+((?:\d+(?:\.\d+)?\s*[dhms]\s*)+)", re.I)


def observed_argon_caps(ts: str, *, log_dir: Path | None = None) -> list[dict]:
    """Reuse bounded local logs. Zero prompts, zero credentials, fixed deadline.

    Only files with a verified account and an Argon model context qualify.
    Glog carries local wall time; the filename supplies its year. A sample's
    timestamp never extends a deadline computed from the original error time.
    """
    now = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    log_dir = log_dir or Path(os.environ.get("AI_QUOTAS_AGY_LOG_DIR") or
                              Path.home()/".gemini/antigravity-cli/log")
    best: dict[str, dict] = {}
    for path in sorted(log_dir.glob("cli-????????_??????.log"))[-256:]:
        try:
            year = int(path.name[4:8])
            date = datetime.strptime(path.name[4:12], "%Y%m%d").astimezone()
            if date < now-timedelta(days=8) or date > now+timedelta(days=1):
                continue
            # Bound IO even if a real agent writes a very large log.
            with path.open("rb") as f:
                data = f.read(1024 * 1024).decode("utf-8", "replace")
        except (OSError, ValueError):
            continue
        account = None
        model = None
        for line in data.splitlines():
            if match := _ACCOUNT.search(line):
                account = match[1].lower()
                if "@" not in account:
                    account = None
            if match := _MODEL.search(line):
                model = match[1]
            reset = _RESET.search(line)
            stamp = _STAMP.search(line)
            if not reset or not stamp or not account or not model or "argon" not in model:
                continue
            try:
                observed = datetime(year, *map(int, stamp.groups())).astimezone()
            except ValueError:
                continue
            if observed > now:
                continue
            seconds = sum(float(n)*{"d":86400, "h":3600, "m":60, "s":1}[unit.lower()]
                          for n, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([dhms])", reset[1], re.I))
            if not 0 < seconds <= 8*86400:
                continue
            deadline = observed+timedelta(seconds=seconds)
            if deadline <= now:
                continue
            row = dict(ts=ts, provider="agy", window="week_gemini_argon", account=account,
                       used_percent=100.0, resets_at=deadline.isoformat(), status="ok",
                       plan=None, limit=None, used=None, individual_cap=True,
                       observed_at=observed.isoformat(), evidence_file=path.name,
                       model=model, reason="Argon individual quota reached (observed CLI error; no probe)")
            if account not in best or observed > datetime.fromisoformat(best[account]["observed_at"]):
                best[account] = row
    return list(best.values())
