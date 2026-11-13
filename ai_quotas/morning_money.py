"""Money lines for the morning report: what the plans delivered, what went unused, what to claim.

Petr, 13 Nov 2026: «include a meaningful API cost summary and maybe some API
alerts (unused quota, etc.)». 12 Nov: «api balances (and expirations)» ·
«claim api credits == to your subscription cost) I want to track that too».

Format (write:telegram-formatting): «Provide max 3 KEY bullet points worth
attention / Show warnings - and meaningful diffs … AND IF NOT DIFFERENT - DONT
WARN!» · «minimal required information and all secondary unnecessary
information hidden in a collapsed quote block» · «NEVER DO LISTS OF > 3 ITEMS
AT SAME LEVEL!!!! NEST AND GROUP!!!!!» · «IF THERE IS NO ETA - DON'T WRITE ETA
UNKNOWN!!!».

So the visible lines are only diffs and deadlines, at most three, each an
action or a number: value lost at a refresh in the last day, spare value on a
login no live alert watches, something expiring within a week, and yesterday's
plan value used against what the plans cost per day. Standing facts (30-day
waste, credits to claim, banked resets, logins without readings) go in the
fold, three groups at most. An unknown price, balance or claim is left out.

Input is the dashboard's ``panels.json`` (``plots/generate.py``): the same
prices (``subscriptions.allocation_value`` → ``series[].window_usd``), the
same unused-value events (``prep.underutilised_events``) and the same API
records (``api_tracking.snapshot``). Nothing is re-priced here. Tokens are not
priced at API list rates: Claude rows lack cache-write tokens and every
login's turns share one table, Codex rows carry no model. Grok's own CLI
reports a cost per turn (``spend.py``: costUsdTicks); that number is shown as
the CLI's, not as a bill.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from ai_quotas.alert_text import app_name
from ai_quotas.alerts import SPARE_1D_HOURS, SPARE_2D_HOURS, SPARE_HALF, SPARE_QUARTER
from ai_quotas.reset_credits import EXPIRING_SOON
from ai_quotas.subscriptions import normalize_plan

STALE_DOC = timedelta(hours=6)       # panels.json older than this: say nothing
FRESH_READING = timedelta(hours=6)   # spare value needs a current reading
UNREAD_PAID = timedelta(days=7)      # a paid login with no reading this long
LOST_WINDOW = timedelta(hours=24)    # one morning to the next, counted once
WASTE_WINDOW = timedelta(days=30)
SPAN_GAP = timedelta(hours=12)       # a drop across a longer gap is not attributed
MIN_USD = 5.0                        # smaller amounts are not worth a line
MAX_VISIBLE = 3
DEFAULT_WINDOW_HOURS = 168.0

# Monthly API credits included in a subscription. Checked 13 Nov 2026:
# https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers
#   Max $100, Ultra $200 a month; Team pools seats for the plan owner; Pro, Free,
#   Enterprise not eligible; claimed by linking a Console organization; unused
#   credits expire at the end of each billing cycle, no rollover; they do not
#   cover Claude Code.
# https://developers.openai.com/api/docs/guides/chatgpt-api-credits
#   ChatGPT Pro $100 / $200 / $500 tiers get $100 / $200 / $500 a month; Plus not
#   eligible; Business pools seats for the workspace; claimed by linking an API
#   organization; unused credits expire at the end of the billing cycle.
# Team / Business pools belong to the workspace owner, so they are not listed.
CLAUDE_CREDIT = {"max": 100.0, "ultra": 200.0}
API_NAME = {"codex": "OpenAI", "claude": "Claude", "grok": "xAI", "agy": "Gemini"}  # who bills the API
CODEX_PRO_CREDIT = {100.0, 200.0, 500.0}
POOLED_PLANS = ("team", "business", "enterprise", "edu")


def money_section(doc: dict | None, *, spend_rows: list[dict] | None, now: datetime, tz: tzinfo,
                  since: datetime | None = None) -> dict:
    """``{"visible": [...], "fold": [...]}`` plain-text lines; empty when there is nothing to say.

    ``since`` is where "lost at a refresh" starts (default: 24 hours ago, so
    one loss is reported on one morning). Never raises.
    """
    try:
        return _section(doc, spend_rows=spend_rows, now=_aware(now), tz=tz, since=since)
    except Exception:  # noqa: BLE001 — the morning report goes out without money lines
        return {"visible": [], "fold": []}


# --- reading panels.json ---------------------------------------------------------------------


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ts(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _points(series: dict) -> list[tuple[float, float]]:
    t, y = series.get("t") or [], series.get("y") or []
    return [(float(a), float(b)) for a, b in zip(t, y) if _num(a) is not None and _num(b) is not None]


def _is_session(series: dict) -> bool:
    return str(series.get("window") or "").startswith("5h")


def _accounts(doc: dict) -> dict[tuple[str, str], dict]:
    """(provider, login) → panels, focus series, price, label. Primary = no ``account`` on the panel."""
    out: dict[tuple[str, str], dict] = {}
    for panel in doc.get("panels") or []:
        if not isinstance(panel, dict):
            continue
        provider = panel.get("provider")
        login = panel.get("account") or (panel.get("source") or {}).get("account")
        if not isinstance(provider, str) or not isinstance(login, str) or not login:
            continue
        acct = out.setdefault((provider, login), {
            "provider": provider, "login": login, "primary": False, "panels": [],
            "focus": [], "label": panel.get("account_label") or login.split("@")[0],
            "monthly": None, "plan": "", "window_hours": None,
        })
        acct["panels"].append(panel)
        acct["primary"] = acct["primary"] or panel.get("account") is None
        sub = panel.get("subscription") or {}
        declared = panel.get("declared_subscription") or {}
        price = _num(declared.get("monthly_usd"))
        if price is None:
            price = _num(sub.get("monthly_usd"))
            # A Starter tier reported by Google is not this login's bill.
            if provider == "agy" and price == 0 and sub.get("source") != "configured":
                price = None
        if price is not None and acct["monthly"] is None:
            acct["monthly"] = price
            acct["plan"] = " ".join(str(x) for x in (declared.get("plan"), sub.get("plan"), sub.get("label")) if x)
        acct["window_hours"] = acct["window_hours"] or _num(sub.get("window_hours"))
        series = panel.get("series")
        for s in series if isinstance(series, list) else []:
            if isinstance(s, dict) and s.get("focus") and not _is_session(s):
                acct["focus"].append(s)
    providers: dict[str, int] = {}
    for provider, _ in out:
        providers[provider] = providers.get(provider, 0) + 1
    for (provider, _), acct in out.items():
        acct["name"] = app_name(provider) + (f" {acct['label']}" if providers[provider] > 1 else "")
        last = [p[-1][0] for s in acct["focus"] if (p := _points(s))]
        last += [float(v) for panel in acct["panels"] if (v := _num(panel.get("sampled_at"))) is not None]
        acct["last"] = datetime.fromtimestamp(max(last), tz=timezone.utc) if last else None
    return out


def _value(series: dict) -> float:
    """USD of one full window of this series, this pool's share of the plan."""
    usd = _num(series.get("window_usd")) or 0.0
    share = _num(series.get("share"))
    return usd * (share if share is not None else 1.0)


# --- formatting ---------------------------------------------------------------------------------


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def _day(dt: datetime, tz: tzinfo) -> str:
    local = dt.astimezone(tz)
    return f"{local.day} {local:%b}"


def _when(dt: datetime, now: datetime, tz: tzinfo) -> str:
    """``Sun 04:00`` within a week, ``30 Nov`` beyond it."""
    local = dt.astimezone(tz)
    return f"{local:%a %H:%M}" if dt - now < timedelta(days=6) else _day(dt, tz)


def _items(pairs: list[tuple[str, float]], *, keep: int = 2) -> str:
    """``Grok $36 · Claude shared $28`` — the biggest; the total next to it covers the rest."""
    pairs = sorted((p for p in pairs if p[1] >= 0.5), key=lambda p: -p[1])
    return " · ".join(f"{name} {_usd(v)}" for name, v in pairs[:keep])


# --- the parts ----------------------------------------------------------------------------------


def _lost(accounts: dict, since: datetime, now: datetime) -> tuple[float, list[tuple[str, float]]]:
    """Unused value that became unspendable at a refresh (or an expired banked reset)."""
    pairs = []
    for acct in accounts.values():
        usd = 0.0
        for panel in acct["panels"]:
            for e in (panel.get("underutilised") or {}).get("events") or []:
                at, v = _ts(e.get("t")), _num(e.get("usd"))
                if at and v and v > 0 and since <= at <= now and e.get("kind") in {"quota_reset", "expired_reset"}:
                    usd += v
        if usd > 0:
            pairs.append((acct["name"], usd))
    return sum(v for _, v in pairs), pairs


def _waste(accounts: dict, now: datetime) -> tuple[float, list[tuple[str, float]]]:
    """30 days: lost value net of free bonus refills, per login, never below zero (prep's rule)."""
    pairs = []
    for acct in accounts.values():
        usd = 0.0
        for panel in acct["panels"]:
            for e in (panel.get("underutilised") or {}).get("events") or []:
                at, v = _ts(e.get("t")), _num(e.get("usd"))
                if at and v is not None and now - WASTE_WINDOW <= at <= now:
                    usd += v
        if usd > 0:
            pairs.append((acct["name"], usd))
    return sum(v for _, v in pairs), pairs


def _spare(acct: dict, now: datetime) -> tuple[float, datetime] | None:
    """Value left before a refresh inside the live alert's lead (same thresholds as alerts.py)."""
    usd, first = 0.0, None
    for s in acct["focus"]:
        pts, reset = _points(s), _ts(s.get("resets_at"))
        if not pts or reset is None or not _value(s):
            continue
        at, remaining = datetime.fromtimestamp(pts[-1][0], tz=timezone.utc), pts[-1][1]
        hours = (reset - now).total_seconds() / 3600.0
        if now - at > FRESH_READING or not 0 < hours <= SPARE_2D_HOURS:
            continue
        if remaining > (SPARE_QUARTER if hours <= SPARE_1D_HOURS else SPARE_HALF):
            usd += remaining / 100.0 * _value(s)
            first = min(first or reset, reset)
    return (usd, first) if first and usd >= MIN_USD else None


def _reset_expiries(acct: dict, now: datetime) -> list[datetime]:
    """Expiry of each banked full reset still available on this login."""
    seen, out = set(), []
    for panel in acct["panels"]:
        for item in (panel.get("reset_credits") or {}).get("items") or []:
            exp = _ts(item.get("expires_at"))
            key = item.get("credit_id") or (item.get("expires_at"), item.get("title"))
            if item.get("status") != "available" or exp is None or exp <= now or key in seen:
                continue
            seen.add(key)
            out.append(exp)
    return out


def _yesterday(accounts: dict, now: datetime, tz: tzinfo) -> tuple[float, float, list[tuple[str, float]]]:
    """(plan value used yesterday, plan cost per day, used per app) over priced logins read yesterday."""
    local = now.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    lo, hi = start.timestamp(), (start + timedelta(days=1)).timestamp()
    used_total = daily_total = 0.0
    by_app: dict[str, float] = {}
    for acct in accounts.values():
        priced = [s for s in acct["focus"] if _value(s)]
        if not priced or acct["last"] is None or acct["last"].timestamp() < lo:
            continue
        hours = acct["window_hours"] or DEFAULT_WINDOW_HOURS
        used, measured = 0.0, True
        for s in priced:
            pts = _points(s)
            for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
                overlap = min(t1, hi) - max(t0, lo)
                if y1 >= y0 or overlap <= 0:
                    continue  # no use, a refill, or outside the day
                if t1 - t0 > SPAN_GAP.total_seconds():
                    measured = False  # use somewhere in a long gap: cannot say how much was yesterday
                    break
                used += (y0 - y1) * overlap / (t1 - t0) / 100.0 * _value(s)
        if not measured:
            continue
        daily_total += sum(_value(s) for s in priced) * 24.0 / hours
        used_total += used
        app = app_name(acct["provider"])
        by_app[app] = by_app.get(app, 0.0) + used
    return used_total, daily_total, list(by_app.items())


def _grok_cli_cost(doc: dict, spend_rows: list[dict] | None, now: datetime, tz: tzinfo) -> float | None:
    """Grok CLI's own reported cost for yesterday (local day). Other CLIs report none."""
    day = (now.astimezone(tz) - timedelta(days=1)).date()
    if spend_rows is not None:
        total, n = 0.0, 0
        for row in spend_rows:
            if row.get("provider") != "grok" or (cost := _num(row.get("cost_usd"))) is None:
                continue
            at = _ts(row.get("ts"))
            if at and at.astimezone(tz).date() == day:
                total, n = total + cost, n + 1
        return total if n else None
    for panel in doc.get("panels") or []:
        if isinstance(panel, dict) and panel.get("provider") == "grok":
            for row in panel.get("spend") or []:
                if isinstance(row, dict) and row.get("date") == day.isoformat():
                    return _num(row.get("cost_usd"))
    return None


def _credit_due(acct: dict) -> float | None:
    """Monthly API credit this subscription includes (verified table above), or None."""
    plan, provider, monthly = normalize_plan(acct["plan"]), acct["provider"], acct["monthly"]
    if not plan or any(word in plan for word in POOLED_PLANS):
        return None
    if provider == "claude":
        return next((usd for word, usd in CLAUDE_CREDIT.items() if word in plan), None)
    if provider == "codex" and "pro" in plan and monthly in CODEX_PRO_CREDIT:
        return monthly
    return None


def _api(doc: dict, accounts: dict, now: datetime, tz: tzinfo) -> tuple[list[str], list[str]]:
    """(expiring within a week → visible, standing API items → fold) from recorded API observations."""
    api = doc.get("api_tracking") or {}
    if api.get("unavailable"):
        return [], []
    grants = [g for g in api.get("grants") or [] if isinstance(g, dict)]
    orgs = [o for o in api.get("organizations") or [] if isinstance(o, dict)]
    expiring, fold = [], []
    for row in grants + orgs:
        is_grant = row.get("kind") == "grant"
        left = _num(row.get("remaining_usd")) if is_grant else _num(row.get("amount_usd"))
        if is_grant and left is None and row.get("status") == "issued":
            left = _num(row.get("amount_usd"))
        exp = _ts(row.get("expires_at"))
        if left and left >= 1 and exp and now < exp <= now + EXPIRING_SOON:
            provider = str(row.get("provider") or "")
            api_name = API_NAME.get(provider, app_name(provider))
            expiring.append((exp, f"{api_name} API {_usd(left)} {_when(exp, now, tz)}"))
    balance = sum(v for o in orgs if (v := _num(o.get("amount_usd"))))
    if balance >= 1:
        fold.append(f"API balances {_usd(balance)}")
    recorded = {(g.get("provider"), g.get("account")) for g in grants}
    unclaimed = sum(due for key, acct in accounts.items()
                    if key not in recorded and (due := _credit_due(acct)))
    if unclaimed:
        fold.append(f"API credits to claim {_usd(unclaimed)}/mo")  # the dashboard's «Credits to claim» tab
    return [text for _, text in sorted(expiring)], fold


# --- assembly -----------------------------------------------------------------------------------


def _section(doc: dict | None, *, spend_rows: list[dict] | None, now: datetime, tz: tzinfo,
             since: datetime | None) -> dict:
    empty = {"visible": [], "fold": []}
    if not isinstance(doc, dict):
        return empty
    written = _ts(doc.get("written_at"))
    if written is None or now - written > STALE_DOC:
        return empty
    accounts = _accounts(doc)
    if not accounts:
        return empty
    warnings: list[str] = []

    # 1. Lost at a refresh since the last morning: a diff, so visible.
    lost, lost_pairs = _lost(accounts, _aware(since) if since else now - LOST_WINDOW, now)
    if lost >= MIN_USD:
        who = lost_pairs[0][0] if len(lost_pairs) == 1 else _items(lost_pairs)
        warnings.append(f"lost {_usd(lost)} unused at refresh · {who}")

    # 2. Spare value before a refresh on logins no live alert watches (alerts cover primaries).
    spare = sorted(((acct["name"], *hit) for acct in accounts.values()
                    if not acct["primary"] and (hit := _spare(acct, now))), key=lambda r: -r[1])
    if len(spare) == 1:
        name, usd, reset = spare[0]
        warnings.append(f"{name}: use {_usd(usd)} by {_when(reset, now, tz)}")
    elif spare:
        warnings.append("use before refresh: " + " · ".join(
            f"{name} {_usd(usd)} {reset.astimezone(tz):%a}" for name, usd, reset in spare[:3]))

    # 3. Expiring within a week: banked resets on non-primary logins (primaries get the
    #    live «banked reset expires» alert) and recorded API credits / balances.
    soon = []
    for acct in accounts.values():
        if acct["primary"]:
            continue
        due = [exp for exp in _reset_expiries(acct, now) if exp <= now + EXPIRING_SOON]
        if due:
            first, n = min(due), len(due)
            soon.append((first, f"{acct['name']} {n} reset{'s' if n > 1 else ''} {_when(first, now, tz)}"))
    api_soon, api_fold = _api(doc, accounts, now, tz)
    expiring = [text for _, text in sorted(soon)] + api_soon
    if expiring:
        warnings.append("expiring: " + " · ".join(expiring[:3]))

    # 4. Cost summary: yesterday's plan value used against what the plans cost per day.
    used, daily, by_app = _yesterday(accounts, now, tz)
    grok_cost = _grok_cli_cost(doc, spend_rows, now, tz)
    cost_line = f"yesterday: {_usd(used)} used of {_usd(daily)}/day paid" if daily >= 1 else ""
    visible = warnings[:MAX_VISIBLE]
    if cost_line and len(visible) < MAX_VISIBLE:
        visible.append(cost_line)

    fold: list[str] = []
    # Group A: yesterday by app, Grok's CLI cost nested under Grok.
    apps = sorted(((a, v) for a, v in by_app if v >= 0.5), key=lambda p: -p[1])[:3]
    cli = f"(CLI cost {_usd(grok_cost)})" if grok_cost and grok_cost >= 1 else ""
    parts = [f"{a} {_usd(v)}" + (f" {cli}" if a == "Grok" and cli else "") for a, v in apps]
    if cli and not any(a == "Grok" for a, _ in apps):
        parts = parts[:2] + [f"Grok {cli}"]
    if cost_line and cost_line not in visible:
        fold.append(cost_line + (": " + " · ".join(parts) if parts else ""))
    elif parts:
        fold.append("by app: " + " · ".join(parts))
    # Group B: 30 days of unused value against the monthly bill.
    waste, waste_pairs = _waste(accounts, now)
    paid = sum(a["monthly"] for a in accounts.values() if a["monthly"])
    if waste >= MIN_USD:
        bill = f" of {_usd(paid)}/mo" if paid else ""
        fold.append(f"unused in 30 days {_usd(waste)}{bill} · {_items(waste_pairs)}")
    # Group C: standing steps — API credits, banked resets, paid logins without readings.
    steps = list(api_fold)
    banked: dict[str, list[datetime]] = {}
    for acct in accounts.values():
        for exp in _reset_expiries(acct, now):
            banked.setdefault(app_name(acct["provider"]), []).append(exp)
    for app, exps in sorted(banked.items(), key=lambda kv: min(kv[1])):
        steps.append(f"{len(exps)} {app} reset{'s' if len(exps) > 1 else ''} banked, first expires "
                     f"{_day(min(exps), tz)}")
    unread = [a for a in accounts.values()
              if a["monthly"] and (a["last"] is None or now - a["last"] > UNREAD_PAID)]
    if unread:
        names = {app_name(a["provider"]) for a in unread}
        who = names.pop() if len(names) == 1 else "paid"
        latest = max((a["last"] for a in unread if a["last"]), default=None)
        since_txt = f" since {_day(latest, tz)}" if latest else ""
        cost = sum(a["monthly"] for a in unread)
        steps.append(f"{len(unread)} {who} login{'s' if len(unread) > 1 else ''} unread{since_txt} "
                     f"({_usd(cost)}/mo)")
    if steps:
        fold.append(" · ".join(steps[:3]))
    return {"visible": visible, "fold": fold}
