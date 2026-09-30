"""Quiet alerts: low reserves AND severe burn, plus spare-quota reminders.

Burn alerts remember the highest delivered severity until the quota resets,
even when the condition clears. Spare-quota reminders fire before a weekly
or monthly reset when a lot of the window is still unspent. State lives in
``<data_dir>/alert-state.json``. Fail-open.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_quotas import core
from ai_quotas.notify import ping_role, send_email, send_telegram, send_urgent
from ai_quotas.paths import data_dir, samples_path
from ai_quotas.reset_credits import burn_relaxation, usable_credits
from ai_quotas.storage import load_reset_credits

# Spare quota before the scheduled reset. "More than half" two days out,
# "more than a quarter" one day out. The one-day half-left case is also
# an urgent personal-Telegram alert.
SPARE_2D_HOURS = 48.0
SPARE_1D_HOURS = 24.0
SPARE_HALF = 50.0
SPARE_QUARTER = 25.0
BURN_WARN_PACE = 300.0
BURN_STOP_PACE = 500.0
BURN_RESERVE_FACTOR = 0.5
STATE_NAME = "alert-state.json"
_SESSION_WINDOWS = ("5h",)
_SKIP_WINDOWS = frozenset({"overage_credits", "unknown", "credits", "free_daily", "—"})


def state_path(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    return data_dir() / STATE_NAME


def load_state(path: str | Path | None = None) -> dict[str, Any]:
    p = state_path(path)
    if not p.is_file():
        return {"sent": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sent": {}}
    if not isinstance(data, dict):
        return {"sent": {}}
    sent = data.get("sent")
    if not isinstance(sent, dict):
        sent = {}
    return {"sent": sent}


def save_state(state: dict[str, Any], path: str | Path | None = None) -> None:
    p = state_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sent": dict(state.get("sent") or {})}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)


def remaining_percent(used: float | None) -> float | None:
    if used is None:
        return None
    return 100.0 - float(used)


def _is_primary_window(window: str) -> bool:
    if window in _SKIP_WINDOWS or window in _SESSION_WINDOWS:
        return False
    if window.startswith("5h"):
        return False
    return True


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _reset_time(value: Any) -> datetime | None:
    reset = core.parse_ts(value) if isinstance(value, str) else None
    if reset is None:
        return None
    reset = reset.replace(tzinfo=reset.tzinfo or timezone.utc).astimezone(timezone.utc)
    # Providers report the reset with sub-second jitter (07:59:59.6 vs
    # 08:00:00.2); round to the minute so the dedupe fingerprint is stable.
    return datetime.fromtimestamp(round(reset.timestamp() / 60) * 60, timezone.utc)


def burn_severity(row: dict[str, Any]) -> str | None:
    """Notification policy, independent of the more sensitive dashboard verdict.

    Reserve gate: remaining quota <= half the fraction of the period left.
    With one day of a week left that is 50 * 24 / 168 = 7.14% remaining.
    Exhausted quota warrants STOP even without a measurable recent burn rate.
    """
    used = row.get("used_percent")
    hours_left = row.get("hours_to_reset")
    window_hours = row.get("window_hours")
    if (
        not _is_primary_window(str(row.get("window") or ""))
        or not all(_finite(v) for v in (used, hours_left, window_hours))
        or hours_left <= 0
        or window_hours <= 0
        or _reset_time(row.get("resets_at")) is None
    ):
        return None
    remaining = 100.0 - used
    threshold = 100.0 * BURN_RESERVE_FACTOR * min(1.0, hours_left / window_hours)
    if remaining > threshold:
        return None
    if remaining <= 0:
        return "STOP"
    pace = row.get("pace_pct")
    if not _finite(pace):
        return None
    if pace >= BURN_STOP_PACE:
        return "STOP"
    if pace >= BURN_WARN_PACE:
        return "WARN"
    return None


def items_from_evaluate(
    result: dict[str, Any], *, include_reset_soon: bool = False,
    credit_rows: list[dict[str, Any]] | None = None, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build alert items from ``core.evaluate`` output."""
    items: list[dict[str, Any]] = []
    verdicts = result.get("verdicts") or {}
    for provider, row in verdicts.items():
        window = str(row.get("window") or "")
        used = row.get("used_percent")
        remaining = remaining_percent(used if isinstance(used, (int, float)) else None)
        hours_left = row.get("hours_to_reset")
        severity = burn_severity(row)
        available = usable_credits(credit_rows or [], provider, window, now=now)
        relaxed = burn_relaxation(available, now=now)
        # Burning is fine with spare/expiring resets. Exhaustion still needs a
        # human to redeem one; never consume a reset automatically.
        if relaxed and remaining is not None and remaining > 0:
            severity = None
        reset = _reset_time(row.get("resets_at"))
        resets_at = reset.isoformat() if reset is not None else None
        if severity is not None:
            items.append(
                {
                    "kind": "burn",
                    "provider": provider,
                    "window": window or "week",
                    "severity": severity,
                    "remaining": remaining,
                    "used_percent": used,
                    "hours_to_reset": hours_left,
                    "pace": row.get("pace"),
                    "projected_final": row.get("projected_final"),
                    "resets_at": resets_at,
                    "resets_available": len(available),
                    "fingerprint": f"burn:{provider}:{window}:{resets_at}",
                }
            )
        if (
            include_reset_soon
            and _finite(remaining)
            and 0 < remaining <= 100.0
            and reset is not None
            and _finite(hours_left)
            and _is_primary_window(window or "week")
        ):
            hours = float(hours_left)
            window_name = window or "week"
            base = {
                "provider": provider,
                "window": window_name,
                "remaining": remaining,
                "used_percent": used,
                "hours_to_reset": hours_left,
                "pace": row.get("pace"),
                "projected_final": row.get("projected_final"),
                "resets_at": resets_at,
            }
            # Each lead has its own fingerprint so the 2-day note does not
            # swallow the 1-day note, and a missed 2-day window is not sent
            # late as if it were still two days out.
            if SPARE_1D_HOURS < hours <= SPARE_2D_HOURS and remaining > SPARE_HALF:
                items.append({
                    **base,
                    "kind": "spare",
                    "lead": "2d",
                    "severity": "WARN",
                    "fingerprint": f"spare:2d:{provider}:{window_name}:{resets_at}",
                })
            if 0 < hours <= SPARE_1D_HOURS and remaining > SPARE_QUARTER:
                items.append({
                    **base,
                    "kind": "spare",
                    "lead": "1d",
                    "severity": "WARN",
                    "fingerprint": f"spare:1d:{provider}:{window_name}:{resets_at}",
                })
            if 0 < hours <= SPARE_1D_HOURS and remaining > SPARE_HALF:
                items.append({
                    **base,
                    "kind": "spare_urgent",
                    "lead": "1d",
                    "severity": "STOP",
                    "fingerprint": f"spare:urgent:{provider}:{window_name}:{resets_at}",
                })
    return items


def apply_dedupe(
    items: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Return (new items, pruned state, pruned+new state).

    Keep the highest delivered severity until reset, including quiet runs.
    Legacy fingerprints without reset metadata expire when their condition ends.
    """
    now = now or datetime.now(timezone.utc).astimezone()
    now = now.replace(tzinfo=now.tzinfo or timezone.utc)
    ts = now.isoformat(timespec="seconds")
    previous: dict[str, Any] = dict(state.get("sent") or {})
    current = {str(it["fingerprint"]) for it in items}
    pruned = {}
    for key, meta in previous.items():
        if not isinstance(meta, dict):
            continue
        reset = _reset_time(meta.get("resets_at"))
        if (reset is not None and reset > now) or (reset is None and key in current):
            pruned[key] = meta
    fresh: list[dict[str, Any]] = []
    merged = dict(pruned)
    for item in items:
        fp = str(item["fingerprint"])
        if fp in merged:
            ranks = {"INFO": 0, "WARN": 1, "STOP": 2}
            previous_rank = ranks.get(merged[fp].get("severity"), 0)
            if item["kind"] != "burn" or ranks.get(item.get("severity"), 0) <= previous_rank:
                continue
        merged[fp] = {
            "ts": ts,
            "kind": item["kind"],
            "severity": item.get("severity"),
            "resets_at": item.get("resets_at"),
        }
        fresh.append(item)
    return fresh, {"sent": pruned}, {"sent": merged}


def format_message(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = [f"ai-quotas alerts ({len(items)})", ""]
    for it in items:
        remaining = it.get("remaining")
        rem_s = f"{remaining:.0f}%" if isinstance(remaining, (int, float)) else "—"
        reset_s = core.format_duration_hours(it.get("hours_to_reset"))
        provider = str(it.get("provider") or "")
        window = str(it.get("window") or "")
        if it.get("kind") == "burn":
            lines.append(f"BURN  {provider} {window}  {it.get('severity')}")
            extra = f"remaining {rem_s} · reset in {reset_s}"
            pace = it.get("pace")
            if pace and pace != "—":
                extra += f" · {pace}"
            projected = it.get("projected_final")
            if isinstance(projected, (int, float)):
                extra += f" · projected {projected:.0f}%"
            lines.append(extra)
            if it.get("remaining", 1) <= 0 and it.get("resets_available", 0):
                lines.append(f"{it['resets_available']} reset(s) available to redeem")
        elif it.get("kind") == "spare_urgent":
            lines.append(f"URGENT  USE IT BEFORE RESET  {provider} {window}")
            lines.append(
                f"more than half still unspent · remaining {rem_s} · reset in {reset_s}"
            )
        else:
            lead = {"2d": "2 days ahead", "1d": "1 day ahead"}.get(it.get("lead") or "", "")
            title = "USE IT BEFORE RESET" + (f"  {lead}" if lead else "")
            lines.append(f"⚠️ {title}  {provider} {window}")
            lines.append(
                f"remaining {rem_s} · reset in {reset_s} · unused quota wipes at reset"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_alerts(
    *,
    path: str | Path | None = None,
    state_file: str | Path | None = None,
    send: bool = True,
    dry_run: bool = False,
    include_reset_soon: bool = False,
    now: datetime | None = None,
    sender: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    samples = core.load_samples(path if path is not None else samples_path())
    result = core.evaluate(samples, now=now)
    try:
        credits = load_reset_credits(path if path is not None else samples_path())
    except (OSError, ValueError, sqlite3.Error):
        credits = []
    items = items_from_evaluate(result, include_reset_soon=include_reset_soon, credit_rows=credits, now=now)
    state = load_state(state_file)
    fresh, pruned_state, _merged = apply_dedupe(items, state, now=now)
    regular = [it for it in fresh if it.get("kind") != "spare_urgent"]
    urgent = [it for it in fresh if it.get("kind") == "spare_urgent"]
    moment = now or datetime.now(timezone.utc).astimezone()
    report: dict[str, Any] = {
        "ts": moment.isoformat(timespec="seconds"),
        "firing": len(items),
        "new": len(fresh),
        "items": fresh,
        "message": format_message(regular) if regular else "",
        "urgent_message": format_message(urgent) if urgent else "",
        "delivery": "skip",
        "email": "skip",
        "urgent": "skip",
    }
    # Previewing must not change live dedupe state.
    if not dry_run and send:
        save_state(pruned_state, state_file)
    if not fresh:
        return report
    if dry_run or not send:
        report["delivery"] = "dry-run" if dry_run else "not-sent"
        if urgent:
            report["urgent"] = report["delivery"]
        return report
    deliver = sender or send_telegram
    marked = dict(pruned_state.get("sent") or {})
    stamp = moment.isoformat(timespec="seconds")

    def _keep(sent_items: list[dict[str, Any]]) -> None:
        for item in sent_items:
            marked[str(item["fingerprint"])] = {
                "ts": stamp,
                "kind": item["kind"],
                "severity": item.get("severity"),
                "resets_at": item.get("resets_at"),
            }

    if regular:
        status = deliver(report["message"])
        report["delivery"] = status
        report["email"] = send_email(report["message"])
        if status == "sent" or report["email"] == "sent":
            _keep(regular)
    if urgent:
        # No personal-account command: remember the skip so a stock install
        # does not retry this period every sample. An error is not remembered.
        urgent_status = send_urgent(report["urgent_message"])
        report["urgent"] = urgent_status
        if urgent_status in {"sent", "skip"}:
            _keep(urgent)
    if marked != (pruned_state.get("sent") or {}):
        save_state({"sent": marked}, state_file)
    # Callers that only look at merged delivery still see a telegram send.
    if report["delivery"] == "skip" and not regular and report["urgent"] == "sent":
        report["delivery"] = "sent"
    return report


def run_after_sample(
    *,
    path: str | Path | None = None,
    send: bool = True,
) -> dict[str, Any]:
    """Best-effort: alerts then Healthchecks ping. Never raises."""
    report: dict[str, Any] = {"alerts": None, "healthchecks": "skip"}
    try:
        report["alerts"] = run_alerts(path=path, send=send, include_reset_soon=True)
    except Exception as exc:  # noqa: BLE001 — sample agent must not die
        report["alerts"] = {"error": str(exc)}
        print(f"alert error: {exc}", file=sys.stderr)
    try:
        report["healthchecks"] = ping_role("sample")
    except Exception as exc:  # noqa: BLE001
        report["healthchecks"] = f"error:{exc}"
        print(f"healthchecks error: {exc}", file=sys.stderr)
    return report
