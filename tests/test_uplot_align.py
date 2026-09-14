"""uPlot shared-x alignment must not break a series at another series' timestamps."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

UPLOT = Path(__file__).resolve().parents[1] / "ai_quotas/plots/static/uplot.html"


def test_align_data_spans_other_series_timestamps():
    if not shutil.which("node"):
        pytest.skip("node not available")
    src = UPLOT.read_text(encoding="utf-8")
    start = src.index("function alignData(seriesData) {")
    end = src.index("\n}", start)
    fn = src[start : end + 3]
    script = (
        fn
        + """
const week = {t:[1,3], y:[80,70]};
const fiveh = {t:[1,2,3], y:[90,0,85]};
const [xs, a, b] = alignData([week, fiveh]);
console.log(JSON.stringify({
  xs, a, b,
  weekHoleIsUndefined: a[1] === undefined,
  weekHoleIsNull: a[1] === null,
  fivehMid: b[1],
}));
"""
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    assert payload["xs"] == [1, 2, 3]
    assert payload["a"][0] == 80
    assert payload["a"][2] == 70
    assert payload["weekHoleIsUndefined"] is True
    assert payload["weekHoleIsNull"] is False
    assert payload["fivehMid"] == 0


def test_align_data_keeps_explicit_nan_breaks():
    if not shutil.which("node"):
        pytest.skip("node not available")
    src = UPLOT.read_text(encoding="utf-8")
    start = src.index("function alignData(seriesData) {")
    end = src.index("\n}", start)
    fn = src[start : end + 3]
    script = (
        fn
        + """
const week = {t:[1,2,3], y:[80,null,70]};
const [xs, a] = alignData([week]);
console.log(JSON.stringify({a, isNull: a[1] === null}));
"""
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    assert payload["a"][0] == 80
    assert payload["isNull"] is True
    assert payload["a"][2] == 70
