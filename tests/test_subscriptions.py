from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_quotas import subscriptions
from ai_quotas.reset_credits import credit_row, usable_credits, burn_relaxation
from ai_quotas.alerts import items_from_evaluate, run_alerts

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def test_codex_user_basis_includes_reset_allocation():
    config = {"codex": {"plan": "pro", "monthly_usd": 200, "regular_allocations": 4, "included_resets": 1}}
    plan = subscriptions.resolve("codex", "pro", config)
    assert subscriptions.allocation_value(plan, 168) == 40
    assert subscriptions.resolve("codex", "plus", config)["monthly_usd"] == 20
    assert subscriptions.resolve("codex", None, config)["monthly_usd"] is None


@pytest.mark.parametrize("provider,plan,expected", [
    ("codex", "pro", None), ("codex", "pro_100", 100), ("codex", "pro_200", 200),
    ("codex", "ChatGPT Plus", 20), ("codex", "enterprise", None),
    ("claude", "max+default_claude_max_20x", 200),
    ("claude", "max+default_claude_max_5x", 100), ("claude", "pro", 20),
    ("claude", "max", None), ("grok", None, None), ("agy", "Antigravity Starter Quota", None),
])
def test_exact_detected_tiers(provider, plan, expected):
    assert subscriptions.resolve(provider, plan, {})["monthly_usd"] == expected


def test_subscription_override_is_per_user_and_validated():
    path = subscriptions.configure("codex", "pro", 200, 4, 1)
    original = path.read_text()
    assert subscriptions.resolve("codex", "pro")["source"] == "configured"
    assert subscriptions.allocation_value(subscriptions.resolve("codex", "pro"), 168) == 40
    for price, count in ((-1, 4), (float("nan"), 4), (200, 0)):
        with pytest.raises(ValueError):
            subscriptions.configure("codex", "pro", price, count, 1)
        assert path.read_text() == original


def test_subscription_cli_saves_explicit_account_valuation():
    from ai_quotas.cli import main
    assert main(["subscription", "--provider", "codex", "--plan", "pro", "--monthly-usd", "200",
                 "--regular-allocations", "4", "--included-resets", "1"]) == 0
    assert subscriptions.allocation_value(subscriptions.resolve("codex", "pro"), 168) == 40


def test_home_env_configuration_preserves_unrelated_settings(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("AI_QUOTAS_SUBSCRIPTIONS")
    dotenv = tmp_path / ".env"
    original = "# existing settings\nUNRELATED_VALUE='keep exactly this'\n"
    dotenv.write_text(original)
    subscriptions.save_dotenv({"codex": {"plan": "pro", "monthly_usd": 200, "regular_allocations": 4, "included_resets": 1}})
    assert subscriptions.allocation_value(subscriptions.resolve("codex", "pro"), 168) == 40
    assert dotenv.read_text().startswith(original)
    assert subscriptions.configure("codex", "pro", 100, 4, 1) == dotenv
    assert subscriptions.allocation_value(subscriptions.resolve("codex", "pro"), 168) == 20
    assert dotenv.read_text().count(subscriptions.ENV_JSON + "=") == 1


def credits(count=1, *, days=20, status="available", age=0, scope="week", provider="codex"):
    return [credit_row((NOW - timedelta(hours=age)).isoformat(), provider, credit_id=f"credit-{i}",
                       expires_at=(NOW + timedelta(days=days)).isoformat(), status=status, scope=scope)
            for i in range(count)]


def verdict(used=90):
    return {"window": "week", "window_hours": 168, "used_percent": used, "hours_to_reset": 100,
            "pace_pct": 600, "verdict": "STOP", "resets_at": (NOW + timedelta(hours=100)).isoformat()}


@pytest.mark.parametrize("rows,relaxed", [
    (credits(2), True), (credits(1, days=7), True), (credits(1, days=1), True),
    (credits(1, days=7.01), False), (credits(2, days=0), False), (credits(2, days=-1), False),
    (credits(2, status="error"), False), (credits(2, status="none"), False),
    (credits(2, age=3), False), (credits(2, scope="5h"), False),
    (credits(2, provider="grok"), False), (credits(2, scope=None), False),
    (credits(2, scope="7d"), True),
])
def test_credit_relaxation_gates(rows, relaxed):
    items = items_from_evaluate({"verdicts": {"codex": verdict()}}, credit_rows=rows, now=NOW)
    assert (items == []) is relaxed


def test_latest_probe_error_or_none_prevents_using_old_credit_cache():
    for status in ("none", "error", "unavailable"):
        rows = credits(2, age=1) + credits(1, status=status)
        assert usable_credits(rows, "codex", "week", now=NOW) == []


def test_exhaustion_still_notifies_and_mentions_redeemable_resets():
    items = items_from_evaluate({"verdicts": {"codex": verdict(100)}}, credit_rows=credits(2), now=NOW)
    assert items[0]["severity"] == "STOP"
    assert items[0]["resets_available"] == 2


def test_run_alerts_reads_credit_sibling_before_deciding(tmp_path, monkeypatch):
    samples = tmp_path / "quota.jsonl"
    samples.write_text("")
    samples.with_name("quota.reset-credits.jsonl").write_text("\n".join(json.dumps(r) for r in credits(2)))
    monkeypatch.setattr("ai_quotas.core.evaluate", lambda *a, **k: {"verdicts": {"codex": verdict()}})
    report = run_alerts(path=samples, state_file=tmp_path / "state.json", now=NOW, dry_run=True)
    assert report["firing"] == 0


def test_underutilisation_counts_partial_reset_and_expiry_once():
    pytest.importorskip("pandas")
    from ai_quotas.plots.prep import ResetEvent, CreditEvent, underutilised_events
    # $200 / 5 allocations = $40. Reset used with 80% left wastes $32 of
    # potential allocation; another fully expired credit wastes $40.
    reset = ResetEvent("Codex week", "Codex", NOW, 20, 0, 80, 100, None, "", "free", 8, 40, 168, "+$8")
    used = CreditEvent("Codex", "codex", "used", "", "consumed", None, NOW + timedelta(days=5), NOW, 40, 8, "", 20)
    expired = CreditEvent("Codex", "codex", "expired", "", "expired", None, NOW + timedelta(days=1), NOW + timedelta(days=1), 40, -40, "", None)
    available = CreditEvent("Codex", "codex", "available", "", "available", None, NOW + timedelta(days=3), None, 40, 0, "", None)
    events = underutilised_events([reset], [used, expired, available], "Codex", "Codex week")
    assert len(events) == 2
    assert sum(e["usd"] for e in events) == 72
    assert [e["usd"] for e in events] == [32, 40]


def test_historical_plan_changes_price_the_correct_reset(tmp_path):
    pytest.importorskip("pandas")
    from ai_quotas.plots.prep import prepare
    rows = []
    for day, hour, used, plan in [(1, 10, 50, "plus"), (1, 11, 0, "pro_200"), (2, 10, 80, "pro_200"), (2, 11, 0, "pro_200")]:
        rows.append({"provider": "codex", "window": "week", "used_percent": used, "plan": plan,
                     "ts": NOW.replace(day=day, hour=hour).isoformat(), "status": "ok"})
    path = tmp_path / "quota.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    df, resets, _ = prepare(path)
    assert resets[0].window_usd == pytest.approx(20 / (30 / 7))
    assert resets[1].window_usd == pytest.approx(200 / (30 / 7))
    assert "plan" in df.columns


def test_extra_bonus_refill_offsets_underutilisation():
    pytest.importorskip("pandas")
    from ai_quotas.plots.prep import ResetEvent, underutilised_events
    paid = ResetEvent("Codex week", "Codex", NOW, 50, 0, 50, 100, None, "", "burn", -20, 40, 168, "")
    # Partial refill restores 20 points, not a whole allocation: $8 bonus.
    bonus = ResetEvent("Codex week", "Codex", NOW + timedelta(days=1), 30, 10, 70, 90, None, "", "free", 8, 40, 168, "")
    events = underutilised_events([paid, bonus], [], "Codex", "Codex week")
    assert [e["usd"] for e in events] == [20, -8]
    assert sum(e["usd"] for e in events) == 12
