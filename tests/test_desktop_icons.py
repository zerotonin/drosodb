"""App icon rendering and hicolor install (ddb.desktop_icons)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from ddb.desktop_icons import (
    DEFAULT_SOURCE,
    ICON_SIZES,
    install_icon_theme,
    main,
    render_app_icon,
)


def test_icon_is_square_rgba_with_transparent_corners() -> None:
    icon = render_app_icon(DEFAULT_SOURCE, 128)
    assert icon.size == (128, 128)
    assert icon.mode == "RGBA"
    assert icon.getpixel((0, 0))[3] == 0, "corners outside the rounded tile stay transparent"
    assert icon.getpixel((64, 64))[3] == 255, "tile centre is opaque"


def test_logo_is_drawn_on_the_tile() -> None:
    icon = render_app_icon(DEFAULT_SOURCE, 256).convert("L")
    darkest, _ = icon.getextrema()
    assert darkest < 100, "the navy logo must be present, not just a white tile"


def test_install_writes_every_size(tmp_path: Path) -> None:
    written = install_icon_theme(tmp_path / "hicolor")
    assert len(written) == len(ICON_SIZES)
    for size, path in zip(ICON_SIZES, written, strict=True):
        assert path == tmp_path / "hicolor" / f"{size}x{size}" / "apps" / "ddb.png"
        with Image.open(path) as image:
            assert image.size == (size, size)


def test_install_replaces_old_symlink_without_touching_its_target(tmp_path: Path) -> None:
    """Older installers symlinked the 256 px icon to the repo logo."""
    logo_copy = tmp_path / "logo.png"
    logo_copy.write_bytes(DEFAULT_SOURCE.read_bytes())
    before = logo_copy.read_bytes()
    link = tmp_path / "hicolor" / "256x256" / "apps" / "ddb.png"
    link.parent.mkdir(parents=True)
    link.symlink_to(logo_copy)

    install_icon_theme(tmp_path / "hicolor", sizes=(256,))

    assert not link.is_symlink()
    assert logo_copy.read_bytes() == before


def test_cli_prints_written_paths(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    assert main(["--icon-root", str(tmp_path)]) == 0
    printed = capsys.readouterr().out.split()
    assert len(printed) == len(ICON_SIZES)
    assert all(Path(p).exists() for p in printed)
