"""Snooze-gesture tests. Pure logic, virtual clock, no hardware/audio.

Runs under pytest AND as a plain script (`python3 tests/test_snooze.py`) so it
can be checked on the Pi, which has no pytest.

The gesture is DURATION-based: pressing Stop on the CPAP leaves it in standby at
~3-4 W, the same band as a breathing dip, so only a power level HELD for
>= debounce_s counts as a real transition. The deliberate gesture is
Stop(hold) / Start(hold) / Stop -- three sustained edges ending OFF. A passive
failure (watts drop and stay down) is a single off edge and can never arm.
"""
import dataclasses
from cpapmon.state import Monitor, Thresholds, State, EventKind, AlarmReason

T = Thresholds(activity_watt_limit=4.3, no_activity_timeout_s=60,
               high_watt_limit=18.5, high_watt_timeout_s=60,
               connection_timeout_s=180, alarm_auto_standby_s=600,
               snooze_s=300, snooze_gesture_edges=3,
               snooze_gesture_window_s=30, snooze_gesture_debounce_s=3)

BREATH, STANDBY, RUNAWAY = 11.0, 3.5, 25.0   # STANDBY = measured Stop draw (3-4 W)
HOLD = 4                                      # s per flick; > debounce (3 s)

def kinds(evs): return [e.kind for e in evs]

def feed(m, t, watts, secs, step=1.0):
    evs = []
    end = t + secs
    while t < end:
        evs += m.on_reading(t, watts); t += step
    return evs, t

def _do_gesture(m, t0):
    """Stop / Start / Stop, each held > debounce -> arms, ending OFF."""
    evs, t = feed(m, t0, BREATH,  HOLD)   # machine running -> ARMED + 'on' baseline
    e, t = feed(m, t, STANDBY, HOLD); evs += e   # Stop  (off edge 1)
    e, t = feed(m, t, BREATH,  HOLD); evs += e   # Start (on  edge 2, blower restart)
    e, t = feed(m, t, STANDBY, HOLD); evs += e   # Stop  (off edge 3) -> arm
    return evs, t

def test_gesture_arms_and_suppresses():
    m = Monitor(thresholds=T)
    a, t = _do_gesture(m, 0.0)
    assert EventKind.SNOOZE_ARMED in kinds(a), kinds(a)
    evs, t = feed(m, t, STANDBY, 120)          # stopped past the 60 s no-breath timeout
    assert EventKind.ALARM_START not in kinds(evs)   # suppressed
    assert m.state is State.ARMED

def test_snooze_expires_then_alarms():
    m = Monitor(thresholds=dataclasses.replace(T, snooze_s=30))
    a, t = _do_gesture(m, 0.0)
    assert EventKind.SNOOZE_ARMED in kinds(a)
    evs, t = feed(m, t, STANDBY, 200)
    k = kinds(evs)
    assert EventKind.SNOOZE_EXPIRED in k        # grace ended
    assert EventKind.ALARM_START in k           # still stopped -> wakes you
    assert m.state is State.ALARMING

def test_resume_clears_snooze():
    m = Monitor(thresholds=T)
    a, t = _do_gesture(m, 0.0)
    assert EventKind.SNOOZE_ARMED in kinds(a)
    _, t = feed(m, t, BREATH, 2)                # machine back on
    assert m._snooze_until is None
    evs, t = feed(m, t, STANDBY, 70)            # later genuine stop
    assert EventKind.ALARM_START in kinds(evs)  # snooze did not linger

def test_single_stop_is_not_a_gesture():
    m = Monitor(thresholds=T)
    _, t = feed(m, 0.0, BREATH, HOLD)           # ARMED
    evs, t = feed(m, t, STANDBY, 70)            # one sustained stop (failure shape)
    k = kinds(evs)
    assert EventKind.SNOOZE_ARMED not in k      # one off edge != gesture
    assert EventKind.ALARM_START in k           # and it still wakes you

def test_breathing_jitter_does_not_arm():
    """The overnight false-alarm bug: breathing dips briefly below the activity
    line but the blower keeps running, so each dip is a 1-sample transient that
    must NOT count as a Stop."""
    m = Monitor(thresholds=T)
    _, t = feed(m, 0.0, BREATH, HOLD)           # ARMED, 'on' baseline
    evs = []
    for _ in range(15):                         # ~45 s of jittery breathing
        e, t = feed(m, t, BREATH, 2); evs += e      # running
        e, t = feed(m, t, STANDBY, 1); evs += e     # a single-sample dip
    assert EventKind.SNOOZE_ARMED not in kinds(evs)  # jitter never arms
    assert EventKind.ALARM_START not in kinds(evs)   # and never false-alarms
    assert m._snooze_until is None

def test_single_blip_is_not_a_gesture():
    m = Monitor(thresholds=T)
    _, t = feed(m, 0.0, BREATH, HOLD)
    m.on_reading(t, STANDBY); t += 1            # 1 s dip (< debounce)
    m.on_reading(t, BREATH); t += 1
    evs, t = feed(m, t, BREATH, 5)
    assert EventKind.SNOOZE_ARMED not in kinds(evs)

def test_snooze_does_not_suppress_runaway():
    m = Monitor(thresholds=T)
    a, t = _do_gesture(m, 0.0)
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
