#!/usr/bin/env bash
# Weekly agentic_step burn check.
# Exit 0 = ok · 1 = substantial (telegram sent if creds resolve) · 2 = check error.
#
# Telegram path (same as daily-plots-bot / calmlib service bot):
#   resolve CALMMAGE_SERVICE_BOT_TOKEN_PROD + CALMMAGE_SERVICE_BOT_CHAT_ID
#   via Engine Keys, then Bot API sendMessage (stdlib urllib).
# Healthchecks is a dead-man's switch (alerts when THIS job does not run).
# Optional: set CALMMAGE_HEALTHCHECKS_PING_KEY and the check slug
# AI_QUOTAS_AGENTIC_STEP_HC_SLUG to ping success/fail after the verdict.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN_KEY="${AGENTIC_STEP_ALERT_TOKEN_KEY:-CALMMAGE_SERVICE_BOT_TOKEN_PROD}"
CHAT_KEY="${AGENTIC_STEP_ALERT_CHAT_KEY:-CALMMAGE_SERVICE_BOT_CHAT_ID}"
CHAT_KEY_FALLBACK="CALMMAGE_TELEGRAM_MY_CHAT_ID"
SINCE="${AGENTIC_STEP_CHECK_SINCE:-7d}"
LOG_DIR="${AI_QUOTAS_DATA_DIR:-$HOME/.local/share/ai-quotas}"
mkdir -p "$LOG_DIR"

if command -v uv >/dev/null 2>&1 && [[ -f "$REPO/pyproject.toml" ]]; then
  AQ=(uv --directory "$REPO" run ai-quotas)
else
  AQ=(python3 -m ai_quotas)
fi

# Fresh harvest so the join is not a week stale. Fail-open.
"${AQ[@]}" spend --max-seconds 90 --json >/dev/null || true

verdict="$("${AQ[@]}" agentic-step-check --since "$SINCE")"
rc=$?
printf '%s\n' "$verdict"

send_telegram() {
  AGENTIC_STEP_ALERT_TEXT="$1" \
  "$HOME/calmmage/projects/meta/engine/.venv/bin/python" - "$REPO" "$TOKEN_KEY" "$CHAT_KEY" "$CHAT_KEY_FALLBACK" <<'PYKEYS'
import json, os, sys, runpy, urllib.error, urllib.parse, urllib.request
from pathlib import Path
bridge = runpy.run_path(str(Path(sys.argv[1]) / "ai_quotas/keys_bridge.py"))
resolve = bridge["resolve"]
token = resolve(sys.argv[2])
chat = resolve(sys.argv[3]) or resolve(sys.argv[4])
if not token or not chat:
    sys.exit("agentic-step-alert: credential unavailable")
text = os.environ["AGENTIC_STEP_ALERT_TEXT"]
body = urllib.parse.urlencode(
    {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}
).encode()
req = urllib.request.Request(
    f"https://api.telegram.org/bot{token}/sendMessage",
    data=body,
    method="POST",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
except urllib.error.HTTPError as exc:
    print(f"agentic-step-alert: telegram HTTP {exc.code}", file=__import__("sys").stderr)
    raise SystemExit(2)
except urllib.error.URLError as exc:
    print("agentic-step-alert: Telegram request failed", file=sys.stderr)
    raise SystemExit(2)
ok = False
try:
    ok = bool(json.loads(raw).get("ok"))
except json.JSONDecodeError:
    pass
if not ok:
    print("agentic-step-alert: telegram send not ok", file=__import__("sys").stderr)
    raise SystemExit(2)
print("agentic-step-alert: telegram sent")
PYKEYS
}

hc_ping() {
  local suffix="$1"
  local key="${CALMMAGE_HEALTHCHECKS_PING_KEY:-}"
  local base="${CALMMAGE_HEALTHCHECKS_BASE_URL:-https://healthchecks.calmmage.com}"
  local slug="${AI_QUOTAS_AGENTIC_STEP_HC_SLUG:-}"
  if [[ -z "$key" || -z "$slug" ]]; then
    return 0
  fi
  curl -fsS --max-time 15 "${base%/}/ping/${key}/${slug}${suffix}" >/dev/null || \
    echo "agentic-step-alert: healthchecks ping failed (slug=$slug)" >&2
}

if [[ "$rc" -eq 1 ]]; then
  tokens="$(printf '%s' "$verdict" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('total_tokens'))")"
  usd="$(printf '%s' "$verdict" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('cost_usd'))")"
  reasons="$(printf '%s' "$verdict" | python3 -c "import json,sys; d=json.load(sys.stdin); print('; '.join(d.get('reasons') or []))")"
  msg="ai-quotas agentic_step burn is substantial (${SINCE})
tokens=${tokens}  usd=${usd}
${reasons}

ai-quotas spend --agentic-step --since ${SINCE}"
  send_telegram "$msg" || echo "agentic-step-alert: telegram delivery failed" >&2
  hc_ping "/fail"
else
  hc_ping ""
fi

exit "$rc"
