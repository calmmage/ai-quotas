# Quota reset notifications and short windows

Open <https://home.tail845ace.ts.net/quotas/>. Each provider keeps its own account
cards. Quota collection runs independently of the display preference.

1. Press **Show 5h quotas**. The chart and legend reveal the reported short
   windows as dim lines, including Codex 5h. On a phone the button reads **5h on**.
2. Press **1m**, switch **day/night**, or reload. The choice stays set.
3. Press **Hide 5h quotas** (**5h off** on a phone). Lines and hover entries hide;
   the collector continues storing every reported short-window value.
4. Inspect Gemini. An expired Argon rejection is historical evidence, with an
   explicit unknown endpoint. A successful real Argon response appears as
   **checked usable · quota unreported**. Its tooltip contains the observation
   time; no remaining percentage is invented.
5. When fresh readings confirm a **weekly or monthly** refill, Telegram identifies
   the account and shows consumption **before** reset: 🟢 ≥80%, 🟡 50–<80%,
   🔴 <50%. Per-window timestamps are folded. Every account is checked
   independently. Failed sends retry; delivered events are deduplicated.
   **5h resets never notify**; their readings and display toggle remain available.

Five frictions fixed:

| Before | Result | Evidence |
|---|---|---|
| No notification after an actual reset | Weekly/monthly refills notify through the existing sample job, coloured by previous consumption | Telegram message 774 proved delivery; the later weekly/monthly correction is verified separately |
| Only the primary login reached the alert evaluator | Reset detection groups by provider, account, and window | Two-account integration test; partial delivery retry test |
| Expired Argon rejection remained a numeric zero | Historical line ends at expiry; successful run supersedes old rejection | One tiny real Argon request returned OK; no numeric balance assigned |
| Deprecated Grok bot survived under other window names | Provider is retired throughout collection, loading, evaluation, and alerts | Retired-provider regression; stored history retained |
| Short windows had no control; zoom overrode visibility | Persistent display button; all reported short readings remain stored | Real browser show/hide, monthly view, reload, theme switch, and 390px checks |

Verification: 554 offline tests passed. Desktop and 390px browser views were
inspected; no horizontal overflow. The outgoing Telegram response contained an
`expandable_blockquote` entity. This proves delivery acceptance, not that the
recipient read the notification. Future scheduled reset delivery is covered by
the same existing sampler path, not by waiting for a future reset during this run.

Weekly/monthly correction: the earlier Telegram message 774 was edited in
place. Its accepted content now contains only the weekly reset, marked 🟢 for
100% consumption before reset. Its 5h entry was removed. Tests exercise monthly
resets, no short-window notices, and all three colours, including exactly 50%
and 80%, and a 0%-used period opening its next reported cycle.

Five notification frictions corrected: short windows notified; prior-period
consumption was absent; colours were absent; grouping obscured individual window
results; rounding near 50/80 could contradict the displayed colour. Each window
now has its own coloured line, and secondary observation details remain folded.

Custom emoji follow-up: this installation now uses Telegram's animated green,
yellow, and red hearts, resolved from a pack found through the owner's cached
messages. Message 774 was edited in place; the response retained a `custom_emoji`
entity with ID `5449380056201697322`. [Resolved IDs and API rules](../TELEGRAM-EMOJI.md).
The configured tags preserve a matching Unicode fallback, leave thresholds and
weekly/monthly filtering intact, escape account text, validate IDs, and retain
the folded observation details. These are the five format checks for this edit.

The provider quota endpoints currently omit Argon. Its exact remaining balance
is unverified; the successful request establishes usability only at that time.
Other account logins were not model-probed. No periodic model probes were added.

Local screenshots and receipts contain personal quota history and stay ignored:

- [Before: desktop](../../output/playwright/before-desktop.png)
- [After: short windows visible](../../output/playwright/after-desktop-night.png)
- [After: phone controls](../../output/playwright/final-phone-day.png)
- [After: Argon card](../../output/playwright/final-desktop-argon.png)
- [After: phone Argon card](../../output/playwright/final-phone-argon.png)
- [Telegram receipt](../../output/playwright/delivery-receipt.json)
- [Argon request receipt](../../output/playwright/argon-probe.json)
- [Corrected Telegram receipt](../../output/playwright/weekly-monthly-delivery-receipt.json)
- [Custom emoji receipt](../../output/playwright/custom-emoji-delivery-receipt.json)
- [Colour-format preview](../../output/playwright/weekly-monthly-preview.html) (synthetic examples; actual delivery is verified by the receipt). Preview screenshot capture was blocked by the browser's file-protocol restriction; native Telegram rendering of the correction remains unverified.
