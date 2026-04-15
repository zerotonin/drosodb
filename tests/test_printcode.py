from ddb.printcode import ALPHABET, CODE_FULL_LEN, generate_print_code, is_valid


def test_generated_codes_validate() -> None:
    for _ in range(200):
        code = generate_print_code()
        assert len(code) == CODE_FULL_LEN
        assert all(c in ALPHABET for c in code)
        assert is_valid(code)


def test_checksum_catches_single_char_error() -> None:
    code = generate_print_code()
    # Flip one body character — checksum must disagree.
    alt_char = "Z" if code[0] != "Z" else "0"
    tampered = alt_char + code[1:]
    assert not is_valid(tampered)


def test_normalise_fixes_human_misreads() -> None:
    # Build a known-good code, then simulate a user typing I instead of 1.
    good = generate_print_code()
    mistyped = good.replace("1", "I").replace("0", "O")
    assert is_valid(mistyped)  # normaliser repairs it


def test_invalid_length_rejected() -> None:
    assert not is_valid("ABC")
    assert not is_valid("ABCDEF")
    assert not is_valid("")
