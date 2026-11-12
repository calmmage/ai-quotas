# API balances, subscription benefits, and stacked totals on the main page

Approved design and implementation, 13 November 2026. Production uses the
existing live uPlot/Plotly renderers. API balances and grants are explicitly
recorded observations; unknown values stay unverified. Key inventory contains
vault references and configured variable names, never secret values.

[Live dashboard](https://home.tail845ace.ts.net/quotas/) · [Deployment](../../DEPLOY.md).
The captured design preview remains a local review artifact and is not published in Git.

## Petr's request

> I want new (top bar nav) tabs for ai-quotas page
>
> - I want api balances (and expirations)
>
> And maybe i want api keys
>
> For ALL accounts
>
> And beyond that there's also this 'claim api credits == to your subscription cost)
> I want to track that too (maybe on the plots?)
>
> Add a new, special, 'unified' view of total available tokens (usage?) instead of current 'split-by-account'. Total = by provider (e.g. all codex subscriptions, all claude subscriptions etc)
>
> Although I am not sure how to properly visualize the breakdown by vendor.
>
> Maybe like stack them? And have vertical lines be from one vendor to another? would be cool, i think..

## Proposed navigation

| Tab | What it answers |
| --- | --- |
| Quotas | A persistent toggle selects either original account plots or stacked provider plots. |
| API balances | What can each billing organization spend, and what expires next? |
| Credits to claim | Has a benefit been checked, linked, issued, spent, or expired? |
| API keys | Which account/project does each key belong to, and where is it managed? |

The account filter covers personal, shared, work, and the existing extra Google
logins. Missing readings stay visible as unknown. They do not contribute zeros.

## Main plots: Petr's correction

> Did I ask to change the main plots?!
>
> The 'stacked' versions should look exactly the same, and should be in the same main page!!

Use the existing uPlot/Plotly renderer, panel CSS, native headers, axes, budget
guides, reset markers, range navigator, settings, and layout controls. Original
account panels and their payloads remain unchanged. Provider totals are added
in place of those panels in the same grid, with the same rendering machinery.
There is no separate Unified navigation tab and no replacement SVG plot design.

Petr clarified:

> The intention was to have this as CONFIGURABLE TOGGLE. to see EITHER stacked vs non-stacked versions!!!
>
> In addition to filler colors, KEEP THE VERTICAL BAR FEATURE! And have each stacked plot have ITS OWN VERTICAL BARS with JUST ON ITS TERRITORY
>
> And GROK 'all-accounts' plot - which has only one account - hence should be EXACTLY as the non-stacked version.

The toolbar has one persistent “Stack accounts” checkbox. Off restores the
original account panels; on replaces multi-account providers with their stacks.
Singleton providers, including Grok, pass through as the original panel with
unchanged data and renderer options. There are no extra singleton total cards.
Consumption bars start at their account layer's baseline and stop at that
layer's boundary. Reset/event lines and badges stay in their own territory.
Budget guides are likewise mapped within their layer. Switching API tabs
preserves the mounted renderer and selected range. Day/night retain the
existing uPlot/Plotly engines.

Petr's next correction:

> still missing the work accounts for some reason..
>
> A stupid hole in the middle of the plot
>
> tooltips are gone, but still see crosshead
>
> all extra gemini accounts are missing too..

All three work accounts and all six Gemini logins must have real plotted
layers, not just status text. Hover, cursor lines and cursor dots stay local to
the plot under the pointer. The native latest-observation marker remains.
Missing samples do not erase other accounts' visible layers. Gaps between
observations are bridged for display, hatched, and explicitly identified as
interpolated in hover details; original measurements are unchanged.

Production source: `ai_quotas/plots/static/stacked.js`, shared navigation in
`navigation.js` / `navigation.css`, and the native `uplot.html` / `plotly.html`
templates. All pages share `panels.json` and the existing poller. Billing
observations, claim states and vault references use `ai_quotas/api_tracking.py`
and `quota_api_records` in the existing quota SQLite database. Cloud snapshots
open the Mac editor for writes, using the existing same-origin/token protection.
Billing organizations are counted once; grants are not added to balances again.
The UI supports record, reload, expiry, account filter, provider management and
return to the matching quota plot. Automatic provider billing connectors remain
separate work; a subscription price is never presented as an API balance.

## Units and identity

- Codex personal costs $500/month and shared costs $100/month, confirmed by
  Petr. The old persisted Pro Max $100 override was corrected to $500.
- Known prices supply a **weight proxy**: $100/month is one weight unit.
  Personal Codex weighs 5 and shared weighs 1. Sum remaining percentages times
  their account weights.
  This is not an absolute token allowance, verified provider capacity ratio,
  API balance, or cash remaining. The current official pricing page does not
  establish an absolute per-account token allowance for these plans.
- An unknown or free-plan capacity no longer excludes the account. It starts
  with the provider's smallest known positive weight, marked `?` and described
  as an **assumed reference weight**, not a reported plan price. Details allows
  per-account weight editing and reset; browser-local settings persist
  across reloads and day/night. This makes the assumption concrete and editable
  without inventing a billing fact. A user-declared Google
  Pro subscription supplies its price separately from Google's reported
  Antigravity Starter access tier. Provider/account identity stays distinct.
- Stale accounts have dashed ghost layers with their last known reading and
  timestamp. They do not contribute to the observed current subtotal. Before
  an account's first observation its layer has no height and hover says
  “not observed yet”; this does not create a zero-percent reading.
- Hover and the inspection cursor are local to the active plot. Tooltip rows
  retain each account's raw
  percentage, sourced token calibration when available, and estimated USD
  allocation value. Unmeasured tokens and unknown USD stay explicit.
- Recorded token usage is a separate measure over the selected interval.
  Input, output, cache-read and cache-write counts need separate provenance.
- Extend the existing token calibration only after it has provider, account,
  model and current-window coverage. `docs/TOKEN-GAUGE.md` describes the current
  primary-account estimator; it is not sufficient proof of all-account capacity.
- Actual currency balances sum once per provider billing organization. Several
  keys or logins can refer to that same balance. An email is not an organization ID.
- A grant's remaining balance is already part of the API balance. Never add the
  grant amount to the balance again. A claim marker is an event, not a subscription
  quota refill.

## Credit benefits: checked sources

[OpenAI's developer guide](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)
and [help article](https://help.openai.com/en/articles/20001626-api-credits-benefit-for-eligible-chatgpt-subscriptions)
describe eligible Pro and Business benefits. Pro's full-period amount follows
the eligible tier; Business depends on paid seat types and caps. The receiving
API organization and actual issued amount must be verified. Renewal, proration,
and expiry mean subscription price alone is not a balance.

[Claude's benefit guide](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)
describes monthly Max, Ultra and Team benefits, organization linking, fixed tier
amounts or pooled seat amounts, and end-of-cycle expiry without rollover. A paid
plan is not evidence that credits have been claimed or issued.

Proposed states: **not checked → eligible / ineligible → linked → issued → active
→ exhausted / expired**. Store observed grant amount, remaining amount, issuing
subscription, receiving organization, issued-at, expires-at, last checked time,
and source. An unavailable source does not erase the last observation or turn it
into a current result.

## Existing owners to extend

- `ai_quotas/collector.py`, `storage.py`, and the existing scheduler own sampling.
- `plots/generate.py`, `plots/dash.py`, `plots/static/` and `panels.json` own the
  dashboard and its existing publish path.
- `spend.py` and the current token gauge own recorded usage/calibration.
- `reset_credits.py` tracks **subscription quota resets**. API credit grants are a
  different record type; preserve the reset history and naming.
- `boosts.py` likewise remains separate from API credit balances.
- `~/calm/framework/tools/vault/keys/` owns key values and metadata. The preview
  proposes masked inventory and provider-management links. Reveal/copy is still
  an unanswered product choice, and any real implementation must retain the vault
  as owner. Static published data contains no secret values.
- `adapters/openrouter.py` currently reads `/api/v1/key`, which describes key
  usage/caps. [The account credit endpoint](https://openrouter.ai/docs/api/api-reference/credits/get-credits)
  is `/api/v1/credits`; do not equate a key limit with the account balance.

## Acceptance for the real build

1. Each known account is represented, even without accessible API billing.
2. Provider/account/organization links are verified, with shared balances counted once.
3. Exact expiry and benefit status have source and observation timestamps.
4. A selected period changes measured usage; it does not change current balance.
5. Original account plots and stacked provider plots stay on the same Quotas page.
6. Grant → API balance → same organization and account; unknown eligibility cannot
   create a claim event.
7. Key inventory → existing vault/provider management → same filtered inventory.
8. Desktop and 390px journeys work, including keyboard detail/return paths.

## Preview verification

Real Chrome, isolated temporary profile, desktop 1440px and mobile viewport 390px.
Correction screenshots, renderer/source receipts, baseline comparison and the
executed journeys are under `output/playwright/ai-quotas-native-correction/`.
The earlier alternative-layout preview was superseded by Petr's correction.
This is a design artifact, not live API billing
integration or a provider claim receipt.
