"""Owned logins, samplers, the spool and the owned/borrowed merge.

Offline: a local HTTP server stands in for the vendors' token endpoints.
Token-shaped strings below must never show up in rows, spool files or output.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from ai_quotas import cli, config, core, owned, sampler, spool
from ai_quotas import reset_credits as rc
from ai_quotas.accounts import device_id, pinned_account
from ai_quotas.adapters import claude, codex, grok
from ai_quotas.storage import load_reset_credits

NOW = datetime.now(timezone.utc).replace(microsecond=0)
OLD_ACCESS = "sk-ant-oat01-" + "A" * 60
OLD_REFRESH = "sk-ant-ort01-" + "B" * 60
NEW_ACCESS = "sk-ant-oat01-" + "C" * 60
NEW_REFRESH = "sk-ant-ort01-" + "D" * 60
SECRETS = (OLD_ACCESS, OLD_REFRESH, NEW_ACCESS, NEW_REFRESH)
USAGE = {
    "five_hour": {"utilization": 10, "resets_at": (NOW + timedelta(hours=3)).isoformat()},
    "seven_day": {"utilization": 20, "resets_at": (NOW + timedelta(days=3)).isoformat()},
}


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _jwt(claims: dict) -> str:
    def enc(part: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


class _Vendor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.reply: tuple[int, dict] = (
            200,
            {"access_token": NEW_ACCESS, "refresh_token": NEW_REFRESH, "expires_in": 28800},
        )
        self.delay = 0.0


@pytest.fixture
def vendor(monkeypatch):
    state = _Vendor()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            state.calls.append((self.headers.get("Content-Type") or "", body))
            time.sleep(state.delay)
            code, payload = state.reply
            blob = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def log_message(self, format, *args):  # noqa: A002 — quiet test server
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/token"
    monkeypatch.setattr(owned, "CLAUDE_TOKEN_URL", url)
    monkeypatch.setattr(owned, "CODEX_TOKEN_URL", url)
    monkeypatch.setattr(grok, "TOKEN_URL", url)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    yield state
    server.shutdown()
    server.server_close()


def _root() -> Path:
    root = config.logins_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _login(provider: str, creds: dict, name: str = "work", account: str = "me@example.com"):
    path = _root() / f"{provider}-{name}"
    path.mkdir()
    owned.write_marker(path, provider=provider, name=name, account=account)
    owned.atomic_write_json(path / owned.CREDENTIALS[provider], creds)
    return owned.gate(path)


def _claude_creds(expires_in: timedelta) -> dict:
    return {
        "claudeAiOauth": {
            "accessToken": OLD_ACCESS,
            "refreshToken": OLD_REFRESH,
            "expiresAt": int((NOW + expires_in).timestamp() * 1000),
            "scopes": ["user:inference"],
            "subscriptionType": "max",
        }
    }


# ---------------------------------------------------------------- config


def test_config_file_then_env_wins(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[node]\nrole = "sampler"\ndevice = "box-1"\n'
        '[logins]\ndir = "/srv/logins"\n[sampler]\nevery = "15m"\n'
        '[[ingest]]\npath = "/srv/inbox"\npull = "u@h:/spool/"\n'
        '[accounts]\nclaude = "Me@Example.com"\n'
        '[healthchecks]\nslug_prefix = "q-"\n'
    )
    monkeypatch.setenv("AI_QUOTAS_CONFIG", str(cfg))
    monkeypatch.delenv("AI_QUOTAS_LOGINS_DIR")
    monkeypatch.delenv("AI_QUOTAS_DEVICE")
    assert config.role() == "sampler"
    assert device_id() == "box-1"
    assert config.logins_dir() == Path("/srv/logins")
    assert config.sampler_every() == 900
    assert config.ingest_sources() == [config.IngestSource(Path("/srv/inbox"), "u@h:/spool/")]
    assert pinned_account("claude") == "me@example.com"
    assert config.hc_slug_prefix() == "q-"

    monkeypatch.setenv("AI_QUOTAS_ROLE", "hub")
    monkeypatch.setenv("AI_QUOTAS_DEVICE", "env-box")
    monkeypatch.setenv("AI_QUOTAS_INGEST", "/a")
    monkeypatch.setenv("AI_QUOTAS_ACCOUNT_CLAUDE", "other@example.com")
    assert config.role() == "hub"
    assert device_id() == "env-box"
    assert config.ingest_sources() == [config.IngestSource(Path("/a"))]
    assert pinned_account("claude") == "other@example.com"


def test_broken_config_is_defaults_and_reported(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[node\nrole = 'sampler'\n")
    monkeypatch.setenv("AI_QUOTAS_CONFIG", str(cfg))
    assert config.role() == "hub"
    assert "error" in config.doctor_lines()[0]


@pytest.mark.parametrize("raw, seconds", [("30m", 1800), ("1h", 3600), ("900", 900), (60, 60), ("x", None), ("0", None)])
def test_parse_duration(raw, seconds):
    assert config.parse_duration(raw) == seconds


# ---------------------------------------------------------------- gate


def test_gate_refuses_anything_it_does_not_own(tmp_path):
    root = _root()
    (root / "claude-bare").mkdir()
    off = root / "claude-off"
    off.mkdir()
    (off / owned.MARKER).write_text(json.dumps({"provider": "claude", "name": "off", "owned": False}))
    elsewhere = tmp_path / "elsewhere" / "claude-out"
    elsewhere.mkdir(parents=True)
    owned.write_marker(elsewhere, provider="claude", name="out", account=None)
    (root / "claude-link").symlink_to(elsewhere)
    mismatch = root / "claude-mismatch"
    mismatch.mkdir()
    owned.write_marker(mismatch, provider="claude", name="other", account=None)
    cli_home = tmp_path / "home" / ".claude"
    cli_home.mkdir(parents=True)
    (cli_home / ".credentials.json").write_text(json.dumps(_claude_creds(timedelta(minutes=-1))))
    linked = _login("claude", {}, name="linked")
    linked.creds_path.unlink()
    linked.creds_path.symlink_to(cli_home / ".credentials.json")
    good = _login("codex", {"tokens": {}}, name="good")

    for path in (root / "claude-bare", off, elsewhere, root / "claude-link", mismatch, linked.path):
        with pytest.raises(owned.NotOwned):
            owned.gate(path, root)
    logins, skipped = owned.discover(root)
    assert [l.id for l in logins] == [good.id]
    assert {p.name for p, _ in skipped} == {"claude-bare", "claude-off", "claude-link", "claude-mismatch", "claude-linked"}

    # A CLI's own home is never refreshed, even when handed over as a Login.
    borrowed = owned.Login(provider="claude", name="home", path=cli_home)
    before = (cli_home / ".credentials.json").read_bytes()
    with pytest.raises(owned.NotOwned):
        owned.fresh_credentials(borrowed)
    assert (cli_home / ".credentials.json").read_bytes() == before


# ---------------------------------------------------------------- refresh


def test_claude_refresh_rotates_and_persists(vendor):
    vendor.reply = (200, {"access_token": NEW_ACCESS, "refresh_token": NEW_REFRESH,
                          "expires_in": 28800, "scope": "user:inference user:profile"})
    login = _login("claude", _claude_creds(timedelta(minutes=5)))
    creds = owned.fresh_credentials(login)
    assert creds["claudeAiOauth"]["accessToken"] == NEW_ACCESS
    disk = json.loads(login.creds_path.read_text())["claudeAiOauth"]
    assert disk["refreshToken"] == NEW_REFRESH
    assert disk["subscriptionType"] == "max"
    assert disk["scopes"] == ["user:inference", "user:profile"]
    assert disk["expiresAt"] > (NOW + timedelta(hours=7)).timestamp() * 1000
    assert os.stat(login.creds_path).st_mode & 0o777 == 0o600
    assert json.loads(vendor.calls[0][1]) == {
        "grant_type": "refresh_token", "refresh_token": OLD_REFRESH, "client_id": owned.CLAUDE_CLIENT_ID,
    }
    assert owned.read_state(login)["refreshed_at"]
    owned.fresh_credentials(login)  # still fresh: no second call
    assert len(vendor.calls) == 1


def test_valid_token_is_not_refreshed(vendor):
    login = _login("claude", _claude_creds(timedelta(hours=5)))
    assert owned.fresh_credentials(login)["claudeAiOauth"]["accessToken"] == OLD_ACCESS
    assert vendor.calls == []


def test_codex_refresh_when_chain_is_a_week_old(vendor):
    new_access = _jwt({"exp": int((NOW + timedelta(days=10)).timestamp())})
    vendor.reply = (200, {"access_token": new_access, "refresh_token": "rt-new",
                          "id_token": _jwt({"email": "me@example.com"})})
    login = _login("codex", {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": _jwt({"email": "me@example.com"}),
            "access_token": _jwt({"exp": int((NOW + timedelta(days=5)).timestamp())}),
            "refresh_token": "rt-old",
            "account_id": "acct-1",
        },
        "last_refresh": _ts(NOW - timedelta(days=8)),
    })
    creds = owned.fresh_credentials(login)
    assert creds["tokens"]["access_token"] == new_access
    assert creds["tokens"]["refresh_token"] == "rt-new"
    assert creds["tokens"]["account_id"] == "acct-1"
    assert owned._from_iso(creds["last_refresh"]) >= NOW - timedelta(minutes=1)
    body = json.loads(vendor.calls[0][1])
    assert body["client_id"] == owned.CODEX_CLIENT_ID and body["refresh_token"] == "rt-old"
    assert owned.creds_account(login, creds) == "me@example.com"


def test_grok_refresh_uses_its_oidc_form(vendor):
    vendor.reply = (200, {"access_token": "grok-new", "refresh_token": "grok-rt-new", "expires_in": 3600})
    login = _login("grok", {"default": {
        "key": "grok-old", "refresh_token": "grok-rt", "oidc_client_id": "cid",
        "expires_at": _ts(NOW + timedelta(minutes=2)), "email": "me@example.com",
    }})
    creds = owned.fresh_credentials(login)
    assert creds["default"]["key"] == "grok-new"
    assert creds["default"]["refresh_token"] == "grok-rt-new"
    ctype, body = vendor.calls[0]
    assert ctype == "application/x-www-form-urlencoded"
    assert parse_qs(body.decode())["refresh_token"] == ["grok-rt"]


def test_invalid_grant_means_relogin_and_keeps_the_file(vendor):
    vendor.reply = (400, {"error": "invalid_grant", "error_description": "Refresh token expired"})
    login = _login("claude", _claude_creds(timedelta(minutes=-30)))
    before = login.creds_path.read_bytes()
    with pytest.raises(owned.NeedsRelogin) as exc:
        owned.fresh_credentials(login)
    assert "ai-quotas login claude --name work" in str(exc.value)
    assert not any(s in str(exc.value) for s in SECRETS)
    assert login.creds_path.read_bytes() == before
    assert owned.status(login)["state"] == "needs-relogin"


def test_malformed_request_is_an_error_not_relogin(vendor):
    vendor.reply = (400, {"error": "invalid_request"})
    login = _login("claude", _claude_creds(timedelta(minutes=-30)))
    with pytest.raises(owned.OwnedError) as exc:
        owned.fresh_credentials(login)
    assert not isinstance(exc.value, owned.NeedsRelogin)
    assert "invalid_request" in str(exc.value)


def test_two_samplers_on_one_login_refresh_once(vendor):
    vendor.delay = 0.4
    login = _login("claude", _claude_creds(timedelta(minutes=-1)))
    got: list[str] = []

    def run() -> None:
        got.append(owned.fresh_credentials(login)["claudeAiOauth"]["accessToken"])

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert got == [NEW_ACCESS] * 3
    assert len(vendor.calls) == 1


# ---------------------------------------------------------------- sampler


def _spooled(login_id: str) -> tuple[str, list[dict]]:
    files = sorted((config.spool_dir() / device_id() / login_id).glob("*.jsonl"))
    text = "".join(p.read_text() for p in files)
    return text, [json.loads(line) for line in text.splitlines() if line.strip()]


def test_sampler_tick_refreshes_spools_and_never_leaks(vendor, monkeypatch, capsys):
    monkeypatch.setenv("AI_QUOTAS_ROLE", "sampler")
    monkeypatch.setattr(claude, "_fetch_usage", lambda token, url=None: USAGE)
    monkeypatch.setattr(claude, "_profile_email", lambda token: None)
    login = _login("claude", _claude_creds(timedelta(minutes=-1)))

    result = sampler.tick(login.id)
    print(sampler.format_tick(result))
    assert result["ok"] and result["refreshed"] and result["ping"] == "skip"
    assert result["windows"]
    text, rows = _spooled(login.id)
    assert rows and all(r["source"] == "owned" and r["login"] == login.id for r in rows)
    assert all(r["account"] == "me@example.com" and r["device"] == "example-mac" for r in rows)
    assert {r["status"] for r in rows} == {"ok"}
    out = text + json.dumps(result) + capsys.readouterr().out
    assert not any(s in out for s in SECRETS)
    assert owned.read_state(login)["last_ok_at"] == result["ts"]


def test_adapter_error_carrying_a_token_is_scrubbed(vendor, monkeypatch):
    monkeypatch.setenv("AI_QUOTAS_ROLE", "sampler")

    def boom(token, url=None):
        raise RuntimeError(f"upstream echoed {token}")

    monkeypatch.setattr(claude, "_fetch_usage", boom)
    login = _login("claude", _claude_creds(timedelta(hours=5)))
    result = sampler.tick(login.id)
    assert not result["ok"]
    text, rows = _spooled(login.id)
    assert rows[0]["status"] == "error" and rows[0]["used_percent"] is None
    assert "<redacted>" in rows[0]["reason"]
    assert not any(s in text + json.dumps(result) for s in SECRETS)


def test_dead_login_becomes_a_relogin_row(vendor, monkeypatch):
    monkeypatch.setenv("AI_QUOTAS_ROLE", "sampler")
    vendor.reply = (400, {"error": "invalid_grant"})
    login = _login("claude", _claude_creds(timedelta(minutes=-30)))
    result = sampler.tick(login.id)
    assert result["ok"] is False and result["status"] == "unavailable"
    assert "ai-quotas login claude --name work" in result["reason"]
    _, rows = _spooled(login.id)
    assert rows[0]["status"] == "unavailable" and rows[0]["used_percent"] is None
    assert owned.read_state(login)["status"] == "needs-relogin"
    assert sampler.run(login.id, once=True) == 1


def test_unknown_login_is_a_clean_failure():
    result = sampler.tick("claude-nope")
    assert result["ok"] is False and "no owned login" in result["reason"]


def test_hub_mode_writes_the_database(vendor, monkeypatch, tmp_path):
    monkeypatch.setattr(claude, "_fetch_usage", lambda token, url=None: USAGE)
    monkeypatch.setattr(claude, "_profile_email", lambda token: None)
    login = _login("claude", _claude_creds(timedelta(hours=5)))
    db = tmp_path / "hub.sqlite3"
    assert sampler.tick(login.id, database=db)["ok"]
    rows = core.load_samples(db)
    assert rows and all(r["source"] == "owned" for r in rows)
    assert not (config.spool_dir()).exists()


def test_healthchecks_url_per_login(monkeypatch):
    monkeypatch.setenv("AI_QUOTAS_HC_PING_KEY", "pk")
    monkeypatch.setenv("CALMMAGE_HEALTHCHECKS_BASE_URL", "https://hc.example")
    login = owned.Login(provider="claude", name="work", path=Path("/x"))
    assert sampler.hc_url(login) == "https://hc.example/ping/pk/ai-quotas-claude-work"


# ---------------------------------------------------------------- codex wham


def test_wham_usage_gives_the_codexbar_windows():
    data = {
        "email": "Me@Example.com",
        "plan_type": "pro",
        "rate_limit": {
            "primary_window": {"used_percent": 12.5, "limit_window_seconds": 18000,
                               "reset_after_seconds": 3600, "reset_at": 1790000000},
            "secondary_window": {"used_percent": 40, "limit_window_seconds": 604800,
                                 "reset_at": 1790500000},
        },
        "additional_rate_limits": [{"limit_name": "other", "rate_limit": {
            "primary_window": {"used_percent": 3, "limit_window_seconds": 18000, "reset_at": 1790000000},
        }}],
        "credits": {"has_credits": True, "unlimited": False, "balance": "62230.93"},
    }
    rows = codex.parse_wham_usage(_ts(NOW), data)
    by = {r["window"]: r for r in rows}
    assert set(by) == {codex._window_name(300), codex._window_name(10080), codex.CREDITS_WINDOW}
    assert by["5h"]["used_percent"] == 12.5 and by["5h"]["status"] == "ok"
    assert by["week"]["used_percent"] == 40
    assert by["week"]["resets_at"] == codex._resets_at_iso(1790500000)
    assert by["credits_balance"]["remaining"] == pytest.approx(62230.93)
    assert {r["account"] for r in rows} == {"me@example.com"}


def test_wham_reset_credits_only_available():
    data = {"available_count": 1, "credits": [
        {"id": "RateLimitResetCredit_a", "status": "available", "reset_type": "codex_rate_limits",
         "granted_at": "2026-09-22T21:03:33Z", "expires_at": "2026-10-22T21:03:33Z", "title": "Full reset"},
        {"id": "RateLimitResetCredit_b", "status": "redeemed"},
    ]}
    rows = codex.parse_wham_reset_credits(_ts(NOW), data, "me@example.com")
    assert [r["credit_id"] for r in rows] == ["RateLimitResetCredit_a"]
    assert codex.parse_wham_reset_credits(_ts(NOW), {"credits": []}, None)[0]["status"] == "none"


# ---------------------------------------------------------------- reset-credit identity


def _credit(ts: datetime, cid: str, source: str, granted: str = "2026-09-22T21:03:33+00:00") -> dict:
    row = rc.credit_row(_ts(ts), "codex", credit_id=cid, title="Full reset", granted_at=granted,
                        expires_at="2026-10-22T21:03:33+00:00", account="me@example.com")
    row["source"] = source
    return row


def test_one_credit_seen_by_codexbar_and_by_the_api_is_one_credit():
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    rows = []
    for step in range(4):
        tick = t0 + timedelta(minutes=30 * step)
        rows.append(_credit(tick, "codex-reset-credit-v1-89ab", "borrowed"))
        rows.append(_credit(tick + timedelta(minutes=10), "RateLimitResetCredit_a", "owned",
                            granted="2026-09-22T21:03:33Z"))
    states = rc.credit_states(rows, now=t0 + timedelta(hours=3))
    assert [s["status"] for s in states] == ["available"]

    # Redeemed: both sources stop listing it → one consumed credit, not two.
    gone = t0 + timedelta(hours=2)
    for source, offset in (("borrowed", 0), ("owned", 10)):
        none = rc.none_row(_ts(gone + timedelta(minutes=offset)), "codex", account="me@example.com")
        none["source"] = source
        rows.append(none)
    states = rc.credit_states(rows, now=gone + timedelta(hours=5))
    assert [s["status"] for s in states] == ["consumed"]


def test_two_credits_granted_together_stay_two():
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    rows = [_credit(t0, "a", "borrowed"), _credit(t0, "b", "borrowed"),
            _credit(t0 + timedelta(minutes=5), "raw-a", "owned"),
            _credit(t0 + timedelta(minutes=5), "raw-b", "owned")]
    # Ambiguous pairing is never merged: no source's credit is hidden.
    assert len(rc.credit_states(rows, now=t0 + timedelta(hours=1))) == 4
    same_source = [_credit(t0, "a", "borrowed"), _credit(t0, "b", "borrowed")]
    assert len(rc.credit_states(same_source, now=t0 + timedelta(hours=1))) == 2


# ---------------------------------------------------------------- merge + fallback


def _row(ts: datetime, pct: float, source: str, *, status: str = "ok", account: str = "me@example.com") -> dict:
    return {"ts": _ts(ts), "provider": "claude", "window": "week", "status": status,
            "used_percent": pct if status == "ok" else None, "account": account,
            "source": source, "device": "mac" if source == "borrowed" else "cloud"}


def test_owned_row_shadows_a_borrowed_row_of_the_same_moment():
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    rows = [
        _row(t0, 40, "borrowed"),
        _row(t0 + timedelta(minutes=5), 41, "owned"),
        _row(t0 + timedelta(hours=1), 45, "borrowed"),  # no owned row near: kept
        _row(t0, 70, "borrowed", account="other@example.com"),  # other login: kept
    ]
    merged = core.merge_sources(rows)
    assert [(r["used_percent"], r["source"]) for r in merged] == [(41, "owned"), (45, "borrowed"), (70, "borrowed")]


def test_newer_failure_does_not_hide_a_fresh_reading():
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    rows = [_row(t0, 41, "owned"), _row(t0 + timedelta(minutes=30), 0, "owned", status="unavailable")]
    assert [r["used_percent"] for r in core.latest_by_key(rows).values()] == [41]
    rows.append(_row(t0 + timedelta(hours=3), 0, "owned", status="unavailable"))
    assert [r["status"] for r in core.latest_by_key(rows).values()] == ["unavailable"]


def test_dead_sampler_leaves_the_borrowed_series_showing():
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    rows = [_row(t0, 41, "owned"), _row(t0 + timedelta(hours=1), 44, "borrowed"),
            _row(t0 + timedelta(hours=2), 47, "borrowed")]
    latest = list(core.latest_by_key(core.merge_sources(rows)).values())
    assert [(r["used_percent"], r["source"]) for r in latest] == [(47, "borrowed")]


def test_rows_of_one_round_are_not_stale():
    newest = _ts(datetime(2026, 10, 1, 12, 40, tzinfo=timezone.utc))
    assert cli.same_tick(_ts(datetime(2026, 10, 1, 12, 5, tzinfo=timezone.utc)), newest)
    assert not cli.same_tick(_ts(datetime(2026, 10, 1, 11, 0, tzinfo=timezone.utc)), newest)


# ---------------------------------------------------------------- spool


def test_spool_ingest_is_idempotent_and_waits_for_partial_lines(tmp_path):
    inbox = tmp_path / "inbox"
    db = tmp_path / "hub.sqlite3"
    ts = _ts(NOW)
    boost = claude._boost_rows(ts, {"notices": [{"text": "limits temporarily boosted +50% through 13 Sep"}]})
    credit = rc.credit_row(ts, "codex", credit_id="raw-a", granted_at="2026-09-22T21:03:33Z",
                           expires_at="2099-10-22T21:03:33Z", account="me@example.com")
    path = spool.append([_row(NOW, 41, "owned"), credit, *boost], root=inbox, device="box", login="claude-work")
    assert path == inbox / "box" / "claude-work" / f"{NOW.strftime('%Y-%m-%d')}.jsonl"
    assert os.stat(path).st_mode & 0o777 == 0o600

    source = [config.IngestSource(inbox)]
    first = spool.ingest(db, source, do_pull=False)
    assert first["imported"] == 3 and not first["errors"]
    assert spool.ingest(db, source, do_pull=False)["imported"] == 0
    assert len(core.load_samples(db)) == 1
    assert len(load_reset_credits(db)) == 1

    line = json.dumps(_row(NOW + timedelta(minutes=30), 43, "owned"))
    with path.open("a") as fh:
        fh.write(line[:20])
    assert spool.ingest(db, source, do_pull=False)["imported"] == 0
    with path.open("a") as fh:
        fh.write(line[20:] + "\n")
    assert spool.ingest(db, source, do_pull=False)["imported"] == 1
    assert len(core.load_samples(db)) == 2


def test_ingest_refuses_a_jsonl_target(tmp_path):
    report = spool.ingest(tmp_path / "samples.jsonl", [], do_pull=False)
    assert report["errors"]


def test_push_and_pull_skip_when_unset():
    assert spool.push() == "skip"
    assert spool.pull(config.IngestSource(Path("/nowhere"))) == "skip"


# ---------------------------------------------------------------- roles


def test_sampler_node_never_alerts_and_spools_its_local_rows(monkeypatch, tmp_path, capsys):
    from ai_quotas import alerts, notify

    monkeypatch.setenv("AI_QUOTAS_ROLE", "sampler")
    row = _row(NOW, 41, "borrowed") | {"device": "example-mac"}
    monkeypatch.setattr(cli, "sample_now_split", lambda **kw: ([row], [], []))
    monkeypatch.setattr(cli, "_harvest_best_effort", lambda **kw: {"error": "skipped in test"})

    def no_alerts(**_kw):
        raise AssertionError("a sampler node must not alert")

    monkeypatch.setattr(alerts, "run_after_sample", no_alerts)
    pings: list[str] = []
    monkeypatch.setattr(notify, "ping_role", lambda role, **kw: pings.append(role) or "skip")
    assert cli.main(["--database", str(tmp_path / "local.sqlite3"), "sample"]) == 0
    assert pings == ["sample"]
    _, rows = _spooled(sampler.BORROWED_LOGIN)
    assert rows == [row]


def test_latest_source_names_the_owned_login_and_its_fix(monkeypatch):
    from ai_quotas.accounts import latest_source

    monkeypatch.setenv("AI_QUOTAS_LOGINS_MOVE_TO", "box:/srv/logins")
    login = owned.Login(provider="claude", name="work", path=Path("/x"))
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    dead = _row(t0 + timedelta(hours=1), 0, "owned", status="unavailable") | {
        "login": login.id, "reason": str(owned.NeedsRelogin(login, "refresh refused (HTTP 400 invalid_grant)"))}
    rows = [_row(t0, 41, "owned") | {"login": login.id}, dead,
            _row(t0 + timedelta(hours=2), 0, "borrowed", status="unavailable")]
    src = latest_source(rows)
    assert src["login"] == login.id
    assert src["relogin"] == "ai-quotas login claude --name work --move-to box:/srv/logins"
    assert "relogin" not in latest_source(rows[:1])


def test_rsync_creates_the_destination(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(spool.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(spool.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    local = tmp_path / "a" / "b" / "inbox"
    assert spool._rsync("src/", str(local) + "/") == "ok"
    assert local.is_dir()
    assert spool._rsync("src/", "box:/srv/my spool/") == "ok"
    assert "--rsync-path=mkdir -p '/srv/my spool/' && rsync" in calls[-1]
    assert spool._remote("/abs/path") is None and spool._remote("user@box:dir") == ("user@box", "dir")


def test_unreachable_vendor_is_a_network_error_not_a_relogin(monkeypatch, tmp_path):
    import urllib.error

    from ai_quotas.adapters import claude

    login = _login("claude", _claude_creds(timedelta(days=30)), name="net")

    def offline(ts, lg):
        raise urllib.error.URLError(OSError(-3, "Temporary failure in name resolution"))

    monkeypatch.setattr(claude, "snapshot_owned", offline)
    rows = sampler.snapshot("2026-10-17T00:00:00+00:00", login)
    assert rows[0]["status"] == "error"
    assert rows[0]["reason"] == "network: Temporary failure in name resolution"
    assert "re-login" not in rows[0]["reason"]


def test_owned_login_without_a_reading_says_where_it_failed():
    from ai_quotas.plots.prep import account_attrs, reset_account_registry

    reset_account_registry()
    base = {"provider": "claude", "window": "unknown", "source": "owned", "login": "claude-work",
            "device": "box-1", "account": "me@example.com", "used_percent": None}
    net = {**base, "ts": "2026-10-17T00:00:00+00:00", "status": "error", "reason": "network: timed out"}
    setup = account_attrs([net])["setups"]["Claude"]
    assert setup["status"] == "No reading yet"
    assert setup["body"] == "claude-work on box-1: network: timed out"
    gone = {**base, "ts": "2026-10-17T00:30:00+00:00", "status": "unavailable",
            "reason": "refresh rejected (invalid_grant); needs re-login: `ai-quotas login claude --name work`"}
    setup = account_attrs([net, gone])["setups"]["Claude"]
    assert setup["status"] == "Signed out"
    assert setup["command"] == "uv run ai-quotas login claude --name work"
    ok = {**base, "ts": "2026-10-17T01:00:00+00:00", "status": "ok", "window": "week", "used_percent": 10}
    assert "Claude" not in account_attrs([net, gone, ok])["setups"]
    borrowed = {**net, "source": "borrowed", "login": None}
    assert "Claude" not in account_attrs([borrowed])["setups"]


def test_ssh_binary_prefers_the_override_then_the_system_one(monkeypatch):
    monkeypatch.setenv("AI_QUOTAS_SSH", "/opt/my/ssh")
    assert config.ssh_binary() == "/opt/my/ssh"
    monkeypatch.delenv("AI_QUOTAS_SSH")
    expected = "/usr/bin/ssh" if os.access("/usr/bin/ssh", os.X_OK) else "ssh"
    assert config.ssh_binary() == expected


def test_secure_does_not_chmod_through_symlinks(tmp_path):
    from ai_quotas import login as login_mod

    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    os.chmod(outside, 0o644)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "creds.json").write_text("{}")
    (staging / "link").symlink_to(outside)
    login_mod._secure(staging)
    assert outside.stat().st_mode & 0o777 == 0o644
    assert (staging / "creds.json").stat().st_mode & 0o777 == 0o600
