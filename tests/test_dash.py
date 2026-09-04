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


# ---------------------------------------------------------------------------
# after-regen hook + generated-at stamp (adr 0025 §10 cloud mirror, 04 Sep 2026)


def test_dash_parser_has_after_regen():
    option_strings = [s for a in _dash_parser()._actions for s in a.option_strings]
    assert "--after-regen" in option_strings
    args = build_parser().parse_args(["dash", "--after-regen", "echo hi"])
    assert args.after_regen == "echo hi"
    assert build_parser().parse_args(["dash"]).after_regen is None


def test_hook_runs_and_writes_marker(tmp_path, capsys):
    from ai_quotas.plots.dash import AfterRegenHook

    marker = tmp_path / "ran"
    hook = AfterRegenHook(f"touch '{marker}'", timeout=10)
    assert hook.fire() is True
    hook.join(5)
    assert marker.is_file()
    out, err = capsys.readouterr()
    assert "hook start" in out
    assert "hook ok" in out
    assert "hook" not in err


def test_hook_fail_logs_rc(capsys):
    from ai_quotas.plots.dash import AfterRegenHook

    hook = AfterRegenHook("exit 3", timeout=10)
    assert hook.fire() is True
    hook.join(5)
    _, err = capsys.readouterr()
    assert "hook fail rc=3" in err


def test_hook_timeout(capsys):
    import time

    from ai_quotas.plots.dash import AfterRegenHook

    hook = AfterRegenHook("sleep 5", timeout=0.3)
    t0 = time.monotonic()
    assert hook.fire() is True
    hook.join(4)
    assert time.monotonic() - t0 < 3.5
    _, err = capsys.readouterr()
    assert "hook timeout" in err


def test_hook_skip_while_running(capsys):
    from ai_quotas.plots.dash import AfterRegenHook

    hook = AfterRegenHook("sleep 0.6", timeout=10)
    assert hook.fire() is True
    assert hook.fire() is False
    hook.join(5)
    _, err = capsys.readouterr()
    assert "hook skip" in err
    assert hook.fire() is True
    hook.join(5)


def test_hook_env_precedence(monkeypatch):
    pytest.importorskip("pandas")
    import ai_quotas.plots.dash as dash_mod
    from ai_quotas.cli import main
    from ai_quotas.paths import ENV_AFTER_REGEN

    seen: list[dict] = []

    def fake_run_dash(**kwargs):
        seen.append(kwargs)
        return 0

    monkeypatch.setattr(dash_mod, "run_dash", fake_run_dash)
    base = ["--samples", str(FIXTURE), "dash", "--port", "0"]

    monkeypatch.setenv(ENV_AFTER_REGEN, "echo env")
    assert main(base) == 0
    assert seen[-1]["after_regen"] == "echo env"

    assert main(base + ["--after-regen", "echo cli"]) == 0
    assert seen[-1]["after_regen"] == "echo cli"

    monkeypatch.setenv(ENV_AFTER_REGEN, "   ")
    assert main(base) == 0
    assert seen[-1]["after_regen"] is None


def test_utc_stamp_format():
    from datetime import datetime, timedelta, timezone

    from ai_quotas.plots.dash import utc_stamp

    assert utc_stamp(datetime(2026, 9, 4, 5, 0, tzinfo=timezone.utc)) == "2026-09-04T05:00:00Z"
    plus3 = timezone(timedelta(hours=3))
    assert utc_stamp(datetime(2026, 9, 4, 8, 0, tzinfo=plus3)) == "2026-09-04T05:00:00Z"
    assert utc_stamp(datetime(2026, 9, 4, 5, 0)) == "2026-09-04T05:00:00Z"
    import re

    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", utc_stamp())


def test_write_meta(tmp_path):
    import json

    from ai_quotas.plots.dash import META_NAME, STALE_AFTER_S, write_meta

    path = write_meta(tmp_path, generated_at="2026-09-04T05:00:00Z", interval=30)
    assert path.name == META_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["generated_at"] == "2026-09-04T05:00:00Z"
    assert data["stale_after_s"] == STALE_AFTER_S == 7200
    assert data["poll_interval_s"] == 30.0
    assert data["producer"] == "ai-quotas dash"
    assert not list(tmp_path.glob("*.tmp"))


def test_write_live_page_stamps_generated_at(tmp_path):
    import re

    live = write_live_page(tmp_path, interval=15, generated_at="2026-09-04T05:00:00Z")
    text = live.read_text(encoding="utf-8")
    assert '<meta name="generated-at" content="2026-09-04T05:00:00Z"/>' in text
    assert 'data-stale-after="7200"' in text
    assert 'id="stale"' in text
    assert "03_plotly/index.html" in text
    assert "10_uplot/index.html" in text
    assert INDEX_NAME not in text
    assert not list(tmp_path.glob("*.tmp"))
    text2 = write_live_page(tmp_path, interval=15).read_text(encoding="utf-8")
    assert re.search(r'name="generated-at" content="\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"', text2)


def test_stamp_writes_meta_and_live(tmp_path):
    import json

    from ai_quotas.plots.dash import META_NAME, _stamp

    _stamp(tmp_path, 15)
    meta = json.loads((tmp_path / META_NAME).read_text(encoding="utf-8"))
    live = (tmp_path / LIVE_NAME).read_text(encoding="utf-8")
    assert f'content="{meta["generated_at"]}"' in live


def test_stale_js_logic():
    """Banner logic under node: fresh / stale / garbage, DD MMM YYYY zero-padded."""
    import json
    import shutil

    from ai_quotas.plots.dash import STALE_JS

    if not shutil.which("node"):
        pytest.skip("node not available")
    script = (
        STALE_JS
        + """
console.log(JSON.stringify({
  fresh: staleState("2026-09-04T05:00:00Z", Date.parse("2026-09-04T06:59:00Z"), 7200000),
  stale: staleState("2026-09-04T05:00:00Z", Date.parse("2026-09-04T07:01:00Z"), 7200000),
  bad: staleState("garbage", 0, 1),
  day1: fmtStamp(new Date(Date.UTC(2026, 0, 1, 9, 5))),
}));
"""
    )
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        env={**os.environ, "TZ": "UTC"},
    )
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["fresh"]["stale"] is False
    assert got["stale"]["stale"] is True
    assert got["stale"]["text"] == "plots generated 04 Sep 2026 05:00 · stale"
    assert got["bad"] == {"stale": False, "text": ""}
    assert got["day1"] == "01 Jan 2026 09:05"


def test_dash_cli_after_regen_fires_on_start_and_regen(tmp_path):
    """End-to-end: hook fires after the initial generation and after a regen."""
    pytest.importorskip("pandas")
    import json
    import shutil
    import signal
    import time

    samples = tmp_path / "samples.jsonl"
    shutil.copy(FIXTURE, samples)
    out = tmp_path / "plots"
    marker = tmp_path / "hook.log"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "ai_quotas",
            "--samples",
            str(samples),
            "dash",
            "--out",
            str(out),
            "--port",
            "0",
            "--interval",
            "0.5",
            "--engine",
            "plotly",
            "--after-regen",
            f"echo x >> '{marker}'",
        ],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    buf: list[str] = []

    def _lines() -> int:
        return len(marker.read_text().splitlines()) if marker.is_file() else 0

    def _wait_lines(n: int, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if _lines() >= n:
                return
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        raise AssertionError(f"hook marker never reached {n} lines: {_lines()}")

    try:
        assert proc.stdout is not None
        url = None
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            buf.append(line)
            if line.startswith("URL"):
                url = line.split(None, 1)[1].strip()
                break
        assert url, "".join(buf)
        _wait_lines(1, 15)
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
        with urllib.request.urlopen(url, timeout=5) as resp:
            live = resp.read().decode("utf-8", errors="replace")
        assert f'content="{meta["generated_at"]}"' in live
        # append a sample row → fingerprint (mtime, size) changes → regen → hook again
        time.sleep(0.05)
        last = samples.read_text(encoding="utf-8").rstrip("\n").splitlines()[-1]
        with samples.open("a", encoding="utf-8") as fh:
            fh.write(last + "\n")
        _wait_lines(2, 20)
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise
        rest = proc.stdout.read() if proc.stdout else ""
        buf.append(rest)
    log = "".join(buf)
    assert proc.returncode == 0, log
    assert log.count("hook ok") >= 2, log
    assert "regen " in log, log
