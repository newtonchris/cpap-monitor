"""Tasmota backend — the only shipped backend.

Why Tasmota:

* **Local HTTP, no cloud.** One GET to the plug on the LAN. No vendor
  account, no credentials on disk beyond an optional web password, works
  with the internet down.
* **Voltage-independent, travel-safe.** The plug reports real measured
  watts, and watt thresholds mean the same thing at 110 V and 230 V mains.
  This is the backend to use abroad (e.g. Iceland/Europe) — same config,
  same thresholds, just plug adapters.
* **Simple failure surface.** HTTP timeout = unreachable. No discovery
  protocol, no cloud-auth token expiry.

Protocol:  GET http://<host>/cm?cmnd=Status%2010
Response:  {"StatusSNS": {"Time": "...", "ENERGY": {"Power": 12.3, ...}}}

Some Tasmota builds return the ENERGY object at the top level or under
SENSOR; extract_power_watts() accepts all three shapes (behaviour ported
from the field-proven script).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .base import Backend, PlugUnreachable


def extract_power_watts(payload: object) -> float:
    """Pull ENERGY.Power out of a Tasmota status payload, tolerantly."""
    if not isinstance(payload, dict):
        raise ValueError("Tasmota response was not a JSON object")

    candidates = [
        payload.get("StatusSNS"),
        payload,
        payload.get("SENSOR"),
    ]
    for container in candidates:
        if isinstance(container, dict):
            energy = container.get("ENERGY")
            if isinstance(energy, dict) and "Power" in energy:
                return float(energy["Power"])

    raise ValueError(f"No ENERGY.Power in Tasmota response: {payload!r}")


class TasmotaBackend(Backend):
    name = "tasmota"

    def __init__(self, host: str, timeout_s: float = 3.0,
                 user: str = "", password: str = ""):
        if not host:
            raise ValueError("tasmota backend requires a host")
        self.host = host
        self.timeout_s = timeout_s
        self.user = user
        self.password = password

    def _url(self, command: str) -> str:
        query = {"cmnd": command}
        if self.user and self.password:
            # Tasmota takes web credentials as query params on /cm.
            query = {"user": self.user, "password": self.password, **query}
        return f"http://{self.host}/cm?{urllib.parse.urlencode(query)}"

    def read_watts(self) -> float:
        req = urllib.request.Request(
            self._url("Status 10"),
            headers={"User-Agent": "cpap-monitor/1.0", "Connection": "close"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return extract_power_watts(json.loads(raw))
        except Exception as exc:  # timeout, refused, DNS, bad JSON, bad shape
            raise PlugUnreachable(f"tasmota {self.host}: {exc}") from exc

    def describe(self) -> str:
        return f"tasmota @ http://{self.host} (timeout {self.timeout_s}s)"
