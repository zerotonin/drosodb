"""Status block decoder — table-driven, no hardware."""

from __future__ import annotations

from ddb.printing.status import decode_status_blocks


def _mk(overrides: dict[int, int] | None = None) -> bytes:
    """Build a plausible 32-byte status block with optional field overrides."""
    # Defaults = "idle QL-820NWB, DK-11204 loaded, no error, reply-to-status-req".
    b = bytearray(32)
    b[0] = 0x80  # head mark
    b[1] = 0x20  # size
    b[2] = 0x42  # 'B'
    b[3] = 0x34  # series
    b[4] = 0x41  # model
    b[5] = 0x30  # country
    b[10] = 0x11  # width 17 mm
    b[11] = 0x0B  # die-cut
    b[17] = 0x36  # length 54 mm
    b[18] = 0x00  # reply-to-status-req
    b[19] = 0x00  # waiting
    if overrides:
        for k, v in overrides.items():
            b[k] = v
    return bytes(b)


def test_decodes_single_idle_block() -> None:
    blocks = decode_status_blocks(_mk())
    assert len(blocks) == 1
    b = blocks[0]
    assert b.loaded_width_mm == 17
    assert b.loaded_length_mm == 54
    assert b.status_type == 0x00
    assert b.status_name == "reply-to-status-req"
    assert b.phase_name == "waiting"
    assert not b.is_error
    assert not b.is_done
    assert b.error_flags == []


def test_full_print_sequence() -> None:
    seq = (
        _mk({18: 0x00, 19: 0x00})  # reply to status
        + _mk({18: 0x06, 19: 0x01})  # phase change -> printing
        + _mk({18: 0x01, 19: 0x01})  # printing done
        + _mk({18: 0x06, 19: 0x00})  # phase back to waiting
    )
    blocks = decode_status_blocks(seq)
    assert len(blocks) == 4
    assert [b.status_type for b in blocks] == [0x00, 0x06, 0x01, 0x06]
    assert blocks[2].is_done
    assert not any(b.is_error for b in blocks)


def test_media_mismatch_error() -> None:
    b = decode_status_blocks(_mk({9: 0x40, 18: 0x02}))[0]
    assert b.is_error
    assert "media-mismatch-replace" in b.error_flags


def test_cover_open_and_overheat() -> None:
    b = decode_status_blocks(_mk({9: 0x30}))[0]
    assert b.error_flags == ["cover-open", "overheat"]


def test_no_media_and_end_of_media() -> None:
    b = decode_status_blocks(_mk({8: 0x03}))[0]
    assert b.error_flags == ["no-media", "end-of-media"]


def test_skips_framing_garbage() -> None:
    # Two valid blocks separated by a stray non-0x80 byte.
    buf = _mk() + b"\x00\x11\x22" + _mk({18: 0x01})
    blocks = decode_status_blocks(buf)
    assert len(blocks) == 2
    assert blocks[1].is_done


def test_returns_empty_on_empty_buffer() -> None:
    assert decode_status_blocks(b"") == []


def test_describe_includes_media_and_error_flags() -> None:
    b = decode_status_blocks(_mk({9: 0x40, 18: 0x02}))[0]
    d = b.describe()
    assert "error" in d and "17x54mm" in d and "media-mismatch-replace" in d
