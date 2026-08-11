# ai-quotas

Standalone **subscription quota** sampler, trend math, and human CLI for AI vendor CLIs (Claude, Codex, Grok, OpenRouter).

Stdlib-only runtime. No cloud service of its own — it reads **your** logged-in credentials and writes local samples to a JSONL file.

> **Caveat:** Vendor usage endpoints are unofficial/undocumented and may change without notice. This tool accesses them with **your own** logged-in credentials only. Use at your own risk.


## Ecosystem

One package, five surfaces:

| Surface | How |
|---------|-----|
| **CLI** | `ai-quotas` table · `sample` · `plot` · `history` · `verdicts` |
| **Library** | `load_samples`, `sample_now`, `prepare_plots`, `generate_plots`, `is_reset`, `classify_money` |
| **Plots** | Multi-vendor plotly + uplot dashboards (optional deps) — see [docs/PLOTS.md](docs/PLOTS.md) |
| **Automation** | LaunchAgent template → `ai-quotas sample` every 30m (`make install-automation`) |
| **Setup** | `make setup` / `make install-plot` / `make doctor` |

```bash
# Core (stdlib runtime — table + collector)
uv sync --extra dev
uv run ai-quotas --no-refresh

# Plots
uv sync --extra plot
uv run ai-quotas plot --open

# Full local setup + paths check
make setup
make sample && make table && make plot
```

### Plot previews

![% remaining by vendor](docs/examples/remaining-by-vendor.png)

![Codex example](docs/examples/codex-remaining-example.png)

Interactive dashboards are **generated** into `~/.local/share/ai-quotas/plots/` (not committed). Static PNGs above are docs-only examples.

### Automation (macOS)

```bash
make dry-run-automation   # print resolved program path
make install-automation   # LaunchAgent: uv run ai-quotas sample @ 30m
# optional cutover: keep writing the old calmmage samples file
# AI_QUOTAS_SAMPLES=~/calmmage/data/automation_logs/quota/samples.jsonl make install-automation
```

Old agent `com.calmmage.quota-snapshot` (poc watchdog) can be unloaded after cutover:
`launchctl bootout gui/$(id -u)/com.calmmage.quota-snapshot`.

## Install

```bash
# from a clone
uv sync --extra dev          # or: pip install -e ".[dev]"
uv run ai-quotas --help
```

Python ≥ 3.11. Runtime dependencies: **none** (stdlib). Optional plots: `uv sync --extra plot`.

### Optional external tools

| tool | used by | notes |
|---|---|---|
| `claude` CLI | Claude adapter | keeps OAuth token fresh in macOS Keychain / `~/.claude` |
| `codex` CLI | Codex offline fallback | writes rate limits into `~/.codex/sessions` |
| [codexbar](https://github.com/steipete/CodexBar) | Codex live probe | optional; `CODEXBAR_BIN` or PATH or Homebrew |
| `grok` CLI | Grok adapter | `~/.grok/auth.json` |
| OpenRouter API key | OpenRouter adapter | `OPENROUTER_API_KEY` env or `~/.env` |

## Quickstart

```bash
# Human table (soft-collect if samples older than 5 minutes)
ai-quotas

# Force a live probe
ai-quotas -r

# Cache only (offline)
ai-quotas --no-refresh

# Display-model JSON (metrics + burn pairs + color enums)
ai-quotas --json --no-refresh

# Machine verdicts (exit 0=OK, 1=WARN, 2=STOP)
python -m ai_quotas.collector
python -m ai_quotas.collector --no-sample --pretty

# History: peak used% per reset period
ai-quotas history
ai-quotas legend
```

### Offline demo (no vendor accounts)

```bash
AI_QUOTAS_SAMPLES=tests/fixtures/multi.jsonl uv run ai-quotas --no-refresh
AI_QUOTAS_SAMPLES=tests/fixtures/multi.jsonl uv run ai-quotas --json --no-refresh
python -m ai_quotas.collector --no-sample --samples tests/fixtures/multi.jsonl
```

## Adapters (public v1)

| provider | windows | auth |
|---|---|---|
| **claude** | `5h`, `week`, `week_*` (e.g. Fable), `overage_credits` | macOS Keychain `Claude Code-credentials` or `~/.claude/.credentials.json` (read-only) |
| **codex** | `5h`, `week`, … | live `codexbar` → offline `~/.codex/sessions` rollout |
| **grok** | `week`, `month` | `~/.grok/auth.json` (in-memory token refresh only) |
| **openrouter** | `credits`, `free_daily` | `OPENROUTER_API_KEY` / `OPENROUTER_KEY` or `~/.env` |

Private drop-in adapters (e.g. owner-only vendors): set `AI_QUOTAS_EXTRA_ADAPTERS=/path/to/dir` with `*.py` modules exposing `snapshot(ts)`.

## Configuration

| env / flag | meaning |
|---|---|
| `AI_QUOTAS_SAMPLES` | full path to `samples.jsonl` |
| `AI_QUOTAS_DATA_DIR` | directory; file becomes `$DIR/samples.jsonl` |
| `AI_QUOTAS_EXTRA_ADAPTERS` | directory of extra adapter modules |
| `CODEXBAR_BIN` | path to `codexbar` |
| `--samples PATH` | CLI override for the samples file |

**Default data path:** `~/.local/share/ai-quotas/samples.jsonl`  
**Default plots path:** `~/.local/share/ai-quotas/plots/` (runtime only)

Reader and writer resolve through the same module (`ai_quotas.paths`) — there is no split-brain between the human CLI and the collector.

## Display conventions

Columns:

```
quota | used | resets | burn 24h | need rem | burn tot | need avg
```

- All rates are **% of quota** (`%/h` for short windows, `%/d` for week/month).
- Default rows: every Claude / Codex / Grok window. OpenRouter and extras: `--full`.
- Soft refresh at 5 minutes; `-r` force; `--no-refresh` skip.
- Colors: green on-target; warm (orange→red) = over pace; cool (cyan→blue) = under; resets red when ≤1/7 of the window left.

See `docs/CONTRACT.md` for the row schema, adapter rules, and verdict thresholds.

## Library API

```python
from ai_quotas import (
    load_samples,
    latest_by_key,
    metrics_for_row,
    burn_metrics,
    table_rows,
    history,
    sample_now,
    verdicts,
)

samples = load_samples()                 # or path=...
by = latest_by_key(samples)
rows = table_rows(full=False)            # display model
v = verdicts(samples)
h = history(samples)
fresh = sample_now()                     # probe + append
```

## Optional scheduling (generic)

Samples get more useful with regular collection. Example **launchd** (macOS) — replace `$HOME` paths:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.ai-quotas.snapshot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>python3</string>
    <string>-m</string>
    <string>ai_quotas.collector</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$HOME/src/ai-quotas</string>
  <key>StartInterval</key><integer>1800</integer>
  <key>StandardOutPath</key>
  <string>$HOME/.local/share/ai-quotas/collector.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/.local/share/ai-quotas/collector.log</string>
</dict>
</plist>
```

Or cron:

```cron
*/30 * * * * cd $HOME/src/ai-quotas && python3 -m ai_quotas.collector >> $HOME/.local/share/ai-quotas/collector.log 2>&1
```

## Testing

```bash
uv run pytest -q
```

Tests are fully offline (no network, no real `~/.claude` / `~/.codex`).

## Portability notes

- **macOS Keychain** is used when available (Claude). On other OSes adapters fall back to credential files or report `unavailable`.
- Paths are `$HOME`-relative or env-overridable — no hard-coded personal home directories.
- Vendor CLIs and endpoints differ by platform; graceful degradation is by design.

## License

MIT — see [LICENSE](LICENSE).
