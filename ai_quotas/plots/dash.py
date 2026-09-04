"""Local live viewer for generated plot dashboards.

Stdlib only at import time. ``generate_plots`` (optional plot extras) is imported
inside ``run_dash`` so ``make_server`` stays usable in tests without pandas.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ai_quotas.storage import fingerprint

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_INTERVAL = 15.0
LIVE_NAME = "live.html"
INDEX_NAME = "00_INDEX.html"
REFRESH_MARK = "<!-- ai-quotas-dash-refresh -->"


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


def write_live_page(out_dir: Path, *, interval: float) -> Path:
    """Thin wrapper that frames the plot (day=Plotly, night=uPlot).

    The index and engine pages get a meta-refresh (see ``inject_meta_refresh``)
    so navigating into Plotly/uPlot still picks up regenerations. This wrapper
    does not refresh itself — that would kick the iframe back to the landing
    plot. ``interval`` is accepted so the CLI/docs stay aligned; the wrapper
    does not use it.
    """
    del interval
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / LIVE_NAME
    path.write_text(
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>ai-quotas · live</title>
<style>
html,body{margin:0;height:100%;background:#fafafa}
iframe{border:0;width:100%;height:100%}
</style>
</head>
<body>
<iframe id="plot" title="ai-quotas plots"></iframe>
<script>
(function () {
  var night = localStorage.getItem("quota-theme") === "night";
  document.documentElement.style.background = night ? "#111318" : "#fafafa";
  document.getElementById("plot").src = night
    ? "10_uplot/index.html"
    : "03_plotly/index.html";
})();
</script>
</body>
</html>
""",
        encoding="utf-8",
    )
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


def _stamp(out_dir: Path, interval: float) -> Path:
    live = write_live_page(out_dir, interval=interval)
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
) -> int:
    """Generate, serve on 127.0.0.1, and regenerate when samples change."""
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
    try:
        while True:
            time.sleep(interval)
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
            except Exception as e:
                print(f"regen failed: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
        if thread.is_alive():
            print("warning: server thread still running", file=sys.stderr)
    return 0
