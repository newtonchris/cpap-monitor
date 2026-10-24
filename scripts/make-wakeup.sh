#!/usr/bin/env bash
# make-wakeup.sh — build the runtime alarm file: a Jimmy Cliff intro, then the
# Envato "Alarm Pulse" (item 44718) repeated until you wake. Both source tracks
# are copyrighted/licensed and are GITIGNORED (*.mp3); this script and the
# resulting sounds/wakeup.wav are not committed. Re-run after swapping either mp3.
#
#   JIMMY : /home/pi/plug-method/jimmy-cliff-american-plan.mp3  (the gentle nudge)
#   PULSE : sounds/alarm-pulse.mp3                              (the wake-up)
#
# The monitor LOOPS this whole file until the alarm clears: fix the mask during
# the Jimmy Cliff intro and you only ever hear that; ignore it and the pulse hammers.
set -euo pipefail
command -v sox >/dev/null || { echo "need sox"; exit 1; }
cd "$(dirname "$0")/.."
R=44100
JIMMY="/home/pi/plug-method/jimmy-cliff-american-plan.mp3"
PULSE="sounds/alarm-pulse.mp3"
OUT="sounds/wakeup.wav"
PULSE_REPEATS="${PULSE_REPEATS:-8}"
for f in "$JIMMY" "$PULSE"; do [ -f "$f" ] || { echo "missing source: $f"; exit 1; }; done
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
sox "$JIMMY" -r$R -c1 "$T/jimmy.wav"
sox "$PULSE" -r$R -c1 "$T/pulse.wav" gain -n -1
args=("$T/jimmy.wav"); for i in $(seq "$PULSE_REPEATS"); do args+=("$T/pulse.wav"); done
sox "${args[@]}" "$OUT"
echo "wrote $OUT — $(soxi -D "$OUT")s, $(du -h "$OUT" | cut -f1)"
