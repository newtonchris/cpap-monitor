"""TOML config loading + validation (stdlib tomllib, Python >= 3.11).

The real config lives OUTSIDE the repo at /etc/cpap-monitor/config.toml
(mode 0640) because it contains secrets (Kasa account credentials, Pushover
token). The repo ships only config/config.example.toml with REPLACE_ME
placeholders. Never commit a real config.

Both backend sections ([tasmota] and [kasa]) are always present in the
file; only the one named by the top-level `backend` key is active. That
makes travel switchovers a one-line edit.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .state import Thresholds

DEFAULT_CONFIG_PATH = "/etc/cpap-monitor/config.toml"

_PLACEHOLDER = "REPLACE_ME"


class ConfigError(Exception):
    """Raised when the config file is missing, unparsable, or invalid."""


@dataclass(frozen=True)
class TasmotaConfig:
    host: str = ""
    timeout_s: float = 3.0
    user: str = ""          # only if a Tasmota web password is set
    password: str = ""


@dataclass(frozen=True)
class KasaConfig:
    alias: str = ""
    host: str = ""          # pinned IP; discovery-by-alias is the fallback
    username: str = ""      # TP-Link cloud account email
    password: str = ""
    timeout_s: float = 5.0


@dataclass(frozen=True)
class MonitorConfig:
    poll_interval_s: float = 1.0
    retry_interval_s: float = 15.0    # poll cadence while unreachable
    startup_delay_s: float = 30.0     # let Wi-Fi/AP settle after boot
    heartbeat_path: str = "/run/user/%(uid)s/cpap-monitor-status.json"
    heartbeat_interval_s: float = 30.0


@dataclass(frozen=True)
class SoundConfig:
    alarm_file: str = ""    # mp3/wav played (looped) via sox `play`


@dataclass(frozen=True)
class PushoverConfig:
    enabled: bool = False
    token: str = ""
    user_key: str = ""


@dataclass(frozen=True)
class AppConfig:
    backend: str = "tasmota"
    thresholds: Thresholds = field(default_factory=Thresholds)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    sound: SoundConfig = field(default_factory=SoundConfig)
    tasmota: TasmotaConfig = field(default_factory=TasmotaConfig)
    kasa: KasaConfig = field(default_factory=KasaConfig)
    pushover: PushoverConfig = field(default_factory=PushoverConfig)

    # ------------------------------------------------------------------ #

    def heartbeat_path(self) -> str:
        return self.monitor.heartbeat_path % {"uid": os.getuid()}

    def validate(self) -> list[str]:
        """Return a list of problems; empty list means the config is sane."""
        problems = list(self.thresholds.validate())

        if self.backend not in ("tasmota", "kasa"):
            problems.append(
                f"backend must be 'tasmota' or 'kasa', got {self.backend!r}")

        if self.backend == "tasmota" and not self.tasmota.host:
            problems.append("[tasmota] host is required (backend = tasmota)")
        if self.backend == "kasa":
            if not (self.kasa.alias or self.kasa.host):
                problems.append(
                    "[kasa] needs alias and/or host (backend = kasa)")
            for key in ("username", "password"):
                if _PLACEHOLDER in getattr(self.kasa, key):
                    problems.append(
                        f"[kasa] {key} still contains {_PLACEHOLDER}")

        if not self.sound.alarm_file:
            problems.append("[sound] alarm_file is required "
                            "(the local siren is the primary alarm)")
        elif not Path(self.sound.alarm_file).is_file():
            problems.append(
                f"[sound] alarm_file not found: {self.sound.alarm_file}")

        if self.pushover.enabled:
            for key in ("token", "user_key"):
                value = getattr(self.pushover, key)
                if not value or _PLACEHOLDER in value:
                    problems.append(
                        f"[notify.pushover] enabled but {key} is not set")

        for name in ("poll_interval_s", "retry_interval_s",
                     "heartbeat_interval_s"):
            if getattr(self.monitor, name) <= 0:
                problems.append(f"[monitor] {name} must be > 0")

        return problems


def _section(data: dict, *keys: str) -> dict:
    for key in keys:
        data = data.get(key, {})
        if not isinstance(data, dict):
            raise ConfigError(f"config section {'.'.join(keys)!r} "
                              "must be a table")
    return data


def load(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load and parse the TOML config. Does NOT validate; call .validate()."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(
            f"config file not found: {path}\n"
            f"Copy config/config.example.toml there and fill in REPLACE_ME "
            f"values (see docs/DEPLOY.md).")
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    def build(cls, section: dict):
        allowed = cls.__dataclass_fields__
        unknown = set(section) - set(allowed)
        if unknown:
            raise ConfigError(
                f"unknown key(s) {sorted(unknown)} in a [{cls.__name__}]-"
                f"backed section of {path}")
        return cls(**section)

    return AppConfig(
        backend=data.get("backend", "tasmota"),
        thresholds=build(Thresholds, _section(data, "thresholds")),
        monitor=build(MonitorConfig, _section(data, "monitor")),
        sound=build(SoundConfig, _section(data, "sound")),
        tasmota=build(TasmotaConfig, _section(data, "tasmota")),
        kasa=build(KasaConfig, _section(data, "kasa")),
        pushover=build(PushoverConfig, _section(data, "notify", "pushover")),
    )
