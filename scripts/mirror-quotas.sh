#!/usr/bin/env bash
# Mirror this dashboard only; nginx and all other applications keep their owners.
set -euo pipefail
QUOTA_REMOTE="${AI_QUOTAS_MIRROR_HOST:-root@100.78.4.58}"
QUOTA_PLOTS="${PLOTS:-${AI_QUOTAS_DATA_DIR:-$HOME/.local/share/ai-quotas}/plots}"
QUOTA_SSH="${SSH:-/usr/bin/ssh}"
"$QUOTA_SSH" -o BatchMode=yes -o ConnectTimeout=10 "$QUOTA_REMOTE" 'mkdir -p /opt/calmmage/quotas'
rsync -az --delete -e "$QUOTA_SSH -o BatchMode=yes -o ConnectTimeout=10" "$QUOTA_PLOTS/" "$QUOTA_REMOTE:/opt/calmmage/quotas/"
"$QUOTA_SSH" -o BatchMode=yes "$QUOTA_REMOTE" 'ln -sfn live.html /opt/calmmage/quotas/index.html; curl --fail --silent --show-error -o /dev/null http://127.0.0.1:8792/10_uplot/index.html'
printf 'Mirrored quota dashboard to %s\n' "$QUOTA_REMOTE"
