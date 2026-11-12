"""scripts/build_manual_pdf.py: page breaks, picture width, and a real build."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_manual_pdf.py"


@pytest.fixture(scope="module")
def pdf():
    spec = importlib.util.spec_from_file_location("build_manual_pdf", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sections_start_on_a_new_page_except_the_first(pdf, tmp_path):
    html = pdf.to_html("# T\n\n## One\n\ntext\n\n### Two\n\n## Three\n", tmp_path, 500)
    assert html.count("page-break-before:always") == 2


def test_pictures_get_the_text_width(pdf, tmp_path):
    html = pdf.to_html("# T\n\n![x](img/a.png)\n", tmp_path, 512)
    assert 'width="512"' in html


def test_builds_the_real_manual(pdf, tmp_path):
    out = pdf.build(pdf.MANUAL_PATH, tmp_path / "m.pdf", "9.9.9")
    assert out.read_bytes()[:5] == b"%PDF-"
    assert out.stat().st_size > 100_000  # the screenshots are in it
