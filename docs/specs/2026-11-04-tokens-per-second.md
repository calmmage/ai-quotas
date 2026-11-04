# Tokens per second for every AI chat on this Mac, by model, provider and fast mode

Spec written 4 Nov 2026 (Claude, from Petr's request). Builder: Grok via Orca.

## Petr's words

4 Nov 2026:

> I want a global 'tokens per second' tracknig for all models and providers AND the fast / ultrafast mode
> (for all ai chats) - on this mac

## Data on this Mac (checked 4 Nov)

| Harness | Where | Per-response fields |
| --- | --- | --- |
| Claude Code (CLI, Orca, Desktop Code) | `~/.claude/projects/**/*.jsonl` incl. `subagents/`; Desktop metadata `~/Library/Application Support/Claude/claude-code-sessions/` | assistant lines: `timestamp`, `message.id`, `message.model`, `message.usage.output_tokens`, `usage.output_tokens_details.thinking_tokens`, `usage.speed` / `usage.service_tier` (`standard`, fast mode = `fast`) |
| Codex (CLI, app, Orca runtimes) | `~/.codex/sessions/**/rollout-*.jsonl`, `~/.codex/archived_sessions/`, Orca Codex homes under `~/Library/Application Support/Orca/` | `turn_context.payload.model`, `service_tier` (`default` / `priority` = fast / `ultrafast`), `reasoning_effort`; `event_msg` `token_count` → `info.last_token_usage.{output_tokens,reasoning_output_tokens}` with `timestamp` |
| Grok CLI | `~/.grok/sessions/<cwd>/<id>/{events,updates,chat_history}.jsonl` | find the model, usage and timing fields yourself; document them |
| Model gateway (LiteLLM, `127.0.0.1:4000`) | `~/calm/projects/p-model-gateway` | if it keeps request logs with tokens and latency, include them as provider rows |
| Others | Antigravity (`agy`), Claude Desktop Cowork | include when per-response tokens + time exist; otherwise list them as "no timing data" |

Skip `~/calm/data/ai-chats/<node>/` (the other Mac's mirror): "on this mac". Keep a `node` column so it can be added later.

## What to build (inside ai-quotas)

1. **Collector** `ai_quotas/speed/` + CLI `ai-quotas speed scan`. Incremental: remember a byte offset + mtime per file, so a rescan reads only new lines. Follow `/Users/petrlavrov/calm/framework/systems/script-progress.md` (progress bar, timing, safe resume).
2. **One row per model response** in `~/.local/share/ai-quotas/ai-quotas.sqlite3`, table `response_speed`: `ts, node, harness, provider, model, tier (standard|fast|priority|ultrafast|unknown), effort, output_tokens, reasoning_tokens, seconds, tokens_per_second, session_id, cwd, source_file`.
   - `seconds` = this response's last timestamp − the previous event's timestamp (user message or tool result). It includes time-to-first-token. Call it end-to-end in the UI.
   - Claude writes several lines per `message.id`: merge them and count usage once.
   - Drop responses under 20 output tokens or under 0.3 s from the speed stats (too noisy), but keep the rows.
   - Mark parallel tool calls and compaction/summary turns so they don't skew the numbers.
3. **Views**:
   - CLI `ai-quotas speed --json` and a table: p50 / p90 tok/s and response count per (provider, model, tier), for the last 24 h / 7 d / 30 d.
   - A **Speed** page/section in the existing ai-quotas web dashboard: per-model tok/s by tier (fast vs standard side by side), a daily trend, and a sortable table. Follow `craft:design-code`, `dataviz`; tables sortable and quiet; under 500 ms.
4. **Schedule**: run the scan with the existing ai-quotas LaunchAgent cadence (or a new `com.calmmage.ai-quotas-speed` plist every 10 min). Prepare the plist + `make` target; installing it is part of `make deploy`. Don't add a second sampler for quotas.

## Rules

- Orca worktree of `/Users/petrlavrov/calm/projects/p-ai-quotas`, branch `feat/tokens-per-second-20261104` from `origin/main`. The main checkout has 17 uncommitted files: don't touch them.
- Read-only on every transcript. Open SQLite app databases `mode=ro`. Never copy transcript text into the speed table, only numbers and ids.
- Don't read `~/.claude/projects` with subagents or scripts that will prompt. If your harness prompts for that path, do the scan in your main session.
- Tests: `make test` (offline) + new fixture tests per harness: Claude multi-line message merge, Codex tier detection (default/priority/ultrafast), the noise filter, incremental offset resume.
- Real run on this Mac: `ai-quotas speed scan` then `ai-quotas speed`. Paste the table (top 15 model×tier rows) into the report.
- Commit locally, push the branch, open a PR into `main`. Never merge. Don't run `make deploy`. Claude reviews and deploys.
- Report: `docs/specs/2026-11-04-tokens-per-second.REPORT.md` in your branch (fields found per harness, a screenshot of the Speed page, the real table, gaps).
- Done marker: `/Users/petrlavrov/calm/state/bot-observability-20261104/tokens-per-second.DONE.json` with `{branch, pr_url, commit, tests, report_path, unverified}`.
