"""Quota burner dry-run.

Reads spare quota and proposes agent_run rows for the night report.
This module only reads. It does not launch agents, write the database,
or send alerts.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_quotas.accounts import split_accounts
from ai_quotas.core import RETIRED_WINDOWS, evaluate, pick_harness
from ai_quotas.tiers import candidates_for, describe_harness

# Council queue, 17 Oct 2026: three bot tasks stalled behind chat 01a13738.
STALLED_TASKS: tuple[dict[str, str], ...] = (
    {
        "id": "vision-board",
        "title": "Vision board",
        "kind": "stalled",
        "tier": "smart",
        "quote": "3 bot do tasks stalled 52h behind idle chat 01a13738",
    },
    {
        "id": "env-add",
        "title": "env-add",
        "kind": "stalled",
        "tier": "smart",
        "quote": "3 bot do tasks stalled 52h behind idle chat 01a13738",
    },
    {
        "id": "instagram",
        "title": "instagram",
        "kind": "stalled",
        "tier": "smart",
        "quote": "3 bot do tasks stalled 52h behind idle chat 01a13738",
    },
)

DEFAULT_VISIONS = Path(
    "/Users/petrlavrov/calm/state/orca-tab-sessions/launch-2026-10-13"
    "/visions-board.data/final.json"
)
FIXTURE_SAMPLES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "multi.jsonl"
SPARE_GATE = 50.0
FABLE_RULE = "Fable NEVER builds. Fable only plans."


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def read_sqlite_ro(path: Path) -> list[dict[str, Any]]:
    """Read quota_samples through SQLite URI mode=ro. Raises if the file is locked."""
    uri = "file:" + str(path.resolve()) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        fetched = conn.execute(
            "SELECT payload_json FROM quota_samples ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    rows: list[dict[str, Any]] = []
    for (payload,) in fetched:
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def load_samples_ro(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Return (rows, source). Falls back to the committed fixture if sqlite will not open."""
    suffix = path.suffix.lower()
    if suffix in {".sqlite", ".sqlite3", ".db"}:
        try:
            return read_sqlite_ro(path), "sqlite-ro"
        except (OSError, sqlite3.Error) as exc:
            if FIXTURE_SAMPLES.is_file():
                return _read_jsonl(FIXTURE_SAMPLES), "fixture:" + type(exc).__name__
            raise
    if path.is_file():
        return _read_jsonl(path), "jsonl"
    if FIXTURE_SAMPLES.is_file():
        return _read_jsonl(FIXTURE_SAMPLES), "fixture:missing"
    return [], "empty"


def _primary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = [
        row for row in rows
        if (row.get("provider"), row.get("window")) not in RETIRED_WINDOWS
    ]
    return split_accounts(kept)[0]


def load_queue(visions_path: Path | None = None) -> tuple[list[dict[str, str]], str]:
    """Stalled tasks, then every vision whose built state is code (not live)."""
    items = [dict(row) for row in STALLED_TASKS]
    path = visions_path or DEFAULT_VISIONS
    if not path.is_file():
        return items, f"visions missing: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return items, f"visions unreadable: {exc}"
    if not isinstance(data, list):
        return items, "visions file is not a list"
    partial = 0
    for row in data:
        if not isinstance(row, dict):
            continue
        built = row.get("built") if isinstance(row.get("built"), dict) else {}
        if built.get("state") != "code":
            continue
        partial += 1
        quote = str(row.get("quote") or row.get("need") or "")
        items.append({
            "id": str(row.get("id") or f"vision-{partial}"),
            "title": str(row.get("title") or "untitled"),
            "kind": "vision",
            "tier": "cheap",
            "quote": quote[:400],
        })
    return items, f"partial visions: {partial}"


def _spare_of(row: dict[str, Any]) -> float | None:
    used = row.get("used_percent")
    if used is None:
        return None
    try:
        return round(100.0 - float(used), 1)
    except (TypeError, ValueError):
        return None


def spare_table(result: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per provider verdict, plus each agy week bucket."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(provider: str, window: str | None, used: Any, verdict: str) -> None:
        key = (provider, str(window or ""))
        if key in seen:
            return
        seen.add(key)
        spare = None if used is None else _spare_of({"used_percent": used})
        out.append({
            "provider": provider,
            "window": window,
            "used_percent": used,
            "spare_percent": spare,
            "verdict": verdict,
        })

    for provider, row in (result.get("verdicts") or {}).items():
        add(provider, row.get("window"), row.get("used_percent"), str(row.get("verdict") or "UNKNOWN"))
    for row in result.get("windows") or []:
        window = str(row.get("window") or "")
        if window.startswith("5h"):
            continue
        if row.get("provider") != "agy" and window in {None, ""}:
            continue
        if row.get("provider") == "agy" or window.startswith("week"):
            add(
                str(row.get("provider") or ""),
                window,
                row.get("used_percent"),
                str(row.get("verdict") or "UNKNOWN"),
            )
    return out


def _window_row(result: dict[str, Any], harness: str) -> dict[str, Any] | None:
    picked = pick_harness(result, candidates=(harness,))
    chosen = picked.get("chosen") or {}
    if not chosen:
        return None
    return {
        "harness": harness,
        "window": chosen.get("window"),
        "used_percent": chosen.get("used_percent"),
        "verdict": chosen.get("verdict"),
        "spare_percent": _spare_of(chosen),
    }


def assign_runs(
    result: dict[str, Any],
    items: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """One proposed agent_run per item. Nothing is launched."""
    fable = _window_row(result, "claude-fable")
    fable_ok = bool(fable and fable.get("verdict") == "OK")
    runs: list[dict[str, Any]] = []
    for item in items:
        asked = item.get("tier") or "cheap"
        role = "build" if asked == "smart" else "context"
        picked = pick_harness(result, candidates=candidates_for(asked, role=role))
        harness = picked.get("harness")
        via_middle = False
        if not harness:
            mid = pick_harness(result, candidates=candidates_for("middle"))
            harness = mid.get("harness")
            picked = mid
            via_middle = bool(harness)
        spec = describe_harness(harness) if harness else {}
        chosen_row = picked.get("chosen") or {}
        spare = _spare_of(chosen_row) if harness else None
        verdict = chosen_row.get("verdict") if harness else None
        clears = bool(spare is not None and spare > SPARE_GATE and verdict == "OK")
        assigned = "middle" if via_middle else asked
        if not harness:
            reason = f"no OK harness in {asked} or middle"
            if asked == "smart":
                reason = f"{reason}. {FABLE_RULE}"
            selected = False
        else:
            gate = (
                f"clears {SPARE_GATE:.0f}% spare gate"
                if clears
                else f"spare {spare}% does not clear the {SPARE_GATE:.0f}% gate"
            )
            if via_middle:
                reason = f"{asked} tier blocked; Grok in the middle; {gate}"
            else:
                reason = f"{item.get('kind')} → {assigned} {spec.get('label')}; {gate}"
            if asked == "smart":
                reason = f"{reason}. {FABLE_RULE}"
            selected = True
        runs.append({
            "type": "agent_run",
            "status": "proposed",
            "ran": False,
            "id": item.get("id"),
            "title": item.get("title"),
            "kind": item.get("kind"),
            "tier": assigned,
            "asked_tier": asked,
            "harness": harness,
            "model": spec.get("model"),
            "role": spec.get("role"),
            "window": chosen_row.get("window"),
            "used_percent": chosen_row.get("used_percent"),
            "spare_percent": spare,
            "verdict": verdict,
            "clears_spare_gate": clears,
            "reason": reason,
            "selected": selected,
            "quote": item.get("quote") or "",
            "plan_harness": "claude-fable" if asked == "smart" and fable_ok else None,
        })
    return runs


def run_dry(
    path: Path,
    *,
    now: datetime | None = None,
    visions_path: Path | None = None,
    items: list[dict[str, str]] | None = None,
    samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate spare quota and propose rows. Live launch count is always zero."""
    if samples is None:
        raw, source = load_samples_ro(path)
    else:
        raw, source = samples, "given"
    rows = _primary_rows(raw)
    result = evaluate(rows, now=now or datetime.now(timezone.utc).astimezone())
    if items is None:
        queue, queue_note = load_queue(visions_path)
    else:
        queue, queue_note = items, "given"
    runs = assign_runs(result, queue)
    return {
        "command": "burn",
        "dry_run": True,
        "dispatch_calls": 0,
        "source": source,
        "queue": queue_note,
        "ts": result.get("ts"),
        "spare": spare_table(result),
        "runs": runs,
        "selected": sum(1 for row in runs if row.get("selected")),
        "candidates": len(runs),
    }


def format_log(report: dict[str, Any]) -> str:
    lines = [
        "aiq burn --dry-run",
        f"source={report.get('source')}",
        f"queue={report.get('queue')}",
        "dispatch_calls=0",
    ]
    for row in report.get("spare") or []:
        lines.append(
            "spare provider={provider} window={window} used={used} spare={spare} verdict={verdict}".format(
                provider=row.get("provider"),
                window=row.get("window"),
                used=row.get("used_percent"),
                spare=row.get("spare_percent"),
                verdict=row.get("verdict"),
            )
        )
    for run in report.get("runs") or []:
        lines.append(
            "candidate id={id} title={title} tier={tier} harness={harness} "
            "model={model} reason={reason} status=proposed, not run".format(
                id=run.get("id"),
                title=json.dumps(run.get("title"), ensure_ascii=False),
                tier=run.get("tier"),
                harness=run.get("harness"),
                model=run.get("model"),
                reason=json.dumps(run.get("reason"), ensure_ascii=False),
            )
        )
    lines.append(
        f"candidates={report.get('candidates')} selected={report.get('selected')}"
    )
    return "\n".join(lines)
