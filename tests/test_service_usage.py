from datetime import datetime, timezone
from urllib.error import HTTPError

from ai_quotas.adapters import service_usage
from ai_quotas.service_dashboard import summarize_usage


def test_task_history_denied_preserves_balance(monkeypatch):
    def denied(metrics, daily, start, end):
        metrics["Credits available"] = 450
        raise HTTPError("", 403, "restricted", {}, None)

    monkeypatch.setattr(service_usage, "READERS", {"meshy": denied})
    row, = service_usage.observe(datetime.now(timezone.utc).isoformat())
    result = summarize_usage([row])["meshy"]
    assert result["status"] == "partial"
    assert result["metrics"]["Credits available"] == 450
    assert list(result["balance_history"].values()) == [450]
    assert "daily" not in result


def test_spending_excludes_grants_and_handles_quiet_days(monkeypatch):
    stamp = "2026-11-24T20:00:00Z"
    def cli(*args):
        if args[1] == "status":
            return {"credits": 0}
        return {"items": [
            {"action": action, "created_at": "2026-11-24T10:00:00Z", "credits": value}
            for action, value in [("grant", 100), ("spend", -2)]
        ]}
    monkeypatch.setattr(service_usage, "higgs", cli)
    monkeypatch.setattr(service_usage, "READERS", {"higgsfield": service_usage.higgsfield})
    row, = service_usage.observe(stamp)
    assert row["metrics"]["Credits available"] == 0
    assert len(row["daily"]) == 7
    assert sum(row["daily"].values()) == row["daily"]["2026-11-24"] == 2


def test_activity_uses_native_days_without_adding_repeat_samples_or_gaps():
    from datetime import date
    from ai_quotas.service_activity import observed_activity

    def row(hour, balance):
        return {"provider": "meshy", "window": "service_usage", "ts": f"2026-11-24T{hour}:00Z",
                "metrics": {"Credits available": balance}}

    rows = [row("00:05", 450), row("00:10", 440), row("09:10", 300), row("09:15", 350)]
    assert observed_activity(rows[::-1], date(2026, 11, 24))["meshy"] == {"2026-11-24": 10}
    rows.append(dict(row("09:20", 350), daily={"2026-11-23": 0, "2026-11-24": 12}))
    assert observed_activity(rows, date(2026, 11, 24))["meshy"] == {"2026-11-23": 0, "2026-11-24": 12}


def test_openrouter_initial_lifetime_usage_is_not_daily_spend():
    from datetime import date
    from ai_quotas.service_activity import observed_activity
    from ai_quotas.adapters.openrouter import _rows_from_payload
    day = date(2026, 11, 24)
    rows = _rows_from_payload("2026-11-24T12:00:00Z", {"data": {"usage": 9.5}})
    assert observed_activity(rows, day)["openrouter"] == {}
    rows += _rows_from_payload("2026-11-24T12:02:00Z", {"data": {"usage": 9.5, "usage_daily": 0.25}})
    assert observed_activity(rows, day)["openrouter"] == {"2026-11-24": 0.25}
