# Gemini quota truth

The private adapter's successful `retrieveUserQuotaSummary` response can contain
only Claude/GPT. Since 15 October, accepting it as complete stopped all Gemini
series while the 30-minute sampler continued to exit successfully.

`integrations/agy-gemini.patch` connects the existing private adapter to the
credential-free helpers in `ai_quotas.agy_limits`. When the summary omits Gemini,
one legacy quota read supplements it. Only Gemini rows are merged; Claude/GPT
keeps its summary readings. A short-window reading never becomes a week. Missing
windows emit `unavailable` rows, invalidating historical successes for routing.

The collector reuses timestamped Argon errors from real CLI logs. It makes **zero
model calls**. It requires an Argon model context, the login reported in that log,
and `Individual quota reached ... Resets in <duration>`. The original error time
plus that duration determines `resets_at`; later samples cannot extend it. The
row is `week_gemini_argon`, 100% used, with `individual_cap`, `observed_at`, and
the log basename. No credentials, prompts, or full logs enter the row. Scanning
is bounded to 256 recent files, their first 1 MiB, within eight calendar days.
`AI_QUOTAS_AGY_LOG_DIR` overrides the log directory for tests or another install.

Argon checks its shared Pro window **and** its individual cap. An active cap is
STOP even in the last hour. At its deadline it becomes UNKNOWN until there is a
new observation; a past deadline does not prove a refill. UNKNOWN windows cannot
be picked. Flash does not read Argon's cap, but still requires its own known
weekly allowance. The new legacy response may omit that too, so a successful
Flash response does not automatically make `pick` say OK.

The dashboard shows the cap separately from priced usage, including observed
time and reset date. It reports the fresh short window beside the historical
week, suppresses spending advice and the future budget guide for a missing
window, and retains old samples as history. Individual caps do not create
subscription allocation, burn-rate, or dollar estimates.

## Integration

The worktree includes the night's dashboard branch. No background jobs or
servers are installed, enabled, restarted, or deployed by this change.

After integrating this branch into the checkout used by the existing sampler,
apply the private adapter patch (it changes two functions, no auth code):

```sh
patch --dry-run /Users/petrlavrov/calm/private/ai-quotas-extra/agy.py < integrations/agy-gemini.patch
patch /Users/petrlavrov/calm/private/ai-quotas-extra/agy.py < integrations/agy-gemini.patch
```

The existing sampler will then use the updated code on its next scheduled run.
No new launchd job is needed. This patch must follow the helper module: an older
checkout does not contain `ai_quotas.agy_limits`.

## Verification

```sh
uv run --extra all pytest -q tests/test_agy_truth.py tests/test_panel_state.py tests/test_burn.py
```

Full suite: `uv run --extra all pytest`. Local screenshots and their journey page
are kept under ignored `output/playwright/`, because they contain personal quota
history. `scripts/capture-gemini-truth.sh` repeats the desktop and 390px shots
against the static before/after previews; it must not target the live dashboard.
