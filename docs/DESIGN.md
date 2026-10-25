# DESIGN.md — architecture and rationale

## The problem

A CPAP that quietly stops (power cut, machine fault, hose off, dead
plug-Wi-Fi) endangers the sleeper. We can't instrument the airway, but we
can instrument the wall: a CPAP with a breathing human attached draws
power that *modulates with every breath*. Plug-level wattage is therefore
a usable liveness signal for both the machine and, indirectly, the
therapy.

## History: two scripts, one 78-day silence

This repo replaces two divergent field scripts (`alarm.py` and
`alarm_tasmota.py`, one per plug vendor) that each carried their own copy
of the alarm logic, their own config constants, and an SMTP notifier
bolted to a burner Gmail account. The predecessor was **silently dead for 78 days**:
the process wasn't running, nothing watched it, and the notify channel
that might have said so had rotted. Every structural choice below is a
response to that.

## Architecture

```
                 +--------------------------+
   config.toml → |  config.py  (tomllib)    |  validate() gates startup
                 +------------+-------------+
                              |
                 +------------v-------------+
                 |  runner.py  (poll loop)  |
                 +--+--------+---------+----+
     read_watts()   |        |         |  events
   +----------------v-+   +--v---------v-----------+
   | backends/         |   |  state.py (PURE)       |
   |  tasmota (HTTP)   |   |  SEARCHING→ARMED→      |
   |                   |   |  ALARMING              |
   +-------------------+   +--+------+------+-------+
                              |      |      |
                        sound.py  notify.py heartbeat.py
                        (siren)   (Pushover) (status.json)
```

### One brain, pluggable senses

All alarm decisions live in `state.py`: a pure state machine with two
inputs (`on_reading(now, watts)`, `on_unreachable(now)`) and event-list
outputs. It touches no clock, socket, or file, which is what makes the
test suite in `tests/test_monitor.py` possible without hardware — and
what guarantees the alarm behaviour can never drift with the hardware
again (the predecessor's two scripts each had their own copy of the
logic). The entire hardware contract is:

```python
read_watts() -> float          # or raises PlugUnreachable
```

### States

* **SEARCHING** — startup standby. Deliberately silent: at bedtime the
  plug may not be powered yet, and a false siren every evening trains
  the operator to distrust the device. No alarm can fire here.
* **ARMED** — the plug has answered once. From this point *everything*
  is alarm-worthy, including losing the plug.
* **ALARMING** — siren looping. Only a verified-safe reading (plug
  reachable AND watts in the normal band) clears it. Going unreachable
  mid-alarm never silences anything; recovery resets all windows so a
  cleared alarm can't instantly re-trigger off stale timers.

### Alarm conditions (watts are voltage-independent)

| Reason | Trigger | Default |
|---|---|---|
| NO_BREATH | watts ≤ activity limit continuously | < 4.3 W for 60 s |
| RUNAWAY | watts > high limit continuously | > 18.5 W for 60 s |
| CONNECTION_LOST | no successful poll, once armed | 180 s |

18.5 W (not lower) because deep breaths legitimately spike the draw;
60 s windows because breath-to-breath modulation routinely dips under
the activity limit for tens of seconds.

### Backend

* **tasmota (the only shipped backend):** one local HTTP GET
  (`/cm?cmnd=Status 10`), no cloud, no account, and — because the plug
  hardware is dual-voltage and the thresholds are in watts — the same
  config works on 110 V and 230 V mains, so it travels as-is.
  The `Status 10` parser tolerates all three response shapes seen in
  the field (`StatusSNS.ENERGY`, top-level `ENERGY`, `SENSOR.ENERGY`).

The backend factory (`backends/__init__.py`) plus the `read_watts()`
contract in `backends/base.py` keep the architecture pluggable: a future
plug type adds a module, a config section, and a factory branch —
`state.py` never changes.

## Anti-silent-failure design

The 78-day silence is the enemy. Defence in depth, in order of maturity:

### Implemented now

1. **Explicit ARMED log line** at first plug contact — a grep-able,
   human-checkable "the watcher is watching" moment, plus an armed push
   when Pushover is configured.
2. **Heartbeat file** (`heartbeat.py`): atomic JSON status
   (state/watts/pid/timestamp) rewritten every 30 s. Freshness is
   externally checkable by cron, another host, or a monitoring agent —
   a stale mtime is proof of death regardless of what systemd claims.
3. **Config validation gates startup** (`ExecStartPre=... validate`): a
   bad edit fails loudly at restart instead of producing a monitor that
   runs but cannot alarm (e.g. missing siren file — validate checks the
   file exists).
4. **`Restart=always` + `StartLimitIntervalSec=0`**: systemd never gives
   up restarting a safety device.
5. **`test-alert` command**: fires the *real* siren and the *real* push
   on demand. The documented discipline is to run it monthly.
6. **Notify simplification**: SMTP/burner-Gmail dropped entirely (its
   app-password rot was a proven silent-failure vector); Pushover with
   an explicit log-fallback when unconfigured, and notifications are
   fire-and-forget so a dead notify path can never stall the siren.

### Documented future work (not yet wired)

* **Morning "still armed" push** — a daily scheduled Pushover message
  sent only if the heartbeat is fresh; its *absence* on your phone is
  the alarm. Bounds silent death to hours instead of months.
* **External watchdog** — another host (or Zabbix, already deployed on
  this fleet) alerts when the heartbeat file goes stale or the service
  drops; the watcher gets its own watcher.
* **systemd `WatchdogSec`** — have the run loop `sd_notify` so systemd
  itself kills/restarts a hung (not just dead) process.
* **Nightly summary revival** — the old per-night wattage summaries were
  useful; if revived they stay local/gitignored (health data never
  enters the public repo).

## Snooze — pausing for a break without defeating the alarm

A sleeper who gets up for 10–15 minutes (bathroom) wants to stop the siren
from firing while the mask is off — *without* a mechanism that could also mute
a real failure. Snooze is a bounded grace (default 15 min, `snooze_s`) that
suppresses **only NO_BREATH**; RUNAWAY and CONNECTION_LOST still fire. It
auto-expires (fall back asleep unmasked and it still wakes you) and auto-clears
the instant the machine runs again.

Intent is signalled by a gesture on the CPAP's **own Stop/Start button**:
**Stop (hold ~3 s) → Start (hold ~3 s) → Stop**, ending stopped — then take the
break. Implemented in `state.py::Monitor._track_gesture`.

### Why the gesture is detected by DURATION, not wattage

Measured on the reference ResMed, through the plug:

| State | Plug draw |
|---|---|
| Running (blower on, breathing) | ~11 W, modulating |
| **Stopped at the machine's own button** | **steady 3–4 W (standby)** |
| Unplugged at the wall outlet | ~0 W |
| `activity_watt_limit` (the no-breath line) | 4.3 W |

Pressing **Stop does not cut power** — the machine sits in standby at **3–4 W**,
which is in the *same sub-4.3 W band as an ordinary breathing dip*. So no
wattage threshold can separate "the user pressed Stop" from "the user exhaled":
they are the same magnitude. The only thing that distinguishes them is **time** —
a real Stop is *held*; a breath dip is a transient while the blower keeps running.

So the detector **debounces**: a power level is trusted only once it has been
*held* for `snooze_gesture_debounce_s` (default 3 s). `snooze_gesture_edges`
(default 3) sustained transitions within `snooze_gesture_window_s` (default 30 s),
ending OFF, arm the snooze. Two properties keep it safe:

* **A passive failure can never look like the gesture.** It requires an ON edge —
  a real blower *restart* — between two OFF edges. A mask-off or dead machine only
  makes watts drop and *stay* down: one edge, never three, so a genuine emergency
  always sounds. This upholds the "bias conservative" rule — the gesture can only
  ever *fail to arm* (annoying), never *falsely arm* on a failure (dangerous).
* **It arms only when ending OFF**, so the grace covers the break that follows
  instead of being cleared by a still-running machine on the very next sample.

### Incident: the first snooze design false-armed overnight (2026-10-24)

The first implementation defined a power edge as *any* crossing of
`activity_watt_limit` (4.3 W). Because normal breathing modulation dips below
4.3 W for a sample now and then, the detector occasionally saw three "edges"
inside its window and **armed the snooze on its own** — heard by the owner as the
two-chirp confirmation *while asleep*. A false snooze silences NO_BREATH for up to
15 minutes: the alarm was being muted mid-sleep — the exact silent-failure class
this whole project exists to prevent.

Root cause was an assumption that "off" meant ~0 W (an unplug). It does not: the
owner pauses with the machine's **Stop button**, which reads **3–4 W**, inside the
breath-dip band. (A brief hysteresis attempt — a separate low "off" floor — was
written then discarded for the same reason: standby and breath dips share a
wattage band, so only duration separates them.) The fix is the duration-based
detector above. The regression test is
`tests/test_snooze.py::test_breathing_jitter_does_not_arm` — single-sample dips
must never arm — alongside `test_single_stop_is_not_a_gesture` (a real failure is
one sustained edge and still alarms).

## Non-goals

* Not a medical device; supplements, never replaces, clinical equipment.
* No cloud dependency in the alarm path — the siren must work with the
  internet down.
* No plug *control* (on/off switching): read-only monitoring keeps the
  failure surface small; the monitor can't accidentally power off a CPAP.

## Testing philosophy

The state machine is tested exhaustively with a virtual clock (arming,
each alarm's fire/no-fire boundary, spike/dip immunity, recovery,
timer-reset-after-recovery, unreachable-while-alarming). The runner and
backends are deliberately thin; hardware verification is procedural —
`check`, `simulate`, `test-alert`, and the pull-the-plug drill in
docs/DEPLOY.md step 7.
