"""Kasa (TP-Link) backend — home/secondary option.

Uses python-kasa. Connection strategy (ported from the field-proven script):

1. Try the pinned IP first (fast, deterministic on the Pi-hosted network).
2. Fall back to LAN discovery by alias, so a DHCP-moved plug is still found.

Requires a TP-Link cloud account's credentials for newer (KLAP-protocol)
plugs even for purely local reads — one reason Tasmota is the preferred
backend. python-kasa is imported lazily so the tasmota-only install never
needs it.

Watt thresholds remain voltage-independent here too, but Kasa hardware
sold in the US is typically 110 V-only — another reason Tasmota is the
travel backend.
"""

from __future__ import annotations

import asyncio

from .base import Backend, PlugUnreachable


class KasaBackend(Backend):
    name = "kasa"

    def __init__(self, alias: str, host: str = "",
                 username: str = "", password: str = "",
                 timeout_s: float = 5.0):
        if not alias and not host:
            raise ValueError("kasa backend requires an alias and/or host")
        self.alias = alias
        self.host = host
        self.username = username
        self.password = password
        self.timeout_s = timeout_s
        self._dev = None  # cached kasa device between polls

    # -- async internals ------------------------------------------------ #

    def _credentials(self):
        from kasa import Credentials
        if self.username and self.password:
            return Credentials(self.username, self.password)
        return None

    async def _connect(self):
        """Pinned IP first, then discovery-by-alias fallback."""
        from kasa import Discover

        creds = self._credentials()

        if self.host:
            try:
                dev = await Discover.discover_single(
                    self.host, credentials=creds, timeout=self.timeout_s)
                await dev.update()
                return dev
            except Exception:
                pass  # fall through to discovery

        found = await Discover.discover(
            credentials=creds, discovery_timeout=self.timeout_s)
        for dev in found.values():
            try:
                await dev.update()
            except Exception:
                continue
            if dev.alias == self.alias:
                return dev

        raise PlugUnreachable(
            f"kasa plug '{self.alias}' not found "
            f"(pinned host {self.host or 'none'}, discovery empty)")

    async def _read_async(self) -> float:
        from kasa import Module

        if self._dev is None:
            self._dev = await self._connect()
        try:
            await self._dev.update()
        except Exception as exc:
            # Drop the cached device; next poll re-discovers.
            self._dev = None
            raise PlugUnreachable(f"kasa update failed: {exc}") from exc

        dev = self._dev
        energy = dev.modules.get(Module.Energy) if hasattr(dev, "modules") else None
        power = None
        if energy is not None:
            power = energy.power
        elif hasattr(dev, "emeter_realtime"):
            power = dev.emeter_realtime.power

        if power is None:
            raise PlugUnreachable(
                f"kasa plug '{dev.alias}' reported no power reading")
        return float(power)

    # -- Backend contract ------------------------------------------------ #

    def read_watts(self) -> float:
        try:
            return asyncio.run(self._read_async())
        except PlugUnreachable:
            raise
        except Exception as exc:
            self._dev = None
            raise PlugUnreachable(f"kasa: {exc}") from exc

    def describe(self) -> str:
        return (f"kasa alias '{self.alias}' "
                f"(pinned host {self.host or 'discovery-only'})")
