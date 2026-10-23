#!/usr/bin/env bash
# Replay against generated static previews. Never point this at a live dash.
set -euo pipefail
cd "$(dirname "$0")/.."
base_url="${1:-http://127.0.0.1:8851}"
pw() { npx --yes --package @playwright/cli playwright-cli -s=q1-gemini "$@"; }
mkdir -p output/playwright
pw open "$base_url/after/10_uplot/index.html"
for version in before after; do
  pw goto "$base_url/$version/10_uplot/index.html"
  pw snapshot
  for viewport in desktop phone; do
    if [[ "$viewport" == desktop ]]; then pw resize 1440 1000; else pw resize 390 844; fi
    pw eval "() => { const p = window._quotaPanels.find(p => p.vendor === 'Gemini'); const section = [...document.querySelectorAll('.panel')].find(s => s.querySelector('.vendor-name')?.textContent === quotaDisplayTitle(p) && s.textContent.includes(quotaAccountUsername(p))); if (!section) throw Error('Primary Gemini panel missing'); section.scrollIntoView({block:'start'}); if ('$version' === 'before') window.scrollBy(0,-100); return {text: section.innerText, overflow: document.documentElement.scrollWidth > innerWidth}; }"
    pw screenshot --filename="output/playwright/$viewport-$version.png"
  done
done
