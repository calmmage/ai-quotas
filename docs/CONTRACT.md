# ai-quotas contract

Fresh rewrite of the sample-row schema and adapter rules. This is the public contract for the library and CLI.

## Sample row schema

Each line of `samples.jsonl` is one JSON object:

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
2. env `AI_QUOTAS_SAMPLES` (file)
3. env `AI_QUOTAS_DATA_DIR` + `/samples.jsonl`
4. default `~/.local/share/ai-quotas/samples.jsonl`

Reader and writer both use this module.

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

## Extra private adapters

Set `AI_QUOTAS_EXTRA_ADAPTERS` to a directory of `*.py` modules each defining `snapshot(ts)`. Useful for owner-only vendors not shipped in public v1.
