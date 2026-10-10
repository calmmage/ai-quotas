"""Antigravity panels: two buckets per login, Orca duplicate hidden when agy is ok."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_quotas import core
from ai_quotas.cli import table_rows
from ai_quotas.plots.generate import panel_title
from ai_quotas.subscriptions import resolve

PRO = "pro@example.com"
PLUS = "plus@example.com"
TS = ("2026-10-09T12:00:00+00:00", "2026-10-10T12:00:00+00:00")
GEMINI_WINDOWS = (
    "week_gemini_flash",
    "week_gemini_pro",
    "5h_gemini_flash",
    "5h_gemini_pro",
)
CLAUDE_WINDOWS = (
    "week_gpt",
    "week_opus",
    "week_sonnet",
    "5h_gpt",
    "5h_opus",
    "5h_sonnet",
)
DRAWN = ("Gemini week", "Gemini 5h", "Claude+GPT week", "Claude+GPT 5h")


def _row(ts, provider, window, used, *, account, plan, default=False, status="ok"):
    return {
        "ts": ts,
        "provider": provider,
        "window": window,
        "used_percent": used,
        "resets_at": "2026-10-16T12:00:00+00:00",
        "plan": plan,
        "status": status,
        "reason": None if status == "ok" else "down",
        "account": account,
        "device": "example-mac",
        "account_default": default,
    }


def _agy_pair(account, plan, *, default, gemini_used, claude_used):
    rows = []
    for ts in TS:
        for window in GEMINI_WINDOWS:
            used = gemini_used if window.startswith("week") else gemini_used / 2
            rows.append(_row(ts, "agy", window, used, account=account, plan=plan, default=default))
        for window in CLAUDE_WINDOWS:
            used = claude_used if window.startswith("week") else claude_used / 2
            rows.append(_row(ts, "agy", window, used, account=account, plan=plan, default=default))
    return rows


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _both_buckets(tmp_path: Path) -> Path:
    rows = _agy_pair(PRO, "Google AI Pro", default=True, gemini_used=30.0, claude_used=55.0)
    rows += _agy_pair(PLUS, "Google AI Plus", default=False, gemini_used=12.0, claude_used=40.0)
    rows.append(_row(
        TS[-1], "antigravity", "week", 70.0, account=PRO, plan="Google AI Pro", default=True,
    ))
    return _write(tmp_path, rows)


def test_subscription_labels_say_antigravity():
    pro = resolve("agy", "Google AI Pro", {})
    plus = resolve("agy", "Google AI Plus", {})
    assert (pro["label"], pro["monthly_usd"]) == ("Antigravity Pro", 20.0)
    assert (plus["label"], plus["monthly_usd"]) == ("Antigravity Plus", 10.0)


@pytest.mark.parametrize("plan,label,expected", [
    ("Google AI Ultra", "", "Antigravity Ultra"),
    ("Google AI Pro", "Antigravity Pro", "Antigravity Pro"),
    ("Google AI Plus", "Antigravity Plus", "Antigravity Plus"),
    ("Antigravity Starter Quota", "Antigravity Starter Quota", "Antigravity Free"),
    ("free", "", "Antigravity Free"),
    ("", "Plan not reported", "Antigravity"),
])
def test_panel_title_tiers(plan, label, expected):
    assert panel_title("Gemini", {
        "provider": "agy", "plan": plan, "label": label,
    }) == expected


def test_header_js_titles_match():
    if not shutil.which("node"):
        pytest.skip("node not available")
    js = Path(__file__).resolve().parents[1].joinpath(
        "ai_quotas", "plots", "static", "panel_header.js"
    ).read_text(encoding="utf-8")
    script = js + """
const cases = [
  [{vendor: 'Gemini', subscription: {provider: 'agy', plan: 'Google AI Ultra', label: ''}}, 'Antigravity Ultra'],
  [{vendor: 'Gemini', subscription: {provider: 'agy', plan: 'Google AI Pro', label: 'Antigravity Pro'}}, 'Antigravity Pro'],
  [{vendor: 'Gemini · plus@example.com', subscription: {provider: 'agy', plan: 'Google AI Plus', label: 'Antigravity Plus'}}, 'Antigravity Plus'],
  [{vendor: 'Gemini', subscription: {provider: 'agy', plan: 'Antigravity Starter Quota', label: 'Antigravity Starter Quota'}}, 'Antigravity Free'],
  [{vendor: 'Gemini', subscription: {provider: 'agy', plan: '', label: 'Plan not reported'}}, 'Antigravity'],
];
const bad = cases.filter(([panel, title]) => quotaDisplayTitle(panel) !== title);
console.log(JSON.stringify(bad.map(([panel, title]) => [quotaDisplayTitle(panel), title])));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10, check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []


def test_each_agy_login_draws_both_buckets_and_hides_orca(tmp_path):
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots
    from ai_quotas.plots.prep import COLORS, reset_account_registry

    reset_account_registry()
    samples = _both_buckets(tmp_path)
    out = tmp_path / "plots"
    generate_plots(samples=samples, out_dir=out, engines=())
    payload = json.loads((out / "panels.json").read_text(encoding="utf-8"))
    by = {panel["vendor"]: panel for panel in payload["panels"]}
    other = f"Gemini · {PLUS}"
    assert other in by
    assert "Antigravity" not in by
    assert all(panel["provider"] != "antigravity" for panel in payload["panels"])
    for vendor, title in (("Gemini", "Antigravity Pro"), (other, "Antigravity Plus")):
        panel = by[vendor]
        assert panel["title"] == title
        assert panel["provider"] == "agy"
        labels = [series["label"] for series in panel["series"]]
        if vendor == "Gemini":
            assert labels == list(DRAWN)
        else:
            assert set(labels) == {f"{name} · {PLUS}" for name in DRAWN}
        colors = {series["label"]: series["color"] for series in panel["series"]}
        week = "Gemini week" if vendor == "Gemini" else f"Gemini week · {PLUS}"
        other_week = "Claude+GPT week" if vendor == "Gemini" else f"Claude+GPT week · {PLUS}"
        assert colors[week] == COLORS["Gemini week"]
        assert colors[other_week] == "#2E9C93"
    reset_account_registry()


def test_orca_panel_stays_without_an_ok_agy_row(tmp_path):
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots
    from ai_quotas.plots.prep import reset_account_registry

    reset_account_registry()
    only_orca = _write(tmp_path, [
        _row(TS[-1], "antigravity", "week", 70.0, account=PRO, plan="Google AI Pro", default=True),
    ])
    payload = json.loads(generate_plots(
        samples=only_orca, out_dir=tmp_path / "orca", engines=(),
    )["panels"].read_text(encoding="utf-8"))
    by = {panel["vendor"]: panel for panel in payload["panels"]}
    assert by["Antigravity"]["configured"] is True
    assert [series["label"] for series in by["Antigravity"]["series"]] == ["Antigravity week"]

    broken = tmp_path / "broken"
    broken.mkdir()
    agy_down = _write(broken, [
        _row(TS[-1], "agy", "week_gemini_pro", None, account=PRO, plan="Google AI Pro",
             default=True, status="error"),
        _row(TS[-1], "antigravity", "week", 70.0, account=PRO, plan="Google AI Pro", default=True),
    ])
    payload = json.loads(generate_plots(
        samples=agy_down, out_dir=broken / "plots", engines=(),
    )["panels"].read_text(encoding="utf-8"))
    assert any(panel["vendor"] == "Antigravity" and panel["configured"] for panel in payload["panels"])
    reset_account_registry()


def test_cli_table_shows_both_buckets_and_drops_orca(tmp_path):
    samples = _both_buckets(tmp_path)
    rows = table_rows(full=False, path=samples)
    agy = [row for row in rows if row.provider == "agy"]
    assert {row.title for row in agy} == set(DRAWN)
    assert {row.window for row in agy} == {
        "5h_gemini_pro", "week_gemini_pro", "5h_opus", "week_opus",
    }
    assert all(row.provider != "antigravity" for row in rows)
    full = table_rows(full=True, path=samples)
    assert all(row.provider != "antigravity" for row in full)
    assert any(row.title == "Gemini Flash week" for row in full)


def test_verdicts_drop_orca_only_when_agy_is_ok(tmp_path):
    hidden = core.evaluate(core.load_samples(_both_buckets(tmp_path)))
    assert "antigravity" not in hidden["verdicts"]
    assert "agy" in hidden["verdicts"]
    assert all(row["provider"] != "antigravity" for row in hidden["windows"])

    kept = core.evaluate([
        _row(TS[-1], "antigravity", "week", 70.0, account=PRO, plan="Google AI Pro", default=True),
    ])
    assert "antigravity" in kept["verdicts"]
    assert kept["verdicts"]["antigravity"]["window"] == "week"
