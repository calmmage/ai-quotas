# ai-quotas contract

Fresh rewrite of the sample-row schema and adapter rules. This is the public contract for the library and CLI.

## Sample row schema

Each record in SQLite table `quota_samples` has this logical object shape:

| field | type | notes |
|---|---|---|
| `ts` | string (ISO-8601) | sample tick timestamp; shared across rows from the same collect |
| `provider` | string | e.g. `claude`, `codex`, `grok`, `openrouter` |
| `window` | string | e.g. `5h`, `week`, `week_fable`, `month`, `credits` |
| `used_percent` | number \| null | percent of quota **used** (0–100+). Null when unreadable |
| `resets_at` | string \| null | ISO-8601 when the window resets |
| `plan` | string \| null | optional plan/tier label from the vendor |
| `status` | string | `ok` \| `unavailable` \| `error` |
| `reason` | string \| null | human note; required when status ≠ ok |
| `limit` | number \| null | absolute limit when the vendor exposes one (display remains %-only) |
| `used` | number \| null | absolute used when the vendor exposes one |

## Adapter rules

1. **`snapshot(ts) -> list[dict]` never raises.** Import/load failures are converted to error rows by the collector.
2. **Never fabricate `used_percent: 0` on failure.** Non-ok rows must have `used_percent: null`. A genuine zero from a healthy probe is fine (`status=ok`, `used_percent=0`).
3. **Failure → one (or more) rows** with `status` in `{unavailable, error}` and a non-empty `reason`.
4. **Read-only credentials.** Adapters may read Keychain / local auth files; they must not write or rotate tokens when doing so would break the vendor CLI.
5. **stdlib only** for built-in adapters (no third-party runtime deps).

## Path resolution

Single module: `ai_quotas.paths`.

1. explicit call-site override
2. env `AI_QUOTAS_DATABASE`
3. env `AI_QUOTAS_DATA_DIR` + `/ai-quotas.sqlite3`
4. default `~/.local/share/ai-quotas/ai-quotas.sqlite3`

Reader and writer both use this module.

`AI_QUOTAS_SAMPLES`, `AI_QUOTAS_SPEND`, and `--samples` are bounded legacy
JSONL compatibility paths for fixtures, migration, or rollback. They are not
the normal live default.

## SQLite schema

Schema version 1 stores quota rows in `quota_samples`, session usage in
`spend_turns`, incremental harvester state in `harvest_files`, and import
provenance in `legacy_import_rows`. Query-critical fields have typed columns;
`payload_json` preserves each complete logical record, including unknown fields.
The database uses WAL, foreign-key enforcement, a busy timeout, and transactional
writes.

## Trend math (summary)

| metric | definition |
|---|---|
| `trend_total` | `used_percent / hours_elapsed_in_window` |
| `trend_24h` | Δused / Δhours over samples ≤24h (noise guards) |
| `need_avg` | `100 / window_hours` |
| `need_rem` | `(100 - used) / hours_left` |
| `runway` | `(100 - used) / trend` when trend > 0 |

Noise guards for 24h burn:

- baseline must be ≥ 30 minutes older than current
- if `|Δused| ≤ 1` and interval < 2h → treat as quantization noise → null burn

## Verdicts (collector exit codes)

Provider-level gate on a preferred window (week for most; month for grok; credits for openrouter):

| verdict | exit | when |
|---|---|---|
| STOP | 2 | projected end ≥ 100% with measurable burn, or used ≥ 85% with >24h left, or 24h runway < hours left |
| WARN | 1 | projected ≥ 70% with burn, or used ≥ 60% with >48h left |
| OK | 0 | otherwise (measurable data) |
| UNKNOWN | 0 | no readable window |

**Null burn never produces a projection-based STOP.**

## Display conventions (human CLI)

- All rates as **percent of quota** (`%/h` for ≤12h windows, `%/d` otherwise)
- Columns: `quota | used | resets | burn 24h | need rem | burn tot | need avg`
- Default rows: all `claude` / `codex` / `grok` windows; other providers only with `--full`
- Soft refresh if newest sample older than 5 minutes; `-r` force; `--no-refresh` skip
- `ai-quotas --json` emits the **display model** (metrics + burn pairs + color enums), not only raw sample rows

## Session spend schema (`spend_turns`)

Separate table in the same SQLite database. One row per **turn**, harvested
from local session logs — not from vendor quota APIs.

| field | type | notes |
|---|---|---|
| `kind` | string | `turn` |
| `provider` | string | `grok` \| `claude` \| `codex` |
| `session_id` | string | harness session id |
| `turn_id` | string | grok `prompt_id` · claude `message.id` · codex token_count timestamp |
| `ts` | string \| null | turn timestamp |
| `input_tokens` | int \| null | uncached input when the vendor splits it |
| `cached_tokens` | int \| null | cache-read tokens |
| `output_tokens` | int \| null | |
| `reasoning_tokens` | int \| null | grok / codex; claude usually null |
| `total_tokens` | int \| null | as reported, or input+cached+output |
| `model_calls` | int \| null | |
| `api_ms` | int \| null | grok only |
| `cost_usd` | number \| null | grok TUI estimate (`costUsdTicks / 1e10`). Null on subscription claude/codex |
| `model` | string \| null | |
| `source` | string | which log + field |

Rules:

1. **Do not mix spend and quota sample rows.** They have separate tables and grains.
2. Harvest is **read-only** on vendor session dirs. Incremental state is in `harvest_files`.
3. Dedup key is `(provider, session_id, turn_id)`. Re-running harvest must not duplicate.
4. `cost_usd: 0` from a subscription transcript is stored as **null** (unknown), not free.
5. Session-end hooks are not required. Logs already on disk are the source of truth.

## agentic_step burn (`jobs.jsonl` join)

The jobs file is owned by `agentic_step` (not this package):

`~/.local/share/agentic-step/jobs.jsonl` (override `AGENTIC_STEP_JOBS`).

One line per job. Join to `spend_turns` on `(provider, session_id = chat_id)`.

| field | type | notes |
|---|---|---|
| `ts` | string | job timestamp |
| `job_id` | string | |
| `backend` / `harness` | string \| null | |
| `provider` | string | same tokens as spend (`claude` / `grok` / `codex`) |
| `model` | string \| null | |
| `chat_id` | string | == spend `session_id` |
| `caller` / `task_slug` / `item_ref` | string \| null | the label that marks THESE chats |
| `usage` | object \| null | fallback when spend has no session: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd` |
| `duration_ms` | number \| null | |

Rules:

1. Prefer harvested spend for a matching session (`source=spend`).
2. No match → job `usage`, `source=job` (harness/model spend.py does not read).
3. `ai-quotas spend --agentic-step` prints totals per caller / task_slug / provider.
4. `ai-quotas agentic-step-check` exits **0** ok / **1** substantial. Defaults live in `ai_quotas/agentic_step.py`: window `7d`, `1_000_000` tokens, `$5` known cost. Substantial if **either** threshold is crossed (unknown `$` does not fire the usd rule).

## Extra private adapters

Set `AI_QUOTAS_EXTRA_ADAPTERS` to a directory of `*.py` modules each defining `snapshot(ts)`. Useful for owner-only vendors not shipped in public v1.
