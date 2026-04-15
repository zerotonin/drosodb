"""Crockford Base32 print codes with a lightweight mod-32 checksum.

4 random chars + 1 checksum char = 5 total (fits the DK-11204 label comfortably).
Crockford's alphabet excludes I, L, O, U to avoid reading errors, which matters
when a tired grad student types a fallback code by hand.

The checksum is `(Σ char_values) mod 32`, mapped back to the alphabet. That
catches any single-character misread; it does NOT catch transpositions. For
a single-user lab that's enough — the partial unique index on active
print_codes is the real safety net.
"""

from __future__ import annotations

import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_VALUES = {c: i for i, c in enumerate(ALPHABET)}
assert len(ALPHABET) == 32

CODE_BODY_LEN = 4
CODE_FULL_LEN = CODE_BODY_LEN + 1  # + checksum


def _normalise(code: str) -> str:
    """Uppercase, strip separators, fix common misreads (I→1, L→1, O→0)."""
    return (
        code.upper()
        .replace("-", "")
        .replace(" ", "")
        .replace("I", "1")
        .replace("L", "1")
        .replace("O", "0")
        .replace("U", "V")
    )


def _checksum(body: str) -> str:
    total = sum(_VALUES[c] for c in body)
    return ALPHABET[total % 32]


def generate_print_code(rng: secrets.SystemRandom | None = None) -> str:
    """Return a fresh 5-char print code (4 random + 1 checksum)."""
    r = rng or secrets.SystemRandom()
    body = "".join(r.choice(ALPHABET) for _ in range(CODE_BODY_LEN))
    return body + _checksum(body)


def is_valid(code: str) -> bool:
    """True iff the code is well-formed Crockford and its checksum matches."""
    c = _normalise(code)
    if len(c) != CODE_FULL_LEN:
        return False
    if any(ch not in _VALUES for ch in c):
        return False
    return _checksum(c[:CODE_BODY_LEN]) == c[-1]
