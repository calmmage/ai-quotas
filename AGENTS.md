# AGENTS.md — install, deploy, integrate

This file is the install wizard for agents. Humans who just want the demo: [README.md](README.md). Makefile targets are the source of truth for commands (`make help`).

Standalone install is done when `make doctor` prints `cli: ok` and `AI_QUOTAS_SAMPLES=tests/fixtures/multi.jsonl uv run ai-quotas --no-refresh` prints a table. Continue to live sampling, serving, alerts, or automation only for the features the user requested.

## 1. Install the package

Needs **[uv](https://docs.astral.sh/uv/)**, **make**, and CPython **≥ 3.11** (uv downloads one if the system interpreter is older). If `uv` is missing:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Follow the installer's PATH instructions (or open a new shell), then check `uv --version` before continuing. The first setup needs network access to download dependencies and, if needed, Python.

Then:

```bash
git clone https://github.com/calmmage/ai-quotas.git
cd ai-quotas
make wizard          # prints this file's pointer, then `make setup`
# equivalent: make setup
```

`make setup` runs `uv sync --extra all`, then `make doctor`. That is the only install path (`make wizard` is a pointer + `make setup`). It creates a local `.venv`; run the CLI with `uv run ai-quotas` from this checkout. Setup does not collect samples, start a server, or install LaunchAgents.

Done when: `uv --version` works and `make doctor` prints the package version and `cli: ok`. Runtime is stdlib; plots need `--extra plot` (included in `all`).

## 2. Vendor logins (human)

Adapters read **already-logged-in** vendor CLIs. The agent cannot complete this step.

| adapter | needs |
|---|---|
| Claude | `claude` CLI logged in (Keychain / `~/.claude`) |
| Codex | `codex` CLI and/or [codexbar](https://github.com/steipete/CodexBar) |
| Grok | `grok` CLI (`~/.grok/auth.json`) |
| Gemini | extra adapter: `AI_QUOTAS_EXTRA_ADAPTERS` pointing at a `snapshot(ts)` module (not in the public wheel) |
| OpenRouter | `OPENROUTER_API_KEY` in the environment (built-in; `--full`, not on the default 2×2) |

Done when: `make sample` prints `ok` rows for the vendors the human uses. Missing vendors become `unavailable` / `error` — never a fake 0%.

Offline without accounts:

```bash
AI_QUOTAS_SAMPLES=tests/fixtures/multi.jsonl uv run ai-quotas --no-refresh
```

Done when: the `AI SUBSCRIPTION QUOTA` table contains Claude, Codex, and Grok fixture rows. The fixture timestamps are fixed, so old samples and `due` resets are expected. This command reads the fixture without probing vendor accounts.

## 3. Sample, table, dash

```bash
make sample          # probe + append SQLite (~/.local/share/ai-quotas/)
make table           # human table, cache only
make dash            # generate + serve 127.0.0.1:8765 (KeepAlive loop)
```

Done when: the table prints and `http://127.0.0.1:8765/` loads the plot. Default database is `~/.local/share/ai-quotas/ai-quotas.sqlite3`.

Plot contract, engines, money labels: [docs/PLOTS.md](docs/PLOTS.md). Row schema / verdicts: [docs/CONTRACT.md](docs/CONTRACT.md).

## 4. Deploy (macOS LaunchAgents)

```bash
make dry-run-automation    # print resolved argv; does not install
make install-automation    # sample @ 30m + dash KeepAlive + weekly agentic_step check
```

`make install-automation` installs **three** agents. If `~/Library/LaunchAgents/com.calmmage.ai-quotas-{sample,dash}.plist` is already a **symlink** (an owner machine with its own launchd tree), the installer leaves it alone.

Uninstall: `make uninstall-automation` (also skips symlinks).

Done when: `launchctl print gui/$(id -u)/com.calmmage.ai-quotas-sample` exists, and dash is either the new KeepAlive or the pre-existing symlink.

Linux: cron the equivalent of `ai-quotas sample` every 30 minutes; run `ai-quotas dash --port 8765` under your supervisor.

## 5. Telegram remaining/burn + reset-soon

`ai-quotas sample` already runs alerts after each collect (disable with `--no-alert`).

| alert | fires when |
|---|---|
| **BURN** | primary window verdict is `WARN` or `STOP` (pace will exhaust the quota) |
| **RESET SOON** | remaining ≥ 40% and reset within 48h on a week/month window |

One Telegram message per new fingerprint. Same fingerprint is not resent; `WARN`→`STOP` is a new fingerprint. Ended conditions drop out of `<data_dir>/alert-state.json` so they can fire again.

`--dry-run` sends nothing and does not save new fingerprints, but it can still prune ended conditions from an existing state file.

```bash
export AI_QUOTAS_TELEGRAM_BOT_TOKEN=…
export AI_QUOTAS_TELEGRAM_CHAT_ID=…
make alert                 # dry-run (no send, no persist of new fingerprints)
uv run ai-quotas alert     # send if creds resolve
```

Done when: `uv run ai-quotas alert --dry-run` prints a preview (`delivery=dry-run` for new alerts; `delivery=skip` when none fire). If sending was requested and credentials are configured, a live `uv run ai-quotas alert` returns `delivery=sent` for new alerts.

## 6. Healthchecks (dead man's switch)

Sample pings after every collect. Dash pings on start and every 5 minutes while KeepAlive is up. A dead renderer no longer looks “up” just because nginx is still serving frozen HTML.

Set **full ping URLs** (healthchecks.io or self-hosted):

```bash
export AI_QUOTAS_HC_SAMPLE_URL=https://hc-ping.com/<uuid>
export AI_QUOTAS_HC_DASH_URL=https://hc-ping.com/<uuid>
```

Or a ping key + slugs (`AI_QUOTAS_SAMPLE_HC_SLUG`, `AI_QUOTAS_DASH_HC_SLUG`, `CALMMAGE_HEALTHCHECKS_PING_KEY`). Bake them into LaunchAgents by exporting them **before** `make install-automation`.

Done when: the Healthchecks dashboard records pings from both roles after one sample and one dash start.

## 7. Integrate into other agents

```bash
uv run ai-quotas --json --no-refresh     # display model (rows + colors + burn pairs)
uv run ai-quotas verdicts --no-refresh   # STOP/WARN/OK per provider; exit 2/1/0
uv run ai-quotas alert --dry-run --json  # what would notify
uv run python -m ai_quotas.collector --no-sample
```

Library: `from ai_quotas import load_samples, verdicts, sample_now, table_rows`. Extra private adapters: `AI_QUOTAS_EXTRA_ADAPTERS=/path/to/dir` with `*.py` exposing `snapshot(ts)`.

## 8. Test

```bash
make test          # offline; no network, no real ~/.claude
```

Done when: pytest is green.
