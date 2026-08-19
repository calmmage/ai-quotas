#!/usr/bin/env bash
# Weekly agentic_step burn check.
# Exit 0 = ok · 1 = substantial (telegram sent if creds resolve) · 2 = check error.
#
# Telegram path (same as daily-plots-bot / calmlib service bot):
#   resolve CALMMAGE_SERVICE_BOT_TOKEN_PROD + CALMMAGE_SERVICE_BOT_CHAT_ID
#   via ~/work/calmmage venv, then Bot API sendMessage (stdlib urllib).
# Healthchecks is a dead-man's switch (alerts when THIS job does not run).
# Optional: set CALMMAGE_HEALTHCHECKS_PING_KEY and the check slug
# AI_QUOTAS_AGENTIC_STEP_HC_SLUG to ping success/fail after the verdict.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CALMMAGE="${CALMMAGE:-$HOME/work/calmmage}"
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
  local text="$1"
  if [[ ! -d "$CALMMAGE" ]]; then
    echo "agentic-step-alert: no calmmage checkout at $CALMMAGE — cannot resolve bot token" >&2
    return 2
  fi
  local token chat
  token="$(
    cd "$CALMMAGE" && uv run python -c "
from calmlib.utils import find_calmmage_env_key
print(find_calmmage_env_key('$TOKEN_KEY') or '')
" 2>/dev/null | tail -1
  )"
  chat="$(
    cd "$CALMMAGE" && uv run python -c "
from calmlib.utils import find_calmmage_env_key
print(find_calmmage_env_key('$CHAT_KEY') or find_calmmage_env_key('$CHAT_KEY_FALLBACK') or '')
" 2>/dev/null | tail -1
  )"
  if [[ -z "$token" || -z "$chat" ]]; then
    echo "agentic-step-alert: missing $TOKEN_KEY or chat id ($CHAT_KEY / $CHAT_KEY_FALLBACK)" >&2
    return 2
  fi
  AGENTIC_STEP_ALERT_TOKEN="$token" AGENTIC_STEP_ALERT_CHAT="$chat" \
  AGENTIC_STEP_ALERT_TEXT="$text" \
  python3 - <<'PY'
import json, os, urllib.error, urllib.parse, urllib.request
token = os.environ["AGENTIC_STEP_ALERT_TOKEN"]
chat = os.environ["AGENTIC_STEP_ALERT_CHAT"]
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
    print(f"agentic-step-alert: telegram {exc}", file=__import__("sys").stderr)
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
PY
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
