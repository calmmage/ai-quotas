# Context evidence retained from AGENTS.md

Snapshot: 29 Oct 2026. Dates below belong to their original checks; verify live state during catch-up.

## Up to speed

Format: up-to-speed/v1. Maintained with `sig:up-to-speed`.
Project: ai-quotas · root: `/Users/petrlavrov/calm/projects/p-ai-quotas` · repo: `github.com/calmmage/ai-quotas`.
Scope: multi-account quota sampling, plots and alerts. Search aliases: ai-quotas, quota reset, sampler, Argon, AGY; historical `calmmage/slow/ai-quotas/code` and `work/projects/ai-quotas`.
Last catch-up: 2026-10-29; [incident and journey](docs/walkthroughs/2026-10-29-reset-identity.md).

| Source | Exact locator / account | Query or filter | Check when | Evidence and verification |
| --- | --- | --- | --- | --- |
| Codex | `~/.codex/sessions/2026/10/28/rollout-2026-10-28T00-25-36-01a17f41-1d7b-7ed3-9b08-09e7dbec6602.jsonl`; `codex resume 01a17f41-1d7b-7ed3-9b08-09e7dbec6602` | Actual human requests about per-account resets, weekly/monthly only, all-window collection | Alert changes | Transcript read 29 Oct; explicit 27–28 Oct instructions, not inferred from code |
| Claude Code | `~/.claude/projects/-Users-petrlavrov-calm-projects-p-ai-quotas/bc1f464f-7985-412c-8bea-9fa00e79d8db.jsonl`; `claude --resume bc1f464f-7985-412c-8bea-9fa00e79d8db` from this project | Owned Claude sampler; preserve Orca credential chain | Sampler changes | Human instructions read 29 Oct; latest project transcript 23 Oct |
| Grok CLI | `~/.grok/sessions/%2FUsers%2Fpetrlavrov%2Fcalm%2Fprojects%2Fp-ai-quotas/2170651e-ca99-4191-a463-511ed9befd01/chat_history.jsonl`; `grok --resume 2170651e-ca99-4191-a463-511ed9befd01` from this project | Multi-account independent/cloud samplers | Sampler topology | Actual user queries read 29 Oct; conversation 16 Oct |
| Local contract/runtime | `docs/SAMPLERS.md`, `ai_quotas/adapters/`, `~/.local/share/ai-quotas/{ai-quotas.sqlite3,alert-state.json}`; `~/Library/LaunchAgents/com.calmmage.ai-quotas-*.plist` | Account + source timestamp; read-only SQLite queries | Every incident | 29 Oct inspected DB, delivery fingerprints and all three installed jobs; never infer account from selected chart |
| Orca upstream | `~/calm/projects/p-orca/src/main/rate-limits/antigravity-usage-{command,response,fetcher}.ts`; `orca account list --json` | `rateLimits.antigravity`, `updatedAt`, `usageMetadata` | Anonymous/cached extras | Live read 29 Oct; current aggregate has no email. `agy -p /usage --output-format json --print-timeout 20s` returned zero model turns and no account; detailed AGY adapter supplies authenticated account data |
| Notion | [AI quotas: reset credits](https://www.notion.so/3fdbc5d57f7c8130ba57d8cd452d8cee) | `ai-quotas` | Historical intent | Fetched 29 Oct; original reset-credit/money vision, not current sampler state |
| Latest recordings | `knowledge-base/systems/life-map/recordings.md`; Work Mac `~/calm/state/atlas/runtime/notion-meeting-notes/{latest-attempt,index}.json` | Live Notion created/edited since 27 Oct, no title filter | Every catch-up | Live newest parent [Assistance: Business Product](https://www.notion.so/407bc5d57f7c803a97fafd8936e4ad4e) fetched with full transcript 29 Oct. Remote retained index is still checked 27 Oct 05:01 UTC: later NAS receipt coverage unverified |

### Missing pointers / coverage

- Ordinary Claude and Grok web chat histories were not searched in this pass; local CLI transcripts are not complete coverage of those surfaces. Browser access host reported unavailable; the dashboard journey uses native Chrome.
- No project-specific Telegram decision history was searched beyond Petr's supplied alert screenshots. Source rows and persisted delivery state establish the incident independently.
- Live Notion source accessibility is verified separately from the stale work-Mac retained index; no claim of current NAS recording coverage.
