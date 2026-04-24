"""Split a FlyBase genotype string into X/2/3/4/Y chromosome components.

FlyBase's `FB_genotype` column is semicolon-delimited and positional by
convention: `X ; 2 ; 3 ; 4 ; Y`. In practice most stocks drop trailing
chromosomes that are unremarkable. Empirical distribution on a sample
of BDSC rows:

    3 segments → X ; 2 ; 3      (most common)
    2 segments → 2 ; 3          (X is +/+, omitted)
    1 segment  → single insertion, usually autosomal
    4 segments → X ; 2 ; 3 ; 4
    5 segments → X ; 2 ; 3 ; 4 ; Y  (rare, usually Y-linked stocks)

Anything we infer is just a starting point; the import dialog always
shows the user the parsed split and lets them move pieces between
chromosomes before saving.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedGenotype:
    """Five-slot genotype — empty strings mean 'no call' for that slot."""

    chromosome_x: str = ""
    chromosome_2: str = ""
    chromosome_3: str = ""
    chromosome_4: str = ""
    chromosome_y: str = ""
    raw: str = ""

    def as_dict(self) -> dict[str, str]:
        """Flat mapping, keyed to match Genotype model field names."""
        return {
            "chromosome_x": self.chromosome_x,
            "chromosome_2": self.chromosome_2,
            "chromosome_3": self.chromosome_3,
            "chromosome_4": self.chromosome_4,
            "chromosome_y": self.chromosome_y,
        }


def parse_genotype_string(raw: str) -> ParsedGenotype:
    """Best-effort split of a FlyBase genotype string into chromosome slots.

    The input is semicolon-delimited. Segment count drives the positional
    mapping; see the module docstring for the rules. We strip whitespace
    around each segment and treat a literal `+/+` or `+` as an empty
    placeholder so it doesn't crowd the form.
    """
    text = (raw or "").strip()
    if not text:
        return ParsedGenotype(raw=raw or "")

    segments = [s.strip() for s in text.split(";")]
    segments = [s for s in segments if s]
    slots = {
        "chromosome_x": "",
        "chromosome_2": "",
        "chromosome_3": "",
        "chromosome_4": "",
        "chromosome_y": "",
    }

    n = len(segments)
    if n == 1:
        # Single insertion — no reliable chromosome signal. Drop it on
        # chr 2 (the most common autosomal default). User can move it.
        slots["chromosome_2"] = segments[0]
    elif n == 2:
        # "X is +/+, omitted" is the dominant pattern — land on 2 ; 3.
        slots["chromosome_2"], slots["chromosome_3"] = segments
    elif n == 3:
        slots["chromosome_x"], slots["chromosome_2"], slots["chromosome_3"] = segments
    elif n == 4:
        (
            slots["chromosome_x"],
            slots["chromosome_2"],
            slots["chromosome_3"],
            slots["chromosome_4"],
        ) = segments
    else:
        # 5 or more — use the first 5 positionally; anything beyond that
        # FlyBase doesn't normally emit.
        (
            slots["chromosome_x"],
            slots["chromosome_2"],
            slots["chromosome_3"],
            slots["chromosome_4"],
            slots["chromosome_y"],
        ) = segments[:5]

    # Normalise trivial "+/+" / "+" placeholders to empty so the form
    # doesn't show noise from lines where every chromosome was wild-type.
    for key, val in list(slots.items()):
        if val in {"+", "+/+"}:
            slots[key] = ""

    return ParsedGenotype(raw=raw, **slots)
