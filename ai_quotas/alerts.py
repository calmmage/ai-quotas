"""Remaining-quota Telegram alerts: burn (WARN/STOP) and reset-soon waste.

Dedupe is per fingerprint in ``<data_dir>/alert-state.json``. One Telegram
message per run if anything new fired. Fail-open.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_quotas import core
from ai_quotas.notify import ping_role, send_telegram
from ai_quotas.paths import data_dir, samples_path

RESET_SOON_HOURS = 48.0
REMAINING_HIGH = 40.0
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


def items_from_evaluate(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Build alert items from ``core.evaluate`` output."""
    items: list[dict[str, Any]] = []
    verdicts = result.get("verdicts") or {}
    for provider, row in verdicts.items():
        window = str(row.get("window") or "")
        used = row.get("used_percent")
        remaining = remaining_percent(used if isinstance(used, (int, float)) else None)
        hours_left = row.get("hours_to_reset")
        verdict = str(row.get("verdict") or "UNKNOWN")
        resets_at = row.get("resets_at") if isinstance(row.get("resets_at"), str) else None
        if verdict in ("WARN", "STOP") and remaining is not None:
            items.append(
                {
                    "kind": "burn",
                    "provider": provider,
                    "window": window or "week",
                    "severity": verdict,
                    "remaining": remaining,
                    "used_percent": used,
                    "hours_to_reset": hours_left,
                    "pace": row.get("pace"),
                    "projected_final": row.get("projected_final"),
                    "resets_at": resets_at,
                    "fingerprint": f"burn:{provider}:{window or 'week'}:{verdict}",
                }
            )
        if (
            remaining is not None
            and remaining >= REMAINING_HIGH
            and isinstance(hours_left, (int, float))
            and 0 < float(hours_left) <= RESET_SOON_HOURS
            and _is_primary_window(window or "week")
        ):
            day = (resets_at or "")[:10] or "unknown"
            items.append(
                {
                    "kind": "reset_soon",
                    "provider": provider,
                    "window": window or "week",
                    "severity": "INFO",
                    "remaining": remaining,
                    "used_percent": used,
                    "hours_to_reset": hours_left,
                    "pace": row.get("pace"),
                    "projected_final": row.get("projected_final"),
                    "resets_at": resets_at,
                    "fingerprint": f"reset_soon:{provider}:{window or 'week'}:{day}",
                }
            )
    return items


def apply_dedupe(
    items: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Return (new items, pruned state, pruned+new state).

    Ended conditions drop out of state so they can fire again later.
    """
    now = now or datetime.now(timezone.utc).astimezone()
    ts = now.isoformat(timespec="seconds")
    previous: dict[str, Any] = dict(state.get("sent") or {})
    current = {str(it["fingerprint"]) for it in items}
    pruned = {key: meta for key, meta in previous.items() if key in current}
    fresh: list[dict[str, Any]] = []
    merged = dict(pruned)
    for item in items:
        fp = str(item["fingerprint"])
        if fp in pruned:
            continue
        merged[fp] = {"ts": ts, "kind": item["kind"]}
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
        else:
            lines.append(f"RESET SOON  {provider} {window}")
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
    now: datetime | None = None,
    sender: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    samples = core.load_samples(path if path is not None else samples_path())
    result = core.evaluate(samples, now=now)
    items = items_from_evaluate(result)
    state = load_state(state_file)
    fresh, pruned_state, merged_state = apply_dedupe(items, state, now=now)
    report: dict[str, Any] = {
        "ts": (now or datetime.now(timezone.utc).astimezone()).isoformat(timespec="seconds"),
        "firing": len(items),
        "new": len(fresh),
        "items": fresh,
        "message": format_message(fresh) if fresh else "",
        "delivery": "skip",
    }
    # Always drop ended conditions so they can fire again.
    save_state(pruned_state, state_file)
    if not fresh:
        return report
    if dry_run or not send:
        report["delivery"] = "dry-run" if dry_run else "not-sent"
        return report
    deliver = sender or send_telegram
    status = deliver(report["message"])
    report["delivery"] = status
    if status == "sent":
        save_state(merged_state, state_file)
    return report


def run_after_sample(
    *,
    path: str | Path | None = None,
    send: bool = True,
) -> dict[str, Any]:
    """Best-effort: alerts then Healthchecks ping. Never raises."""
    report: dict[str, Any] = {"alerts": None, "healthchecks": "skip"}
    try:
        report["alerts"] = run_alerts(path=path, send=send)
    except Exception as exc:  # noqa: BLE001 — sample agent must not die
        report["alerts"] = {"error": str(exc)}
        print(f"alert error: {exc}", file=sys.stderr)
    try:
        report["healthchecks"] = ping_role("sample")
    except Exception as exc:  # noqa: BLE001
        report["healthchecks"] = f"error:{exc}"
        print(f"healthchecks error: {exc}", file=sys.stderr)
    return report
