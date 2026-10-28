# Current Argon quota and another Google Pro account

Petr: “I still see '0' in the argon sample.” He also purchased another Pro
subscription on the `petr.b.lavrov` account.

The same authenticated Google account returned different quota groups depending
on the client identity header. The corrected CLI-style header returned a dedicated
Argon weekly bucket: **40.90528% remaining**, with the provider's reset deadline.
This is a quota read, with no model prompt. It supersedes the old CLI rejection.

Entry: <https://home.tail845ace.ts.net/quotas/> → Gemini account card → **Argon ·
41% remaining**. The Argon series is drawn only from real quota samples. A cap
log remains status evidence, with no numeric zero line. Both chart engines use
the same account-scoped payload and status header.

Five frictions fixed:

| Friction | Correction |
|---|---|
| Old cap log appeared as a current zero balance | Log observations stay in raw storage and status evidence; numeric charts use actual quota readings |
| Request identity omitted current model groups | Read the installed CLI version and send its current CLI-style quota header |
| Partial success prevented trying other endpoints | Prefer production; continue after incomplete responses |
| Argon could fold into shared Gemini Pro | Parse the dedicated Argon group separately and route against its own pool |
| New paid account had no saved sampler login | One Google sign-in script, exact-email guard, then automatic sampling and deployment |

561 offline tests passed, including the captured Argon group, a real 41% header,
old-cap exclusion, partial endpoint responses, and account isolation. Browser
captures and live readback receipts remain in ignored `output/playwright/`.

The new Google subscription still needs its own OAuth consent before any quota
can be attributed to it. No existing account's token is relabeled as the new
account. `bash scripts/add-gemini-account.sh petr.b.lavrov@gmail.com` opens the
sign-in and completes sampling and refresh afterward.
