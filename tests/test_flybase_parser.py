"""Tests for the FlyBase genotype-string parser.

Covers the segment-count rules + the `+/+` normalisation. The parser is
intentionally heuristic (users edit the result before saving) so these
tests lock down the heuristic, not the "one true answer".
"""

from __future__ import annotations

import pytest

from ddb.flybase.parser import parse_genotype_string


def test_empty_string_returns_empty_slots() -> None:
    p = parse_genotype_string("")
    assert p.chromosome_x == ""
    assert p.chromosome_2 == ""
    assert p.chromosome_3 == ""
    assert p.chromosome_4 == ""
    assert p.chromosome_y == ""


def test_one_segment_lands_on_chr_2() -> None:
    p = parse_genotype_string("P{UAS-Adcy1.Z}2")
    assert p.chromosome_2 == "P{UAS-Adcy1.Z}2"
    assert p.chromosome_x == ""
    assert p.chromosome_3 == ""


def test_two_segments_map_to_chr2_and_chr3() -> None:
    # Most common BDSC pattern: X is +/+ and omitted, leaving two autosomes.
    p = parse_genotype_string("Adv[1]; TM3, Sb[1]")
    assert p.chromosome_x == ""
    assert p.chromosome_2 == "Adv[1]"
    assert p.chromosome_3 == "TM3, Sb[1]"


def test_three_segments_map_to_xyz() -> None:
    p = parse_genotype_string("w[1118]; Adv[1]; TM3")
    assert p.chromosome_x == "w[1118]"
    assert p.chromosome_2 == "Adv[1]"
    assert p.chromosome_3 == "TM3"


def test_four_segments_extend_to_chr4() -> None:
    p = parse_genotype_string("w[1118]; Adv[1]; TM3; ciD")
    assert p.chromosome_x == "w[1118]"
    assert p.chromosome_2 == "Adv[1]"
    assert p.chromosome_3 == "TM3"
    assert p.chromosome_4 == "ciD"


def test_five_segments_include_y() -> None:
    p = parse_genotype_string("X ; 2 ; 3 ; 4 ; Y")
    assert p.chromosome_x == "X"
    assert p.chromosome_2 == "2"
    assert p.chromosome_3 == "3"
    assert p.chromosome_4 == "4"
    assert p.chromosome_y == "Y"


def test_plus_over_plus_normalises_to_empty() -> None:
    p = parse_genotype_string("+/+; Adv[1]; +/+")
    assert p.chromosome_x == ""
    assert p.chromosome_2 == "Adv[1]"
    assert p.chromosome_3 == ""


def test_whitespace_is_trimmed() -> None:
    p = parse_genotype_string("  w[1118]  ;  Adv[1]  ;  TM3  ")
    assert p.chromosome_x == "w[1118]"
    assert p.chromosome_2 == "Adv[1]"
    assert p.chromosome_3 == "TM3"


def test_empty_segments_are_discarded() -> None:
    # Empty middle segments collapse before the positional mapping runs,
    # so "w[1118]; ; TM3" reads as two non-empty segments → the 2-segment
    # (chr2, chr3) rule. That's imperfect — the user will fix it in the
    # dialog — but the parser isn't allowed to drop data silently: the
    # two provided strings must both show up.
    p = parse_genotype_string("w[1118]; ; TM3")
    values = {p.chromosome_x, p.chromosome_2, p.chromosome_3}
    assert "w[1118]" in values
    assert "TM3" in values


def test_as_dict_matches_genotype_model_keys() -> None:
    p = parse_genotype_string("w[1118]; Adv[1]; TM3")
    d = p.as_dict()
    assert set(d.keys()) == {
        "chromosome_x",
        "chromosome_2",
        "chromosome_3",
        "chromosome_4",
        "chromosome_y",
    }


_BDSC_9405 = "P{ry[+t7.2]=lArB}Adcy1[rut-2080]; P{w[+mC]=UAS-Adcy1.Z}2"


@pytest.mark.parametrize(
    "raw,expected_2,expected_3",
    [
        (_BDSC_9405, "P{ry[+t7.2]=lArB}Adcy1[rut-2080]", "P{w[+mC]=UAS-Adcy1.Z}2"),
        ("betaTub60D[2] Kr[If-1]/CyO;  ", "betaTub60D[2] Kr[If-1]/CyO", ""),
    ],
)
def test_realistic_bdsc_strings(raw: str, expected_2: str, expected_3: str) -> None:
    p = parse_genotype_string(raw)
    assert p.chromosome_2 == expected_2
    assert p.chromosome_3 == expected_3
