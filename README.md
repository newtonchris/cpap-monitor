# cpap-monitor

A safety-critical failure alarm for a CPAP machine, running on a Raspberry
Pi. It watches the **real-time wattage** of the smart plug the CPAP is
plugged into and sounds a loud local siren (plus an optional push
notification) when something is wrong.

> **This is a health device.** Its one job is to wake someone up when the
> CPAP stops doing its job. Every design decision below bends toward
> "never be silently broken."

## Who this is for (and the problem it solves)

CPAP therapy only helps while the mask is on and the machine is running. But
plenty of people **pull the mask off in their sleep without ever waking up** —
or the hose pops off, or the machine quietly quits — and then spend the rest of
the night getting **no therapy at all**. You wake up exhausted and never know
why it was a bad night.

This is a cheap, do-it-yourself early-warning alarm for exactly that. It never
touches your CPAP, your air, or your data — it just watches how much
**electricity** the machine pulls through a smart plug, and sounds a loud siren
(and optionally pings your phone) the moment the power pattern says therapy has
stopped. You — or a caregiver in the next room — wake up and put the mask back
on. No cloud, no subscription, no special medical gear: a Raspberry Pi and a
~$15 energy-monitoring smart plug.

## ⚠️ Disclaimer — no warranty, use at your own risk

**This is NOT a medical device.** It is a hobbyist DIY project. It is **not**
FDA-cleared, CE-marked, or certified as an apnea/CPAP alarm, and it is **not a
substitute** for prescribed medical equipment, a certified alarm, or
professional medical advice.

- Provided **"AS IS", WITHOUT WARRANTY OF ANY KIND**, express or implied. **You
  assume all risk.** The authors and contributors accept **no liability** for any
  injury, harm, missed alarm, false alarm, lost sleep, or damage arising from its
  use or failure.
- It infers "is the machine working?" from a **crude, plug-level power reading
  only.** It can **miss real failures** and can **false-alarm.** **Do not rely on
  it as a life-safety device.**
- Nothing here is medical advice. **Talk to your physician, sleep specialist, or
  DME provider,** and do not change your CPAP settings based on this tool.

By using this software you accept these terms.

## How the wattage method works

A CPAP with a breathing human attached doesn't draw constant power — the
blower modulates with every breath. That makes plug-level power a
surprisingly good proxy for "the machine is on and someone is breathing
through it":

| Condition | Power signature | Alarm |
|---|---|---|
| Normal sleep | fluctuates above ~4.3 W | — |
| Machine off / unplugged / dead | flat below **4.3 W** for **60 s** | **NO_BREATH** |
| Mask off / hose blown off (blower spins up) | above **18.5 W** for **60 s** | **RUNAWAY** |
| Plug unreachable (Wi-Fi/power problem) | no readings for **3 min** (once armed) | **CONNECTION_LOST** |

Thresholds are in **watts** and therefore **voltage-independent** — the
same numbers hold on 110 V and 230 V mains. The shipped defaults
(**4.3 W / 18.5 W**) were tuned for a **ResMed AirSense 10 (S10) AutoSet
running ~6–10 cmH₂O**. A different machine, mask, humidifier, or pressure
range draws different power, so **you will almost certainly need to re-tune**
yours with `python3 -m cpapmon check` while watching the machine breathe
(see [Tuning it for your CPAP](#tuning-it-for-your-cpap)). (18.5 W, not
lower, because deep breaths briefly spike the draw.)

On alarm the monitor loops a loud mp3 through the Pi's audio output via
sox (`play`) until the condition clears, logs the event, and — if
configured — sends a Pushover notification.

## One backend, one brain

All alarm logic lives in a single pure state machine
(`cpapmon/state.py`): **SEARCHING → ARMED → ALARMING**. The hardware is
abstracted behind one method, `read_watts()` — a pluggable backend
contract (`cpapmon/backends/`), selected by a single config key. Tasmota
is the only shipped backend, but a future plug type would just implement
`read_watts()` and register in the factory:

```toml
backend = "tasmota"
```

### Tasmota — the backend

* **Local HTTP, no cloud.** One `GET http://<plug>/cm?cmnd=Status 10` on
  the LAN. No vendor account, no credentials, works with the internet
  down.
* **Works at all mains voltages (110 V / 230 V).** Combined with
  watt-based thresholds, this makes it fully **travel-ready** — take it
  to Iceland or anywhere in Europe and nothing changes but the wall
  adapter.
* Reference hardware: Athom PG03 v4 (US16A) with factory Tasmota.

### Iceland (and any other 230 V country)

Nothing in the config changes for travel: the thresholds are in watts
(voltage-independent) and the plug hardware is dual-voltage. Pack the
Tasmota plug and the Pi, run the arrival checklist in
[docs/TRAVEL.md](docs/TRAVEL.md), done.

## Hardware — it runs on almost any always-on computer

Nothing here is Raspberry-Pi-specific. The monitor is just **Python + `sox`**
talking to a smart plug over the network, so it runs on basically **any
always-on machine that can (a) reach the plug on your LAN and (b) make noise** —
a Raspberry Pi, a fanless mini-PC, an old laptop, a home server/NAS that runs
Python, even a Mac (with `sox`; swap the systemd unit for launchd).

Minimum needs:
* **Python 3.11+** (uses stdlib `tomllib`; on older Python add `tomli`).
* **`sox`** for the siren (`sudo apt install sox libsox-fmt-all`) plus a working
  audio output — or route the alarm elsewhere.
* **On the same network as the plug**, and **always on** — it has to be running
  the entire time you're asleep.

**Pick something SILENT for the bedroom.** This box sits next to where you sleep
and runs all night, so a whirring fan defeats the whole purpose. That's the real
reason the reference build is a **Raspberry Pi — fanless, silent, low-power, and
cheap.** Any quiet/fanless machine is great; avoid anything with an audible fan.

Two Pi-specific conveniences in the default setup are **optional**:
* The Pi **hosts its own `cpap` Wi-Fi network** for the plug (handy for travel and
  isolation). A stationary home box can skip that — just put the plug on your
  normal Wi-Fi and point the config at its IP.
* The systemd unit and the `/home/cnewton/cpap-monitor` path assume a Linux host;
  adjust paths (or the service manager) for your machine.

## Quick start

```bash
git clone <this-repo> /home/cnewton/cpap-monitor
cd /home/cnewton/cpap-monitor
pip install -r requirements.txt          # dev/test tools only; runtime is stdlib
sudo scripts/install.sh                  # unit + /etc/cpap-monitor skeleton
sudo $EDITOR /etc/cpap-monitor/config.toml   # fill every REPLACE_ME
python3 -m cpapmon validate              # config sanity check
python3 -m cpapmon check                 # read the plug once, print watts
python3 -m cpapmon simulate              # dry-run the alarm logic
python3 -m cpapmon test-alert            # HEAR the siren, get the push
sudo systemctl restart cpap-monitor      # arm for real
journalctl -u cpap-monitor -f            # watch it arm
```

Full new-Pi walkthrough (including the plug Wi-Fi network):
[docs/DEPLOY.md](docs/DEPLOY.md). Architecture and rationale:
[docs/DESIGN.md](docs/DESIGN.md). Traveling to a different country/voltage
(e.g. Iceland, 230 V): [docs/TRAVEL.md](docs/TRAVEL.md) — the watt thresholds
are voltage-independent, but run the arrival checklist to verify the plug's
meter on-site.

## Alarm sound (bring your own, or use the escalating siren)

The siren is just an audio file you point `[sound] alarm_file` at, played on a
loop through the Pi's speaker until the alarm clears. **Use whatever will
actually wake *you*** — a song, an air-horn, a klaxon, your kid yelling your
name. (Personally I use a Jimmy Cliff track. Pick something you can't sleep
through.)

The repo ships a default original, royalty-free **escalating siren**,
`sounds/alarm.wav`: it **starts mild and ramps through several levels to a
full "nuclear-hazard" blare** — a gentle nudge if you stir quickly, impossible
to sleep through if the failure persists. Regenerate or customize it with
`scripts/make-siren.sh` (pure `sox`, no downloads). Your own `*.mp3` files are
git-ignored, so drop any track on the Pi and point the config at it — nothing
personal or copyrighted ever lands in this public repo.

## Tuning it for your CPAP

The two wattage thresholds are the **only** machine-specific numbers, and the
defaults fit exactly one setup (ResMed AirSense 10 AutoSet, ~6–10 cmH₂O). Your
model, mask, humidifier, and pressure will draw differently, so plan to re-tune:

1. `python3 -m cpapmon check` with the machine **off** → note the **idle** watts.
2. `python3 -m cpapmon check` while **breathing normally on the mask** → note the
   **running** watts.
3. In `/etc/cpap-monitor/config.toml`, set `activity_watt_limit` between the two
   (comfortably above idle) and `high_watt_limit` above your highest normal
   reading. Re-run `check` until idle reads clearly below and running clearly
   above the line. (Same idea for a new country/voltage — see
   [docs/TRAVEL.md](docs/TRAVEL.md).)

**Not comfortable doing that yourself?** The whole job — clone it, flash a Pi,
set up the plug network, read your machine's watts, and dial the thresholds in
for your model and pressures — is very doable in an evening with an AI coding
assistant. Roughly **~$20 for one month of something like
[Claude Code](https://www.anthropic.com/claude-code)** (cancel afterward) can
most likely walk you through the entire setup and tune it to your CPAP — this
repository was itself built and tuned that way.

> **On a low-RAM Pi (the 3B/3B+ has ~1 GB), don't run the AI assistant *on* the
> Pi** — it'll bog down. Run Claude Code (or similar) on a **separate, more
> powerful computer** and give it **SSH access to the Pi** (a sudo account, or a
> temporary root key) so the heavy work happens off-box. **Remove that access
> when you're done** — delete the temp key and any passwordless-sudo entry, and
> re-tighten SSH. Details in [AGENTS.md](AGENTS.md).

## CLI

```
python3 -m cpapmon run          # the monitor loop (what systemd runs)
python3 -m cpapmon check        # one wattage reading from the active backend
python3 -m cpapmon validate     # config lint; systemd gates startup on this
python3 -m cpapmon simulate     # synthetic watts through the state machine
python3 -m cpapmon test-alert   # real siren + real push, on demand
```

## Safety notes

* **The local siren is the primary alarm.** Pushover is a bonus channel;
  the monitor never depends on the internet to wake you.
* **Test the whole path monthly:** `python3 -m cpapmon test-alert`. A
  monitor you haven't heard fire recently is a monitor you should not
  trust. (This project exists because a previous alarm was silently dead
  for 78 days.)
* The monitor is deliberately conservative: once alarming, only a
  genuinely safe reading silences it. Losing the plug mid-alarm never
  stops the siren.
* A heartbeat file (`logs/status.json` by default) is refreshed every
  30 s with the current state — external watchdogs can (and should)
  check its freshness. See docs/DESIGN.md for the fuller
  anti-silent-failure roadmap.
* This is a homebrew aid, **not a medical device**. It supplements — and
  must never replace — clinical equipment and common sense.

## Secrets policy (public repo)

No credentials, tokens, Wi-Fi passphrases, health logs, or audio files
are committed — ever. The real config lives at
`/etc/cpap-monitor/config.toml` (mode 0640, outside the repo); the repo
ships only `config/config.example.toml` with `REPLACE_ME` placeholders.
`.gitignore` enforces the rest (`*.log`, `logs/`, `nightly-summaries/`,
`*.mp3`).

## Tests

Pure-logic tests for the state machine; no hardware needed:

```bash
pytest tests/
```

## Sound output on this build (pw-play) and the wake-up track

The siren plays through **`pw-play` (PipeWire)**, not raw-ALSA sox `play`. On this
Pi `play` with no device argument cannot open a default ALSA device (dmix fails),
so it errored silently; and the USB speaker (a Gembird Honk HK-5002) re-enumerates
on its own, which raw ALSA can't ride out. `pw-play` targets the session's default
sink (set to the USB speaker; `loginctl enable-linger cnewton` keeps PipeWire up at
boot) and reconnects when the device returns. sox `play` remains a fallback.

The runtime alarm is built by **`scripts/make-wakeup.sh`** in **two parts**, and
the player treats them differently (see `AlarmSound` in `cpapmon/sound.py`):

* **`sounds/wakeup-intro.wav`** — the gentle nudge, played **once**. A short
  Jimmy Cliff track (the full song by default): fix the mask during it and you
  only ever hear that.
* **`sounds/wakeup-alarm.wav`** — the wake-up, **looped forever** until the alarm
  clears. A ~10-minute bed of **10 escalating alarms**: the licensed Envato
  *Alarm Pulse* (item 44718) first, then 9 progressively heavier/gnarlier alarms
  synthesized by `sox` at build time. Because the intro is not in the loop, the
  song never interrupts the alarms again — they just cycle and keep hammering.

`/etc/cpap-monitor/config.toml` points `alarm_file` at the alarm bed and
`intro_file` at the nudge. Only the Jimmy Cliff and Envato **mp3s** are
copyrighted — they stay on the Pi (`*.mp3` is gitignored); the built wavs are
gitignored too. Re-run `scripts/make-wakeup.sh` after swapping either mp3.
Tunables: `SEG_SECS` (seconds per alarm; 10 of them ≈ total minutes) and
`INTRO_SECS` (intro length; the full ~9-min song by default — set e.g.
`INTRO_SECS=30` to trim it so the real alarm comes sooner).

⚠️ The USB speaker (a Gembird Honk HK-5002) is a cheap self-resetting unit; for a
safety alarm a powered speaker on the 3.5 mm jack, or a powered USB hub, would be
more dependable.

## Snooze a break without the siren (power-cycle gesture)

Need the bathroom at 3am? Use the CPAP's own power button: **press Stop and hold
~3 s, press Start and hold ~3 s, press Stop** — then leave it off for your break.
That's three sustained transitions ending stopped; the monitor **chirps twice**
and suppresses the mask-off (NO_BREATH) alarm for **15 minutes** while you're up.

**Why the holds, not quick flicks.** Pressing Stop doesn't cut the power — the
machine sits in standby at **~3–4 W**, which is the *same wattage band as a normal
breathing dip*. So the gesture can't be told from breathing by wattage; it's told
by **duration**. A power level must be *held* for `snooze_gesture_debounce_s`
(~3 s) to count as a real Stop or Start. This is the fix for the bug where
ordinary breathing jitter across the 4.3 W line false-armed the snooze overnight
and silently muted the alarm — short dips are now ignored.

It is also deliberately **impossible to defeat the alarm by a passive failure**:
the gesture requires the blower to actually *restart* between two stops (an on
edge between two off edges), and a mask-off or dead machine only makes watts drop
and stay down — one edge, never a gesture, so a genuine failure always sounds. The
snooze **auto-expires** — if you fall back asleep without the mask on, it wakes
you when the grace period ends — and **auto-clears** the instant the machine is
running again. Runaway-blower and plug-unreachable alarms are never snoozable.
Tune or disable it in `[thresholds]` (`snooze_s = 0` disables).
