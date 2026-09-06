<h1 align="center">
  <img src="docs/examples/logo.svg" alt="ai-quotas" width="64" valign="middle" /> ai-quotas
</h1>

<p align="center">
  <a href="https://github.com/calmmage/ai-quotas/stargazers"><img src="https://img.shields.io/github/stars/calmmage/ai-quotas?style=flat&label=★&color=08C" alt="GitHub stars" /></a>
  <a href="https://github.com/calmmage/ai-quotas/actions/workflows/test.yml"><img src="https://github.com/calmmage/ai-quotas/actions/workflows/test.yml/badge.svg" alt="test" /></a>
  <img src="https://img.shields.io/badge/license-MIT-08C?style=flat" alt="License: MIT" />
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/runtime-stdlib-6e7681?style=flat" alt="stdlib runtime" />
  <img src="https://img.shields.io/badge/macOS-111318?style=flat" alt="macOS" />
</p>

<p align="center">
  <strong>See remaining subscription quota as it burns.</strong><br/>
  Claude, Codex, Grok, and Gemini on one dash — sampled from your logged-in CLIs, stored on disk.
</p>

<p align="center">
  <a href="https://docs.anthropic.com/claude/docs/claude-code"><kbd>Claude</kbd></a>
  &nbsp;
  <a href="https://github.com/openai/codex"><kbd>Codex</kbd></a>
  &nbsp;
  <a href="https://x.ai/grok"><kbd>Grok</kbd></a>
  &nbsp;
  <a href="https://gemini.google.com"><kbd>Gemini</kbd></a>
</p>

<h3 align="center"><a href="#install"><ins>Install ai-quotas</ins></a></h3>

<p align="center">
  <img src="docs/examples/dash-night.png" alt="ai-quotas night dashboard: remaining % for Claude, Codex, Grok, and Gemini" width="960" />
</p>

> Vendor usage endpoints are unofficial and can change. This tool uses **your** credentials, read-only. Use at your own risk.

## Give this to an agent

Paste:

> Clone https://github.com/calmmage/ai-quotas and follow **[AGENTS.md](AGENTS.md)**. Install [uv](https://docs.astral.sh/uv/) if missing. Do not invent an install path. Vendor CLI logins are the human's. Skip Gemini unless they give you an extra adapter (`AI_QUOTAS_EXTRA_ADAPTERS`). Then `make sample && make dash`.

That gets them the package and a local dash. Live Claude / Codex / Grok rows need those CLIs already logged in. Gemini is not in the public wheel. LaunchAgents are macOS (`make install-automation`). Telegram alerts need `AI_QUOTAS_TELEGRAM_BOT_TOKEN` + chat id.

## Install

```bash
git clone https://github.com/calmmage/ai-quotas.git
cd ai-quotas
make setup                 # uv sync --extra all + doctor
```

Needs **Python ≥ 3.11**, **[uv](https://docs.astral.sh/uv/)**, and **make**. Core runtime is **stdlib-only**. Plots: `make install-plot` (already in `make setup`).

**Agents:** do not improvise — follow **[AGENTS.md](AGENTS.md)**. `make wizard` points there and runs `make setup`.

## Quickstart

```bash
# Human table (soft-collect if samples older than 5 minutes)
uv run ai-quotas

# Force a live probe
uv run ai-quotas -r

# Cache only
uv run ai-quotas --no-refresh

# Interactive dash (day = Plotly, night = uPlot)
uv run ai-quotas dash --open
```

Offline, no vendor accounts:

```bash
AI_QUOTAS_SAMPLES=tests/fixtures/multi.jsonl uv run ai-quotas --no-refresh
```

## What you get

| Surface | Command |
|---------|---------|
| **Table** | `ai-quotas` — used% · burn vs need · reset ETA · color pace |
| **Dash** | `ai-quotas dash` — % remaining over time, money markers, reset-credit badges |
| **Verdicts** | `ai-quotas verdicts` — `STOP` / `WARN` / `OK` (exit 2 / 1 / 0) |
| **Alerts** | Telegram when you are **burning** a still-high bar, or a **reset is soon** with leftover quota |
| **Spend** | `ai-quotas spend` — local session tokens/$ (Claude / Codex / Grok logs) |
| **Automation** | `make install-automation` — sample every 30m + dash KeepAlive + weekly spend check |

The default 2×2 is **Claude / Codex / Grok / Gemini**. Gemini is a drop-in extra adapter (`AI_QUOTAS_EXTRA_ADAPTERS`). OpenRouter is a built-in adapter, shown with `--full`, not on that 2×2.

## Automation

```bash
make dry-run-automation    # print resolved argv
make install-automation    # macOS LaunchAgents: sample + dash + weekly check
```

Healthchecks pings (optional) keep a dead sampler or dash from looking “up”. Telegram + ping URLs: [AGENTS.md](AGENTS.md) §5–6.

## Docs

| | |
|---|---|
| Agent install / deploy / integrate | [AGENTS.md](AGENTS.md) |
| Plot engines, money, reset credits | [docs/PLOTS.md](docs/PLOTS.md) |
| Sample-row contract + verdicts | [docs/CONTRACT.md](docs/CONTRACT.md) |
| Owner-machine map (optional) | [docs/MAP.md](docs/MAP.md) |

```bash
make test     # offline
make doctor   # resolved paths
```

## License

MIT — see [LICENSE](LICENSE).
