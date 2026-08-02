"""Heartbeat/status file — first line of anti-silent-failure defence.

Every heartbeat_interval_s the runner writes a small JSON status file
(atomic tmp+rename). Anything external — a cron job, another host, a
Zabbix item, a human with `cat` — can then answer the one question that
matters: "is the alarm actually alive and armed RIGHT NOW?"

A stale mtime (older than ~2x the interval) or a missing file means the
monitor is dead, regardless of what systemd thinks. See docs/DESIGN.md
for the fuller external-watchdog plan.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class Heartbeat:
    def __init__(self, path: str, interval_s: float = 30.0):
        self.path = Path(path)
        self.interval_s = interval_s
        self._last_write = 0.0

    def beat(self, state: str, watts: float | None, *,
             force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_write) < self.interval_s:
            return
        payload = {
            "ts": now,
            "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "state": state,
            "watts": watts,
            "pid": os.getpid(),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload) + "\n")
            os.replace(tmp, self.path)
            self._last_write = now
        except OSError:
            # A failed heartbeat must never take down the monitor itself.
            pass
