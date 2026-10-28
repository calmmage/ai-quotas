# Google account quota view and current Argon readings

Petr: “I still see '0' in the argon sample.” “AGY and gemini for same account
should be the same plot, why are they separate? Just have two lines...” He also
confirmed a new Pro subscription on `petr.b.lavrov` and completed Google sign-in.

Open <https://home.tail845ace.ts.net/quotas/>. One **Google** card replaces the
separate Gemini and Antigravity cards. **Viewing account** selects a login and
remembers it after reload. That login's Gemini and Claude/GPT quotas have separate
lines on the same plot; a separately reported Argon pool has its own line too.
Every other login continues being sampled and stored.

The default `msuorange` account returned a dedicated Argon weekly bucket with
**40.90528% remaining** and its provider reset deadline. The card shows **41%**,
and a latest-reading dot makes even a few minutes of new history visible in a
week-long view. This is a quota RPC reading, with no model prompt.

Choose the new `petr.b.lavrov` account. Its identity is verified and it returns
weekly Gemini and Claude/GPT quotas. The subscription is **Pro**, confirmed by
Petr. Google's quota endpoint currently reports **Antigravity Starter Quota** and
says the account is ineligible for higher plan rate limits at this time. This is
displayed separately from the purchased subscription. It returns no Argon bucket:
the page says **not measured**, with no fabricated zero. A Starter response on
another account is likewise labelled as quota access, rather than inferred Free
billing. The reason for the new account's eligibility mismatch is unresolved.

Two older logins, `calmmage.shared` and `petr.lavrov.swiss`, receive Google's
“Verify your account to continue” response. The selector labels that gate and
their current balances stay unknown. Historical lines remain available; a
failed current read is not replaced with a recent successful balance.

**Show 5h quotas** reveals dim short-window lines and their status text;
**Hide 5h quotas** hides them together and persists across reload. Collection
continues in either state. Only weekly/monthly refills notify; 5h never does.

Five frictions fixed:

| Friction | Correction |
|---|---|
| Old CLI cap logs became a numeric zero; short new history was invisible | Raw logs remain evidence, actual quota readings supply the line, and the latest reading gets a visible dot |
| Old request identity and partial-success handling omitted Gemini/Argon | Installed CLI-style version header, production first, and continued attempts after incomplete answers |
| Gemini and Antigravity duplicated each account across cards | One account selector and one plot; primary-login source identity preserved; each pool keeps its own balance and deadline |
| New paid login was absent or labelled Free | Saved, identity-checked OAuth login; user-confirmed Pro billing separated from provider-reported Starter quota access |
| Hidden 5h lines left noisy status text visible | One persisted toggle now controls lines and short-window status together; no reset notifications for short windows |

The full offline suite passed **563 tests**. Final header changes also passed the
focused quota/header suite. Desktop, 390px mobile, persisted account selection,
5h on/off, and both uPlot/Plotly journeys are checked against the deployed page.

Private owner captures (ignored by Git):

- `output/playwright/google-combined-desktop.png`
- `output/playwright/google-new-combined-desktop.png`
- `output/playwright/google-combined-mobile.png`
- `output/playwright/google-new-combined-mobile.png`
- `output/playwright/google-combined-plotly.png`
- `output/playwright/new-google-account-summary.json`
- `output/playwright/new-google-account-entitlement.json`
- `output/playwright/google-final-readback.json`

The selector changes the viewed data. It does not switch CLI authentication.
Installed `agy` 1.4.8 has no account-profile switch command; the documented flow
is `/logout`, then start `agy` and choose the account in the browser. Google
shares local authentication across its Antigravity surfaces and says automated
consumer account rotation is unsupported. A hot-switch of an already-running
agent is unverified. [Primary sources and integration details](../GEMINI-TRUTH.md).
