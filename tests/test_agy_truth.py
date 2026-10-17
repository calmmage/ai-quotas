"""The shared Gemini bucket must never hide a real Argon rejection."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_quotas import core
from ai_quotas.collector import sample_now

NOW = datetime(2026, 10, 17, 9, tzinfo=timezone.utc)


def row(window, used, **extra):
    return dict(ts=NOW.isoformat(), provider="agy", window=window,
                used_percent=used, status="ok", account="owner@example.test",
                account_default=True, resets_at=(NOW + timedelta(hours=159)).isoformat(),
                **extra)


def test_individual_cap_blocks_argon_without_blocking_flash():
    result = core.evaluate([
        row("week_gemini_pro", 6.1), row("week_gemini_flash", 6.1),
        row("week_gemini_argon", 100, individual_cap=True),
    ], now=NOW)
    assert result["verdicts"]["agy"]["verdict"] == "STOP"
    assert result["verdicts"]["agy"]["trend_total_pct_per_hour"] is None
    picked = core.pick_harness(result, candidates=("gemini-argon", "gemini-flash"))
    assert picked["harness"] == "gemini-flash"
    assert picked["skipped"][0]["verdict"] == "STOP"


def test_individual_cap_stops_even_in_last_hour_and_expires_to_unknown():
    cap = row("week_gemini_argon", 100, individual_cap=True)
    reset = datetime.fromisoformat(cap["resets_at"])
    assert core.evaluate([cap], now=reset-timedelta(minutes=2))["windows"][0]["verdict"] == "STOP"
    result = core.evaluate([cap], now=reset+timedelta(minutes=2))
    assert result["windows"][0]["verdict"] == "UNKNOWN"
    assert core.pick_harness(result, candidates=("gemini-argon",))["harness"] is None


def test_missing_week_does_not_reuse_old_success():
    old = row("week_gemini_pro", 6.1)
    missing = {**old, "ts": (NOW+timedelta(minutes=30)).isoformat(),
               "status": "unavailable", "used_percent": None,
               "reason": "Gemini week not reported by either quota endpoint"}
    result = core.evaluate([old, missing], now=NOW+timedelta(minutes=30))
    assert result["verdicts"]["agy"]["verdict"] == "UNKNOWN"
    assert core.pick_harness(result, candidates=("gemini-argon",))["harness"] is None


def test_collector_reuses_argon_error_with_observation_time(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "cli-20261017_112435.log").write_text(
        'I1017 11:24:35.743117 1 resolver.go:85] Model ID gemini-4-argon-high not in local config\n'
        'I1017 11:24:36.338564 1 server_oauth.go:224] applyAuthResult: email=owner@example.test, authMethod=consumer\n'
        'E1017 11:24:41.116352 485 errorreport.go:224] RESOURCE_EXHAUSTED (code 429): '
        'Individual quota reached. Resets in 159h49m49s.\n'
    )
    monkeypatch.setenv("AI_QUOTAS_AGY_LOG_DIR", str(logs))
    # Glog timestamps are local. Compare in that same timezone, including DST.
    observed = datetime(2026, 10, 17, 11, 24, 41).astimezone()
    ts = (observed+timedelta(minutes=10)).isoformat()
    path = tmp_path / "quota.sqlite3"
    rows = sample_now(ts=ts, path=path, adapters={"agy": lambda ts: [
        {**row("week_gemini_pro", 6.1), "ts": ts},
    ]})
    cap = next(r for r in rows if r["window"] == "week_gemini_argon")
    assert cap["account"] == "owner@example.test"
    assert cap["used_percent"] == 100
    assert datetime.fromisoformat(cap["resets_at"]) == observed+timedelta(hours=159, minutes=49, seconds=49)
    assert datetime.fromisoformat(cap["observed_at"]) == observed
    stored = core.load_samples(path, all_accounts=True)
    assert any(r["window"] == cap["window"] for r in stored)
    # A later sample must not push the reset deadline forward.
    again = sample_now(ts=(observed+timedelta(hours=1)).isoformat(), append=False,
                       adapters={"agy": lambda ts: []})
    assert again[0]["resets_at"] == cap["resets_at"]


def test_argon_dashboard_notice_is_account_scoped(tmp_path):
    from ai_quotas.plots.prep import prepare
    from ai_quotas.plots.generate import _vendor_panel_payload
    path = tmp_path / "quota.jsonl"
    rows = [row("week_gemini_pro", 6.1), row("week_gemini_argon", 100, individual_cap=True)]
    rows += [{**row("week_gemini_pro", 1), "account": "other@example.test", "account_default": False}]
    path.write_text("".join(json.dumps(r)+"\n" for r in rows))
    df, resets, _ = prepare(path)
    panel = _vendor_panel_payload(df, resets, "Gemini")
    assert panel["model_limits"][0]["window"] == "week_gemini_argon"
    assert not _vendor_panel_payload(df, resets, "Gemini · other@example.test")["model_limits"]


def test_partial_summary_fetches_gemini_instead_of_silently_stopping():
    from ai_quotas.agy_limits import fetch_complete_quota, complete_quota_rows
    calls = []
    def fetch(urls, token, body):
        calls.append(urls)
        if urls == "summary":
            return {"groups": [{"displayName": "Claude and GPT models"}]}, []
        return {"buckets": [{"modelId": "gemini-3.8-flash-tiered", "remainingFraction": .99}]}, []
    payload = fetch_complete_quota("unused", load=lambda _: (None, "Google AI Pro"),
                                   fetch=fetch, summary_urls="summary", quota_urls="legacy")
    assert calls == ["summary", "legacy"]
    rows = complete_quota_rows(NOW.isoformat(), payload,
        summary_parser=lambda ts, p: [row("week_opus", 51)],
        bucket_parser=lambda ts, p: [row("5h_gemini_flash", 1)])
    by = {r["window"]: r for r in rows}
    assert by["5h_gemini_flash"]["used_percent"] == 1
    assert by["week_opus"]["used_percent"] == 51
    assert by["week_gemini_pro"]["status"] == "unavailable"
    assert by["week_gemini_pro"]["used_percent"] is None


def test_complete_summary_needs_no_extra_rpc_and_failed_fallback_is_unknown():
    from ai_quotas.agy_limits import fetch_complete_quota, complete_quota_rows
    calls = []
    def fetch(urls, token, body):
        calls.append(urls)
        return {"groups": [{"displayName": "Gemini models"}]}, []
    payload = fetch_complete_quota("unused", load=lambda _: (None, None), fetch=fetch,
                                   summary_urls="summary", quota_urls="legacy")
    assert calls == ["summary"]
    rows = complete_quota_rows(NOW.isoformat(), payload,
        summary_parser=lambda ts, p: [row("week_gemini_pro", 6.1)],
        bucket_parser=lambda *_: [])
    assert next(r for r in rows if r["window"] == "week_gemini_pro")["used_percent"] == 6.1


def test_fallback_network_failure_emits_unknown_windows():
    from ai_quotas.agy_limits import fetch_complete_quota, complete_quota_rows
    def fetch(urls, *_):
        return ({"groups": [{"displayName": "Claude and GPT models"}]}, []) if urls == "summary" else (None, ["HTTP 403"])
    p = fetch_complete_quota("unused", load=lambda _: (None, None), fetch=fetch,
                            summary_urls="summary", quota_urls="legacy")
    rows = complete_quota_rows(NOW.isoformat(), p, summary_parser=lambda *_: [], bucket_parser=lambda *_: [])
    assert len(rows) == 4
    assert all(r["used_percent"] is None and r["status"] == "unavailable" for r in rows)


@pytest.mark.parametrize("model,account,age", [
    ("gemini-3.8-flash-low", True, 10),
    ("gemini-4-argon-low", False, 10),
    ("gemini-4-argon-low", True, 200),
])
def test_cap_requires_argon_verified_login_and_unexpired_error(tmp_path, model, account, age):
    from ai_quotas.agy_limits import observed_argon_caps
    observed = datetime(2026, 10, 17, 11, 25, 23).astimezone()
    (tmp_path/"cli-20261017_112519.log").write_text(
        f'I1017 11:25:19.000001 1 resolver.go:85] Model ID {model} not in local config\n'
        + ('I1017 11:25:20.000001 1 server_oauth.go:224] applyAuthResult: email=owner@example.test, authMethod=consumer\n' if account else '')
        + 'E1017 11:25:23.000001 1 errorreport.go:224] Individual quota reached. Resets in 159h49m6s.\n')
    assert not observed_argon_caps((observed+timedelta(hours=age)).isoformat(), log_dir=tmp_path)


def test_missing_week_suppresses_allowance_advice_and_keeps_fresh_short_reading(tmp_path):
    from ai_quotas.plots.prep import prepare
    from ai_quotas.plots.generate import _vendor_panel_payload
    old = row("week_gemini_pro", 6.1)
    missing = {**old, "ts": (NOW+timedelta(minutes=30)).isoformat(),
               "status": "unavailable", "used_percent": None}
    path = tmp_path/"quota.jsonl"
    path.write_text("".join(json.dumps(r)+"\n" for r in [old, missing, row("5h_gemini_pro", 2.2)]))
    df, resets, _ = prepare(path)
    panel = _vendor_panel_payload(df, resets, "Gemini")
    assert panel["missing_windows"][0]["window"] == "week_gemini_pro"
    assert panel["short_reading"]["used_percent"] == 2.2
    assert panel["budget"] == []
