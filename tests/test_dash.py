"""dash subcommand: help, generate+serve smoke, no hung threads."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ai_quotas.cli import build_parser
from ai_quotas.plots.dash import (
    INDEX_NAME,
    LIVE_NAME,
    _code_mtime,
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


def test_code_mtime_sees_the_package():
    assert _code_mtime() > 0


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
    """Standalone plot output must not carry a page-level refresh."""
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots

    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly",))
    for name in (INDEX_NAME, "03_plotly/index.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert "http-equiv=\"refresh\"" not in html.lower()
        assert "setInterval(function(){" not in html


def test_shells_are_static_and_data_is_separate(tmp_path):
    """Engine pages are byte-identical across regenerations; the data they fetch
    lives in panels.json (with a .gz sibling). That is what lets a browser keep
    the shells + CDN libraries cached and only re-fetch the data."""
    pytest.importorskip("pandas")
    import gzip
    import json

    from ai_quotas.plots.generate import PANELS_NAME, SHELL_VERSION, generate_plots

    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly", "uplot"))
    shells = {n: (out / n).read_bytes() for n in ("03_plotly/index.html", "10_uplot/index.html")}
    for name, body in shells.items():
        assert (out / (name + ".gz")).is_file()
        assert gzip.decompress((out / (name + ".gz")).read_bytes()) == body
        text = body.decode("utf-8")
        assert PANELS_NAME in text and "meta.json" in text
        assert f"'{SHELL_VERSION}'" in text
        assert "createLivePoller" in text
        assert "location.reload" in text  # only for a redeployed shell
        assert "Claude" in text  # vendors are embedded for the skeleton
        assert not re.findall(r"__[A-Z][A-Z0-9_]+__", text)
    data = json.loads((out / PANELS_NAME).read_text(encoding="utf-8"))
    assert data["shell_version"] == SHELL_VERSION
    assert [p["vendor"] for p in data["panels"]] == data["vendors"]
    assert gzip.decompress((out / (PANELS_NAME + ".gz")).read_bytes()) == (out / PANELS_NAME).read_bytes()
    assert not list(out.rglob("*.tmp"))
    # regenerate: shells unchanged, data rewritten
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly", "uplot"))
    for name, body in shells.items():
        assert (out / name).read_bytes() == body


def test_live_page_frames_both_engines(tmp_path):
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots

    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly",))
    write_live_page(out, interval=15)
    live = (out / LIVE_NAME).read_text(encoding="utf-8")
    assert "03_plotly/index.html" in live
    assert "10_uplot/index.html" in live
    assert "quota-theme" in live
    assert INDEX_NAME not in live
    assert "iframe" in live


def _get(url: str, **headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_dash_serves_gzip_and_revalidates(tmp_path):
    """no-cache + Last-Modified → 304 on repeat; .gz sibling for gzip clients;
    identity bytes otherwise; API stays no-store."""
    pytest.importorskip("pandas")
    import gzip

    from ai_quotas.plots.generate import PANELS_NAME, generate_plots

    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly",))
    write_live_page(out, interval=15)
    httpd = make_server(out, 0)
    thread = threading.Thread(target=httpd.serve_forever, name="dash-gzip", daemon=True)
    thread.start()
    try:
        base = "http://%s:%d" % httpd.server_address
        plain = (out / PANELS_NAME).read_bytes()
        status, headers, body = _get(f"{base}/{PANELS_NAME}")
        assert status == 200 and body == plain
        assert headers["Cache-Control"] == "no-cache"
        assert "Content-Encoding" not in headers
        assert headers.get("Vary") == "Accept-Encoding"
        last_modified = headers["Last-Modified"]
        status, headers, body = _get(f"{base}/{PANELS_NAME}", **{"Accept-Encoding": "gzip, br"})
        assert status == 200 and headers["Content-Encoding"] == "gzip"
        assert headers["Content-Type"] == "application/json"
        assert gzip.decompress(body) == plain
        assert len(body) < len(plain)
        for enc in ({}, {"Accept-Encoding": "gzip"}):
            status, _, body = _get(f"{base}/{PANELS_NAME}", **{"If-Modified-Since": last_modified, **enc})
            assert status == 304 and body == b""
        status, headers, _ = _get(f"{base}/03_plotly/index.html", **{"Accept-Encoding": "gzip"})
        assert status == 200 and headers["Content-Encoding"] == "gzip"
        assert headers["Content-Type"].startswith("text/html")
        status, headers, _ = _get(f"{base}/api/subscriptions")
        assert headers["Cache-Control"] == "no-store"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def test_dash_skips_stale_gz(tmp_path):
    """An original rewritten after its .gz is served as identity, never stale bytes."""
    pytest.importorskip("pandas")
    import os
    import time as _time

    from ai_quotas.plots.generate import PANELS_NAME, generate_plots

    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly",))
    target = out / PANELS_NAME
    target.write_text('{"fresh": true}', encoding="utf-8")
    gz = out / (PANELS_NAME + ".gz")
    old = _time.time() - 10
    os.utime(gz, (old, old))
    httpd = make_server(out, 0)
    thread = threading.Thread(target=httpd.serve_forever, name="dash-stale", daemon=True)
    thread.start()
    try:
        base = "http://%s:%d" % httpd.server_address
        status, headers, body = _get(f"{base}/{PANELS_NAME}", **{"Accept-Encoding": "gzip"})
        assert status == 200 and body == b'{"fresh": true}'
        assert "Content-Encoding" not in headers
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def test_dash_serve_smoke(tmp_path):
    """Generate + serve on an ephemeral port, fetch index HTML, stop cleanly."""
    pytest.importorskip("pandas")
    from ai_quotas.plots.generate import generate_plots

    out = tmp_path / "plots"
    generate_plots(samples=FIXTURE, out_dir=out, engines=("plotly",))
    write_live_page(out, interval=15)

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
        assert "meta.json" in index  # nav page reloads itself on regeneration
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


def test_live_refresh_poller_logic():
    """live_refresh.js under node with a fake fetch: initial paint, no refetch
    while generated_at is unchanged, refetch on change, stop on 404 meta, one
    reload when shell_version differs (deferred while a dialog is open)."""
    import shutil
    from importlib.resources import files

    if not shutil.which("node"):
        pytest.skip("node not available")
    js = files("ai_quotas.plots.static").joinpath("live_refresh.js").read_text(encoding="utf-8")
    script = js + r"""
const log = [];
let meta = {generated_at: "A", poll_interval_s: 30};
let data = {shell_version: "v1", panels: []};
let metaStatus = 200;
const fakeFetch = (url) => {
  log.push("GET " + url);
  const isMeta = url.endsWith("meta.json");
  const status = isMeta ? metaStatus : 200;
  const body = isMeta ? meta : data;
  return Promise.resolve({status, ok: status === 200, json: () => Promise.resolve(JSON.parse(JSON.stringify(body)))});
};
let timers = [];
const paints = [];
let reloads = 0, busy = false;
const p = createLivePoller({
  fetchImpl: fakeFetch, metaUrl: "../meta.json", dataUrl: "../panels.json", shellVersion: "v1",
  onData: (d, o) => paints.push([d.shell_version, o.initial]),
  onOutdated: () => reloads++, isBusy: () => busy, fallbackMs: 15000,
  setTimeoutImpl: (fn, ms) => { timers.push([fn, ms]); return timers.length; },
  clearTimeoutImpl: () => {},
});
const fire = () => { const t = timers.pop(); timers = []; return t ? t[0]() : Promise.resolve(); };
(async () => {
  await p.start();
  const out = {};
  out.initial = paints.slice();                 // one initial paint
  out.delayAfterStart = timers[0][1];           // 30 s from meta
  await fire();                                 // same generated_at: no data fetch
  out.paintsAfterQuietTick = paints.length;
  meta = {generated_at: "B", poll_interval_s: 7};
  await fire();                                 // changed: refetch, repaint
  out.paintsAfterChange = paints.length;
  out.delayAfterChange = timers[0][1];
  busy = true;
  data = {shell_version: "v2", panels: []};
  meta = {generated_at: "C", poll_interval_s: 7};
  await fire();                                 // outdated shell, dialog open: painted, no reload yet
  out.reloadsWhileBusy = reloads;
  busy = false;
  await fire();                                 // dialog closed: reload once
  out.reloadsAfterBusy = reloads;
  metaStatus = 404;
  await fire();
  out.stopped = p._state().stopped;
  out.timersAfterStop = timers.length;
  out.gets = log.filter(l => l.endsWith("panels.json")).length;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = __import__("json").loads(proc.stdout.strip().splitlines()[-1])
    assert out["initial"] == [["v1", True]]
    assert out["delayAfterStart"] == 30000
    assert out["paintsAfterQuietTick"] == 1
    assert out["paintsAfterChange"] == 2
    assert out["delayAfterChange"] == 7000
    assert out["reloadsWhileBusy"] == 0
    assert out["reloadsAfterBusy"] == 1
    assert out["stopped"] is True
    assert out["timersAfterStop"] == 0
    assert out["gets"] == 3  # initial + change B + change C
