"""Remaining/burn + reset-soon alerts: items, dedupe, CLI dry-run. Offline."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_quotas.alerts import (
    apply_dedupe,
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
    projected: float | None = None,
) -> dict:
    return {
        "verdict": verdict,
        "used_percent": used,
        "hours_to_reset": hours_left,
        "window": window,
        "resets_at": resets_at or (NOW + timedelta(hours=hours_left)).isoformat(),
        "pace": pace,
        "projected_final": projected,
    }


def test_remaining_percent():
    assert remaining_percent(25.0) == 75.0
    assert remaining_percent(None) is None


def test_burn_item_from_warn():
    result = {
        "verdicts": {
            "claude": _verdict("claude", used=30.0, verdict="WARN", hours_left=80.0, projected=110.0),
        }
    }
    items = items_from_evaluate(result)
    kinds = {i["kind"] for i in items}
    assert "burn" in kinds
    burn = next(i for i in items if i["kind"] == "burn")
    assert burn["provider"] == "claude"
    assert burn["severity"] == "WARN"
    assert burn["remaining"] == 70.0


def test_reset_soon_high_remaining():
    result = {
        "verdicts": {
            "grok": _verdict("grok", used=20.0, verdict="OK", hours_left=12.0),
        }
    }
    items = items_from_evaluate(result)
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
    items = items_from_evaluate(result)
    assert items == []


def test_dedupe_sends_once_then_upgrades_stop():
    warn = {
        "kind": "burn",
        "provider": "claude",
        "window": "week",
        "fingerprint": "burn:claude:week:WARN",
    }
    stop = {
        "kind": "burn",
        "provider": "claude",
        "window": "week",
        "fingerprint": "burn:claude:week:STOP",
    }
    fresh, pruned, merged = apply_dedupe([warn], {"sent": {}}, now=NOW)
    assert len(fresh) == 1
    fresh2, _, _ = apply_dedupe([warn], merged, now=NOW)
    assert fresh2 == []
    fresh3, _, _ = apply_dedupe([stop], merged, now=NOW)
    assert len(fresh3) == 1
    assert fresh3[0]["fingerprint"].endswith("STOP")
    # ended condition clears
    fresh4, pruned4, _ = apply_dedupe([], merged, now=NOW)
    assert fresh4 == []
    assert pruned4["sent"] == {}


def test_format_message_contains_both_kinds():
    items = [
        {
            "kind": "burn",
            "provider": "claude",
            "window": "week",
            "severity": "WARN",
            "remaining": 70.0,
            "hours_to_reset": 80.0,
            "pace": "▲ 180% of quota pace",
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
    assert "remaining 70%" in msg


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
        now=NOW,
        sender=lambda text: sent.append(text) or "sent",
    )
    assert report2["new"] == 0
    assert sent == []


def test_heartbeat_due():
    assert heartbeat_due(0.0, 10.0, 5.0) is True
    assert heartbeat_due(8.0, 10.0, 5.0) is False
    assert heartbeat_due(1.0, 10.0, 5.0) is True
    assert heartbeat_due(0.0, 1.0, 0.0) is False


def test_ping_healthchecks_skip_empty():
    assert ping_healthchecks("") == "skip"


def test_cli_alert_dry_run(multi_path: Path, tmp_path: Path, monkeypatch):
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "AI_QUOTAS_SAMPLES": str(multi_path),
        "AI_QUOTAS_DATA_DIR": str(tmp_path),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "ai_quotas", "--samples", str(multi_path), "alert", "--dry-run"],
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
