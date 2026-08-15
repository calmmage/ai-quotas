"""CLI table rows, display-model JSON, offline table render."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ai_quotas.cli import filter_default_rows, sort_rows, table_rows


def test_table_rows_display_model(multi_path: Path, monkeypatch):
    monkeypatch.setenv("AI_QUOTAS_SAMPLES", str(multi_path))
    rows = table_rows(full=True, path=multi_path)
    assert rows
    d = rows[0].as_dict()
    assert "provider" in d
    assert "burn_24h" in d
    assert "need_rem" in d
    assert "burn_tot" in d
    assert "need_avg" in d
    assert "colors" in d
    assert set(d["colors"]) >= {"burn_24h", "burn_tot", "need_rem", "resets"}
    # %-only: no absolute token display fields required in title
    assert d["unit"] in ("%/h", "%/d")


def test_filter_default_hides_openrouter(multi_samples):
    filtered = filter_default_rows(multi_samples, full=False)
    providers = {r["provider"] for r in filtered}
    assert "openrouter" not in providers
    assert "claude" in providers
    full = filter_default_rows(multi_samples, full=True)
    assert any(r["provider"] == "openrouter" for r in full)


def test_sort_period_orders_month_before_5h(multi_samples):
    ok = [
        r
        for r in multi_samples
        if r.get("status") == "ok" and r.get("used_percent") is not None
    ]
    sorted_rows = sort_rows(ok, by="period")
    ranks = []
    for r in sorted_rows:
        w = str(r["window"])
        if w.startswith("month"):
            ranks.append(0)
        elif w.startswith("week"):
            ranks.append(1)
        elif w.startswith("5h"):
            ranks.append(2)
        else:
            ranks.append(9)
    assert ranks == sorted(ranks)


def test_cli_offline_table(multi_path: Path):
    env = {**os.environ, "AI_QUOTAS_SAMPLES": str(multi_path), "COLUMNS": "120"}
    proc = subprocess.run(
        [sys.executable, "-m", "ai_quotas", "--no-refresh"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "AI SUBSCRIPTION QUOTA" in out
    assert "Claude" in out or "claude" in out.lower()
    assert "used" in out.lower() or "%" in out


def test_cli_json_display_model(multi_path: Path):
    env = {**os.environ, "AI_QUOTAS_SAMPLES": str(multi_path), "COLUMNS": "120"}
    proc = subprocess.run(
        [sys.executable, "-m", "ai_quotas", "--no-refresh", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert "rows" in data
    assert data["rows"]
    row = data["rows"][0]
    assert "burn_24h" in row
    assert "colors" in row
    assert "title" in row


def test_collector_module_no_sample(multi_path: Path):
    env = {**os.environ, "AI_QUOTAS_SAMPLES": str(multi_path)}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quotas.collector",
            "--no-sample",
            "--samples",
            str(multi_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=30,
        check=False,
    )
    # exit 0/1/2 all valid
    assert proc.returncode in (0, 1, 2), proc.stderr
    data = json.loads(proc.stdout)
    assert "verdicts" in data
    assert "windows" in data


def test_adapters_never_raise_without_creds(tmp_path: Path, monkeypatch):
    """Offline: adapters degrade to unavailable/error, never raise, never fake 0."""
    monkeypatch.setattr(
        "ai_quotas.adapters.claude.CREDS_PATH", tmp_path / "no-creds.json"
    )
    monkeypatch.setattr(
        "ai_quotas.adapters.claude._read_keychain_oauth", lambda: None
    )
    monkeypatch.setattr(
        "ai_quotas.adapters.grok.AUTH_PATH", tmp_path / "no-auth.json"
    )
    monkeypatch.setattr(
        "ai_quotas.adapters.codex.SESSIONS_ROOT", tmp_path / "no-sessions"
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_KEY", raising=False)
    monkeypatch.setattr(
        "ai_quotas.adapters.openrouter._parse_dotenv", lambda _p: None
    )
    # Force codex offline (no binary)
    monkeypatch.setattr(
        "ai_quotas.adapters.codex.DEFAULT_CODEXBAR", str(tmp_path / "no-codexbar")
    )

    from ai_quotas.adapters import claude, codex, grok, openrouter

    ts = "2026-07-28T12:00:00+00:00"
    for mod in (claude, codex, grok, openrouter):
        rows = mod.snapshot(ts)
        assert isinstance(rows, list)
        assert rows
        for r in rows:
            if r.get("status") != "ok":
                assert r.get("used_percent") is None


def test_plot_respects_root_samples_flag(tmp_path):
    """Root --samples must reach plot (subparser must not clobber it)."""
    import subprocess
    import sys
    from pathlib import Path

    fixture = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
    out = tmp_path / "plots"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quotas",
            "--samples",
            str(fixture),
            "plot",
            "--out",
            str(out),
            "--engine",
            "plotly",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert (out / "03_plotly" / "index.html").is_file()
    assert "no samples" not in (proc.stdout + proc.stderr).lower()


def test_dash_respects_root_samples_flag(tmp_path):
    """Root --samples must reach dash (subparser must not clobber it)."""
    fixture = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
    out = tmp_path / "plots"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "ai_quotas",
            "--samples",
            str(fixture),
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
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    buf: list[str] = []
    try:
        assert proc.stdout is not None
        import time

        deadline = time.monotonic() + 40
        saw_url = False
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            buf.append(line)
            if line.startswith("URL"):
                saw_url = True
                break
        assert saw_url, "".join(buf)
        assert (out / "00_INDEX.html").is_file()
        assert "no samples" not in "".join(buf).lower()
    finally:
        import signal

        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise
    assert proc.returncode == 0, "".join(buf)
