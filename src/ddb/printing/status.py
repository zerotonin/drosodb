"""Decode Brother QL status blocks (32-byte 0x80-prefixed replies).

The QL-820NWB (and siblings) send these on the same channel we sent the raster
on. During a successful print we see the sequence:

    reply-to-status-req (phase=waiting)
    phase-change (phase=printing)
    printing-done (phase=printing)
    phase-change (phase=waiting)

If err1 or err2 are non-zero, the printer has refused the job. The commonly
seen flags are covered in ERROR_FLAGS below; the raster command reference PDF
has the exhaustive list if one of the "unknown" values ever shows up.
"""

from __future__ import annotations

from dataclasses import dataclass

# Brother raster command reference, status block layout.
# Byte offsets match the spec; some "reserved" bytes are exposed as raw ints
# in case later firmware revisions start populating them.
_STATUS_SIZE = 32
_HEAD_MARK = 0x80

# err1 (byte 8) bit masks
_ERR1_FLAGS: dict[int, str] = {
    0x01: "no-media",
    0x02: "end-of-media",
    0x04: "tape-cutter-jam",
    0x10: "main-unit-in-use",
    0x40: "fan-error",
}

# err2 (byte 9) bit masks
_ERR2_FLAGS: dict[int, str] = {
    0x04: "transmission-err",
    0x10: "cover-open",
    0x20: "overheat",
    0x40: "media-mismatch-replace",
}

_STATUS_TYPES: dict[int, str] = {
    0x00: "reply-to-status-req",
    0x01: "printing-done",
    0x02: "error",
    0x04: "turned-off",
    0x05: "notification",
    0x06: "phase-change",
}

_PHASE_TYPES: dict[int, str] = {
    0x00: "waiting",
    0x01: "printing",
}


@dataclass(frozen=True)
class StatusBlock:
    """One 32-byte Brother status packet, parsed into named fields."""

    err1: int
    err2: int
    loaded_width_mm: int
    media_type: int
    loaded_length_mm: int
    status_type: int
    phase_type: int
    phase_number: int
    notification: int
    raw: bytes

    @property
    def status_name(self) -> str:
        return _STATUS_TYPES.get(self.status_type, f"unknown(0x{self.status_type:02x})")

    @property
    def phase_name(self) -> str:
        return _PHASE_TYPES.get(self.phase_type, f"unknown(0x{self.phase_type:02x})")

    @property
    def error_flags(self) -> list[str]:
        out: list[str] = []
        for bit, name in _ERR1_FLAGS.items():
            if self.err1 & bit:
                out.append(name)
        for bit, name in _ERR2_FLAGS.items():
            if self.err2 & bit:
                out.append(name)
        return out

    @property
    def is_error(self) -> bool:
        return self.err1 != 0 or self.err2 != 0 or self.status_type == 0x02

    @property
    def is_done(self) -> bool:
        return self.status_type == 0x01

    def describe(self) -> str:
        parts = [
            f"{self.status_name}/{self.phase_name}",
            f"media={self.loaded_width_mm}x{self.loaded_length_mm}mm",
        ]
        if self.error_flags:
            parts.append(f"err=[{','.join(self.error_flags)}]")
        return " ".join(parts)


def decode_status_blocks(buf: bytes) -> list[StatusBlock]:
    """Parse a byte buffer into zero or more StatusBlocks.

    Silently skips bytes that don't start with 0x80 or aren't followed by a
    full 32-byte block — keeps decoding robust against stray framing bytes.
    """
    blocks: list[StatusBlock] = []
    i = 0
    while i + _STATUS_SIZE <= len(buf):
        if buf[i] != _HEAD_MARK:
            i += 1
            continue
        b = buf[i : i + _STATUS_SIZE]
        blocks.append(
            StatusBlock(
                err1=b[8],
                err2=b[9],
                loaded_width_mm=b[10],
                media_type=b[11],
                loaded_length_mm=b[17],
                status_type=b[18],
                phase_type=b[19],
                phase_number=int.from_bytes(b[20:22], "big"),
                notification=b[22],
                raw=bytes(b),
            )
        )
        i += _STATUS_SIZE
    return blocks
