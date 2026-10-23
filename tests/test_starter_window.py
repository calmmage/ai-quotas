"""Starter and zero-leftover windows must not print a contradictory dollar."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PRO = "pro@example.com"
STARTER = "starter@example.com"


def _row(ts, used, plan, resets_at, account):
    return {
        "ts": ts,
        "provider": "agy",
        "window": "week_opus",
        "used_percent": used,
        "resets_at": resets_at,
        "plan": plan,
        "status": "ok",
        "reason": None,
        "account": account,
        "device": "example-mac",
        "account_default": True,
    }


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _panel(tmp_path: Path, rows: list[dict]) -> dict:
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots
    from ai_quotas.plots.prep import reset_account_registry

    reset_account_registry()
    try:
        payload = json.loads(generate_plots(
            samples=_write(tmp_path, rows), out_dir=tmp_path / "plots", engines=(),
        )["panels"].read_text(encoding="utf-8"))
    finally:
        reset_account_registry()
    panels = [p for p in payload["panels"] if p["vendor"].startswith("Antigravity")]
    assert len(panels) == 1
    return panels[0]


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_zero_leftover_on_a_priced_week_is_not_a_zero_dollar_pill(tmp_path):
    """100% used → 0% used leaves nothing. '$0 unused' reads as a free window."""
    first = datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc)
    second = first + timedelta(hours=1)
    rows = [
        _row(_iso(first), 100.0, "Google AI Pro", _iso(first + timedelta(minutes=30)), PRO),
        _row(_iso(second), 0.0, "Google AI Pro", _iso(first + timedelta(minutes=30, days=7)), PRO),
    ]
    panel = _panel(tmp_path, rows)
    pills = [mark["pill"] for mark in panel["resets"]]
    tips = [mark["tooltip"] for mark in panel["resets"]]
    assert "Fully used" in pills
    assert not any("$0" in text for text in pills + tips)
    assert not any(text.startswith("$") for text in pills)


def test_missing_price_stays_unknown(tmp_path):
    first = datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc)
    second = first + timedelta(hours=1)
    rows = [
        _row(_iso(first), 80.0, "not-a-listed-plan", _iso(first + timedelta(days=7)), PRO),
        _row(_iso(second), 10.0, "not-a-listed-plan", _iso(first + timedelta(days=7)), PRO),
    ]
    panel = _panel(tmp_path, rows)
    assert panel["subscription"]["monthly_usd"] is None
    active = [period for period in panel["usage_periods"] if period["active"]]
    assert active and active[0]["allocation_usd"] is None
    assert not any("$" in mark["pill"] for mark in panel["resets"])


def test_sliding_starter_window_is_not_priced_as_a_week(tmp_path):
    """Starter republishes now+7d on every sample. That is not one closed week."""
    start = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
    rows = []
    for step in range(6):
        when = start + timedelta(hours=step)
        rows.append(_row(
            _iso(when), 0.0, "Antigravity Starter Quota", _iso(when + timedelta(days=7)), STARTER,
        ))
    panel = _panel(tmp_path, rows)
    assert panel["subscription"]["monthly_usd"] == 0
    active = [period for period in panel["usage_periods"] if period["active"]]
    assert len(active) == 1
    assert active[0]["period_ok"] is False
    assert active[0]["allocation_usd"] is None
    assert active[0]["used_pct"] == 0
    blob = json.dumps(panel["resets"]) + json.dumps(panel["usage_periods"])
    assert "$0" not in blob


def test_chart_assets_are_local_and_day_text_clears_white(tmp_path):
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots

    out = tmp_path / "plots"
    # Reuse the zero-leftover rows so generate has something to draw.
    first = datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc)
    rows = [
        _row(_iso(first), 10.0, "Google AI Pro", _iso(first + timedelta(days=7)), PRO),
        _row(_iso(first + timedelta(hours=1)), 12.0, "Google AI Pro", _iso(first + timedelta(days=7)), PRO),
    ]
    from ai_quotas.plots.prep import reset_account_registry
    reset_account_registry()
    try:
        generate_plots(samples=_write(tmp_path, rows), out_dir=out, engines=("plotly", "uplot"))
    finally:
        reset_account_registry()
    plotly = (out / "03_plotly" / "index.html").read_text(encoding="utf-8")
    uplot = (out / "10_uplot" / "index.html").read_text(encoding="utf-8")
    for text in (plotly, uplot):
        assert "cdn.plot.ly" not in text
        assert "jsdelivr" not in text
    assert "../vendor/plotly-basic-2.35.2.min.js" in plotly
    assert "../vendor/uPlot.iife.min.js" in uplot
    assert "../vendor/uPlot.min.css" in uplot
    vendor = out / "vendor"
    plotly_js = (vendor / "plotly-basic-2.35.2.min.js").read_text(encoding="utf-8")
    assert "v2.35.2" in plotly_js[:240]
    assert "MIT" in plotly_js[:240]
    assert (vendor / "plotly-basic-2.35.2.LICENSE").read_text(encoding="utf-8").startswith("The MIT License")
    uplot_js = (vendor / "uPlot.iife.min.js").read_text(encoding="utf-8")
    assert "v1.6.31" in uplot_js[:80]
    assert (vendor / "uPlot.LICENSE").read_text(encoding="utf-8").startswith("The MIT License")
    # The day shell's old #888 / #777 text failed 4.5:1 on white.
    # border-color:#8888 is an alpha border, not that text color.
    assert not re.search(r"(?<![\w-])color:#888\b", plotly)
    assert not re.search(r"(?<![\w-])color:#777\b", plotly)
    assert "color:#4a4a4a" in plotly
    root = Path(__file__).resolve().parents[1] / "ai_quotas/plots/static"
    layout = (root / "layout.css").read_text(encoding="utf-8")
    assert "flex-wrap: nowrap" in layout
    assert "max-width: 9.5rem" in layout
    header = (root / "panel_header.js").read_text(encoding="utf-8")
    assert "Nothing lost" in header
    assert "No unused quota left" not in header
