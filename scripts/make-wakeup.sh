#!/usr/bin/env bash
# make-wakeup.sh — build the runtime wake-up audio in TWO parts:
#
#   sounds/wakeup-intro.wav  the gentle nudge, played ONCE  (Jimmy Cliff)
#   sounds/wakeup-alarm.wav  the wake-up bed, LOOPED forever (10 escalating alarms)
#
# The monitor plays the intro once, then loops the alarm bed until the alarm
# clears (mask back on / you stop it). So: fix the mask during the Jimmy Cliff
# intro and you only ever hear that; ignore it and ~10 minutes of escalating
# alarms cycle and keep cycling until you are up. The song is NOT in the loop,
# so it never interrupts the alarms again.
#
# The 10 alarms, in order, get heavier each step:
#   1  Envato "Alarm Pulse" (item 44718) -- the one you like, first
#   2  clean urgent pulse        3  faster/higher pulse     4  buzzy square
#   5  rising wail siren         6  gritty sawtooth         7  hi/lo two-tone
#   8  thick overdriven square   9  heavy low sawtooth     10  gnarliest
# Alarms 2-10 are synthesized by sox (royalty-free, reproducible). Only the
# Jimmy Cliff and Envato mp3s are copyrighted -- they are GITIGNORED, as are the
# built wavs. Re-run after swapping either mp3.
#
#   JIMMY : /home/pi/plug-method/jimmy-cliff-american-plan.mp3
#   PULSE : sounds/alarm-pulse.mp3
#
# Tunables (env):
#   SEG_SECS   (default 60) seconds per alarm; 10 alarms => ~10 min of alarms.
#   INTRO_SECS (default full) length of the Jimmy Cliff intro before the alarms.
#              Default plays the WHOLE ~9-min song (owner's choice). Set a number
#              (e.g. INTRO_SECS=30) to trim it so the real alarm comes sooner.
set -euo pipefail
command -v sox >/dev/null || { echo "need sox (+libsox-fmt-mp3)"; exit 1; }
cd "$(dirname "$0")/.."

R=44100
SEG_SECS="${SEG_SECS:-60}"                 # seconds per alarm; 10 => ~10 min bed
INTRO_SECS="${INTRO_SECS:-full}"           # song nudge length; full|0 = whole song (default); or N seconds
JIMMY="/home/pi/plug-method/jimmy-cliff-american-plan.mp3"
PULSE="sounds/alarm-pulse.mp3"
INTRO="sounds/wakeup-intro.wav"
ALARM="sounds/wakeup-alarm.wav"
for f in "$JIMMY" "$PULSE"; do [ -f "$f" ] || { echo "missing source: $f"; exit 1; }; done

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mono(){ sox "$1" -r$R -c1 "$2"; }          # downmix/resample helper

if [ "$INTRO_SECS" = "full" ] || [ "$INTRO_SECS" = "0" ]; then
  echo "intro: Jimmy Cliff, full track (played once)"
  mono "$JIMMY" "$INTRO"
else
  echo "intro: Jimmy Cliff, first ${INTRO_SECS}s nudge (played once)"
  sox "$JIMMY" -r$R -c1 "$INTRO" trim 0 "$INTRO_SECS" fade 0 "$INTRO_SECS" 3
fi

# --- the 10 alarms, escalating ----------------------------------------------
# Direct-synth alarms: one continuous SEG_SECS tone with its own character.
syn(){ sox -n -r$R -c1 "$1" synth "$SEG_SECS" "${@:2}" gain -n -2; }
# Motif alarms: build a short loop then repeat+trim to SEG_SECS.
loopmotif(){ local motif="$1" out="$2"; sox "$motif" "$out" repeat "$SEG_SECS" trim 0 "$SEG_SECS"; }

echo "alarm 1/10: Envato Alarm Pulse (your pick)"
mono "$PULSE" "$T/pulse.wav"
loopmotif "$T/pulse.wav" "$T/a01.wav"

echo "alarm 2/10: clean urgent pulse"
syn "$T/a02.wav" sine 1000 tremolo 9 85
echo "alarm 3/10: faster, higher pulse"
syn "$T/a03.wav" sine 1250 tremolo 13 85
echo "alarm 4/10: buzzy square"
syn "$T/a04.wav" square 900 tremolo 10 80
echo "alarm 5/10: rising wail siren"
sox -n -r$R -c1 "$T/m05.wav" synth 2 sine 700-1500 gain -n -2
loopmotif "$T/m05.wav" "$T/a05.wav"
echo "alarm 6/10: gritty sawtooth"
syn "$T/a06.wav" sawtooth 650 tremolo 11 80 overdrive 10
echo "alarm 7/10: hi/lo two-tone siren"
sox -n -r$R -c1 "$T/t_hi.wav" synth 0.5 sine 988 gain -n -2
sox -n -r$R -c1 "$T/t_lo.wav" synth 0.5 sine 740 gain -n -2
sox "$T/t_hi.wav" "$T/t_lo.wav" "$T/m07.wav"
loopmotif "$T/m07.wav" "$T/a07.wav"
echo "alarm 8/10: thick overdriven square"
syn "$T/a08.wav" square 500 tremolo 12 80 overdrive 20
echo "alarm 9/10: heavy low sawtooth"
syn "$T/a09.wav" sawtooth 380 tremolo 14 80 overdrive 30
echo "alarm 10/10: gnarliest"
syn "$T/a10.wav" sawtooth 280 tremolo 16 75 overdrive 40

echo "stitching the bed..."
sox "$T"/a01.wav "$T"/a02.wav "$T"/a03.wav "$T"/a04.wav "$T"/a05.wav \
    "$T"/a06.wav "$T"/a07.wav "$T"/a08.wav "$T"/a09.wav "$T"/a10.wav "$ALARM"

echo "wrote $INTRO  ($(soxi -D "$INTRO")s, $(du -h "$INTRO" | cut -f1))"
echo "wrote $ALARM  ($(soxi -D "$ALARM")s, $(du -h "$ALARM" | cut -f1)) -- loops forever"
