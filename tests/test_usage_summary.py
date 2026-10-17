"""Usage estimates must respect visible periods and unknown/free pricing."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def summary(periods, start=0, end=100):
    if not shutil.which("node"):
        pytest.skip("node not available")
    src = (Path(__file__).resolve().parents[1] / "ai_quotas/plots/static/panel_header.js").read_text()
    data = json.dumps({"usage_periods": periods})
    script = src + f"\nconsole.log(JSON.stringify(quotaUsageSummary({data}, {start}, {end})));"
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


def test_consumed_quota_and_expired_credit_are_weighted_by_value():
    periods = [
        {"t": 10, "used_pct": 50, "allocation_usd": 40},
        {"t": 20, "used_pct": 0, "allocation_usd": 20},  # expired unused reset
        {"t": 30, "used_pct": 25, "allocation_usd": 40, "active": True},
    ]
    assert summary(periods) == {"percent": 30, "usd": 30, "periods": 3}
    assert summary(periods, 25, 35) == {"percent": 25, "usd": 10, "periods": 1}


def test_unknown_price_reports_usage_without_inventing_dollars():
    assert summary([{"t": 10, "used_pct": 25, "allocation_usd": None}]) == {
        "percent": 25, "usd": None, "periods": 1,
    }
    # A $0 allocation is a free window, not a claim that the user spent $0.
    assert summary([{"t": 10, "used_pct": 25, "allocation_usd": 0}]) == {
        "percent": 25, "usd": None, "periods": 1,
    }


def test_mismatched_period_stays_out_of_the_dollar():
    """A sliding open window keeps its percent and does not inherit the closed week's price."""
    mixed = summary([
        {"t": 10, "used_pct": 100, "allocation_usd": 4, "period_ok": True},
        {"t": 20, "used_pct": 50, "allocation_usd": 4, "period_ok": False, "active": True},
    ])
    assert mixed == {"percent": 50, "usd": None, "periods": 1}
    # A mismatched row that is not the open window stays out of a priced average.
    closed_only = summary([
        {"t": 10, "used_pct": 100, "allocation_usd": 4, "period_ok": True},
        {"t": 20, "used_pct": 50, "allocation_usd": 4, "period_ok": False},
    ])
    assert closed_only == {"percent": 100, "usd": 4, "periods": 1}
    only_sliding = summary([
        {"t": 20, "used_pct": 0, "allocation_usd": 0, "period_ok": False},
    ])
    assert only_sliding == {"percent": 0, "usd": None, "periods": 1}


def _labels(script_tail: str):
    if not shutil.which("node"):
        pytest.skip("node not available")
    src = (Path(__file__).resolve().parents[1] / "ai_quotas/plots/static/panel_header.js").read_text()
    raw = subprocess.run(["node", "-e", src + "\n" + script_tail], capture_output=True, text=True, check=True).stdout
    return raw.strip()


def test_zero_and_free_labels_do_not_claim_dollars():
    assert _labels("console.log(quotaLossHtml(0, 0, true, true));") == "Free plan"
    assert _labels("console.log(quotaLossHtml(0, 0, true, false));") == "Nothing lost"
    assert _labels("console.log(quotaLossHtml(0, 1, false, false));") == "Loss value unknown"
    usage = _labels('console.log(quotaUsageHtml({percent: 75, usd: null}));')
    assert usage == "75% plan utilisation"
    assert "$" not in usage
    paid = _labels('console.log(quotaUsageHtml({percent: 100, usd: 4}));')
    assert "$4 used effectively" in paid
    assert "$0" not in paid


def test_no_observation_is_unknown_instead_of_zero_usage():
    assert summary([]) == {"percent": None, "usd": None, "periods": 0}
    assert summary([{"t": 200, "used_pct": 90, "allocation_usd": 40}]) == {
        "percent": None, "usd": None, "periods": 0,
    }
