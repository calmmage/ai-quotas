"""Morning money lines (Petr, 13 Nov 2026: «a meaningful API cost summary and maybe
some API alerts (unused quota, etc.)»). Offline: a hand-built panels.json document.

Visible = diffs and deadlines only, three lines at most; the rest folds; an unknown
price, balance or claim is left out («IF THERE IS NO ETA - DON'T WRITE ETA UNKNOWN!!!»).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ai_quotas.morning_money import money_section

TZ = timezone(timedelta(hours=3))  # Moscow, no DST
NOW = datetime(2026, 11, 13, 5, 5, tzinfo=timezone.utc)  # Fri 08:05 local
H = 3600


def ep(hours_from_now: float) -> int:
    return int((NOW + timedelta(hours=hours_from_now)).timestamp())


def series(points, *, usd, reset_in=None, window="week", share=None, focus=True):
    """points: [(hours_from_now, remaining_pct), ...]."""
    s = {"label": window, "window": window, "focus": focus, "window_usd": usd,
         "t": [ep(h) for h, _ in points], "y": [y for _, y in points],
         "resets_at": ep(reset_in) if reset_in is not None else None}
    if share is not None:
        s["share"] = share
    return s


def panel(provider, login, *, primary=False, label="personal", monthly=None, plan="", series_=(),
          events=(), credits=(), sampled=-0.1, spend=None, declared=None):
    p = {"provider": provider, "account": None if primary else login, "source": {"account": login},
         "account_label": label, "sampled_at": ep(sampled),
         "subscription": {"plan": plan, "label": plan, "monthly_usd": monthly, "window_hours": 168,
                          "source": "configured" if monthly is not None else "unknown"},
         "declared_subscription": declared, "series": list(series_),
         "underutilised": {"events": [{"t": ep(h), "kind": k, "usd": v} for h, k, v in events]},
         "reset_credits": {"items": [{"credit_id": f"c{i}", "status": st, "expires_at":
                                      (NOW + timedelta(hours=h)).isoformat()} for i, (h, st) in enumerate(credits)]},
         "spend": spend or []}
    return p


def doc(*panels, api=None, written=-0.1):
    return {"written_at": (NOW + timedelta(hours=written)).isoformat(), "panels": list(panels),
            "api_tracking": api or {"accounts": [], "organizations": [], "grants": [], "unavailable": False}}


def run(d, spend_rows=None, **kw):
    return money_section(d, spend_rows=spend_rows, now=NOW, tz=TZ, **kw)


FLAT_DAY = [(-30, 80.0), (-20, 80.0), (-10, 80.0), (-1, 80.0)]


def quiet_claude(**kw):
    return panel("claude", "me@x.com", primary=True, monthly=200, plan="max_20x",
                 series_=[series(FLAT_DAY, usd=46.67, reset_in=120)], **kw)


# --- never raise, never stale -------------------------------------------------------------------


def test_missing_stale_or_broken_doc_says_nothing():
    assert run(None) == {"visible": [], "fold": []}
    assert run(doc(quiet_claude(), written=-7)) == {"visible": [], "fold": []}
    assert run({"written_at": "garbage", "panels": "nope"}) == {"visible": [], "fold": []}
    assert run({"written_at": NOW.isoformat(), "panels": [{"provider": 3, "series": {"x": 1}}]}) == {
        "visible": [], "fold": []}


# --- 1. lost at a refresh: a diff, so visible ---------------------------------------------------


def test_value_lost_at_a_refresh_since_last_morning_is_visible():
    grok = panel("grok", "me@x.com", primary=True, monthly=300, plan="ultra",
                 series_=[series(FLAT_DAY, usd=70, reset_in=150)], events=[(-9, "quota_reset", 35.7)])
    out = run(doc(grok))
    assert out["visible"][0] == "lost $36 unused at refresh · Grok"


def test_a_loss_already_reported_yesterday_and_small_losses_stay_out_of_view():
    grok = panel("grok", "me@x.com", primary=True, monthly=300, plan="ultra",
                 series_=[series(FLAT_DAY, usd=70, reset_in=150)],
                 events=[(-30, "quota_reset", 35.7), (-2, "quota_reset", 1.9), (-3, "bonus_refill", -50)])
    assert not any(line.startswith("lost") for line in run(doc(grok))["visible"])


# --- 2. spare value before a refresh on logins no live alert watches -----------------------------


def shared_claude(remaining, reset_in, *, primary=False, usd=46.67, monthly=200):
    return panel("claude", "shared@x.com", primary=primary, label="shared", monthly=monthly, plan="max_20x",
                 series_=[series([(-20, remaining), (-0.5, remaining)], usd=usd, reset_in=reset_in)])


def test_spare_value_on_a_secondary_login_is_an_action_line():
    out = run(doc(quiet_claude(), shared_claude(60.0, 44)))
    assert "Claude shared: use $28 by Sun 04:05" in out["visible"]


def test_spare_follows_the_alert_thresholds_and_skips_primary_and_unpriced():
    def spare(d):
        return [v for v in run(d)["visible"] if ": use $" in v]

    assert spare(doc(quiet_claude(), shared_claude(40.0, 44))) == []  # 2 days out needs more than half
    assert spare(doc(quiet_claude(), shared_claude(30.0, 20))) == ["Claude shared: use $14 by Sat 04:05"]  # 1 day: >25%
    assert spare(doc(quiet_claude(), shared_claude(60.0, 60))) == []  # beyond 48h
    primary = panel("claude", "me@x.com", primary=True, monthly=200, plan="max_20x",
                    series_=[series([(-1, 90.0)], usd=46.67, reset_in=20)])
    assert spare(doc(primary)) == []  # the live alert owns it
    work = panel("claude", "work@x.com", label="work", plan="team",
                 series_=[series([(-1, 90.0)], usd=0.0, reset_in=20)])
    assert spare(doc(quiet_claude(), work)) == []  # no price, no money line


# --- 3. expiring within a week -------------------------------------------------------------------


def test_banked_resets_expiring_on_a_secondary_login_are_visible_primary_only_folded():
    flat = [series(FLAT_DAY, usd=23.33, reset_in=150)]
    shared = panel("codex", "shared@x.com", label="shared", monthly=100, plan="prolite", series_=flat,
                   credits=[(80, "available"), (90, "available"), (500, "available"), (10, "consumed")])
    mine = panel("codex", "me@x.com", primary=True, monthly=500, plan="promax",
                 series_=[series(FLAT_DAY, usd=116.67, reset_in=150)], credits=[(30, "available")])
    out = run(doc(mine, shared))
    assert "expiring: Codex shared 2 resets Mon 16:05" in out["visible"]
    assert not any("Codex personal" in v for v in out["visible"])
    assert "4 Codex resets banked, first expires 14 Nov" in out["fold"][-1]


def test_recorded_api_credits_expiring_soon_are_visible_and_claims_drop_from_the_to_do():
    grant = {"kind": "grant", "provider": "claude", "account": "me@x.com", "status": "issued",
             "amount_usd": 100.0, "remaining_usd": 60.0, "expires_at": (NOW + timedelta(days=2)).isoformat()}
    api = {"organizations": [], "grants": [grant], "unavailable": False}
    out = run(doc(quiet_claude(), api=api))
    assert "expiring: Claude API $60 Sun 08:05" in out["visible"]
    assert not any("to claim" in f for f in out["fold"])


# --- 4. cost summary ------------------------------------------------------------------------------


def test_yesterday_plan_value_used_against_the_daily_bill_and_grok_cli_cost():
    # Thursday (local) is -32h .. -8h. Codex drops 52 → 40 (12% of $116.67 = $14).
    codex = panel("codex", "me@x.com", primary=True, monthly=500, plan="promax",
                  series_=[series([(-34, 52.0), (-30, 52.0), (-20, 45.0), (-10, 40.0), (-1, 40.0)],
                                  usd=116.67, reset_in=100)])
    grok = panel("grok", "me@x.com", primary=True, monthly=300, plan="ultra",
                 series_=[series([(-31, 90.0), (-20, 86.0), (-1, 86.0)], usd=70, reset_in=100)])
    rows =[{"ts": (NOW - timedelta(hours=20)).isoformat(), "provider": "grok", "cost_usd": 50.0},
            {"ts": (NOW - timedelta(hours=15)).isoformat(), "provider": "grok", "cost_usd": 26.3},
            {"ts": (NOW - timedelta(hours=2)).isoformat(), "provider": "grok", "cost_usd": 99.0},  # today
            {"ts": (NOW - timedelta(hours=20)).isoformat(), "provider": "claude", "cost_usd": None}]
    out = run(doc(codex, grok), spend_rows=rows)
    assert out["visible"] == ["yesterday: $17 used of $27/day paid"]  # 14 + 2.8 of (116.67 + 70) / 7
    assert out["fold"][0] == "by app: Codex $14 · Grok $3 (CLI cost $76)"


def test_grok_cli_cost_falls_back_to_the_panel_spend_strip():
    grok = panel("grok", "me@x.com", primary=True, monthly=300, plan="ultra",
                 series_=[series([(-31, 90.0), (-20, 80.0), (-1, 80.0)], usd=70, reset_in=100)],
                 spend=[{"date": "2026-11-12", "tokens": 1, "cost_usd": 76.33}])
    assert run(doc(grok))["fold"][0] == "by app: Grok $7 (CLI cost $76)"


def test_use_hidden_in_a_long_gap_leaves_the_login_out_instead_of_a_fake_zero():
    grok = panel("grok", "me@x.com", primary=True, monthly=300, plan="ultra",
                 series_=[series([(-31, 90.0), (-12, 60.0), (-1, 60.0)], usd=70, reset_in=100)])
    assert run(doc(grok))["visible"] == []
    out = run(doc(grok, quiet_claude()))
    assert out["visible"] == ["yesterday: $0 used of $7/day paid"]  # Claude only, measured flat


def test_use_straddling_midnight_is_split_by_time():
    # -34h .. -30h: 10% over 4h, of which the 2h05m after local midnight (-32h05m) count.
    codex = panel("codex", "me@x.com", primary=True, monthly=500, plan="promax",
                  series_=[series([(-34, 60.0), (-30, 50.0), (-1, 50.0)], usd=116.67, reset_in=100)])
    assert run(doc(codex))["visible"] == ["yesterday: $6 used of $17/day paid"]  # 10% × 2.08/4 × $116.67


def test_three_warnings_push_the_cost_line_into_the_fold():
    shared = shared_claude(60.0, 44)
    shared["underutilised"]["events"] = [{"t": ep(-5), "kind": "quota_reset", "usd": 28.0}]
    shared["reset_credits"]["items"] = [{"credit_id": "a", "status": "available",
                                         "expires_at": (NOW + timedelta(days=3)).isoformat()}]
    out = run(doc(quiet_claude(), shared))
    assert out["visible"] == ["lost $28 unused at refresh · Claude shared",
                              "Claude shared: use $28 by Sun 04:05",
                              "expiring: Claude shared 1 reset Mon 08:05"]
    assert out["fold"][0] == "yesterday: $0 used of $13/day paid"


def test_a_login_not_read_yesterday_is_out_of_the_cost_and_a_paid_one_is_flagged():
    stale = panel("agy", "old@x.com", label="old", monthly=20, plan="google_ai_pro", sampled=-400,
                  series_=[series([(-400, 100.0)], usd=4.67, reset_in=-300, share=0.5)])
    free = panel("agy", "free@x.com", label="free", monthly=0, plan="starter", sampled=-400,
                 series_=[series([(-400, 100.0)], usd=0.0, reset_in=-300)])
    free["subscription"]["source"] = "detected list price"
    out = run(doc(quiet_claude(), stale, free))
    assert out["visible"] == ["yesterday: $0 used of $7/day paid"]
    assert "1 Antigravity login unread since 27 Oct ($20/mo)" in out["fold"][-1]


# --- folded standing facts ------------------------------------------------------------------------


def test_thirty_day_waste_and_verified_api_credit_amounts():
    claude_shared = panel("claude", "shared@x.com", label="shared", monthly=200, plan="max+default_claude_max_20x",
                          series_=[series(FLAT_DAY, usd=46.67, reset_in=150)],
                          events=[(-100, "quota_reset", 28.0), (-300, "quota_reset", 24.3), (-900, "quota_reset", 99)])
    codex = panel("codex", "me@x.com", primary=True, monthly=500, plan="promax",
                  series_=[series(FLAT_DAY, usd=116.67, reset_in=150)],
                  events=[(-50, "quota_reset", 20.0), (-60, "bonus_refill", -100.0)])
    codex_work = panel("codex", "work@x.com", label="work", plan="team",
                       series_=[series(FLAT_DAY, usd=0.0, reset_in=150)])
    plus = panel("codex", "plus@x.com", label="plus", monthly=20, plan="plus",
                 series_=[series(FLAT_DAY, usd=4.67, reset_in=150)])
    out = run(doc(quiet_claude(), claude_shared, codex, codex_work, plus))
    waste = next(f for f in out["fold"] if f.startswith("unused in 30 days"))
    assert waste == "unused in 30 days $52 of $920/mo · Claude shared $52"  # Codex net of its free refill: 0
    # Claude Max $100 each, Codex Pro $500 tier $500; Team and Plus are not eligible.
    assert "API credits to claim $700/mo" in out["fold"][-1]


def test_the_section_stays_short_grouped_and_free_of_empty_words():
    shared = shared_claude(60.0, 44)
    shared["underutilised"]["events"] = [{"t": ep(-5), "kind": "quota_reset", "usd": 28.0}]
    shared["reset_credits"]["items"] = [{"credit_id": "a", "status": "available",
                                         "expires_at": (NOW + timedelta(days=3)).isoformat()}]
    api = {"organizations": [{"kind": "balance", "provider": "codex", "organization": "org-1", "amount_usd": 42.0,
                              "expires_at": (NOW + timedelta(days=5)).isoformat()}],
           "grants": [], "unavailable": False}
    out = run(doc(quiet_claude(), shared, api=api))
    assert len(out["visible"]) <= 3 and len(out["fold"]) <= 3
    text = "\n".join(out["visible"] + out["fold"])
    assert len(text) < 550
    assert not re.search(r"unknown|N/A|\$0\b(?! used)|none|nan", text, re.I)
    for line in out["visible"] + out["fold"]:
        assert line.count(" · ") <= 2, line  # never more than three items at one level
    assert "<" not in text and "&" not in text  # plain text; the caller escapes
