"""Integration: real generate_plots writes dashboards (shipped entry)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pandas")

from importlib.resources import files  # noqa: E402

from ai_quotas.plots.generate import generate_plots  # noqa: E402


def test_plot_templates_are_package_resources():
    static = files("ai_quotas.plots.static")
    for name in ("plotly.html", "uplot.html", "index.html", "time_axis.js", "theme.js", "live_refresh.js", "range_nav.js", "range_nav.css", "layout.js", "layout.css"):
        target = static.joinpath(name)
        assert target.is_file(), name
        text = target.read_text(encoding="utf-8")
        assert text.strip()
        if name.endswith(".html"):
            assert "<!DOCTYPE html>" in text
            # A phone lays the page out at 980px unless this is set, so the
            # 430px rules never match.
            assert 'name="viewport"' in text
            assert "width=device-width" in text


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
    for token in ("__PANELS__", "__CUTOFF__", "__BURN_W__", "__BURN_A__", "__VENDORS__", "__SHELL_VERSION__", "__PANELS_NAME__", "__RANGE_NAV_JS__", "__RANGE_NAV_CSS__", "__LAYOUT_JS__", "__LAYOUT_CSS__"):
        assert token not in html
        assert token not in uplot
    assert (out / "panels.json").is_file()
    assert "../panels.json" in html and "../panels.json" in uplot
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
    assert "m.has(t) ? m.get(t) : undefined" in uplot
    assert "drag: { x: false, y: false }" in uplot
    assert "scrollZoom: false" in html
    assert "dragmode: false" in html
    assert "range-nav" in html and "range-nav" in uplot
    assert "window below" in html and "window below" in uplot
    assert 'class="range-controls" hidden' in html
    assert 'class="range-controls" hidden' in uplot
    assert 'data-span="7" class="active"' in uplot
    assert 'data-span="7" class="active"' in html
    assert "No quota samples yet" in html
    assert "No quota samples yet" in uplot
    assert "Set it up" in html and "Set it up" in uplot
    assert "openVendorSetup" in html
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


def test_charts_below_the_fold_wait_for_scroll():
    """The first paint is the charts on screen. Already-drawn charts stay there."""
    import json
    import shutil
    import subprocess

    from ai_quotas.plots.generate import LAYOUT_JS

    if not shutil.which("node"):
        pytest.skip("node not available")
    script = (
        LAYOUT_JS
        + """
const charts = [];
for (let i = 0; i < 14; i++) {
  charts.push({id: i, top: i * 400, bottom: i * 400 + 380, drawn: false});
}
const split = quotaChartsToPaint(charts, 900, 0);
if (split.now.join(',') !== '0,1,2') throw new Error('now ' + JSON.stringify(split.now));
charts[10].drawn = true;
const again = quotaChartsToPaint(charts, 900, 0);
if (again.now.indexOf(10) < 0) throw new Error('drawn chart left the first paint');
if (again.later.indexOf(10) >= 0) throw new Error('drawn chart deferred');
console.log(JSON.stringify({now: split.now, later: split.later.length}));
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
    got = json.loads(proc.stdout)
    assert got["now"] == [0, 1, 2]
    assert got["later"] == 11


def test_close_plan_changes_share_one_pill():
    """Overlapping Plan change pills become one. The hover keeps each change.

    A week-wide chart is 640px. Four changes a few hours apart overlap.
    Two changes three days apart do not. A money pill between them stays.
    A third change joins only while it still overlaps the first pill.
    """
    import json
    import shutil
    import subprocess

    from ai_quotas.plots.generate import LAYOUT_JS

    if not shutil.which("node"):
        pytest.skip("node not available")
    script = (
        LAYOUT_JS
        + """
function px(seconds) { return seconds / (168 * 3600) * 640; }
const tips = ['5x → 20x', '20x → 5x', '5x → 20x again', '20x → 5x again'];
const hours = [0, 4, 9, 10];
const markers = hours.map((h, i) => ({
  kind: 'plan_change', t: h, x: px(h * 3600), pill: 'Plan change', tooltip: tips[i],
}));
markers.splice(2, 0, {kind: 'burn', t: 5, x: px(5 * 3600), pill: '−$12', tooltip: 'money tip'});
const merged = quotaMergePlanChanges(markers, {xOf: m => m.x, widthOf: () => 93});
const plans = merged.filter(m => m.kind === 'plan_change');
const money = merged.filter(m => m.kind === 'burn');
if (plans.length !== 1) throw new Error('close ' + plans.length);
tips.forEach(tip => { if (plans[0].tooltip.indexOf(tip) < 0) throw new Error('missing ' + tip); });
if (plans[0].tooltip !== tips.join('\\n')) throw new Error('one change per line: ' + plans[0].tooltip);
if (plans[0].pill !== 'Plan change') throw new Error(plans[0].pill);
if (money.length !== 1 || money[0].pill !== '−$12' || money[0].tooltip !== 'money tip') {
  throw new Error('money changed');
}
const far = [0, 72].map((h, i) => ({
  kind: 'plan_change', t: h, x: px(h * 3600), pill: 'Plan change', tooltip: 'tip ' + i,
}));
const apart = quotaMergePlanChanges(far, {xOf: m => m.x, widthOf: () => 93});
if (apart.length !== 2) throw new Error('apart ' + apart.length);
const chain = [0, 80, 160].map((x, i) => ({
  kind: 'plan_change', t: i, x: x, pill: 'Plan change', tooltip: 't' + i,
}));
const chained = quotaMergePlanChanges(chain, {xOf: m => m.x, widthOf: () => 93});
if (chained.length !== 2) throw new Error('chain ' + chained.length);
if (chained[0].tooltip.indexOf('t0') < 0 || chained[0].tooltip.indexOf('t1') < 0) throw new Error(chained[0].tooltip);
if (chained[0].tooltip.indexOf('t2') >= 0) throw new Error('third joined the first cluster');
const one = quotaMergePlanChanges(
  [{kind: 'plan_change', t: 1, x: 0, pill: 'Plan change', tooltip: 'pro → promax'}],
  {xOf: m => m.x, widthOf: () => 93},
);
if (one.length !== 1 || one[0].tooltip !== 'pro → promax') throw new Error('single');
console.log(JSON.stringify({close: plans.length, apart: apart.length, chain: chained.length}));
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
    got = json.loads(proc.stdout)
    assert got == {"close": 1, "apart": 2, "chain": 2}


def test_close_money_pills_do_not_cover_each_other():
    """19 Oct review, 390px phone: "$20 unused" at x=127 and "+$4 bonus" at
    x=186 overlapped, because the rows were a fixed 7% of a short chart (9px)
    and the row was the pill's index, not where there was room.

    Pills that overlap go to separate rows, a pill with room stays on the
    bottom row, and a row is a full pill tall in pixels.
    """
    import json
    import shutil
    import subprocess

    from ai_quotas.plots.generate import LAYOUT_JS

    if not shutil.which("node"):
        pytest.skip("node not available")
    script = LAYOUT_JS + """
const w = t => quotaPlanPillWidth(t);
const items = [
  {x: 127, w: w('$20 unused')},
  {x: 186, w: w('+$4 bonus')},
  {x: 330, w: w('Plan change')},
  {x: 140, w: w('+$1 bonus')},
];
const rows = quotaPillRows(items, 3);
const full = quotaPillRows([{x: 0, w: 100}, {x: 10, w: 100}, {x: 20, w: 100}], 2);
console.log(JSON.stringify({rows, full, rowPx: QUOTA_PILL_ROW_PX}));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10, check=False)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    rows = got["rows"]
    assert rows[0] != rows[1]  # the two that overlapped on the phone
    assert rows[2] == 0  # room on the bottom row
    assert len({rows[0], rows[1], rows[3]}) == 3
    assert got["full"] == [0, 1, 0]
    assert got["rowPx"] >= 22  # a pill is ~22px tall with its border

    from importlib.resources import files

    html = files("ai_quotas.plots.static").joinpath("plotly.html").read_text(encoding="utf-8")
    assert "(i % 4)" not in html
    assert "quotaPillRows(" in html
    # rows are pixels (yshift), not a share of a chart whose height varies
    assert "yshift: pillBottom(i)" in html


def test_plotly_pill_hover_prints_percent_once():
    """The pill copy goes through customdata, not the hovertemplate.

    A hovertemplate prints "%%" literally ("59%% left before", 19 Oct review),
    and a merged Plan change pill shows one change per line.
    """
    from importlib.resources import files

    html = files("ai_quotas.plots.static").joinpath("plotly.html").read_text(encoding="utf-8")
    assert "replace(/%/g, '%%')" not in html
    assert "hovertemplate: '%{customdata}<extra></extra>'" in html
    assert ".replace(/\\n/g, '<br>')" in html
    uplot = files("ai_quotas.plots.static").joinpath("uplot.html").read_text(encoding="utf-8")
    assert "esc(hit.tooltip).replace(/\\n/g, '<br>')" in uplot


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
                "plan": "pro_200",
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
    assert any(p.endswith("unused") for p in pills)
    assert any(p.endswith("bonus") for p in pills)
    tips = [r["tooltip"] for r in payload["resets"]]
    assert any(t.startswith("Quota left unused") for t in tips)
    assert any(t.startswith("Inferred bonus refill") for t in tips)
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


def test_sparse_history_panel_includes_inferred(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone

    from ai_quotas.plots.generate import _vendor_panel_payload
    from ai_quotas.plots.prep import prepare

    now = datetime(2026, 9, 16, 18, tzinfo=timezone.utc)
    reset = now + timedelta(hours=18)
    samples = tmp_path / "one.jsonl"
    samples.write_text(
        json.dumps(
            {
                "ts": now.isoformat(),
                "provider": "codex",
                "window": "week",
                "used_percent": 30.0,
                "resets_at": reset.isoformat(),
                "plan": "pro",
                "status": "ok",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    df, resets, _ = prepare(samples)
    payload = _vendor_panel_payload(df, resets, "Codex")
    assert payload["configured"] is True
    assert payload["inferred"]
    assert payload["inferred"][0]["label"] == "Estimated since last reset"
    assert payload["inferred"][0]["segs"][0][0][1] == 100.0
    assert payload["inferred"][0]["segs"][0][-1][1] == 70.0


def test_empty_samples_write_unconfigured_panels(tmp_path):
    import json

    samples = tmp_path / "none.jsonl"
    samples.write_text(
        '{"ts":"2026-09-16T12:00:00Z","provider":"claude","window":"week","used_percent":null,"status":"unavailable"}\n',
        encoding="utf-8",
    )
    result = generate_plots(samples=samples, out_dir=tmp_path / "plots", engines=("uplot",))
    payload = json.loads(result["panels"].read_text())
    assert payload["panels"]
    assert all(not p["configured"] for p in payload["panels"])
    html = (tmp_path / "plots" / "10_uplot" / "index.html").read_text(encoding="utf-8")
    assert "No quota samples yet" in html


def test_unconfigured_vendor_is_flagged(tmp_path):
    import json

    from ai_quotas.plots.generate import _vendor_panel_payload
    from ai_quotas.plots.prep import prepare

    fixture = Path(__file__).resolve().parent / "fixtures" / "unavailable.jsonl"
    df, resets, _ = prepare(fixture)
    payload = json.loads(
        generate_plots(samples=fixture, out_dir=tmp_path / "plots", engines=("uplot",))["panels"].read_text()
    )
    by = {p["vendor"]: p for p in payload["panels"]}
    assert by["Codex"]["configured"] is True
    assert by["Claude"]["configured"] is False
    assert by["Grok"]["configured"] is False
    assert by["Gemini"]["configured"] is False
    assert by["Kimi"]["configured"] is False
    assert by["Kimi"]["setup"]["source"] == "orca"
    assert by["Gemini"]["setup"]["body"] == "Connect Gemini CLI, then collect a sample."
    assert by["OpenRouter"]["setup"]["body"] == "Set OPENROUTER_API_KEY, then collect a sample."
    assert by["Muse"]["configured"] is False
    assert by["Muse"]["setup"]["provider"] == "muse"
    assert by["Muse"]["setup"]["command"] == "muse login"
    assert "Set it up" in (tmp_path / "plots" / "10_uplot" / "index.html").read_text(encoding="utf-8")
    claude = _vendor_panel_payload(df, resets, "Claude")
    assert claude["configured"] is False
    assert claude["inferred"] == []


def test_range_nav_zoom_math():
    import json
    import shutil
    import subprocess

    from ai_quotas.plots.generate import RANGE_NAV_JS

    if not shutil.which("node"):
        pytest.skip("node not available")
    script = (
        RANGE_NAV_JS
        + """
const week = 7*86400, month = 30*86400;
const c = clampWindow(0, week, 0, month);
const z = zoomWindow(0, week, week/2, 2, 0, month);
const tight = clampWindow(0, 1, 0, month);
console.log(JSON.stringify({c, z, tight, min: RANGE_MIN_WINDOW, week}));
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
    out = json.loads(proc.stdout)
    assert out["c"] == [0, out["week"]]
    assert out["z"][0] < 0 + 1e-6 or out["z"][0] == 0
    assert out["z"][1] > out["week"]
    assert out["tight"][1] - out["tight"][0] == out["min"]
