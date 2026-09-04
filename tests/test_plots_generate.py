"""Integration: real generate_plots writes dashboards (shipped entry)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pandas")

from importlib.resources import files  # noqa: E402

from ai_quotas.plots.generate import generate_plots  # noqa: E402


def test_plot_templates_are_package_resources():
    static = files("ai_quotas.plots.static")
    for name in ("plotly.html", "uplot.html", "index.html", "time_axis.js", "theme.js"):
        target = static.joinpath(name)
        assert target.is_file(), name
        text = target.read_text(encoding="utf-8")
        assert text.strip()
        if name.endswith(".html"):
            assert "<!DOCTYPE html>" in text


def test_generate_plots_writes_index_and_engines(tmp_path):
    fixture = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
    out = tmp_path / "plots"
    result = generate_plots(samples=fixture, out_dir=out, engines=("plotly", "uplot"))
    assert result["index"].is_file()
    assert result["money"].is_file()
    assert (out / "03_plotly" / "index.html").is_file()
    assert (out / "10_uplot" / "index.html").is_file()
    html = (out / "03_plotly" / "index.html").read_text(encoding="utf-8")
    # multi-vendor single page + cols control
    assert "plots / row" in html or "plots/row" in html or "data-cols" in html
    assert "Claude" in html or "panels" in html
    assert "data-cols" in html
    assert 'data-theme="day"' in html
    assert 'data-theme="night"' in html
    assert "00_INDEX.html" not in html  # no version-catalog link: one final page (petr, 04 Sep 2026)
    uplot = (out / "10_uplot" / "index.html").read_text(encoding="utf-8")
    assert "data-cols" in uplot
    assert 'data-theme="night"' in uplot
    assert "00_INDEX.html" not in uplot
    assert "Quota remaining <span class=\"info\"" in uplot and ".hint" not in uplot
    assert result["n_rows"] > 0
    assert "function timeAxis" in html
    assert "function timeAxis" in uplot
    assert "quota-theme" in html
    assert "quota-theme" in uplot
    assert "__TIME_AXIS_JS__" not in html
    assert "__TIME_AXIS_JS__" not in uplot
    assert "__THEME_JS__" not in html
    assert "__THEME_JS__" not in uplot
    for token in ("__PANELS__", "__CUTOFF__", "__BURN_W__", "__BURN_A__"):
        assert token not in html
        assert token not in uplot
    # 5h series may still be drawn; reset marks must not mention it
    from ai_quotas.plots.generate import _vendor_panel_payload
    from ai_quotas.plots.prep import prepare

    df, resets, _ = prepare(fixture)
    payload = _vendor_panel_payload(df, resets, "Claude")
    assert all("5h" not in r["label"].lower() for r in payload["resets"])
    fiveh = [s for s in payload["series"] if "5h" in s["label"]]
    if fiveh:
        assert all(s["dim"] for s in fiveh)
        assert all(s["window_usd"] == 0 for s in fiveh)
    week = [s for s in payload["series"] if s["label"] == "Claude week"]
    if week:
        assert not week[0]["dim"]
    assert "quota-tip" in uplot
    assert "setCursor" in uplot
    assert "remaining" in uplot
    assert "isDim(s)" in html
    assert "isDim(s)" in uplot
    assert "mixHex" in uplot
    assert "0.22" in uplot
    index = result["index"].read_text(encoding="utf-8")
    assert "Daily spend" in index
    assert "__SPEND_ROWS__" not in index


def test_time_axis_tick_density():
    """1w / 1m / 1q must not emit a label per day."""
    import json
    import shutil
    import subprocess

    from ai_quotas.plots.generate import TIME_AXIS_JS

    if not shutil.which("node"):
        pytest.skip("node not available")
    script = (
        TIME_AXIS_JS
        + """
const week = timeAxis(0, 7*86400);
const month = timeAxis(0, 30*86400);
const quarter = timeAxis(0, 90*86400);
console.log(JSON.stringify({
  week: week.majors.length,
  month: month.majors.length,
  quarter: quarter.majors.length,
  weekFmt: week.fmt(week.majors[0] || 0),
  monthFmt: month.fmt(month.majors[0] || 0),
}));
"""
    )
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    counts = json.loads(proc.stdout)
    assert 6 <= counts["week"] <= 10
    assert 4 <= counts["month"] <= 8
    assert 5 <= counts["quarter"] <= 10
