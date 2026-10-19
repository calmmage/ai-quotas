"""Panel chrome: stale fix command, spare-quota borders, exhausted countdown."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("pandas")

from ai_quotas.plots.generate import _vendor_panel_payload, generate_plots  # noqa: E402
from ai_quotas.plots.prep import prepare  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
HEADER_JS = (
    Path(__file__).resolve().parent.parent / "ai_quotas" / "plots" / "static" / "panel_header.js"
)


def _run_js(cases: list[dict]) -> list:
    if not shutil.which("node"):
        pytest.skip("node not available")
    script = HEADER_JS.read_text(encoding="utf-8") + """
const cases = %s;
const out = cases.map(c => {
  const state = quotaPanelState(c.panel, c.now);
  if (c.op === 'count') return quotaCountdownText(state.exhausted && state.exhausted.at, c.now);
  if (c.op === 'sample') return quotaSampleCommand(c.panel);
  if (c.op === 'summary') return quotaSampleSummary(c.rows, c.account);
  if (c.op === 'used') return quotaUsedReset(c.credit, c.now);
  if (c.op === 'reason') return quotaShortReason(c.reason);
  if (c.op === 'missing') return quotaMissingText(c.panel);
  if (c.op === 'short') return quotaShortText(c.panel, c.now);
  return state;
});
console.log(JSON.stringify(out));
""" % json.dumps(cases)
    proc = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env={**os.environ, "TZ": "UTC"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _panel(provider, vendor, *, sampled, remaining, resets_at, dim=False, focus=True):
    return {
        "vendor": vendor,
        "checkout": "/tmp/ai-quotas",
        "troubleshoot_bin": "grok",
        "sampled_at": sampled,
        "subscription": {"provider": provider},
        "series": [{
            "label": f"{vendor} week",
            "focus": focus,
            "dim": dim,
            "y": [remaining],
            "resets_at": resets_at,
        }],
    }


def test_series_payload_carries_reset_and_checkout():
    df, resets, _ = prepare(FIXTURE)
    payload = _vendor_panel_payload(df, resets, "Claude")
    week = next(s for s in payload["series"] if s["label"] == "Claude week")
    last = None
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("provider") == "claude" and row.get("window") == "week" and row.get("resets_at"):
            last = row["resets_at"]
    assert week["resets_at"] == int(datetime.fromisoformat(last).timestamp())
    assert (Path(payload["checkout"]) / "pyproject.toml").is_file()
    assert payload["troubleshoot_bin"] == "grok"


def test_generated_shell_inlines_panel_chrome(tmp_path):
    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("uplot",))
    html = (out / "10_uplot" / "index.html").read_text(encoding="utf-8")
    assert "function quotaPanelState" in html
    assert "Copy fix command" in html
    assert "Check now" in html
    assert "spare-stop" in html
    assert "reset-countdown" in html


def test_stale_claude_codex_grok_copy_a_troubleshooting_command():
    now = 1_800_000_000_000  # ms
    now_s = now / 1000
    stale = now_s - 3 * 3600
    fresh = now_s - 30 * 60
    reset = now_s + 10 * 3600
    got = _run_js([
        {"op": "state", "panel": _panel("claude", "Claude", sampled=stale, remaining=90, resets_at=reset), "now": now},
        {"op": "state", "panel": _panel("codex", "Codex", sampled=stale, remaining=90, resets_at=reset), "now": now},
        {"op": "state", "panel": _panel("grok", "Grok", sampled=stale, remaining=90, resets_at=reset), "now": now},
        {"op": "state", "panel": _panel("claude", "Claude", sampled=fresh, remaining=90, resets_at=reset), "now": now},
        {"op": "state", "panel": _panel("agy", "Gemini", sampled=stale, remaining=90, resets_at=reset), "now": now},
        {"op": "sample", "panel": _panel("claude", "Claude", sampled=stale, remaining=90, resets_at=reset), "now": now},
        {"op": "summary", "rows": [{"window": "week", "status": "ok", "used_percent": 12}], "now": now, "panel": {}},
        {"op": "summary", "rows": [{"window": "week", "status": "error", "reason": "token expired"}], "now": now, "panel": {}},
    ])
    claude, codex, grok, fresh, gemini, sample_cmd, summary_ok, summary_bad = got
    cmd = claude["fix"]
    assert cmd.startswith("cd '/tmp/ai-quotas' && grok '")
    assert "Claude quota data is stale." in cmd
    assert "Run any claude command to refresh it" in cmd
    assert "Do not print tokens or secrets." in cmd
    assert "\n" not in cmd
    assert "codexbar" in codex["fix"]
    assert "make grok-fix" in grok["fix"]
    assert fresh["fix"] == ""
    assert fresh["stale"] is False
    assert gemini["stale"] is True
    assert gemini["fix"] == ""
    assert sample_cmd == "cd '/tmp/ai-quotas' && uv run ai-quotas sample --provider claude"
    assert summary_ok == "Checked · week 88% remaining"
    assert summary_bad == "Check failed · token expired"


def test_spare_borders_follow_alert_severity():
    now = 1_800_000_000_000
    now_s = now / 1000

    specs = [(80, 10), (60, 30), (30, 10), (20, 10), (80, 60), (80, -1), (90, 2), (90, 2)]
    flags = [{}, {}, {}, {}, {}, {}, {"dim": True}, {"focus": False}]
    got = _run_js([
        {"op": "state", "now": now, "panel": _panel(
            "claude", "Claude", sampled=now_s, remaining=rem, resets_at=now_s + hours * 3600, **flag,
        )}
        for (rem, hours), flag in zip(specs, flags, strict=True)
    ])
    assert [row["spare"] for row in got] == ["stop", "warn", "warn", None, None, None, None, None]


def test_near_zero_is_exhausted_and_counts_down():
    now = 1_800_000_000_000
    now_s = now / 1000
    empty = _panel("codex", "Codex", sampled=now_s, remaining=0, resets_at=now_s + 3 * 3600 + 125)
    near = _panel("codex", "Codex", sampled=now_s, remaining=5, resets_at=now_s + 90)
    usable = _panel("codex", "Codex", sampled=now_s, remaining=5.01, resets_at=now_s + 3600)
    overdue = _panel("codex", "Codex", sampled=now_s, remaining=0, resets_at=now_s - 60)
    missing = _panel("codex", "Codex", sampled=now_s, remaining=0, resets_at=None)
    got = _run_js([
        {"op": "count", "panel": empty, "now": now},
        {"op": "state", "panel": near, "now": now},
        {"op": "state", "panel": usable, "now": now},
        {"op": "count", "panel": overdue, "now": now},
        {"op": "count", "panel": missing, "now": now},
    ])
    assert got[0] == "Resets in 03:02:05"
    assert got[1]["exhausted"]["at"] == near["series"][0]["resets_at"]
    assert got[2]["exhausted"] is None
    assert got[3] == "Reset overdue"
    assert got[4] == "Reset time not reported"


def test_argon_notice_suppresses_spare_and_discloses_separate_clocks():
    now = 1_800_000_000_000
    now_s = now / 1000
    panel = _panel("agy", "Gemini", sampled=now_s, remaining=94, resets_at=now_s+3600)
    panel["model_limits"] = [{"window": "week_gemini_argon", "observed_at": "2027-01-15T00:00:00Z",
                              "resets_at": "2027-02-01T03:14:29Z"}]
    panel["missing_windows"] = [{"window": "week_gemini_pro"}]
    panel["short_reading"] = {"used_percent": 2.2, "ts": "2027-01-15T00:00:00Z"}
    state, missing, short, expired = _run_js([
        {"op": "state", "panel": panel, "now": now},
        {"op": "missing", "panel": panel, "now": now},
        {"op": "short", "panel": panel, "now": now},
        {"op": "state", "panel": panel, "now": 1_900_000_000_000},
    ])
    assert state["spare"] is None
    assert state["modelLimit"].startswith("Argon · STOP · cap reached · Resets in ")
    # Details live in the tooltip; the label stays one short line.
    assert "reset 2027-02-01 03:14" in state["modelLimitTitle"]
    assert "observed 2027-01-15 00:00" in state["modelLimitTitle"]
    assert "week · UNKNOWN" in missing
    assert "98% remaining" in short and "separate from Argon cap" in short
    assert "Argon · UNKNOWN" in expired["modelLimit"]


def test_auto_check_does_not_open_the_clipboard_dialog_when_api_is_offline():
    """A stale panel auto-clicks Check now. On the cloud mirror there is no
    sample API, and a clipboard write without a gesture opens a modal.
    """
    if not shutil.which("node"):
        pytest.skip("node not available")
    script = HEADER_JS.read_text(encoding="utf-8") + r"""
const dialogs = [];
let copyCalls = 0;
globalThis.window = globalThis;
globalThis.isSecureContext = true;
globalThis.location = { href: 'https://mirror.example/quotas/03_plotly/index.html' };
const origCopy = quotaCopyCommand;
quotaCopyCommand = function(...args) {
  copyCalls++;
  return origCopy.apply(this, args);
};
function stub(tag) {
  return {
    tag, style: {}, value: '', textContent: '',
    setAttribute() {}, select() {}, remove() {}, append() {},
    addEventListener() {},
    showModal() { dialogs.push(tag); },
    close() {},
    querySelector() { return stub('part'); },
  };
}
globalThis.document = {
  createElement: stub,
  body: { append() {} },
  execCommand() { return false; },
};
globalThis.fetch = () => Promise.reject(new Error('offline'));
const section = { querySelector() { return null; } };
const panel = { subscription: { provider: 'claude' }, checkout: '/tmp/ai-quotas', vendor: 'Claude' };
const button = () => ({ disabled: false, textContent: 'Check now' });
(async () => {
  const autoBtn = button();
  await quotaCheckNow(section, panel, autoBtn, { auto: true });
  const afterAuto = { text: autoBtn.textContent, disabled: autoBtn.disabled, copyCalls, dialogs: dialogs.length };
  let autoFlag = 'missing';
  const saved = quotaCheckNow;
  quotaCheckNow = (sec, p, btn, opts) => { autoFlag = !!(opts && opts.auto); return Promise.resolve(); };
  quotaMaybeAutoCheck(section, panel, { disabled: false, hidden: false });
  await new Promise(resolve => setTimeout(resolve, 500));
  quotaCheckNow = saved;
  const manualBtn = button();
  await quotaCheckNow(section, panel, manualBtn);
  await new Promise(resolve => setTimeout(resolve, 30));
  console.log(JSON.stringify({
    afterAuto, autoFlag,
    manual: { copyCalls, dialogs: dialogs.length, text: manualBtn.textContent },
  }));
})().catch(err => { console.error(err); process.exit(1); });
"""
    proc = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["afterAuto"] == {"text": "Check now", "disabled": False, "copyCalls": 0, "dialogs": 0}
    assert out["autoFlag"] is True
    assert out["manual"]["copyCalls"] == 1
    assert out["manual"]["dialogs"] >= 1


def test_cached_check_does_not_wait_for_a_regeneration():
    """A reused reading returns at once. The page does not poll meta.json."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    script = HEADER_JS.read_text(encoding="utf-8") + r"""
const urls = [];
globalThis.window = globalThis;
globalThis.location = { href: 'http://127.0.0.1:8765/03_plotly/index.html' };
globalThis.document = { querySelectorAll() { return []; } };
globalThis.fetch = (url, init) => {
  urls.push(String(url));
  if (init && init.method === 'POST') {
    return Promise.resolve({
      ok: true,
      json: async () => ({cached: true, rows: [{provider: 'claude', window: 'week', status: 'ok', used_percent: 10, account: 'a@x.com'}]}),
    });
  }
  return Promise.resolve({ok: true, json: async () => ({token: 't'})});
};
const section = { querySelector() { return {hidden: true, textContent: '', title: ''}; } };
const panel = { subscription: { provider: 'claude' }, vendor: 'Claude', account: 'a@x.com' };
const button = { disabled: false, textContent: 'Check now' };
(async () => {
  await quotaCheckNow(section, panel, button, {auto: true});
  console.log(JSON.stringify({urls, text: button.textContent}));
})().catch(err => { console.error(err); process.exit(1); });
"""
    proc = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["text"] == "Checked"
    assert not any("meta.json" in url for url in out["urls"])
    assert sum(1 for url in out["urls"] if url.endswith("/api/sample")) == 2


def test_stale_owned_login_offers_the_sign_in_command():
    now = 1_790_000_000 * 1000
    panel = _panel("claude", "Claude", sampled=1_790_000_000 - 4 * 3600, remaining=50, resets_at=None)
    relogin = "ai-quotas login claude --name work --move-to box:/srv/ai-quotas/logins"
    owned = {**panel, "source": {"account": "me@example.com", "relogin": relogin}}
    hostile = {**panel, "source": {"relogin": "ai-quotas login claude; rm -rf ~"}}
    got = _run_js([{"panel": owned, "now": now}, {"panel": hostile, "now": now}, {"panel": panel, "now": now}])
    assert got[0]["fix"] == f"cd '/tmp/ai-quotas' && uv run {relogin}"
    assert got[1]["fix"].startswith("cd '/tmp/ai-quotas' && grok ")  # not a valid command: agent prompt
    assert got[2]["fix"] == got[1]["fix"]


def test_gemini_panel_without_argon_reading_says_not_measured():
    """Petr, 19 Oct 2026: Argon is "the most important one"; silence hid it."""
    now = 1_800_000_000_000
    gem = _panel("agy", "Gemini · me@example.com", sampled=now / 1000, remaining=94, resets_at=now / 1000 + 3600)
    anti = _panel("agy", "Antigravity", sampled=now / 1000, remaining=94, resets_at=now / 1000 + 3600)
    gem_state, anti_state = _run_js([
        {"op": "state", "panel": gem, "now": now},
        {"op": "state", "panel": anti, "now": now},
    ])
    assert gem_state["modelLimit"] == "Argon · not measured" and gem_state["modelLimitMuted"]
    assert anti_state["modelLimit"] == "" and not anti_state["modelLimitMuted"]


def test_check_reads_only_the_panels_own_login():
    """A check samples every Claude login. calmmage.shared's panel said
    "Checked · week 43% remaining": that was petr.b.lavrov's week (19 Oct 2026)."""
    rows = [
        {"window": "unknown", "status": "unavailable", "account": "shared@example.com",
         "reason": "stored token for this login expired 17 Oct 2026 04:14 UTC; not refreshing (rotation would log Orca out)."},
        {"window": "week", "status": "ok", "used_percent": 57, "account": "me@example.com"},
    ]
    shared, mine, other, legacy, reason = _run_js([
        {"op": "summary", "rows": rows, "account": "Shared@example.com", "now": 0, "panel": {}},
        {"op": "summary", "rows": rows, "account": "me@example.com", "now": 0, "panel": {}},
        {"op": "summary", "rows": rows, "account": "nobody@example.com", "now": 0, "panel": {}},
        {"op": "summary", "rows": [{"window": "week", "status": "ok", "used_percent": 20}],
         "account": "me@example.com", "now": 0, "panel": {}},
        {"op": "reason", "reason": "HTTP 429; retry later", "now": 0, "panel": {}},
    ])
    assert shared == "Check failed · login expired 17 Oct"
    assert mine == "Checked · week 43% remaining"
    assert other == "Check failed · no reading for this login"
    assert legacy == "Checked · week 80% remaining"  # rows without accounts: older server
    assert reason == "HTTP 429"


def test_stale_panel_says_why_and_the_fix_carries_the_error():
    now = 1_790_000_000 * 1000
    panel = _panel("claude", "Claude · shared@example.com", sampled=1_790_000_000 - 50 * 3600,
                   remaining=50, resets_at=None)
    why = "stored token for this login expired 17 Oct 2026 04:14 UTC; not refreshing (rotation would log Orca out). Use this login once in Orca to refresh it."
    failing = {**panel, "source": {"account": "shared@example.com", "failing": why}}
    (state,) = _run_js([{"panel": failing, "now": now}])
    assert state["stale"] is True
    assert "Latest error: stored token for this login expired 17 Oct 2026" in state["fix"]
    assert "uv run ai-quotas login claude --name NAME" in state["fix"]
    assert "Run any claude command" not in state["fix"]
    assert "\n" not in state["fix"]


def test_used_reset_is_named_until_its_grant_expires():
    """Petr, 19 Oct 2026: "The reset on main account was already used"."""
    credit = {"available": 0, "status": "none", "items": [
        {"title": "Reset for free", "status": "consumed", "expires_at": "2026-10-22T21:59:59+00:00",
         "label": "reset redeemed +$45"}]}
    before = int(datetime(2026, 10, 19).timestamp() * 1000)
    after = int(datetime(2026, 10, 24).timestamp() * 1000)  # local midnight; grant ends 22 Oct 21:59Z
    used, gone = _run_js([
        {"op": "used", "credit": credit, "now": before, "panel": {}},
        {"op": "used", "credit": credit, "now": after, "panel": {}},
    ])
    assert used["label"] == "reset redeemed +$45"
    assert gone is None


def test_vendor_json_is_not_shown_raw():
    """19 Oct review: the Grok card read "Data stale · billing credits HTTP 503:
    {"code":"The service is currently…". The status line keeps what failed and
    the vendor's own words, never the JSON."""
    reasons = [
        'billing credits HTTP 503: {"code":"The service is currently unavailable",'
        '"error":"cursor billing: meter unavailable"}',
        'usage HTTP 429: {\n  "error": {\n    "type": "rate_limit_error",\n'
        '    "message": "Rate limited. Please try again later."\n  }\n}',
        'billing credits HTTP 400: {"code":"Unknown error","error":"transport error"}',
        'codexbar exit 1: [{"provider":"codex","source":"auto","error":{"kind":"provider",'
        '"message":"Codex connection failed: failed to fetch codex rate limits: error sending request"}}]',
        'quota: https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary: '
        '<urlopen error [Errno 8] nodename nor servname provided, or not known>; https://x',
        "HTTP 429; retry later",
        "auth/token: HTTP Error 400: Bad Request",
    ]
    got = _run_js([{"op": "reason", "reason": r, "now": 0, "panel": {}} for r in reasons])
    assert got[0] == "billing credits HTTP 503: The service is currently unavailable"
    assert got[1] == "usage HTTP 429: Rate limited. Please try again later."
    assert got[2] == "billing credits HTTP 400: Unknown error"
    assert got[3].startswith("codexbar exit 1: Codex connection failed")
    assert got[4] == "no network"
    assert got[5] == "HTTP 429"
    assert got[6] == "auth/token: HTTP Error 400: Bad Request"
    for line in got:
        assert "{" not in line and '"' not in line and len(line) <= 72
