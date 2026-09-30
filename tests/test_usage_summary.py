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
    assert summary([{"t": 10, "used_pct": 25, "allocation_usd": 0}]) == {
        "percent": 25, "usd": 0, "periods": 1,
    }


def test_no_observation_is_unknown_instead_of_zero_usage():
    assert summary([]) == {"percent": None, "usd": None, "periods": 0}
    assert summary([{"t": 200, "used_pct": 90, "allocation_usd": 40}]) == {
        "percent": None, "usd": None, "periods": 0,
    }
