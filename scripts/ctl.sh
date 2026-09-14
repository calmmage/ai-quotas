#!/usr/bin/env bash
# Day-to-day control of the installed macOS LaunchAgents.
# make start | stop | restart | deploy | status
set -euo pipefail

DASH_LABEL="com.calmmage.ai-quotas-dash"
SAMPLE_LABEL="com.calmmage.ai-quotas-sample"
DOMAIN="gui/$(id -u)"
PLIST_DIR="${HOME}/Library/LaunchAgents"
LAUNCHPAD_MIRROR="${HOME}/calmmage/projects/meta/launchpad/deploy/mirror-home-quotas.sh"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DRY=0

usage() {
  cat <<EOF
Usage: $0 start|stop|restart|deploy|status [--dry-run]

  start     load dash KeepAlive (no-op if already running)
  stop      unload dash
  restart   kill + start dash so it loads this checkout
  deploy    regen plots + rsync to cloud nginx (not a git push)
  status    loaded? pid? url?

Foreground one-off server: make dash
First-time install:         make install-automation
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    start|stop|restart|deploy|status) CMD="$1"; shift ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done
CMD="${CMD:-}"
if [[ -z "$CMD" ]]; then
  usage
  exit 2
fi

run() {
  if [[ "$DRY" -eq 1 ]]; then
    printf 'dry-run:'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

is_darwin() {
  [[ "$(uname -s)" == "Darwin" ]]
}

plist_path() {
  echo "${PLIST_DIR}/${1}.plist"
}

is_loaded() {
  launchctl print "${DOMAIN}/${1}" >/dev/null 2>&1
}

dash_plist() {
  plist_path "$DASH_LABEL"
}

dash_port() {
  local plist
  plist="$(dash_plist)"
  if [[ -f "$plist" ]] && command -v /usr/libexec/PlistBuddy >/dev/null 2>&1; then
    local args i next
    args="$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments' "$plist" 2>/dev/null || true)"
    next=0
    while IFS= read -r line; do
      line="${line#"${line%%[![:space:]]*}"}"
      if [[ "$next" -eq 1 ]]; then
        echo "$line"
        return 0
      fi
      if [[ "$line" == "--port" ]]; then
        next=1
      fi
    done <<< "$args"
  fi
  echo "${DASH_PORT:-8765}"
}

mirror_cmd() {
  if [[ -n "${AI_QUOTAS_AFTER_REGEN:-}" ]]; then
    echo "${AI_QUOTAS_AFTER_REGEN}"
    return 0
  fi
  local plist
  plist="$(dash_plist)"
  if [[ -f "$plist" ]] && command -v /usr/libexec/PlistBuddy >/dev/null 2>&1; then
    local baked
    baked="$(/usr/libexec/PlistBuddy -c 'Print :EnvironmentVariables:AI_QUOTAS_AFTER_REGEN' "$plist" 2>/dev/null || true)"
    if [[ -n "$baked" ]]; then
      echo "$baked"
      return 0
    fi
  fi
  if [[ -f "$LAUNCHPAD_MIRROR" ]]; then
    echo "/bin/bash ${LAUNCHPAD_MIRROR}"
    return 0
  fi
  return 1
}

need_macos() {
  if is_darwin; then
    return 0
  fi
  echo "start/stop/restart/deploy need macOS LaunchAgents. Use: make dash" >&2
  exit 1
}

need_plist() {
  local plist
  plist="$(dash_plist)"
  if [[ -f "$plist" ]]; then
    return 0
  fi
  echo "no ${plist} — install once with: make install-automation" >&2
  exit 1
}

cmd_start() {
  need_macos
  need_plist
  if is_loaded "$DASH_LABEL"; then
    echo "start ${DASH_LABEL} (already loaded)"
    run launchctl kickstart "${DOMAIN}/${DASH_LABEL}" || true
  else
    echo "start ${DASH_LABEL}"
    run launchctl bootstrap "${DOMAIN}" "$(dash_plist)"
  fi
  echo "open http://127.0.0.1:$(dash_port)/"
}

cmd_stop() {
  need_macos
  if is_loaded "$DASH_LABEL"; then
    echo "stop ${DASH_LABEL}"
    run launchctl bootout "${DOMAIN}/${DASH_LABEL}"
  else
    echo "stop ${DASH_LABEL} (not loaded)"
  fi
}

cmd_restart() {
  need_macos
  need_plist
  local rc=0
  if is_loaded "$DASH_LABEL"; then
    echo "restart ${DASH_LABEL}"
    run launchctl kickstart -k "${DOMAIN}/${DASH_LABEL}" || rc=$?
  else
    echo "restart ${DASH_LABEL} (was not loaded — starting)"
    run launchctl bootstrap "${DOMAIN}" "$(dash_plist)" || rc=$?
  fi
  echo "open http://127.0.0.1:$(dash_port)/"
  return "$rc"
}

cmd_status() {
  local port pid state
  port="$(dash_port)"
  echo "plist  $(dash_plist)"
  if is_darwin && is_loaded "$DASH_LABEL"; then
    pid="$(launchctl print "${DOMAIN}/${DASH_LABEL}" 2>/dev/null | awk '/^[ \t]*pid = /{print $NF; exit}')"
    state="$(launchctl print "${DOMAIN}/${DASH_LABEL}" 2>/dev/null | awk '/job state =/{print $NF; exit}')"
    echo "dash   loaded  pid=${pid:-?}  state=${state:-?}"
  else
    echo "dash   not loaded"
  fi
  if is_darwin && is_loaded "$SAMPLE_LABEL"; then
    echo "sample loaded"
  else
    echo "sample not running (interval job is fine)"
  fi
  echo "local  http://127.0.0.1:${port}/"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS -o /dev/null --connect-timeout 1 "http://127.0.0.1:${port}/" 2>/dev/null; then
      echo "probe  127.0.0.1:${port} ok"
    else
      echo "probe  127.0.0.1:${port} down"
    fi
    if curl -fsS -o /dev/null --connect-timeout 1 "http://home/quotas/" 2>/dev/null; then
      echo "probe  http://home/quotas/ ok"
    fi
  fi
}

run_mirror() {
  local cmd
  if ! cmd="$(mirror_cmd)"; then
    echo "cloud  skip (no AI_QUOTAS_AFTER_REGEN / launchpad mirror script)"
    return 0
  fi
  echo "cloud  ${cmd}"
  if [[ "$DRY" -eq 1 ]]; then
    echo "dry-run: PLOTS=${AI_QUOTAS_DEPLOY_PLOTS:-} ${cmd}"
    return 0
  fi
  # AFTER_REGEN is stored as a shell command string ("/bin/bash /path/script.sh").
  # PLOTS overrides the mirror source when we generated into a temp dir.
  if [[ -n "${AI_QUOTAS_DEPLOY_PLOTS:-}" ]]; then
    PLOTS="${AI_QUOTAS_DEPLOY_PLOTS}" bash -lc "$cmd"
  else
    bash -lc "$cmd"
  fi
}

kick_sample() {
  if ! is_darwin; then
    return 0
  fi
  if is_loaded "$SAMPLE_LABEL" || [[ -f "$(plist_path "$SAMPLE_LABEL")" ]]; then
    echo "sample ${SAMPLE_LABEL}"
    run launchctl kickstart -k "${DOMAIN}/${SAMPLE_LABEL}" || run launchctl kickstart "${DOMAIN}/${SAMPLE_LABEL}" || true
  else
    echo "sample skip (not installed)"
  fi
}

plist_env() {
  local plist
  plist="$(dash_plist)"
  if [[ -f "$plist" ]] && command -v /usr/libexec/PlistBuddy >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:${1}" "$plist" 2>/dev/null || true
  fi
}

load_dash_env() {
  local key val
  for key in AI_QUOTAS_EXTRA_ADAPTERS AI_QUOTAS_READ_DOTENV AI_QUOTAS_DATA_DIR AI_QUOTAS_DATABASE AI_QUOTAS_AFTER_REGEN; do
    if [[ -z "${!key:-}" ]]; then
      val="$(plist_env "$key")"
      if [[ -n "$val" ]]; then
        export "${key}=${val}"
      fi
    fi
  done
}

wait_serving() {
  local port url i
  port="$(dash_port)"
  url="http://127.0.0.1:${port}/"
  for i in $(seq 1 "${1:-20}"); do
    if curl -fsS -o /dev/null --connect-timeout 1 "$url" 2>/dev/null; then
      echo "probe  ${url} ok"
      return 0
    fi
    sleep 1
  done
  echo "probe  ${url} down"
  return 1
}

generate_plots_now() {
  load_dash_env
  local py="$REPO/.venv/bin/python"
  if [[ ! -x "$py" ]]; then
    py="$(command -v python3)"
  fi
  local data="${AI_QUOTAS_DATA_DIR:-$HOME/.local/share/ai-quotas}"
  local db="${AI_QUOTAS_DATABASE:-$data/ai-quotas.sqlite3}"
  local out="$data/plots"
  echo "plot   ${py} -m ai_quotas plot --out ${out}"
  echo "cloud  is rsync of that dir to hetzner /opt/calmmage/quotas (Coolify nginx)."
  echo "       git push does not update https://home.tail845ace.ts.net/quotas/"
  if [[ "$DRY" -eq 1 ]]; then
    echo "dry-run: ${py} -m ai_quotas plot --out ${out}"
    return 0
  fi
  stamp_live() {
    local dir="$1"
    (cd "$REPO" && "$py" -c "from pathlib import Path; from ai_quotas.plots.dash import write_live_page; write_live_page(Path(r'''${dir}'''), interval=30)")
  }
  if (cd "$REPO" && "$py" -m ai_quotas plot --out "$out"); then
    stamp_live "$out" || true
    return 0
  fi
  local tmp
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/ai-quotas-plot.XXXXXX")"
  echo "plot   live paths not writable here — plotting from a copy"
  cp "$db" "$tmp/ai-quotas.sqlite3"
  [[ -f "${db}-wal" ]] && cp "${db}-wal" "$tmp/ai-quotas.sqlite3-wal"
  [[ -f "${db}-shm" ]] && cp "${db}-shm" "$tmp/ai-quotas.sqlite3-shm"
  mkdir -p "$tmp/plots"
  local rc=0
  (
    export AI_QUOTAS_DATABASE="$tmp/ai-quotas.sqlite3"
    cd "$REPO"
    "$py" -m ai_quotas plot --out "$tmp/plots"
  ) || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    stamp_live "$tmp/plots" || true
    export AI_QUOTAS_DEPLOY_PLOTS="$tmp/plots"
    # Best-effort sync into the live dir (nginx http://home/quotas/).
    rsync -a "$tmp/plots/" "$out/" 2>/dev/null || true
  fi
  # tmp plots kept until mirror runs; caller should unset after.
  AI_QUOTAS_DEPLOY_TMP="$tmp"
  export AI_QUOTAS_DEPLOY_TMP
  return "$rc"
}

cmd_deploy() {
  echo "deploy = regen HTML plots + rsync to cloud nginx. Not git. Not Coolify rebuild."
  cmd_restart || true
  if [[ "$DRY" -eq 0 ]]; then
    wait_serving 15 || true
  fi
  generate_plots_now
  kick_sample || true
  run_mirror
  if [[ "$DRY" -eq 0 ]]; then
    wait_serving 10 || true
  fi
  cmd_status
  if [[ -n "${AI_QUOTAS_DEPLOY_TMP:-}" ]]; then
    rm -rf "${AI_QUOTAS_DEPLOY_TMP}"
    unset AI_QUOTAS_DEPLOY_TMP AI_QUOTAS_DEPLOY_PLOTS
  fi
}

case "$CMD" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  deploy) cmd_deploy ;;
  status) cmd_status ;;
  *) usage; exit 2 ;;
esac
