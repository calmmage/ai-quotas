#!/usr/bin/env bash
# Install / uninstall weekly LaunchAgent: agentic_step burn check + telegram on exit 1.
set -euo pipefail

LABEL="com.calmmage.ai-quotas-agentic-step-check"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DRY=0
UNINSTALL=0

usage() {
  cat <<EOF
Usage: $0 [--dry-run] [--uninstall]

Installs ${LABEL} to run scripts/agentic-step-alert.sh every Monday 09:00.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO/scripts/agentic-step-alert.sh"
LOG_DIR="${AI_QUOTAS_DATA_DIR:-$HOME/.local/share/ai-quotas}"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/agentic-step-alert.log"

if [[ "$UNINSTALL" -eq 1 ]]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST_DST"
  echo "uninstalled $LABEL"
  exit 0
fi

if [[ ! -x "$SCRIPT" ]]; then
  chmod +x "$SCRIPT"
fi

echo "program: $SCRIPT"
echo "when: Monday 09:00"
echo "log: $LOG"
echo "plist: $PLIST_DST"

if [[ "$DRY" -eq 1 ]]; then
  echo "(dry-run — not installing)"
  exit 0
fi

PLIST_PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
FORWARD_ENV_KEYS=(
  AI_QUOTAS_DATABASE
  AI_QUOTAS_SAMPLES
  AI_QUOTAS_DATA_DIR
  AI_QUOTAS_SPEND
  AGENTIC_STEP_JOBS
  AGENTIC_STEP_CHECK_SINCE
  CALMMAGE_HEALTHCHECKS_PING_KEY
  CALMMAGE_HEALTHCHECKS_BASE_URL
  AI_QUOTAS_AGENTIC_STEP_HC_SLUG
)

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
    <string>/bin/bash</string>
    <string>${SCRIPT}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${REPO}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>1</integer>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
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
