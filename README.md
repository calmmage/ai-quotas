# ai-quotas

[![test](https://github.com/calmmage/ai-quotas/actions/workflows/test.yml/badge.svg)](https://github.com/calmmage/ai-quotas/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![runtime: stdlib](https://img.shields.io/badge/runtime-stdlib-lightgrey)](pyproject.toml)

Local **subscription quota** sampler, trend math, and dashboards for Claude, Codex, Grok, and OpenRouter. Reads *your* already-logged-in vendor CLIs. Writes SQLite on disk. No cloud service of its own.

> Vendor usage endpoints are unofficial and can change. This tool uses **your** credentials, read-only. Use at your own risk.

![Quota remaining dashboard](docs/examples/dash-night.png)

<p align="center">
  <a href="#install"><img src="https://img.shields.io/badge/install-make%20setup-black?style=for-the-badge" alt="make setup"></a>
  <a href="#quickstart"><img src="https://img.shields.io/badge/demo-ai--quotas%20dash-1f6feb?style=for-the-badge" alt="ai-quotas dash"></a>
  <a href="AGENTS.md"><img src="https://img.shields.io/badge/agents-AGENTS.md-111827?style=for-the-badge" alt="AGENTS.md"></a>
</p>

## Install

```bash
git clone https://github.com/calmmage/ai-quotas.git
cd ai-quotas
make setup                 # uv sync --extra all + doctor
```

Python ≥ 3.11. Core runtime is **stdlib-only**. Plots: `make install-plot` (already in `make setup`).

**Agents:** do not improvise an install path — follow **[AGENTS.md](AGENTS.md)**. `make wizard` points there and runs `make setup`.

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

Adapters: Claude (`5h`, `week`, …), Codex, Grok (`week`, `month`), OpenRouter. Drop in extras with `AI_QUOTAS_EXTRA_ADAPTERS`.

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
