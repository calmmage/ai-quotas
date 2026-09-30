"""Source labels (account + device) and the per-account split.

Every stored row says which login and which machine measured it. Two logins
of one provider are never blended into one series or one reset count.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_quotas import accounts, core
from ai_quotas.collector import sample_all_split, sample_now
from ai_quotas.reset_credits import credit_row, none_row
from ai_quotas.storage import (
    SCHEMA_VERSION,
    append_reset_credits,
    append_samples,
    load_reset_credits,
    load_samples,
    schema_version,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
ALICE = "alice@example.com"
BOB = "bob@example.com"


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _q(dt, used, *, account=None, default=None, provider="codex", window="week", **extra):
    row = {
        "ts": _ts(dt), "provider": provider, "window": window, "used_percent": used,
        "resets_at": _ts(NOW + timedelta(days=3)), "plan": "pro", "status": "ok",
        "reason": None, "limit": None, "used": None, "account": account, "device": "example-mac",
    }
    if default is not None:
        row["account_default"] = default
    row.update(extra)
    return row


# ── device + stamping ────────────────────────────────────────────────────────


def test_device_id_is_short_hostname_or_override(monkeypatch):
    monkeypatch.delenv("AI_QUOTAS_DEVICE", raising=False)
    monkeypatch.setattr(accounts.socket, "gethostname", lambda: "Example-Mac.local")
    assert accounts.device_id() == "example-mac"
    monkeypatch.setattr(accounts.socket, "gethostname", lambda: "box.corp.example.com")
    assert accounts.device_id() == "box"
    monkeypatch.setenv("AI_QUOTAS_DEVICE", "desk-1")
    assert accounts.device_id() == "desk-1"


def test_normalize_account_never_guesses():
    assert accounts.normalize_account(" Alice@Example.COM ") == ALICE
    for bad in (None, "", "everyday", "a b@c.d", "@example.com", "alice@", 42):
        assert accounts.normalize_account(bad) is None


def test_collector_stamps_device_and_account_on_quota_and_credit_rows():
    def adapter(ts):
        return [
            {"ts": ts, "provider": "codex", "window": "week", "used_percent": 10.0,
             "status": "ok", "account": "Alice@Example.com"},
            {"ts": ts, "provider": "codex", "window": "5h", "used_percent": 3.0, "status": "ok"},
            credit_row(ts, "codex", credit_id="c1", account="Alice@Example.com"),
        ]

    rows, credits, _ = sample_all_split(_ts(NOW), adapters={"codex": adapter})
    assert {r["device"] for r in rows + credits} == {"example-mac"}
    assert rows[0]["account"] == ALICE
    assert rows[1]["account"] is None  # vendor did not say: null, not guessed
    assert credits[0]["account"] == ALICE


def test_sample_now_stores_account_and_device_columns(tmp_path):
    db = tmp_path / "q.sqlite3"

    def adapter(ts):
        return [{"ts": ts, "provider": "grok", "window": "week", "used_percent": 5.0,
                 "status": "ok", "account": ALICE},
                credit_row(ts, "grok", credit_id="r1", account=ALICE)]

    sample_now(path=db, adapters={"grok": adapter}, ts=_ts(NOW))
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT account, device FROM quota_samples").fetchall() == [(ALICE, "example-mac")]
        assert conn.execute("SELECT account, device FROM reset_credits").fetchall() == [(ALICE, "example-mac")]
    assert load_samples(db)[0]["device"] == "example-mac"  # payload_json stays the truth


# ── storage migration ────────────────────────────────────────────────────────


def _v3_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE quota_samples (
            id INTEGER PRIMARY KEY, ts TEXT, provider TEXT, quota_window TEXT,
            used_percent REAL, resets_at TEXT, plan TEXT, status TEXT, reason TEXT,
            limit_value REAL, used_value REAL, payload_json TEXT NOT NULL);
        CREATE TABLE spend_turns (id INTEGER PRIMARY KEY, turn_key TEXT NOT NULL UNIQUE, ts TEXT,
            provider TEXT, session_id TEXT, turn_id TEXT, total_tokens INTEGER, cost_usd REAL,
            payload_json TEXT NOT NULL);
        CREATE TABLE harvest_files (path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL, n_new INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
        CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE legacy_import_rows (kind TEXT NOT NULL, source_path TEXT NOT NULL,
            line_number INTEGER NOT NULL, row_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL,
            PRIMARY KEY(kind, source_path, line_number));
        CREATE TABLE reset_credits (id INTEGER PRIMARY KEY, ts TEXT, provider TEXT, credit_id TEXT,
            status TEXT, granted_at TEXT, expires_at TEXT, payload_json TEXT NOT NULL);
        CREATE TABLE boosts (id INTEGER PRIMARY KEY, boost_key TEXT NOT NULL UNIQUE, provider TEXT,
            quota_window TEXT, percent REAL, starts_at TEXT, ends_at TEXT, first_seen_ts TEXT,
            last_seen_ts TEXT, raw_text TEXT, payload_json TEXT NOT NULL);
        PRAGMA user_version = 3;
        """
    )
    legacy = {"ts": _ts(NOW - timedelta(days=1)), "provider": "codex", "window": "week",
              "used_percent": 40.0, "status": "ok"}
    conn.execute("INSERT INTO quota_samples(ts, provider, quota_window, used_percent, status, payload_json)"
                 " VALUES (?, 'codex', 'week', 40.0, 'ok', ?)", (legacy["ts"], json.dumps(legacy)))
    conn.commit()
    conn.close()


def test_migration_v3_to_v4_adds_account_device_columns_and_index(tmp_path):
    db = tmp_path / "old.sqlite3"
    _v3_database(db)
    append_samples(db, [_q(NOW, 50.0, account=ALICE)])
    append_reset_credits(db, [{**credit_row(_ts(NOW), "codex", credit_id="c1", account=ALICE),
                               "device": "example-mac"}])
    assert schema_version(db) == SCHEMA_VERSION == 4
    with sqlite3.connect(db) as conn:
        for table in ("quota_samples", "reset_credits"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert {"account", "device"} <= cols
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"quota_samples_account", "reset_credits_account"} <= indexes
        got = conn.execute("SELECT account, device FROM quota_samples ORDER BY id").fetchall()
    assert got == [(None, None), (ALICE, "example-mac")]  # legacy rows stay null
    assert len(load_samples(db)) == 2
    assert load_reset_credits(db)[0]["account"] == ALICE


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "old.sqlite3"
    _v3_database(db)
    append_samples(db, [_q(NOW, 1.0)])
    append_samples(db, [_q(NOW, 2.0)])
    assert schema_version(db) == 4
    assert len(load_samples(db)) == 3


# ── primary account + blend guard ────────────────────────────────────────────


def test_default_login_flag_wins_over_newer_other_login():
    rows = [
        _q(NOW - timedelta(hours=1), 50.0, account=ALICE, default=True),
        _q(NOW, 86.0, account=BOB, default=False),
    ]
    assert accounts.primary_accounts(rows) == {"codex": ALICE}


def test_without_flag_newest_ok_row_names_primary():
    rows = [_q(NOW - timedelta(hours=1), 50.0, account=ALICE),
            _q(NOW, 86.0, account=BOB),
            {**_q(NOW + timedelta(minutes=5), None, account=ALICE), "status": "error"}]
    assert accounts.primary_accounts(rows) == {"codex": BOB}


def test_pin_overrides_detection(monkeypatch):
    monkeypatch.setenv("AI_QUOTAS_ACCOUNT_CODEX", BOB.upper())
    rows = [_q(NOW, 50.0, account=ALICE, default=True), _q(NOW, 86.0, account=BOB)]
    assert accounts.primary_accounts(rows) == {"codex": BOB}


def test_core_load_samples_keeps_primary_and_legacy_rows_only(tmp_path):
    db = tmp_path / "q.sqlite3"
    append_samples(db, [
        _q(NOW - timedelta(hours=2), 40.0),  # legacy: no account → primary
        _q(NOW - timedelta(hours=1), 45.0, account=ALICE, default=True),
        _q(NOW - timedelta(minutes=30), 86.0, account=BOB, default=False),
        _q(NOW, 46.0, account=ALICE, default=True),
    ])
    kept = core.load_samples(db)
    assert [r["used_percent"] for r in kept] == [40.0, 45.0, 46.0]
    assert len(core.load_samples(db, all_accounts=True)) == 4
    # The verdict series is one login: no fake drop 86 → 46.
    latest = core.latest_ok_by_provider_window(kept)[("codex", "week")]
    assert latest["account"] == ALICE


def test_split_accounts_groups_other_logins():
    rows = [_q(NOW, 50.0, account=ALICE, default=True), _q(NOW, 86.0, account=BOB, default=False),
            _q(NOW, 10.0)]
    primary, others = accounts.split_accounts(rows)
    assert [r["used_percent"] for r in primary] == [50.0, 10.0]
    assert list(others) == [("codex", BOB)]


def test_primary_only_credit_rows_use_sample_primary():
    samples = [_q(NOW, 50.0, account=ALICE, default=True)]
    credits = [credit_row(_ts(NOW), "codex", credit_id=f"a{i}", account=ALICE) for i in range(3)]
    credits += [credit_row(_ts(NOW), "codex", credit_id="b1", account=BOB)]
    kept = accounts.primary_only(credits, samples)
    assert sorted(r["credit_id"] for r in kept) == ["a0", "a1", "a2"]


# ── plots: one panel per login, never one line ───────────────────────────────


def _two_account_jsonl(tmp_path: Path) -> Path:
    path = tmp_path / "samples.jsonl"
    start = NOW - timedelta(hours=10)
    rows = []
    for i in range(10):
        t = start + timedelta(hours=i)
        rows.append(_q(t, 40.0 + i, account=ALICE, default=True))
        rows.append(_q(t, 80.0 + i / 2, account=BOB, default=False, plan="prolite"))
    rows.append({**_q(NOW, None, account=BOB, default=False), "window": "credits_balance",
                 "used_percent": None, "remaining": 62500.0, "unit": "credits"})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    credits = [credit_row(_ts(NOW), "codex", credit_id=f"a{i}", account=ALICE,
                          expires_at=_ts(NOW + timedelta(days=20))) for i in range(3)]
    credits.append(credit_row(_ts(NOW), "codex", credit_id="b1", account=BOB,
                              expires_at=_ts(NOW + timedelta(days=20))))
    for row in credits:
        row["device"] = "example-mac"
    path.with_name(path.stem + ".reset-credits.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in credits))
    return path


def test_plots_draw_each_login_as_its_own_panel(tmp_path):
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots
    from ai_quotas.plots.prep import prepare, reset_account_registry

    reset_account_registry()
    samples = _two_account_jsonl(tmp_path)
    df, resets, _ = prepare(samples)
    other = f"Codex · {BOB}"
    assert set(df[df["vendor"] == "Codex"]["series"]) == {"Codex week"}
    assert set(df[df["vendor"] == other]["series"]) == {f"Codex week · {BOB}"}
    # No reset detected from mixing 89% (other login) and 49% (default login).
    assert not [r for r in resets if r.vendor == "Codex"]

    out = tmp_path / "plots"
    generate_plots(samples=samples, out_dir=out, engines=("uplot",))
    panels = json.loads((out / "panels.json").read_text())
    by = {p["vendor"]: p for p in panels["panels"]}
    assert panels["vendors"].index(other) == panels["vendors"].index("Codex") + 1
    assert by["Codex"]["source"] == {"account": ALICE, "device": "example-mac", "ts": _ts(NOW - timedelta(hours=1))}
    assert by[other]["source"]["account"] == BOB
    assert by[other]["account"] == BOB
    assert by[other]["credits_balance"]["remaining"] == 62500.0
    assert by["Codex"]["credits_balance"] is None
    assert [s["label"] for s in by["Codex"]["series"]] == ["Codex week"]
    assert [s["y"][-1] for s in by["Codex"]["series"]] == [51.0]
    # Reset credits are counted per login.
    assert {i["credit_id"] for i in by["Codex"]["reset_credits"]["items"]} == {"a0", "a1", "a2"}
    assert {i["credit_id"] for i in by[other]["reset_credits"]["items"]} == {"b1"}
    reset_account_registry()


def test_legacy_rows_without_accounts_stay_on_the_catalog_panel(tmp_path, multi_path):
    pytest.importorskip("pandas")
    from ai_quotas.plots.prep import panel_vendors, prepare, reset_account_registry

    reset_account_registry()
    df, _, _ = prepare(multi_path)
    assert df.attrs["extra_vendors"] == []
    assert "·" not in "".join(panel_vendors(df))


def test_panel_header_source_line_js():
    if not shutil.which("node"):
        pytest.skip("node not available")
    js = (Path(__file__).resolve().parent.parent / "ai_quotas" / "plots" / "static" / "panel_header.js").read_text()
    script = js + """
console.log(JSON.stringify([
  quotaSourceText({source: {account: 'alice@example.com', device: 'example-mac'}}),
  quotaSourceText({source: {account: null, device: 'example-mac'}}),
  quotaSourceText({}),
  quotaBalanceText({credits_balance: {remaining: 62500, unit: 'credits'}}),
  quotaBalanceText({credits_balance: null}),
]));
"""
    proc = subprocess.run(["node", "--input-type=commonjs", "-e", script],
                          capture_output=True, text=True, timeout=20, check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == [
        "alice@example.com · example-mac", "example-mac", "", "62,500 credits", ""]


def test_cli_table_lists_sources_and_other_logins(tmp_path, capsys):
    from ai_quotas.cli import account_source_lines

    db = tmp_path / "q.sqlite3"
    append_samples(db, [
        _q(NOW, 50.0, account=ALICE, default=True),
        _q(NOW, 86.0, account=BOB, default=False),
    ])
    append_reset_credits(db, [credit_row(_ts(NOW), "codex", credit_id="b1", account=BOB,
                                         expires_at=_ts(NOW + timedelta(days=20)))])
    lines = account_source_lines(db, ["codex"])
    assert lines[0].split() == ["source", "codex", ALICE, "·", "example-mac"]
    assert BOB in lines[1] and "week 14% left" in lines[1] and "1 reset" in lines[1]


# ── adapters ────────────────────────────────────────────────────────────────


def _fake_codexbar(tmp_path: Path, by_home: dict[str, str]) -> Path:
    """A codexbar stand-in that answers per CODEX_HOME, like the real one."""
    lines = ["#!/bin/sh", 'case "$CODEX_HOME" in']
    for home, email in by_home.items():
        payload = json.dumps([{
            "provider": "codex", "source": "fake",
            "credits": {"remaining": 100, "balanceReadSucceeded": True},
            "usage": {"identity": {"accountEmail": email, "loginMethod": "pro"},
                      "secondary": {"usedPercent": 10, "windowMinutes": 10080},
                      "codexResetCredits": {"credits": []}},
        }])
        lines.append(f"  '{home}') echo '{payload}' ;;")
    lines += ["  *) echo 'unexpected home' >&2; exit 3 ;;", "esac"]
    script = tmp_path / "codexbar"
    script.write_text("\n".join(lines) + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_codex_probes_each_home_explicitly_and_ignores_callers_codex_home(tmp_path, monkeypatch):
    from ai_quotas.adapters import codex

    home_a, home_b = tmp_path / "home-a", tmp_path / "home-b"
    home_a.mkdir(), home_b.mkdir()
    bin_path = _fake_codexbar(tmp_path, {str(home_a): ALICE, str(home_b): BOB})
    monkeypatch.setenv("CODEX_HOME", str(home_b))  # an Orca terminal's login
    rows = codex.snapshot(_ts(NOW), codexbar_bin=str(bin_path), homes=[home_a, home_b])
    week = [r for r in rows if r.get("window") == "week"]
    assert [(r["account"], r["account_default"]) for r in week] == [(ALICE, True), (BOB, False)]
    balance = [r for r in rows if r.get("window") == "credits_balance"]
    assert [b["remaining"] for b in balance] == [100.0, 100.0]
    assert all(b["used_percent"] is None for b in balance)
    credits = [r for r in rows if r.get("kind") == "reset_credit"]
    assert {c["account"] for c in credits} == {ALICE, BOB}


def test_codex_same_login_in_two_homes_is_probed_once(tmp_path):
    from ai_quotas.adapters import codex

    home_a, home_b = tmp_path / "home-a", tmp_path / "home-b"
    home_a.mkdir(), home_b.mkdir()
    bin_path = _fake_codexbar(tmp_path, {str(home_a): ALICE, str(home_b): ALICE})
    rows = codex.snapshot(_ts(NOW), codexbar_bin=str(bin_path), homes=[home_a, home_b])
    assert len([r for r in rows if r.get("window") == "week"]) == 1


def test_codex_homes_env_and_orca_discovery(tmp_path, monkeypatch):
    from ai_quotas.adapters import codex

    orca = tmp_path / "orca"
    (orca / "u1" / "home").mkdir(parents=True)
    (orca / "u1" / "home" / "auth.json").write_text("{}")
    (orca / "u2" / "home").mkdir(parents=True)  # no login
    monkeypatch.setattr(codex, "ORCA_CODEX_ACCOUNTS", orca)
    monkeypatch.setattr(codex, "DEFAULT_HOME", tmp_path / "default")
    assert codex.codex_homes() == [tmp_path / "default", orca / "u1" / "home"]
    monkeypatch.setenv("AI_QUOTAS_CODEX_HOMES", os.pathsep.join([str(tmp_path / "x"), str(tmp_path / "y")]))
    assert codex.codex_homes() == [tmp_path / "x", tmp_path / "y"]


def _claude_usage():
    return {"limits": [{"kind": "weekly_all", "percent": 30, "resets_at": _ts(NOW + timedelta(days=2))}]}


def test_claude_labels_default_login_from_profile(monkeypatch):
    from ai_quotas.adapters import claude

    monkeypatch.setattr(claude, "_read_keychain_oauth", lambda *a, **k: {"accessToken": "t", "subscriptionType": "max"})
    monkeypatch.setattr(claude, "_get_access_token", lambda: ("t", "max"))

    def fetch(token, url=claude.USAGE_URL):
        return {"account": {"email": "Alice@Example.com"}} if url == claude.PROFILE_URL else _claude_usage()

    monkeypatch.setattr(claude, "_fetch_usage", fetch)
    rows = claude.snapshot(_ts(NOW), homes=[])
    quota = [r for r in rows if r.get("kind") != "reset_credit"]
    assert quota and all(r["account"] == ALICE and r["account_default"] is True for r in quota)


def test_claude_extra_login_with_expired_token_is_unavailable_not_refreshed(tmp_path, monkeypatch):
    from ai_quotas.adapters import claude

    home = tmp_path / "acct" / "auth"
    home.mkdir(parents=True)
    (home / claude.ORCA_MARKER).write_text("acct-id\n")
    (home / "oauth-account.json").write_text(json.dumps({"emailAddress": BOB}))
    expired_ms = (NOW - timedelta(days=10)).timestamp() * 1000

    def keychain(service=claude.KEYCHAIN_SERVICE, account=None):
        if service == claude.ORCA_KEYCHAIN_SERVICE:
            assert account == "acct-id"
            return {"accessToken": "old", "refreshToken": "r", "expiresAt": expired_ms}
        return {"accessToken": "t"}

    monkeypatch.setattr(claude, "_read_keychain_oauth", keychain)
    monkeypatch.setattr(claude, "_get_access_token", lambda: ("t", "max"))
    calls: list[str] = []

    def fetch(token, url=claude.USAGE_URL):
        calls.append(token)
        return {"account": {"email": ALICE}} if url == claude.PROFILE_URL else _claude_usage()

    monkeypatch.setattr(claude, "_fetch_usage", fetch)
    rows = claude.snapshot(_ts(NOW), homes=[home])
    extra = [r for r in rows if r.get("account") == BOB]
    assert extra and extra[0]["status"] == "unavailable"
    assert "not refreshing" in extra[0]["reason"]
    assert extra[0]["account_default"] is False
    assert "old" not in calls  # the expired token was never sent anywhere


def test_claude_extra_login_with_live_token_is_sampled(tmp_path, monkeypatch):
    from ai_quotas.adapters import claude

    home = tmp_path / "acct" / "auth"
    home.mkdir(parents=True)
    (home / claude.ORCA_MARKER).write_text("acct-id\n")
    (home / "oauth-account.json").write_text(json.dumps({"emailAddress": BOB}))
    fresh_ms = (datetime.now(timezone.utc) + timedelta(hours=5)).timestamp() * 1000
    monkeypatch.setattr(
        claude, "_read_keychain_oauth",
        lambda service=claude.KEYCHAIN_SERVICE, account=None: (
            {"accessToken": "bob-token", "expiresAt": fresh_ms, "subscriptionType": "max"}
            if service == claude.ORCA_KEYCHAIN_SERVICE else {"accessToken": "t"}),
    )
    monkeypatch.setattr(claude, "_get_access_token", lambda: ("t", "max"))
    emails = {"t": ALICE, "bob-token": BOB}
    monkeypatch.setattr(
        claude, "_fetch_usage",
        lambda token, url=claude.USAGE_URL: {"account": {"email": emails[token]}}
        if url == claude.PROFILE_URL else _claude_usage(),
    )
    rows = claude.snapshot(_ts(NOW), homes=[home])
    week = [r for r in rows if r.get("window") == "week"]
    assert [(r["account"], r["account_default"]) for r in week] == [(ALICE, True), (BOB, False)]


def test_grok_rows_carry_the_auth_entry_email(tmp_path, monkeypatch):
    from ai_quotas.adapters import grok

    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"issuer": {"key": "k", "email": "Alice@Example.com",
                                           "expires_at": "2099-01-01T00:00:00Z"}}))
    monkeypatch.setenv("AI_QUOTAS_GROK_AUTH_FILE", str(auth))
    assert grok._auth_account() == ALICE
