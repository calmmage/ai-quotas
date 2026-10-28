"""Credential-free helpers for the optional private Antigravity adapter.

Quota RPCs can omit Gemini. Individual model caps are separate observations,
reused from real CLI runs: this module never launches a model or reads auth.
"""

from __future__ import annotations

import os
import re
import math
import platform
import shutil
import subprocess
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path


@lru_cache(maxsize=1)
def native_user_agent() -> str:
    """Use the installed CLI's version and quota-capable client identity."""
    binary = shutil.which("agy") or str(Path.home()/".local/bin/agy")
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5, check=True)
    match = re.search(r"\d+\.\d+\.\d+", result.stdout)
    if not match:
        raise RuntimeError("cannot determine installed agy version")
    return f"antigravity/cli/{match[0]} ({platform.system().lower()}; {platform.machine().lower()})"


def quota_groups(payload: dict) -> list[dict]:
    wrapped = payload.get("quotaSummary")
    source = wrapped if isinstance(wrapped, dict) else payload
    groups = source.get("groups")
    if isinstance(groups, list):
        return [g for g in groups if isinstance(g, dict)]
    # Some summaries put bucket IDs directly at the top level.
    buckets = source.get("buckets") or []
    grouped = {}
    for bucket in buckets if isinstance(buckets, list) else []:
        if not isinstance(bucket, dict):
            continue
        key = str(bucket.get("bucketId") or "").lower()
        name = "Gemini 4 Argon" if "argon" in key else "Gemini Models" if "gemini" in key else "Claude and GPT models"
        grouped.setdefault(name, []).append(bucket)
    return [dict(displayName=name, buckets=values) for name, values in grouped.items()]


def _argon_group(group: dict) -> bool:
    return "argon" in f"{group.get('displayName', '')} {group.get('description', '')}".lower()


def summary_has_gemini(payload: dict) -> bool:
    return any("gemini" in f"{g.get('displayName', '')} {g.get('description', '')}".lower()
               and not _argon_group(g) for g in quota_groups(payload))


def fetch_complete_quota(token, *, load, fetch, summary_urls, quota_urls) -> dict:
    """Prefer production and keep trying after a partial successful response."""
    project, plan = load(token)
    errors, partial = [], {}
    attempts = [summary_urls] if isinstance(summary_urls, str) else [[url] for url in sorted(
        summary_urls, key=lambda url: not url.startswith("https://cloudcode-pa.googleapis.com/"))]
    for body in ([{"project": project}, {}] if project else [{}]):
        for urls in attempts:
            payload, failed = fetch(urls, token, body)
            errors.extend(failed)
            if payload is None:
                continue
            for group in quota_groups(payload):
                partial.setdefault(str(group.get("displayName")), group)
            combined = {"groups": list(partial.values()), "_agy_plan": plan}
            if summary_has_gemini(combined):
                return combined
    if partial:
        legacy, _ = fetch(quota_urls, token, {})
        return {"groups": list(partial.values()), "_agy_plan": plan,
                "_gemini_fallback": {**(legacy or {}), "_agy_plan": plan}}
    payload, failed = fetch(quota_urls, token, {})
    if payload is not None:
        return {**payload, "_agy_plan": plan}
    raise RuntimeError("; ".join([*errors, *failed]) or "all quota endpoints failed")


def complete_quota_rows(ts, payload, *, summary_parser, bucket_parser) -> list[dict]:
    if ("groups" in payload or "quotaSummary" in payload
            or any(isinstance(b, dict) and b.get("bucketId") for b in payload.get("buckets", []) or [])):
        groups = quota_groups(payload)
        rows = summary_parser(ts, {**payload, "groups": [g for g in groups if not _argon_group(g)],
                                  "quotaSummary": None})
        for group in groups:
            if not _argon_group(group):
                continue
            for bucket in group.get("buckets") or []:
                if not isinstance(bucket, dict):
                    continue
                fraction = bucket.get("remainingFraction")
                if not isinstance(fraction, (float, int)) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
                    continue
                horizon = str(bucket.get("window") or "").lower()
                if not bucket.get("resetTime") or horizon not in {"weekly", "week", "5h"}:
                    continue
                rows.append(dict(ts=ts, provider="agy", window=("week" if horizon != "5h" else "5h")+"_gemini_argon",
                                 used_percent=(1-fraction)*100, status="ok", resets_at=bucket["resetTime"],
                                 plan=payload.get("_agy_plan"), quota_source="retrieveUserQuotaSummary"))
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


def argon_success_observation(ts: str, *, account: str, model: str, evidence_file: str) -> dict:
    """Record a successful real run without inventing a remaining percentage.

    Call only after an actual successful response for this verified login.
    Sampling never invokes a model to manufacture this observation.
    """
    from ai_quotas.accounts import normalize_account

    account = normalize_account(account)
    if not account or "argon" not in model:
        raise ValueError("a verified login and Argon model are required")
    return dict(ts=ts, observed_at=ts, provider="agy", account=account,
                window="week_gemini_argon", status="unavailable", used_percent=None,
                resets_at=None, individual_cap=True, availability="usable",
                model=model, evidence_file=Path(evidence_file).name,
                reason="Successful Argon response; remaining quota not reported")


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
