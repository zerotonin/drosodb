"""Render a 17×54mm vial label (DK-11204) as a PNG.

Layout at 300 DPI (≈ 201×638 pixels):

    +------+-----------------------------------------------+
    |      |  <print code, large>   g<N> · YYYY-MM-DD      |
    | QR   |  <genotype notation, wrapping>                |
    |      |  <short name, same size as notation>          |
    |      |  @<owner>                       donor#<id>    |
    +------+-----------------------------------------------+

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
    owner_username: str | None = None,
    generation: int | None = None,
    genotype_notation: str | None = None,
    created_date: str | None = None,
) -> bytes:
    """Return PNG bytes for the label.

    When `genotype_notation` is provided (e.g. `+/+ ; GAL4/CyO ; +/+`) it
    becomes the dominant middle text and `genotype_name` drops to a small
    italic subtitle. When notation is None, we fall back to the old layout
    with the name as the main text — keeps existing callers/tests working.
    """
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

    # Top row: print code + optional g<N> · YYYY-MM-DD badge. The print
    # code shrinks (48→36px) if absurdly long; the badge shrinks its own
    # font ladder and drops the date before the generation.
    meta_font = _load_font(18)
    code_font, badge_font, badge_text = _fit_top_row(
        draw,
        print_code=print_code,
        generation=generation,
        created_date=created_date,
        max_width=text_w,
    )
    y = 4
    draw.text((text_x, y), print_code, fill="black", font=code_font)
    if badge_text:
        code_w = int(draw.textlength(print_code, font=code_font))
        # Vertically centre the small badge against the big print code.
        badge_y = y + (code_font.size - badge_font.size) // 2 + 2
        draw.text((text_x + code_w + 12, badge_y), badge_text, fill="#444", font=badge_font)
    y += code_font.size + 8

    # Middle zone: notation (primary) + short name (subtitle), same font size.
    # Picking one font shared by both keeps the label readable at a glance —
    # biologists scan for the expression first, the alias second.
    primary_text = genotype_notation or genotype_name
    subtitle_text: str | None = None
    if genotype_notation and genotype_name and genotype_name != genotype_notation:
        subtitle_text = genotype_name

    body_font, primary_lines, subtitle_lines = _fit_body(
        draw, primary_text, subtitle_text, text_w
    )
    line_h = body_font.size + 4
    for line in primary_lines:
        draw.text((text_x, y), line, fill="black", font=body_font)
        y += line_h
    for line in subtitle_lines:
        draw.text((text_x, y), line, fill="#444", font=body_font)
        y += line_h

    meta_y = h - 22

    # Bottom strip: owner (left) + donor#id (right). Split `text_w` between
    # them so that a long username or a long donor id can never collide —
    # each side gets ellipsised independently if it overflows its half.
    owner_text = f"@{owner_username}" if owner_username else ""
    donor_text = f"donor#{donor_strain_id}" if donor_strain_id else ""
    # Donor gets priority on its natural width, owner gets whatever's left.
    donor_w = int(draw.textlength(donor_text, font=meta_font)) if donor_text else 0
    donor_budget = min(donor_w, text_w - 40)  # always leave 40px for owner
    owner_budget = text_w - donor_budget - 8  # 8px separator gap
    if owner_text:
        owner_text = _clip_to_width(draw, owner_text, meta_font, owner_budget)
        draw.text((text_x, meta_y), owner_text, fill="black", font=meta_font)
    if donor_text:
        donor_text = _clip_to_width(draw, donor_text, meta_font, donor_budget)
        donor_w = int(draw.textlength(donor_text, font=meta_font))
        draw.text((w - donor_w - 6, meta_y), donor_text, fill="black", font=meta_font)

    buf = BytesIO()
    label.save(buf, format="PNG", dpi=(DPI, DPI))
    return buf.getvalue()


def _clip_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    """Ellipsise `text` until it fits `max_width`. Returns `text` unchanged if already fits."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    clipped = text
    while clipped and draw.textlength(clipped + "…", font=font) > max_width:
        clipped = clipped[:-1]
    return clipped + "…" if clipped else ""


def _fit_top_row(
    draw: ImageDraw.ImageDraw,
    *,
    print_code: str,
    generation: int | None,
    created_date: str | None,
    max_width: int,
) -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, str]:
    """Choose fonts for the print code + metadata badge so they fit.

    Priority when space is tight (date > generation, since the date never
    changes but the generation number is often inferrable from lineage):

      1. Start with 48px code + 22px badge.
      2. If total doesn't fit, shrink badge: 22→18→14.
      3. If still doesn't fit, drop the generation from the badge.
      4. If still doesn't fit, shrink the print code: 48→40→32.
      5. If still doesn't fit, ellipsise the print code (last resort).
    """
    gap = 12

    def _compose(gen_ok: bool) -> str:
        parts: list[str] = []
        if gen_ok and generation is not None and generation > 0:
            parts.append(f"g{generation}")
        if created_date:
            parts.append(created_date)
        return " · ".join(parts)

    for code_size in (48, 40, 32):
        code_font = _load_font(code_size)
        code_w = int(draw.textlength(print_code, font=code_font))
        for gen_ok in (True, False):
            badge_text = _compose(gen_ok)
            if not badge_text:
                # Nothing to show on the right — the code alone just has to fit.
                if code_w <= max_width:
                    return code_font, _load_font(22), ""
                continue
            for badge_size in (22, 18, 14):
                badge_font = _load_font(badge_size)
                badge_w = int(draw.textlength(badge_text, font=badge_font))
                if code_w + gap + badge_w <= max_width:
                    return code_font, badge_font, badge_text
    # Last resort: ellipsise the print code at 32px, no badge.
    code_font = _load_font(32)
    return code_font, _load_font(14), ""


def _fit_body(
    draw: ImageDraw.ImageDraw,
    primary: str,
    subtitle: str | None,
    max_width: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], list[str]]:
    """Pick one font size that fits primary + subtitle in ≤3 lines.

    Subtitle is always 1 line (no wrap). Primary wraps on "; " first (the
    chromosome separator) and may take 2 lines when a subtitle is present,
    or 3 lines otherwise. We try 26→24→22→20 px and pick the biggest that
    fits; if nothing fits at 20, we drop the subtitle to free a line.
    """
    for size in (26, 24, 22, 20):
        font = _load_font(size)
        p_lines = _wrap_semicolons(draw, primary, font, max_width)
        p_fits = all(draw.textlength(line, font=font) <= max_width for line in p_lines)
        if not p_fits:
            continue
        if subtitle is not None:
            if draw.textlength(subtitle, font=font) > max_width:
                continue  # subtitle alone doesn't fit — try smaller
            if len(p_lines) <= 2:
                return font, p_lines, [subtitle]
        else:
            if len(p_lines) <= 3:
                return font, p_lines, []
    # Fallback: smallest size. Ellipsise any overflowing line — prefer
    # truncating the subtitle over dropping it silently, so the biologist
    # always sees at least a partial alias on the label.
    font = _load_font(20)
    p_lines = _wrap_semicolons(draw, primary, font, max_width)
    p_lines = p_lines[:3 if subtitle is None else 2]
    p_lines = [_clip_to_width(draw, line, font, max_width) for line in p_lines]
    if subtitle is not None:
        return font, p_lines, [_clip_to_width(draw, subtitle, font, max_width)]
    return font, p_lines, []


def _wrap_semicolons(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Prefer to break at `; ` (chromosome separator) before word-wrapping."""
    if draw.textlength(text, font=font) <= max_width:
        return [text]
    if "; " in text:
        chunks = [c.strip() for c in text.split(";") if c.strip()]
        lines: list[str] = []
        cur = ""
        for i, chunk in enumerate(chunks):
            piece = chunk if i == len(chunks) - 1 else chunk + " ;"
            trial = f"{cur} {piece}".strip() if cur else piece
            if draw.textlength(trial, font=font) <= max_width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = piece
        if cur:
            lines.append(cur)
        # If any line still too wide (e.g. one chromosome that alone overflows),
        # fall back to word-wrap on just that line.
        out: list[str] = []
        for line in lines:
            if draw.textlength(line, font=font) <= max_width:
                out.append(line)
            else:
                out.extend(_wrap(draw, line, font, max_width))
        return out
    return _wrap(draw, text, font, max_width)


def save_label(png_bytes: bytes, out_dir: Path, print_code: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{print_code}.png"
    path.write_bytes(png_bytes)
    return path
