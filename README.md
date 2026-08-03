# cpap-monitor

A safety-critical failure alarm for a CPAP machine, running on a Raspberry
Pi. It watches the **real-time wattage** of the smart plug the CPAP is
plugged into and sounds a loud local siren (plus an optional push
notification) when something is wrong.

> **This is a health device.** Its one job is to wake someone up when the
> CPAP stops doing its job. Every design decision below bends toward
> "never be silently broken."

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
same numbers hold on 110 V and 230 V mains. They were tuned against a
ResMed AirSense; re-tune yours with `python3 -m cpapmon check` while
watching the machine breathe. (18.5 W, not lower, because deep breaths
briefly spike the draw.)

On alarm the monitor loops a loud mp3 through the Pi's audio output via
sox (`play`) until the condition clears, logs the event, and — if
configured — sends a Pushover notification.

## Two backends, one brain

All alarm logic lives in a single pure state machine
(`cpapmon/state.py`): **SEARCHING → ARMED → ALARMING**. The hardware is
abstracted behind one method, `read_watts()`. Two plug backends implement
it, selected by a single config key:

```toml
backend = "tasmota"   # or "kasa"
```

### Tasmota — preferred and default

* **Local HTTP, no cloud.** One `GET http://<plug>/cm?cmnd=Status 10` on
  the LAN. No vendor account, no credentials, works with the internet
  down.
* **Works at all mains voltages (110 V / 230 V).** Combined with
  watt-based thresholds, this makes it **the travel backend** — take it
  to Iceland or anywhere in Europe and nothing changes but the wall
  adapter.
* Reference hardware: Athom PG03 v4 (US16A) with factory Tasmota.

### Kasa (TP-Link) — home/secondary

* Uses `python-kasa`; found by pinned IP first, then LAN discovery by
  alias (survives DHCP moves).
* Needs TP-Link cloud account credentials on disk (even for local reads
  on newer plugs), and US Kasa hardware is typically 110 V-only.

### The Iceland switch

Both backend sections stay filled in in the config at all times. To
travel, change **one line** (`backend = "tasmota"`), pack the Tasmota
plug, done. Watt thresholds don't change.

## Quick start

```bash
git clone <this-repo> /home/cnewton/cpap-monitor
cd /home/cnewton/cpap-monitor
pip install -r requirements.txt          # python-kasa only needed for kasa
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
