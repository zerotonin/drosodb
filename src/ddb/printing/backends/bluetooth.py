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
import time
from pathlib import Path

from ddb.printing.status import StatusBlock, decode_status_blocks

# Exit codes from the sidecar (see _SENDER_SCRIPT).
_SIDECAR_RC_CONNECT_FAILED = 3
_SIDECAR_RC_SEND_FAILED = 4

# When we get EBUSY on connect (the printer's BT stack hasn't fully torn
# down the previous RFCOMM socket), back off and retry. Values tuned on
# a QL-820NWB — typical retry need in a batch-print scenario is 1 pause.
_CONNECT_RETRY_BACKOFFS_S: tuple[float, ...] = (0.75, 1.5, 3.0, 6.0)

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
        """Send a raster job; retry transparently on EBUSY at connect time.

        EBUSY (errno 16) from `connect()` means the printer's BT stack
        is still reclaiming the previous socket. This is common when the
        caller batches several prints — the fix is to wait and try again,
        not to bail. Failures during the send itself are NOT retried —
        partial raster bytes on the wire can confuse the printer.
        """
        if not self.system_python.exists():
            raise FileNotFoundError(
                f"{self.system_python} not found — BT backend needs a Python "
                f"built with BlueZ support. Try /usr/bin/python3."
            )

        last_error: str | None = None
        for attempt in range(len(_CONNECT_RETRY_BACKOFFS_S) + 1):
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
            if proc.returncode == 0:
                break

            stderr = proc.stderr.decode(errors="replace").strip()
            last_error = stderr
            # Only the connect-phase EBUSY failure is retried. Anything
            # else (send-phase, non-errno-16 connect errors, etc.) raises
            # immediately so we don't mask real bugs.
            is_retriable = (
                proc.returncode == _SIDECAR_RC_CONNECT_FAILED
                and "[Errno 16]" in stderr
                and attempt < len(_CONNECT_RETRY_BACKOFFS_S)
            )
            if not is_retriable:
                raise ConnectionError(f"Bluetooth send failed (rc={proc.returncode}): {stderr}")
            time.sleep(_CONNECT_RETRY_BACKOFFS_S[attempt])
        else:  # exhausted retries
            raise ConnectionError(
                f"Bluetooth send failed after {len(_CONNECT_RETRY_BACKOFFS_S) + 1} "
                f"attempts: {last_error}"
            )

        try:
            line = proc.stdout.decode().strip().splitlines()[-1]
            payload = json.loads(line)
            reply = bytes.fromhex(payload["hex"])
        except (ValueError, IndexError, KeyError) as e:
            raise ConnectionError(f"Could not parse sidecar output: {proc.stdout!r}") from e
        return decode_status_blocks(reply)
