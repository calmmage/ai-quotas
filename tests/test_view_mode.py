"""Phone or desktop layout: decided once in <head>, never by a width media query.

Petr, 3 Nov 2026: "Fix mobile view: right now it's too much bulky text, too few
plots" and "the 'request desktop version' doesn't work (probably because of
dimension-based layout..)".
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "ai_quotas" / "plots" / "static"
VIEW_JS = (STATIC / "view_mode.js").read_text(encoding="utf-8")
LAYOUT_JS = (STATIC / "layout.js").read_text(encoding="utf-8")

IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15"


def _node(script: str):
    if not shutil.which("node"):
        pytest.skip("node not available")
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _env(**kw):
    env = {"saved": None, "platform": "iPhone", "ua": IPHONE_UA, "touch": 5, "width": 390, "screen": 390}
    env.update(kw)
    return env


CASES = {
    "phone": _env(),
    # iOS Safari "Request Desktop Website": Mac agent, touch screen, device-width tag.
    "desktop_request": _env(platform="MacIntel", ua=MAC_UA),
    "saved_desktop_on_phone": _env(saved="desktop"),
    "saved_phone_on_desktop_request": _env(saved="phone", platform="MacIntel", ua=MAC_UA),
    "laptop": _env(platform="MacIntel", ua=MAC_UA, touch=0, width=1280, screen=900),
    "narrow_laptop_window": _env(platform="MacIntel", ua=MAC_UA, touch=0, width=400, screen=900),
    "saved_phone_on_laptop": _env(saved="phone", platform="MacIntel", ua=MAC_UA, touch=0, width=1280, screen=900),
    "android_desktop_site": _env(platform="Linux armv8l", ua="Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0 Safari/537.36"),
    "phone_landscape": _env(width=844),
}


def test_view_mode_decision_order():
    got = _node(VIEW_JS + """
const cases = %s;
const out = {};
for (const [k, env] of Object.entries(cases)) {
  const mode = quotaViewMode(env);
  out[k] = {mode, viewport: quotaViewport(mode, env), toggle: quotaViewSwitchable(env),
    unsaved: quotaUnsavedView(env, mode)};
}
console.log(JSON.stringify(out));
""" % json.dumps(CASES))
    want = {
        "phone": ("phone", "width=device-width, initial-scale=1", True),
        "desktop_request": ("desktop", "width=1100, initial-scale=0.355", True),
        "saved_desktop_on_phone": ("desktop", "width=1100, initial-scale=0.355", True),
        "saved_phone_on_desktop_request": ("phone", "width=device-width, initial-scale=1", True),
        "laptop": ("desktop", "width=device-width, initial-scale=1", False),
        "narrow_laptop_window": ("phone", "width=device-width, initial-scale=1", True),
        "saved_phone_on_laptop": ("phone", "width=device-width, initial-scale=1", True),
        "android_desktop_site": ("desktop", "width=1100, initial-scale=0.355", True),
        # Width-decided desktop keeps device width, so rotating back is a phone again.
        "phone_landscape": ("desktop", "width=device-width, initial-scale=1", True),
    }
    # With no saved choice: what the toggle compares against before saving.
    # A desktop width this page forced on a phone does not count.
    unsaved = {"saved_desktop_on_phone": "phone", "saved_phone_on_desktop_request": "desktop",
               "saved_phone_on_laptop": "desktop"}
    for name, (mode, viewport, toggle) in want.items():
        want_unsaved = unsaved.get(name, mode)
        assert got[name] == {"mode": mode, "viewport": viewport, "toggle": toggle, "unsaved": want_unsaved}, name


FAKE_WINDOW = """
function fakeWindow(opts) {
  const store = new Map(Object.entries(opts.store || {}));
  const meta = {attrs: {content: 'width=device-width, initial-scale=1'},
    getAttribute(k) { return this.attrs[k]; }, setAttribute(k, v) { this.attrs[k] = v; }};
  const win = {
    innerWidth: opts.width, screen: {width: opts.screen, height: 844},
    navigator: {platform: opts.platform, userAgent: opts.ua, maxTouchPoints: opts.touch},
    localStorage: opts.blocked ? {getItem() { throw new Error('denied'); }} : {
      getItem: k => store.has(k) ? store.get(k) : null,
      setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k)},
    document: {documentElement: {dataset: {}}, querySelector: () => meta},
  };
  return {win, meta, store};
}
"""


def test_apply_view_sets_attribute_and_viewport_before_layout():
    got = _node(VIEW_JS + FAKE_WINDOW + """
const iphone = %s, mac = %s;
const out = {};
for (const [k, opts] of Object.entries({
  phone: {...iphone, width: 390, screen: 390, touch: 5},
  request: {...mac, width: 390, screen: 390, touch: 5},
  stored: {...iphone, width: 390, screen: 390, touch: 5, store: {'quota-view': 'desktop'}},
  blocked: {...iphone, width: 390, screen: 390, touch: 5, blocked: true},
})) {
  const f = fakeWindow(opts);
  const mode = quotaApplyView(f.win);
  out[k] = [mode, f.win.document.documentElement.dataset.view, f.meta.attrs.content];
}
console.log(JSON.stringify(out));
""" % (json.dumps({"platform": "iPhone", "ua": IPHONE_UA}), json.dumps({"platform": "MacIntel", "ua": MAC_UA})))
    assert got["phone"] == ["phone", "phone", "width=device-width, initial-scale=1"]
    assert got["request"] == ["desktop", "desktop", "width=1100, initial-scale=0.355"]
    assert got["stored"] == ["desktop", "desktop", "width=1100, initial-scale=0.355"]
    # Storage blocked (private mode): falls back to the device, never throws.
    assert got["blocked"] == ["phone", "phone", "width=device-width, initial-scale=1"]


def test_layout_columns_follow_the_mode_not_the_width():
    got = _node(VIEW_JS.split("if (typeof window !== 'undefined'")[0] + """
global.document = {
  documentElement: {dataset: {}, style: {setProperty() {}}},
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
};
""" + LAYOUT_JS + """
const layout = createQuotaLayout('uplot', () => {});
const out = {};
document.documentElement.dataset.view = 'phone';
out.phone = [layout.columns(6, 390), layout.columns(6, 1100)];
document.documentElement.dataset.view = 'desktop';
out.desktop = [layout.columns(6, 390), layout.columns(6, 1100)];
console.log(JSON.stringify(out));
""")
    assert got == {"phone": [1, 1], "desktop": [1, 2]}


@pytest.mark.parametrize("name", ["layout.css", "panel_header.css", "uplot.html", "plotly.html"])
def test_phone_css_is_scoped_to_the_mode(name):
    text = (STATIC / name).read_text(encoding="utf-8")
    # A width query would ignore the desktop request and the header toggle.
    assert not re.search(r"@media[^{]*max-width:\s*4[0-9]{2}px", text), name
    assert "innerWidth || w) <= 430" not in text


def test_phone_header_is_one_line_per_fact():
    css = (STATIC / "panel_header.css").read_text(encoding="utf-8")
    phone = "\n".join(line for line in css.splitlines() if "data-view=phone" in line)
    # Credits and resets lose the boxed card and the big number.
    assert ".credit-balance:not([hidden])" in phone and "border:0" in css
    assert re.search(r"\(\.credit-balance, \.reset-reserve\) strong \{\s*font-size:12px", css)
    assert ".wordy" in phone and ".reset-extra { display:none; }" in phone
    # Extras come back on tap.
    assert "[data-open]" in phone
    js = (STATIC / "panel_header.js").read_text(encoding="utf-8")
    assert "quotaWordyLabel(" in js and "data-open" in js


def test_shells_and_live_page_decide_the_mode_in_head(tmp_path):
    pytest.importorskip("pandas")
    from ai_quotas.plots.dash import write_live_page
    from ai_quotas.plots.generate import generate_plots

    fixture = Path(__file__).resolve().parent / "fixtures" / "multi.jsonl"
    out = tmp_path / "plots"
    generate_plots(samples=fixture, out_dir=out, engines=("plotly", "uplot"))
    write_live_page(out, interval=60)
    pages = [out / "03_plotly" / "index.html", out / "10_uplot" / "index.html", out / "live.html"]
    for page in pages:
        html = page.read_text(encoding="utf-8")
        head = html.split("</head>", 1)[0]
        assert "function quotaApplyView" in head, page
        # After the viewport tag it rewrites, before any styles or body.
        assert head.index('name="viewport"') < head.index("quotaApplyView(window)"), page
        assert "__VIEW_MODE_JS__" not in html
    for page in pages[:2]:
        assert "data-view-toggle" in page.read_text(encoding="utf-8")
