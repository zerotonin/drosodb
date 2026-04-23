"""Format chromosome fields as standard Drosophila notation.

Biologists write a genotype as `X ; II ; III [; IV]`, semicolon-separated.
Slots where data was NOT recorded render as `?` — NOT `+/+`, because a
blank field means "we don't know", while `+/+` would assert a verified
wildtype locus. Avoiding that false certainty matters when a downstream
reader acts on what's printed on the label.

If a biologist explicitly typed `+/+` into a chromosome field, it is
preserved verbatim — that's a claim they made, not an assumption we're
making for them.

The short DB `name` field is an internal alias ("spalthof", "strain_10");
the notation here is the expression that actually appears in notebooks.
"""

from __future__ import annotations

from dataclasses import dataclass

# `?` (not `+/+`) — see module docstring for why.
UNKNOWN_SLOT = "?"
SEPARATOR = " ; "


@dataclass(frozen=True)
class ChromosomeSet:
    """Thin carrier so non-Genotype callers (e.g. VialRow) can format too."""

    chromosome_x: str | None = None
    chromosome_2: str | None = None
    chromosome_3: str | None = None
    chromosome_4: str | None = None
    chromosome_y: str | None = None


def _slot(value: str | None) -> str:
    if value is None:
        return UNKNOWN_SLOT
    v = value.strip()
    return v or UNKNOWN_SLOT


def format_notation(source: object) -> str:
    """Return `X ; II ; III [; IV]`, with `?` for unrecorded slots.

    Accepts anything with chromosome_x / _2 / _3 / _4 / _y attributes — a
    Genotype model instance, a ChromosomeSet, or any duck-typed stand-in.
    """
    x = _slot(getattr(source, "chromosome_x", None))
    y = getattr(source, "chromosome_y", None)
    if y and y.strip():
        # Male stock convention: X slot reads `X/Y`.
        x = f"{x}/{y.strip()}"

    parts = [
        x,
        _slot(getattr(source, "chromosome_2", None)),
        _slot(getattr(source, "chromosome_3", None)),
    ]
    fourth = getattr(source, "chromosome_4", None)
    if fourth and fourth.strip():
        parts.append(fourth.strip())
    return SEPARATOR.join(parts)
