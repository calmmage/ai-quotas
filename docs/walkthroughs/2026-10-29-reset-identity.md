# Reset alerts: account identity and source time — 29 Oct 2026

Entry: [live quotas](https://home.tail845ace.ts.net/quotas/).

## What was intended

The 27–28 Oct Codex transcript asks for notifications on **each account**, weekly/monthly only, consumption-coloured custom emoji, and continued collection of **all** quota values. The 16 Oct Grok conversation asks for reliable independent multi-account samplers. The 23 Oct Claude instructions preserve the separate owned Claude grant and forbid rotating Orca's borrowed credential. Exact source pointers are in [AGENTS.md](../../AGENTS.md#up-to-speed).

## What happened

- The real Codex refill was recorded on 28 Oct at 09:53:36 MSK and delivered at 09:54:07, separately for both accounts (65% / 83% consumed).
- At 29 Oct 02:41:18 a failed live probe fell back to a rollout with 51% consumed. Its source event was 02:17:21 MSK, but the stored row had no account or observation timestamp. The anonymous alert series compared it with another anonymous fallback from 28 Oct 09:17:41, showing 87% consumed. Seventeen hours of named observations were invisible to that series. Delivery state records the second notification at 02:59:47, matching the screenshot's approximately 03:01 message.
- The Antigravity alert at 18:32 coincides with the detailed adapter's active CLI account changing from msuorange to petr.b.lavrov. Orca's aggregate exposes no login. Comparing its previous 100% consumed total with the new account's 0% was an account switch, not a refill.
- The dashboard already hides the Orca aggregate when detailed AGY data exists; reset notifications omitted that same rule.

## Intended journey and five repaired failures

| Step / user experience | Failure | Repair and evidence |
| --- | --- | --- |
| Receive a reset notice, identify the login | Anonymous fallback became a second account | Codex fallback uses its configured home's identity; anonymous samples cannot establish reset events. Live and forced-offline reads both name the primary login. |
| Read the time, compare with the plot | Reading cached data again looked newly measured | Rollout event time and Orca `updatedAt` are retained as `observed_at`. Shared analysis uses that time for chart placement and current-value selection, while storage retains collection time. |
| See one notice for one actual refill | Sparse anonymous history / corrections produced new events | Reset proof requires named comparable observations within 90 minutes, a changed deadline and refill (or an actual deadline crossing). Long gaps remain history without asserting when a reset occurred. |
| Switch Google accounts and return to the same chart | Orca's anonymous aggregate compared different logins | Detailed per-account AGY readings remain the canonical notification source. The Orca sampler still runs and stores its aggregate; no account is guessed from the selector. |
| Change sampler source or retry after delivery | A second observation could generate a new fingerprint | Delivery also deduplicates by provider, account, window and next reset deadline, accepting legacy state. Unsent batches still retry; a genuinely new cycle still notifies. |

## Verification

- Full suite: **576 passed**. Includes 13 incident/provenance regressions, existing successful delivery/retry, all-account colouring, monthly resets and all-window storage/5h tests.
- Real database replay at 29 Oct 02:59:47: old code produces the anonymous 87% Codex reset; repaired code retains only the two genuine 09:53 named Codex events. Raw database and sent-message history were not rewritten.
- Live probe: both Codex accounts return `ok` (55% and 19% consumed at the probe); Google reads identify both successful accounts. Existing shared/Swiss Google verification errors remain explicit.
- Forced offline read: primary Codex returns `ok`, the correct account and the actual rollout event timestamp.
- `agy /usage` returned `num_turns: 0`, empty conversation ID and no identity. This confirms why assigning that aggregate to whichever account is selected would be unsafe.
- Deployed commit `0ebc834` with `make deploy`: dashboard restarted, plots regenerated and mirrored, existing sampler invoked. Local and home chart HTML, metadata and `panels.json` have matching SHA-256 hashes. The general and owned Claude sampler jobs remain loaded; their completed runs exited 0.
- Live alert dry-run at 03:50 MSK: **0 new alerts**, no state changes and no messages sent. The Telegram formatting checker accepts the two-account reset preview.
- Fresh scheduled collection at 03:51:41 MSK confirms both named Codex accounts are `ok` (55% / 19% used), primary Google is named and `ok`, and Orca aggregate collection continues with its own observation time. The independent owned Claude sampler also wrote four `ok` rows at 03:53:34. Another alert dry-run still proposes **0 new alerts**. Receipt: `output/playwright/reset-identity-fresh-sample.json`.
- Ignored evidence in the canonical checkout's `output/playwright/`: `reset-provenance-replay.json`, `live-probe.json`, `reset-identity-deploy.log`, `reset-identity-deployed-readback.json`, `reset-format-preview.html`.

## Deployed screen journey

Opened `http://home/quotas/` in a new Safari tab. The page renders the existing account cards and plots; primary Codex is labelled `petr.b.lavrov` at 55% used and shared Codex `calmmage.shared` at 19% used. [Entry screenshot](../../output/playwright/reset-identity-browser-attempt.png).

Clicked primary Codex **Details & settings**: its source expands to `petr.b.lavrov@gmail.com · petrs-macbook-max`, with quota-cycle explanation. [Expanded details](../../output/playwright/reset-identity-codex-details.png). Clicked the same control to return, then scrolled through the remaining provider charts. [Returned chart page](../../output/playwright/reset-identity-return.png).

Native browser control was initially extremely slow; browser-host DOM access was unavailable. The eventual Safari captures above are actual deployed-page evidence. Google selector switching and phone layout were not re-certified in this run; neither control was changed by this patch.

## Boundaries

No synthetic Telegram notification is sent. Delivery formatting and dedupe are exercised through the real alert pipeline with a captured sender. The next natural provider reset remains the live Telegram delivery acceptance step. The existing general sampler, owned Claude sampler and dashboard jobs are preserved.

Other Google access failures remain separate: shared/Swiss report provider verification required; the fresh scheduled read reports the `me@petrlavrov.com` Keychain lookup unavailable, although the earlier interactive probe succeeded. Credentials were not changed or recreated. The deployment-time Codex timeouts and AGY import failure did not recur in the fresh scheduled collection.

Catch-up coverage: live recent Notion source content was accessible, including the newest meeting parent with full transcript. The work-Mac retained meeting index is still dated 27 Oct 05:01 UTC, so current NAS receipt coverage is not claimed. Ordinary Claude/Grok web histories were not searched; the browser access host was unavailable. This gap does not replace the directly read project CLI instructions or the live incident rows.
