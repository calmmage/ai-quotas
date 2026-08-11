"""Integration: real generate_plots writes dashboards (shipped entry)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pandas")

from ai_quotas.plots.generate import generate_plots  # noqa: E402


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
    uplot = (out / "10_uplot" / "index.html").read_text(encoding="utf-8")
    assert "data-cols" in uplot
    assert result["n_rows"] > 0
