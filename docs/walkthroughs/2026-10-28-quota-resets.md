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
5. When fresh readings confirm a refill, Telegram identifies the account.
   Per-window observations are folded. Every account is checked independently,
   including short windows. Failed sends retry; delivered events are deduplicated.

Five frictions fixed:

| Before | Result | Evidence |
|---|---|---|
| No notification after an actual reset | Observed refills notify through the existing sample job | Telegram message 774 accepted; repeat check had zero new events |
| Only the primary login reached the alert evaluator | Reset detection groups by provider, account, and window | Two-account integration test; partial delivery retry test |
| Expired Argon rejection remained a numeric zero | Historical line ends at expiry; successful run supersedes old rejection | One tiny real Argon request returned OK; no numeric balance assigned |
| Deprecated Grok bot survived under other window names | Provider is retired throughout collection, loading, evaluation, and alerts | Retired-provider regression; stored history retained |
| Short windows had no control; zoom overrode visibility | Persistent display button; all reported short readings remain stored | Real browser show/hide, monthly view, reload, theme switch, and 390px checks |

Verification: 528 offline tests passed. Desktop and 390px browser views were
inspected; no horizontal overflow. The outgoing Telegram response contained an
`expandable_blockquote` entity. This proves delivery acceptance, not that the
recipient read the notification. Future scheduled reset delivery is covered by
the same existing sampler path, not by waiting for a future reset during this run.

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
