#!/bin/sh
# Reshoot the night quotas board. Serves a read-only dump, never the live sqlite.
# Start the server first, from the generated plots dir (port 8820-8849):
#   cd /tmp/aiq-plots-burn && python3 -m http.server 8822 --bind 127.0.0.1
# Then open http://127.0.0.1:8822/10_uplot/index.html
# Desktop 1440x900 scrolled to "Gemini Pro" and "Antigravity Pro".
# Phone 390x844, deviceScaleFactor 2, is_mobile, scrolled so the title sits
# just under the sticky header (about 132px).
# Shots: 01-desktop-gemini 02-desktop-antigravity 03-phone-gemini
#        04-phone-antigravity 05-phone-codex-credits
echo "see docs/journey/index.html"
