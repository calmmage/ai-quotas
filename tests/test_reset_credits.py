"""Reset credits: lifecycle derivation, adapters' parsers, storage, CLI surface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_quotas import reset_credits as rc
from ai_quotas.adapters import codex, grok
from ai_quotas.adapters import claude as claude_adapter
from ai_quotas.collector import sample_all_split
from ai_quotas.storage import append_reset_credits, load_reset_credits, row_counts, schema_version

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _avail(dt: datetime, cid: str = "c1", exp: datetime | None = None) -> dict:
    return rc.credit_row(
        _ts(dt),
        "codex",
        credit_id=cid,
        title="Full reset",
        granted_at=_ts(dt - timedelta(days=10)),
        expires_at=_ts(exp or (dt + timedelta(days=10))),
    )


# ---------------------------------------------------------------- lifecycle


def test_available_state_and_remaining_total():
    rows = [_avail(NOW - timedelta(hours=2)), _avail(NOW - timedelta(hours=1))]
    states = rc.credit_states(rows, now=NOW)
    assert [s["status"] for s in states] == ["available"]
    summary = rc.summarize(rows, now=NOW)
    assert summary["codex"]["available"] == 1
    assert summary["codex"]["credits"][0]["expires_in_hours"] == pytest.approx(239.0, abs=0.1)
    assert rc.remaining_total(46.0, 1) == 154.0
    assert rc.remaining_total(None, 1) is None


def test_consumed_when_id_disappears_before_expiry():
    t0 = NOW - timedelta(hours=3)
    rows = [_avail(t0), _avail(t0 + timedelta(minutes=30)), rc.none_row(_ts(t0 + timedelta(hours=1)), "codex")]
    states = rc.credit_states(rows, now=NOW)
    assert states[0]["status"] == "consumed"
    assert states[0]["ended_at"] == _ts(t0 + timedelta(hours=1))
    assert rc.summarize(rows, now=NOW)["codex"]["consumed"] == 1


def test_expired_when_still_listed_past_expiry():
    exp = NOW - timedelta(hours=1)
    rows = [_avail(NOW - timedelta(days=1), exp=exp)]
    states = rc.credit_states(rows, now=NOW)
    assert states[0]["status"] == "expired"
    assert states[0]["ended_at"] == _ts(exp)


def test_outage_ticks_do_not_fake_redemption():
    t0 = NOW - timedelta(hours=3)
    rows = [
        _avail(t0),
        rc.error_row(_ts(t0 + timedelta(minutes=30)), "codex", "codexbar timed out"),
        rc.unavailable_row(_ts(t0 + timedelta(hours=1)), "codex", "offline"),
    ]
    states = rc.credit_states(rows, now=NOW)
    assert states[0]["status"] == "available"
    latest = rc.latest_probe(rows)["codex"]
    assert latest["status"] == "unavailable"


# ---------------------------------------------------------------- adapters


CODEXBAR_PAYLOAD = [
    {
        "source": "oauth",
        "provider": "codex",
        "usage": {
            "loginMethod": "pro",
            "secondary": {"usedPercent": 54, "windowMinutes": 10080, "resetsAt": "2026-09-07T02:29:10Z"},
            "codexResetCredits": {
                "credits": [
                    {
                        "id": "codex-reset-credit-v1-abc",
                        "title": "Full reset",
                        "reset_type": "codex_rate_limits",
                        "status": "available",
                        "granted_at": "2026-08-22T00:24:05Z",
                        "expires_at": "2026-09-21T00:24:05Z",
                    }
                ],
                "availableCount": 1,
            },
        },
    }
]


def test_codex_parses_codexbar_reset_credits():
    rows = codex.snapshot("2026-09-04T00:00:00+00:00", codexbar_json=json.dumps(CODEXBAR_PAYLOAD))
    quota = [r for r in rows if not rc.is_reset_credit_row(r)]
    credits = [r for r in rows if rc.is_reset_credit_row(r)]
    assert quota and quota[0]["window"] == "week"
    assert len(credits) == 1
    assert credits[0]["credit_id"] == "codex-reset-credit-v1-abc"
    assert credits[0]["expires_at"] == "2026-09-21T00:24:05+00:00"
    assert credits[0]["status"] == "available"


def test_codex_no_credits_is_none_not_unavailable():
    payload = json.loads(json.dumps(CODEXBAR_PAYLOAD))
    payload[0]["usage"]["codexResetCredits"]["credits"] = []
    rows = codex.snapshot("2026-09-04T00:00:00+00:00", codexbar_json=json.dumps(payload))
    credits = [r for r in rows if rc.is_reset_credit_row(r)]
    assert credits[0]["status"] == "none"


# Live grok.com GetRemainingResets body captured 04 Sep 2026 (one token).
GROK_BODY = bytes.fromhex(
    "00000000235221520d726573746f6b5f76705944716fa20106089c80f3d306f20106089cbd96d506"
    "800000000f677270632d7374617475733a300d0a"
)


def test_grok_grpc_web_decode():
    tokens, status = grok.parse_remaining_resets(GROK_BODY)
    assert status == "0"
    assert len(tokens) == 1
    assert tokens[0]["id"] == "restok_vpYDqo"
    assert tokens[0]["granted_at"] == "2026-08-12T18:49:00+00:00"
    assert tokens[0]["expires_at"] == "2026-09-12T18:49:00+00:00"


def test_grok_empty_response_means_none():
    tokens, status = grok.parse_remaining_resets(b"\x00\x00\x00\x00\x00" + b"\x80\x00\x00\x00\x0fgrpc-status:0\r\n")
    assert tokens == [] and status == "0"


def test_claude_marks_reset_credit_unavailable():
    row = claude_adapter._reset_credit_row("2026-09-04T00:00:00+00:00", {"limits": []})
    assert row["status"] == "unavailable" and "no rate-limit reset credit" in row["reason"]


# ---------------------------------------------------------------- collector + storage


def test_collector_splits_reset_rows_and_stores_them(tmp_path: Path):
    def fake(ts: str) -> list[dict]:
        return [
            {"provider": "fake", "window": "week", "used_percent": 10.0, "status": "ok"},
            rc.credit_row(ts, "fake", credit_id="x1", expires_at="2026-10-01T00:00:00+00:00"),
        ]

    quota, credits = sample_all_split("2026-09-04T00:00:00+00:00", adapters={"fake": fake})
    assert len(quota) == 1 and len(credits) == 1
    db = tmp_path / "q.sqlite3"
    append_reset_credits(db, credits)
    assert schema_version(db) == 2
    stored = load_reset_credits(db)
    assert stored[0]["credit_id"] == "x1"
    assert row_counts(db)["reset_credits"] == 1


def test_jsonl_fallback_uses_sibling_file(tmp_path: Path):
    samples = tmp_path / "samples.jsonl"
    samples.write_text("")
    append_reset_credits(samples, [rc.none_row("2026-09-04T00:00:00+00:00", "codex")])
    assert (tmp_path / "samples.reset-credits.jsonl").is_file()
    assert load_reset_credits(samples)[0]["status"] == "none"


def test_cli_json_exposes_reset_credits(tmp_path: Path):
    from ai_quotas.storage import append_samples

    db = tmp_path / "q.sqlite3"
    ts = _ts(NOW - timedelta(minutes=5))
    append_samples(
        db,
        [
            {"ts": ts, "provider": "codex", "window": "week", "used_percent": 40.0,
             "resets_at": _ts(NOW + timedelta(days=3)), "status": "ok", "reason": None},
            {"ts": ts, "provider": "codex", "window": "5h", "used_percent": 10.0,
             "resets_at": _ts(NOW + timedelta(hours=2)), "status": "ok", "reason": None},
        ],
    )
    append_reset_credits(db, [rc.credit_row(ts, "codex", credit_id="c1", expires_at=_ts(NOW + timedelta(days=5)))])
    env = {**os.environ, "AI_QUOTAS_DATABASE": str(db)}
    proc = subprocess.run(
        [sys.executable, "-m", "ai_quotas", "--json", "--no-refresh"],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["reset_credits"]["codex"]["available"] == 1
    week = next(r for r in data["rows"] if r["window"] == "week")
    five = next(r for r in data["rows"] if r["window"] == "5h")
    assert week["remaining_percent"] == 60.0
    assert week["reset_credits_available"] == 1
    assert week["remaining_percent_total"] == 160.0
    assert five["reset_credits_available"] is None
