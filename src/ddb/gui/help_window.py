# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — help_window                                               ║
# ║  « the manual, one keypress away »                               ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Non-modal window showing docs/manual/index.md: contents tree,   ║
# ║  find-as-you-type search, and F1 from any tab jumps straight to  ║
# ║  that tab's section. Screenshots scale down to fit the window.   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""In-app user manual.

The window renders the same Markdown file mkdocs publishes, so editing
``docs/manual/index.md`` updates both. It is a top-level, non-modal
window: users can keep it open beside DDB while they work.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QImage,
    QKeySequence,
    QShortcut,
    QTextBlock,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ddb.manual import MANUAL_PATH, load_manual, parse_sections, slugify

from .screen import clamp_to_screen

WINDOW_SIZE: tuple[int, int] = (1100, 760)
CONTENTS_WIDTH = 260
MAX_IMAGE_WIDTH = 820
IMAGE_SIDE_MARGIN = 48
NO_MATCH_STYLE = "color: #D55E00;"  # Wong vermilion
EXTERNAL_SCHEMES = {"http", "https", "mailto"}

# ┌────────────────────────────────────────────────────────────┐
# │ Window  « contents tree | search bar | rendered manual »   │
# └────────────────────────────────────────────────────────────┘


class ManualBrowser(QTextBrowser):
    """QTextBrowser that resolves images next to the manual and fits them."""

    def __init__(self, base_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_dir = base_dir
        self.setOpenLinks(False)
        self.document().setBaseUrl(QUrl.fromLocalFile(f"{base_dir}/"))

    def loadResource(self, kind: int, url: QUrl) -> object:  # noqa: N802 — Qt override
        if int(kind) == QTextDocument.ResourceType.ImageResource.value:
            image = QImage(str(self._resolve(url)))
            if not image.isNull():
                fit = min(MAX_IMAGE_WIDTH, max(200, self.viewport().width() - IMAGE_SIDE_MARGIN))
                if image.width() > fit:
                    image = image.scaledToWidth(fit, Qt.TransformationMode.SmoothTransformation)
                return image
        return super().loadResource(kind, url)

    def _resolve(self, url: QUrl) -> Path:
        if url.isLocalFile():
            return Path(url.toLocalFile())
        return self._base_dir / url.path()


class HelpWindow(QWidget):
    """The user manual with a contents tree and a search bar."""

    def __init__(self, manual_path: Path = MANUAL_PATH) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("DDB — User manual")
        self.resize(*clamp_to_screen(*WINDOW_SIZE, 0.9))
        self._manual_path = manual_path
        self._heading_blocks: dict[str, QTextBlock] = {}
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._pending_slug: str | None = None

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the manual…")
        self.search.setClearButtonEnabled(True)
        self.prev_btn = QPushButton("Previous")
        self.next_btn = QPushButton("Next")
        self.match_lbl = QLabel()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.next_btn)
        bar.addWidget(self.match_lbl)
        bar.addStretch()
        bar.addWidget(close_btn)

        self.contents = QTreeWidget()
        self.contents.setHeaderLabel("Contents")
        self.browser = ManualBrowser(manual_path.parent)

        splitter = QSplitter()
        splitter.addWidget(self.contents)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([CONTENTS_WIDTH, self.width() - CONTENTS_WIDTH])

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(splitter, 1)

        self.contents.itemClicked.connect(self._on_contents_clicked)
        self.browser.anchorClicked.connect(self._on_link)
        self.search.textChanged.connect(lambda _text: self.find(from_start=True))
        self.search.returnPressed.connect(self.find)
        self.next_btn.clicked.connect(self.find)
        self.prev_btn.clicked.connect(lambda: self.find(backward=True))
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.search.setFocus)
        QShortcut(QKeySequence.StandardKey.FindNext, self, activated=self.find)
        QShortcut(QKeySequence("Escape"), self, activated=self.close)

        self.reload()

    def reload(self) -> None:
        """(Re)read the manual from disk, e.g. after editing it."""
        try:
            markdown = load_manual(self._manual_path)
        except OSError:
            self.browser.setPlainText(
                f"The manual file is missing:\n{self._manual_path}\n\n"
                "Update DDB (Settings → Updates) or ask your admin."
            )
            return
        self.browser.setMarkdown(markdown)
        self._index_headings()
        self._build_contents(markdown)

    # ┌────────────────────────────────────────────────────────────┐
    # │ Navigation  « headings, links and search »                 │
    # └────────────────────────────────────────────────────────────┘

    def show_section(self, title_or_slug: str) -> bool:
        """Scroll to a heading; return False if the manual has no such heading."""
        slug = slugify(title_or_slug)
        if slug not in self._heading_blocks:
            return False
        if not self.isVisible():
            self._pending_slug = slug  # layout isn't final until shown
            return True
        self._scroll_to(slug)
        return True

    def section_slugs(self) -> list[str]:
        return list(self._heading_blocks)

    def find(self, *, backward: bool = False, from_start: bool = False) -> bool:
        """Select the next (or previous) match of the search text, wrapping around."""
        text = self.search.text()
        if not text:
            self.match_lbl.clear()
            return False
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if from_start:
            self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        found = self.browser.find(text, flags)
        if not found:  # wrap
            edge = QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start
            self.browser.moveCursor(edge)
            found = self.browser.find(text, flags)
        self.match_lbl.setText("" if found else "No matches")
        self.match_lbl.setStyleSheet("" if found else NO_MATCH_STYLE)
        return found

    def showEvent(self, event: object) -> None:  # noqa: N802 — Qt override
        super().showEvent(event)
        if self._pending_slug is not None:
            slug, self._pending_slug = self._pending_slug, None
            QTimer.singleShot(0, lambda: self._scroll_to(slug))

    def _scroll_to(self, slug: str) -> None:
        block = self._heading_blocks[slug]
        layout = self.browser.document().documentLayout()
        self.browser.verticalScrollBar().setValue(int(layout.blockBoundingRect(block).top()))
        cursor = QTextCursor(block)
        self.browser.setTextCursor(cursor)
        item = self._tree_items.get(slug)
        if item is not None:
            self.contents.setCurrentItem(item)

    def _index_headings(self) -> None:
        self._heading_blocks.clear()
        block = self.browser.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() > 0:
                self._heading_blocks.setdefault(slugify(block.text()), block)
            block = block.next()

    def _build_contents(self, markdown: str) -> None:
        self.contents.clear()
        self._tree_items.clear()
        parent: QTreeWidgetItem | None = None
        for section in parse_sections(markdown):
            if section.level == 2:
                parent = QTreeWidgetItem(self.contents, [section.title])
                item = parent
            elif section.level == 3 and parent is not None:
                item = QTreeWidgetItem(parent, [section.title])
            else:
                continue
            item.setData(0, Qt.ItemDataRole.UserRole, section.slug)
            self._tree_items[section.slug] = item

    def _on_contents_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        slug = item.data(0, Qt.ItemDataRole.UserRole)
        if slug in self._heading_blocks:
            self._scroll_to(slug)

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() in EXTERNAL_SCHEMES:
            QDesktopServices.openUrl(url)
        elif url.hasFragment() and url.fragment() in self._heading_blocks:
            self._scroll_to(url.fragment())


# ┌────────────────────────────────────────────────────────────┐
# │ Single instance  « one Help window, raised on reuse »      │
# └────────────────────────────────────────────────────────────┘

_window: HelpWindow | None = None


def show_help(section: str | None = None) -> HelpWindow:
    """Open (or raise) the manual, optionally at ``section``."""
    global _window
    if _window is None:
        _window = HelpWindow()
        _window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        _window.destroyed.connect(_forget_window)
    else:
        _window.reload()  # pick up edits to the Markdown without a restart
    if section is not None:
        _window.show_section(section)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def _forget_window(_obj: QObject | None = None) -> None:
    global _window
    _window = None
