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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ai_quotas import core
from ai_quotas.alert_plot import plot_png
from ai_quotas.alert_text import first_line, format_alert, format_refresh, local_tz, logins_by_app, plain
from ai_quotas.notify import (
    CAPTION_LIMIT, ping_role, send_email, send_telegram, send_telegram_photo, send_urgent,
)
from ai_quotas.paths import data_dir, samples_path
from ai_quotas.reset_credits import EXPIRING_SOON, burn_relaxation, parse_ts as parse_credit_ts, usable_credits
from ai_quotas.accounts import primary_accounts, primary_only
from ai_quotas.storage import load_reset_credits
from ai_quotas.reset_notifications import reset_items, reset_marker

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
LOG_NAME = "alert-log.jsonl"
_SESSION_WINDOWS = ("5h",)
_SKIP_WINDOWS = frozenset({
    "overage_credits", "credits_balance", "credit_grant", "unknown", "credits", "free_daily", "—",
})
# Independent agy week buckets. The provider verdict keeps one of them;
# the other still alerts.
_AGY_BUCKET_WINDOWS = frozenset({"week_gemini_pro", "week_opus"})


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
    out: dict[str, Any] = {"sent": sent}
    if isinstance(data.get("morning"), str):
        out["morning"] = data["morning"]  # last morning report date (local)
    return out


def save_state(state: dict[str, Any], path: str | Path | None = None) -> None:
    p = state_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"sent": dict(state.get("sent") or {})}
    if isinstance(state.get("morning"), str):
        payload["morning"] = state["morning"]
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


def _as_utc(now: datetime | None) -> datetime:
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        return now_dt.replace(tzinfo=timezone.utc)
    return now_dt.astimezone(timezone.utc)


def _expiring_item(
    *,
    provider: str,
    credit_id: str,
    title: str | None,
    expires_at: str,
    hours: float,
    window: str = "week",
) -> dict[str, Any]:
    return {
        "kind": "credit_expiring",
        "provider": provider,
        "window": window,
        "severity": "WARN",
        "credit_id": credit_id,
        "title": title,
        "expires_at": expires_at,
        # Dedupe drops the fingerprint once this moment has passed.
        "resets_at": expires_at,
        "hours_to_reset": hours,
        "fingerprint": f"credit_expiring:{provider}:{credit_id}:{expires_at}",
    }


def _credit_expiring_items(
    credit_rows: list[dict[str, Any]] | None,
    grant_rows: list[dict[str, Any]] | None,
    now: datetime | None,
) -> list[dict[str, Any]]:
    """Available reset credits, and prepaid grants with remaining, inside 7 days.

    A balance whose vendor published no expiry is not an alert. A grant that
    already expired is recorded by the adapter and not notified.
    """
    now_dt = _as_utc(now)
    items: list[dict[str, Any]] = []
    providers = {
        row.get("provider")
        for row in (credit_rows or [])
        if isinstance(row.get("provider"), str)
    }
    for provider in sorted(providers):
        for credit in usable_credits(credit_rows or [], provider, "week", now=now):
            expires_at = credit.get("expires_at")
            exp = parse_credit_ts(expires_at)
            if exp is None:
                continue
            delta = exp - now_dt
            if not (timedelta(0) < delta <= EXPIRING_SOON):
                continue
            items.append(_expiring_item(
                provider=provider,
                credit_id=str(credit.get("credit_id") or "credit"),
                title=credit.get("title"),
                expires_at=str(expires_at),
                hours=delta.total_seconds() / 3600.0,
                window=str(credit.get("scope") or "week"),
            ))
    for grant in grant_rows or []:
        if grant.get("window") != "credit_grant" or grant.get("status") != "ok":
            continue
        remaining = grant.get("remaining")
        if isinstance(remaining, (int, float)) and not remaining > 0:
            continue
        expires_at = grant.get("expires_at")
        exp = parse_credit_ts(expires_at) if isinstance(expires_at, str) else None
        if exp is None:
            continue
        delta = exp - now_dt
        if not (timedelta(0) < delta <= EXPIRING_SOON):
            continue
        provider = str(grant.get("provider") or "")
        credit_id = str(grant.get("credit_id") or grant.get("title") or "grant")
        items.append(_expiring_item(
            provider=provider,
            credit_id=credit_id,
            title=str(grant.get("title") or "") or None,
            expires_at=str(expires_at),
            hours=delta.total_seconds() / 3600.0,
            window="credit_grant",
        ))
    return items


def _alert_rows(result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Verdict row per provider, plus both agy week buckets."""
    verdicts = result.get("verdicts") or {}
    rows: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for provider, row in verdicts.items():
        if not isinstance(row, dict) or str(provider) in core.RETIRED_PROVIDERS:
            continue
        key = (str(provider), str(row.get("window") or ""))
        rows.append((str(provider), row))
        seen.add(key)
    for row in result.get("windows") or []:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "")
        window = str(row.get("window") or "")
        if provider != "agy" or window not in _AGY_BUCKET_WINDOWS:
            continue
        if (provider, window) in seen:
            continue
        rows.append((provider, row))
        seen.add((provider, window))
    return rows


def items_from_evaluate(
    result: dict[str, Any], *, include_reset_soon: bool = False,
    credit_rows: list[dict[str, Any]] | None = None,
    grant_rows: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build alert items from ``core.evaluate`` output."""
    items: list[dict[str, Any]] = []
    for provider, row in _alert_rows(result):
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
    items.extend(_credit_expiring_items(credit_rows, grant_rows, now))
    return items


def _same_warning(item: dict[str, Any], sent: dict[str, Any]) -> str | None:
    """A delivered key for this warning whose deadline only jittered.

    Rounding to the minute alone resends when a deadline wobbles across a
    minute edge (07:00:29 → 07:00:31), so match within the reset tolerance.
    """
    end = _reset_time(item.get("resets_at"))
    raw = str(item.get("resets_at") or "")
    fp = str(item["fingerprint"])
    if item.get("kind") == "reset" or end is None or not raw or not fp.endswith(raw):
        return None
    prefix = fp[: -len(raw)]
    for key, meta in sent.items():
        if key.startswith(prefix) and isinstance(meta, dict):
            prior = _reset_time(meta.get("resets_at"))
            if prior is not None and abs(prior - end) <= core.RESET_TIMESTAMP_TOLERANCE:
                return key
    return None


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
        reset = _reset_time(meta.get("dedupe_until") or meta.get("resets_at"))
        if (reset is not None and reset > now) or (reset is None and key in current):
            pruned[key] = meta
    fresh: list[dict[str, Any]] = []
    merged = dict(pruned)
    for item in items:
        fp = str(item["fingerprint"])
        if item["kind"] == "reset":
            # Equivalent observations from a fallback/device must share the
            # already-delivered cycle, including fingerprints from older code.
            prefix = f"reset:{item['provider']}:{item['account']}:{item['window']}:"
            end = _reset_time(item.get("resets_at"))
            if end and any(
                key.startswith(prefix) and (prior_end := _reset_time(meta.get("resets_at")))
                and abs(end-prior_end) <= core.RESET_TIMESTAMP_TOLERANCE
                for key, meta in merged.items()
            ):
                continue
        prior = fp if fp in merged else _same_warning(item, merged)
        if prior is not None:
            ranks = {"INFO": 0, "WARN": 1, "STOP": 2}
            previous_rank = ranks.get(merged[prior].get("severity"), 0)
            if item["kind"] != "burn" or ranks.get(item.get("severity"), 0) <= previous_rank:
                continue
        merged[fp] = {
            "ts": ts,
            "kind": item["kind"],
            "severity": item.get("severity"),
            "resets_at": item.get("resets_at"),
            "dedupe_until": item.get("dedupe_until"),
        }
        fresh.append(item)
    return fresh, {"sent": pruned}, {"sent": merged}


def format_message(items: list[dict[str, Any]], *, logins: dict[str, set[str]] | None = None,
                   tz: Any = None) -> str:
    """One Telegram HTML message per item, blank-line separated (preview/CLI)."""
    zone = tz or local_tz()
    return "\n\n".join(format_alert(it, logins=logins or {}, tz=zone) for it in items)


def _drop_old_cycle(items: list[dict[str, Any]], resets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A window that just refreshed gets no warning about the cycle that ended."""
    newest: dict[tuple[str, str], datetime] = {}
    for it in resets:
        end = _reset_time(it.get("resets_at"))
        key = (str(it.get("provider")), str(it.get("window")))
        if end is not None and (key not in newest or end > newest[key]):
            newest[key] = end
    out = []
    for it in items:
        if it.get("kind") in {"burn", "spare", "spare_urgent"}:
            end = _reset_time(it.get("resets_at"))
            later = newest.get((str(it.get("provider")), str(it.get("window"))))
            if end is not None and later is not None and end < later - core.RESET_TIMESTAMP_TOLERANCE:
                continue
        out.append(it)
    return out


# Nested windows that are one alert with their app's main window.
_NESTED = {("claude", "week_fable"): "week"}


def _messages(fresh: list[dict[str, Any]], *, logins: dict[str, set[str]], tz: Any,
              sent_state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(messages, silently delivered items). One message per event.

    Refreshes of one login (Claude week + Fable week) are one event. A nested
    window refreshing after its main window's alert already went out is
    recorded without a second message.
    """
    messages: list[dict[str, Any]] = []
    silent: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for it in fresh:
        if it.get("kind") == "reset":
            groups.setdefault((str(it["provider"]), str(it.get("account"))), []).append(it)
    for (provider, account), group in groups.items():
        windows = {str(it.get("window")) for it in group}
        nested_only = all((provider, w) in _NESTED for w in windows)
        if nested_only:
            parents = {_NESTED[(provider, w)] for w in windows}
            prefixes = tuple(f"reset:{provider}:{account}:{w}:" for w in parents)
            if any(key.startswith(prefixes) for key in sent_state):
                silent.extend(group)
                continue
        main = sorted(group, key=lambda it: (it.get("window") != "week", str(it.get("window"))))[0]
        marker = reset_marker(float(main.get("used_before_reset") or 0))
        messages.append({
            "kind": "reset", "items": group, "provider": provider, "account": main.get("account"),
            "text": format_refresh(group, logins=logins, tz=tz, marker=marker),
        })
    for it in fresh:
        if it.get("kind") == "reset":
            continue
        messages.append({
            "kind": it.get("kind"), "items": [it], "provider": str(it.get("provider")),
            "account": it.get("account"),
            "text": format_alert(it, logins=logins, tz=tz),
        })
    return messages, silent


def _visible(text: str) -> str:
    """The open lines only: the urgent DM is plain text without a fold."""
    return plain(text.split("<blockquote", 1)[0])


def log_path(state_file: str | Path | None = None) -> Path:
    return state_path(state_file).with_name(LOG_NAME)


def append_log(entries: list[dict[str, Any]], state_file: str | Path | None = None) -> None:
    """Delivered alerts, one JSON line each, for the morning digest."""
    if not entries:
        return
    p = log_path(state_file)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"alert log error: {exc}", file=sys.stderr)


def read_log(state_file: str | Path | None = None, *, since: datetime) -> list[dict[str, Any]]:
    p = log_path(state_file)
    if not p.is_file():
        return []
    out = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-2000:]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = core.parse_ts(entry.get("ts")) if isinstance(entry, dict) else None
        if ts is not None and ts.replace(tzinfo=ts.tzinfo or timezone.utc) >= since:
            out.append(entry)
    return out


def _photo_for(message: dict[str, Any], samples: list[dict[str, Any]], now: datetime, tz: Any) -> bytes | None:
    return plot_png(samples, message["provider"], message.get("account"), now=now, tz=tz)


def deliver_message(text: str, photo: bytes | None) -> str:
    """Photo with the message as its caption; the plain message when that fails."""
    if photo is not None and len(plain(text)) <= CAPTION_LIMIT:
        status = send_telegram_photo(photo, caption=text, parse_mode="HTML")
        if status == "sent" or status == "skip":
            return status
        print(f"alert photo {status}; sending text", file=sys.stderr)
    return send_telegram(text, parse_mode="HTML")


def run_alerts(
    *,
    path: str | Path | None = None,
    state_file: str | Path | None = None,
    send: bool = True,
    dry_run: bool = False,
    include_reset_soon: bool = False,
    now: datetime | None = None,
    sender: Callable[[str], str] | None = None,
    photos: bool = True,
) -> dict[str, Any]:
    all_samples = core.load_samples(path if path is not None else samples_path(), all_accounts=True)
    samples = primary_only(all_samples, all_samples)
    result = core.evaluate(samples, now=now)
    try:
        all_credits = load_reset_credits(path if path is not None else samples_path())
        credits = primary_only(all_credits, samples)
    except (OSError, ValueError, sqlite3.Error):
        all_credits, credits = [], []
    grants = [row for row in samples if row.get("window") == "credit_grant"]
    items = items_from_evaluate(
        result, include_reset_soon=include_reset_soon,
        credit_rows=credits, grant_rows=grants, now=now,
    )
    moment = now or datetime.now(timezone.utc).astimezone()
    moment = moment.replace(tzinfo=moment.tzinfo or timezone.utc)
    primaries = primary_accounts(all_samples)
    for it in items:
        it.setdefault("account", primaries.get(str(it.get("provider"))))
    resets = reset_items(all_samples, now=moment, credit_rows=all_credits)
    items = _drop_old_cycle(items, resets) + resets
    state = load_state(state_file)
    fresh, pruned_state, _merged = apply_dedupe(items, state, now=now)
    tz = local_tz()
    logins = logins_by_app(all_samples, moment)
    messages, silent = _messages(fresh, logins=logins, tz=tz, sent_state=state.get("sent") or {})
    chat = [m for m in messages if m["kind"] != "spare_urgent"]
    urgent = [m for m in messages if m["kind"] == "spare_urgent"]
    report: dict[str, Any] = {
        "ts": moment.isoformat(timespec="seconds"),
        "firing": len(items),
        "new": len(fresh),
        "items": fresh,
        "messages": [m["text"] for m in chat],
        "message": "\n\n".join(m["text"] for m in chat if m["kind"] != "reset"),
        "urgent_message": "\n\n".join(_visible(m["text"]) for m in urgent),
        "reset_message": "\n\n".join(m["text"] for m in chat if m["kind"] == "reset"),
        "reset_delivery": "skip",
        "delivery": "skip",
        "email": "skip",
        "urgent": "skip",
    }
    # Previewing must not change live dedupe state.
    if not dry_run and send:
        save_state({**state, **pruned_state}, state_file)
    if not fresh:
        return report
    if dry_run or not send:
        report["delivery"] = "dry-run" if dry_run else "not-sent"
        if urgent:
            report["urgent"] = report["delivery"]
        return report
    marked = dict(pruned_state.get("sent") or {})
    stamp = moment.isoformat(timespec="seconds")
    log: list[dict[str, Any]] = []

    def _keep(sent_items: list[dict[str, Any]]) -> None:
        for item in sent_items:
            marked[str(item["fingerprint"])] = {
                "ts": stamp,
                "kind": item["kind"],
                "severity": item.get("severity"),
                "resets_at": item.get("resets_at"),
                "dedupe_until": item.get("dedupe_until"),
            }

    _keep(silent)
    statuses: list[str] = []
    for message in chat:
        if sender is not None:
            status = sender(message["text"])
        else:
            photo = _photo_for(message, all_samples, moment, tz) if photos else None
            status = deliver_message(message["text"], photo)
        statuses.append(status)
        if message["kind"] == "reset":
            report["reset_delivery"] = status
        email = "skip"
        if message["kind"] != "reset":
            email = send_email(plain(message["text"]))
            if email != "skip":
                report["email"] = email
        if status == "sent" or email == "sent":
            _keep(message["items"])
            log.append({"ts": stamp, "kind": message["kind"], "head": first_line(message["text"])})
        elif status.startswith("error"):
            break  # Telegram is down: the rest retry on the next sample.
    if statuses:
        failed = [s for s in statuses if s != "sent"]
        report["delivery"] = failed[0] if failed else "sent"
    for message in urgent:
        # No personal-account command: remember the skip so a stock install
        # does not retry this period every sample. An error is not remembered.
        urgent_status = send_urgent(_visible(message["text"]))
        report["urgent"] = urgent_status
        if urgent_status in {"sent", "skip"}:
            _keep(message["items"])
    append_log(log, state_file)
    if marked != (pruned_state.get("sent") or {}):
        save_state({**load_state(state_file), "sent": marked}, state_file)
    # Callers that only look at merged delivery still see a telegram send.
    if report["delivery"] == "skip" and not chat and report["urgent"] == "sent":
        report["delivery"] = "sent"
    return report


def run_after_sample(
    *,
    path: str | Path | None = None,
    send: bool = True,
) -> dict[str, Any]:
    """Best-effort: alerts then Healthchecks ping. Never raises."""
    report: dict[str, Any] = {"alerts": None, "morning": None, "healthchecks": "skip"}
    try:
        report["alerts"] = run_alerts(path=path, send=send, include_reset_soon=True)
    except Exception as exc:  # noqa: BLE001 — sample agent must not die
        report["alerts"] = {"error": str(exc)}
        print(f"alert error: {exc}", file=sys.stderr)
    if send:
        try:
            from ai_quotas.morning import run_morning

            morning = run_morning(path=path)
            report["morning"] = {k: v for k, v in morning.items() if k != "photos"}
        except Exception as exc:  # noqa: BLE001
            report["morning"] = {"error": str(exc)}
            print(f"morning report error: {exc}", file=sys.stderr)
    try:
        report["healthchecks"] = ping_role("sample")
    except Exception as exc:  # noqa: BLE001
        report["healthchecks"] = f"error:{exc}"
        print(f"healthchecks error: {exc}", file=sys.stderr)
    return report
