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
