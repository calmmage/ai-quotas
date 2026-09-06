"""Quota samples for plotting: % remaining, family colors, reset from used drops.

Rules (Petr 11 Aug 2026):
- Drop samples before the long collection gap (~20d hole after Jul 28).
- One plot per vendor always (Claude / Codex / Grok / Gemini).
- Reset = used% goes to ~0 OR decreases significantly (not claimed dates).
- False refill: remaining jumps up then snaps back to the previous used%
  within 3h — drop those samples (Petr 07 Sep 2026).
- Y axis for plots = % remaining = 100 - used.
- Colors match ai-quotas family: Claude orange, Codex blue, Grok green, Gemini purple.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ai_quotas.core import load_samples
from ai_quotas.paths import data_dir, samples_path
from ai_quotas.reset_credits import credit_states, parse_ts as parse_credit_ts, summarize as summarize_credits
from ai_quotas.storage import load_reset_credits

# Default runtime output (gitignored): ~/.local/share/ai-quotas/plots
# Samples resolve through the shared SQLite/legacy-JSONL storage layer.

# After the Jul28→Aug7 black hole; anything earlier is dropped entirely.
MIN_TS_LOCAL_DEFAULT = datetime(2026, 8, 7, 0, 0, 0)  # filled with local tz in load

KEEP_WINDOWS = {
    "claude": {"week", "week_fable", "5h"},
    "codex": {"week"},
    "grok": {"week", "month"},
    "agy": {
        "week_gemini_flash",
        "week_gemini_pro",
        "5h_gemini_flash",
        "5h_gemini_pro",
    },
}

LABELS = {
    "claude/week_fable": "Claude Fable",
    "claude/week": "Claude week",
    "claude/5h": "Claude 5h",
    "codex/week": "Codex week",
    "grok/week": "Grok week",
    "grok/month": "Grok month",
    "agy/week_gemini_flash": "Gemini Flash week",
    "agy/week_gemini_pro": "Gemini Pro week",
    "agy/5h_gemini_flash": "Gemini Flash 5h",
    "agy/5h_gemini_pro": "Gemini Pro 5h",
}

VENDOR_OF = {
    "Claude Fable": "Claude",
    "Claude week": "Claude",
    "Claude 5h": "Claude",
    "Codex week": "Codex",
    "Grok week": "Grok",
    "Grok month": "Grok",
    "Gemini Flash week": "Gemini",
    "Gemini Pro week": "Gemini",
    "Gemini Flash 5h": "Gemini",
    "Gemini Pro 5h": "Gemini",
}

VENDORS = ["Claude", "Codex", "Grok", "Gemini"]

# Family colors — same family = same hue; intensity = window
COLORS = {
    "Claude Fable": "#E67E22",
    "Claude week": "#C49A6C",
    "Claude 5h": "#E8B84A",
    "Codex week": "#5B9BD5",
    "Grok week": "#7CB87C",
    "Grok month": "#2E7D4F",
    "Gemini Flash week": "#9B6BB5",
    "Gemini Pro week": "#6B3F8A",
    "Gemini Flash 5h": "#C9A0DC",
    "Gemini Pro 5h": "#8E5BB0",
}

# Reset detection (on used%):
#   - goes to ~0 from meaningful use  → always reset
#   - absolute drop ≥ SIG_ABS         → reset
#   - relative drop ≥ SIG_REL of prior → reset
# Noise guard: 1%→0% quantization is not a reset (need prior ≥ TO_ZERO_MIN_PRIOR).
TO_ZERO = 1.0
TO_ZERO_MIN_PRIOR = 3.0
SIG_ABS = 5.0
SIG_REL = 0.25
MAX_SAMPLE_GAP = timedelta(hours=3)
# False refill: remaining jumps up (used drops) then snaps back to the
# pre-jump used% within MAX_SAMPLE_GAP. Real resets stay high and burn down.
SNAP_ABS = 8.0


# ─── money valuation ─────────────────────────────────────────────────────────
# Monthly subscription list prices (USD). Window value = monthly × (hours/window / hours/month).
MONTHLY_USD = {
    "Claude": 200.0,  # Claude Max-ish
    "Codex": 200.0,  # ChatGPT / Codex
    "Grok": 300.0,
    "Gemini": 30.0,
}
HOURS_PER_MONTH = 30.0 * 24.0  # pro-rate base

# Expected full-window length, used to judge "before/after full window since last burn"
WINDOW_HOURS = {
    "Claude Fable": 7 * 24,
    "Claude week": 7 * 24,
    "Claude 5h": 5,
    "Codex week": 7 * 24,
    "Grok week": 7 * 24,
    "Grok month": HOURS_PER_MONTH,
    "Gemini Flash week": 7 * 24,
    "Gemini Pro week": 7 * 24,
    "Gemini Flash 5h": 5,
    "Gemini Pro 5h": 5,
}

# Rolling session/rate-limit windows (5h) aren't purchased subscription blocks —
# they recycle many times a day regardless of usage, so pricing their leftover
# as lost/free $ is meaningless. Only price real subscription-cycle windows
# (week/month). Threshold in hours; 5h windows fall well under it.
MONEY_MIN_WINDOW_HOURS = 24.0

# Nested scoped windows sit inside the vendor's billed total (Claude
# weekly_scoped / Fable inside weekly_all). Pricing them independently
# double-counts the same subscription dollar (Petr 07 Sep 2026).
SCOPED_SERIES = {"Claude Fable"}


def is_priced_series(series: str) -> bool:
    """Whether leftover/used% of this series is valued as lost/gained $."""
    if series in SCOPED_SERIES:
        return False
    return float(WINDOW_HOURS.get(series, 0)) >= MONEY_MIN_WINDOW_HOURS


def annotates_reset(series: str) -> bool:
    """Whether this series gets vertical reset marks and $ pills on the plot.

    5h session windows refresh many times a day; drawing every refresh
    buries the week/month curves (Petr 17 Aug 2026). Scoped slices of a
    billed total (Claude Fable) are drawn as curves but not marked — the
    total window (Claude week / weekly_all) carries the $ (Petr 07 Sep 2026).
    """
    return is_priced_series(series)


# Subscription windows only — not rolling 5h session limits.
RESET_ANNOTATE = {s for s in LABELS.values() if annotates_reset(s)}


@dataclass(frozen=True)
class ResetEvent:
    series: str
    vendor: str
    at: datetime
    used_before: float
    used_after: float
    remaining_before: float
    remaining_after: float
    period_before: timedelta | None  # time since last BURN (None on first reset)
    label: str
    # money
    kind: str  # "burn" | "free"
    money_usd: float  # + free money, − burn
    window_usd: float  # full-window $ value for this series
    expected_hours: float
    money_label: str  # e.g. "FREE +$12" / "BURN −$8"


def parse_ts(raw: str) -> datetime:
    text = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def local_tz():
    return datetime.now().astimezone().tzinfo or timezone.utc


def fmt_delta(td: timedelta | None) -> str:
    if td is None:
        return "n/a"
    secs = max(0, int(td.total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def is_reset(used_before: float, used_after: float) -> bool:
    """ANY time used% goes to ~0 or decreases significantly → reset.

    Guards: ignore 1% quantization (1→0). Require meaningful prior level
    for to-zero and relative rules; absolute ≥ SIG_ABS always counts.
    """
    if used_after >= used_before:
        return False
    drop = used_before - used_after
    if drop < 2.0:
        return False  # 1pp noise
    # to zero from real use
    if used_after <= TO_ZERO and used_before >= TO_ZERO_MIN_PRIOR:
        return True
    if drop >= SIG_ABS:
        return True
    if used_before >= TO_ZERO_MIN_PRIOR and (drop / used_before) >= SIG_REL:
        return True
    return False


def is_snapback(prior_used: float, trough_used: float, later_used: float) -> bool:
    """Later used% returned near the pre-reset level, not a gradual burn from the trough."""
    if later_used + SNAP_ABS < prior_used:
        return False
    if later_used - trough_used < SIG_ABS:
        return False
    return True


def glitch_reset_indices(ts: list[datetime], used: list[float]) -> list[int]:
    """Indices of false-refill samples: used% drops, then snaps back within MAX_SAMPLE_GAP.

    A real reset stays near the trough and then burns up gradually. A glitch
    (Codex week 44%→0%→44% in 30m on 03 Sep 2026) is dropped so it does not
    draw a 100% remaining spike or mint a FREE leftover-$ marker.
    """
    n = len(used)
    if n < 3:
        return []
    drop: set[int] = set()
    last_kept = 0
    i = 1
    while i < n:
        if not is_reset(used[last_kept], used[i]):
            last_kept = i
            i += 1
            continue
        prior = used[last_kept]
        trough = used[i]
        j = i
        snapped = False
        while j + 1 < n and (ts[j + 1] - ts[i]) <= MAX_SAMPLE_GAP:
            nxt = used[j + 1]
            if is_snapback(prior, trough, nxt):
                drop.update(range(i, j + 1))
                i = j + 1
                last_kept = i
                i += 1
                snapped = True
                break
            if nxt <= trough + SNAP_ABS:
                j += 1
                continue
            break
        if snapped:
            continue
        last_kept = i
        i += 1
    return sorted(drop)


def drop_glitch_reset_samples(df: pd.DataFrame) -> pd.DataFrame:
    """Remove false-refill rows per series. Call before gap-NaN insertion."""
    if df.empty or "series" not in df.columns:
        return df
    pieces: list[pd.DataFrame] = []
    for _, g in df.groupby("series", sort=False):
        g = g.sort_values("ts")
        if len(g) < 3:
            pieces.append(g)
            continue
        ts = g["ts"].tolist()
        used = [float(x) for x in g["used_percent"]]
        drop = set(glitch_reset_indices(ts, used))
        if not drop:
            pieces.append(g)
            continue
        keep = [idx for pos, idx in enumerate(g.index) if pos not in drop]
        pieces.append(g.loc[keep])
    return pd.concat(pieces, ignore_index=True) if pieces else df


# ─── burn walk (shared by density ticks + rate line) ─────────────────────────


@dataclass(frozen=True)
class BurnWalk:
    """Reset- and gap-aware walk over one series' used% samples.

    Single owner of the "what counts as burn" semantics, so the density ticks
    and the %/h rate line can never disagree about resets, noise, or gaps.
    """

    ts: list[datetime]  # ts_local, NaN rows dropped, sorted
    used: list[float]
    cum: list[float]  # counted burn since the start of this segment
    seg: list[int]  # segment id; increments on a real reset OR a sampling gap
    inc: list[float]  # counted burn on (i-1 → i]; 0.0 at segment starts


def cumulative_burn(g: pd.DataFrame) -> BurnWalk:
    """Walk samples, accumulating burn within reset/gap-delimited segments.

    A real reset (per `is_reset`) or a sampling gap > MAX_SAMPLE_GAP starts a
    new segment. Sub-threshold downward jitter is clamped to zero burn but does
    NOT restart the segment — otherwise every noisy sample would look like a
    reset and pile ticks up against the top of the plot.
    """
    g = g.dropna(subset=["used_percent", "ts_local"]).sort_values("ts_local")
    ts = g["ts_local"].tolist()
    used = [float(x) for x in g["used_percent"]]
    if not ts:
        return BurnWalk([], [], [], [], [])
    cum, seg, inc = [0.0], [0], [0.0]
    for i in range(1, len(used)):
        if (ts[i] - ts[i - 1]) > MAX_SAMPLE_GAP or is_reset(used[i - 1], used[i]):
            seg.append(seg[-1] + 1)
            cum.append(0.0)
            inc.append(0.0)
            continue
        d = max(used[i] - used[i - 1], 0.0)
        seg.append(seg[-1])
        inc.append(d)
        cum.append(cum[-1] + d)
    return BurnWalk(ts, used, cum, seg, inc)


def sustainable_rate(series: str) -> float:
    """Pace (%/h) that exactly consumes the window. Above it → early exhaustion."""
    return 100.0 / float(WINDOW_HOURS.get(series, 7 * 24))


def budget_line(g: pd.DataFrame, series: str) -> list[list[tuple[datetime, float]]]:
    """Constant-pace depletion line for each quota window: reset → next reset.

    Slope is 100% / expected window (7d or 30d), i.e. the pace the subscription
    is priced for. Drawn in the same "% remaining" space as the data: the curve
    above the line means under-spending, below means it runs out early.

    One 2-point polyline per window. Windows are delimited by *resets* only —
    sampling gaps split the burn walk but do not start a new quota window. The
    first (partial) window is anchored at the first observed value, since its
    real start predates the data.
    """
    gg = g.dropna(subset=["used_percent", "ts_local"]).sort_values("ts_local")
    ts = gg["ts_local"].tolist()
    used = [float(x) for x in gg["used_percent"]]
    if len(ts) < 2:
        return []
    rate = sustainable_rate(series)  # %/h
    if rate <= 0:
        return []
    starts = [0] + [i for i in range(1, len(used)) if is_reset(used[i - 1], used[i])]
    out = []
    for k, i in enumerate(starts):
        t0, y0 = ts[i], 100.0 - used[i]
        # window ends at the next reset, else at the last sample
        end_i = starts[k + 1] if k + 1 < len(starts) else len(ts) - 1
        t_end = ts[end_i]
        if y0 <= 0 or t0 >= t_end:
            continue
        # stop early if the pace would already have exhausted the window
        t_zero = t0 + timedelta(hours=y0 / rate)
        t1 = min(t_zero, t_end)
        y1 = y0 - rate * ((t1 - t0).total_seconds() / 3600.0)
        out.append([(t0, y0), (t1, max(y1, 0.0))])
    return out


def window_usd_value(series: str, vendor: str) -> tuple[float, float]:
    """Return (full_window_usd, expected_hours)."""
    hours = float(WINDOW_HOURS.get(series, 7 * 24))
    monthly = float(MONTHLY_USD.get(vendor, 0.0))
    usd = monthly * (hours / HOURS_PER_MONTH)
    return usd, hours


def value_remaining_usd(remaining_pct: float, window_usd: float) -> float:
    """Dollar value of leftover remaining% of one full window."""
    return (max(0.0, remaining_pct) / 100.0) * window_usd


def classify_money(
    series: str,
    vendor: str,
    remaining_before: float,
    period_since_last_burn: timedelta | None,
    is_first_reset: bool,
) -> tuple[str, float, float, float, str]:
    """Return (kind, money_usd, window_usd, expected_hours, money_label).

    Per series:
      1. First reset ever → BURN: leftover remaining is lost (−$).
      2. Reset before a full expected window has passed since the last BURN → FREE (+$):
         the used% that got refilled, not the leftover remaining (Petr 07 Sep 2026:
         80% left → +20% of window, not +80%).
      3. Reset after a full expected window since the last BURN → new BURN (−$).
    money_label is a plain "+$N" / "−$N" tooltip (fmt_money).

    Windows shorter than MONEY_MIN_WINDOW_HOURS (rolling session limits like
    Claude/Gemini 5h) and scoped nested windows (Claude Fable) are not priced
    at all — see MONEY_MIN_WINDOW_HOURS / SCOPED_SERIES.
    """
    window_usd, expected_h = window_usd_value(series, vendor)
    leftover_usd = value_remaining_usd(remaining_before, window_usd)
    used_usd = value_remaining_usd(max(0.0, 100.0 - remaining_before), window_usd)

    if not is_priced_series(series):
        return "reset", 0.0, window_usd, expected_h, ""

    if is_first_reset:
        money_usd = -leftover_usd
        return "burn", money_usd, window_usd, expected_h, fmt_money(money_usd)

    period_h = (
        period_since_last_burn.total_seconds() / 3600.0
        if period_since_last_burn is not None
        else 0.0
    )
    if expected_h > 0 and period_h < expected_h:
        money_usd = +used_usd
        return "free", money_usd, window_usd, expected_h, fmt_money(money_usd)

    money_usd = -leftover_usd
    return "burn", money_usd, window_usd, expected_h, fmt_money(money_usd)


def fmt_money(usd: float) -> str:
    if abs(usd) < 0.05:
        return "$0"
    sign = "+" if usd > 0 else "−"
    return f"{sign}${abs(usd):.0f}"


def load_long(samples: Path | None = None) -> tuple:
    """Long df after gap cutoff. remaining_percent = 100 - used. NaN line breaks on gaps.

    ``samples`` overrides path resolution (CLI ``--samples`` / library callers).
    """
    path = samples_path(samples)
    rows: list[dict] = []
    if not path.is_file():
        raise FileNotFoundError(path)
    for o in load_samples(path):
        if o.get("status") != "ok" or o.get("used_percent") is None:
            continue
        prov = o.get("provider")
        win = o.get("window")
        if not isinstance(prov, str) or not isinstance(win, str):
            continue
        if win not in KEEP_WINDOWS.get(prov, set()):
            continue
        key = f"{prov}/{win}"
        label = LABELS.get(key)
        if not label:
            continue
        used = float(o["used_percent"])
        rows.append(
            {
                "ts": parse_ts(o["ts"]),
                "series": label,
                "vendor": VENDOR_OF[label],
                "used_percent": used,
                "remaining_percent": 100.0 - used,
            }
        )
    if not rows:
        raise RuntimeError("no ok samples")

    df = pd.DataFrame(rows).sort_values(["series", "ts"]).reset_index(drop=True)
    tz = local_tz()
    df["ts_local"] = df["ts"].map(lambda t: t.astimezone(tz))

    # Drop pre-gap island: keep only samples on/after continuous era.
    # Prefer auto: after the largest inter-batch gap; floor at MIN if no big gap.
    batch_ts = sorted(df["ts_local"].unique())
    cutoff = MIN_TS_LOCAL_DEFAULT.replace(tzinfo=tz)
    if len(batch_ts) >= 2:
        biggest = None
        for a, b in zip(batch_ts, batch_ts[1:], strict=False):
            gap = b - a
            if biggest is None or gap > biggest[0]:
                biggest = (gap, b)
        # Only treat as the collection hole if gap ≥ 24h
        if biggest is not None and biggest[0] >= timedelta(hours=24):
            cutoff = biggest[1]
    kept = df[df["ts_local"] >= cutoff].copy()
    if kept.empty:
        # Fixtures / short histories that predate the default floor: keep all.
        cutoff = df["ts_local"].min()
        kept = df
    df = kept
    if df.empty:
        raise RuntimeError(f"no samples after cutoff {cutoff}")

    df = drop_glitch_reset_samples(df)
    if df.empty:
        raise RuntimeError(f"no samples after cutoff {cutoff}")

    # Line breaks across sampling gaps within retained window
    broken: list[dict] = []
    for series, g in df.groupby("series", sort=False):
        g = g.sort_values("ts")
        prev_ts = None
        for row in g.itertuples(index=False):
            if prev_ts is not None and (row.ts - prev_ts) > MAX_SAMPLE_GAP:
                broken.append(
                    {
                        "ts": prev_ts + timedelta(seconds=1),
                        "series": series,
                        "vendor": row.vendor,
                        "used_percent": float("nan"),
                        "remaining_percent": float("nan"),
                    }
                )
            broken.append(
                {
                    "ts": row.ts,
                    "series": series,
                    "vendor": row.vendor,
                    "used_percent": row.used_percent,
                    "remaining_percent": row.remaining_percent,
                }
            )
            prev_ts = row.ts
    df = pd.DataFrame(broken)
    df["ts_local"] = df["ts"].map(lambda t: t.astimezone(tz))
    return df, cutoff


def detect_resets(df: pd.DataFrame) -> list[ResetEvent]:
    events: list[ResetEvent] = []
    for series, g in df.groupby("series", sort=False):
        if series not in RESET_ANNOTATE:
            continue
        g = g.dropna(subset=["used_percent"]).sort_values("ts")
        if g.empty:
            continue
        vendor = str(g["vendor"].iloc[0])
        last_burn_at: datetime | None = None  # money-eligible series only
        prev_reset_at: datetime | None = None  # any series, for display only
        pts = list(zip(g["ts"], g["used_percent"], strict=True))
        for (t0, y0), (t1, y1) in zip(pts, pts[1:], strict=False):
            # Remaining going *up* cannot be a sampling hole — a gap can
            # only hide extra burn, never invent leftover quota. The 3h
            # MAX_SAMPLE_GAP still breaks the drawn line; it must not
            # hide weekly refills that happened overnight (Claude week
            # 63%→100% and Grok week 65%→98% on 13 Aug were dropped).
            if not is_reset(float(y0), float(y1)):
                continue
            is_first_reset = last_burn_at is None
            period = None if is_first_reset else (t1 - last_burn_at)
            rem_before = 100.0 - float(y0)
            rem_after = 100.0 - float(y1)
            kind, money_usd, window_usd, expected_h, money_label = classify_money(
                str(series), vendor, rem_before, period, is_first_reset
            )
            if money_label:
                label = f"{money_label} · {fmt_delta(period) if period is not None else 'first'}"
            else:
                # not priced (e.g. rolling session window) — just show elapsed time
                label = f"reset · {fmt_delta(t1 - prev_reset_at) if prev_reset_at is not None else 'first'}"
            events.append(
                ResetEvent(
                    series=str(series),
                    vendor=vendor,
                    at=t1,
                    used_before=float(y0),
                    used_after=float(y1),
                    remaining_before=rem_before,
                    remaining_after=rem_after,
                    period_before=period,
                    label=label,
                    kind=kind,
                    money_usd=money_usd,
                    window_usd=window_usd,
                    expected_hours=expected_h,
                    money_label=money_label,
                )
            )
            if kind == "burn":
                last_burn_at = t1
            prev_reset_at = t1
    return events


# ─── reset credits (vendor "reset your weekly limit" tokens) ─────────────────
PROVIDER_VENDOR = {"claude": "Claude", "codex": "Codex", "grok": "Grok", "agy": "Gemini"}
CREDIT_MATCH_WINDOW = timedelta(hours=3)


@dataclass(frozen=True)
class CreditEvent:
    """One reset credit and what happened to it (see reset_credits.credit_states)."""

    vendor: str
    provider: str
    credit_id: str
    title: str
    status: str  # available | consumed | expired
    granted_at: datetime | None
    expires_at: datetime | None
    ended_at: datetime | None
    window_usd: float  # value of one full primary window
    money_usd: float  # + consumed (used% refilled) · − expired unused · 0 available
    money_label: str
    used_before: float | None  # used% refilled by a consumed credit, when matched


def load_credit_events(
    samples: Path | None = None,
    *,
    resets: list[ResetEvent] | None = None,
    now: datetime | None = None,
    rows: list[dict] | None = None,
) -> list[CreditEvent]:
    """Reset credits priced against the vendor's primary window.

    expired unused → −(full window $) — the reset would have refilled a whole
                     window and was never used
    consumed       → +(used% at redemption × window $), matched to the used%
                     drop detected on the quota series within ±3h; unmatched
                     → +0 with label "consumed (value unknown)"
    available      → 0 (shown as a badge, not money yet)
    """
    if rows is None:
        try:
            rows = load_reset_credits(samples_path(samples))
        except Exception:
            rows = []
    events: list[CreditEvent] = []
    for st in credit_states(rows, now=now):
        vendor = PROVIDER_VENDOR.get(st["provider"], st["provider"].title())
        series = PRIMARY_SERIES.get(vendor)
        window_usd = window_usd_value(series, vendor)[0] if series else 0.0
        ended = parse_credit_ts(st.get("ended_at"))
        used_before: float | None = None
        money = 0.0
        label = "reset available"
        if st["status"] == "expired":
            money = -window_usd
            label = f"reset expired unused {fmt_money(money)}"
        elif st["status"] == "consumed":
            if ended is not None and resets:
                near = [
                    r
                    for r in resets
                    if r.vendor == vendor
                    and r.series == series
                    and abs(r.at - ended) <= CREDIT_MATCH_WINDOW
                ]
                if near:
                    best = min(near, key=lambda r: abs(r.at - ended))
                    used_before = float(best.used_before)
                    money = (used_before / 100.0) * window_usd
            label = (
                f"reset redeemed {fmt_money(money)}"
                if used_before is not None
                else "reset redeemed (value unknown)"
            )
        events.append(
            CreditEvent(
                vendor=vendor,
                provider=st["provider"],
                credit_id=st["credit_id"],
                title=str(st.get("title") or "reset"),
                status=st["status"],
                granted_at=parse_credit_ts(st.get("granted_at")),
                expires_at=parse_credit_ts(st.get("expires_at")),
                ended_at=ended,
                window_usd=window_usd,
                money_usd=money,
                money_label=label,
                used_before=used_before,
            )
        )
    return events


def credit_summary(events: list[CreditEvent]) -> dict[str, dict[str, float]]:
    """Per-vendor + TOTAL: available / consumed / expired counts, gain / loss $."""
    out: dict[str, dict[str, float]] = {
        v: {"available": 0.0, "consumed": 0.0, "expired": 0.0, "gain": 0.0, "loss": 0.0}
        for v in [*VENDORS, "TOTAL"]
    }
    for e in events:
        for key in (e.vendor, "TOTAL"):
            b = out.setdefault(
                key, {"available": 0.0, "consumed": 0.0, "expired": 0.0, "gain": 0.0, "loss": 0.0}
            )
            b[e.status] += 1
            if e.money_usd > 0:
                b["gain"] += e.money_usd
            elif e.money_usd < 0:
                b["loss"] += -e.money_usd
    return out


def credit_badge(events: list[CreditEvent], vendor: str, now: datetime | None = None) -> str:
    """Short subtitle fragment: `1 reset · exp 12 Sep (8d)` / `` when none."""
    now = now or datetime.now(timezone.utc)
    avail = [e for e in events if e.vendor == vendor and e.status == "available"]
    if not avail:
        return ""
    nxt = min(avail, key=lambda e: e.expires_at or datetime.max.replace(tzinfo=timezone.utc))
    if nxt.expires_at is None:
        return f"{len(avail)} reset"
    left = nxt.expires_at - now
    days = left.total_seconds() / 86400.0
    left_txt = f"{days:.0f}d" if days >= 2 else f"{left.total_seconds() / 3600:.0f}h"
    return (
        f"{len(avail)} reset · exp {nxt.expires_at.astimezone(local_tz()).strftime('%d %b')} "
        f"({left_txt})"
    )


def money_summary(resets: list[ResetEvent]) -> dict[str, dict[str, float]]:
    """Per-vendor and total free / burn / net USD."""
    out: dict[str, dict[str, float]] = {
        v: {"free": 0.0, "burn": 0.0, "net": 0.0, "events": 0.0} for v in VENDORS
    }
    out["TOTAL"] = {"free": 0.0, "burn": 0.0, "net": 0.0, "events": 0.0}
    for r in resets:
        if not r.money_label:
            continue  # not priced (e.g. rolling session window) — excluded from $ summary
        bucket = out.setdefault(r.vendor, {"free": 0.0, "burn": 0.0, "net": 0.0, "events": 0.0})
        if r.money_usd > 0:
            bucket["free"] += r.money_usd
            out["TOTAL"]["free"] += r.money_usd
        elif r.money_usd < 0:
            bucket["burn"] += -r.money_usd
            out["TOTAL"]["burn"] += -r.money_usd
        bucket["net"] += r.money_usd
        bucket["events"] += 1
        out["TOTAL"]["net"] += r.money_usd
        out["TOTAL"]["events"] += 1
    return out


def format_money_report(
    resets: list[ResetEvent], credits: list[CreditEvent] | None = None
) -> str:
    summary = money_summary(resets)
    lines = [
        "QUOTA MONEY — free (early reset leftover) vs burn (proper reset leftover)",
        f"Monthly: Claude ${MONTHLY_USD['Claude']:.0f} · Codex ${MONTHLY_USD['Codex']:.0f} · "
        f"Grok ${MONTHLY_USD['Grok']:.0f} · Gemini ${MONTHLY_USD['Gemini']:.0f}",
        "First reset/series = BURN; reset before full window since last burn = FREE; "
        "reset after full window = new BURN. leftover $ = rem% × window value",
        "",
        f"{'vendor':8} {'free +$':>10} {'burn −$':>10} {'net':>10} {'n':>4}",
        f"{'─'*8} {'─'*10} {'─'*10} {'─'*10} {'─'*4}",
    ]
    for v in [*VENDORS, "TOTAL"]:
        s = summary[v]
        lines.append(
            f"{v:8} {s['free']:>10.1f} {s['burn']:>10.1f} {s['net']:>+10.1f} {int(s['events']):>4}"
        )
    if credits is not None:
        cs = credit_summary(credits)
        lines.append("")
        lines.append(
            "RESET CREDITS — vendor 'reset your limit' tokens: redeemed +$ (used% refilled × window), "
            "expired unused −$ (one full window lost)"
        )
        lines.append(
            f"{'vendor':8} {'avail':>6} {'used':>6} {'expired':>8} {'gain +$':>9} {'loss −$':>9}"
        )
        for v in [*VENDORS, "TOTAL"]:
            b = cs[v]
            lines.append(
                f"{v:8} {int(b['available']):>6} {int(b['consumed']):>6} {int(b['expired']):>8} "
                f"{b['gain']:>9.1f} {b['loss']:>9.1f}"
            )
        for e in credits:
            exp = e.expires_at.astimezone(local_tz()).strftime("%d %b %H:%M") if e.expires_at else "?"
            ended = (
                f"  ended {e.ended_at.astimezone(local_tz()).strftime('%d %b %H:%M')}"
                if e.ended_at
                else ""
            )
            lines.append(
                f"  {e.vendor:7} {e.credit_id[:24]:24} exp {exp}{ended}  win ${e.window_usd:.1f}  → {e.money_label}"
            )
    lines.append("")
    lines.append("Events:")
    for r in resets:
        if not r.money_label and r.money_usd == 0:
            tag = r.kind
        else:
            tag = r.money_label or r.kind
        lines.append(
            f"  {r.vendor:7} {r.series:18} {r.at.astimezone(local_tz()).strftime('%d %b %H:%M')}  "
            f"rem {r.remaining_before:.0f}%  period {fmt_delta(r.period_before)} / "
            f"exp {fmt_delta(timedelta(hours=r.expected_hours))}  "
            f"win ${r.window_usd:.1f}  → {tag}"
        )
    return "\n".join(lines)


def series_order_for_vendor(df: pd.DataFrame, vendor: str) -> list[str]:
    present = set(df.loc[df["vendor"] == vendor, "series"].unique())
    order = [s for s in LABELS.values() if s in present and VENDOR_OF[s] == vendor]
    for s in sorted(present):
        if s not in order:
            order.append(s)
    return order


# The one series each vendor's plot draws. Explicit rather than derived:
# "longest window" would pick Grok month over Grok week, and Flash over Pro
# for Gemini (both are 168h, and LABELS order puts Flash first).
PRIMARY_SERIES = {
    "Claude": "Claude week",
    "Codex": "Codex week",
    "Grok": "Grok week",
    "Gemini": "Gemini Pro week",
}


def primary_series_for_vendor(df: pd.DataFrame, vendor: str) -> str | None:
    """The single series to plot for this vendor, or None if it has no data."""
    order = series_order_for_vendor(df, vendor)
    if not order:
        return None
    pref = PRIMARY_SERIES.get(vendor)
    if pref in order:
        return pref
    # Fallback: longest real subscription window, LABELS order breaking ties.
    subs = [s for s in order if WINDOW_HOURS.get(s, 0) >= MONEY_MIN_WINDOW_HOURS]
    pool = subs or order
    return max(pool, key=lambda s: (WINDOW_HOURS.get(s, 0), -pool.index(s)))


def plot_series_for_vendor(df: pd.DataFrame, vendor: str) -> list[str]:
    """Single-element series list for the live renderers.

    Returned as a list so callers keep their `for s in order:` shape; the
    archived multi-series renderers keep using `series_order_for_vendor`.
    """
    s = primary_series_for_vendor(df, vendor)
    return [s] if s else []


def is_session_series(series: str) -> bool:
    """Rolling 5h (etc.) session windows — dim on the plot, never priced."""
    return float(WINDOW_HOURS.get(series, 0)) < MONEY_MIN_WINDOW_HOURS


def fmt_tokens(n: float | None) -> str:
    """Short leftover-token label. Empty when uncalibrated."""
    if n is None or n <= 0:
        return ""
    if n >= 1_000_000:
        return f"~{n / 1_000_000:.1f}M tok"
    if n >= 1000:
        return f"~{n / 1000:.0f}k tok"
    return f"~{n:.0f} tok"


VENDOR_SPEND_PROVIDER = {
    "Claude": "claude",
    "Codex": "codex",
    "Grok": "grok",
    "Gemini": "agy",
}


def _spend_ts(row: dict, tz) -> datetime | None:
    raw = row.get("ts")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        try:
            dt = parse_ts(str(raw))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def daily_spend_for_vendor(
    spend_rows: list[dict],
    vendor: str,
    *,
    days: int = 14,
    now: datetime | None = None,
) -> list[dict]:
    """Last ``days`` local-day token/$ totals. Separate grain from the % chart."""
    tz = local_tz()
    now_local = (now or datetime.now(tz)).astimezone(tz)
    today = now_local.date()
    start = today - timedelta(days=days - 1)
    provider = VENDOR_SPEND_PROVIDER.get(vendor)
    buckets = {
        start + timedelta(days=i): {"tokens": 0, "cost_usd": 0.0, "cost_n": 0}
        for i in range(days)
    }
    if provider:
        for row in spend_rows:
            if row.get("provider") != provider:
                continue
            dt = _spend_ts(row, tz)
            if dt is None:
                continue
            day = dt.date()
            if day not in buckets:
                continue
            tok = row.get("total_tokens")
            if isinstance(tok, (int, float)):
                buckets[day]["tokens"] += int(tok)
            cost = row.get("cost_usd")
            if isinstance(cost, (int, float)):
                buckets[day]["cost_usd"] += float(cost)
                buckets[day]["cost_n"] += 1
    out: list[dict] = []
    for day in sorted(buckets):
        b = buckets[day]
        out.append(
            {
                "day": day.strftime("%d %b"),
                "date": day.isoformat(),
                "tokens": b["tokens"],
                "cost_usd": None if b["cost_n"] == 0 else round(b["cost_usd"], 4),
            }
        )
    return out


def tokens_per_percent(
    df: pd.DataFrame,
    resets: list[ResetEvent],
    spend_rows: list[dict],
    vendor: str,
    series: str | None = None,
) -> float | None:
    """TOKEN-GAUGE: tokens observed ÷ Δused% in the current reset period.

    Returns tokens per 1% remaining, or None when Δused% is too small / no spend.
    """
    series = series or PRIMARY_SERIES.get(vendor)
    if not series or is_session_series(series):
        return None
    provider = VENDOR_SPEND_PROVIDER.get(vendor)
    if not provider:
        return None
    g = df[(df["vendor"] == vendor) & (df["series"] == series)].dropna(
        subset=["remaining_percent"]
    ).sort_values("ts_local")
    if len(g) < 2:
        return None
    last_reset = max(
        (r.at for r in resets if r.vendor == vendor and r.series == series),
        default=None,
    )
    if last_reset is not None:
        tz = g["ts_local"].iloc[0].tzinfo
        lr = last_reset.astimezone(tz) if last_reset.tzinfo else last_reset.replace(tzinfo=tz)
        g = g[g["ts_local"] >= lr]
    if len(g) < 2:
        return None
    used0 = 100.0 - float(g.iloc[0]["remaining_percent"])
    used1 = 100.0 - float(g.iloc[-1]["remaining_percent"])
    delta = used1 - used0
    if delta < 5.0:
        return None
    t0 = g.iloc[0]["ts_local"]
    t1 = g.iloc[-1]["ts_local"]
    tz = t0.tzinfo
    tok = 0
    for row in spend_rows:
        if row.get("provider") != provider:
            continue
        dt = _spend_ts(row, tz)
        if dt is None or dt < t0 or dt > t1:
            continue
        n = row.get("total_tokens")
        if isinstance(n, (int, float)) and n > 0:
            tok += int(n)
    if tok <= 0:
        return None
    return tok / delta


def annotate_reset_tokens(
    resets: list[ResetEvent],
    df: pd.DataFrame,
    spend_rows: list[dict],
) -> list[ResetEvent]:
    """Append leftover-token estimates to reset labels. $ labels stay as-is."""
    gauges: dict[tuple[str, str], float | None] = {}
    out: list[ResetEvent] = []
    for r in resets:
        key = (r.vendor, r.series)
        if key not in gauges:
            gauges[key] = tokens_per_percent(df, resets, spend_rows, r.vendor, r.series)
        tpp = gauges[key]
        qty = r.used_before if r.kind == "free" else r.remaining_before
        extra = fmt_tokens(None if tpp is None else qty * tpp)
        if extra:
            out.append(replace(r, label=f"{r.label} · {extra}"))
        else:
            out.append(r)
    return out


def color_map(order: list[str]) -> dict[str, str]:
    return {s: COLORS.get(s, "#888888") for s in order}


def default_plots_dir() -> Path:
    """Runtime plot output directory (not committed)."""
    from ai_quotas.paths import plots_dir

    return plots_dir()


def prepare(
    samples: Path | None = None,
    *,
    out_dir: Path | None = None,
) -> tuple:
    """Load samples, detect resets. Optionally ensure ``out_dir`` exists."""
    df, cutoff = load_long(samples)
    resets = detect_resets(df)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    return df, resets, cutoff


def title_vendor(vendor: str, df: pd.DataFrame) -> str:
    sub = df[df["vendor"] == vendor]
    if sub.empty:
        return f"{vendor} — % remaining"
    t0 = sub["ts_local"].min()
    t1 = sub["ts_local"].max()
    return (
        f"{vendor} — % remaining  ·  "
        f"{t0.strftime('%d %b %H:%M')} to {t1.strftime('%d %b %H:%M')}"
    )


def subtitle_resets(
    resets: list[ResetEvent],
    vendor: str | None = None,
    series: list[str] | None = None,
) -> str:
    """Summary line. Pass `series` to scope it to what the plot actually draws.

    Without it a Claude plot showing only the week series would still claim the
    4 resets that belong to Claude 5h.
    """
    rs = [
        r
        for r in resets
        if (vendor is None or r.vendor == vendor) and (series is None or r.series in series)
    ]
    free = sum(r.money_usd for r in rs if r.money_usd > 0)
    burn = sum(-r.money_usd for r in rs if r.money_usd < 0)
    net = sum(r.money_usd for r in rs)
    return (
        f"y = remaining  ·  {len(rs)} reset(s)  ·  "
        f"FREE +${free:.0f}  BURN −${burn:.0f}  net {fmt_money(net)}"
    )


if __name__ == "__main__":
    df, resets, cutoff = prepare()
    print("cutoff:", cutoff.isoformat())
    print("rows:", len(df), "span:", df["ts_local"].min(), "→", df["ts_local"].max())
    for v in VENDORS:
        order = series_order_for_vendor(df, v)
        print(f"\n{v}: {order}")
    print()
    print(format_money_report(resets))
