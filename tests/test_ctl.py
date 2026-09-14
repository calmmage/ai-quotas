"""scripts/ctl.sh — make start/stop/restart/deploy."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CTL = REPO / "scripts" / "ctl.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CTL), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def test_ctl_usage_lists_the_four_commands():
    r = _run()
    assert r.returncode == 2
    text = r.stdout + r.stderr
    for word in ("start", "stop", "restart", "deploy"):
        assert word in text


def test_ctl_help_ok():
    r = _run("--help")
    assert r.returncode == 0
    assert "restart" in r.stdout


def test_ctl_unknown_arg():
    r = _run("explode")
    assert r.returncode == 2


def test_ctl_dry_run_restart_prints_launchctl_or_macos_hint():
    r = _run("restart", "--dry-run")
    text = r.stdout + r.stderr
    assert r.returncode in (0, 1)
    assert "kickstart" in text or "bootstrap" in text or "macOS" in text or "install-automation" in text
