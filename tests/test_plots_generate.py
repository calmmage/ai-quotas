"""Integration: real generate_plots writes dashboards (shipped entry)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pandas")

from importlib.resources import files  # noqa: E402

from ai_quotas.plots.generate import generate_plots  # noqa: E402


def test_plot_templates_are_package_resources():
    static = files("ai_quotas.plots.static")
    for name in ("plotly.html", "uplot.html", "index.html", "time_axis.js", "theme.js"):
        target = static.joinpath(name)
        assert target.is_file(), name
        text = target.read_text(encoding="utf-8")
        assert text.strip()
        if name.endswith(".html"):
            assert "<!DOCTYPE html>" in text


def test_generate_plots_writes_index_and_engines(tmp_path):
    fixture = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
    out = tmp_path / "plots"
    result = generate_plots(samples=fixture, out_dir=out, engines=("plotly", "uplot"))
    assert result["index"].is_file()
    assert result["money"].is_file()
    assert (out / "03_plotly" / "index.html").is_file()
    assert (out / "10_uplot" / "index.html").is_file()
    html = (out / "03_plotly" / "index.html").read_text(encoding="utf-8")
    # multi-vendor single page + cols control
    assert "plots / row" in html or "plots/row" in html or "data-cols" in html
    assert "Claude" in html or "panels" in html
    assert "data-cols" in html
    assert 'data-theme="day"' in html
    assert 'data-theme="night"' in html
    assert "00_INDEX.html" not in html  # no version-catalog link: one final page (petr, 04 Sep 2026)
    uplot = (out / "10_uplot" / "index.html").read_text(encoding="utf-8")
    assert "data-cols" in uplot
    assert 'data-theme="night"' in uplot
    assert "00_INDEX.html" not in uplot
    assert "Quota remaining <span class=\"info\"" in uplot and ".hint" not in uplot
    assert result["n_rows"] > 0
    assert "function timeAxis" in html
    assert "function timeAxis" in uplot
    assert "quota-theme" in html
    assert "quota-theme" in uplot
    assert "__TIME_AXIS_JS__" not in html
    assert "__TIME_AXIS_JS__" not in uplot
    assert "__THEME_JS__" not in html
    assert "__THEME_JS__" not in uplot
    for token in ("__PANELS__", "__CUTOFF__", "__BURN_W__", "__BURN_A__"):
        assert token not in html
        assert token not in uplot
    # 5h series may still be drawn; reset marks must not mention it
    from ai_quotas.plots.generate import _vendor_panel_payload
    from ai_quotas.plots.prep import prepare

    df, resets, _ = prepare(fixture)
    payload = _vendor_panel_payload(df, resets, "Claude")
    assert all("5h" not in r["label"].lower() for r in payload["resets"])
    assert all("5h" not in (r.get("pill") or "").lower() for r in payload["resets"])
    assert all("Fable" not in (r.get("tooltip") or "") for r in payload["resets"])
    fiveh = [s for s in payload["series"] if "5h" in s["label"]]
    if fiveh:
        assert all(s["dim"] for s in fiveh)
        assert all(s["window_usd"] == 0 for s in fiveh)
    week = [s for s in payload["series"] if s["label"] == "Claude week"]
    if week:
        assert not week[0]["dim"]
    assert "quota-tip" in uplot
    assert "setCursor" in uplot
    assert "remaining" in uplot
    assert "isDim(s)" in html
    assert "isDim(s)" in uplot
    assert "mixHex" in uplot
    assert "0.22" in uplot
    index = result["index"].read_text(encoding="utf-8")
    assert "Daily spend" in index
    assert "__SPEND_ROWS__" not in index


def test_time_axis_tick_density():
    """1w / 1m / 1q must not emit a label per day."""
    import json
    import shutil
    import subprocess

    from ai_quotas.plots.generate import TIME_AXIS_JS

    if not shutil.which("node"):
        pytest.skip("node not available")
    script = (
        TIME_AXIS_JS
        + """
const week = timeAxis(0, 7*86400);
const month = timeAxis(0, 30*86400);
const quarter = timeAxis(0, 90*86400);
console.log(JSON.stringify({
  week: week.majors.length,
  month: month.majors.length,
  quarter: quarter.majors.length,
  weekFmt: week.fmt(week.majors[0] || 0),
  monthFmt: month.fmt(month.majors[0] || 0),
}));
"""
    )
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    counts = json.loads(proc.stdout)
    assert 6 <= counts["week"] <= 10
    assert 4 <= counts["month"] <= 8
    assert 5 <= counts["quarter"] <= 10


def test_reset_pills_are_short_tooltips_hold_copy(tmp_path):
    import json
    from datetime import timedelta

    from ai_quotas.plots.generate import _vendor_panel_payload
    from ai_quotas.plots.prep import CreditEvent, prepare

    def row(ts: str, used: float) -> str:
        return json.dumps(
            {
                "ts": ts,
                "provider": "codex",
                "window": "week",
                "used_percent": used,
                "resets_at": None,
                "plan": None,
                "status": "ok",
                "reason": None,
                "limit": None,
                "used": None,
            }
        )

    samples = tmp_path / "samples.jsonl"
    samples.write_text(
        "\n".join(
            [
                row("2026-09-01T10:00:00+03:00", 50.0),
                row("2026-09-01T10:30:00+03:00", 0.0),
                row("2026-09-02T10:00:00+03:00", 20.0),
                row("2026-09-02T10:30:00+03:00", 0.0),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    df, resets, _ = prepare(samples)
    payload = _vendor_panel_payload(df, resets, "Codex")
    pills = [r["pill"] for r in payload["resets"]]
    assert pills
    assert all("Codex week" not in p for p in pills)
    assert all("tok" not in p.lower() for p in pills)
    assert any(p.startswith("+$") for p in pills)
    assert any(p.startswith("-$") for p in pills)
    tips = [r["tooltip"] for r in payload["resets"]]
    assert any(t.startswith("Lost unused") for t in tips)
    assert any(t.startswith("Gained free") for t in tips)
    assert all("Codex week" in t for t in tips)

    free = next(r for r in resets if r.kind == "free")
    when = free.at
    credits = [
        CreditEvent(
            vendor="Codex",
            provider="codex",
            credit_id="used-1",
            title="Full reset",
            status="consumed",
            granted_at=when - timedelta(days=10),
            expires_at=when + timedelta(days=5),
            ended_at=when,
            window_usd=46.0,
            money_usd=20.0,
            money_label="reset redeemed +$20",
            used_before=20.0,
        ),
        CreditEvent(
            vendor="Codex",
            provider="codex",
            credit_id="exp-1",
            title="Full reset",
            status="expired",
            granted_at=when - timedelta(days=20),
            expires_at=when + timedelta(days=2),
            ended_at=when + timedelta(days=2),
            window_usd=46.0,
            money_usd=-46.0,
            money_label="reset expired unused −$46",
            used_before=None,
        ),
    ]
    with_credits = _vendor_panel_payload(df, resets, "Codex", credits=credits)
    kinds = {r["kind"] for r in with_credits["resets"]}
    pills2 = {r["pill"] for r in with_credits["resets"]}
    assert "credit_used" in kinds
    assert "credit_expired" in kinds
    assert "Reset used" in pills2
    assert "Reset expired" in pills2
    used_tip = next(r["tooltip"] for r in with_credits["resets"] if r["kind"] == "credit_used")
    assert used_tip.startswith("Quota reset used")
    exp_tip = next(r["tooltip"] for r in with_credits["resets"] if r["kind"] == "credit_expired")
    assert exp_tip.startswith("Quota reset expired")
    leftover_free = [r for r in with_credits["resets"] if r["kind"] == "free"]
    assert leftover_free == []


def test_claude_pills_only_on_weekly_total(tmp_path):
    """Fable resets must not mint a second $ pill next to weekly_all."""
    import json

    from ai_quotas.plots.generate import _vendor_panel_payload
    from ai_quotas.plots.prep import prepare

    def row(ts: str, window: str, used: float) -> str:
        return json.dumps(
            {
                "ts": ts,
                "provider": "claude",
                "window": window,
                "used_percent": used,
                "resets_at": None,
                "plan": None,
                "status": "ok",
                "reason": None,
                "limit": None,
                "used": None,
            }
        )

    samples = tmp_path / "samples.jsonl"
    samples.write_text(
        "\n".join(
            [
                row("2026-08-10T10:00:00+03:00", "week", 40.0),
                row("2026-08-10T10:00:00+03:00", "week_fable", 80.0),
                row("2026-08-10T10:30:00+03:00", "week", 0.5),
                row("2026-08-10T10:30:00+03:00", "week_fable", 0.5),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    df, resets, _ = prepare(samples)
    payload = _vendor_panel_payload(df, resets, "Claude")
    pills = [r for r in payload["resets"] if r["kind"] in {"burn", "free"}]
    assert len(pills) == 1
    assert "Fable" not in pills[0]["tooltip"]
    assert "Claude week" in pills[0]["tooltip"]


def test_uplot_pill_hitbox_is_plot_relative():
    from importlib.resources import files

    js = files("ai_quotas.plots.static").joinpath("uplot.html").read_text(encoding="utf-8")
    assert "(bx - left) / DPR" in js
    assert "(boxTop - top) / DPR" in js
