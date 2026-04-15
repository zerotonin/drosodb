"""Live QR scan loop. Yields each distinct payload seen.

Debouncing rule: once a payload is decoded, it is not yielded again
until it has been OUT of view for `reset_after_s` seconds. This prevents
firing dozens of duplicate events while a vial is held up to the camera.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import cv2

from ddb.camera.capture import open_capture, resolve_role
from ddb.scanner.decoder import decode_image


def scan_loop(
    role: str,
    *,
    show_preview: bool = True,
    reset_after_s: float = 1.5,
    stop_after: int | None = None,
) -> Iterator[str]:
    """Yield decoded QR payloads one at a time.

    Press 'q' in the preview window (or close it) to stop. If `stop_after`
    is set, the loop exits after that many distinct payloads.
    """
    cam = resolve_role(role)
    last_seen: dict[str, float] = {}  # payload -> monotonic time last seen
    emitted = 0
    window = f"scan: {role}"

    with open_capture(cam.device_path) as cap:
        if show_preview:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break

                now = time.monotonic()
                payloads = decode_image(frame)

                fresh: list[str] = []
                for p in payloads:
                    prev = last_seen.get(p)
                    if prev is None or (now - prev) > reset_after_s:
                        fresh.append(p)
                    last_seen[p] = now

                # Drop stale entries so the dict doesn't grow unbounded.
                last_seen = {k: t for k, t in last_seen.items() if now - t <= reset_after_s}

                if show_preview:
                    _overlay(frame, payloads)
                    cv2.imshow(window, frame)
                    if (cv2.waitKey(1) & 0xFF) == ord("q"):
                        return
                    if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                        return

                for p in fresh:
                    yield p
                    emitted += 1
                    if stop_after is not None and emitted >= stop_after:
                        return
        finally:
            if show_preview:
                cv2.destroyWindow(window)


def _overlay(frame, payloads: list[str]) -> None:
    """Draw a small HUD listing what was just decoded."""
    if not payloads:
        return
    for i, p in enumerate(payloads[:3]):
        cv2.putText(
            frame,
            p[:72],
            (10, 30 + i * 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
