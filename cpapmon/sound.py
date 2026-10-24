"""Local siren via pw-play (PipeWire), sox `play` fallback — the PRIMARY alarm channel.

The siren loops until explicitly stopped: a player thread re-launches
`play` each time the track ends, so a 3-minute mp3 still means a
continuous alarm. stop() kills the current process and ends the loop.

Requires the `sox` package with mp3 support (`sox` + `libsox-fmt-mp3`
on Raspberry Pi OS / Debian).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading

log = logging.getLogger("cpapmon.sound")


def play_chirp(chirp_file, count=2):
    """Play a short confirmation chirp `count` times (blocking). Best-effort:
    a missing player or file just means no chirp -- never raises."""
    import subprocess as _sp, time as _t
    argv = _player_argv(chirp_file)
    if argv is None:
        return
    for _ in range(max(1, count)):
        try:
            _sp.run(argv, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=5)
        except Exception:
            return
        _t.sleep(0.12)


def _player_argv(path):
    """Command to play ONE pass of the alarm file.

    Prefer `pw-play` (PipeWire): it reaches the session's default sink and
    survives the USB speaker re-enumerating mid-alarm, which raw-ALSA sox
    `play` does NOT — on this box `play` cannot even open a default device.
    Fall back to sox `play` only if pw-play is missing.
    """
    import shutil as _sh
    if _sh.which("pw-play"):
        return ["pw-play", path]
    if _sh.which("play"):
        return ["play", "-q", path, "vol", "1.0"]
    return None


class AlarmSound:
    def __init__(self, alarm_file: str):
        self.alarm_file = alarm_file
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.playing:
                return
            if _player_argv(self.alarm_file) is None:
                log.critical("no audio player (pw-play or sox `play`) found — "
                             "NO LOCAL SIREN. apt install pipewire-bin or sox")
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            log.info("siren started (%s)", self.alarm_file)

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
            self._thread = None
        log.info("siren stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    _player_argv(self.alarm_file),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._proc.wait()
            except Exception as exc:
                log.error("siren playback error: %s", exc)
                # Brief pause so a broken audio stack can't busy-loop.
                if self._stop.wait(timeout=5):
                    return
