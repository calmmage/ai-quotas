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
    ("Google AI Ultra", "", "Gemini Ultra"),
    ("Google AI Pro", "Antigravity Pro", "Gemini Pro"),
    ("Google AI Plus", "Antigravity Plus", "Gemini Plus"),
    ("Antigravity Starter Quota", "Antigravity Starter Quota", "Gemini Free"),
    ("free", "", "Gemini Free"),
    ("", "Plan not reported", "Gemini"),
])
def test_panel_title_tiers(plan, label, expected):
    assert panel_title("Gemini", {
        "provider": "agy", "plan": plan, "label": label,
    }) == expected


def test_antigravity_row_keeps_its_own_title():
    assert panel_title("Antigravity", {
        "provider": "agy", "plan": "Google AI Pro", "label": "Antigravity Pro",
    }) == "Antigravity Pro"


def test_extra_login_legend_drops_the_account_suffix():
    """The series key stays 'Gemini week · email'. Legend and trace names do not."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    root = Path(__file__).resolve().parents[1] / "ai_quotas" / "plots" / "static"
    plotly = (root / "plotly.html").read_text(encoding="utf-8")
    uplot = (root / "uplot.html").read_text(encoding="utf-8")
    assert "name: quotaSeriesLegend(p, s.label)" in plotly
    assert "<b>${s.label}</b>" in plotly
    assert "quotaSeriesLegend(chart.panel, s.label)" in uplot
    assert "quotaSeriesLegend(p, s.label)" in uplot
    assert "${s.label} ${Number(y).toFixed(1)}%" in uplot
    js = (root / "panel_header.js").read_text(encoding="utf-8")
    script = js + """
const email = 'calmmage.shared@gmail.com';
const panel = { account: email };
const cases = [
  [panel, 'Gemini week · ' + email, 'Gemini week'],
  [panel, 'Claude+GPT week · ' + email, 'Claude+GPT week'],
  [panel, 'Codex week · ' + email, 'Codex week'],
  [panel, 'Claude week · ' + email, 'Claude week'],
  [{}, 'Gemini week', 'Gemini week'],
  [panel, 'Budget', 'Budget'],
];
const bad = cases.filter(([p, label, want]) => quotaSeriesLegend(p, label) !== want);
console.log(JSON.stringify(bad));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10, check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []


def test_header_js_titles_match():
    if not shutil.which("node"):
        pytest.skip("node not available")
    js = Path(__file__).resolve().parents[1].joinpath(
        "ai_quotas", "plots", "static", "panel_header.js"
    ).read_text(encoding="utf-8")
    script = js + """
const cases = [
  [{vendor: 'Gemini', subscription: {provider: 'agy', plan: 'Google AI Ultra', label: ''}}, 'Gemini Ultra'],
  [{vendor: 'Gemini', subscription: {provider: 'agy', plan: 'Google AI Pro', label: 'Antigravity Pro'}}, 'Gemini Pro'],
  [{vendor: 'Gemini · plus@example.com', subscription: {provider: 'agy', plan: 'Google AI Plus', label: 'Antigravity Plus'}}, 'Gemini Plus'],
  [{vendor: 'Gemini', subscription: {provider: 'agy', plan: 'Antigravity Starter Quota', label: 'Antigravity Starter Quota'}}, 'Gemini Free'],
  [{vendor: 'Gemini', subscription: {provider: 'agy', plan: '', label: 'Plan not reported'}}, 'Gemini'],
  [{vendor: 'Antigravity', subscription: {provider: 'agy', plan: 'Google AI Pro', label: 'Antigravity Pro'}}, 'Antigravity Pro'],
  [{vendor: 'Antigravity', subscription: {provider: 'agy', plan: 'Antigravity Starter Quota', label: 'Antigravity Free'}}, 'Antigravity Free'],
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
    other_agy = f"Antigravity · {PLUS}"
    assert other in by and "Antigravity" in by and other_agy in by
    assert all(panel["provider"] != "antigravity" for panel in payload["panels"])
    expectations = {
        "Gemini": ("Gemini Pro", ["Gemini week", "Gemini 5h"]),
        "Antigravity": ("Antigravity Pro", ["Claude+GPT week", "Claude+GPT 5h"]),
        other: ("Gemini Plus", [f"Gemini week · {PLUS}", f"Gemini 5h · {PLUS}"]),
        other_agy: ("Antigravity Plus", [f"Claude+GPT week · {PLUS}", f"Claude+GPT 5h · {PLUS}"]),
    }
    for vendor, (title, labels) in expectations.items():
        panel = by[vendor]
        assert panel["title"] == title
        assert panel["provider"] == "agy"
        assert [series["label"] for series in panel["series"]] == labels
    colors = {series["label"]: series["color"] for series in by["Gemini"]["series"]}
    assert colors["Gemini week"] == COLORS["Gemini week"]
    agy_colors = {series["label"]: series["color"] for series in by["Antigravity"]["series"]}
    assert agy_colors["Claude+GPT week"] == "#2E9C93"
    assert "antigravity" in by["Gemini"]["distinguish"]
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


def test_plan_utilisation_averages_both_agy_week_buckets(tmp_path):
    """Gemini week and Claude+GPT week each take half the allocation.

    20% and 50% used then average to 35%. A renewal's lost value is split
    the same way. The 5h buckets stay out of the plan figure.
    """
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots
    from ai_quotas.plots.prep import reset_account_registry, window_usd_value

    reset_account_registry()
    stamps = (
        ("2026-10-09T10:00:00+00:00", 80.0, 40.0),
        ("2026-10-09T11:00:00+00:00", 0.0, 0.0),
        # More than MAX_SAMPLE_GAP after the trough, so the refill is not a glitch.
        ("2026-10-09T15:00:00+00:00", 20.0, 50.0),
    )

    def login(account, plan, default):
        rows = []
        for ts, gemini_used, claude_used in stamps:
            for window in GEMINI_WINDOWS:
                used = gemini_used if window.startswith("week") else gemini_used / 2
                rows.append(_row(ts, "agy", window, used, account=account, plan=plan, default=default))
            for window in CLAUDE_WINDOWS:
                used = claude_used if window.startswith("week") else claude_used / 2
                rows.append(_row(ts, "agy", window, used, account=account, plan=plan, default=default))
        return rows

    try:
        samples = _write(tmp_path, login(PRO, "Google AI Pro", True) + login(PLUS, "Google AI Plus", False))
        payload = json.loads(generate_plots(
            samples=samples, out_dir=tmp_path / "plots", engines=(),
        )["panels"].read_text(encoding="utf-8"))
        by = {panel["vendor"]: panel for panel in payload["panels"]}
        full = window_usd_value("Gemini week", "Gemini", plan="Google AI Pro", config={})[0]
        gemini = by["Gemini"]
        agy = by["Antigravity"]
        g_active = [period for period in gemini["usage_periods"] if period["active"]]
        a_active = [period for period in agy["usage_periods"] if period["active"]]
        assert [period["used_pct"] for period in g_active] == [20.0]
        assert [period["used_pct"] for period in a_active] == [50.0]
        assert g_active[0]["allocation_usd"] == pytest.approx(full / 2, rel=1e-2)
        assert a_active[0]["allocation_usd"] == pytest.approx(full / 2, rel=1e-2)
        active = g_active + a_active
        weighted = sum(period["used_pct"] / 100 * period["allocation_usd"] for period in active)
        budget = sum(period["allocation_usd"] for period in active)
        assert weighted / budget * 100 == pytest.approx(35.0)
        assert budget == pytest.approx(full, rel=1e-2)

        g_closed = [period for period in gemini["usage_periods"] if not period["active"]]
        a_closed = [period for period in agy["usage_periods"] if not period["active"]]
        assert [period["used_pct"] for period in g_closed] == [80.0]
        assert [period["used_pct"] for period in a_closed] == [40.0]
        g_loss = [event for event in gemini["underutilised"]["events"] if event["kind"] == "quota_reset"]
        a_loss = [event for event in agy["underutilised"]["events"] if event["kind"] == "quota_reset"]
        assert len(g_loss) == 1 and len(a_loss) == 1
        assert g_loss[0]["usd"] == pytest.approx(round(round(20.0 / 100 * full, 4) * 0.5, 4))
        assert a_loss[0]["usd"] == pytest.approx(round(round(60.0 / 100 * full, 4) * 0.5, 4))

        other = by[f"Gemini · {PLUS}"]
        assert [series["label"] for series in other["series"]] == [f"Gemini week · {PLUS}", f"Gemini 5h · {PLUS}"]
        other_agy = by[f"Antigravity · {PLUS}"]
        other_active = [period for period in other["usage_periods"] if period["active"]]
        other_agy_active = [period for period in other_agy["usage_periods"] if period["active"]]
        assert [period["used_pct"] for period in other_active] == [20.0]
        assert [period["used_pct"] for period in other_agy_active] == [50.0]
        plus = window_usd_value("Gemini week", "Gemini", plan="Google AI Plus", config={})[0]
        assert other_active[0]["allocation_usd"] == pytest.approx(plus / 2, rel=1e-2)
        assert other_agy_active[0]["allocation_usd"] == pytest.approx(plus / 2, rel=1e-2)
    finally:
        reset_account_registry()


def test_other_login_line_lists_only_the_two_buckets(tmp_path):
    from ai_quotas.cli import account_source_lines

    lines = account_source_lines(_both_buckets(tmp_path), ["agy"])
    other = next(line for line in lines if PLUS in line)
    assert "Gemini week 88% left" in other
    assert "Claude+GPT week 60% left" in other
    assert "flash" not in other and "sonnet" not in other and "worst" not in other
