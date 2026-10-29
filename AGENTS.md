# AI quotas

- Setup: `make setup` → `make doctor`; fixture check: `AI_QUOTAS_SAMPLES=tests/fixtures/multi.jsonl uv run ai-quotas --no-refresh`. Full [install wizard](INSTALL.md); `make help` owns commands. Enable live sampling/serving/alerts only when requested.
- Read existing vendor logins. Missing/failed readings stay unavailable/error, never fake 0%. Login and sampler ownership: [SAMPLERS](docs/SAMPLERS.md), [quota truth](docs/GEMINI-TRUTH.md).
- Deploy: `make deploy` restarts the Mac dashboard, samples and mirrors generated plots. A git push does not update the cloud page. [Deployment](INSTALL.md#4-deploy-macos-launchagents).
- Tests: `make test` (offline). Alerts/Healthchecks: [wizard](INSTALL.md#5-telegram-remainingburn--spare-quota-before-reset); do not add a second sampler or sender. Schemas: [CONTRACT](docs/CONTRACT.md), [PLOTS](docs/PLOTS.md).

## Up to speed

- Codex: `~/.codex/sessions/2026/10/28/rollout-2026-10-28T00-25-36-01a17f41-1d7b-7ed3-9b08-09e7dbec6602.jsonl`; `codex resume 01a17f41-1d7b-7ed3-9b08-09e7dbec6602`; search: Actual human requests about per-account resets, weekly/monthly only, all-window collection.
- Claude Code: `~/.claude/projects/-Users-petrlavrov-calm-projects-p-ai-quotas/bc1f464f-7985-412c-8bea-9fa00e79d8db.jsonl`; `claude --resume bc1f464f-7985-412c-8bea-9fa00e79d8db` from this project; search: Owned Claude sampler; preserve Orca credential chain.
- Grok CLI: `~/.grok/sessions/%2FUsers%2Fpetrlavrov%2Fcalm%2Fprojects%2Fp-ai-quotas/2170651e-ca99-4191-a463-511ed9befd01/chat_history.jsonl`; `grok --resume 2170651e-ca99-4191-a463-511ed9befd01` from this project; search: Multi-account independent/cloud samplers.
- Local contract/runtime: `docs/SAMPLERS.md`, `ai_quotas/adapters/`, `~/.local/share/ai-quotas/{ai-quotas.sqlite3,alert-state.json}`; `~/Library/LaunchAgents/com.calmmage.ai-quotas-*.plist`; search: Account + source timestamp; read-only SQLite queries.
- Orca upstream: `~/calm/projects/p-orca/src/main/rate-limits/antigravity-usage-{command,response,fetcher}.ts`; `orca account list --json`; search: `rateLimits.antigravity`, `updatedAt`, `usageMetadata`.
- Notion: [AI quotas: reset credits](https://www.notion.so/3fdbc5d57f7c8130ba57d8cd452d8cee); search: `ai-quotas`.
- Latest recordings: `knowledge-base/systems/life-map/recordings.md`; Work Mac `~/calm/state/atlas/runtime/notion-meeting-notes/{latest-attempt,index}.json`; search: Live Notion created/edited since 27 Oct, no title filter.
- Catch-up evidence, source constraints and gaps: [context report](agent-context-2026-10-29.md). Read for the relevant source; recheck dated claims.
