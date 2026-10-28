#!/usr/bin/env bash
# One browser sign-in, then sample the new login and refresh the live dashboard.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
account="${1:?Pass the Google account email}"
adapter="${AI_QUOTAS_EXTRA_ADAPTERS:-$HOME/calm/private/ai-quotas-extra}/agy.py"
python_bin="$repo/.venv/bin/python"
if [[ ! -f "$adapter" || ! -x "$python_bin" ]]; then
  echo "The private Gemini adapter and project Python must be installed first." >&2
  exit 1
fi
"$python_bin" "$adapter" login "$account"
export AI_QUOTAS_EXTRA_ADAPTERS="$(dirname "$adapter")"
cd "$repo"
"$python_bin" -m ai_quotas sample --provider agy --no-alert
make deploy
