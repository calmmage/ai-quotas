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
  if (c.op === 'summary') return quotaSampleSummary(c.rows);
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
