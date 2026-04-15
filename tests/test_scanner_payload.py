import pytest

from ddb.qr import build_payload
from ddb.scanner.payload import PayloadParseError, parse_payload


def test_roundtrip_with_build_payload() -> None:
    raw = build_payload(42, "AB12Z", "local")
    parsed = parse_payload(raw)
    assert parsed.version == 1
    assert parsed.entity == "vial"
    assert parsed.entity_id == 42
    assert parsed.print_code == "AB12Z"
    assert parsed.database_id == "local"


def test_print_code_is_upper_cased() -> None:
    parsed = parse_payload("ddb:1:vial:7?pc=ab12z&db=local")
    assert parsed.print_code == "AB12Z"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "hello",
        "https://example.com/",
        "ddb:1:genotype:5?pc=X&db=local",  # wrong entity
        "ddb:1:vial:abc?pc=X&db=local",  # non-int id
        "ddb:1:vial:5?db=local",  # missing pc
        "ddb:1:vial:5?pc=X",  # missing db
        "ddb:NAN:vial:5?pc=X&db=local",  # non-int version
        "ddb:1:vial?pc=X&db=local",  # malformed path
    ],
)
def test_malformed_payloads_raise(raw: str) -> None:
    with pytest.raises(PayloadParseError):
        parse_payload(raw)
