"""Remote notifications: Pushover, with a log-line fallback.

Design decisions:

* Pushover replaces the old burner-Gmail SMTP path. Email is intentionally
  dropped (app-password rot + SMTP flakiness made it a silent-failure
  vector).
* Notifications are best-effort and NEVER block or crash the monitor: the
  local siren is the primary alarm, remote push is a bonus. Every send runs
  in a daemon thread and swallows its own errors into the log.
* If Pushover is disabled or the token is still a REPLACE_ME placeholder,
  notify() degrades to a log line — the monitor still runs.

Future work (see docs/DESIGN.md, anti-silent-failure plan): a morning
"still armed" push so that a dead monitor is noticed within hours, not
after 78 days.
"""

from __future__ import annotations

import logging
import threading
import urllib.parse
import urllib.request

from .config import PushoverConfig

log = logging.getLogger("cpapmon.notify")

PUSHOVER_API = "https://api.pushover.net/1/messages.json"


class Notifier:
    def __init__(self, cfg: PushoverConfig):
        self.cfg = cfg

    @property
    def active(self) -> bool:
        c = self.cfg
        return (c.enabled and c.token and c.user_key
                and "REPLACE_ME" not in c.token
                and "REPLACE_ME" not in c.user_key)

    def notify(self, title: str, message: str, priority: int = 0) -> None:
        """Fire-and-forget push. priority 1 = high (alarms)."""
        if not self.active:
            log.info("NOTIFY (pushover disabled) [%s] %s", title, message)
            return

        def _send() -> None:
            try:
                body = urllib.parse.urlencode({
                    "token": self.cfg.token,
                    "user": self.cfg.user_key,
                    "title": f"[CPAP] {title}",
                    "message": message,
                    "priority": priority,
                }).encode()
                req = urllib.request.Request(PUSHOVER_API, data=body)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp.read()
                log.info("NOTIFY sent: %s", title)
            except Exception as exc:
                log.error("NOTIFY failed (%s): %s", title, exc)

        threading.Thread(target=_send, daemon=True).start()
