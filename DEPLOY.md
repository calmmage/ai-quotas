# Deploy AI quotas

- Branch: `main` on `origin` (`calmmage/ai-quotas`). Single release branch;
  merge an updated `origin/main` before pushing. No `dev` branch is used.
- Renderer owner: this checkout on the main Mac. The existing
  `com.calmmage.ai-quotas-dash` LaunchAgent serves `127.0.0.1:8876` and writes
  `~/.local/share/ai-quotas/plots/`. The existing sampler remains the only sampler.
- Cloud: nginx serves `/opt/calmmage/quotas` on `root@100.78.4.58` over the
  tailnet. URLs: `https://home.tail845ace.ts.net/quotas/` and `http://home/quotas/`.
  A Git push does not deploy these files.

## Release

1. Run focused tests and `git diff --check`; stage source only. Never stage
   `.env`, credentials, databases, captured quota snapshots, or preview exports.
2. Commit, merge `origin/main` if needed, push `origin main`.
3. Configure the existing dashboard's `AI_QUOTAS_AFTER_REGEN` to
   `/bin/bash <checkout>/scripts/mirror-quotas.sh`. This mirrors only quota files,
   without replacing any unrelated home application.
4. `make deploy` restarts the installed dashboard, kicks its existing sampler,
   regenerates plots, and calls that mirror hook. `make restart` only restarts.
5. Verify both `10_uplot/index.html` and `03_plotly/index.html` at the cloud URL.
   In cloud `panels.json`, `release_commit` must equal `git rev-parse HEAD` and
   `shell_version` must equal the generated local value. Open the actual URL in
   Chrome and exercise the Stack accounts toggle and navigation.

## API records

API billing observations, claim states, expirations and vault references use
`quota_api_records` in the existing `ai-quotas.sqlite3` database. They are manual
observations until a provider connector supplies verified records; subscription
quota percentages are not API balances. Grant amounts are never added to a
balance twice. Unknown expiry stays unknown. Key values are never accepted.

The Mac editor uses `/api/tracking` with the existing same-origin/token write
protection. The cloud serves a read-only snapshot and opens the configured
`AI_QUOTAS_SETTINGS_URL` editor for changes. Existing keys remain owned by the
vault; the dashboard stores only variable names and masked fingerprints.

Optional `AI_QUOTAS_ACCOUNT_LABELS_JSON` supplies non-secret display aliases.
The renderer's Stack accounts preference and editable account weights stay in
browser-local settings, shared between day and night.
