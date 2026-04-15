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
