"""Backend contract: a plug backend is anything with read_watts().

The entire hardware surface of this project is one method. Everything above
it (state machine, runner, CLI) is backend-agnostic.
"""

from __future__ import annotations

import abc


class PlugUnreachable(Exception):
    """Raised by a backend when the plug cannot be reached or parsed.

    The runner converts this into Monitor.on_unreachable(); once armed,
    sustained unreachability is itself an alarm condition.
    """


class Backend(abc.ABC):
    """A source of real-time plug wattage."""

    name: str = "abstract"

    @abc.abstractmethod
    def read_watts(self) -> float:
        """Return the plug's instantaneous power draw in watts.

        Raises PlugUnreachable on any failure (network, timeout, bad
        payload). Must never block much longer than its configured timeout.
        """

    def describe(self) -> str:
        """One-line human description for logs / `check` output."""
        return self.name
