#!/usr/bin/env python
# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — build_manual_pdf                                          ║
# ║  « docs/manual/index.md → one PDF »                              ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Qt reads the Markdown and prints it: no pandoc or LaTeX needed. ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Build the user manual PDF from its Markdown and screenshots.

Usage::

    python scripts/build_manual_pdf.py [--md docs/manual/index.md] [--out PDF]

Every chapter and section (``##``, ``###``) starts on a new page; pictures are
scaled to the text width.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QMarginsF, QSizeF, QUrl  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QFont,
    QPageLayout,
    QPageSize,
    QPdfWriter,
    QTextDocument,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from ddb import __version__  # noqa: E402
from ddb.manual import MANUAL_DIR, MANUAL_PATH  # noqa: E402

PDF_PATH = MANUAL_DIR / "ddb_manual.pdf"
PDF_TITLE = "DDB user manual"
DPI = 300
SCREEN_DPI = 96
MARGIN_MM = 16
BODY_PT = 10
STYLE = (
    "h1 { font-size: 22pt; } h2 { font-size: 16pt; } h3 { font-size: 13pt; } "
    "td, th { padding: 3px 6px; } code { font-family: monospace; }"
)


def to_html(md: str, base: Path, text_w: int) -> str:
    """Markdown → HTML with section page breaks and pictures at the text width.

    Args:
        md: Manual text.
        base: Folder the image links are relative to.
        text_w: Picture width in screen pixels.

    Returns:
        HTML for a QTextDocument.
    """
    doc = QTextDocument()
    doc.setBaseUrl(QUrl.fromLocalFile(str(base) + "/"))
    doc.setMarkdown(md)
    html = doc.toHtml()
    html = re.sub(r"<img ([^>]*?)/?>", lambda m: _sized_img(m.group(1), text_w), html)
    first = True

    def page_break(m: re.Match) -> str:
        nonlocal first
        if first:  # the first chapter stays on the title page
            first = False
            return m.group(0)
        tag = m.group(0)[:3]
        return m.group(0).replace(tag, f'{tag} style="page-break-before:always;"', 1)

    return re.sub(r"<h[23][^>]*>", page_break, html)


def _sized_img(attrs: str, text_w: int) -> str:
    attrs = re.sub(r'\s(width|height)="[^"]*"', "", attrs)
    return f'<img {attrs} width="{text_w}" />'


def _writer(out: Path) -> QPdfWriter:
    writer = QPdfWriter(str(out))
    writer.setResolution(DPI)
    writer.setTitle(PDF_TITLE)
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(MARGIN_MM, MARGIN_MM, MARGIN_MM, MARGIN_MM),
            QPageLayout.Unit.Millimeter,
        )
    )
    return writer


def build(md_path: Path, out: Path, version: str) -> Path:
    """Write the manual at ``md_path`` as a PDF.

    Args:
        md_path: Manual Markdown file.
        out: PDF to write.
        version: Shown under the title, with today's date.

    Returns:
        ``out``.
    """
    md = md_path.read_text(encoding="utf-8")
    md = md.replace("\n", f"\n\n*Version {version}, {date.today():%d %B %Y}*\n\n", 1)
    writer = _writer(out)
    page = writer.pageLayout().paintRectPixels(DPI)
    doc = QTextDocument()
    doc.documentLayout().setPaintDevice(writer)  # lay out at the PDF's resolution
    doc.setPageSize(QSizeF(page.width(), page.height()))
    doc.setBaseUrl(QUrl.fromLocalFile(str(md_path.parent) + "/"))
    font = QFont()
    font.setPointSize(BODY_PT)
    doc.setDefaultFont(font)
    doc.setDefaultStyleSheet(STYLE)
    text_w = int(page.width() * 0.96 * SCREEN_DPI / DPI)  # <img width> is in screen px
    doc.setHtml(to_html(md, md_path.parent, text_w))
    doc.print_(writer)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--md", type=Path, default=MANUAL_PATH)
    ap.add_argument("--out", type=Path, default=PDF_PATH)
    a = ap.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841 - must stay alive
    out = build(a.md, a.out, __version__)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
