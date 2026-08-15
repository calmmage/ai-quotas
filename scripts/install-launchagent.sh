#!/usr/bin/env bash
# Install / uninstall macOS LaunchAgent that runs `ai-quotas sample` on an interval.
# Replaces the old prototypes/poc/quota-providers/watchdog.py agent.
set -euo pipefail

LABEL="com.calmmage.ai-quotas-sample"
PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/automation/com.calmmage.ai-quotas-sample.plist.template"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
INTERVAL=1800
DRY=0
UNINSTALL=0

usage() {
  cat <<EOF
Usage: $0 [--dry-run] [--uninstall] [--interval SECONDS]

Installs ${LABEL} to run ai-quotas sample every INTERVAL seconds (default 1800).
Resolves the ai-quotas entry via: uv run (if uv + project) or python -m ai_quotas.collector
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
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/sample-agent.log"

# Prefer project-local uv run so the correct package is used.
if command -v uv >/dev/null 2>&1 && [[ -f "$REPO/pyproject.toml" ]]; then
  PROG_ARGS=()
  UV_BIN="$(command -v uv)"
  # LaunchAgent needs absolute paths; run: uv --directory REPO run ai-quotas sample
  BIN0="$UV_BIN"
  ARG1="--directory"
  ARG2="$REPO"
  ARG3="run"
  ARG4="ai-quotas"
  ARG5="sample"
  DESCR="uv --directory $REPO run ai-quotas sample"
elif command -v ai-quotas >/dev/null 2>&1; then
  BIN0="$(command -v ai-quotas)"
  ARG1="sample"
  ARG2=""
  ARG3=""
  ARG4=""
  ARG5=""
  DESCR="$BIN0 sample"
else
  BIN0="$(command -v python3)"
  ARG1="-m"
  ARG2="ai_quotas.collector"
  ARG3=""
  ARG4=""
  ARG5=""
  DESCR="$BIN0 -m ai_quotas.collector"
fi

if [[ "$UNINSTALL" -eq 1 ]]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST_DST"
  echo "uninstalled $LABEL"
  exit 0
fi

echo "program: $DESCR"
echo "interval: ${INTERVAL}s"
echo "log: $LOG"
echo "plist: $PLIST_DST"

# Optional owner env — bake into the plist only when set at install time.
# Do not invent defaults; omit unset keys (portable).
FORWARD_ENV_KEYS=(AI_QUOTAS_SAMPLES AI_QUOTAS_DATA_DIR AI_QUOTAS_EXTRA_ADAPTERS)
PLIST_PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
echo "env (plist EnvironmentVariables):"
echo "  PATH=${PLIST_PATH_VALUE}"
echo "  HOME=${HOME}"
for key in "${FORWARD_ENV_KEYS[@]}"; do
  if [[ -n "${!key:-}" ]]; then
    echo "  ${key}=${!key}"
  fi
done

if [[ "$DRY" -eq 1 ]]; then
  echo "(dry-run — not installing)"
  exit 0
fi

# Build plist
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
  for a in "$ARG1" "$ARG2" "$ARG3" "$ARG4" "$ARG5"; do
    if [[ -n "$a" ]]; then
      echo "    <string>${a}</string>"
    fi
  done
  cat <<EOF
  </array>
  <key>WorkingDirectory</key>
  <string>${REPO}</string>
  <key>StartInterval</key>
  <integer>${INTERVAL}</integer>
  <key>RunAtLoad</key>
  <true/>
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
echo "tail -f $LOG"
