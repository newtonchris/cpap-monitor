"""Pure alarm-logic state machine. No I/O, no clocks, no hardware.

This is the single source of truth for WHEN the alarm fires. Both backends
(tasmota, kasa) feed it the same two inputs:

    on_reading(now, watts)   -- a successful wattage sample
    on_unreachable(now)      -- the plug could not be reached

and it returns a list of Events describing what just changed. The runner
turns those events into side effects (sound, logs, notifications).

States
------
SEARCHING  Startup standby. The plug has never answered. Silent by design:
           the operator may not have powered the plug yet. No alarms here.
ARMED      The plug has answered at least once. From now on, every failure
           mode is alarm-worthy.
ALARMING   An alarm condition is active (siren should be sounding).

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
    ARMED = "ARMED"                    # first successful plug contact
    ALARM_START = "ALARM_START"        # start the siren + notify
    ALARM_STOP = "ALARM_STOP"          # safe state restored, silence siren
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
        return problems


@dataclass
class Monitor:
    """The state machine. Feed it time-stamped observations; act on events.

    Deliberately conservative: once ALARMING, only a genuinely safe reading
    (plug reachable AND watts in the normal band) silences the alarm. Going
    unreachable while already alarming never silences anything.
    """

    thresholds: Thresholds = field(default_factory=Thresholds)

    state: State = State.SEARCHING
    alarm_reason: AlarmReason | None = None

    _last_activity_at: float | None = None
    _high_watt_since: float | None = None
    _unreachable_since: float | None = None
    _was_unreachable: bool = False

    # ------------------------------------------------------------------ #

    def on_reading(self, now: float, watts: float) -> list[Event]:
        """Process one successful wattage sample."""
        t = self.thresholds
        events: list[Event] = []

        if self.state is State.SEARCHING:
            self.state = State.ARMED
            self._reset_timers(now)
            events.append(Event(EventKind.ARMED, watts=watts))

        if self._was_unreachable:
            self._was_unreachable = False
            events.append(Event(EventKind.RECONNECTED, watts=watts))
        self._unreachable_since = None

        # A connection-lost alarm is cleared by the mere fact the plug is
        # back; wattage evaluation continues below and may immediately
        # re-alarm for NO_BREATH/RUNAWAY if the readings warrant it.
        if self.alarm_reason is AlarmReason.CONNECTION_LOST:
            events.append(self._stop_alarm(now, "plug reachable again"))

        # --- track activity / runaway windows -------------------------- #
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
                events.append(Event(EventKind.ALARM_START, reason=reason,
                                    watts=watts, detail=detail))
        elif self.state is State.ALARMING:
            events.append(self._stop_alarm(now, "safe wattage restored",
                                           watts=watts))

        return events

    def on_unreachable(self, now: float) -> list[Event]:
        """Process one failed poll (backend raised PlugUnreachable)."""
        events: list[Event] = []

        if self.state is State.SEARCHING:
            # Standby: plug may simply not be powered yet. Stay silent.
            return events

        if not self._was_unreachable:
            self._was_unreachable = True
            self._unreachable_since = now
            events.append(Event(EventKind.UNREACHABLE))

        downtime = now - (self._unreachable_since or now)
        if (downtime > self.thresholds.connection_timeout_s
                and self.state is not State.ALARMING):
            self.state = State.ALARMING
            self.alarm_reason = AlarmReason.CONNECTION_LOST
            events.append(Event(
                EventKind.ALARM_START,
                reason=AlarmReason.CONNECTION_LOST,
                detail=f"plug unreachable for {downtime:.0f}s",
            ))

        return events

    # ------------------------------------------------------------------ #

    def _stop_alarm(self, now: float, why: str,
                    watts: float | None = None) -> Event:
        self.state = State.ARMED
        stopped = self.alarm_reason
        self.alarm_reason = None
        self._reset_timers(now)
        return Event(EventKind.ALARM_STOP, reason=stopped, watts=watts,
                     detail=why)

    def _reset_timers(self, now: float) -> None:
        self._last_activity_at = now
        self._high_watt_since = None
