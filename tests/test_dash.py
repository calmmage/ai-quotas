"""dash subcommand: help, generate+serve smoke, no hung threads."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from ai_quotas.cli import build_parser
from ai_quotas.plots.dash import (
    INDEX_NAME,
    LIVE_NAME,
    REFRESH_MARK,
    inject_meta_refresh,
    make_server,
    samples_mtime,
    write_live_page,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"


def _dash_parser():
    ap = build_parser()
    for action in ap._subparsers._group_actions:
        if "dash" in getattr(action, "choices", {}):
            return action.choices["dash"]
    raise AssertionError("dash subparser missing")


def test_dash_is_subparser():
    ap = build_parser()
    args = ap.parse_args(["dash", "--port", "9001", "--interval", "20"])
    assert args.command == "dash"
    assert args.port == 9001
    assert args.interval == 20
    option_strings = [
        s for a in _dash_parser()._actions for s in a.option_strings
    ]
    assert "--samples" not in option_strings
    assert "--port" in option_strings
    assert "--interval" in option_strings
    assert "--open" in option_strings
    assert "--engine" in option_strings


def test_dash_help():
    proc = subprocess.run(
        [sys.executable, "-m", "ai_quotas", "dash", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "--port" in out
    assert "--interval" in out
    assert "--open" in out
    assert "--engine" in out
    assert "--out" in out


def test_dash_help_lists_on_root():
    proc = subprocess.run(
        [sys.executable, "-m", "ai_quotas", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "dash" in proc.stdout
    assert "ai-quotas dash --open" in proc.stdout


def test_plot_html_has_no_dash_refresh(tmp_path):
    """Standalone plot output must not carry dash meta-refresh."""
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots

    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly",))
    html = (out / INDEX_NAME).read_text(encoding="utf-8")
    assert REFRESH_MARK not in html
    assert "http-equiv=\"refresh\"" not in html.lower()


def test_inject_meta_refresh_and_live_page(tmp_path):
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots

    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly",))
    write_live_page(out, interval=15)
    inject_meta_refresh(out, 15)
    index = (out / INDEX_NAME).read_text(encoding="utf-8")
    assert REFRESH_MARK in index
    assert 'http-equiv="refresh" content="15"' in index
    plotly = (out / "03_plotly" / "index.html").read_text(encoding="utf-8")
    assert REFRESH_MARK in plotly
    live = (out / LIVE_NAME).read_text(encoding="utf-8")
    assert "03_plotly/index.html" in live
    assert "10_uplot/index.html" in live
    assert "quota-theme" in live
    assert INDEX_NAME not in live
    assert "iframe" in live
    # re-inject replaces, does not stack
    inject_meta_refresh(out, 30)
    index2 = (out / INDEX_NAME).read_text(encoding="utf-8")
    assert index2.count(REFRESH_MARK) == 1
    assert 'content="30"' in index2


def test_dash_serve_smoke(tmp_path):
    """Generate + serve on an ephemeral port, fetch index HTML, stop cleanly."""
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots

    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly",))
    write_live_page(out, interval=15)
    inject_meta_refresh(out, 15)

    httpd = make_server(out, 0)
    thread = threading.Thread(target=httpd.serve_forever, name="dash-smoke", daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        assert host == "127.0.0.1"
        assert port != 0
        base = f"http://{host}:{port}"
        with urllib.request.urlopen(base + "/", timeout=5) as resp:
            # / redirects to live.html
            assert resp.status == 200
            live = resp.read().decode("utf-8", errors="replace")
        assert "03_plotly/index.html" in live
        assert "10_uplot/index.html" in live
        with urllib.request.urlopen(base + f"/{INDEX_NAME}", timeout=5) as resp:
            index = resp.read().decode("utf-8", errors="replace")
        assert "ai-quotas" in index
        assert "Dashboards" in index or "plotly" in index.lower()
        assert REFRESH_MARK in index
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_samples_mtime_missing(tmp_path):
    assert samples_mtime(tmp_path / "nope.jsonl") is None
    p = tmp_path / "samples.jsonl"
    p.write_text("{}\n", encoding="utf-8")
    assert samples_mtime(p) is not None


def test_install_dry_run_forwards_env():
    script = REPO / "scripts" / "install-launchagent.sh"
    env = {
        **os.environ,
        "AI_QUOTAS_DATABASE": "/tmp/ai-quotas-test.sqlite3",
        "AI_QUOTAS_DATA_DIR": "/tmp/ai-quotas-test-data",
        "AI_QUOTAS_EXTRA_ADAPTERS": "/tmp/ai-quotas-test-extra",
    }
    proc = subprocess.run(
        ["bash", str(script), "--dry-run"],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "AI_QUOTAS_DATABASE=/tmp/ai-quotas-test.sqlite3" in out
    assert "AI_QUOTAS_DATA_DIR=/tmp/ai-quotas-test-data" in out
    assert "AI_QUOTAS_EXTRA_ADAPTERS=/tmp/ai-quotas-test-extra" in out
    assert "(dry-run" in out


def test_install_dry_run_omits_unset_optional_env():
    script = REPO / "scripts" / "install-launchagent.sh"
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "AI_QUOTAS_SAMPLES",
            "AI_QUOTAS_DATABASE",
            "AI_QUOTAS_DATA_DIR",
            "AI_QUOTAS_EXTRA_ADAPTERS",
        }
    }
    proc = subprocess.run(
        ["bash", str(script), "--dry-run"],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "AI_QUOTAS_SAMPLES=" not in proc.stdout
    assert "AI_QUOTAS_DATABASE=" not in proc.stdout
    assert "AI_QUOTAS_DATA_DIR=" not in proc.stdout
    assert "AI_QUOTAS_EXTRA_ADAPTERS=" not in proc.stdout


def test_dash_cli_generate_serve_and_stop(tmp_path):
    """End-to-end: subprocess dash --port 0, fetch, SIGINT, no hang."""
    pytest.importorskip("pandas")
    out = tmp_path / "plots"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "ai_quotas",
            "--samples",
            str(FIXTURE),
            "dash",
            "--out",
            str(out),
            "--port",
            "0",
            "--interval",
            "30",
            "--engine",
            "plotly",
        ],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    url = None
    buf: list[str] = []
    try:
        assert proc.stdout is not None
        deadline = __import__("time").monotonic() + 40
        while __import__("time").monotonic() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            buf.append(line)
            if line.startswith("URL"):
                url = line.split(None, 1)[1].strip()
                break
        assert url, "".join(buf)
        assert url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        assert "03_plotly/index.html" in body or "ai-quotas" in body
        index_url = url.rsplit("/", 1)[0] + f"/{INDEX_NAME}"
        with urllib.request.urlopen(index_url, timeout=5) as resp:
            index = resp.read().decode("utf-8", errors="replace")
        assert "ai-quotas" in index
    finally:
        proc.send_signal(__import__("signal").SIGINT)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise
    assert proc.returncode == 0, "".join(buf)
    assert (out / INDEX_NAME).is_file()
    assert (out / LIVE_NAME).is_file()
