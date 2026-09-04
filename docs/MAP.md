# ai-quotas map

Owner-machine snapshot, 23 Aug 2026 · **moved 04 Sep 2026** (task 09_04_5, adr 0026 draft; cloud mirror per adr 0025 §10): canonical package is now `~/calmmage/projects/meta/ai-quotas`; launch agents + alias come from new-nonix (`~/calmmage/projects/meta/nonix/launchd`, `shell/aliases.sh`); private adapters in `~/calmmage/private/ai-quotas-extra/`. `~/work/projects/ai-quotas` and the old-nonix plist are frozen, not deleted. Paths below that still say `~/work/...` describe the pre-move state.

Package code lives in this repo. Live wiring does not.

## Tree

```
ai-quotas
├── USED NOW
│   ├── ~/calmmage/projects/meta/ai-quotas/ canonical package (CLI, lib, plots, spend, agentic_step, reset credits) — was ~/work/projects/ai-quotas
│   │   ├── ai_quotas/adapters/             claude, codex, grok, openrouter
│   │   ├── ai_quotas/plots/static/         plotly.html, uplot.html, index.html, time_axis.js, theme.js
│   │   └── automation/*.plist.template     sample + agentic-step-check (dash plist is NOT here)
│   ├── ~/calmmage/private/ai-quotas-extra/ AI_QUOTAS_EXTRA_ADAPTERS — agy.py (self-contained copy of the donor, 04 Sep)
│   ├── ~/work/prototypes/poc/quota-providers/agy.py   donor of that copy — archive since 04 Sep (not imported at runtime)
│   ├── ~/.local/share/ai-quotas/
│   │   ├── ai-quotas.sqlite3               LIVE quota + spend + harvest cursor database
│   │   └── plots/                          nginx + dash write here → http://home/quotas/
│   │       ├── live.html                    DEFAULT page (day=Plotly, night=uPlot)
│   │       ├── 03_plotly/index.html         day engine (kept)
│   │       ├── 10_uplot/index.html          night engine (kept)
│   │       └── 00_INDEX.html                money / resets nav
│   ├── ~/work/projects/ai-quotas/.plots-bak-23-Aug-2026/  snapshot of live HTML before landing-page change (gitignored)
│   ├── ~/calmmage/projects/meta/nonix/     new-nonix (adr 0014) — the owner wiring
│   │   ├── launchd/com.calmmage.ai-quotas-dash.plist     running · uv run dash --port 8876 · 30s poll · AI_QUOTAS_AFTER_REGEN = launchpad mirror
│   │   ├── launchd/com.calmmage.ai-quotas-sample.plist   interval 1800s · writes default SQLite database
│   │   │                                    (both symlinked into ~/Library/LaunchAgents by launchd/deploy-plists.sh)
│   │   ├── nginx/servers/home.conf          alias /quotas/ → plots dir (index = live.html)
│   │   └── shell/aliases.sh                 ai-quotas() / aiq · extra adapters; default database
│   └── cloud mirror (adr 0025 §10, launchpad task 09_04_4)
│       ├── ~/calmmage/projects/meta/launchpad/deploy/mirror-home-quotas.sh   rsync plots dir → hetzner /opt/calmmage/quotas (fired by the dash after each regen)
│       ├── https://hetzner-helsinki.tail845ace.ts.net/quotas/   read-only copy; live.html shows "stale" past 2 h via meta.json
│       └── plots/meta.json                  generated_at · stale_after_s · poll_interval_s · host
│
├── DONOR / SUPERSEDED (not on the live path, still referenced)
│   ├── ~/work/prototypes/governor/quotas.py
│   ├── ~/work/prototypes/poc/quota-providers/{claude,codex,grok,openrouter,watchdog}.py
│   ├── ~/work/prototypes/poc/quota-providers/TOKEN-GAUGE.md
│   ├── ~/work/prototypes/quota-plot-bakeoff/   archived; winner migrated here
│   ├── token-quota skill                   still names donor adapters as live
│   └── platform/.../orchestrator.py        still calls governor/quotas.py
│
└── PRESERVED LEGACY / STALE (not live)
    ├── ~/calmmage/data/automation_logs/quota/{samples,spend}.jsonl
    ├── ~/calmmage/data/automation_logs/quota/spend-cursor.json
    ├── ~/.local/share/ai-quotas/spend.jsonl        older default-path harvest
    ├── ~/.local/share/ai-quotas/spend-cursor.json
    ├── com.calmmage.quota-snapshot.plist.bak       old watchdog agent
    └── matplotlib extra                    removed from pyproject (PNG examples stay as docs images)
```

## Website versions

| # | What | URL | Status |
|---|------|-----|--------|
| 1 | **Live (wired)** | `http://home/quotas/` → `live.html` → Plotly (day) or uPlot (night) | **this is the one** |
| 2 | Same files | `http://quotas/` (hosts) · `http://127.0.0.1:8876/` (dash) · `http://localhost/quotas/` | same dir |
| 3 | Nav / money | `http://home/quotas/00_INDEX.html` | linked from plot header |
| 4 | Engines kept | `…/03_plotly/index.html` · `…/10_uplot/index.html` | backup + toggle targets |
| 5 | Bakeoff out/ | `~/work/prototypes/quota-plot-bakeoff/out/` | frozen, not served |
| 6 | Home catalog | `http://home/` card points at `/quotas/` | pointer only |
| 7 | Cloud mirror | `https://hetzner-helsinki.tail845ace.ts.net/quotas/` (tailnet; docker `calmmage-home` on hetzner) | same files, refreshed after each regen; stale bar when the Mac is off |

nginx `index` is `live.html` then `00_INDEX.html`. Directory URLs like `/quotas/03_plotly/` 403 unless you hit `index.html` — the in-page links already do.

## Not in this repo (but used)

1. Gemini/agy adapter + its donor (`ai-quotas-extra` + `quota-providers/agy.py`)
2. nginx alias, zsh alias, both LaunchAgent plists — new-nonix `nginx/servers/home.conf`, `shell/aliases.sh`, `launchd/`
4. Cloud mirror script + registry entry — `projects/meta/launchpad` (`deploy/mirror-home-quotas.sh`, `registry/services.json` id `quotas`)
3. Live database contents under `~/.local/share/ai-quotas/` (code/schema are in the repo)

Public adapters, trend math, CLI, plots, spend harvest, agentic_step join: **in the repo**.

`scripts/apply-owner-cutover.sh` (Aug 2026 owner cutover in the old world) is
**superseded** by the 04 Sep move; kept for history, do not run.

## Roadmap

### Plot landing (this round)

- [x] Default `live.html` / `http://home/quotas/` = plots, not nav
- [x] Day = Plotly, night = uPlot, remembered in `localStorage`
- [x] Link from plot header → `00_INDEX.html`; link from index → plots
- [x] Keep both engine pages as backup; snapshot in `.plots-bak-23-Aug-2026/` (gitignored)
- [x] **Publish:** dash reloaded from the new home 04 Sep 2026 (bootout + bootstrap of the new-nonix plist)
- [x] Tokens/$ on hover/reset **labels** only (not a second curve)
- [x] Daily spend **strip** from SQLite `spend_turns` (separate grain; not on the remaining-% line)
- [x] uPlot hover tooltip with remaining % (+ leftover $ / tokens when known)
- [x] 5h series dimmed (opacity ~0.3, thinner stroke)
- [ ] nginx `index` add `index.html` so `/quotas/03_plotly/` is not 403 (nonix, outside this repo)

### Move remaining active code here

- [x] Copy `agy.py` into `ai-quotas-extra` (stop importing the donor at runtime) — 04 Sep: `~/calmmage/private/ai-quotas-extra/agy.py`
- [x] Point extra-adapters README at that copy; leave `quota-providers/` as donor archive
- [x] Copy `TOKEN-GAUGE.md` → `docs/TOKEN-GAUGE.md`
- [x] Ship `com.calmmage.ai-quotas-dash.plist.template` next to the other automation templates
- [x] Keep Gemini private (extra dir stays **out of the wheel**)

### One data dir

- [x] Migrate samples, spend, and harvester cursor to `~/.local/share/ai-quotas/ai-quotas.sqlite3`
- [x] Update the sample LaunchAgent and shell alias to use the package default database; preserve old JSONL files for rollback
- [x] `make doctor` prints set vs unset env and resolved paths

### Retire donors

- [ ] Mark `governor/quotas.py` and `quota-providers/watchdog.py` archived; grep remaining callers
- [ ] Retarget `platform/.../orchestrator.py` + `pipeline.py` to `ai-quotas` CLI/lib
- [ ] Fix `token-quota` skill: drop “donor adapters still at quota-providers”
- [ ] Unload/delete `com.calmmage.quota-snapshot.plist.bak` after confirming sample agent is healthy
- [x] Drop unused `matplotlib` extra (PNG examples stay as docs images)

### agentic_step

- [ ] Install weekly check LaunchAgent from the repo template (not loaded today)
