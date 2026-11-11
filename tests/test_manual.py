"""User manual: structure, links, and drift against the live GUI.

The drift tests are the point of this file. They build the real main
window and fail when a button, checkbox or group box exists that the
manual never mentions, so a GUI change that skips the manual turns CI
red instead of silently leaving users with stale instructions.
"""

from __future__ import annotations

import os
import re

import pytest

from ddb.manual import (
    MANUAL_DIR,
    MANUAL_PATH,
    TAB_SECTIONS,
    anchor_targets,
    image_targets,
    load_manual,
    parse_sections,
    slugify,
)

# Labels that carry no user-facing meaning of their own.
UNDOCUMENTED_LABELS: set[str] = {
    "?",  # inline "what's this" buttons next to settings
}
# mkdocs-only syntax that Qt's Markdown renderer prints literally.
MKDOCS_ONLY = re.compile(r"^\s*(!!!|\?\?\?|===)\s|\{:\s|\{\s*\.", re.MULTILINE)


@pytest.fixture(scope="module")
def manual() -> str:
    return load_manual()


def normalise(label: str) -> str:
    """Compare labels the way a reader sees them."""
    text = label.replace("&", "").replace("…", "").replace("...", "")
    text = text.strip().lstrip("+").strip()
    return re.sub(r"\s+", " ", text).lower()


# ┌────────────────────────────────────────────────────────────┐
# │ Structure  « headings, links, images, portable syntax »    │
# └────────────────────────────────────────────────────────────┘


def test_manual_exists_with_a_title(manual: str) -> None:
    assert MANUAL_PATH.is_file()
    assert parse_sections(manual)[0].level == 1


def test_every_tab_has_its_f1_section(manual: str) -> None:
    slugs = {section.slug for section in parse_sections(manual)}
    for tab, heading in TAB_SECTIONS.items():
        assert slugify(heading) in slugs, f"F1 on the {tab} tab needs a '{heading}' heading"


def test_internal_links_resolve(manual: str) -> None:
    slugs = {section.slug for section in parse_sections(manual)}
    broken = [target for target in anchor_targets(manual) if target not in slugs]
    assert not broken, f"links to missing headings: {broken}"


def test_images_exist(manual: str) -> None:
    missing = [target for target in image_targets(manual) if not (MANUAL_DIR / target).is_file()]
    assert not missing, f"run scripts/make_manual_screenshots.py; missing: {missing}"


def test_no_mkdocs_only_syntax(manual: str) -> None:
    hits = [m.group(0).strip() for m in MKDOCS_ONLY.finditer(manual)]
    assert not hits, f"the in-app viewer can't render these; use '> **Tip:**' instead: {hits}"


def test_slugs_match_python_markdown() -> None:
    markdown = pytest.importorskip("markdown")
    from markdown.extensions.toc import slugify as toc_slugify

    for title in ("Scan tab", "Flipping a vial", "Settings → Updates", "What's a *flip*?"):
        assert slugify(title) == toc_slugify(re.sub(r"[*_`]", "", title), "-")
    assert markdown  # imported for its side effect only


# ┌────────────────────────────────────────────────────────────┐
# │ Drift  « every control in the GUI is in the manual »       │
# └────────────────────────────────────────────────────────────┘


@pytest.fixture(scope="module")
def main_window():  # noqa: ANN201
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ddb.config import settings
    from ddb.gui.main_window import MainWindow

    saved = (settings.printer_enabled, settings.gui_update_check)
    settings.printer_enabled = False
    settings.gui_update_check = False
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()
    settings.printer_enabled, settings.gui_update_check = saved


def gui_labels(window) -> dict[str, set[str]]:  # noqa: ANN001
    """Tab title → every button/checkbox/group label on that tab."""
    from PySide6.QtWidgets import QAbstractButton, QGroupBox

    labels: dict[str, set[str]] = {}
    for index in range(window.tabs.count()):
        page = window.tabs.widget(index)
        found = {b.text() for b in page.findChildren(QAbstractButton)}
        found |= {g.title() for g in page.findChildren(QGroupBox)}
        labels[window.tabs.tabText(index)] = {t for t in found if t.strip()}
    return labels


def test_every_gui_label_is_documented(main_window, manual: str) -> None:  # noqa: ANN001
    text = normalise(manual)
    undocumented = {}
    for tab, labels in gui_labels(main_window).items():
        missing = sorted(
            label
            for label in labels - UNDOCUMENTED_LABELS
            if normalise(re.sub(r"^.*'s vials$", "'s vials", label)) not in text
        )
        if missing:
            undocumented[tab] = missing
    assert not undocumented, (
        "These GUI labels are not mentioned in docs/manual/index.md. Document them "
        f"(or rename them consistently): {undocumented}"
    )


def test_help_button_opens_manual_at_current_tab(main_window) -> None:  # noqa: ANN001
    from PySide6.QtWidgets import QApplication

    from ddb.gui import help_window

    main_window.tabs.setCurrentIndex(3)  # Settings
    main_window.help_btn.click()
    for _ in range(5):
        QApplication.processEvents()
    window = help_window._window
    assert window is not None and window.isVisible()
    assert window.contents.currentItem().text(0) == TAB_SECTIONS["Settings"]
    assert window.browser.verticalScrollBar().value() > 0, "scrolled down to the section"
    window.search.setText("vial")
    assert window.match_lbl.text() == ""
    window.search.setText("zzz-no-such-text")
    assert window.match_lbl.text() == "No matches"
    window.close()
