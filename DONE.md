# Rework 1019 — 19 Oct review

Local commits on `rework-1019`. Nothing was pushed, merged, or deployed.

## Commits

- `6cf70d7` Paint on-screen quota charts first, and total spend from columns.
- `03a5c8b` Merge overlapping Plan change labels into one pill.
- `579a4fc` Serve the dashboard when there are no samples.
- `3279b16` Reuse a usage check for ten minutes per login.

User-journey review, the same evening (top 5 fixed, one test each):

- `02bd393` Regenerate the page when the first samples appear. Test: `test_dash_regenerates_when_the_first_samples_appear`.
- `4f10c80` Let a second usage check wait for the first instead of failing. Test: `test_two_checks_at_once_both_answer`.
- `b174131` Plan change hover: plan names, one change per line, percent once. Test: `test_plan_change_names_the_plans_and_fits_a_phone_hover`, `test_plotly_pill_hover_prints_percent_once`.
- `8502c22` Keep close reset pills apart on a phone. Test: `test_close_money_pills_do_not_cover_each_other`.
- `92a1cfc` Status line: the vendor's words, not its JSON body. Test: `test_vendor_json_is_not_shown_raw`.

Journey report: https://claude.ai/artifact/XNBGEptA5PuCFijYvjvmWE (shots and scripts in `~/calm/state/reviews-2026-10-19/quotas-journey/`).

## Tests

`510 passed in 29.27s` (`make test`) after the journey fixes. Before them: `504 passed in 31.77s`; baseline before the first four commits was 497 passed.

## Build marker

`44b3e0f8e85f` (was `de7085be5870` before the journey fixes)

Computed the same way `scripts/deploy-rework-1019.sh` does: archive `rework-1019` and import `SHELL_VERSION`. The live pages currently serve `7fb8d78a47f3` (read-only GET of `panels.json` on 127.0.0.1:8876).

`scripts/deploy-rework-1019.sh --dry-run` refused before it printed the marker. The main checkout has uncommitted changes this work did not make:

- `ai_quotas/plots/prep.py`
- `tests/test_reset_budget.py`

## Screenshots

Looked at each of these. They are a local server against a read-only copy of the live database in `/tmp`, plus a dashboard pointed at a missing database. The live dashboard was not opened in a browser.

- Before, full page: `/tmp/aiq-rework-1019-shots/01-speed-before-full.png`
- Before, overlapping Plan change pills: `/tmp/aiq-rework-1019-shots/02-plan-change-before.png`
- Before, bare 404: `/tmp/aiq-rework-1019-shots/03-empty-before.png`
- After, full page (charts drawn): `/tmp/aiq-rework-1019-shots/04-speed-after-full.png`
- After, one Plan change pill on Claude · calmmage.shared: `/tmp/aiq-rework-1019-shots/05-plan-change-after.png`
- After, empty page: `/tmp/aiq-rework-1019-shots/06-empty-after.png`

Headless Chrome on the after page reached `quota-charts-ready` at 198 ms, with 6 charts drawn and 8 still waiting below the fold. After scrolling, 14 charts were drawn and none were still loading. The empty page reads "No quota samples yet. Run make sample". The missing database file was not created.

## What changed

1. The first paint draws the charts on screen. Charts further down wait until they scroll near. Spend totals are read from the database columns, so a regeneration no longer parses every turn. On the read-only copy, regeneration went from 6.25 s to 3.42 s. The test fails if the main page or a production-sized `panels.json` takes over 500 ms. Test: `test_page_and_panels_answer_within_half_a_second`, `test_charts_below_the_fold_wait_for_scroll`.

2. Plan change pills that overlap share one quiet "Plan change" label. Each change stays in the hover text. A money pill is left alone. Test: `test_close_plan_changes_share_one_pill`.

3. A missing database is an empty history. The page loads and says to run `make sample`. The file is not created. Test: `test_missing_database_serves_the_empty_page`.

4. Check now still runs on its own. The server reuses the last reading for that login for 10 minutes, whoever asks, and does not regenerate the plots for a reused reading. Test: `test_usage_check_is_reused_for_ten_minutes`, `test_cached_check_does_not_wait_for_a_regeneration`.

## Unverified

Regeneration of the production database is 3.42 s, not under 500 ms. The 500 ms test covers the page and `panels.json`. A page view that finds a reading older than 10 minutes still waits for a real probe and a regeneration.

Journey fixes were checked on local servers against a copy of the database with stubbed usage checks; the live page was not re-shot. On a 1440 screen, a mouse hover on a Plan change pill did not show the plan text in the capture script, before or after.

## Needs Petr

Deploy when you want this live. The deploy script will refuse until the two uncommitted files in the main checkout are committed or moved. The marker to wait for is `44b3e0f8e85f`. Main's uncommitted `prep.py` and `b174131` edit different parts of `prep.py`; a merge conflict is unlikely but possible.

Decisions from the journey report (F–I): reset time written on each panel, the «Chat about this page» button on phones and in the public repo, Gemini status lines, and plan flapping on calmmage.shared.
