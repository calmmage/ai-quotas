"""Scheduled deadlines, rather than nominal week length, control the guide."""
from datetime import datetime, timedelta, timezone
import json
import shutil
import subprocess

import pytest

pd = pytest.importorskip("pandas")
from ai_quotas.plots.prep import budget_line, cumulative_burn, prepare
from ai_quotas.plots.generate import _burn_density_ticks, _vendor_panel_payload

T = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


def frame(hours, used, resets):
    return pd.DataFrame({"ts_local": [T + timedelta(hours=h) for h in hours],
                         "used_percent": used, "resets_at": resets})


def test_single_partial_sample_reaches_reported_deadline():
    end = T + timedelta(hours=18)
    assert budget_line(frame([0], [30], [end]), "Codex week") == [[(T, 70), (end, 0)]]


def test_gap_and_corrected_deadline_keep_original_anchor():
    end = T + timedelta(hours=30)
    lines = budget_line(frame([0, 6], [30, 40], [T + timedelta(hours=24), end]), "Codex week")
    assert lines == [[(T, 70), (end, 0)]]


def test_mixed_provider_timestamp_formats_preserve_latest_correction():
    end = T + timedelta(hours=30, microseconds=123456)
    deadlines = [(T + timedelta(hours=24)).isoformat(),
                 end.astimezone(timezone(timedelta(hours=3))).isoformat()]
    assert budget_line(frame([0, 6], [30, 40], deadlines), "Codex week") == [[(T, 70), (end, 0)]]


@pytest.mark.parametrize("reset", [None, "broken", T - timedelta(hours=1)])
def test_no_guessed_deadline(reset):
    assert budget_line(frame([0], [30], [reset]), "Codex week") == []


def test_early_refill_aims_old_budget_at_zero_at_the_reset():
    old = T + timedelta(hours=24)
    new = T + timedelta(hours=72)
    lines = budget_line(frame([0, 6, 12], [20, 40, 0], [old, old, new]), "Codex week")
    assert lines == [[(T, 80), (T + timedelta(hours=12), 0)],
                     [(T + timedelta(hours=12), 100), (new, 0)]]


def test_small_gap_keeps_one_burn_segment():
    g = frame([0, 1, 8], [20, 30, 50], [T + timedelta(days=4)] * 3)
    w = cumulative_burn(g)
    assert w.seg == [0, 0, 0]
    assert w.inc[-1] == 20.0


def test_multi_day_gap_still_restarts_burn_segment():
    g = frame([0, 1, 18], [20, 30, 50], [T + timedelta(days=4)] * 3)
    w = cumulative_burn(g)
    assert w.seg[-1] == 1
    assert w.inc[-1] == 0.0


def test_burn_ticks_fill_small_gap():
    g = frame([0, 1, 8], [0, 20, 80], [T + timedelta(days=4)] * 3)
    ticks = _burn_density_ticks(g, target_ticks=15)
    mid = [t for t, _ in ticks if T + timedelta(hours=1) < t < T + timedelta(hours=8)]
    assert mid


def test_crossed_reset_splits_even_when_usage_drop_was_missed():
    old = T + timedelta(hours=12)
    new = T + timedelta(hours=48)
    lines = budget_line(frame([0, 18], [30, 40], [old, new]), "Codex week")
    assert lines == [[(T, 70), (old, 0)], [(T + timedelta(hours=18), 60), (new, 0)]]


def test_known_scheduled_reset_reaches_zero_not_first_post_reset_sample():
    old = T + timedelta(hours=24)
    new = T + timedelta(days=8)
    lines = budget_line(frame([0, 23, 25], [10, 65, 0], [old, old, new]), 'Codex week')
    assert lines[0] == [(T, 90), (old, 0)]
    assert lines[1] == [(T + timedelta(hours=25), 100), (new, 0)]


def test_source_reset_survives_to_both_engines_payload(tmp_path):
    end = T + timedelta(hours=21)
    samples = tmp_path / "samples.jsonl"
    samples.write_text(json.dumps({"ts": T.isoformat(), "provider": "codex", "window": "week",
                                  "used_percent": 30, "resets_at": end.isoformat(), "status": "ok"}) + "\n")
    df, resets, _ = prepare(samples)
    panel = _vendor_panel_payload(df, resets, "Codex")
    assert panel["budget"][0]["segs"] == [[[int(T.timestamp()), 70], [int(end.timestamp()), 0]]]


def test_resumed_samples_keep_unknown_gap_in_shared_plot_payload(tmp_path):
    samples = tmp_path / 'samples.jsonl'
    rows = [{"ts": (T + timedelta(hours=h)).isoformat(), "provider": "grok", "window": "week",
             "used_percent": used, "resets_at": (T + timedelta(days=4)).isoformat(), "status": "ok"}
            for h, used in [(0, 20), (1, 25), (8, 40)]]
    samples.write_text('\n'.join(map(json.dumps, rows)) + '\n')
    df, resets, _ = prepare(samples)
    panel = _vendor_panel_payload(df, resets, 'Grok')
    series = next(s for s in panel['series'] if s['label'] == 'Grok week')
    # 7h hole is small — assume continuity and keep the usage line connected.
    assert series['y'] == [80, 75, 60]
    assert panel['sampled_at'] == int((T + timedelta(hours=8)).timestamp())


def test_multi_day_gap_still_breaks_the_usage_line(tmp_path):
    samples = tmp_path / 'samples.jsonl'
    rows = [{"ts": (T + timedelta(hours=h)).isoformat(), "provider": "grok", "window": "week",
             "used_percent": used, "resets_at": (T + timedelta(days=4)).isoformat(), "status": "ok"}
            for h, used in [(0, 20), (1, 25), (18, 40)]]
    samples.write_text('\n'.join(map(json.dumps, rows)) + '\n')
    df, resets, _ = prepare(samples)
    panel = _vendor_panel_payload(df, resets, 'Grok')
    series = next(s for s in panel['series'] if s['label'] == 'Grok week')
    assert series['y'] == [80, 75, None, 60]


@pytest.mark.parametrize("engine", ["plotly", "uplot"])
@pytest.mark.parametrize("span", [0, 7, 30])
def test_view_preserves_history_and_adds_day_after_now(engine, span):
    from importlib.resources import files
    if not shutil.which("node"):
        pytest.skip("node not available")
    template = files("ai_quotas.plots.static").joinpath(engine + ".html").read_text()
    func = template.split("function viewMinMax() {", 1)[1].split("\n}", 1)[0]
    script = f"const LAST_T=1000000, FIRST_T=1, spanDays={span}; Date.now=()=>1003600*1000;\n"
    script += "function viewMinMax(){" + func + "\n}\nconsole.log(JSON.stringify(viewMinMax()));"
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [max(1, 1000000 - span * 86400) if span else 1, 1003600 + 86400]
