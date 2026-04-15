"""Render a 17×54mm vial label (DK-11204) as a PNG.

Layout at 300 DPI (≈ 201×638 pixels):

    +------+--------------------------------+
    |      |  <print code, large>           |
    | QR   |  <genotype name, wrapping>     |
    |      |  <donor / strain id, small>    |
    +------+--------------------------------+

The PNG is the single artefact shared between screen preview and the
printer (the printer wrapper takes PNG bytes). Switching to the real
printer later changes nothing about this file.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ddb.qr import build_payload, make_qr_png

LABEL_MM = (54.0, 17.0)  # (width, height)
DPI = 300
_MM_PER_INCH = 25.4


def _px(mm: float) -> int:
    return round(mm / _MM_PER_INCH * DPI)


LABEL_PX = (_px(LABEL_MM[0]), _px(LABEL_MM[1]))  # ≈ (638, 201)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    # Pillow's default bitmap font is too small and ugly; DejaVu ships with
    # most Linux + conda envs. Fall back gracefully.
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Greedy word-wrap; breaks very long single tokens by character."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        # Single word longer than the line — hard-break by char.
        if draw.textlength(word, font=font) > max_width:
            chunk = ""
            for ch in word:
                if draw.textlength(chunk + ch, font=font) <= max_width:
                    chunk += ch
                else:
                    lines.append(chunk)
                    chunk = ch
            cur = chunk
        else:
            cur = word
    if cur:
        lines.append(cur)
    return lines


def render_label(
    vial_id: int,
    print_code: str,
    genotype_name: str,
    database_id: str,
    donor_strain_id: str | None = None,
) -> bytes:
    """Return PNG bytes for the label."""
    payload = build_payload(vial_id, print_code, database_id)
    qr_png = make_qr_png(payload, scale=6, border=1)
    qr_img = Image.open(BytesIO(qr_png)).convert("RGB")

    w, h = LABEL_PX
    label = Image.new("RGB", (w, h), "white")

    # QR takes the left square, 2px margin.
    qr_side = h - 8
    qr_img = qr_img.resize((qr_side, qr_side), Image.NEAREST)
    label.paste(qr_img, (4, 4))

    draw = ImageDraw.Draw(label)
    text_x = qr_side + 12
    text_w = w - text_x - 4

    code_font = _load_font(48)
    name_font = _load_font(22)
    meta_font = _load_font(18)

    y = 4
    draw.text((text_x, y), print_code, fill="black", font=code_font)
    y += 54

    for line in _wrap(draw, genotype_name, name_font, text_w)[:3]:
        draw.text((text_x, y), line, fill="black", font=name_font)
        y += 26

    if donor_strain_id:
        draw.text(
            (text_x, h - 22),
            f"donor#{donor_strain_id}",
            fill="black",
            font=meta_font,
        )

    buf = BytesIO()
    label.save(buf, format="PNG", dpi=(DPI, DPI))
    return buf.getvalue()


def save_label(png_bytes: bytes, out_dir: Path, print_code: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{print_code}.png"
    path.write_bytes(png_bytes)
    return path
