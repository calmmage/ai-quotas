#!/usr/bin/env bash
# Ship branch rework-1019 to the live ai-quotas dashboard.
#
#   bash /Users/petrlavrov/calm/.ai-worktrees/p-ai-quotas/rework-1019/scripts/deploy-rework-1019.sh --dry-run
#   bash /Users/petrlavrov/calm/.ai-worktrees/p-ai-quotas/rework-1019/scripts/deploy-rework-1019.sh
#
# 1. refuses if the main checkout has changes other than the known ones
# 2. merges rework-1019 into main (fast-forward when possible), runs the tests
# 3. restarts the dash LaunchAgent (scripts/ctl.sh restart)
# 4. waits until 127.0.0.1:8876 and http://home/quotas/ serve the new build:
#    panels.json "shell_version" == SHELL_VERSION of the merged code
#
# Nothing is pushed. --dry-run changes nothing: it prints every step and the
# marker it would wait for.
#
# Overrides (for testing against a scratch clone): AIQ_MAIN, AIQ_BRANCH,
# AIQ_RESTART (command run instead of ctl.sh restart), AIQ_URLS (space
# separated panels.json URLs), AIQ_WAIT (seconds per URL), AIQ_SKIP_TESTS=1.
set -euo pipefail

MAIN="${AIQ_MAIN:-/Users/petrlavrov/calm/projects/p-ai-quotas}"
BRANCH="${AIQ_BRANCH:-rework-1019}"
URLS="${AIQ_URLS:-http://127.0.0.1:8876/panels.json http://home/quotas/panels.json}"
WAIT="${AIQ_WAIT:-300}"
# Uncommitted paths another session owns, plus this rework's status doc.
ALLOWED=(" M scripts/ctl.sh" "?? docs/walkthroughs/" "?? docs/rework-status-2026-10-19.md")

DRY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY=1 ;;
    -h|--help) sed -n 2,19p "$0"; exit 0 ;;
    *) echo "unknown argument: $arg (use --dry-run)" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
die() { printf 'REFUSED: %s\n' "$*" >&2; exit 1; }
git_main() { git -C "$MAIN" "$@"; }

[[ $DRY -eq 1 ]] && say "dry run: nothing will change"
say "main checkout  $MAIN"

# --- 1. preflight ---------------------------------------------------------
git_main rev-parse --git-dir >/dev/null 2>&1 || die "$MAIN is not a git checkout"
current="$(git_main symbolic-ref --short HEAD 2>/dev/null || echo detached)"
[[ "$current" == "main" ]] || die "$MAIN is on '$current', not main"
git_main rev-parse --verify -q "refs/heads/$BRANCH" >/dev/null || die "branch $BRANCH does not exist"
[[ -e "$(git_main rev-parse --git-path MERGE_HEAD)" ]] && die "a merge is already in progress in $MAIN"

unexpected=()
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  ok=0
  for allowed in "${ALLOWED[@]}"; do [[ "$line" == "$allowed" ]] && ok=1; done
  [[ $ok -eq 1 ]] || unexpected+=("$line")
done < <(git_main status --porcelain --untracked-files=normal)
if [[ ${#unexpected[@]} -gt 0 ]]; then
  printf 'REFUSED: %s has uncommitted changes this deploy does not expect:\n' "$MAIN" >&2
  printf '  %s\n' "${unexpected[@]}" >&2
  printf 'Allowed: %s\n' "$(printf "'%s' " "${ALLOWED[@]}")" >&2
  printf 'Commit or move them, then run again.\n' >&2
  exit 1
fi
say "clean enough   only known changes in main"

old="$(git_main rev-parse --short HEAD)"
new="$(git_main rev-parse --short "$BRANCH")"
if git_main merge-base --is-ancestor "$BRANCH" HEAD; then
  say "merge          $BRANCH ($new) is already in main ($old)"
  mode=none
elif git_main merge-base --is-ancestor HEAD "$BRANCH"; then
  say "merge          fast-forward main $old -> $new ($(git_main rev-list --count HEAD.."$BRANCH") commits)"
  mode=ff
else
  say "merge          main moved since $BRANCH branched: merge commit (aborts on conflict)"
  mode=merge
fi

shell_version() {  # $1 = directory holding ai_quotas/
  (cd "$1" && "$MAIN/.venv/bin/python" -c \
    'import ai_quotas, pathlib, sys
from ai_quotas.plots.generate import SHELL_VERSION
assert pathlib.Path(ai_quotas.__file__).resolve().is_relative_to(pathlib.Path.cwd().resolve()), ai_quotas.__file__
print(SHELL_VERSION)')
}

marker_at() {  # $1 = panels.json URL; prints shell_version or nothing
  curl -fsS --connect-timeout 3 --max-time 10 "$1" 2>/dev/null \
    | "$MAIN/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("shell_version",""))' 2>/dev/null || true
}

# --- dry run: report and stop ---------------------------------------------
if [[ $DRY -eq 1 ]]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  git_main archive "$BRANCH" ai_quotas | tar -x -C "$tmp"
  expect="$(shell_version "$tmp")"
  say "marker         new build = $expect"
  for url in $URLS; do
    say "now            $url -> $(marker_at "$url" | grep . || echo unreachable)"
  done
  if [[ $mode == merge ]]; then
    if git_main merge-tree --write-tree HEAD "$BRANCH" >/dev/null 2>&1; then
      say "would merge    cleanly"
    else
      say "would merge    WITH CONFLICTS: the real run will abort and change nothing"
    fi
  fi
  [[ $mode == ff ]] && say "would run      git -C $MAIN merge --ff-only $BRANCH"
  [[ $mode == merge ]] && say "would run      git -C $MAIN merge --no-ff --no-edit $BRANCH"
  [[ ${AIQ_SKIP_TESTS:-0} == 1 ]] || say "would run      tests in $MAIN"
  if [[ -n "${AIQ_RESTART:-}" ]]; then
    say "would run      $AIQ_RESTART"
  else
    bash "$MAIN/scripts/ctl.sh" restart --dry-run
  fi
  say "would wait     up to ${WAIT}s per URL for shell_version $expect"
  exit 0
fi

# --- 2. merge + tests -----------------------------------------------------
case "$mode" in
  ff) git_main merge --ff-only "$BRANCH" ;;
  merge)
    if ! git_main merge --no-ff --no-edit "$BRANCH"; then
      git_main merge --abort || true
      die "merge of $BRANCH into main conflicted; aborted, main is unchanged at $old"
    fi ;;
esac
say "main           now $(git_main rev-parse --short HEAD) (was $old)"

if [[ ${AIQ_SKIP_TESTS:-0} != 1 ]]; then
  say "tests          running (about 30 s)"
  if ! (cd "$MAIN" && .venv/bin/python -m pytest -o addopts= -q -p no:cacheprovider | tail -3); then
    die "tests failed on merged main. The dash was NOT restarted. Previous main: $old"
  fi
fi

expect="$(shell_version "$MAIN")"
say "marker         waiting for shell_version $expect"

# --- 3. restart -------------------------------------------------------------
if [[ -n "${AIQ_RESTART:-}" ]]; then
  bash -c "$AIQ_RESTART"
else
  bash "$MAIN/scripts/ctl.sh" restart
fi

# --- 4. check the dashboard serves the new build -----------------------------
fail=0
for url in $URLS; do
  got=""
  deadline=$(( $(date +%s) + WAIT ))
  while :; do
    got="$(marker_at "$url")"
    [[ "$got" == "$expect" ]] && break
    (( $(date +%s) >= deadline )) && break
    sleep 5
  done
  if [[ "$got" == "$expect" ]]; then
    say "LIVE           $url shell_version=$got"
  else
    say "NOT YET        $url shell_version=${got:-unreachable}, expected $expect (waited ${WAIT}s)"
    fail=1
  fi
done

if [[ $fail -eq 0 ]]; then
  say "DONE           rework-1019 is live. Previous main: $old"
else
  say "CHECK          merged and restarted, but not every URL shows the new build."
  say "               Log: /Users/petrlavrov/.local/share/ai-quotas/dash.log  Previous main: $old"
  exit 3
fi
