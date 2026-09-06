# ai-quotas contract

Fresh rewrite of the sample-row schema and adapter rules. This is the public contract for the library and CLI.

[All docs](../README.md#docs)

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

Schema version 3 stores quota rows in `quota_samples`, session usage in
`spend_turns`, reset credits in `reset_credits` (v2), temporary limit boosts
in `boosts` (v3), incremental harvester state in `harvest_files`, and import
provenance in `legacy_import_rows`. Query-critical fields have typed columns;
`payload_json` preserves each complete logical record, including unknown fields.
The database uses WAL, foreign-key enforcement, a busy timeout, and transactional
writes.

## Reset credits (`reset_credits`)

Vendor "reset your limit" tokens: Codex grants a *Full reset* (redeem when a
rate limit is hit), Grok grants a *Usage limit reset* (settings → Usage).
Claude exposes none (04 Sep 2026: only overage credits, guest passes and
temporary boosts). Separate grain from `quota_samples`; separate table.

| field | type | notes |
|---|---|---|
| `kind` | string | always `reset_credit` |
| `ts` | string | sample tick (shared with the quota rows of the same collect) |
| `provider` | string | `codex` \| `grok` \| `claude` … |
| `credit_id` | string \| null | vendor id; null for `none` / `unavailable` / `error` |
| `title` | string \| null | vendor label (`Full reset`, `Usage limit reset`) |
| `granted_at` | string \| null | ISO-8601 |
| `expires_at` | string \| null | ISO-8601 |
| `status` | string | `available` \| `none` (vendor answered, zero credits) \| `unavailable` (not exposed / offline) \| `error` |
| `reason` | string \| null | source note or failure reason |
| `scope` | string \| null | window the reset refills (`week`) |

Adapter rule: `snapshot(ts)` may include these rows next to quota rows; the
collector splits on `kind` and stores them in `reset_credits`. A vendor that is
probed but lists nothing must emit `status=none` — never silence — so a later
disappearance can be dated.

Lifecycle is derived from history (`ai_quotas.reset_credits.credit_states`):
`available → consumed` when the id disappears from an answering tick before
`expires_at`; `available → expired` when it disappears after `expires_at` or is
still listed past it. `unavailable` / `error` ticks are ignored, so an outage
never fakes a redemption.

Sources: Codex = codexbar `usage.codexResetCredits`; Grok =
`POST https://grok.com/prod_mc_billing.ConsumerUiSvc/GetRemainingResets`
(grpc-web, CLI OAuth bearer accepted, hand-decoded protobuf).

### API surface

`ai-quotas --json` adds a top-level `reset_credits` block per provider
(`status`, `reason`, `checked_at`, `available`, `credits[]` with
`expires_in_hours`, `consumed`, `expired`) and, on each provider's **primary
window row only**, `remaining_percent`, `reset_credits_available` and
`remaining_percent_total = remaining + 100 × available`. Plots keep the y-axis
at 0–100 and show credits as a subtitle badge (`1 reset · exp 12 Sep (8d)`),
never as a 200 % line (Petr, 04 Sep 2026).

### Money

Priced against the vendor's primary window value (`MONTHLY_USD` pro-rated):
expired unused = **−one full window** (the reset would have refilled a whole
window); redeemed = **+used% × window** matched to the used% drop within ±3h
(unmatched → +0, "value unknown"); available = 0 until it ends. Reported in
`money.txt` / `00_INDEX.html` as a separate "RESET CREDITS" block — not mixed
into the free/burn columns.

## Boosts (`boosts`)

Vendor temporary limit perks (Claude, 04 Sep 2026: **"limits temporarily boosted
+50% through 13 Sep"**). History only — no money math. Separate grain from
`quota_samples` and `reset_credits`; separate table (schema v3). The sampler
**upserts** on `(provider, window, percent, ends_at)`: extend `last_seen_ts`,
do not duplicate.

| field | type | notes |
|---|---|---|
| `kind` | string | always `boost` |
| `ts` | string | sample tick (shared with the quota rows of the same collect) |
| `provider` | string | `claude` … |
| `window` | string | quota window the perk applies to (`week`) |
| `percent` | number | boost amount (e.g. 50) |
| `starts_at` | string \| null | first seen (ISO-8601) |
| `ends_at` | string \| null | vendor "through" date, parsed |
| `first_seen_ts` | string | first upsert |
| `last_seen_ts` | string | last upsert |
| `raw_text` | string \| null | vendor copy as seen |

Adapter rule: `snapshot(ts)` may include these rows next to quota rows when it
**sees** the perk in a payload it already fetches. The collector splits on
`kind` and upserts them in `boosts`. Silence when the payload has no perk —
unlike reset credits, a missing boost is not an `unavailable` row.

Lifecycle is derived from the stored row (`ai_quotas.boosts.boost_states`):
`active → ended` when `ends_at` passes; `active → vanished` when a later
answering tick no longer lists the perk before `ends_at`.

Source today: **not** in `GET https://api.anthropic.com/api/oauth/usage`
(CLI 2.1.260 schema: `five_hour` / `seven_day*` / `cinder_cove` /
`extra_usage` / `limits[]`). The copy is on
`https://claude.ai/settings/usage` (help:
[Claude Code May–August 2026 weekly limits promotion](https://support.claude.com/en/articles/15910845-claude-code-may-august-2026-weekly-limits-promotion),
through 13 Sep 2026 11:59 PM PT). The adapter parses the perk if it appears
as extra fields or notice text on the usage payload; it does not scrape the
settings page.

### API surface

`ai-quotas --json` adds a top-level `boosts: [...]` list (`provider`,
`window`, `percent`, `starts_at`, `ends_at`, `status`, `raw_text`,
`first_seen_ts`, `last_seen_ts`). The human table prints one `boosts:` line
only when any boost is **active** or **ended in the last 7 days**. Plots: a
subtitle badge on the vendor panel while active (`+50% through 13 Sep`);
y-axis stays ≤ 100 %.

### Money

Boosts are not priced.

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

## Plots freshness (`meta.json`)

`ai-quotas dash` writes `<plots dir>/meta.json` after every successful generation, before `live.html`. Stable keys:

| key | type | meaning |
|---|---|---|
| `generated_at` | string | `YYYY-MM-DDTHH:MM:SSZ`, UTC, the same value stamped into `live.html` `<meta name="generated-at">` |
| `stale_after_s` | int | viewer threshold; the live page shows its stale bar past this age (default 7200) |
| `poll_interval_s` | number | the dash's database poll interval |
| `host` | string | producing machine |
| `producer` | string | `ai-quotas dash` |

Mirrors copy the generated directory as-is; monitors may read `generated_at` for their own stale rule. `AI_QUOTAS_AFTER_REGEN` / `--after-regen CMD` runs once per generation (60 s timeout, serialized, never fatal).
