"""State-machine tests. Pure logic, virtual clock, zero hardware.

Run:  pytest tests/          (from the repo root)
"""

import pytest

from cpapmon.state import (
    AlarmReason,
    Event,
    EventKind,
    Monitor,
    State,
    Thresholds,
)

# Small, readable thresholds for tests (semantics identical to production).
T = Thresholds(
    activity_watt_limit=4.3,
    no_activity_timeout_s=60,
    high_watt_limit=18.5,
    high_watt_timeout_s=60,
    connection_timeout_s=180,
)

BREATHING = 11.0   # normal in-band draw
IDLE = 1.0         # machine off / flatline
RUNAWAY = 25.0     # blower screaming


def kinds(events):
    return [e.kind for e in events]


def feed(monitor, start, seconds, watts, step=1.0):
    """Feed a constant wattage for `seconds`; return (events, end_time)."""
    all_events = []
    now = start
    end = start + seconds
    while now <= end:
        all_events.extend(monitor.on_reading(now, watts))
        now += step
    return all_events, now


def feed_down(monitor, start, seconds, step=5.0):
    all_events = []
    now = start
    end = start + seconds
    while now <= end:
        all_events.extend(monitor.on_unreachable(now))
        now += step
    return all_events, now


# --------------------------------------------------------------------- #
# arming
# --------------------------------------------------------------------- #

def test_starts_searching():
    m = Monitor(thresholds=T)
    assert m.state is State.SEARCHING


def test_first_reading_arms():
    m = Monitor(thresholds=T)
    events = m.on_reading(0.0, BREATHING)
    assert kinds(events) == [EventKind.ARMED]
    assert m.state is State.ARMED


def test_searching_is_silent_on_unreachable():
    """Standby: an unpowered plug at startup must never alarm."""
    m = Monitor(thresholds=T)
    events, _ = feed_down(m, 0.0, 10_000)
    assert events == []
    assert m.state is State.SEARCHING


def test_idle_plug_stays_in_silent_standby():
    """arm-after-active: a reachable-but-idle plug (machine off, e.g. a daytime
    reboot) must NOT arm and must NEVER alarm. This is what makes the monitor
    safe to auto-start on boot."""
    m = Monitor(thresholds=T)
    events = m.on_reading(0.0, IDLE)
    assert events == []                      # no ARMED event
    assert m.state is State.SEARCHING
    # Hours of idle: still silent, never arms, never alarms.
    events, _ = feed(m, 1.0, 10_000, IDLE)
    assert events == []
    assert m.state is State.SEARCHING


def test_arms_only_after_machine_runs():
    """Idle first (silent), then the machine actually runs -> ARM."""
    m = Monitor(thresholds=T)
    assert m.on_reading(0.0, IDLE) == []     # standby
    events = m.on_reading(5.0, BREATHING)    # machine started
    assert kinds(events) == [EventKind.ARMED]
    assert m.state is State.ARMED


def test_no_breath_auto_stands_down():
    """Once ARMED, a NO_BREATH alarm that rings past alarm_auto_standby_s with
    no recovery stands down to silent SEARCHING (no empty-room siren all day),
    and re-arms only when the machine runs again."""
    Ts = Thresholds(activity_watt_limit=4.3, no_activity_timeout_s=60,
                    high_watt_limit=18.5, high_watt_timeout_s=60,
                    connection_timeout_s=180, alarm_auto_standby_s=300)
    m = Monitor(thresholds=Ts)
    _, now = feed(m, 0.0, 30, BREATHING)     # arm
    events, now = feed(m, now, 90, IDLE)     # -> NO_BREATH alarm
    assert AlarmReason.NO_BREATH in [e.reason for e in events
                                     if e.kind is EventKind.ALARM_START]
    assert m.state is State.ALARMING
    events, now = feed(m, now, 320, IDLE)    # keep idle past the stand-down
    assert EventKind.ALARM_STOP in kinds(events)
    assert m.state is State.SEARCHING        # silent standby, awaiting re-arm
    # ...and it re-arms when the machine runs again:
    assert kinds(m.on_reading(now + 1, BREATHING)) == [EventKind.ARMED]


# --------------------------------------------------------------------- #
# no-breath
# --------------------------------------------------------------------- #

def test_no_breath_alarm_fires_after_timeout():
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 120, BREATHING)
    events, _ = feed(m, now, 90, IDLE)
    starts = [e for e in events if e.kind is EventKind.ALARM_START]
    assert len(starts) == 1
    assert starts[0].reason is AlarmReason.NO_BREATH
    assert m.state is State.ALARMING


def test_no_breath_not_fired_before_timeout():
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 120, BREATHING)
    events, _ = feed(m, now, 45, IDLE)  # under the 60s window
    assert EventKind.ALARM_START not in kinds(events)
    assert m.state is State.ARMED


def test_brief_dips_do_not_alarm():
    """Breathing modulates the draw; short sub-threshold dips are normal."""
    m = Monitor(thresholds=T)
    now = 0.0
    m.on_reading(now, BREATHING)
    for _ in range(50):  # 30s dip, 5s breath, repeated
        _, now = feed(m, now, 30, IDLE)
        _, now = feed(m, now, 5, BREATHING)
    assert m.state is State.ARMED


def test_no_breath_recovery_stops_alarm():
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 10, BREATHING)
    _, now = feed(m, now, 90, IDLE)
    assert m.state is State.ALARMING
    events = m.on_reading(now, BREATHING)
    stops = [e for e in events if e.kind is EventKind.ALARM_STOP]
    assert len(stops) == 1
    assert stops[0].reason is AlarmReason.NO_BREATH
    assert m.state is State.ARMED


def test_timers_reset_after_recovery():
    """After an alarm clears, the full window applies again — no instant
    re-trigger off stale timestamps."""
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 10, BREATHING)
    _, now = feed(m, now, 90, IDLE)          # alarm
    m.on_reading(now, BREATHING)             # recover
    events, _ = feed(m, now + 1, 30, IDLE)   # 30s idle: within window
    assert EventKind.ALARM_START not in kinds(events)


# --------------------------------------------------------------------- #
# runaway
# --------------------------------------------------------------------- #

def test_runaway_alarm_fires_after_timeout():
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 10, BREATHING)
    events, _ = feed(m, now, 90, RUNAWAY)
    starts = [e for e in events if e.kind is EventKind.ALARM_START]
    assert len(starts) == 1
    assert starts[0].reason is AlarmReason.RUNAWAY


def test_deep_breath_spikes_do_not_alarm():
    """Short high-watt spikes (deep breaths) must not trigger runaway."""
    m = Monitor(thresholds=T)
    now = 0.0
    m.on_reading(now, BREATHING)
    for _ in range(20):
        _, now = feed(m, now, 10, RUNAWAY)    # 10s spike
        _, now = feed(m, now, 5, BREATHING)   # resets the high-watt window
    assert m.state is State.ARMED


def test_runaway_recovery():
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 10, BREATHING)
    _, now = feed(m, now, 90, RUNAWAY)
    assert m.alarm_reason is AlarmReason.RUNAWAY
    events = m.on_reading(now, BREATHING)
    assert EventKind.ALARM_STOP in kinds(events)
    assert m.state is State.ARMED


# --------------------------------------------------------------------- #
# connection lost
# --------------------------------------------------------------------- #

def test_connection_lost_alarm_once_armed():
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 10, BREATHING)
    events, _ = feed_down(m, now, 300)
    starts = [e for e in events if e.kind is EventKind.ALARM_START]
    assert len(starts) == 1
    assert starts[0].reason is AlarmReason.CONNECTION_LOST
    assert m.state is State.ALARMING


def test_short_outage_does_not_alarm():
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 10, BREATHING)
    events, now = feed_down(m, now, 120)  # under the 180s budget
    assert EventKind.ALARM_START not in kinds(events)
    events = m.on_reading(now, BREATHING)
    assert EventKind.RECONNECTED in kinds(events)
    assert m.state is State.ARMED


def test_connection_restored_clears_alarm_and_rearms():
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 10, BREATHING)
    _, now = feed_down(m, now, 300)
    assert m.state is State.ALARMING
    events = m.on_reading(now, BREATHING)
    assert EventKind.RECONNECTED in kinds(events)
    assert EventKind.ALARM_STOP in kinds(events)
    assert m.state is State.ARMED
    # And the idle window restarted: no instant no-breath alarm.
    events, _ = feed(m, now + 1, 30, IDLE)
    assert EventKind.ALARM_START not in kinds(events)


def test_unreachable_while_alarming_keeps_alarming():
    """Losing the plug mid-alarm must never silence the siren."""
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 10, BREATHING)
    _, now = feed(m, now, 90, IDLE)  # NO_BREATH alarm
    assert m.state is State.ALARMING
    events, _ = feed_down(m, now, 600)
    assert EventKind.ALARM_STOP not in kinds(events)
    assert m.state is State.ALARMING


def test_reconnect_to_idle_plug_rearms_no_breath():
    """Connection alarm clears on reconnect, but if the CPAP is actually
    off, the no-breath alarm must follow within its window."""
    m = Monitor(thresholds=T)
    _, now = feed(m, 0.0, 10, BREATHING)
    _, now = feed_down(m, now, 300)          # CONNECTION_LOST alarm
    events, _ = feed(m, now, 90, IDLE)       # plug back, machine off
    stops = [e for e in events if e.kind is EventKind.ALARM_STOP]
    starts = [e for e in events if e.kind is EventKind.ALARM_START]
    assert stops and stops[0].reason is AlarmReason.CONNECTION_LOST
    assert starts and starts[0].reason is AlarmReason.NO_BREATH


# --------------------------------------------------------------------- #
# thresholds
# --------------------------------------------------------------------- #

def test_default_thresholds_match_field_tuning():
    t = Thresholds()
    assert t.activity_watt_limit == pytest.approx(4.3)
    assert t.high_watt_limit == pytest.approx(18.5)
    assert t.no_activity_timeout_s == 60
    assert t.high_watt_timeout_s == 60
    assert t.connection_timeout_s == 180
    assert t.validate() == []


def test_threshold_validation_catches_nonsense():
    bad = Thresholds(activity_watt_limit=20.0, high_watt_limit=10.0)
    assert any("exceed" in p for p in bad.validate())
