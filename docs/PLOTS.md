# Plots

Interactive multi-vendor dashboards for subscription quota **% remaining** over time.

[Install ai-quotas](../README.md#install) · [All docs](../README.md#docs)

## What you get

| Engine | File | Notes |
|--------|------|--------|
| Live | `<data_dir>/plots/live.html` | Default landing. Day = Plotly, night = uPlot (`localStorage.quota-theme`) |
| Plotly | `<data_dir>/plots/03_plotly/index.html` | Day / light, hover tooltips (kept as backup) |
| uPlot | `<data_dir>/plots/10_uplot/index.html` | Night / dark canvas, fast (kept as backup) |
| Index | `<data_dir>/plots/00_INDEX.html` | Money table + reset list + links. Linked from the plot header |
| Data | `<data_dir>/plots/panels.json` (+ `.gz`) | The series both engine pages fetch and redraw in place. The pages themselves are static shells |

Each page shows the default 2×2 **Claude / Codex / Grok / Gemini** (Gemini via `AI_QUOTAS_EXTRA_ADAPTERS`). OpenRouter is a built-in adapter and shows up with `--full`, not on that 2×2.

- **plots / row** control (1–4) + auto-scale on resize
- family colors (orange / blue / green / purple)
- resets from used% drops (not claimed `resets_at`); 5h session windows are drawn but **not** marked (too many refreshes)
- false refill: remaining jumps up then snaps back to the previous used% within 3h — those samples are dropped (a real reset stays high and burns down)
- small sampling holes (≤ 12h) keep the usage line connected (assume continuity); gaps longer than 12h still insert a NaN break so a restored sampler cannot invent a line across days
- uPlot night view aligns series onto a shared x-axis: missing timestamps of *another* window are spanned (not drawn as holes). Only the >12h NaNs break the line
- time axis zooms freely (drag box / wheel). 1w / 1m / 1q / all are snap presets; double-click returns to the last snap
- zoomed out past ~10 days: hide 5h session spikes, thin burn ticks, keep only a handful of $ reset pills so month/quarter stays readable
- money markers: first reset = burn (−$ leftover); early reset within window = free (+$ of used% refilled). On the plot these are short pills (`+$42` / `-$45`); the full line (Lost unused / Gained free · series · leftover · tokens) is the hover tooltip. Nested scoped windows (Claude Fable / `weekly_scoped`) are drawn but **not** priced — only the billed total (Claude week / `weekly_all`) carries $
- reset credits: subtitle badge `1 reset · exp 12 Sep (8d)` while available; **Reset expired** / **Reset used** pills on the timeline (hover has the credit title and $); the y-axis never shows >100 %
- boosts: subtitle badge on the vendor panel while a temporary limit perk is active (`+50% through 13 Sep`); y-axis stays ≤ 100 %; history only, no money
- time-axis ticks/grid scale with the visible window (day labels on a week, week labels on a month), including after a free zoom
- denser grid + burn-density ticks under the curve (ticks keep going across holes ≤ 12h, same as the usage line)
- budget dotted line aims at 0 at the window's real end: reported deadline if still open, or the observed reset if the window already refilled early

Default `data_dir` is `~/.local/share/ai-quotas`; the default source is
`ai-quotas.sqlite3` there (override with `AI_QUOTAS_DATABASE` or
`AI_QUOTAS_DATA_DIR`). Explicit JSONL remains supported via `--samples`.

Page **source** ships in the wheel: `ai_quotas/plots/static/` (`plotly.html`, `uplot.html`, `index.html`, `time_axis.js`, `theme.js`, `panel_header.js`/`.css`, `live_refresh.js`). `generate_plots` writes the sample data to `panels.json` and the engine pages as **static shells**: byte-identical across regenerations, so a browser keeps them cached and only re-fetches the data. Plotly (`plotly-basic`, ~1 MB instead of the 4.5 MB full bundle) and uPlot still load from CDN, deferred, while the page already paints the four vendor panels as placeholders.

## Loading and refresh

- First paint is the header plus four skeleton panels (vendor name, reserved chart height). Charts fill in when the library and `panels.json` have arrived; panels fade in once.
- The dash serves everything `Cache-Control: no-cache` with `Last-Modified`: repeat visits revalidate and get `304` until a regeneration; the API is `no-store`. Clients that accept gzip get the `.gz` sibling written next to each shell and `panels.json` (≈ 40 KB instead of ≈ 290 KB).
- Open pages poll `meta.json` (200 bytes) every `poll_interval_s` and, when `generated_at` changes, fetch `panels.json` and redraw **in place**: uPlot `setData`, `Plotly.react`. Nothing reloads, so there is no flicker on a tick. Changed numbers in the panel header dip briefly; the header dot pulses. Under `prefers-reduced-motion` all motion is off.
- A hidden tab skips polls and catches up when it becomes visible. Plain `ai-quotas plot` output has no `meta.json`, so the page paints once and stops polling.
- `panels.json` carries `shell_version` (a hash of the page sources). A page whose sources were redeployed reloads itself once, deferred while a settings dialog is open.

Default landing (`ai-quotas dash --open`) is the **plot**, not the nav index: `http://127.0.0.1:8765/live.html`. Day/night in the header swaps Plotly ↔ uPlot (same % remaining series; not a second curve). `index` in that header opens the money/reset page.

## Where the dash lives

| Surface | How |
|---------|-----|
| Package | `ai-quotas plot` / `ai-quotas dash` writes `<data_dir>/plots/` (`live.html`, `03_plotly/`, `10_uplot/`, `00_INDEX.html`) |
| Local viewer | `ai-quotas dash --open` serves that directory on `127.0.0.1` only (default port 8765) |
| Optional copy | `AI_QUOTAS_AFTER_REGEN` / `--after-regen CMD` runs after each generation so you can rsync the plots dir to a static host. Not installed by the package. A copied `live.html` shows a stale bar past 2 h. |

## CLI

Run these from the cloned repo after [installation](../README.md#install); `make setup` includes the plot dependencies.

```bash
uv run ai-quotas plot                          # live samples path
uv run ai-quotas --samples path/to.jsonl plot  # explicit file (root flag)
uv run ai-quotas plot --out ./my-plots --open
uv run ai-quotas plot --money                  # also print $ report
uv run ai-quotas dash --open                   # generate + local server
```

## Live viewer (`dash`)

`ai-quotas dash` is the same generators as `plot`, plus a **local** stdlib HTTP server.

It is **not** a push stream. The loop is:

1. `generate_plots` into `--out` or `<data_dir>/plots`
2. serve that directory on `127.0.0.1` only (default port 8765)
3. poll the SQLite sample count/max-id change token every `--interval` seconds (default 15)
4. on change, regenerate in place; open pages see the new `generated_at` in `meta.json` on their next poll and redraw from the fresh `panels.json` without reloading (see *Loading and refresh*)
5. after every successful generation (the first one included): write `meta.json` (`generated_at` UTC, `stale_after_s`, `poll_interval_s`, `host`, `producer`), stamp the same `generated_at` into `live.html`, then run `AI_QUOTAS_AFTER_REGEN` / `--after-regen CMD` in a background thread — 60 s timeout, one run at a time (a fire during a running hook is skipped), failures logged (`hook fail rc=N` / `hook timeout`) and never fatal

```bash
uv run ai-quotas dash --open
uv run ai-quotas --samples path/to.jsonl dash --port 8765 --interval 15
uv run ai-quotas dash --engine plotly --out ./my-plots
```

`live.html` re-checks `meta.json` every minute and shows `plots generated DD MMM YYYY HH:MM · stale` once the stamp is older than 2 h — nothing is shown while fresh. A copied plots dir shows the same stale bar when the producer is off.

`--open` opens `http://127.0.0.1:<port>/live.html` (the plot; day/night remembers the last engine). Bind failure prints the error and exits 1 (no silent port hop unless you pass `--port 0`, which prints the chosen URL). Ctrl-C stops the server. Plot dependencies are included in `make setup`.

## Library

```python
from pathlib import Path
from ai_quotas import generate_plots, prepare_plots, is_reset, classify_money

df, resets, cutoff = prepare_plots()  # default SQLite database
result = generate_plots(out_dir=Path("/tmp/qplots"))
print(result["index"])
```

Core install stays **stdlib-only**. Plot stack is optional: `ai-quotas[plot]` → pandas, plotly.

## Example preview

Screenshot of the night dash (uPlot), using recorded samples. Same image as the README hero; this is a static preview.

![Quota remaining dashboard](examples/dash-night.png)

## Tokens and $ (labels / hover, not a second curve)

The y-axis stays **% remaining**. Session token/$ is harvested into SQLite `spend_turns` (`ai-quotas spend`) — a different grain.

1. Hover (Plotly + uPlot): remaining % · leftover $ (remaining% × window value) · leftover tokens when the current reset period can be calibrated ([TOKEN-GAUGE.md](TOKEN-GAUGE.md): tokens observed ÷ Δused%).
2. Reset pills on the canvas: `+$N` / `-$N` / `Reset expired` / `Reset used`. Hover tooltip: Lost unused (leftover remaining × window) / Gained free (used% refilled × window), period, tokens when calibrated (`~12k tok`).
3. Daily spend **strip** under each panel + table on `00_INDEX.html`. Not drawn on the remaining-% line.
4. 5h session windows are dimmed (they recycle all day) and are not priced.

Grok `cost_usd` is the TUI estimate. Claude/Codex subscription $ stays unknown; show tokens only. Uncalibrated leftover tokens are omitted, never invented.

## Money rules (short)

1. First reset on a series → **burn** (leftover remaining valued as lost $).
2. Reset before a full expected window since last burn → **free** (+$ of the **used% refilled**, not leftover remaining: 80% left → +20% of the window).
3. Reset after a full window → new **burn**.
4. Rolling 5h session windows are **not priced** (label only).
5. Nested scoped windows (Claude Fable) are **not priced** — they sit inside the weekly total. One pill per reset, on the billed total only.

Rates: Claude/Codex $200/mo · Grok $300/mo · Gemini $30/mo, pro-rated to window length.

## Sources-only policy

Filled dashboards are **generated at runtime** and live under the data dir (gitignored). This repo ships **templates** (`ai_quotas/plots/static/`) + generators + static README PNG examples, not live HTML trees with sample data.
