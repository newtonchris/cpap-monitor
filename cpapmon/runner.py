"""The run loop: wires backend -> state machine -> side effects.

All alarm DECISIONS live in state.Monitor (pure, tested). This module only
polls the backend and executes the resulting events: siren, logs,
notifications, heartbeat.
"""

from __future__ import annotations

import logging
import time

from .backends import make_backend
from .backends.base import PlugUnreachable
from .config import AppConfig
from .heartbeat import Heartbeat
from .notify import Notifier
from .sound import AlarmSound
from .state import EventKind, Monitor, State

log = logging.getLogger("cpapmon.runner")


class Runner:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.backend = make_backend(cfg)
        self.monitor = Monitor(thresholds=cfg.thresholds)
        self.sound = AlarmSound(cfg.sound.alarm_file)
        self.notifier = Notifier(cfg.pushover)
        self.heartbeat = Heartbeat(cfg.heartbeat_path(),
                                   cfg.monitor.heartbeat_interval_s)

    # ------------------------------------------------------------------ #

    def run_forever(self) -> None:
        m = self.cfg.monitor
        log.info("cpap-monitor starting: backend=%s (%s)",
                 self.backend.name, self.backend.describe())
        log.info("thresholds: no-breath < %.1fW/%ds, runaway > %.1fW/%ds, "
                 "connection-lost > %ds",
                 self.cfg.thresholds.activity_watt_limit,
                 int(self.cfg.thresholds.no_activity_timeout_s),
                 self.cfg.thresholds.high_watt_limit,
                 int(self.cfg.thresholds.high_watt_timeout_s),
                 int(self.cfg.thresholds.connection_timeout_s))

        if m.startup_delay_s > 0:
            log.info("startup delay %ds (letting Wi-Fi/AP settle)...",
                     int(m.startup_delay_s))
            time.sleep(m.startup_delay_s)

        log.info("SEARCHING — silent standby until the machine is running")
        self.heartbeat.beat(self.monitor.state.value, None, force=True)

        watts: float | None = None
        while True:
            now = time.time()
            try:
                watts = self.backend.read_watts()
                events = self.monitor.on_reading(now, watts)
            except PlugUnreachable as exc:
                watts = None
                events = self.monitor.on_unreachable(now)
                if self.monitor.state is not State.SEARCHING:
                    log.warning("plug unreachable: %s", exc)

            for event in events:
                self._handle(event)

            self.heartbeat.beat(self.monitor.state.value, watts)

            if watts is None:
                time.sleep(m.retry_interval_s)
            else:
                time.sleep(m.poll_interval_s)

    # ------------------------------------------------------------------ #

    def _handle(self, event) -> None:
        kind = event.kind

        if kind is EventKind.ARMED:
            # The anti-silent-failure "I am actually watching you" line.
            log.info("ARMED — machine running (%.2f W); monitoring is LIVE",
                     event.watts)
            self.heartbeat.beat(self.monitor.state.value, event.watts,
                                force=True)
            self.notifier.notify("Armed",
                                 f"CPAP monitor armed via {self.backend.name}"
                                 f" ({event.watts:.1f} W)")

        elif kind is EventKind.ALARM_START:
            reason = event.reason.value if event.reason else "?"
            log.critical("ALARM_START %s — %s", reason, event.detail)
            self.sound.start()
            self.heartbeat.beat(self.monitor.state.value, event.watts,
                                force=True)
            self.notifier.notify(f"ALARM: {reason}", event.detail or reason,
                                 priority=1)

        elif kind is EventKind.ALARM_STOP:
            reason = event.reason.value if event.reason else "?"
            log.warning("ALARM_STOP (%s) — %s", reason, event.detail)
            self.sound.stop()
            self.heartbeat.beat(self.monitor.state.value, event.watts,
                                force=True)
            self.notifier.notify("Alarm cleared",
                                 f"{reason}: {event.detail}")

        elif kind is EventKind.UNREACHABLE:
            log.warning("plug missed a poll; retrying every %ss "
                        "(alarm at %ds of downtime)",
                        int(self.cfg.monitor.retry_interval_s),
                        int(self.cfg.thresholds.connection_timeout_s))

        elif kind is EventKind.RECONNECTED:
            log.info("plug reachable again (%.2f W)", event.watts or 0.0)
