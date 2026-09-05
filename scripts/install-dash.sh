#!/usr/bin/env bash
# Install / uninstall macOS LaunchAgent that keeps `ai-quotas dash` alive.
set -euo pipefail

LABEL="com.calmmage.ai-quotas-dash"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
PORT="${DASH_PORT:-8765}"
INTERVAL="${DASH_INTERVAL:-15}"
DRY=0
UNINSTALL=0

usage() {
  cat <<EOF
Usage: $0 [--dry-run] [--uninstall] [--port N] [--interval SECONDS]

Installs ${LABEL} (KeepAlive) to serve generated plots on 127.0.0.1:PORT.
If ${PLIST_DST} is already a symlink (owner-machine nonix wiring), this
script leaves it alone.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --interval) INTERVAL="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${AI_QUOTAS_DATA_DIR:-$HOME/.local/share/ai-quotas}"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/dash.log"

if [[ "$UNINSTALL" -eq 1 ]]; then
  if [[ -L "$PLIST_DST" ]]; then
    echo "skip uninstall: $PLIST_DST is a symlink (owner wiring)"
    exit 0
  fi
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST_DST"
  echo "uninstalled $LABEL"
  exit 0
fi

if command -v uv >/dev/null 2>&1 && [[ -f "$REPO/pyproject.toml" ]]; then
  BIN0="$(command -v uv)"
  ARG1="--directory"
  ARG2="$REPO"
  ARG3="run"
  ARG4="ai-quotas"
  ARG5="dash"
  ARG6="--port"
  ARG7="$PORT"
  ARG8="--interval"
  ARG9="$INTERVAL"
  DESCR="uv --directory $REPO run ai-quotas dash --port $PORT --interval $INTERVAL"
else
  BIN0="$(command -v python3)"
  ARG1="-m"
  ARG2="ai_quotas"
  ARG3="dash"
  ARG4="--port"
  ARG5="$PORT"
  ARG6="--interval"
  ARG7="$INTERVAL"
  ARG8=""
  ARG9=""
  DESCR="$BIN0 -m ai_quotas dash --port $PORT --interval $INTERVAL"
fi

echo "program: $DESCR"
echo "log: $LOG"
echo "plist: $PLIST_DST"

PLIST_PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
FORWARD_ENV_KEYS=(
  AI_QUOTAS_DATABASE
  AI_QUOTAS_DATA_DIR
  AI_QUOTAS_EXTRA_ADAPTERS
  AI_QUOTAS_SAMPLES
  AI_QUOTAS_AFTER_REGEN
  AI_QUOTAS_TELEGRAM_BOT_TOKEN
  AI_QUOTAS_TELEGRAM_CHAT_ID
  AI_QUOTAS_HC_SAMPLE_URL
  AI_QUOTAS_HC_DASH_URL
  CALMMAGE_HEALTHCHECKS_PING_KEY
  CALMMAGE_HEALTHCHECKS_BASE_URL
  AI_QUOTAS_SAMPLE_HC_SLUG
  AI_QUOTAS_DASH_HC_SLUG
  AI_QUOTAS_HC_INTERVAL
  AI_QUOTAS_READ_DOTENV
)
SECRET_ENV_KEYS="AI_QUOTAS_TELEGRAM_BOT_TOKEN AI_QUOTAS_TELEGRAM_CHAT_ID CALMMAGE_HEALTHCHECKS_PING_KEY"
echo "env (plist EnvironmentVariables):"
echo "  PATH=${PLIST_PATH_VALUE}"
echo "  HOME=${HOME}"
for key in "${FORWARD_ENV_KEYS[@]}"; do
  if [[ -n "${!key:-}" ]]; then
    if [[ " $SECRET_ENV_KEYS " == *" $key "* ]]; then
      echo "  ${key}=set"
    else
      echo "  ${key}=${!key}"
    fi
  fi
done

if [[ "$DRY" -eq 1 ]]; then
  if [[ -L "$PLIST_DST" ]]; then
    echo "would skip install: $PLIST_DST is a symlink (owner wiring)"
  fi
  echo "(dry-run — not installing)"
  exit 0
fi

if [[ -L "$PLIST_DST" ]]; then
  echo "skip install: $PLIST_DST is a symlink (owner wiring). Not replacing."
  exit 0
fi

{
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${BIN0}</string>
EOF
  for a in "$ARG1" "$ARG2" "$ARG3" "$ARG4" "$ARG5" "$ARG6" "$ARG7" "$ARG8" "$ARG9"; do
    if [[ -n "$a" ]]; then
      echo "    <string>${a}</string>"
    fi
  done
  cat <<EOF
  </array>
  <key>WorkingDirectory</key>
  <string>${REPO}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>15</integer>
  <key>StandardOutPath</key>
  <string>${LOG}</string>
  <key>StandardErrorPath</key>
  <string>${LOG}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${PLIST_PATH_VALUE}</string>
    <key>HOME</key>
    <string>${HOME}</string>
EOF
  for key in "${FORWARD_ENV_KEYS[@]}"; do
    if [[ -n "${!key:-}" ]]; then
      echo "    <key>${key}</key>"
      echo "    <string>${!key}</string>"
    fi
  done
  cat <<EOF
  </dict>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
EOF
} > "$PLIST_DST"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
echo "installed $LABEL → $PLIST_DST"
echo "open http://127.0.0.1:${PORT}/"
echo "tail -f $LOG"
