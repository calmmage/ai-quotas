"""Orca extras: Kimi / MiniMax / OpenCode / Antigravity from account list JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ai_quotas.adapters import orca as orca_ad


def test_orca_snapshot_reads_ok_weekly(monkeypatch):
    payload = {
        "result": {
            "rateLimits": {
                "kimi": {
                    "status": "ok",
                    "weekly": {
                        "usedPercent": 22,
                        "windowMinutes": 10080,
                        "resetsAt": 1789611820767,
                    },
                },
                "minimax": {"status": "unavailable", "error": "cookie missing"},
                "opencodeGo": {
                    "status": "ok",
                    "weekly": {"usedPercent": 10, "windowMinutes": 10080, "resetsAt": 1},
                },
                "claude": {
                    "status": "ok",
                    "weekly": {"usedPercent": 5, "windowMinutes": 10080},
                },
            }
        }
    }
    monkeypatch.setattr(orca_ad.shutil, "which", lambda _: "/usr/local/bin/orca")
    monkeypatch.setattr(
        orca_ad.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""})(),
    )
    rows = orca_ad.snapshot("2026-09-16T12:00:00+00:00")
    by = {r["provider"]: r for r in rows}
    assert "claude" not in by
    assert by["kimi"]["status"] == "ok"
    assert by["kimi"]["used_percent"] == 22
    assert by["kimi"]["window"] == "week"
    assert by["kimi"]["resets_at"]
    assert "minimax" not in by
    assert by["opencode"]["used_percent"] == 10


def test_orca_missing_cli_is_silent(monkeypatch):
    monkeypatch.setattr(orca_ad.shutil, "which", lambda _: None)
    assert orca_ad.snapshot(datetime.now(timezone.utc).isoformat()) == []
