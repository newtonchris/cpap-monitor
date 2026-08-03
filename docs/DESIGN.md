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
