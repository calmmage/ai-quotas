"""Remaining/burn + reset-soon alerts: items, dedupe, CLI dry-run. Offline."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_quotas import core
from ai_quotas.alerts import (
    apply_dedupe,
    burn_severity,
    format_message,
    items_from_evaluate,
    remaining_percent,
    run_alerts,
)
from ai_quotas.notify import heartbeat_due, ping_healthchecks


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _verdict(
    provider: str,
    *,
    window: str = "week",
    used: float = 20.0,
    verdict: str = "OK",
    hours_left: float = 72.0,
    resets_at: str | None = None,
    pace: str = "—",
    pace_pct: float | None = None,
    projected: float | None = None,
) -> dict:
    return {
        "verdict": verdict,
        "used_percent": used,
        "hours_to_reset": hours_left,
        "window_hours": core.window_hours(window, NOW),
        "window": window,
        "resets_at": resets_at or (NOW + timedelta(hours=hours_left)).isoformat(),
        "pace": pace,
        "pace_pct": pace_pct,
        "projected_final": projected,
    }


def test_remaining_percent():
    assert remaining_percent(25.0) == 75.0
    assert remaining_percent(None) is None


def test_burn_severity_is_independent_of_dashboard_verdict():
    result = {
        "verdicts": {
            "claude": _verdict("claude", used=80.0, verdict="STOP", hours_left=80.0, pace_pct=300.0),
        }
    }
    items = items_from_evaluate(result)
    kinds = {i["kind"] for i in items}
    assert "burn" in kinds
    burn = next(i for i in items if i["kind"] == "burn")
    assert burn["provider"] == "claude"
    assert burn["severity"] == "WARN"
    assert burn["remaining"] == 20.0


@pytest.mark.parametrize("used,hours_left,pace", [
    (21.0, 146.0, 148.0),
    (21.0, 141.0, 77.0),
    (27.0, 139.0, 91.0),
    (21.0, 81.0, 115.0),
])
def test_reported_spam_is_silent(used, hours_left, pace):
    row = _verdict("codex", used=used, hours_left=hours_left, pace_pct=pace, verdict="STOP")
    assert items_from_evaluate({"verdicts": {"codex": row}}) == []


@pytest.mark.parametrize("used,hours_left,pace,expected", [
    (49.0, 168.0, 1000.0, None),  # plenty of quota, even at extreme burn
    (50.0, 168.0, 300.0, "WARN"),
    (49.0, 200.0, 1000.0, None),  # reserve threshold is capped at 50%
    (80.0, 80.0, 299.9, None),
    (80.0, 80.0, 300.0, "WARN"),
    (80.0, 80.0, 499.9, "WARN"),
    (80.0, 80.0, 500.0, "STOP"),
    (74.9, 84.0, 1000.0, None),  # half a week left: 25% remaining gate
    (75.0, 84.0, 300.0, "WARN"),
    (92.85, 24.0, 1000.0, None),  # one day left: gate is 1/14 of quota
    (92.86, 24.0, 300.0, "WARN"),
    (100.0, 24.0, None, "STOP"),  # exhausted, even without measurable burn
    (100.0, 24.0, 0.0, "STOP"),
    (100.0, 0.0, 500.0, None),  # already reset
    (99.0, -1.0, 500.0, None),
    (80.0, 80.0, None, None),
    (80.0, 80.0, float("nan"), None),
    (80.0, 80.0, float("inf"), None),
])
def test_burn_gate_and_thresholds(used, hours_left, pace, expected):
    assert burn_severity(_verdict("codex", used=used, hours_left=hours_left, pace_pct=pace)) == expected


@pytest.mark.parametrize("key,value", [
    ("used_percent", None), ("used_percent", float("nan")),
    ("hours_to_reset", None), ("hours_to_reset", float("inf")),
    ("window_hours", None), ("window_hours", 0),
    ("resets_at", None), ("resets_at", "invalid"),
])
def test_burn_missing_or_invalid_metrics_are_silent(key, value):
    row = _verdict("codex", used=95.0, hours_left=80.0, pace_pct=500.0)
    row[key] = value
    assert burn_severity(row) is None


@pytest.mark.parametrize("window,used,hours_left,expected", [
    ("month", 98.0, 24.0, None),  # September: 1/60 remaining threshold
    ("month", 99.0, 24.0, "WARN"),
    ("7d", 93.0, 24.0, "WARN"),
    ("5h", 99.0, 4.0, None),
    ("overage_credits", 99.0, 24.0, None),
    ("unknown", 99.0, 24.0, None),
])
def test_burn_uses_window_length_and_skips_non_primary(window, used, hours_left, expected):
    row = _verdict("codex", window=window, used=used, hours_left=hours_left, pace_pct=300.0)
    assert burn_severity(row) == expected


def test_reset_soon_high_remaining():
    result = {
        "verdicts": {
            "grok": _verdict("grok", used=20.0, verdict="OK", hours_left=12.0),
        }
    }
    assert items_from_evaluate(result) == []
    items = items_from_evaluate(result, include_reset_soon=True)
    assert len(items) == 1
    assert items[0]["kind"] == "reset_soon"
    assert items[0]["remaining"] == 80.0


def test_reset_soon_skips_5h_and_low_remaining():
    result = {
        "verdicts": {
            "claude": _verdict("claude", window="5h", used=10.0, verdict="OK", hours_left=2.0),
            "codex": _verdict("codex", used=90.0, verdict="OK", hours_left=10.0),
        }
    }
    items = items_from_evaluate(result, include_reset_soon=True)
    assert items == []


def test_dedupe_sends_once_then_upgrades_stop():
    reset = (NOW + timedelta(hours=80)).isoformat()
    warn = {
        "kind": "burn",
        "provider": "claude",
        "window": "week",
        "severity": "WARN",
        "resets_at": reset,
        "fingerprint": f"burn:claude:week:{reset}",
    }
    stop = {**warn, "severity": "STOP"}
    fresh, pruned, merged = apply_dedupe([warn], {"sent": {}}, now=NOW)
    assert len(fresh) == 1
    fresh2, _, _ = apply_dedupe([warn], merged, now=NOW)
    assert fresh2 == []
    fresh3, _, stopped = apply_dedupe([stop], merged, now=NOW)
    assert len(fresh3) == 1
    assert fresh3[0]["severity"] == "STOP"
    # Quiet runs and downgrades retain the highest delivered severity.
    fresh4, pruned4, _ = apply_dedupe([], stopped, now=NOW)
    assert fresh4 == []
    assert pruned4 == stopped
    for item in (warn, stop):
        assert apply_dedupe([item], pruned4, now=NOW)[0] == []
    # A new quota period is eligible even if the condition never cleared.
    reset2 = (NOW + timedelta(hours=248)).isoformat()
    next_warn = {**warn, "resets_at": reset2, "fingerprint": f"burn:claude:week:{reset2}"}
    fresh5, pruned5, _ = apply_dedupe([next_warn], stopped, now=NOW + timedelta(hours=81))
    assert fresh5 == [next_warn]
    assert pruned5["sent"] == {}


def test_dedupe_warn_does_not_rearm_after_quiet_run():
    row = _verdict("codex", used=80.0, hours_left=80.0, pace_pct=300.0)
    items = items_from_evaluate({"verdicts": {"codex": row}})
    _, _, state = apply_dedupe(items, {"sent": {}}, now=NOW)
    _, quiet, _ = apply_dedupe([], state, now=NOW + timedelta(hours=1))
    assert apply_dedupe(items, quiet, now=NOW + timedelta(hours=2))[0] == []


def test_legacy_state_is_pruned_and_reset_offsets_are_normalized():
    row = _verdict("codex", used=80.0, hours_left=80.0, pace_pct=300.0)
    items = items_from_evaluate({"verdicts": {"codex": row}})
    row["resets_at"] = datetime.fromisoformat(row["resets_at"]).astimezone(timezone(timedelta(hours=3))).isoformat()
    assert items_from_evaluate({"verdicts": {"codex": row}}) == items
    legacy = {"sent": {"burn:codex:week:STOP": {"ts": NOW.isoformat(), "kind": "burn"}}}
    fresh, pruned, _ = apply_dedupe(items, legacy, now=NOW)
    assert fresh == items
    assert pruned["sent"] == {}


def test_format_message_contains_both_kinds():
    items = [
        {
            "kind": "burn",
            "provider": "claude",
            "window": "week",
            "severity": "WARN",
            "remaining": 20.0,
            "hours_to_reset": 80.0,
            "pace": "🔴 300% of quota pace",
            "projected_final": 110.0,
        },
        {
            "kind": "reset_soon",
            "provider": "grok",
            "window": "week",
            "remaining": 64.0,
            "hours_to_reset": 20.0,
        },
    ]
    msg = format_message(items)
    assert "BURN  claude week  WARN" in msg
    assert "RESET SOON  grok week" in msg
    assert "remaining 20%" in msg


def test_run_alerts_dry_run_does_not_persist(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AI_QUOTAS_DATA_DIR", str(tmp_path))
    samples = [
        {
            "ts": NOW.isoformat(),
            "provider": "claude",
            "window": "week",
            "used_percent": 20.0,
            "resets_at": (NOW + timedelta(hours=10)).isoformat(),
            "status": "ok",
            "plan": None,
            "reason": None,
            "limit": None,
            "used": None,
        }
    ]
    monkeypatch.setattr("ai_quotas.core.load_samples", lambda *a, **k: samples)
    sent: list[str] = []
    state = tmp_path / "alert-state.json"
    report = run_alerts(
        path=tmp_path / "unused.jsonl",
        state_file=state,
        send=True,
        dry_run=True,
        include_reset_soon=True,
        now=NOW,
        sender=lambda text: sent.append(text) or "sent",
    )
    assert sent == []
    assert report["new"] >= 1
    assert report["delivery"] == "dry-run"
    stored = json.loads(state.read_text()) if state.exists() else {"sent": {}}
    assert stored.get("sent") == {}


def test_run_alerts_sender_persists(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AI_QUOTAS_DATA_DIR", str(tmp_path))
    result_samples = [
        {
            "ts": NOW.isoformat(),
            "provider": "claude",
            "window": "week",
            "used_percent": 20.0,
            "resets_at": (NOW + timedelta(hours=10)).isoformat(),
            "status": "ok",
            "plan": None,
            "reason": None,
            "limit": None,
            "used": None,
        }
    ]
    monkeypatch.setattr("ai_quotas.core.load_samples", lambda *a, **k: result_samples)
    sent: list[str] = []
    state = tmp_path / "alert-state.json"
    report = run_alerts(
        path=tmp_path / "unused.jsonl",
        state_file=state,
        send=True,
        include_reset_soon=True,
        now=NOW,
        sender=lambda text: sent.append(text) or "sent",
    )
    assert report["new"] >= 1
    assert sent and "RESET SOON" in sent[0]
    stored = json.loads(state.read_text())
    assert stored["sent"]
    sent.clear()
    report2 = run_alerts(
        path=tmp_path / "unused.jsonl",
        state_file=state,
        send=True,
        include_reset_soon=True,
        now=NOW,
        sender=lambda text: sent.append(text) or "sent",
    )
    assert report2["new"] == 0
    assert sent == []


@pytest.mark.parametrize("mode", [{"dry_run": True}, {"send": False}])
def test_preview_never_changes_existing_state(tmp_path: Path, monkeypatch, mode):
    monkeypatch.setattr("ai_quotas.core.load_samples", lambda *a, **k: [])
    state = tmp_path / "alert-state.json"
    original = '{"sent":{"old":{"kind":"burn","ts":"old"}}}\n'
    state.write_text(original)
    run_alerts(path=tmp_path / "unused.jsonl", state_file=state, now=NOW, **mode)
    assert state.read_text() == original


def test_burn_delivery_retries_and_persists_escalation(tmp_path: Path, monkeypatch):
    reset = NOW + timedelta(hours=80)
    samples = [
        {"ts": (NOW - timedelta(hours=1)).isoformat(), "provider": "claude", "window": "week",
         "used_percent": 78.0, "resets_at": reset.isoformat(), "status": "ok"},
        {"ts": NOW.isoformat(), "provider": "claude", "window": "week",
         "used_percent": 80.0, "resets_at": reset.isoformat(), "status": "ok"},
    ]
    monkeypatch.setattr("ai_quotas.core.load_samples", lambda *a, **k: samples)
    state = tmp_path / "alert-state.json"
    deliveries = []

    def run(status="sent"):
        return run_alerts(
            path=tmp_path / "unused.jsonl", state_file=state, now=NOW,
            sender=lambda msg: deliveries.append(msg) or status,
        )

    # Actual sample math: 2%/h = 336% of weekly pace, remaining 20%.
    assert run("error:offline")["delivery"] == "error:offline"
    assert json.loads(state.read_text())["sent"] == {}
    assert run()["items"][0]["severity"] == "WARN"
    assert run()["new"] == 0
    # Missing provider and a quiet reading do not forget the sent warning.
    current = list(samples)
    samples.clear()
    assert run()["new"] == 0
    samples.extend(current)
    samples[0]["used_percent"] = 80.0
    assert run()["new"] == 0
    samples[0]["used_percent"] = 78.0
    assert run()["new"] == 0
    # 3%/h = 504%; a failed escalation retries without forgetting WARN.
    samples[0]["used_percent"] = 77.0
    assert run("error:offline")["items"][0]["severity"] == "STOP"
    assert next(iter(json.loads(state.read_text())["sent"].values()))["severity"] == "WARN"
    assert run()["items"][0]["severity"] == "STOP"
    samples[0]["used_percent"] = 78.0
    assert run()["new"] == 0
    samples[0]["used_percent"] = 77.0
    assert run()["new"] == 0
    assert len(deliveries) == 4  # failed WARN, WARN, failed STOP, STOP


def test_heartbeat_due():
    assert heartbeat_due(0.0, 10.0, 5.0) is True
    assert heartbeat_due(8.0, 10.0, 5.0) is False
    assert heartbeat_due(1.0, 10.0, 5.0) is True
    assert heartbeat_due(0.0, 1.0, 0.0) is False


def test_ping_healthchecks_skip_empty():
    assert ping_healthchecks("") == "skip"


@pytest.mark.parametrize("extra", [[], ["--reset-soon"]])
def test_cli_alert_dry_run(multi_path: Path, tmp_path: Path, monkeypatch, extra):
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "AI_QUOTAS_SAMPLES": str(multi_path),
        "AI_QUOTAS_DATA_DIR": str(tmp_path),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "ai_quotas", "--samples", str(multi_path), "alert", "--dry-run", *extra],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "alerts firing=" in proc.stdout
    assert "delivery=" in proc.stdout
