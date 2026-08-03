"""Backend factory. One config key ("backend") selects the implementation."""

from __future__ import annotations

from .base import Backend, PlugUnreachable

BACKEND_NAMES = ("tasmota",)


def make_backend(cfg) -> Backend:
    """Build the backend selected by cfg.backend from an AppConfig."""
    if cfg.backend == "tasmota":
        from .tasmota import TasmotaBackend
        t = cfg.tasmota
        return TasmotaBackend(host=t.host, timeout_s=t.timeout_s,
                              user=t.user, password=t.password)
    raise ValueError(
        f"unknown backend {cfg.backend!r}; expected one of {BACKEND_NAMES}")


__all__ = ["Backend", "PlugUnreachable", "make_backend", "BACKEND_NAMES"]
