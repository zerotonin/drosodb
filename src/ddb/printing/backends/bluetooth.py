"""Bluetooth RFCOMM backend for Brother QL-series printers.

SDP enumeration on the QL-820NWB shows that raster commands are accepted on
RFCOMM channel 1 — despite being advertised as "Serial Port Profile" in the
Service Class record. (OBEX Object Push on ch 2 and BIP on ch 3 are red
herrings; see memory/project_printer_bt.md.)

The Python socket module in our conda env is NOT built with BlueZ support
(no `AF_BLUETOOTH`). Rather than requiring a rebuild or pulling in `pybluez`
(which is unmaintained and won't build against BlueZ 5.7x on current Ubuntu),
we shell out to the *system* `python3` — that one IS built against BlueZ and
has `AF_BLUETOOTH` baked in. Raster bytes go to stdin, status blocks come
back on stdout as JSON lines.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ddb.printing.status import StatusBlock, decode_status_blocks

# Sidecar script. Small, stdlib-only, runs under /usr/bin/python3.
_SENDER_SCRIPT = r"""
import json, socket, sys, time

mac = sys.argv[1]
channel = int(sys.argv[2])
timeout = float(sys.argv[3])

raster = sys.stdin.buffer.read()

try:
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
except AttributeError:
    sys.stderr.write("system python lacks AF_BLUETOOTH — no BlueZ headers at build time\n")
    sys.exit(2)

s.settimeout(timeout)
try:
    s.connect((mac, channel))
except OSError as e:
    sys.stderr.write("connect failed: %s\n" % e)
    sys.exit(3)

# Stream in small chunks; some BT stacks choke on huge writes.
try:
    view = memoryview(raster)
    for off in range(0, len(view), 512):
        s.sendall(view[off:off+512])
        time.sleep(0.01)
except OSError as e:
    sys.stderr.write("send failed: %s\n" % e)
    s.close()
    sys.exit(4)

# Drain status replies.
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
    # Terminate early if we've seen a phase-back-to-waiting or an error block.
    i = 0
    terminal = False
    while i + 32 <= len(buf):
        if buf[i] == 0x80:
            blk = buf[i:i+32]
            status_type = blk[18]
            phase_type = blk[19]
            err1, err2 = blk[8], blk[9]
            if err1 or err2 or status_type == 0x02:
                terminal = True
                break
            if status_type == 0x06 and phase_type == 0x00:
                terminal = True
                break
            i += 32
        else:
            i += 1
    if terminal:
        time.sleep(0.2)
        try:
            extra = s.recv(256)
            if extra:
                buf += extra
        except (TimeoutError, OSError):
            pass
        break
s.close()

# Write raw hex so the parent can parse with the normal decoder.
sys.stdout.write(json.dumps({"hex": buf.hex()}))
sys.stdout.write("\n")
"""


class BluetoothRFCOMMBackend:
    def __init__(
        self,
        mac: str,
        channel: int = 1,
        *,
        system_python: Path = Path("/usr/bin/python3"),
    ) -> None:
        self.mac = mac
        self.channel = channel
        self.system_python = system_python

    @property
    def description(self) -> str:
        return f"bt:{self.mac}@ch{self.channel}"

    def send(self, raster: bytes, *, timeout: float = 30.0) -> list[StatusBlock]:
        if not self.system_python.exists():
            raise FileNotFoundError(
                f"{self.system_python} not found — BT backend needs a Python "
                f"built with BlueZ support. Try /usr/bin/python3."
            )
        proc = subprocess.run(
            [
                str(self.system_python),
                "-c",
                _SENDER_SCRIPT,
                self.mac,
                str(self.channel),
                str(timeout),
            ],
            input=raster,
            capture_output=True,
            timeout=timeout + 10,
            check=False,
        )
        if proc.returncode != 0:
            raise ConnectionError(
                f"Bluetooth send failed (rc={proc.returncode}): "
                f"{proc.stderr.decode(errors='replace').strip()}"
            )
        try:
            line = proc.stdout.decode().strip().splitlines()[-1]
            payload = json.loads(line)
            reply = bytes.fromhex(payload["hex"])
        except (ValueError, IndexError, KeyError) as e:
            raise ConnectionError(
                f"Could not parse sidecar output: {proc.stdout!r}"
            ) from e
        return decode_status_blocks(reply)
