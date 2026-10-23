# Samplers: logins that stay fresh on their own

[All docs](../README.md#docs)

By default ai-quotas only **reads** the logins of the CLIs you already use
(`claude`, `codex`, `grok`). It never refreshes the Claude or Codex tokens:
refresh tokens rotate, so a refresh by ai-quotas would sign that CLI out. The
borrowed Grok login is the old exception: when its token is about to expire,
or the API answers 401/403, ai-quotas refreshes `~/.grok/auth.json` and writes
the new tokens back. The catch is that a
login no client uses for a few weeks expires, and its quota goes stale.

A **sampler** fixes that. You sign in once for it, and from then on it keeps
its own login alive and samples on a schedule: on your laptop, on a server,
in a container, under any scheduler. Your everyday CLI logins are not touched.

## Four ideas

1. **Owned login.** `ai-quotas login claude --name work` runs the vendor's own
   sign-in into a fresh directory, `<logins dir>/claude-work/`. That is a new
   grant with its own refresh-token chain, not a copy of your CLI's tokens, so
   the sampler is its only holder and may refresh it freely. The directory
   holds `login.json` (`owned: true`, provider, name, account) and the
   vendor's credentials file.
2. **Sampler.** One process per owned login: `ai-quotas sampler --login
   claude-work`. Each tick locks the login, refreshes the token when it is
   within 10 minutes of expiry, reads the quota, writes rows, and pings
   Healthchecks. It writes into the hub database on the same machine, or into
   a **spool** when the hub is elsewhere.
3. **Spool → hub.** The spool is plain JSONL files,
   `<spool>/<device>/<login>/YYYY-MM-DD.jsonl`. The hub (the machine with the
   database, page and alerts) imports them with `ai-quotas ingest`. Importing
   twice adds nothing. How the files travel is up to you: the same disk, a
   docker volume, rsync over ssh (`pull` / `push` below), Syncthing.
4. **One merge rule.** Every row says where it came from: `source` is `owned`
   (a sampler) or `borrowed` (your everyday CLI login). Within one series
   (provider, account, window), an owned reading hides a borrowed one taken
   within ±15 minutes. A newer failed row never hides an ok reading less
   than 90 minutes older.

   So both directions fall back: when samplers stop, your local readings carry
   the series; when the laptop sleeps, the samplers do.

Only the hub alerts. A machine with `role = "sampler"` never sends alerts, so a
second machine never doubles them.

## Pick a setup

| You have | Hub (page, alerts) | Samplers | Setup |
|---|---|---|---|
| A laptop only | laptop | LaunchAgents / cron on the laptop | [A](#a-everything-on-one-machine) |
| A laptop and a server | laptop | containers on the server | [B](#b-samplers-on-a-server-hub-on-the-laptop) |
| A server that should be the hub | server | containers on the server, laptop sends its local readings | [C](#c-the-hub-on-a-server) |

### A. Everything on one machine

```bash
make login PROVIDER=claude NAME=work     # browser sign-in, once
make login PROVIDER=codex NAME=work
uv run ai-quotas logins                  # each login: ok / refresh-due / needs-relogin
make sampler-once                        # one tick of every login, into the database
make install-samplers                    # macOS: one LaunchAgent per login, every 30m
```

Linux and any other scheduler: run `ai-quotas sampler --all --once` every 30
minutes (cron, xyOps, a CI schedule), or use the systemd units in
[`deploy/systemd/`](../deploy/systemd/) (one timer per login, hardened).

### B. Samplers on a server, hub on the laptop

On the server (Docker):

```bash
cd deploy/docker
cp compose.example.yaml compose.yaml       # one 4-line service per login
mkdir -p logins spool && sudo chown -R 10001:10001 logins spool
```

On the laptop, sign in and send each login to the server. The local copy is
removed, so the server holds the only one:

```bash
make login PROVIDER=claude NAME=work MOVE_TO=me@server:/srv/ai-quotas/deploy/docker/logins
```

Back on the server:

```bash
AI_QUOTAS_DEVICE=server docker compose -f compose.yaml up -d --build
```

On the laptop, pull the spool before each import (`~/.config/ai-quotas/config.toml`):

```toml
[[ingest]]
path = "~/.local/share/ai-quotas/inbox/server"
pull = "me@server:/srv/ai-quotas/deploy/docker/spool/"
```

The laptop's regular `ai-quotas sample` (or `make sample`) now samples its
own CLIs, pulls and imports the server's spool, then alerts on both. `uv run
ai-quotas ingest` does only the pull and import.

### C. The hub on a server

The compose file has an optional `hub` service: it imports `./spool` every 15
minutes, sends alerts, and serves the page on the server's loopback (port
8765; put your own proxy in front).

```bash
docker compose -f compose.yaml --profile hub up -d --build
```

The laptop keeps sampling its everyday CLI logins as the fallback and sends
those rows to the server:

```toml
[node]
role = "sampler"
device = "laptop"

[spool]
push = "me@server:/srv/ai-quotas/deploy/docker/spool/"
```

## Signing in

`ai-quotas login <provider> --name <name> [--move-to HOST[:DIR]] [--default]`

| provider | what runs | where the token lives |
|---|---|---|
| `claude` | `claude auth login --claudeai` (URL + pasted code) | `.credentials.json`. On macOS the CLI first stores it in the Keychain; ai-quotas moves it into the directory and deletes the Keychain item |
| `codex` | `codex login --device-auth`, file credential store | `auth.json` |
| `grok` | `grok login --device-auth` | `auth.json` |

Each runs with its home (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GROK_HOME`)
pointed at a fresh directory, so your everyday CLI login does not change.
`--move-to` streams the finished directory over ssh into `DIR` (default
`~/.local/share/ai-quotas/logins` on that host) and deletes the local copy.

When a vendor rejects the refresh token (`invalid_grant`), the sampler writes
an `unavailable` row whose reason ends in the fix, for example
``needs re-login: `ai-quotas login claude --name work --move-to me@server:…` ``.
`ai-quotas logins` shows the same command, the page's copy button copies it,
and Healthchecks marks that login's check failed. Running it again replaces
the credentials inside the same login.

## Run under anything

| Scheduler | Command |
|---|---|
| Docker | `deploy/docker/compose.example.yaml` (one container per login) |
| macOS launchd | `make install-samplers` / `make uninstall-samplers` (`make dry-run-samplers` prints the plan) |
| systemd | `deploy/systemd/ai-quotas-sampler@.{service,timer}`: `systemctl enable --now ai-quotas-sampler@claude-work.timer` |
| cron, xyOps, anything | `ai-quotas sampler --all --once` every 30 min, or `--login <id> --once` per login |
| a terminal | `ai-quotas sampler --login claude-work --every 30m` |

`--once` exits 0 when every login read ok, else 1, so schedulers can flag a
failure.

## Config

`~/.config/ai-quotas/config.toml` (another path: `AI_QUOTAS_CONFIG`). Every
key is optional. An environment variable always wins over the file. A broken
file shows in `make doctor` and is otherwise ignored, so it never stops a sample.

```toml
[node]
role = "hub"                 # hub (default) | sampler
device = "my-laptop"         # stamped on every row; default: short hostname

[logins]
dir = "~/.local/share/ai-quotas/logins"
move_to = ""                 # on a server: put into "needs re-login" commands

[sampler]
every = "30m"

[spool]
dir = "~/.local/share/ai-quotas/spool"
push = ""                    # rsync destination after each tick (sampler role)

[[ingest]]                   # hub side, repeatable
path = "~/.local/share/ai-quotas/inbox/server"
pull = "me@server:/srv/ai-quotas/spool/"   # optional rsync source

[accounts]                   # which login is the primary series
claude = "me@example.com"

[healthchecks]
slug_prefix = "ai-quotas-"   # per-login check: ai-quotas-claude-work
```

| env | config key |
|---|---|
| `AI_QUOTAS_ROLE` | `[node] role` |
| `AI_QUOTAS_DEVICE` | `[node] device` |
| `AI_QUOTAS_LOGINS_DIR` | `[logins] dir` (default `<data dir>/logins`) |
| `AI_QUOTAS_LOGINS_MOVE_TO` | `[logins] move_to` |
| `AI_QUOTAS_SAMPLER_EVERY` | `[sampler] every` |
| `AI_QUOTAS_SPOOL_DIR` | `[spool] dir` (default `<data dir>/spool`) |
| `AI_QUOTAS_SPOOL_PUSH` | `[spool] push` |
| `AI_QUOTAS_INGEST` | `[[ingest]] path`, `:`-separated, no pull |
| `AI_QUOTAS_ACCOUNT_<PROVIDER>` | `[accounts] <provider>` |
| `AI_QUOTAS_HC_SLUG_PREFIX` | `[healthchecks] slug_prefix` |
| `AI_QUOTAS_HC_PING_KEY` (or `CALMMAGE_HEALTHCHECKS_PING_KEY`) | none: a secret, keep it in the environment |
| `AI_QUOTAS_SSH` | none: the ssh for `pull`, `push` and `--move-to`; default `/usr/bin/ssh`, else `ssh` on PATH |

`make doctor` prints the resolved role, device, directories, ingest sources and
every login's state.

## Safety and isolation

- **Only owned logins refresh.** The refresh code refuses a directory that is
  not directly inside the logins dir, is a symlink, or has no `login.json`
  saying `owned: true` with a matching name. Your everyday CLI logins are only
  read, as before ([CONTRACT rule 4](CONTRACT.md#adapter-rules)).
- **One refresh at a time.** An exclusive file lock per login, so two
  samplers on one login refresh once. Writes are atomic (temp file, fsync,
  rename) with mode 0600.
- **No tokens in output.** Rows, spool files, logs and `ai-quotas logins`
  never contain a token; error reasons are scrubbed of anything token-shaped.
- **Containers.** No vendor CLI inside, stdlib Python only. Each runs as uid
  10001 with a read-only root filesystem, every capability dropped,
  `no-new-privileges` and 128 MB. Each sees only its own login directory and
  the spool.
- **systemd.** `ProtectSystem=strict`, `ProtectHome=read-only`, a dedicated
  user, writes only under `/var/lib/ai-quotas`.
- **Network.** Samplers call only the vendor's token and usage endpoints and,
  when configured, Healthchecks. Codex reset credits are only read, never
  redeemed.

## Healthchecks

With `AI_QUOTAS_HC_PING_KEY` set, every tick pings the check
`<slug_prefix><login>`, for example `ai-quotas-claude-work`. It sends
`/fail` when the login did not read ok, and the check is created on the first
ping. On a sampler-role machine `ai-quotas sample` still pings its usual
sample check, so a dead laptop is noticed too.

## Row fields

Rows from samplers carry `source: "owned"` and `login: "<provider>-<name>"`.
Rows from everyday CLI logins carry `source: "borrowed"`. See
[CONTRACT.md](CONTRACT.md#source-account--device).
