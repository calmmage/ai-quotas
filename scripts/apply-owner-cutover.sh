#!/usr/bin/env bash
# Owner-machine cutover that this agent cannot write (sandbox).
# Copies the private Gemini adapter, retargets leftover donor callers,
# fixes nginx index, archives the old watchdog agent, installs the weekly
# agentic_step check, and restarts dash so http://home/quotas/ picks up plots.
set -euo pipefail

HOME="${HOME:-/Users/petrlavrov}"
EXTRA="$HOME/work/prototypes/ai-quotas-extra"
DONOR="$HOME/work/prototypes/poc/quota-providers/agy.py"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SKILL="$HOME/work/calmmage/skills/workflow/token-quota/SKILL.md"
NGINX="$HOME/work/calmmage/nonix/config/nginx-local-services.conf"
SNAPSHOT="$HOME/work/calmmage/nonix/resources/launch_agents/com.calmmage.quota-snapshot.plist"
ORCH="$HOME/work/projects/platform/dev/unsorted/lib/auto/orchestrator.py"
PIPE="$HOME/work/projects/platform/dev/unsorted/lib/auto/pipeline.py"

echo "repo: $REPO"

# 1. extra/agy.py — copy donor (secret stays out of the public repo)
mkdir -p "$EXTRA"
if [[ -f "$DONOR" ]]; then
  cp "$DONOR" "$EXTRA/agy.py"
  echo "copied donor agy.py -> $EXTRA/agy.py"
  cat > "$EXTRA/README.md" <<'EOF'
# ai-quotas-extra

Private drop-in adapters for `AI_QUOTAS_EXTRA_ADAPTERS`.

Gemini/`agy` lives here. Do not publish this directory. Do not copy it into
the public `ai-quotas` wheel.

Canonical CLI: `~/work/projects/ai-quotas`.
EOF
else
  echo "WARN: donor agy.py missing at $DONOR" >&2
fi

# 2. donor archive banners
python3 - <<'PY'
from pathlib import Path
banner = (
    "SUPERSEDED. Canonical CLI: ~/work/projects/ai-quotas. "
    "This tree is a donor archive. Live Gemini adapter: "
    "~/work/prototypes/ai-quotas-extra/agy.py.\n"
)
files = [
    Path.home() / "work/prototypes/governor/quotas.py",
    Path.home() / "work/prototypes/poc/quota-providers/watchdog.py",
    Path.home() / "work/prototypes/poc/quota-providers/README.md",
]
for p in files:
    if not p.is_file():
        print("skip missing", p)
        continue
    text = p.read_text(encoding="utf-8")
    if "SUPERSEDED. Canonical CLI" in text:
        print("already bannered", p)
        continue
    if p.suffix == ".md":
        p.write_text("> " + banner + "\n" + text, encoding="utf-8")
    else:
        if text.lstrip().startswith('"""'):
            i = text.find('"""')
            j = text.find('"""', i + 3)
            p.write_text(text[: j] + "\n\n" + banner + text[j:], encoding="utf-8")
        else:
            p.write_text('"""' + banner + '"""\n' + text, encoding="utf-8")
    print("bannered", p)
PY

# 3. token-quota skill
if [[ -f "$SKILL" ]]; then
  python3 - "$SKILL" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
text = text.replace(
    "compute burn rate via the quota watchdog",
    "compute burn rate via the ai-quotas CLI",
)
old = (
    "Canonical CLI: `~/work/projects/ai-quotas` (alias `ai-quotas` / `aiq`). "
    "Live samples stay at `~/calmmage/data/automation_logs/quota/samples.jsonl`. "
    "Donor adapters still at `~/work/prototypes/poc/quota-providers`. "
    "Never treat an unreadable provider as 0% used."
)
new = (
    "Canonical CLI: `~/work/projects/ai-quotas` (alias `ai-quotas` / `aiq`). "
    "Live DB: `~/.local/share/ai-quotas/ai-quotas.sqlite3`. "
    "Private extra adapter dir: `~/work/prototypes/ai-quotas-extra`. "
    "Never treat an unreadable provider as 0% used."
)
if old in text:
    text = text.replace(old, new)
else:
    print("WARN: skill canonical-CLI sentence not found verbatim", file=__import__("sys").stderr)
text = text.replace("## Watchdog (burn rate + verdicts)", "## Verdicts (burn rate)")
p.write_text(text, encoding="utf-8")
print("updated", p)
PY
fi

# 4. platform unsorted auto
python3 - <<'PY'
from pathlib import Path

orch = Path.home() / "work/projects/platform/dev/unsorted/lib/auto/orchestrator.py"
pipe = Path.home() / "work/projects/platform/dev/unsorted/lib/auto/pipeline.py"

if orch.is_file():
    t = orch.read_text(encoding="utf-8")
    t = t.replace(
        'QUOTAS_CLI = Path.home() / "work/prototypes/governor/quotas.py"\n\n\n',
        "",
    )
    old = '''    try:
        import subprocess
        out = subprocess.run([str(QUOTAS_CLI), "--json", "--no-refresh"],
                             capture_output=True, text=True, timeout=60).stdout
        worst: dict[str, float] = {}
        for r in json.loads(out):
            p, u, w = r.get("provider"), r.get("used_percent"), r.get("window", "")
            if w in ("overage_credits", "unknown"):  # spend / unreadable rows, not quota
                continue
            if p and isinstance(u, (int, float)):
                worst[p] = max(worst.get(p, 0.0), float(u))
'''
    new = '''    try:
        from ai_quotas.core import load_samples
        worst: dict[str, float] = {}
        for r in load_samples():
            p, u, w = r.get("provider"), r.get("used_percent"), r.get("window", "")
            if w in ("overage_credits", "unknown"):  # spend / unreadable rows, not quota
                continue
            if p and isinstance(u, (int, float)):
                worst[p] = max(worst.get(p, 0.0), float(u))
'''
    if old in t:
        t = t.replace(old, new)
        orch.write_text(t, encoding="utf-8")
        print("updated", orch)
    else:
        print("WARN: orchestrator fallback block not found verbatim")

if pipe.is_file():
    t = pipe.read_text(encoding="utf-8")
    old = '''    cli = Path.home() / "work/prototypes/governor/quotas.py"
    try:
        out = subprocess.run([str(cli), "--json", "--no-refresh"],
                             capture_output=True, text=True, timeout=60)
        worst = 0.0
        for r in json.loads(out.stdout):
            if r.get("provider") != "claude":
                continue
            if r.get("window") in ("overage_credits", "unknown"):
                continue
            u = r.get("used_percent")
            if isinstance(u, (int, float)):
                worst = max(worst, u)
        print(f"quota gate: claude worst window {worst:.0f}% used")
        return worst < 90
    except Exception as e:
        print(f"quota check failed ({e}) — refusing to launch", file=sys.stderr)
        return False
'''
    new = '''    try:
        from ai_quotas.core import load_samples
        worst = 0.0
        for r in load_samples():
            if r.get("provider") != "claude":
                continue
            if r.get("window") in ("overage_credits", "unknown"):
                continue
            u = r.get("used_percent")
            if isinstance(u, (int, float)):
                worst = max(worst, u)
        print(f"quota gate: claude worst window {worst:.0f}% used")
        return worst < 90
    except Exception as e:
        print(f"quota check failed ({e}) — refusing to launch", file=sys.stderr)
        return False
'''
    if old in t:
        t = t.replace(old, new)
        pipe.write_text(t, encoding="utf-8")
        print("updated", pipe)
    else:
        print("WARN: pipeline _quota_ok block not found verbatim")
PY

# 5. nginx + old snapshot agent
if [[ -f "$NGINX" ]]; then
  python3 - "$NGINX" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
t2 = t.replace("index live.html 00_INDEX.html;", "index live.html index.html 00_INDEX.html;")
if t2 != t:
    p.write_text(t2, encoding="utf-8")
    print("nginx index.html added")
else:
    print("nginx already has index.html or pattern missing")
PY
fi
if [[ -f "$SNAPSHOT" ]]; then
  mv "$SNAPSHOT" "${SNAPSHOT}.deprecated"
  echo "renamed quota-snapshot plist to .deprecated"
fi

# 6. weekly agentic_step check + restart dash
bash "$REPO/scripts/install-agentic-step-alert.sh" || echo "WARN: agentic-step install failed"
launchctl kickstart -k "gui/$(id -u)/com.calmmage.ai-quotas-dash" || echo "WARN: dash kickstart failed"

echo "owner cutover done"
