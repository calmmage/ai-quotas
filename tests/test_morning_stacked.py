"""Morning album: one stacked photo per multi-login app, same numbers as the dashboard's stack.

Petr, 13 Nov 2026: «update the daily Telegram notifications to send stacked plots
instead of the single plots».
"""
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from importlib.resources import files

import pytest

from ai_quotas import morning, stacked_plot

NOW = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)  # 09:00 Moscow
T = int(NOW.timestamp())


def panel(provider, account, price, label, *, stale=False, window="week", color="#5B9BD5", declared=None):
    at = T - 3 * 86400 if stale else T - 600
    times = [at - 4 * 86400, at - 2 * 86400, at - 86400, at]
    return {"provider": provider, "account": account, "account_label": label, "source": {"account": account},
            "subscription": {"monthly_usd": price, "source": "configured" if price else "unknown",
                             "regular_allocations": 720 / 168, "included_resets": 0},
            "declared_subscription": declared,
            "series": [{"window": window, "label": "week", "color": color, "t": times,
                        "y": [100, 80, None, 60], "resets_at": T + 3 * 86400}],
            "burn_ticks": [{"color": color, "pts": [[times[1], 80], [times[3], 60]]}],
            "resets": [{"t": times[0], "kind": "burn", "edge_dark": "#ff5c72", "tooltip": "reset"}],
            "budget": [{"color": color, "segs": [[[times[0], 100], [T + 3 * 86400, 0]]]}],
            "inferred": []}


def doc(written=NOW):
    panels = [
        panel("claude", None, 200, "personal", color="#C49A6C"),
        panel("claude", "shared@example.test", 200, "shared", color="#C49A6C"),
        panel("claude", "work@example.test", None, "work", color="#C49A6C"),
        panel("codex", None, 500, "personal"),
        panel("codex", "shared@example.test", 100, "shared"),
        panel("grok", None, 300, "personal", color="#7CB87C"),
        panel("agy", None, 0, "personal", window="week_gemini_pro", declared={"plan": "Google AI Pro", "monthly_usd": 20}),
        panel("agy", "free@example.test", 0, "free", window="week_gemini_pro"),
        panel("agy", "old@example.test", 20, "old", window="week_gemini_pro", stale=True),
    ]
    for p in panels:
        if p["account"] is None:
            p["source"]["account"] = "me@example.test"
    return {"written_at": written.isoformat(), "panels": panels}


def test_stacks_match_the_shipped_dashboard_stack():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    raw = doc()
    source = files("ai_quotas.plots.static").joinpath("stacked.js").read_text()
    script = """
const raw=JSON.parse(require('fs').readFileSync(0,'utf8'));
let quotaLayoutRead=()=>null,quotaDisplayTitle=p=>p.title,quotaSeriesLegend=(p,s)=>s,quotaHeader=()=>'',fillPanel=()=>{},updateQuotaHeader=()=>{};
const quotaLastNumber=ys=>ys.findLast(Number.isFinite),window={addEventListener(){}};
""" + source + """
console.log(JSON.stringify(quotaStackedStacks(raw).map(p=>({provider:p.provider,capacity:p.stack_capacity,
  observed_total:p.stack_observed_total,stale:p.stack_stale,assumed:p.stack_assumed,
  layers:p.series.slice(1).map(s=>[s.label,s.stack_weight,s.stack_weight_source,s.stack_stale,s.color])}))));
"""
    result = subprocess.run(["node", "-e", script], input=json.dumps(raw), capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    js = {p["provider"]: p for p in json.loads(result.stdout)}
    py = stacked_plot.build_stacks(raw)
    assert set(py) == set(js) == {"claude", "codex", "agy"}  # one Grok login: not stacked
    for provider, stack in py.items():
        want = js[provider]
        assert stack["capacity"] == pytest.approx(want["capacity"])
        assert stack["observed_total"] == pytest.approx(want["observed_total"])
        assert (stack["stale"], stack["assumed"]) == (want["stale"], want["assumed"])
        assert [[a["label"], pytest.approx(a["weight"]), "assumed" if a["assumed"] else "price", a["stale"], a["color"]]
                for a in stack["layers"]] == want["layers"]


def test_weights_follow_prices_and_unknown_is_assumed():
    stacks = stacked_plot.build_stacks(doc())
    assert [a["weight"] for a in stacks["codex"]["layers"]] == [5.0, 1.0]
    claude = stacks["claude"]
    assert [(a["label"], a["weight"], a["assumed"]) for a in claude["layers"]] == [
        ("personal", 2.0, False), ("shared", 2.0, False), ("work", 2.0, True)]
    assert stacked_plot.header_total(claude) == "3.6/6 left"
    assert stacked_plot.left_percent(claude) == 60
    google = stacks["agy"]
    assert stacked_plot.header_total(google) == "0.24 left 1 stale"
    assert stacked_plot.left_percent(google) == 60  # the stale login is not a current reading


def test_stack_png_draws_and_survives_bad_data():
    stacks = stacked_plot.build_stacks(doc())
    png = stacked_plot.stack_png(stacks["claude"], now=NOW, tz=timezone.utc)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000
    broken = dict(stacks["claude"], layers=[{"obs": "nonsense"}])
    assert stacked_plot.stack_png(broken, now=NOW) is None


def test_old_missing_or_future_panels_are_not_used(tmp_path):
    path = tmp_path / "panels.json"
    path.write_text(json.dumps(doc()))
    assert stacked_plot.load_panels(path, now=NOW + timedelta(hours=1)) is not None
    assert stacked_plot.load_panels(path, now=NOW + timedelta(hours=7)) is None
    assert stacked_plot.load_panels(path, now=NOW - timedelta(days=10)) is None
    assert stacked_plot.load_panels(tmp_path / "missing.json", now=NOW) is None


def test_morning_album_sends_stacks_instead_of_per_login_plots(tmp_path, monkeypatch):
    panels_file = tmp_path / "panels.json"
    panels_file.write_text(json.dumps(doc()))
    logins = {"claude": [None, "shared@example.test", "work@example.test"], "codex": [None, "shared@example.test"],
              "grok": [None], "agy": [None, "free@example.test"]}
    monkeypatch.setattr(morning, "_panels", lambda samples, now: [(p, a) for p, accts in logins.items() for a in accts])
    singles = []
    monkeypatch.setattr(morning, "plot_png", lambda samples, provider, account, **kw: singles.append(provider) or b"\x89PNG single")
    photos, shown = morning._photos([], stacked_plot.build_stacks(stacked_plot.load_panels(panels_file, now=NOW)),
                                    NOW, timezone.utc)
    assert shown == ["Claude stack", "Codex stack", "Grok", "Google stack"]
    assert singles == ["grok"] and len(photos) == 4
    # Without fresh dashboard data the report keeps the per-login plots.
    singles.clear()
    photos, shown = morning._photos([], {}, NOW, timezone.utc)
    assert len(photos) == 8 and singles.count("claude") == 3


def test_left_line_uses_the_weighted_stack_where_there_is_one():
    stacks = stacked_plot.build_stacks(doc())
    result = {"verdicts": {"claude": {"used_percent": 2}, "grok": {"used_percent": 14}}}
    assert morning._left_line(result, stacks) == "Claude 60% · Codex 60% · Grok 86% · Google 60%"
    assert morning._left_line(result) == "Claude 98% · Grok 86%"
    # Muse silent for a week: left out, not «Muse 100%» from its last old reading.
    result["verdicts"]["muse"] = {"used_percent": 0}
    assert morning._left_line(result, stacks, recent={"grok"}).endswith("Grok 86% · Google 60%")


def test_caption_keeps_money_lines_and_stays_under_the_limit():
    from ai_quotas.alert_text import plain

    money = {"visible": ["Grok $76 yesterday"], "fold": [f"detail line {i} " + "x" * 30 for i in range(40)]}
    many = [{"ts": NOW.isoformat(), "head": f"use it before reset · Grok · login{i}"} for i in range(80)]
    caption = morning.format_caption(today=NOW, left="Claude 60%", alerts=many, tz=timezone.utc, money=money)
    text = plain(caption)
    assert len(text) <= 1024 and "+" in text and "more" in text
    assert text.split("\n")[:3] == ["morning quotas · Fri 13 Nov", "Claude 60%", "Grok $76 yesterday"]
    assert caption.count("<blockquote expandable>") == 2
    quiet = morning.format_caption(today=NOW, left="Claude 60%", alerts=[], tz=timezone.utc,
                                   money={"visible": [], "fold": []})
    assert quiet == "morning quotas · Fri 13 Nov\nClaude 60%\nno alerts since yesterday"
