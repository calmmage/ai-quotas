"""Collector: sample_all, append, adapter discovery, failure rows."""

from __future__ import annotations

import json
from pathlib import Path

from ai_quotas.collector import append_samples, discover_adapters, sample_all, sample_now


def test_discover_builtin_adapters():
    adapters = discover_adapters()
    for name in ("claude", "codex", "grok", "openrouter"):
        assert name in adapters
    assert "agy" not in adapters  # gate G3 — not public


def test_sample_all_never_raises_on_bad_adapter():
    def boom(_ts: str):
        raise RuntimeError("exploded")

    def ok(ts: str):
        return [
            {
                "ts": ts,
                "provider": "codex",
                "window": "week",
                "used_percent": 3.0,
                "resets_at": None,
                "plan": None,
                "status": "ok",
                "reason": None,
                "limit": None,
                "used": None,
            }
        ]

    rows = sample_all("2026-07-28T12:00:00+00:00", adapters={"claude": boom, "codex": ok})
    assert len(rows) == 2
    err = next(r for r in rows if r["provider"] == "claude")
    good = next(r for r in rows if r["provider"] == "codex")
    assert err["status"] == "error"
    assert err["used_percent"] is None
    assert "exploded" in (err.get("reason") or "")
    assert good["status"] == "ok"
    assert good["used_percent"] == 3.0


def test_sample_all_strips_fake_zero_on_failure():
    def bad(_ts: str):
        return [
            {
                "provider": "claude",
                "window": "unknown",
                "used_percent": 0,
                "status": "unavailable",
                "reason": "no creds",
            }
        ]

    rows = sample_all("2026-07-28T12:00:00+00:00", adapters={"claude": bad})
    assert len(rows) == 1
    assert rows[0]["status"] == "unavailable"
    assert rows[0]["used_percent"] is None


def test_append_does_not_truncate(tmp_samples: Path):
    r1 = [
        {
            "ts": "t1",
            "provider": "claude",
            "window": "5h",
            "used_percent": 1.0,
            "status": "ok",
            "resets_at": None,
            "plan": None,
            "reason": None,
            "limit": None,
            "used": None,
        }
    ]
    r2 = [
        {
            "ts": "t2",
            "provider": "codex",
            "window": "week",
            "used_percent": 2.0,
            "status": "ok",
            "resets_at": None,
            "plan": None,
            "reason": None,
            "limit": None,
            "used": None,
        }
    ]
    append_samples(r1, tmp_samples)
    append_samples(r2, tmp_samples)
    lines = tmp_samples.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["provider"] == "claude"
    assert json.loads(lines[1])["provider"] == "codex"


def test_sample_now_append(tmp_samples: Path):
    def ok(ts: str):
        return [
            {
                "ts": ts,
                "provider": "grok",
                "window": "month",
                "used_percent": 10.0,
                "status": "ok",
                "resets_at": None,
                "plan": None,
                "reason": None,
                "limit": None,
                "used": None,
            }
        ]

    rows = sample_now(
        path=tmp_samples,
        append=True,
        adapters={"grok": ok},
        ts="2026-07-28T12:00:00+00:00",
    )
    assert len(rows) == 1
    assert tmp_samples.read_text().strip()
    assert "grok" in tmp_samples.read_text()


def test_extra_adapters_dir(tmp_path: Path, monkeypatch):
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "fakevendor.py").write_text(
        """
def snapshot(ts):
    return [{
        "ts": ts,
        "provider": "fakevendor",
        "window": "day",
        "used_percent": 50.0,
        "resets_at": None,
        "plan": None,
        "status": "ok",
        "reason": None,
        "limit": None,
        "used": None,
    }]
""",
        encoding="utf-8",
    )
    adapters = discover_adapters(extra_dir=extra)
    assert "fakevendor" in adapters
    rows = adapters["fakevendor"]("2026-07-28T12:00:00+00:00")
    assert rows[0]["provider"] == "fakevendor"
