"""format_notation — Drosophila chromosome-expression formatter."""

from __future__ import annotations

from dataclasses import dataclass

from ddb.genotype import ChromosomeSet, format_notation


@dataclass
class _Fake:
    chromosome_x: str | None = None
    chromosome_2: str | None = None
    chromosome_3: str | None = None
    chromosome_4: str | None = None
    chromosome_y: str | None = None


def test_all_blank_renders_as_triple_unknown() -> None:
    """Blank != wildtype. No data recorded → '?' in every slot."""
    assert format_notation(_Fake()) == "? ; ? ; ?"


def test_single_locus_on_second() -> None:
    g = _Fake(chromosome_2="GAL4-Bloop/CyO")
    assert format_notation(g) == "? ; GAL4-Bloop/CyO ; ?"


def test_all_three_populated() -> None:
    g = _Fake(
        chromosome_x="w[1118]",
        chromosome_2="UAS-mCD8-GFP/CyO",
        chromosome_3="TM3, Sb[1]/TM6B",
    )
    assert format_notation(g) == "w[1118] ; UAS-mCD8-GFP/CyO ; TM3, Sb[1]/TM6B"


def test_explicit_wildtype_is_preserved_verbatim() -> None:
    """A biologist who typed '+/+' asserted verified wildtype; don't rewrite it."""
    g = _Fake(chromosome_x="+/+", chromosome_2="GAL4/CyO", chromosome_3="+/+")
    assert format_notation(g) == "+/+ ; GAL4/CyO ; +/+"


def test_fourth_chromosome_appended_when_set() -> None:
    g = _Fake(chromosome_4="Df(4)42")
    assert format_notation(g) == "? ; ? ; ? ; Df(4)42"


def test_y_chromosome_folds_into_x_slot() -> None:
    g = _Fake(chromosome_x="w[1118]", chromosome_y="Y")
    assert format_notation(g).startswith("w[1118]/Y ;")


def test_whitespace_only_slot_treated_as_blank() -> None:
    g = _Fake(chromosome_2="   ")
    assert " ? ; " in format_notation(g)


def test_works_on_chromosome_set_dataclass() -> None:
    cs = ChromosomeSet(chromosome_2="Kr-GAL4/CyO")
    assert format_notation(cs) == "? ; Kr-GAL4/CyO ; ?"


def test_strips_leading_and_trailing_spaces() -> None:
    g = _Fake(chromosome_2="  UAS-Dcr2 / CyO  ")
    assert format_notation(g) == "? ; UAS-Dcr2 / CyO ; ?"
