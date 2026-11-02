# Alerts clarity, morning report, compact phone page — 3 Nov 2026

Petr, 3 Nov 2026:

> Put Action, then App (Claude / Grok / Codex etc) then account (if multiple connected) FIRST IN THE MESSAGE. DO NOT BATCH, IT'S USUALLY ABOUT A SINGLE THING ANYWAYS. SEPRATE FABLE AND OPUS ALERTS ARE STUPID
> If "quota reset" is on schedule, write something more clearly expressing that!!

> Attach usage plot of corresponding to each notification. Just for fun. And i guess send daily a full report - snapshots of all plots (as well as night/yesterday alerts digest?) in the morning?

> Fix mobile view: right now it's too much bulky text, too few plots. Make it more compact … Also the 'request desktop version' doesn't work

## Message shapes (verbatim, rendered from `ai_quotas/alert_text.py`)

Each Telegram alert is one event. It is sent as a photo of that login's last week, with the text as the caption. The login appears only when the app has more than one connected.

```
scheduled quota refresh · Claude · calmmage.shared
🔴 48% used before refresh
▸ Fable week refreshed too
  100% left now
  next refresh Mon 9 Nov 04:00
  seen Mon 2 Nov 04:02
```

```
unexpected quota refresh · Codex · petr.b.lavrov
🟡 70% used before refresh
▸ 99% left now
  due Thu 5 Nov 14:31, came early
  next refresh Tue 10 Nov 10:00
  seen Tue 3 Nov 10:00
```

```
manual quota refresh · Codex · petr.b.lavrov
🟢 96% used before refresh
▸ 100% left now
  banked reset used · Weekly limit reset
  due Thu 5 Nov 14:31, came early
  next refresh Tue 10 Nov 10:00
  seen Tue 3 Nov 10:00
```

```
use it before reset · Grok
86% left · refresh in 23h 53m
▸ refresh Tue 3 Nov 23:07
  unused quota is lost at refresh
```

```
burn stop · Claude · calmmage.shared
27% left · refresh in 4d 8h
▸ 558% of quota pace
  on track for 420% by refresh
  refresh Thu 5 Nov 11:00
```

`▸` marks the `<blockquote expandable>` fold. The colour marker is the existing reset emoji (random custom emoji on the laptop). `check.py --caption --lines 3` is clean on all five (`tests/test_alert_clarity.py::test_every_new_shape_passes_the_telegram_check`).

### Refresh words

The split is the one the plot already draws (`plots/prep.py` `classify_money`, `Reset used` pill):

- **scheduled**: the reading crossed the published deadline, or came within the plot's 12 h slack of it.
- **manual**: early, and a banked reset of that login left the vendor listing within 3 h of the refill. This does not wait out the dashboard's 2 h grace, because the refill itself confirms the redemption.
- **unexpected**: early, with no such credit.

Cannot be told apart: a banked reset used while the credit probe was failing, or for a vendor that lists no credits. It reads as `unexpected` (`test_banked_reset_without_a_credit_listing_reads_as_unexpected`).

New rule: an early refill waits for the next reading. The Grok `Quota reset` at 10:06 on 2 Nov was a probe glitch: 14% → 0% at 10:12 → 14% at 10:42, back on the old 3 Nov deadline. That now sends nothing (`test_early_refill_waits_for_a_second_reading_and_a_glitch_never_alerts`). Scheduled refreshes still send at once.

### Why the same warning arrived twice

Two hubs sent alerts, each with its own `alert-state.json`:

| warning | work Mac sent | laptop sent |
|---|---|---|
| Grok 2 days ahead | 1 Nov 23:11 | 1 Nov 23:30 |
| Grok 1 day ahead | 2 Nov 23:13 | 2 Nov 23:45 |

The work Mac's `com.calmmage.ai-quotas-sample` runs as a hub with no node config. It resolves the bot token through Keys, so it sends. Fix: `AI_QUOTAS_ALERTS=off` keeps a node sampling and pinging Healthchecks without sending. Setting it in the work Mac LaunchAgent was refused by auto mode; see Open items.

Also fixed in the single sender: a deadline that wobbles across a minute edge no longer makes a new fingerprint (match within 2 min, `test_use_it_before_reset_goes_out_once_per_cycle`). And a window that just refreshed gets no warning for the cycle that ended.

## Morning report

Runs inside the job that already sends: `ai-quotas sample` → `run_after_sample` → `morning.run_morning`. On the laptop that is LaunchAgent `com.calmmage.ai-quotas-sample`, every 30 min. It sends at the first sample from 08:00 to 13:00, Europe/Moscow, once per day; the date is stored in `alert-state.json`. One album goes to the same alert chat: a plot for each connected login, with this caption on the first photo:

```
morning quotas · Tue 3 Nov
Claude 98% · Codex 91% · Grok 86%
1 alert since yesterday
▸ Mon 23:14 use it before reset · Grok
```

Delivered alerts are logged to `<data_dir>/alert-log.jsonl`. Preview without sending: `uv run ai-quotas alert --morning --dry-run --png-dir /tmp/q`. A manual `--morning` send does not mark the day as done.

## Phone page

Design code: one line per fact, optional detail on tap, the plot is the page.

- Panel head on a phone: title, then one short line each for the reset window, credits + resets (`84,336 credits  3 resets · Expires 07 Nov · 5d left`) and value. No boxed cards, no 22px numbers. "Current balance" and "Burn alerts relaxed" open on tap. Plots are ~255px tall instead of 180px; about two panels fit a screen.
- Desktop request: `view_mode.js` runs in `<head>` before layout and sets `html[data-view=phone|desktop]`. Order: the on-page choice (`localStorage` `quota-view`, in try/catch), then a desktop-site request (touch screen whose browser claims Mac or Linux desktop, which is what iOS Safari's Request Desktop Website sends), then width ≤ 430px. Desktop on a phone screen sets the viewport to `width=1100` scaled to fit. All former `@media (max-width: 430px)` rules are scoped to `[data-view=phone]`; `layout.js` `columns()` reads the mode. Also in `live.html`, whose viewport decides the iframe's width.
- On-page control: a phone/monitor icon on the title line, words on hover; shown on phone-size or touch screens, or once a choice is saved. Picking what the device would choose anyway clears the saved choice.

Proved with screenshots of real data (Chromium via Playwright imitating an iPhone, `tests/ui/`):

| shot | shows |
|---|---|
| `13-phone-night.png`, `14-phone-night-codex.png` | 390px phone: compact heads, one column, credits on one line |
| `15-phone-desktop-request.png` | 390px with a Mac user agent, `MacIntel`, 5 touch points: 1100px layout, 2 columns |
| `16-phone-desktop-toggle.png` | 390px after tapping the control: desktop, 2 columns, fitted; phone → desktop → phone clears the choice |
| `17-desktop-1280-night.png` | 1280px desktop unchanged, no control |
| `18-phone-day.png` | day (Plotly) page on a phone |
| `19-phone-codex-3-resets-edited.png` | edited `panels.json` (Codex 3 resets) to match his screenshot; the others are unedited data |

Not verified on a real iPhone: Safari's own Request Desktop Website and the toggle there. Tests: `tests/test_view_mode.py` (decision order, viewport, blocked storage, `columns()`, no width-only phone rules, one-line head).

## Open items

OPEN_SECTION
