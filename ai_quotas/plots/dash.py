"""Local live viewer for generated plot dashboards.

Stdlib only at import time. ``generate_plots`` (optional plot extras) is imported
inside ``run_dash`` so ``make_server`` stays usable in tests without pandas.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ai_quotas.notify import hc_interval, heartbeat_due, ping_role
from ai_quotas.storage import fingerprint

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_INTERVAL = 15.0
LIVE_NAME = "live.html"
INDEX_NAME = "00_INDEX.html"
REFRESH_MARK = "<!-- ai-quotas-dash-refresh -->"
META_NAME = "meta.json"
# A read-only mirror (adr 0025 §10) shows "stale" once the newest stamp is older
# than this. Samples land every 30 min; 2 h tolerates a short sleep of the Mac.
STALE_AFTER_S = 2 * 3600
HOOK_TIMEOUT = 60.0

# Client-side stale logic for live.html (plain functions; tests run it under node).
STALE_JS = """\
var MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function pad2(n){return (n<10?"0":"")+n;}
function fmtStamp(d){return pad2(d.getDate())+" "+MONTHS[d.getMonth()]+" "+d.getFullYear()+" "+pad2(d.getHours())+":"+pad2(d.getMinutes());}
function staleState(iso,nowMs,maxAgeMs){var t=Date.parse(iso);if(isNaN(t)){return {stale:false,text:""};}return {stale:(nowMs-t)>maxAgeMs,text:"plots generated "+fmtStamp(new Date(t))+" \u00b7 stale"};}
"""


class DashHandler(SimpleHTTPRequestHandler):
    """Serve a plots directory. ``/`` → ``live.html``. No-store so regen is visible."""

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 — stdlib name
        if self.path.split("?", 1)[0] in ("/", ""):
            self.send_response(302)
            self.send_header("Location", f"/{LIVE_NAME}")
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def samples_mtime(path: Path):
    """Compatibility name: return a change token for JSONL or SQLite samples."""
    return fingerprint(path, kind="samples")


LIVE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="generated-at" content="__GENERATED_AT__"/>
<title>ai-quotas · live</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2064%2064%22%20shape-rendering%3D%22crispEdges%22%3E%3Crect%20width%3D%2264%22%20height%3D%2264%22%20rx%3D%2212%22%20fill%3D%22%2313171c%22%2F%3E%3Cg%20fill%3D%22%239b8cff%22%3E%3Crect%20x%3D%2236%22%20y%3D%2212%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2240%22%20y%3D%2212%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2236%22%20y%3D%2216%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2240%22%20y%3D%2216%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2224%22%20y%3D%2220%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2228%22%20y%3D%2220%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2236%22%20y%3D%2220%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2240%22%20y%3D%2220%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2224%22%20y%3D%2224%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2228%22%20y%3D%2224%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2236%22%20y%3D%2224%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2240%22%20y%3D%2224%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2212%22%20y%3D%2228%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2216%22%20y%3D%2228%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2224%22%20y%3D%2228%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2228%22%20y%3D%2228%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2236%22%20y%3D%2228%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2240%22%20y%3D%2228%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2212%22%20y%3D%2232%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2216%22%20y%3D%2232%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2224%22%20y%3D%2232%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2228%22%20y%3D%2232%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2236%22%20y%3D%2232%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2240%22%20y%3D%2232%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2248%22%20y%3D%2232%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2252%22%20y%3D%2232%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2212%22%20y%3D%2236%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2216%22%20y%3D%2236%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2224%22%20y%3D%2236%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2228%22%20y%3D%2236%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2236%22%20y%3D%2236%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2240%22%20y%3D%2236%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2248%22%20y%3D%2236%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2252%22%20y%3D%2236%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2212%22%20y%3D%2240%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2216%22%20y%3D%2240%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2224%22%20y%3D%2240%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2228%22%20y%3D%2240%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2236%22%20y%3D%2240%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2240%22%20y%3D%2240%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2248%22%20y%3D%2240%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2252%22%20y%3D%2240%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%228%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2212%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2216%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2220%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2224%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2228%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2232%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2236%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2240%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2244%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2248%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3Crect%20x%3D%2252%22%20y%3D%2244%22%20width%3D%224%22%20height%3D%224%22%2F%3E%3C%2Fg%3E%3C%2Fsvg%3E" />
<style>
html,body{margin:0;height:100%;background:#fafafa}
body{display:flex;flex-direction:column}
#stale{display:none;font:12px system-ui,sans-serif;padding:4px 10px;background:#fff3cd;color:#5c4400;border-bottom:1px solid #e6c85a}
#stale.on{display:block}
html.night #stale{background:#3a2f00;color:#f3d77a;border-color:#6b5300}
iframe{border:0;width:100%;flex:1;min-height:0}
</style>
</head>
<body>
<div id="stale" data-stale-after="__STALE_AFTER__"></div>
<iframe id="plot" title="ai-quotas plots"></iframe>
<script>
__STALE_JS__
(function () {
  var night = localStorage.getItem("quota-theme") === "night";
  document.documentElement.classList.toggle("night", night);
  document.documentElement.style.background = night ? "#111318" : "#fafafa";
  document.getElementById("plot").src = night
    ? "10_uplot/index.html"
    : "03_plotly/index.html";
  var bar = document.getElementById("stale");
  var meta = document.querySelector('meta[name="generated-at"]');
  var iso = meta ? meta.content : "";
  var maxAge = (parseInt(bar.getAttribute("data-stale-after"), 10) || 7200) * 1000;
  function render() {
    var s = staleState(iso, Date.now(), maxAge);
    bar.textContent = s.text;
    bar.className = s.stale ? "on" : "";
  }
  function refresh() {
    try {
      fetch("meta.json", {cache: "no-store"})
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { if (j && j.generated_at) { iso = j.generated_at; } render(); })
        .catch(render);
    } catch (e) { render(); }
  }
  render();
  setInterval(refresh, 60000);
})();
</script>
</body>
</html>
"""


def utc_stamp(now: datetime | None = None) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ`` (second precision, always UTC)."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_meta(
    out_dir: Path,
    *,
    generated_at: str,
    stale_after_s: int = STALE_AFTER_S,
    interval: float,
) -> Path:
    """``meta.json`` next to the plots — the machine-readable freshness stamp a
    mirror or monitor reads (adr 0025 §10). Stable keys: ``generated_at``
    (UTC ISO), ``stale_after_s``, ``poll_interval_s``, ``host``, ``producer``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / META_NAME
    payload = {
        "generated_at": generated_at,
        "stale_after_s": int(stale_after_s),
        "poll_interval_s": float(interval),
        "host": socket.gethostname(),
        "producer": "ai-quotas dash",
    }
    _write_atomic(path, json.dumps(payload, indent=1) + "\n")
    return path


def write_live_page(
    out_dir: Path,
    *,
    interval: float,
    generated_at: str | None = None,
    stale_after_s: int = STALE_AFTER_S,
) -> Path:
    """Thin wrapper that frames the plot (day=Plotly, night=uPlot).

    The index and engine pages get a meta-refresh (see ``inject_meta_refresh``)
    so navigating into Plotly/uPlot still picks up regenerations. This wrapper
    does not refresh itself — that would kick the iframe back to the landing
    plot. ``interval`` is accepted so the CLI/docs stay aligned; the wrapper
    does not use it.

    Carries ``<meta name="generated-at">`` and a banner that appears only when
    the stamp is older than ``stale_after_s`` (re-checked every minute against
    ``meta.json`` so a mirror tab un-stales when the producer is back).
    """
    del interval
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / LIVE_NAME
    html = (
        LIVE_TEMPLATE.replace("__GENERATED_AT__", generated_at or utc_stamp())
        .replace("__STALE_AFTER__", str(int(stale_after_s)))
        .replace("__STALE_JS__", STALE_JS.rstrip("\n"))
    )
    _write_atomic(path, html)
    return path


def inject_meta_refresh(out_dir: Path, interval: float) -> None:
    """Stamp a short meta-refresh onto generated HTML (dash only, not ``plot``)."""
    sec = max(1, int(round(float(interval))))
    tag = f'{REFRESH_MARK}<meta http-equiv="refresh" content="{sec}"/>'
    targets = [out_dir / INDEX_NAME, *sorted(out_dir.glob("*/index.html"))]
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if REFRESH_MARK in text:
            start = text.index(REFRESH_MARK)
            end = text.find("/>", start)
            if end != -1:
                text = text[:start] + text[end + 2 :]
        if "<head>" in text:
            text = text.replace("<head>", f"<head>{tag}", 1)
        else:
            text = tag + text
        path.write_text(text, encoding="utf-8")


def make_server(directory: Path, port: int) -> ThreadingHTTPServer:
    """Bind ``127.0.0.1:port`` (port 0 = ephemeral). Raises OSError on bind failure."""
    handler = partial(DashHandler, directory=str(directory))
    return ThreadingHTTPServer((HOST, port), handler)


def _open_url(url: str) -> None:
    opener = shutil.which("open") or shutil.which("xdg-open")
    if opener:
        subprocess.run([opener, url], check=False)
    else:
        print(f"(no open/xdg-open — open manually: {url})", file=sys.stderr)


class AfterRegenHook:
    """Run a shell command after each successful regeneration, off the serve loop.

    One run at a time (a fire while the previous run is going is skipped),
    bounded by ``timeout``; never raises into the caller. Output lines mirror
    the ``regen <ts>`` line: ``hook start`` / ``hook ok 3.2s`` on stdout,
    ``hook fail rc=N`` / ``hook timeout`` / ``hook error`` / ``hook skip`` on stderr.
    """

    def __init__(self, cmd: str, *, timeout: float = HOOK_TIMEOUT, cwd: Path | None = None):
        self.cmd = cmd
        self.timeout = float(timeout)
        self.cwd = cwd
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def fire(self) -> bool:
        """Start the command in a daemon thread. False = skipped (still running)."""
        if not self._lock.acquire(blocking=False):
            print("hook skip (previous run still going)", file=sys.stderr)
            return False
        self._thread = threading.Thread(target=self._run, name="ai-quotas-hook", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        t0 = time.monotonic()
        try:
            print(f"hook start {self.cmd}")
            sys.stdout.flush()
            proc = subprocess.run(
                self.cmd,
                shell=True,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
                timeout=self.timeout,
                check=False,
            )
            dt = time.monotonic() - t0
            if proc.returncode == 0:
                print(f"hook ok {dt:.1f}s")
                sys.stdout.flush()
            else:
                print(f"hook fail rc={proc.returncode} {dt:.1f}s", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"hook timeout {self.timeout:.0f}s", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — a hook must never take the dash down
            print(f"hook error {e}", file=sys.stderr)
        finally:
            self._lock.release()

    def join(self, timeout: float = 0.0) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


def _stamp(out_dir: Path, interval: float) -> Path:
    """Freshness stamp: ``meta.json`` first, then ``live.html`` carrying the same
    ``generated_at`` (a mirror never sees a live page newer than its meta),
    then the meta-refresh tags."""
    stamp = utc_stamp()
    write_meta(out_dir, generated_at=stamp, interval=interval)
    live = write_live_page(out_dir, interval=interval, generated_at=stamp)
    inject_meta_refresh(out_dir, interval)
    return live


def run_dash(
    *,
    samples: Path,
    out_dir: Path | None,
    port: int,
    interval: float,
    engines: tuple[str, ...],
    open_browser: bool = False,
    after_regen: str | None = None,
) -> int:
    """Generate, serve on 127.0.0.1, and regenerate when samples change.

    ``after_regen``: shell command fired after every successful generation
    (including the first) — e.g. the launchpad mirror script that pushes the
    plots dir to the cloud node.
    """
    from ai_quotas.plots.generate import generate_plots

    if interval <= 0:
        print("interval must be > 0", file=sys.stderr)
        return 1
    if port < 0 or port > 65535:
        print("port must be 0..65535", file=sys.stderr)
        return 1

    try:
        result = generate_plots(samples=samples, out_dir=out_dir, engines=engines)
    except FileNotFoundError as e:
        print(f"no samples: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"plot failed: {e}", file=sys.stderr)
        return 1

    dest = Path(result["out_dir"])
    _stamp(dest, interval)
    hook = AfterRegenHook(after_regen) if after_regen else None
    if hook is not None:
        hook.fire()

    try:
        httpd = make_server(dest, port)
    except OSError as e:
        print(f"bind failed: {HOST}:{port}: {e}", file=sys.stderr)
        return 1

    actual_port = int(httpd.server_address[1])
    url = f"http://{HOST}:{actual_port}/{LIVE_NAME}"

    ready = threading.Event()

    def _serve() -> None:
        ready.set()
        httpd.serve_forever(poll_interval=0.3)

    thread = threading.Thread(target=_serve, name="ai-quotas-dash", daemon=True)
    thread.start()
    if not ready.wait(timeout=2):
        print("server failed to start", file=sys.stderr)
        httpd.server_close()
        return 1

    print(f"INDEX {result['index']}")
    print(f"URL   {url}")
    sys.stdout.flush()

    if open_browser:
        _open_url(url)

    last = samples_mtime(samples)
    last_hc = 0.0
    every = hc_interval()

    def _heartbeat() -> None:
        nonlocal last_hc
        now_m = time.monotonic()
        if last_hc != 0.0 and not heartbeat_due(last_hc, now_m, every):
            return
        status = ping_role("dash")
        last_hc = now_m
        if status != "skip":
            print(f"healthchecks dash {status}")
            sys.stdout.flush()

    _heartbeat()
    try:
        while True:
            time.sleep(interval)
            _heartbeat()
            now = samples_mtime(samples)
            if now is None:
                continue
            if last is None:
                last = now
                continue
            if now == last:
                continue
            last = now
            try:
                generate_plots(samples=samples, out_dir=dest, engines=engines)
                _stamp(dest, interval)
                print(f"regen {time.strftime('%Y-%m-%dT%H:%M:%S')}")
                sys.stdout.flush()
                if hook is not None:
                    hook.fire()
            except Exception as e:
                print(f"regen failed: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
        if hook is not None:
            hook.join(timeout=2)
        if thread.is_alive():
            print("warning: server thread still running", file=sys.stderr)
    return 0
