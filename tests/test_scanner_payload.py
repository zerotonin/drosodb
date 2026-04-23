import pytest

from ddb.qr import build_payload
from ddb.scanner.payload import PayloadParseError, parse_payload

# --- Compact (current) format -------------------------------------------


def test_compact_roundtrip_with_build_payload() -> None:
    raw = build_payload("AB12Z")  # → "DDB:AB12Z"
    parsed = parse_payload(raw)
    assert parsed.print_code == "AB12Z"
    assert parsed.is_compact
    # Compact form does not carry vial_id / db id.
    assert parsed.vial_id is None
    assert parsed.database_id is None


def test_compact_accepts_lowercase_prefix() -> None:
    parsed = parse_payload("ddb:ab12z")
    assert parsed.print_code == "AB12Z"
    assert parsed.is_compact


# --- Legacy format (labels printed before the Micro-QR switch) -----------


def test_legacy_roundtrip() -> None:
    parsed = parse_payload("ddb:1:vial:42?pc=AB12Z&db=local")
    assert parsed.print_code == "AB12Z"
    assert parsed.vial_id == 42
    assert parsed.database_id == "local"
    assert not parsed.is_compact


def test_legacy_print_code_is_upper_cased() -> None:
    parsed = parse_payload("ddb:1:vial:7?pc=ab12z&db=local")
    assert parsed.print_code == "AB12Z"


# --- Invalid payloads ---------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "hello",
        "https://example.com/",
        "DDB:",  # compact with no code
        "DDB:!@#",  # compact non-alnum
        "ddb:1:genotype:5?pc=X&db=local",  # legacy wrong entity
        "ddb:1:vial:abc?pc=X&db=local",  # legacy non-int id
        "ddb:1:vial:5?db=local",  # legacy missing pc
        "ddb:1:vial:5?pc=X",  # legacy missing db
        "ddb:1:vial?pc=X&db=local",  # legacy malformed path
    ],
)
def test_malformed_payloads_raise(raw: str) -> None:
    with pytest.raises(PayloadParseError):
        parse_payload(raw)
