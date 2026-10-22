#!/usr/bin/env bash
# Install / uninstall one macOS LaunchAgent per owned login (docs/SAMPLERS.md):
#   com.calmmage.ai-quotas-sampler.<login>  →  ai-quotas sampler --login <login> --once
# StartInterval + --once (not a long-lived loop): launchd runs a missed tick
# after the Mac wakes, and a crash never needs a restart policy.
set -euo pipefail

PREFIX="com.calmmage.ai-quotas-sampler"
AGENTS="${HOME}/Library/LaunchAgents"
INTERVAL=1800
DRY=0
UNINSTALL=0

usage() {
  cat <<EOF
Usage: $0 [--dry-run] [--uninstall] [--interval SECONDS]

One LaunchAgent per login listed by \`ai-quotas logins\` (default every 1800s).
--uninstall removes every ${PREFIX}.* agent, including logins since deleted.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --interval) INTERVAL="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${AI_QUOTAS_DATA_DIR:-$HOME/.local/share/ai-quotas}"

if command -v uv >/dev/null 2>&1 && [[ -f "$REPO/pyproject.toml" ]]; then
  RUN=("$(command -v uv)" --directory "$REPO" run ai-quotas)
elif command -v ai-quotas >/dev/null 2>&1; then
  RUN=("$(command -v ai-quotas)")
else
  echo "ai-quotas not found: run make setup first" >&2
  exit 1
fi

remove_agent() {
  local plist="$1" label
  label="$(basename "$plist" .plist)"
  if [[ -L "$plist" ]]; then
    echo "skip: $plist is a symlink (owner wiring)"
    return
  fi
  if [[ "$DRY" -eq 1 ]]; then
    echo "would remove $label"
    return
  fi
  launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
  rm -f "$plist"
  echo "removed $label"
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  shopt -s nullglob
  for plist in "$AGENTS/${PREFIX}."*.plist; do
    remove_agent "$plist"
  done
  exit 0
fi

LOGINS="$("${RUN[@]}" logins --json | python3 -c 'import json, sys; print("\n".join(l["login"] for l in json.load(sys.stdin)["logins"]))')"
if [[ -z "$LOGINS" ]]; then
  echo "no owned logins yet. Make one: make login PROVIDER=claude NAME=work" >&2
  exit 1
fi

# Same env forwarding as install-launchagent.sh: only keys set right now.
FORWARD_ENV_KEYS=(
  AI_QUOTAS_CONFIG
  AI_QUOTAS_ROLE
  AI_QUOTAS_DEVICE
  AI_QUOTAS_DATA_DIR
  AI_QUOTAS_DATABASE
  AI_QUOTAS_LOGINS_DIR
  AI_QUOTAS_SPOOL_DIR
  AI_QUOTAS_SPOOL_PUSH
  AI_QUOTAS_HC_PING_KEY
  CALMMAGE_HEALTHCHECKS_PING_KEY
  CALMMAGE_HEALTHCHECKS_BASE_URL
  AI_QUOTAS_HC_SLUG_PREFIX
  AI_QUOTAS_READ_DOTENV
)
SECRET_ENV_KEYS=" AI_QUOTAS_HC_PING_KEY CALMMAGE_HEALTHCHECKS_PING_KEY "
PLIST_PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

echo "program: ${RUN[*]} sampler --login <login> --once"
echo "interval: ${INTERVAL}s · logs: ${LOG_DIR}/sampler-<login>.log"
for key in "${FORWARD_ENV_KEYS[@]}"; do
  if [[ -n "${!key:-}" ]]; then
    if [[ "$SECRET_ENV_KEYS" == *" $key "* ]]; then echo "  ${key}=set"; else echo "  ${key}=${!key}"; fi
  fi
done

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

wanted=" "
while IFS= read -r login; do
  [[ -n "$login" ]] || continue
  label="${PREFIX}.${login}"
  plist="${AGENTS}/${label}.plist"
  wanted+="${label} "
  if [[ -L "$plist" ]]; then
    echo "skip: $plist is a symlink (owner wiring)"
    continue
  fi
  if [[ "$DRY" -eq 1 ]]; then
    echo "would install ${label}"
    continue
  fi
  mkdir -p "$AGENTS" "$LOG_DIR"
  {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${label}</string>
  <key>ProgramArguments</key>
  <array>
EOF
    for arg in "${RUN[@]}" sampler --login "$login" --once; do
      echo "    <string>$(xml "$arg")</string>"
    done
    cat <<EOF
  </array>
  <key>WorkingDirectory</key>
  <string>$(xml "$REPO")</string>
  <key>StartInterval</key>
  <integer>${INTERVAL}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$(xml "${LOG_DIR}/sampler-${login}.log")</string>
  <key>StandardErrorPath</key>
  <string>$(xml "${LOG_DIR}/sampler-${login}.log")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${PLIST_PATH_VALUE}</string>
    <key>HOME</key>
    <string>$(xml "$HOME")</string>
EOF
    for key in "${FORWARD_ENV_KEYS[@]}"; do
      if [[ -n "${!key:-}" ]]; then
        echo "    <key>${key}</key>"
        echo "    <string>$(xml "${!key}")</string>"
      fi
    done
    cat <<EOF
  </dict>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
EOF
  } > "$plist"
  chmod 600 "$plist"
  launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "installed ${label}"
done <<< "$LOGINS"

# Agents of logins that no longer exist.
shopt -s nullglob
for plist in "$AGENTS/${PREFIX}."*.plist; do
  label="$(basename "$plist" .plist)"
  [[ "$wanted" == *" ${label} "* ]] || remove_agent "$plist"
done
