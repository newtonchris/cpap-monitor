"""Backend factory. One config key ("backend") selects the implementation."""

from __future__ import annotations

from .base import Backend, PlugUnreachable

BACKEND_NAMES = ("tasmota", "kasa")


def make_backend(cfg) -> Backend:
    """Build the backend selected by cfg.backend from an AppConfig."""
    if cfg.backend == "tasmota":
        from .tasmota import TasmotaBackend
        t = cfg.tasmota
        return TasmotaBackend(host=t.host, timeout_s=t.timeout_s,
                              user=t.user, password=t.password)
    if cfg.backend == "kasa":
        from .kasa import KasaBackend
        k = cfg.kasa
        return KasaBackend(alias=k.alias, host=k.host,
                           username=k.username, password=k.password,
                           timeout_s=k.timeout_s)
    raise ValueError(
        f"unknown backend {cfg.backend!r}; expected one of {BACKEND_NAMES}")


__all__ = ["Backend", "PlugUnreachable", "make_backend", "BACKEND_NAMES"]
