"""Muse Code adapter: MSP usage/read → week + 5h rows."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_quotas.adapters import muse as muse_ad


def test_muse_snapshot_reads_week_and_5h(monkeypatch):
    payload = {
        "account": {"state": "accountLogin", "label": "alice@example.com", "credentialRequired": True},
        "usage": {
            "usage": {
                "observedAtMs": 1789590000000,
                "tier": "everyday",
                "weekly": {"usedPercent": 12, "resetsAtMs": 1789611820767},
                "window": {
                    "usedPercent": 40,
                    "resetsAtMs": 1789591800000,
                    "windowDurationMins": 300,
                },
            }
        },
    }
    monkeypatch.setattr(muse_ad, "_probe", lambda: payload)
    rows = muse_ad.snapshot("2026-09-16T21:00:00+00:00")
    by = {r["window"]: r for r in rows}
    assert by["week"]["status"] == "ok"
    assert by["week"]["used_percent"] == 12
    assert by["week"]["plan"] == "everyday"
    assert by["week"]["resets_at"]
    assert by["5h"]["used_percent"] == 40
    assert by["5h"]["provider"] == "muse"
    assert by["week"]["account"] == "alice@example.com"


def test_muse_logged_in_without_usage_is_zero(monkeypatch):
    monkeypatch.setattr(
        muse_ad,
        "_probe",
        lambda: {"account": {"state": "accountLogin", "label": "x@y.z"}, "usage": {}},
    )
    rows = muse_ad.snapshot("2026-09-16T21:00:00+00:00")
    by = {r["window"]: r for r in rows}
    assert by["week"]["status"] == "ok"
    assert by["week"]["used_percent"] == 0.0
    assert by["week"]["plan"] == "x@y.z"
    assert by["5h"]["used_percent"] == 0.0


def test_muse_logged_out_without_usage_is_silent(monkeypatch):
    monkeypatch.setattr(
        muse_ad,
        "_probe",
        lambda: {"account": {"state": "loggedOut"}, "usage": {}},
    )
    assert muse_ad.snapshot("2026-09-16T21:00:00+00:00") == []


def test_muse_missing_cli_is_silent(monkeypatch):
    monkeypatch.setattr(muse_ad.shutil, "which", lambda _: None)
    assert muse_ad.snapshot(datetime.now(timezone.utc).isoformat()) == []


def test_muse_probe_error_is_unavailable(monkeypatch):
    def boom():
        raise RuntimeError("serve failed")

    monkeypatch.setattr(muse_ad, "_probe", boom)
    rows = muse_ad.snapshot("2026-09-16T21:00:00+00:00")
    assert len(rows) == 1
    assert rows[0]["status"] == "unavailable"
    assert rows[0]["used_percent"] is None
    assert "serve failed" in (rows[0]["reason"] or "")
