#!/usr/bin/env bash
# make-siren.sh — generate the default escalating alarm siren with sox.
#
# 100% synthesized, original, royalty-free — safe to ship in a public repo.
# Produces sounds/alarm.wav: a siren that STARTS MILD and ramps through four
# levels to a full "nuclear-hazard" blare. Because the monitor loops the file
# while alarming, a brief alarm (you stir and fix it) is just a gentle nudge,
# while a persistent failure escalates into something impossible to sleep
# through.
#
# Requires sox:  sudo apt install sox libsox-fmt-all
# Usage:         scripts/make-siren.sh
set -euo pipefail
command -v sox >/dev/null || { echo "sox not installed — sudo apt install sox libsox-fmt-all"; exit 1; }

OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/sounds"
mkdir -p "$OUT_DIR"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
R=22050   # alarm audio doesn't need hi-fi; keeps the file small

# Level 1 — MILD: soft, slow pulse (a gentle "hey...")
sox -n -r $R -c 1 "$TMP/1.wav" synth 8  sine   660              tremolo 1.5 60 gain -18 fade 0.5 8  0.5
# Level 2 — NOTICEABLE: faster, higher, louder
sox -n -r $R -c 1 "$TMP/2.wav" synth 9  sine   784              tremolo 3   70 gain -11 fade 0.3 9  0.3
# Level 3 — URGENT: harsh warble
sox -n -r $R -c 1 "$TMP/3.wav" synth 11 square 820              tremolo 6   80 overdrive 12 gain -6 fade 0.2 11 0.2
# Level 4 — NUCLEAR: dissonant multi-tone blare, fast, loud
sox -n -r $R -c 1 "$TMP/4.wav" synth 20 square 1000 square 1480 tremolo 10  90 overdrive 20 gain -3 fade 0.1 20 0

# concatenate mild -> nuclear into the single looped siren
sox "$TMP/1.wav" "$TMP/2.wav" "$TMP/3.wav" "$TMP/4.wav" "$OUT_DIR/alarm.wav"
echo "wrote $OUT_DIR/alarm.wav — $(soxi -D "$OUT_DIR/alarm.wav" 2>/dev/null)s, escalating mild->nuclear"
