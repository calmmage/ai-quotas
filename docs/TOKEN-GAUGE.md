# Token gauge — estimate absolute quota from measured spend

Plots use this for hover/reset leftover-token **labels** (not a second curve).

[Plots](PLOTS.md) · [All docs](../README.md#docs)

## Why

Vendors mostly expose **percent** only. Grok exposes `monthlyLimit=150000` +
`used` from the billing API — those are **opaque billing units** (`u`), not
verified LLM tokens. 150k "tokens"/month would be absurd for agent work; treat
as `u` until calibrated.

## Goal

For each (provider, window), estimate:

```
token_limit ≈ tokens_we_measured / (Δused_percent / 100)
leftover_tokens ≈ remaining_percent / 100 * token_limit
```

using **only recent** windows (current reset period), because plans change.

Benchmark models (smartest defaults — one per vendor family):

| family | benchmark model |
|--------|-----------------|
| claude | Opus (latest) |
| codex  | ChatGPT / Sol-class flagship |
| gemini | Gemini Pro |
| grok   | Grok 4.5 |

Same model for calibration and for "what does 1% of quota buy me?"

## Data sources (local, already on disk)

| provider | token accounting |
|----------|------------------|
| claude | `~/.claude/projects/**/*.jsonl` → `message.usage` (`input_tokens`, `output_tokens`, cache_*) |
| codex  | rollout/session jsonl usage blocks |
| grok   | per-turn session logs (not subscription quota) — sum for calibration |
| gemini | extra adapter; session logs if present (not in the public wheel) |

Live harvest is `ai-quotas spend` → SQLite `spend_turns`. Calibration uses `total_tokens` in the current reset period of the vendor's primary series.

## Method (v0)

1. Current reset period = samples after the last used%-drop reset (not claimed `resets_at`).
2. Sum `total_tokens` from spend for that provider in `[t0, t1]`.
3. `Δused%` = used at t1 − used at t0 on the primary week series.
4. If `Δused% ≥ 5` and tokens > 0: `tokens_per_pct = tokens / Δused%`.
5. Never invent if Δpct is small.

Claude `total_tokens` includes cache-read. That overstates billable tokens; leftover is a `~` estimate.

## Display

- Hover / reset labels: leftover $ (subscription leftover) and `~N tok` when calibrated.
- Daily spend strip is a **separate** grain (turns per local day), not on the % axis.
- Grok API `u` is never labeled as tokens.
- Uncalibrated → omit tokens, do not show 0.

## Status

23 Aug 2026: v0 is wired into `ai_quotas.plots.prep.tokens_per_percent` and plot hover/labels.
