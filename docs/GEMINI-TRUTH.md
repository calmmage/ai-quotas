# Gemini and Argon quota sources

The current Google summary can report three independent groups: Gemini Models,
Claude/GPT, and Gemini 4 Argon. Argon on Pro has its own weekly pool; it must not
be folded into Gemini Pro or inferred from the shared pool.

The private adapter calls `retrieveUserQuotaSummary` with the installed CLI-style
identity (`antigravity/cli/<version> (<platform>; <arch>)`). The version comes from
`agy --version`, with a five-second timeout and process-local cache. The old
`antigravity/1.1.22 darwin/arm64` identity silently omitted Gemini and Argon groups.
The corrected header was live-verified on the same account: the returned Argon
bucket had 40.90528% remaining, with its own provider reset deadline.

Production is tried first. A successful partial response does not stop later
summary endpoints. Generic Gemini and Argon groups remain separate; the fallback
never invents a weekly allowance from a short-window reading. A missing or
non-numeric fraction stays unknown. Both grouped and top-level-bucket summaries
are supported.

`week_gemini_argon` now holds actual provider measurements. Each sample has
`quota_source=retrieveUserQuotaSummary`. The page shows the measured percentage
and deadline prominently. Separate individual-cap CLI errors remain in raw
storage and status evidence, but are excluded from numeric chart lines. They are
not a zero-percent balance. Argon allowance is not priced as another subscription.

The local log scanner makes no model calls. It reuses verified-account, timestamped
Argon errors, bounded to 256 recent log files and 1 MiB per file. An active observed
cap can stop routing; an expired cap alone cannot establish a current balance.
A newer actual quota sample supersedes older log evidence.

## Private-adapter integration

Install the existing `integrations/agy-gemini.patch` first if its fetch/parser
hooks are not already present. Then, after updating the public helper code:

```sh
patch --dry-run /Users/petrlavrov/calm/private/ai-quotas-extra/agy.py < integrations/agy-current-quotas.patch
patch /Users/petrlavrov/calm/private/ai-quotas-extra/agy.py < integrations/agy-current-quotas.patch
```

The incremental patch updates the request identity and makes an explicit Google
login email mandatory: choosing another account does not store that account.
Existing borrowed client credentials are not replaced.

## Add a Google subscription

```sh
bash scripts/add-gemini-account.sh account@gmail.com
```

The existing private adapter opens Google sign-in, verifies the returned email,
and stores the new sampler refresh token in the existing Keychain service. After
successful consent the script samples Gemini, restarts the existing dashboard,
and mirrors it using the existing deployment path. No new scheduler is installed.
If sign-in is incomplete, that account has no measured quota yet.

## Verification

`pytest tests/test_argon_current_quota.py tests/test_agy_truth.py
 tests/test_panel_state.py tests/test_reset_budget.py` exercises partial endpoint
responses, separate Argon parsing, old-cap exclusion, account-scoped payloads,
and the actual 41%-remaining header. Live source JSON and browser screenshots
stay in ignored `output/playwright/`, because they contain personal quota data.

Related implementation experience, checked against live responses rather than
assumed: [DSH quota fixes](https://github.com/amlyczz/dsh-agy-link/blob/main/CHANGELOG.md).
