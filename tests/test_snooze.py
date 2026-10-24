"""Snooze-gesture tests. Pure logic, virtual clock, no hardware/audio.

Runs under pytest AND as a plain script (`python3 tests/test_snooze.py`) so it
can be checked on the Pi, which has no pytest.
"""
import dataclasses
from cpapmon.state import Monitor, Thresholds, State, EventKind, AlarmReason

T = Thresholds(activity_watt_limit=4.3, no_activity_timeout_s=60,
               high_watt_limit=18.5, high_watt_timeout_s=60,
               connection_timeout_s=180, alarm_auto_standby_s=600,
               snooze_s=300, snooze_gesture_edges=3, snooze_gesture_window_s=12)
BREATH, IDLE, RUNAWAY = 11.0, 1.0, 25.0

def kinds(evs): return [e.kind for e in evs]

def feed(m, t, watts, secs, step=1.0):
    evs = []
    end = t + secs
    while t < end:
        evs += m.on_reading(t, watts); t += step
    return evs, t

def _arm_then_gesture(m, t):
    m.on_reading(t, BREATH); t += 1            # ARMED
    _, t = feed(m, t, BREATH, 4)
    m.on_reading(t, IDLE);   t += 1            # edge 1 (fall)
    m.on_reading(t, BREATH); t += 1            # edge 2 (rise)
    a = m.on_reading(t, IDLE); t += 1          # edge 3 (fall) -> arm
    return a, t

def test_gesture_arms_and_suppresses():
    m = Monitor(thresholds=T)
    a, t = _arm_then_gesture(m, 0.0)
    assert EventKind.SNOOZE_ARMED in kinds(a), kinds(a)
    evs, t = feed(m, t, IDLE, 120)             # idle past 60s timeout
    assert EventKind.ALARM_START not in kinds(evs)   # suppressed
    assert m.state is State.ARMED

def test_snooze_expires_then_alarms():
    m = Monitor(thresholds=dataclasses.replace(T, snooze_s=30))
    a, t = _arm_then_gesture(m, 0.0)
    assert EventKind.SNOOZE_ARMED in kinds(a)
    evs, t = feed(m, t, IDLE, 120)
    k = kinds(evs)
    assert EventKind.SNOOZE_EXPIRED in k        # grace ended
    assert EventKind.ALARM_START in k           # still-idle -> wakes you
    assert m.state is State.ALARMING

def test_resume_clears_snooze():
    m = Monitor(thresholds=T)
    a, t = _arm_then_gesture(m, 0.0)
    assert EventKind.SNOOZE_ARMED in kinds(a)
    m.on_reading(t, BREATH); t += 1             # machine back on
    assert m._snooze_until is None
    evs, t = feed(m, t, IDLE, 70)               # later genuine stop
    assert EventKind.ALARM_START in kinds(evs)  # snooze did not linger

def test_single_stop_is_not_a_gesture():
    m = Monitor(thresholds=T)
    m.on_reading(0.0, BREATH)
    evs, t = feed(m, 1.0, IDLE, 70)
    k = kinds(evs)
    assert EventKind.SNOOZE_ARMED not in k      # one edge != gesture
    assert EventKind.ALARM_START in k

def test_single_blip_is_not_a_gesture():
    m = Monitor(thresholds=T)
    m.on_reading(0.0, BREATH); t = 1.0
    m.on_reading(t, IDLE); t += 1               # edge 1
    m.on_reading(t, BREATH); t += 1             # edge 2  (<3)
    evs, t = feed(m, t, BREATH, 5)
    assert EventKind.SNOOZE_ARMED not in kinds(evs)

def test_snooze_does_not_suppress_runaway():
    m = Monitor(thresholds=T)
    a, t = _arm_then_gesture(m, 0.0)
    assert EventKind.SNOOZE_ARMED in kinds(a)
    evs, t = feed(m, t, RUNAWAY, 70)            # blower runaway during snooze
    assert EventKind.ALARM_START in kinds(evs)
    assert m.alarm_reason is AlarmReason.RUNAWAY

if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__)
        except AssertionError as e:
            bad += 1; print("FAIL", fn.__name__, "--", e)
    print(f"\n{len(fns)-bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
