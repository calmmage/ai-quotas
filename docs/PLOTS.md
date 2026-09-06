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

Each page shows the default 2×2 **Claude / Codex / Grok / Gemini** (Gemini via `AI_QUOTAS_EXTRA_ADAPTERS`). OpenRouter is a built-in adapter and shows up with `--full`, not on that 2×2.

- **plots / row** control (1–4) + auto-scale on resize
- family colors (orange / blue / green / purple)
- resets from used% drops (not claimed `resets_at`); 5h session windows are drawn but **not** marked (too many refreshes)
- money markers: first reset = burn (−$); early reset within window = free (+$)
- reset credits: subtitle badge `1 reset · exp 12 Sep (8d)`; an expired-unused credit is a red marker at its expiry (−one window $); the y-axis never shows >100 %
- boosts: subtitle badge on the vendor panel while a temporary limit perk is active (`+50% through 13 Sep`); y-axis stays ≤ 100 %; history only, no money
- time-axis ticks/grid scale with the 1w / 1m / 1q / all control (day labels on a week, week labels on a month)
- denser grid + burn-density ticks under the curve

Default `data_dir` is `~/.local/share/ai-quotas`; the default source is
`ai-quotas.sqlite3` there (override with `AI_QUOTAS_DATABASE` or
`AI_QUOTAS_DATA_DIR`). Explicit JSONL remains supported via `--samples`.

Page **source** ships in the wheel: `ai_quotas/plots/static/` (`plotly.html`, `uplot.html`, `index.html`, `time_axis.js`, `theme.js`). `generate_plots` fills those templates with sample JSON and writes the runtime files above. Plotly/uPlot JS still loads from CDN.

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
4. on change, regenerate in place; the browser picks up new HTML via a short meta-refresh stamped onto the generated pages
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

Screenshot of the night dash (uPlot), using recorded samples. The Gemini panel has no data in the selected range. Same image as the README hero; this is a static preview.

![Quota remaining dashboard](examples/dash-night.png)

## Tokens and $ (labels / hover, not a second curve)

The y-axis stays **% remaining**. Session token/$ is harvested into SQLite `spend_turns` (`ai-quotas spend`) — a different grain.

1. Hover (Plotly + uPlot): remaining % · leftover $ (remaining% × window value) · leftover tokens when the current reset period can be calibrated ([TOKEN-GAUGE.md](TOKEN-GAUGE.md): tokens observed ÷ Δused%).
2. Reset labels: leftover $ already there; leftover tokens appended when calibrated (`~12k tok`).
3. Daily spend **strip** under each panel + table on `00_INDEX.html`. Not drawn on the remaining-% line.
4. 5h session windows are dimmed (they recycle all day) and are not priced.

Grok `cost_usd` is the TUI estimate. Claude/Codex subscription $ stays unknown; show tokens only. Uncalibrated leftover tokens are omitted, never invented.

## Money rules (short)

1. First reset on a series → **burn** (leftover remaining valued as lost $).
2. Reset before a full expected window since last burn → **free** (+$).
3. Reset after a full window → new **burn**.
4. Rolling 5h session windows are **not priced** (label only).

Rates: Claude/Codex $200/mo · Grok $300/mo · Gemini $30/mo, pro-rated to window length.

## Sources-only policy

Filled dashboards are **generated at runtime** and live under the data dir (gitignored). This repo ships **templates** (`ai_quotas/plots/static/`) + generators + static README PNG examples, not live HTML trees with sample data.
