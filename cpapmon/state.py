"""Pure alarm-logic state machine. No I/O, no clocks, no hardware.

This is the single source of truth for WHEN the alarm fires. Both backends
(tasmota, kasa) feed it the same two inputs:

    on_reading(now, watts)   -- a successful wattage sample
    on_unreachable(now)      -- the plug could not be reached

and it returns a list of Events describing what just changed. The runner
turns those events into side effects (sound, logs, notifications).

States
------
SEARCHING  Silent standby. Either the plug has never answered, OR the plug
           answers but the machine is NOT running (idle/standby watts). The
           alarm does NOT fire here — this is what makes the monitor safe to
           auto-start on boot: if the Pi reboots while the CPAP is off (e.g.
           daytime), it sits silent instead of blaring. It ARMS the moment it
           sees the machine actually running (watts above activity_watt_limit).
ARMED      The machine has been seen running. From now on every failure mode
           is alarm-worthy.
ALARMING   An alarm condition is active (siren should be sounding).

Why "arm only after the machine runs": a CPAP failure alarm must survive a
mid-sleep reboot (come back and re-arm because it sees you breathing) WITHOUT
false-alarming on a daytime reboot (machine off). Keying ARM on an *active*
reading — not merely on the plug answering — delivers both.

Alarm conditions (all evaluated only once ARMED)
------------------------------------------------
NO_BREATH        watts stayed below thresholds.activity_watt_limit for more
                 than thresholds.no_activity_timeout_s. A running CPAP with a
                 breathing human on it modulates above ~4.3 W; a flat low
                 trace means the machine is off, unplugged, or dead.
RUNAWAY          watts stayed above thresholds.high_watt_limit for more than
                 thresholds.high_watt_timeout_s (mask off / hose blown off:
                 the blower spins up trying to hold pressure).
CONNECTION_LOST  the plug was unreachable for more than
                 thresholds.connection_timeout_s.

Auto-stand-down
---------------
If a NO_BREATH or CONNECTION_LOST alarm rings for longer than
thresholds.alarm_auto_standby_s without recovery, the monitor returns to
SEARCHING (silent) and waits to re-arm. Rationale: the siren's job is to WAKE
the sleeper; once it has been blaring that long with the machine plainly OFF
(you woke up and got out of bed, or nobody is there), continuing to blare an
empty room for hours helps no one and would make people disable the whole
thing. Set alarm_auto_standby_s = 0 to disable (ring forever). RUNAWAY (machine
still powered) never auto-stands-down.

All thresholds are in WATTS and are voltage-independent (identical behaviour
at 110 V and 230 V mains).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class State(enum.Enum):
    SEARCHING = "SEARCHING"
    ARMED = "ARMED"
    ALARMING = "ALARMING"


class AlarmReason(enum.Enum):
    NO_BREATH = "NO_BREATH"
    RUNAWAY = "RUNAWAY"
    CONNECTION_LOST = "CONNECTION_LOST"


class EventKind(enum.Enum):
    ARMED = "ARMED"                    # machine seen running; now watching
    ALARM_START = "ALARM_START"        # start the siren + notify
    ALARM_STOP = "ALARM_STOP"          # safe/stand-down, silence siren
    UNREACHABLE = "UNREACHABLE"        # plug missed a poll (informational)
    RECONNECTED = "RECONNECTED"        # plug answered again after misses


@dataclass(frozen=True)
class Event:
    kind: EventKind
    reason: AlarmReason | None = None
    watts: float | None = None
    detail: str = ""


@dataclass(frozen=True)
class Thresholds:
    """Watt thresholds + timeouts. Defaults match the tuned ResMed values."""

    activity_watt_limit: float = 4.3      # below this = no breath activity
    no_activity_timeout_s: float = 60.0
    high_watt_limit: float = 18.5         # above this = runaway blower
    high_watt_timeout_s: float = 60.0
    connection_timeout_s: float = 180.0   # unreachable-while-armed budget
    alarm_auto_standby_s: float = 600.0   # ring this long w/o recovery -> stand
                                          # down to silent SEARCHING (0 = never)

    def validate(self) -> list[str]:
        problems = []
        if self.activity_watt_limit <= 0:
            problems.append("activity_watt_limit must be > 0")
        if self.high_watt_limit <= self.activity_watt_limit:
            problems.append("high_watt_limit must exceed activity_watt_limit")
        for name in ("no_activity_timeout_s", "high_watt_timeout_s",
                     "connection_timeout_s"):
            if getattr(self, name) <= 0:
                problems.append(f"{name} must be > 0")
        if self.alarm_auto_standby_s < 0:
            problems.append("alarm_auto_standby_s must be >= 0 (0 disables)")
        return problems


@dataclass
class Monitor:
    """The state machine. Feed it time-stamped observations; act on events.

    Deliberately conservative: once ALARMING, only a genuinely safe reading
    (plug reachable AND watts in the normal band) silences the alarm early.
    Going unreachable while already alarming never silences anything (except
    the bounded auto-stand-down above).
    """

    thresholds: Thresholds = field(default_factory=Thresholds)

    state: State = State.SEARCHING
    alarm_reason: AlarmReason | None = None

    _last_activity_at: float | None = None
    _high_watt_since: float | None = None
    _unreachable_since: float | None = None
    _was_unreachable: bool = False
    _alarming_since: float | None = None

    # ------------------------------------------------------------------ #

    def on_reading(self, now: float, watts: float) -> list[Event]:
        """Process one successful wattage sample."""
        t = self.thresholds
        events: list[Event] = []

        # Reconnect bookkeeping (applies in any state).
        if self._was_unreachable:
            self._was_unreachable = False
            events.append(Event(EventKind.RECONNECTED, watts=watts))
        self._unreachable_since = None

        # A connection-lost alarm is cleared by the mere fact the plug is back;
        # wattage evaluation continues below and may immediately re-alarm.
        if self.alarm_reason is AlarmReason.CONNECTION_LOST:
            events.append(self._stop_alarm(now, "plug reachable again",
                                           watts=watts))

        # ARM only once the machine is actually RUNNING. A reachable-but-idle
        # plug (machine off / standby) stays in silent SEARCHING — no alarm.
        if self.state is State.SEARCHING:
            if watts > t.activity_watt_limit:
                self.state = State.ARMED
                self._reset_timers(now)
                events.append(Event(EventKind.ARMED, watts=watts))
            else:
                return events  # reachable but idle -> silent standby

        # --- ARMED or ALARMING: track activity / runaway windows ------- #
        if watts > t.activity_watt_limit:
            self._last_activity_at = now
        if self._last_activity_at is None:
            self._last_activity_at = now

        if watts > t.high_watt_limit:
            if self._high_watt_since is None:
                self._high_watt_since = now
        else:
            self._high_watt_since = None

        idle_s = now - self._last_activity_at
        high_s = (now - self._high_watt_since) if self._high_watt_since else 0.0

        # --- trigger decision ------------------------------------------ #
        reason: AlarmReason | None = None
        detail = ""
        if idle_s > t.no_activity_timeout_s:
            reason = AlarmReason.NO_BREATH
            detail = f"watts <= {t.activity_watt_limit} for {idle_s:.0f}s"
        elif high_s > t.high_watt_timeout_s:
            reason = AlarmReason.RUNAWAY
            detail = f"watts > {t.high_watt_limit} for {high_s:.0f}s"

        if reason is not None:
            if self.state is not State.ALARMING:
                self.state = State.ALARMING
                self.alarm_reason = reason
                self._alarming_since = now
                events.append(Event(EventKind.ALARM_START, reason=reason,
                                    watts=watts, detail=detail))
            elif (self.alarm_reason is AlarmReason.NO_BREATH
                    and t.alarm_auto_standby_s > 0
                    and self._alarming_since is not None
                    and now - self._alarming_since > t.alarm_auto_standby_s):
                # Machine has been off and the siren unanswered too long —
                # stand down to silent standby, re-arm when it runs again.
                events.append(self._to_standby(
                    now, f"no recovery in {t.alarm_auto_standby_s:.0f}s — "
                         "standing down to standby"))
        elif self.state is State.ALARMING:
            events.append(self._stop_alarm(now, "safe wattage restored",
                                           watts=watts))

        return events

    def on_unreachable(self, now: float) -> list[Event]:
        """Process one failed poll (backend raised PlugUnreachable)."""
        t = self.thresholds
        events: list[Event] = []

        if self.state is State.SEARCHING:
            # Standby: plug may simply not be powered yet. Stay silent.
            return events

        if not self._was_unreachable:
            self._was_unreachable = True
            self._unreachable_since = now
            events.append(Event(EventKind.UNREACHABLE))

        downtime = now - (self._unreachable_since or now)

        if self.state is not State.ALARMING:
            if downtime > t.connection_timeout_s:
                self.state = State.ALARMING
                self.alarm_reason = AlarmReason.CONNECTION_LOST
                self._alarming_since = now
                events.append(Event(
                    EventKind.ALARM_START,
                    reason=AlarmReason.CONNECTION_LOST,
                    detail=f"plug unreachable for {downtime:.0f}s",
                ))
        elif (self.alarm_reason is AlarmReason.CONNECTION_LOST
                and t.alarm_auto_standby_s > 0
                and self._alarming_since is not None
                and now - self._alarming_since > t.alarm_auto_standby_s):
            # Plug still gone and siren unanswered too long — stand down.
            events.append(self._to_standby(
                now, "plug still unreachable — standing down to standby"))

        return events

    # ------------------------------------------------------------------ #

    def _stop_alarm(self, now: float, why: str,
                    watts: float | None = None) -> Event:
        """Recover to ARMED (safe reading restored). Keeps watching."""
        self.state = State.ARMED
        stopped = self.alarm_reason
        self.alarm_reason = None
        self._alarming_since = None
        self._reset_timers(now)
        return Event(EventKind.ALARM_STOP, reason=stopped, watts=watts,
                     detail=why)

    def _to_standby(self, now: float, why: str) -> Event:
        """Stand down to silent SEARCHING; must see the machine run to re-arm."""
        self.state = State.SEARCHING
        stopped = self.alarm_reason
        self.alarm_reason = None
        self._alarming_since = None
        self._was_unreachable = False
        self._unreachable_since = None
        self._reset_timers(now)
        return Event(EventKind.ALARM_STOP, reason=stopped, detail=why)

    def _reset_timers(self, now: float) -> None:
        self._last_activity_at = now
        self._high_watt_since = None
