#!/usr/bin/env python3
"""Bluetooth RFCOMM raster sender for Brother QL printers.

Invoked by `ddb.printing.backends.bluetooth.BluetoothRFCOMMBackend` under
`/usr/bin/python3` because the conda env's Python is not built against
BlueZ headers and therefore lacks `socket.AF_BLUETOOTH`. Reads raster
bytes from stdin; writes a single JSON line `{"hex": "..."}` to stdout
containing the concatenated status-block reply.

Usage (also runnable by hand for debugging):

    /usr/bin/python3 bt_sender.py <MAC> <CHANNEL> <TIMEOUT_S> < raster.bin

Exit codes (the caller relies on these; don't renumber without updating
`backends/bluetooth.py`):

    0   success (stdout = JSON with hex status blob)
    2   this Python has no AF_BLUETOOTH (wrong interpreter)
    3   connect failed (stderr includes errno; errno 16 = EBUSY-retry)
    4   send failed mid-stream
"""

from __future__ import annotations

import json
import socket
import sys
import time


def main(argv: list[str]) -> int:
    mac = argv[1]
    channel = int(argv[2])
    timeout = float(argv[3])

    raster = sys.stdin.buffer.read()

    try:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    except AttributeError:
        sys.stderr.write(
            "system python lacks AF_BLUETOOTH — no BlueZ headers at build time\n"
        )
        return 2

    s.settimeout(timeout)
    try:
        s.connect((mac, channel))
    except OSError as e:
        sys.stderr.write(f"connect failed: {e}\n")
        return 3

    # Stream in small chunks; some BT stacks choke on huge writes.
    try:
        view = memoryview(raster)
        for off in range(0, len(view), 512):
            s.sendall(view[off : off + 512])
            time.sleep(0.01)
    except OSError as e:
        sys.stderr.write(f"send failed: {e}\n")
        s.close()
        return 4

    # Drain status replies. Each block is 32 bytes starting with 0x80.
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
        # Terminate early on a phase-back-to-waiting or error block.
        terminal = False
        i = 0
        while i + 32 <= len(buf):
            if buf[i] == 0x80:
                blk = buf[i : i + 32]
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
            # One short grace recv in case the printer had another block queued.
            time.sleep(0.2)
            try:
                extra = s.recv(256)
                if extra:
                    buf += extra
            except (TimeoutError, OSError):
                pass
            break
    s.close()

    sys.stdout.write(json.dumps({"hex": buf.hex()}))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
