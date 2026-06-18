"""Bundled sound effects for the GUI.

Currently just `1up.wav` — the SMB "1-Up" extra-life jingle, used as an
audible confirmation that a QR was decoded. Sourced from a public game-
sound archive; intended for personal/lab use only.

Playback goes through a small ladder of system players to handle the
common Linux audio stacks without pulling in PySide6-Addons / a Python
audio library:

    pw-play  (PipeWire native — works when PipeWire owns the device)
    paplay   (PulseAudio / pipewire-pulse compatibility)
    aplay    (ALSA direct — usually works only when nothing else has
              grabbed the card)

The original Brother lab box runs PipeWire, where `aplay` silently
loses to the audio server and produces no output. The ladder is
ordered so the most-likely-to-work player runs first.

Playback is fire-and-forget via `subprocess.Popen`; the GUI thread
doesn't wait for the player to exit. Missing player binaries or a
missing WAV file silently no-op — a missing chirp must never break
scanning.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path

_SOUND_DIR = Path(__file__).resolve().parent
ONE_UP_WAV = _SOUND_DIR / "1up.wav"

# Tried in order. First one on PATH wins. `pw-play` first because the
# common dev/lab box runs PipeWire (where aplay loses the device).
_PLAYER_PREFERENCE: tuple[str, ...] = ("pw-play", "paplay", "aplay")


def _resolve_player() -> tuple[str, list[str]] | None:
    """Pick the first available player; return (binary_path, extra_args)."""
    for name in _PLAYER_PREFERENCE:
        path = shutil.which(name)
        if path is None:
            continue
        # `aplay` benefits from -q to keep stderr quiet; the other two
        # are quiet by default.
        extra = ["-q"] if name == "aplay" else []
        return path, extra
    return None


def play_scan_sound() -> None:
    """Fire-and-forget play of the 1-Up sound. Errors are swallowed."""
    resolved = _resolve_player()
    if resolved is None or not ONE_UP_WAV.exists():
        return
    binary, extra = resolved
    with contextlib.suppress(OSError):
        subprocess.Popen(  # noqa: S603 — `binary` path comes from shutil.which
            [binary, *extra, str(ONE_UP_WAV)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
