"""Trend math, verdicts, and history — the pure analysis layer.

Ported from the private quota-providers watchdog. Stdlib only.
All rates are percent of quota per hour unless a display helper scales them.
"""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ai_quotas.paths import samples_path as resolve_samples_path

# ---------------------------------------------------------------------------
# Thresholds (module-level; tune here, not via flags)
# ---------------------------------------------------------------------------
STOP_PROJECTED = 100.0
STOP_USED = 85.0
STOP_HOURS = 24.0
WARN_PROJECTED = 70.0
WARN_USED = 60.0
WARN_HOURS = 48.0
TREND_24H_LOOKBACK = timedelta(hours=24)
MIN_BURN_INTERVAL = timedelta(minutes=30)
QUANTIZED_DELTA_MAX = 1.0
QUANTIZED_MIN_INTERVAL = timedelta(hours=2)
PROJECTED_FINAL_CAP = 999.0
PACE_RED_ABOVE = 150.0
PACE_YELLOW_AT = 100.0
HISTORY_SPARSE_BELOW = 5
COLLAPSE_ABOVE = 3

# Built-in public providers (agy is a private drop-in only — gate G3).
BUILTIN_PROVIDERS = ("claude", "codex", "grok", "openrouter")
# Verdict loop still lists known names; extra adapters may add more at runtime.
DEFAULT_VERDICT_PROVIDERS = BUILTIN_PROVIDERS

_RATE_COL = 10
_NON_QUOTA_WINDOWS = frozenset({"overage_credits", "unknown", "—"})


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def hours_until(resets_at: str | None, now: datetime) -> float | None:
    reset = parse_ts(resets_at)
    if reset is None:
        return None
    if reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)
    delta = (reset - now).total_seconds() / 3600.0
    return max(0.0, delta)


def load_samples(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = resolve_samples_path(path)
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


def ok_numeric(row: dict[str, Any]) -> bool:
    if row.get("status") != "ok":
        return False
    pct = row.get("used_percent")
    if pct is None:
        return False
    try:
        float(pct)
    except (TypeError, ValueError):
        return False
    return True


def latest_ok_by_provider_window(
    samples: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Most recent ok+numeric row per (provider, window), by sample ts."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    best_ts: dict[tuple[str, str], datetime] = {}
    for row in samples:
        if not ok_numeric(row):
            continue
        provider = row.get("provider")
        window = row.get("window")
        if not isinstance(provider, str) or not isinstance(window, str):
            continue
        ts = parse_ts(row.get("ts"))
        if ts is None:
            continue
        key = (provider, window)
        prev = best_ts.get(key)
        if prev is None or ts >= prev:
            best[key] = row
            best_ts[key] = ts
    return best


def latest_by_key(
    samples: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Latest row per (provider, window) regardless of status (append-order / ts)."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    best_ts: dict[tuple[str, str], datetime | None] = {}
    for row in samples:
        provider = row.get("provider")
        window = row.get("window")
        if not isinstance(provider, str) or not isinstance(window, str):
            continue
        key = (provider, window)
        ts = parse_ts(row.get("ts"))
        prev_ts = best_ts.get(key)
        if key not in best:
            best[key] = row
            best_ts[key] = ts
            continue
        if ts is not None and (prev_ts is None or ts >= prev_ts):
            best[key] = row
            best_ts[key] = ts
        elif ts is None and prev_ts is None:
            # append-only fallback
            best[key] = row
    return best


def window_length(window: str) -> timedelta | None:
    w = (window or "").lower()
    if w == "5h" or w.startswith("5h"):
        return timedelta(hours=5)
    if w == "week" or w.startswith("week"):
        return timedelta(days=7)
    if w in {"month", "overage_credits"}:
        return None
    if w.endswith("d") and w[:-1].isdigit():
        return timedelta(days=int(w[:-1]))
    if w.endswith("h") and w[:-1].isdigit():
        return timedelta(hours=int(w[:-1]))
    return None


def window_hours(window: str, now: datetime | None = None) -> float | None:
    """Quota window length in hours for sustainable-pace math."""
    now = now or datetime.now(timezone.utc).astimezone()
    w = (window or "").lower()
    if w == "5h" or w.startswith("5h"):
        return 5.0
    if w == "week" or w.startswith("week"):
        return 168.0
    if w == "free_daily" or w.endswith("_daily"):
        return 24.0
    if w in {"month", "overage_credits", "credits"}:
        days = monthrange(now.year, now.month)[1]
        return float(days * 24)
    length = window_length(window)
    if length is not None:
        return length.total_seconds() / 3600.0
    return None


def window_start(
    window: str,
    resets_at: str | None,
    now: datetime,
) -> datetime | None:
    w = (window or "").lower()
    reset = parse_ts(resets_at)
    if reset is not None and reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)

    if w in {"month", "overage_credits"}:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    length = window_length(window)
    if length is not None and reset is not None:
        return reset - length
    return None


def trend_total_pct_per_hour(
    used_percent: float,
    window: str,
    resets_at: str | None,
    now: datetime,
) -> float | None:
    start = window_start(window, resets_at, now)
    if start is None:
        return None
    hours = (now - start).total_seconds() / 3600.0
    if hours <= 0:
        return None
    return used_percent / hours


def trend_from_samples(
    samples: list[dict[str, Any]],
    provider: str,
    window: str,
    current: dict[str, Any],
    now: datetime,
    *,
    lookback: timedelta = TREND_24H_LOOKBACK,
) -> tuple[float | None, dict[str, Any]]:
    """Short-term %/h from oldest same (provider, window) sample within lookback."""
    cur_ts = parse_ts(current.get("ts")) or now
    cur_pct = float(current["used_percent"])
    cutoff = now - lookback

    empty_basis: dict[str, Any] = {
        "baseline_ts": None,
        "interval_hours": None,
        "quantized": False,
    }

    oldest: dict[str, Any] | None = None
    oldest_ts: datetime | None = None
    for row in samples:
        if row.get("provider") != provider or row.get("window") != window:
            continue
        if not ok_numeric(row):
            continue
        ts = parse_ts(row.get("ts"))
        if ts is None:
            continue
        if ts < cutoff:
            continue
        if ts >= cur_ts:
            continue
        if oldest_ts is None or ts < oldest_ts:
            oldest = row
            oldest_ts = ts

    if oldest is None or oldest_ts is None:
        return None, empty_basis

    interval = cur_ts - oldest_ts
    hours = interval.total_seconds() / 3600.0
    baseline_ts = oldest.get("ts")
    if isinstance(baseline_ts, str):
        baseline_str: str | None = baseline_ts
    else:
        baseline_str = oldest_ts.isoformat(timespec="seconds")

    basis: dict[str, Any] = {
        "baseline_ts": baseline_str,
        "interval_hours": hours if hours > 0 else None,
        "quantized": False,
    }

    if interval < MIN_BURN_INTERVAL or hours <= 0:
        return None, basis

    base_pct = float(oldest["used_percent"])
    delta = cur_pct - base_pct

    if abs(delta) <= QUANTIZED_DELTA_MAX and interval < QUANTIZED_MIN_INTERVAL:
        basis["quantized"] = True
        return None, basis

    return delta / hours, basis


def burn_rate_per_hour(
    samples: list[dict[str, Any]],
    provider: str,
    window: str,
    current: dict[str, Any],
    now: datetime,
) -> tuple[float | None, dict[str, Any]]:
    return trend_from_samples(
        samples, provider, window, current, now, lookback=TREND_24H_LOOKBACK
    )


def runway_hours(used_percent: float, trend_pct_per_hour: float | None) -> float | None:
    if trend_pct_per_hour is None or trend_pct_per_hour <= 0:
        return None
    remaining = 100.0 - used_percent
    if remaining <= 0:
        return 0.0
    return remaining / trend_pct_per_hour


def pace_pct_of_quota(
    measured_burn_pct_per_hour: float | None,
    win_hours: float | None,
) -> float | None:
    if measured_burn_pct_per_hour is None or win_hours is None or win_hours <= 0:
        return None
    if measured_burn_pct_per_hour <= 0:
        return 0.0
    sustainable = 100.0 / win_hours
    return (measured_burn_pct_per_hour / sustainable) * 100.0


def pace_marker_from_pct(pace_pct: float | None) -> str:
    if pace_pct is None:
        return "—"
    if pace_pct > PACE_RED_ABOVE:
        return "🔴"
    if pace_pct >= PACE_YELLOW_AT:
        return "🟡"
    return "🟢"


def format_pace_label(pace_pct: float | None) -> str:
    if pace_pct is None:
        return "—"
    marker = pace_marker_from_pct(pace_pct)
    return f"{marker} {int(round(pace_pct))}% of quota pace"


def format_duration_hours(hours: float | None) -> str:
    if hours is None:
        return "—"
    if hours < 0:
        return "—"
    if hours == 0:
        return "0h"
    total_min = int(round(hours * 60))
    d, rem = divmod(total_min, 60 * 24)
    h, m = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{m}m"


def rate_scale_unit(window_hours_val: float | None) -> tuple[float, str]:
    if window_hours_val is not None and float(window_hours_val) <= 12:
        return 1.0, "%/h"
    return 24.0, "%/d"


def fmt_rate_body(rate_per_hour: float | None, *, scale: float, unit: str) -> str:
    if rate_per_hour is None:
        return "—"
    v = max(0.0, float(rate_per_hour)) * scale
    if abs(v) >= 100:
        raw = f"{v:.0f}"
    elif abs(v) >= 10:
        raw = f"{v:.1f}".rstrip("0").rstrip(".")
    elif abs(v) >= 1:
        raw = f"{v:.1f}"
    else:
        raw = f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{raw}{unit}"


def format_rate_cell(
    rate_per_hour: float | None,
    *,
    window_hours: float | None,
    width: int = _RATE_COL,
) -> str:
    scale, unit = rate_scale_unit(window_hours)
    body = fmt_rate_body(rate_per_hour, scale=scale, unit=unit)
    return f"{body:>{width}}"


def burn_need_from_metrics(w: dict[str, Any]) -> dict[str, float | None]:
    used = w.get("used_percent")
    hours_left = w.get("hours_to_reset")
    win_h = w.get("window_hours")
    burn_24h = w.get("trend_24h_pct_per_hour")
    if burn_24h is None:
        burn_24h = w.get("burn_per_hour")
    burn_tot = w.get("trend_total_pct_per_hour")

    need_avg: float | None = None
    if win_h is not None and float(win_h) > 0:
        need_avg = 100.0 / float(win_h)

    need_rem: float | None = None
    if used is not None and hours_left is not None and float(hours_left) > 0:
        need_rem = max(0.0, 100.0 - float(used)) / float(hours_left)

    def _floor0(v: float | None) -> float | None:
        if v is None:
            return None
        return max(0.0, float(v))

    return {
        "burn_24h": _floor0(float(burn_24h) if burn_24h is not None else None),
        "burn_tot": _floor0(float(burn_tot) if burn_tot is not None else None),
        "need_rem": need_rem,
        "need_avg": need_avg,
        "window_hours": float(win_h) if win_h is not None else None,
    }


def burn_metrics(row: dict[str, Any], m: dict[str, Any] | None) -> dict[str, Any]:
    """Rates as % of quota per hour (display: %/h short windows, %/d longer)."""
    used_pct = float(row["used_percent"])
    hours_left = m.get("hours_to_reset") if m else None
    win_h = m.get("window_hours") if m else None
    trend_24h = m.get("trend_24h_pct_per_hour") if m else None
    trend_tot = m.get("trend_total_pct_per_hour") if m else None

    def _floor0(v: float | None) -> float | None:
        if v is None:
            return None
        return max(0.0, float(v))

    burn_24h = _floor0(float(trend_24h) if trend_24h is not None else None)
    burn_tot = _floor0(float(trend_tot) if trend_tot is not None else None)
    need_avg = (100.0 / float(win_h)) if win_h and float(win_h) > 0 else None
    rem_pct = max(0.0, 100.0 - used_pct)
    need_rem = (
        rem_pct / float(hours_left)
        if hours_left is not None and float(hours_left) > 0
        else None
    )
    if win_h is not None and float(win_h) <= 12:
        scale = 1.0
        unit = "%/h"
    else:
        scale = 24.0
        unit = "%/d"

    return {
        "burn_24h": burn_24h,
        "burn_tot": burn_tot,
        "need_avg": need_avg,
        "need_rem": need_rem,
        "scale": scale,
        "unit": unit,
        "hours_to_reset": float(hours_left) if hours_left is not None else None,
        "window_hours": float(win_h) if win_h is not None else None,
    }


def pick_verdict_window(
    provider: str,
    by_pw: dict[tuple[str, str], dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    """Choose the window used for the provider-level verdict."""
    if provider == "grok":
        month = by_pw.get((provider, "month"))
        if month is not None:
            return "month", month
        week = by_pw.get((provider, "week"))
        if week is not None:
            return "week", week
        return None, None

    if provider == "openrouter":
        credits = by_pw.get((provider, "credits"))
        if credits is not None:
            return "credits", credits
        free = by_pw.get((provider, "free_daily"))
        if free is not None:
            return "free_daily", free

    week = by_pw.get((provider, "week"))
    if week is not None:
        return "week", week

    weekish: list[tuple[str, dict[str, Any]]] = [
        (w, row)
        for (p, w), row in by_pw.items()
        if p == provider and (w == "week" or w.startswith("week"))
    ]
    if weekish:
        window, row = max(weekish, key=lambda item: float(item[1]["used_percent"]))
        return window, row

    any_rows = [(w, row) for (p, w), row in by_pw.items() if p == provider]
    if not any_rows:
        return None, None
    non_5h = [
        x
        for x in any_rows
        if not x[0].startswith("5h") and x[0] != "overage_credits"
    ]
    pool = non_5h or any_rows
    window, row = max(pool, key=lambda item: float(item[1]["used_percent"]))
    return window, row


def metrics_for_row(
    samples: list[dict[str, Any]],
    row: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Full trend/runway/pace metrics for one ok (provider, window) sample row."""
    provider = str(row.get("provider") or "")
    window = str(row.get("window") or "")
    used = float(row["used_percent"])
    resets_at = row.get("resets_at") if isinstance(row.get("resets_at"), str) else None
    hours_left = hours_until(resets_at, now)
    win_h = window_hours(window, now)

    trend_total = trend_total_pct_per_hour(used, window, resets_at, now)
    trend_24h, basis = trend_from_samples(
        samples, provider, window, row, now, lookback=TREND_24H_LOOKBACK
    )
    rwy_total = runway_hours(used, trend_total)
    rwy_24h = runway_hours(used, trend_24h)

    pace_total = pace_pct_of_quota(trend_total, win_h)
    pace_24h = pace_pct_of_quota(trend_24h, win_h)
    pace_headline = pace_24h if pace_24h is not None else pace_total

    projected: float | None = None
    if trend_24h is not None and hours_left is not None:
        projected = used + trend_24h * hours_left
        if projected > PROJECTED_FINAL_CAP:
            projected = PROJECTED_FINAL_CAP
        elif projected < -PROJECTED_FINAL_CAP:
            projected = -PROJECTED_FINAL_CAP

    return {
        "provider": provider,
        "window": window,
        "used_percent": used,
        "resets_at": resets_at,
        "hours_to_reset": hours_left,
        "window_hours": win_h,
        "trend_total_pct_per_hour": trend_total,
        "trend_24h_pct_per_hour": trend_24h,
        "runway_hours_total": rwy_total,
        "runway_hours_24h": rwy_24h,
        "pace_pct_of_quota": {
            "total": pace_total,
            "24h": pace_24h,
        },
        "pace_pct_of_quota_total": pace_total,
        "pace_pct_of_quota_24h": pace_24h,
        "pace_pct": pace_headline,
        "pace": format_pace_label(pace_headline),
        "pace_marker": pace_marker_from_pct(pace_headline),
        "projected_final": projected,
        "burn_per_hour": trend_24h,
        "basis": basis,
    }


def verdict_for(
    used_percent: float | None,
    burn_per_hour: float | None,
    projected_final: float | None,
    hours_left: float | None,
    *,
    runway_hours_24h: float | None = None,
) -> str:
    """STOP/WARN/OK/UNKNOWN. Null burn never produces a projection-based STOP."""
    if used_percent is None:
        return "UNKNOWN"

    if (
        burn_per_hour is not None
        and projected_final is not None
        and projected_final >= STOP_PROJECTED
    ):
        return "STOP"
    if used_percent >= STOP_USED and hours_left is not None and hours_left > STOP_HOURS:
        return "STOP"
    if (
        runway_hours_24h is not None
        and hours_left is not None
        and runway_hours_24h < hours_left
    ):
        return "STOP"

    if (
        burn_per_hour is not None
        and projected_final is not None
        and projected_final >= WARN_PROJECTED
    ):
        return "WARN"
    if used_percent >= WARN_USED and hours_left is not None and hours_left > WARN_HOURS:
        return "WARN"

    return "OK"


def _empty_basis() -> dict[str, Any]:
    return {"baseline_ts": None, "interval_hours": None, "quantized": False}


def evaluate(
    samples: list[dict[str, Any]],
    now: datetime | None = None,
    *,
    providers: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc).astimezone()
    by_pw = latest_ok_by_provider_window(samples)

    windows: list[dict[str, Any]] = []
    for (provider, window), row in sorted(by_pw.items()):
        m = metrics_for_row(samples, row, now)
        m["verdict"] = verdict_for(
            m["used_percent"],
            m["trend_24h_pct_per_hour"],
            m["projected_final"],
            m["hours_to_reset"],
            runway_hours_24h=m["runway_hours_24h"],
        )
        windows.append(m)

    names = list(providers) if providers is not None else list(DEFAULT_VERDICT_PROVIDERS)
    # Include any providers seen in samples that aren't in the default list
    for p, _w in by_pw:
        if p not in names:
            names.append(p)

    verdicts: dict[str, Any] = {}
    for provider in names:
        window, row = pick_verdict_window(provider, by_pw)
        if row is None:
            verdicts[provider] = {
                "verdict": "UNKNOWN",
                "used_percent": None,
                "burn_per_hour": None,
                "trend_total_pct_per_hour": None,
                "trend_24h_pct_per_hour": None,
                "runway_hours_total": None,
                "runway_hours_24h": None,
                "hours_to_reset": None,
                "window_hours": None,
                "pace_pct_of_quota": {"total": None, "24h": None},
                "pace_pct_of_quota_total": None,
                "pace_pct_of_quota_24h": None,
                "pace_pct": None,
                "projected_final": None,
                "resets_at": None,
                "window": window,
                "basis": _empty_basis(),
                "pace": "—",
                "pace_marker": "—",
            }
            continue

        m = metrics_for_row(samples, row, now)
        v = verdict_for(
            m["used_percent"],
            m["trend_24h_pct_per_hour"],
            m["projected_final"],
            m["hours_to_reset"],
            runway_hours_24h=m["runway_hours_24h"],
        )
        verdicts[provider] = {
            "verdict": v,
            "used_percent": m["used_percent"],
            "burn_per_hour": m["trend_24h_pct_per_hour"],
            "trend_total_pct_per_hour": m["trend_total_pct_per_hour"],
            "trend_24h_pct_per_hour": m["trend_24h_pct_per_hour"],
            "runway_hours_total": m["runway_hours_total"],
            "runway_hours_24h": m["runway_hours_24h"],
            "hours_to_reset": m["hours_to_reset"],
            "window_hours": m["window_hours"],
            "pace_pct_of_quota": m["pace_pct_of_quota"],
            "pace_pct_of_quota_total": m["pace_pct_of_quota_total"],
            "pace_pct_of_quota_24h": m["pace_pct_of_quota_24h"],
            "pace_pct": m["pace_pct"],
            "projected_final": m["projected_final"],
            "resets_at": m["resets_at"],
            "window": m["window"],
            "basis": m["basis"],
            "pace": m["pace"],
            "pace_marker": m["pace_marker"],
        }

    return {
        "ts": now.isoformat(timespec="seconds"),
        "verdicts": verdicts,
        "windows": windows,
    }


def verdicts(
    samples: list[dict[str, Any]] | None = None,
    *,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate STOP/WARN/OK for each provider (lib surface)."""
    if samples is None:
        samples = load_samples(path)
    return evaluate(samples, now=now)


def exit_code(result: dict[str, Any]) -> int:
    labels = [v.get("verdict") for v in result.get("verdicts", {}).values()]
    if any(x == "STOP" for x in labels):
        return 2
    if any(x == "WARN" for x in labels):
        return 1
    return 0


def _normalize_period_end(resets_at: str | None) -> str:
    if not resets_at:
        return "no_reset"
    dt = parse_ts(resets_at)
    if dt is None:
        return str(resets_at)
    return dt.replace(microsecond=0).isoformat()


def history_from_samples(
    samples: list[dict[str, Any]],
    *,
    sparse_below: int = HISTORY_SPARSE_BELOW,
) -> dict[str, Any]:
    """Group ok samples by (provider, window, resets_at) → peak used per period."""
    clusters: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in samples:
        if not ok_numeric(row):
            continue
        provider = row.get("provider")
        window = row.get("window")
        if not isinstance(provider, str) or not isinstance(window, str):
            continue
        if window in {"unknown", "worst"}:
            continue
        resets = row.get("resets_at")
        period_key = _normalize_period_end(
            resets if isinstance(resets, str) else None
        )
        key = (provider, window, period_key)
        try:
            pct = float(row["used_percent"])
        except (TypeError, ValueError):
            continue
        bucket = clusters.get(key)
        if bucket is None:
            clusters[key] = {
                "provider": provider,
                "window": window,
                "period_end": period_key if period_key != "no_reset" else None,
                "peak_used_percent": pct,
                "samples_n": 1,
                "first_ts": row.get("ts"),
                "last_ts": row.get("ts"),
            }
        else:
            bucket["peak_used_percent"] = max(bucket["peak_used_percent"], pct)
            bucket["samples_n"] += 1
            ts = row.get("ts")
            if isinstance(ts, str):
                if bucket["first_ts"] is None or ts < bucket["first_ts"]:
                    bucket["first_ts"] = ts
                if bucket["last_ts"] is None or ts > bucket["last_ts"]:
                    bucket["last_ts"] = ts

    periods: list[dict[str, Any]] = []
    for bucket in clusters.values():
        n = int(bucket["samples_n"])
        bucket["sparse"] = n < sparse_below
        periods.append(bucket)

    def sort_key(p: dict[str, Any]) -> tuple:
        return (
            p.get("provider") or "",
            p.get("window") or "",
            p.get("period_end") or "",
        )

    periods.sort(key=sort_key)

    avg_map: dict[tuple[str, str], list[float]] = {}
    for p in periods:
        key = (str(p["provider"]), str(p["window"]))
        avg_map.setdefault(key, []).append(float(p["peak_used_percent"]))

    averages: list[dict[str, Any]] = []
    for (provider, window), peaks in sorted(avg_map.items()):
        averages.append(
            {
                "provider": provider,
                "window": window,
                "avg_peak_used_percent": sum(peaks) / len(peaks),
                "periods_n": len(peaks),
            }
        )

    return {
        "periods": periods,
        "averages": averages,
        "sparse_below": sparse_below,
    }


def history(
    samples: list[dict[str, Any]] | None = None,
    *,
    path: str | Path | None = None,
) -> dict[str, Any]:
    if samples is None:
        samples = load_samples(path)
    return history_from_samples(samples)


def print_history(history_data: dict[str, Any]) -> None:
    periods = history_data.get("periods") or []
    averages = history_data.get("averages") or []
    sparse_below = history_data.get("sparse_below", HISTORY_SPARSE_BELOW)

    print("\n  QUOTA HISTORY — peak used % per reset period\n")
    print(
        f"  {'provider':<11} {'window':<18} {'period end':<28} "
        f"{'peak used':>9}  {'n':>4}  flag"
    )
    print(
        f"  {'─' * 11} {'─' * 18} {'─' * 28} "
        f"{'─' * 9}  {'─' * 4}  {'─' * 8}"
    )
    if not periods:
        print("  (no ok samples with usable windows yet)")
    for p in periods:
        pend = p.get("period_end") or "—"
        if isinstance(pend, str) and len(pend) > 28:
            pend = pend[:25] + "..."
        peak = p.get("peak_used_percent")
        peak_s = f"{float(peak):6.1f}%" if peak is not None else "     —"
        flag = "sparse" if p.get("sparse") else ""
        print(
            f"  {str(p.get('provider')):<11} {str(p.get('window')):<18} {pend:<28} "
            f"{peak_s:>9}  {int(p.get('samples_n') or 0):>4}  {flag}"
        )

    print()
    print("  Average peak utilization (mean of period peaks):")
    if not averages:
        print("  (none yet)")
    for a in averages:
        print(
            f"  · you use on average {a['avg_peak_used_percent']:.0f}% of your "
            f"{a['provider']} {a['window']}  ({a['periods_n']} period(s))"
        )
    print()
    print(
        f"  sparse = fewer than {sparse_below} samples in that period "
        "(history still thin; regular sampler cadence will fill this in)."
    )
    print()


def print_pretty(result: dict[str, Any]) -> None:
    """Human table: used + burn/need rates + verdict (same dialect as ai-quotas)."""
    ts = result.get("ts") or ""
    print(f"\n  QUOTA WATCHDOG — used + burn/need  (as of {ts})\n")
    print(
        f"  {'vendor':<8} {'window':<18} {'used':>6}  "
        f"{'resets':>10}  "
        f"{'burn 24h':>{_RATE_COL}}  {'need rem':>{_RATE_COL}}  "
        f"{'burn tot':>{_RATE_COL}}  {'need avg':>{_RATE_COL}}  verdict"
    )
    print(
        f"  {'─' * 8} {'─' * 18} {'─' * 6}  "
        f"{'─' * 10}  "
        f"{'─' * _RATE_COL}  {'─' * _RATE_COL}  "
        f"{'─' * _RATE_COL}  {'─' * _RATE_COL}  {'─' * 7}"
    )

    windows = list(result.get("windows") or [])
    windows = [
        w
        for w in windows
        if str(w.get("window") or "") not in _NON_QUOTA_WINDOWS
    ]
    seen_providers = {w.get("provider") for w in windows}
    provider_order = list(DEFAULT_VERDICT_PROVIDERS)
    for name in result.get("verdicts", {}):
        if name not in provider_order:
            provider_order.append(name)
    for name in provider_order:
        if name not in seen_providers:
            v = (result.get("verdicts") or {}).get(name) or {}
            windows.append(
                {
                    "provider": name,
                    "window": v.get("window") or "—",
                    "used_percent": None,
                    "hours_to_reset": None,
                    "window_hours": None,
                    "trend_24h_pct_per_hour": None,
                    "trend_total_pct_per_hour": None,
                    "verdict": v.get("verdict") or "UNKNOWN",
                }
            )

    order = {p: i for i, p in enumerate(provider_order)}
    windows.sort(
        key=lambda w: (order.get(str(w.get("provider")), 9), str(w.get("window") or ""))
    )

    last_provider = None
    for w in windows:
        provider = str(w.get("provider") or "?")
        window = str(w.get("window") or "?")
        label = provider if provider != last_provider else ""
        last_provider = provider

        used = w.get("used_percent")
        used_s = f"{float(used):5.1f}%" if used is not None else "     —"
        reset_s = format_duration_hours(w.get("hours_to_reset"))
        bn = burn_need_from_metrics(w)
        wh = bn["window_hours"]
        burn_24h_s = format_rate_cell(bn["burn_24h"], window_hours=wh)
        need_rem_s = format_rate_cell(bn["need_rem"], window_hours=wh)
        burn_tot_s = format_rate_cell(bn["burn_tot"], window_hours=wh)
        need_avg_s = format_rate_cell(bn["need_avg"], window_hours=wh)
        verdict = w.get("verdict") or "—"

        print(
            f"  {label:<8} {window:<18} {used_s}  "
            f"{reset_s:>10}  "
            f"{burn_24h_s}  {need_rem_s}  "
            f"{burn_tot_s}  {need_avg_s}  {verdict}"
        )

    print()
    print("  used = percent USED.  burn 24h ↔ need rem · burn tot ↔ need avg.")
    print(
        "  need rem = rate to spend remaining by reset; "
        "need avg = even-spend over full window."
    )
    print(
        "  units = %/h (≤12h windows) or %/d (week/month).  JSON still has pace/runway."
    )
    print("  Canonical table: ai-quotas.  This view is for verdict debugging.")
    print()
