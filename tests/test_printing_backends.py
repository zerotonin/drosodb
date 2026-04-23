"""Backend tests that don't need real hardware.

- FileBackend: writes raster to disk.
- NetworkTCPBackend: connects to a localhost loopback socket that records the
  payload and replies with a canned success trace.
- Bluetooth backend is not tested here (needs a paired printer).
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path

from ddb.printing.backends import FileBackend, NetworkTCPBackend


def test_file_backend_writes_raster(tmp_path: Path) -> None:
    be = FileBackend(tmp_path)
    result = be.send(b"hello raster")
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("job_") and files[0].suffix == ".bin"
    assert files[0].read_bytes() == b"hello raster"
    assert result == []


def test_network_backend_roundtrip() -> None:
    received: list[bytes] = []

    # Canned printer: 32-byte idle reply + 32-byte "printing-done" reply.
    done_block = bytes(
        [0x80, 0x20, 0x42, 0x34, 0x41, 0x30, 0, 0, 0, 0, 0x11, 0x0B,
         0, 0, 0, 0, 0, 0x36, 0x01, 0x01, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    )
    idle_block = bytes(
        [0x80, 0x20, 0x42, 0x34, 0x41, 0x30, 0, 0, 0, 0, 0x11, 0x0B,
         0, 0, 0, 0, 0, 0x36, 0x06, 0x00, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    )

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()

    def serve() -> None:
        c, _ = server.accept()
        try:
            c.settimeout(2.0)
            buf = b""
            while True:
                try:
                    chunk = c.recv(4096)
                except TimeoutError:
                    break
                if not chunk:
                    break
                buf += chunk
                if len(buf) >= 5:  # got the sentinel payload
                    break
            received.append(buf)
            c.sendall(done_block + idle_block)
        finally:
            c.close()
            server.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    be = NetworkTCPBackend(host=host, port=port)
    blocks = be.send(b"raster!", timeout=5.0)
    t.join(timeout=3.0)

    assert received and received[0] == b"raster!"
    # Should have collected both status blocks from the server.
    assert len(blocks) == 2
    assert blocks[0].is_done
    assert blocks[-1].phase_name == "waiting"


def test_file_backend_description() -> None:
    assert FileBackend(Path("/tmp/x")).description == "file:/tmp/x"


def test_network_backend_description() -> None:
    assert NetworkTCPBackend("1.2.3.4").description == "tcp:1.2.3.4:9100"
