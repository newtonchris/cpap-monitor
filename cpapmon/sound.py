"""Local siren via pw-play (PipeWire), sox `play` fallback — the PRIMARY alarm channel.

The siren loops until explicitly stopped: a player thread optionally plays
an intro file ONCE (the gentle nudge, e.g. a song), then re-launches the
alarm file each time it ends, so even a short bed means a continuous,
never-ending alarm. The intro is NOT part of the loop -- it is heard once,
then only the escalating alarm bed repeats. stop() kills the current
process and ends the loop.

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
    def __init__(self, alarm_file: str, intro_file: str = "",
                 ramp_file: str = ""):
        self.alarm_file = alarm_file
        self.intro_file = intro_file
        self.ramp_file = ramp_file
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
        # Prelude files play exactly ONCE each, in order, before the loop:
        #   1. intro_file -- the gentle nudge (a song), faded up over its start
        #   2. ramp_file  -- a faded-in FIRST PASS of the alarm bed, so the
        #      alarms themselves ramp 0->100% once before the loop runs at full
        # Both are best-effort: if one is unset, missing, or fails, we fall
        # straight through -- the siren must never be blocked by a prelude.
        for label, path in (("intro", self.intro_file), ("ramp", self.ramp_file)):
            if not path or self._stop.is_set():
                continue
            argv = _player_argv(path)
            if argv is None:
                continue
            try:
                self._proc = subprocess.Popen(
                    argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._proc.wait()
            except Exception as exc:
                log.error("siren %s error (continuing to alarm): %s", label, exc)

        # The alarm bed loops forever until stop() -- this is the wake-up.
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
