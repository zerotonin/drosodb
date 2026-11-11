# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — desktop_icons                                             ║
# ║  « the app icon, at every size the desktop asks for »            ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Renders assets/ddb_logo.png onto a rounded tile with a soft     ║
# ║  shadow and writes it into a freedesktop hicolor icon theme      ║
# ║  (16-512 px), so the app grid, dock and desktop all find it.     ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Build the DDB application icon set.

The source logo is a navy fly on an opaque white square. Used as-is it
shows as a hard white box on GNOME's dark dock and app grid, so each size
is rendered as a rounded white tile with a soft drop shadow, with the
logo trimmed of its white margin and centred on the tile.

Run ``python -m ddb.desktop_icons --icon-root DIR`` (the per-user theme
is ``~/.local/share/icons/hicolor``); ``scripts/install_desktop_entry.sh``
does this for you.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCE = REPO_ROOT / "assets" / "ddb_logo.png"
ICON_NAME = "ddb"
ICON_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256, 512)

MASTER_SIZE = 1024  # render once large, downsample for crisp small sizes
TILE_MARGIN = 0.07  # transparent border around the tile (shadow room)
TILE_RADIUS = 0.20  # corner radius as a fraction of the tile edge
LOGO_FILL = 0.80  # logo's longer side as a fraction of the tile edge
TILE_COLOUR = (255, 255, 255, 255)
TILE_BORDER = (205, 211, 219, 255)
SHADOW_COLOUR = (15, 23, 42, 90)
SHADOW_OFFSET = 0.018
SHADOW_BLUR = 0.025
CONTENT_THRESHOLD = 235  # grey level below which a pixel counts as logo

# ┌────────────────────────────────────────────────────────────┐
# │ Rendering  « logo → rounded tile with shadow »             │
# └────────────────────────────────────────────────────────────┘


def render_app_icon(source: Path, size: int) -> Image.Image:
    """Return the square RGBA icon at ``size`` px."""
    return _render_master(source).resize((size, size), Image.Resampling.LANCZOS)


def _render_master(source: Path) -> Image.Image:
    s = MASTER_SIZE
    margin = round(s * TILE_MARGIN)
    tile_box = (margin, margin, s - margin, s - margin)
    tile_edge = s - 2 * margin
    radius = round(tile_edge * TILE_RADIUS)

    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    canvas.alpha_composite(_shadow(tile_box, radius))
    tile = ImageDraw.Draw(canvas)
    tile.rounded_rectangle(
        tile_box,
        radius=radius,
        fill=TILE_COLOUR,
        outline=TILE_BORDER,
        width=max(2, s // 256),
    )

    logo = _trimmed_logo(source)
    scale = tile_edge * LOGO_FILL / max(logo.size)
    logo = logo.resize(
        (round(logo.width * scale), round(logo.height * scale)), Image.Resampling.LANCZOS
    )
    canvas.alpha_composite(logo, ((s - logo.width) // 2, (s - logo.height) // 2))
    return canvas


def _shadow(tile_box: tuple[int, int, int, int], radius: int) -> Image.Image:
    s = MASTER_SIZE
    offset = round(s * SHADOW_OFFSET)
    left, top, right, bottom = tile_box
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        (left, top + offset, right, bottom + offset), radius=radius, fill=SHADOW_COLOUR
    )
    return layer.filter(ImageFilter.GaussianBlur(s * SHADOW_BLUR))


def _trimmed_logo(source: Path) -> Image.Image:
    """Logo cropped to its non-white content, as RGBA."""
    logo = Image.open(source).convert("RGBA")
    content = ImageOps.invert(logo.convert("L")).point(
        lambda v: 255 if v > 255 - CONTENT_THRESHOLD else 0
    )
    bbox = content.getbbox()
    return logo.crop(bbox) if bbox else logo


# ┌────────────────────────────────────────────────────────────┐
# │ Installing  « hicolor/<size>x<size>/apps/ddb.png »         │
# └────────────────────────────────────────────────────────────┘


def install_icon_theme(
    icon_root: Path,
    source: Path = DEFAULT_SOURCE,
    sizes: tuple[int, ...] = ICON_SIZES,
) -> list[Path]:
    """Write ``ddb.png`` for every size under ``icon_root``; return the paths.

    Existing targets are unlinked first: older installers left a symlink
    to the repo's logo there, and saving through it would overwrite the
    logo itself.
    """
    master = _render_master(source)
    written = []
    for size in sizes:
        target = icon_root / f"{size}x{size}" / "apps" / f"{ICON_NAME}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        master.resize((size, size), Image.Resampling.LANCZOS).save(target, optimize=True)
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--icon-root", type=Path, required=True, help="hicolor theme root")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="logo PNG")
    args = parser.parse_args(argv)
    for path in install_icon_theme(args.icon_root, args.source):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
