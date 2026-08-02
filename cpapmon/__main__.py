"""CLI entry point: python3 -m cpapmon <command>

Commands
--------
run         Run the monitor (what the systemd unit executes).
check       Read the active backend ONCE and print watts. The fastest way
            to verify a plug is wired up and answering.
validate    Parse + sanity-check the config; exit non-zero on problems.
            Used as the systemd ExecStartPre gate.
simulate    Feed synthetic watt samples through the state machine with a
            virtual clock (no hardware, no sleeping) and print every event.
test-alert  Fire the real alert path on demand: siren for a few seconds
            plus a Pushover test message. Run this monthly.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import __version__
from .config import DEFAULT_CONFIG_PATH, AppConfig, ConfigError, load
from .state import EventKind, Monitor, State

log = logging.getLogger("cpapmon")

# A morning-after failure script: normal sleep, then the machine dies,
# then it is turned back on. Exercises ARM -> NO_BREATH alarm -> recovery.
DEFAULT_SCENARIO = "11:120,1:90,11:30"


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def _load_config(path: str, *, must_be_valid: bool) -> AppConfig:
    try:
        cfg = load(path)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if must_be_valid:
        problems = cfg.validate()
        if problems:
            print(f"CONFIG INVALID ({path}):", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            raise SystemExit(2)
    return cfg


# --------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------- #

def cmd_run(args) -> int:
    from .runner import Runner
    cfg = _load_config(args.config, must_be_valid=True)
    Runner(cfg).run_forever()
    return 0  # unreachable; run_forever loops


def cmd_check(args) -> int:
    from .backends import make_backend
    from .backends.base import PlugUnreachable
    cfg = _load_config(args.config, must_be_valid=False)
    backend = make_backend(cfg)
    print(f"backend: {backend.describe()}")
    try:
        started = time.monotonic()
        watts = backend.read_watts()
        elapsed = time.monotonic() - started
    except PlugUnreachable as exc:
        print(f"UNREACHABLE: {exc}")
        return 1
    print(f"watts: {watts:.2f}  ({elapsed * 1000:.0f} ms)")
    t = cfg.thresholds
    if watts > t.high_watt_limit:
        band = "RUNAWAY band (would alarm after sustained high draw)"
    elif watts > t.activity_watt_limit:
        band = "normal activity band"
    else:
        band = "no-breath band (idle/off; would alarm if sustained)"
    print(f"band:  {band}")
    return 0


def cmd_validate(args) -> int:
    _load_config(args.config, must_be_valid=True)
    print(f"OK: {args.config} is valid")
    return 0


def cmd_simulate(args) -> int:
    """Virtual-clock scenario runner for the pure state machine.

    Script format: comma-separated segments of WATTS:SECONDS, where WATTS
    may be the word 'down' for an unreachable plug. Example:
        --script "11:120,down:200,11:30"
    """
    monitor = Monitor()
    now = 0.0
    step = 1.0
    print(f"scenario: {args.script}   (virtual clock, 1s steps)")
    print(f"{'t(s)':>6}  {'input':>8}  event")

    for segment in args.script.split(","):
        raw_watts, _, raw_secs = segment.strip().partition(":")
        seconds = float(raw_secs or 1)
        end = now + seconds
        while now < end:
            if raw_watts.lower() == "down":
                events = monitor.on_unreachable(now)
                shown = "down"
            else:
                watts = float(raw_watts)
                events = monitor.on_reading(now, watts)
                shown = f"{watts:.1f}W"
            for ev in events:
                reason = f" {ev.reason.value}" if ev.reason else ""
                detail = f" ({ev.detail})" if ev.detail else ""
                print(f"{now:6.0f}  {shown:>8}  {ev.kind.value}{reason}{detail}")
            now += step

    print(f"final state: {monitor.state.value}"
          + (f" ({monitor.alarm_reason.value})" if monitor.alarm_reason else ""))
    return 0 if monitor.state is not State.ALARMING or args.expect_alarm else 1


def cmd_test_alert(args) -> int:
    """Prove the alert path end-to-end: siren + push. Run this monthly."""
    from .notify import Notifier
    from .sound import AlarmSound
    cfg = _load_config(args.config, must_be_valid=True)

    print(f"Sounding siren for {args.seconds}s: {cfg.sound.alarm_file}")
    siren = AlarmSound(cfg.sound.alarm_file)
    siren.start()
    time.sleep(args.seconds)
    siren.stop()

    notifier = Notifier(cfg.pushover)
    if notifier.active:
        print("Sending Pushover test message...")
    else:
        print("Pushover not configured; test message goes to the log only.")
    notifier.notify("Test alert", "cpap-monitor test-alert: if you can read "
                    "this, remote notifications work.")
    time.sleep(3)  # give the daemon thread a moment to deliver
    print("Done. Did you HEAR the siren and RECEIVE the push?")
    return 0


# --------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m cpapmon",
        description="CPAP failure alarm: smart-plug wattage watchdog.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                        help=f"config path (default {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--version", action="version",
                        version=f"cpap-monitor {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the monitor loop (systemd entry)")
    sub.add_parser("check", help="read the plug once and print watts")
    sub.add_parser("validate", help="sanity-check the config file")

    p_sim = sub.add_parser("simulate",
                           help="drive the state machine with synthetic watts")
    p_sim.add_argument("--script", default=DEFAULT_SCENARIO,
                       help="WATTS:SECONDS[,WATTS:SECONDS...]; 'down' = "
                            f"unreachable (default: {DEFAULT_SCENARIO})")
    p_sim.add_argument("--expect-alarm", action="store_true",
                       help="exit 0 even if the scenario ends ALARMING")

    p_test = sub.add_parser("test-alert",
                            help="sound the real siren + send a test push")
    p_test.add_argument("--seconds", type=int, default=5,
                        help="siren duration (default 5)")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    handlers = {
        "run": cmd_run,
        "check": cmd_check,
        "validate": cmd_validate,
        "simulate": cmd_simulate,
        "test-alert": cmd_test_alert,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
