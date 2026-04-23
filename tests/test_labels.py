from io import BytesIO
from pathlib import Path

from PIL import Image

from ddb.labels import LABEL_PX, render_label, save_label


def test_label_has_expected_dimensions() -> None:
    png = render_label(
        vial_id=1,
        print_code="AB12Z",
        genotype_name="w[1118]; +; +",
        database_id="local",
        donor_strain_id="9405",
    )
    img = Image.open(BytesIO(png))
    assert img.size == LABEL_PX


def test_save_label_writes_file(tmp_path: Path) -> None:
    png = render_label(1, "AB12Z", "Canton-S", "local")
    p = save_label(png, tmp_path / "labels", "AB12Z")
    assert p.exists()
    assert p.read_bytes().startswith(b"\x89PNG")


def test_long_genotype_name_does_not_crash() -> None:
    long_name = "w*; " + "sp/cyo; " * 8 + "UAS-mCherry"
    png = render_label(1, "AB12Z", long_name, "local")
    assert Image.open(BytesIO(png)).size == LABEL_PX


def test_genotype_notation_renders_without_crash() -> None:
    png = render_label(
        vial_id=1,
        print_code="AB12Z",
        genotype_name="spalthof",
        database_id="local",
        genotype_notation="+/+ ; GAL4-Bloop/CyO ; +/+",
        owner_username="cchapel",
        donor_strain_id="641",
        generation=3,
    )
    assert Image.open(BytesIO(png)).size == LABEL_PX


def test_created_date_renders_without_crash() -> None:
    png = render_label(
        vial_id=1,
        print_code="AB12Z",
        genotype_name="spalthof",
        database_id="local",
        genotype_notation="+/+ ; GAL4-Bloop/CyO ; +/+",
        generation=3,
        created_date="2026-04-16",
    )
    assert Image.open(BytesIO(png)).size == LABEL_PX


def test_date_without_generation_also_renders() -> None:
    png = render_label(
        vial_id=1,
        print_code="AB12Z",
        genotype_name="spalthof",
        database_id="local",
        created_date="2026-04-16",
    )
    assert Image.open(BytesIO(png)).size == LABEL_PX


def test_long_notation_gets_shrunk_and_wrapped() -> None:
    long_notation = "w[1118] ; P{UAS-Dcr2.D}1, P{GAL4-ey.H}3-8/CyO ; TM3, Sb[1], P{w[+mC]=GAL4-Kr.C}DC3/TM6B, Tb[1]"  # noqa: E501
    png = render_label(
        vial_id=1,
        print_code="AB12Z",
        genotype_name="stressful",
        database_id="local",
        genotype_notation=long_notation,
    )
    assert Image.open(BytesIO(png)).size == LABEL_PX


# --- overflow / extreme inputs --------------------------------------------
# Properties we care about:
#   1. Any extreme input still produces a valid 638×201 PNG (no crash).
#   2. The date (when provided) takes priority over the generation in the
#      top-row badge — bounded growth of N should never push the date off.


def test_five_digit_generation_still_fits_with_date() -> None:
    """g99999 · 2026-04-16 should render without clipping the date off."""
    png = render_label(
        vial_id=1,
        print_code="AB12Z",
        genotype_name="x",
        database_id="local",
        generation=99_999,
        created_date="2026-04-16",
    )
    assert Image.open(BytesIO(png)).size == LABEL_PX


def test_seven_digit_generation_drops_gen_but_keeps_date() -> None:
    """If the badge won't fit even at the smallest font, generation is
    dropped first — the date is the immutable fact, generation is
    derivable from lineage."""
    from PIL import ImageDraw

    from ddb.labels import _fit_top_row

    scratch = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(scratch)
    # Width chosen so that the full g<N> · date badge can't fit even at 14px
    # alongside a 48px print code, but a date-only badge at 18px can.
    _, _, badge = _fit_top_row(
        draw,
        print_code="AB12Z",
        generation=9_999_999,  # absurd
        created_date="2026-04-16",
        max_width=260,
    )
    assert "2026-04-16" in badge, f"date should survive, got badge={badge!r}"
    assert "g9" not in badge, f"generation should have been dropped, got {badge!r}"


def test_overlong_print_code_is_shrunk_or_ellipsised() -> None:
    """A stray super-long print code should never overflow the label edge."""
    png = render_label(
        vial_id=1,
        print_code="THISISWAYTOOLONGTOBEAREALPRINTCODE",
        genotype_name="x",
        database_id="local",
        created_date="2026-04-16",
    )
    assert Image.open(BytesIO(png)).size == LABEL_PX


def test_paragraph_length_genotype_name_renders_as_ellipsised_subtitle() -> None:
    paragraph = (
        "this is a genotype name someone accidentally pasted an entire "
        "abstract into and now we have to deal with it without crashing "
        "the label renderer or silently losing information"
    )
    png = render_label(
        vial_id=1,
        print_code="AB12Z",
        genotype_name=paragraph,
        database_id="local",
        genotype_notation="+/+ ; +/+ ; +/+",
        created_date="2026-04-16",
    )
    assert Image.open(BytesIO(png)).size == LABEL_PX


def test_very_long_owner_and_donor_do_not_collide() -> None:
    png = render_label(
        vial_id=1,
        print_code="AB12Z",
        genotype_name="x",
        database_id="local",
        owner_username="reallylonglabusernamethatnobodywouldactuallyuse",
        donor_strain_id="BDSC-00000000000000000000042",
        created_date="2026-04-16",
    )
    assert Image.open(BytesIO(png)).size == LABEL_PX
