"""Network backend — TCP port 9100 (raw print protocol).

The QL-820NWB listens here once it's on ethernet/wifi. Identical wire format
to the Bluetooth backend; only the transport differs.
"""

from __future__ import annotations

import socket
import time

from ddb.printing.status import StatusBlock, decode_status_blocks


class NetworkTCPBackend:
    def __init__(self, host: str, port: int = 9100) -> None:
        self.host = host
        self.port = port

    @property
    def description(self) -> str:
        return f"tcp:{self.host}:{self.port}"

    def send(self, raster: bytes, *, timeout: float = 30.0) -> list[StatusBlock]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((self.host, self.port))
        try:
            # Small chunks are polite and keep the TCP buffer steady on
            # the embedded side (some Brother firmwares choke on huge sends).
            view = memoryview(raster)
            for off in range(0, len(view), 1024):
                s.sendall(view[off : off + 1024])
            return _drain_status(s, timeout=min(timeout, 12.0))
        finally:
            s.close()


def _drain_status(s: socket.socket, *, timeout: float) -> list[StatusBlock]:
    s.settimeout(2.0)
    buf = b""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            chunk = s.recv(256)
        except TimeoutError:
            continue
        if not chunk:
            break
        buf += chunk
        # Stop early if we've seen a terminal status.
        blocks = decode_status_blocks(buf)
        terminal = any(
            b.is_error or (b.status_type in (0x01, 0x06) and b.phase_type == 0x00) for b in blocks
        )
        if terminal:
            # Got an error OR the "phase back to waiting" block — job is over.
            # Small grace period in case more bytes are in flight.
            time.sleep(0.2)
            try:
                extra = s.recv(256)
                if extra:
                    buf += extra
            except (TimeoutError, OSError):
                pass
            break
    return decode_status_blocks(buf)
