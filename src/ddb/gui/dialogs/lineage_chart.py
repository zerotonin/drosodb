# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — gui.dialogs.lineage_chart                                 ║
# ║  « genealogy backwards and forwards, as a chart »                ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Zoomable flow chart of a vial's ancestors and descendants.      ║
# ║  Double-click a card for that vial's audit trail.                ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Genealogy dialog: :class:`LineageScene` in a pan / zoom view."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QShowEvent, QWheelEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session

from ddb.db import engine
from ddb.gui import theme
from ddb.gui.lineage_scene import LineageScene, VialCard
from ddb.gui.screen import clamp_to_screen
from ddb.lineage import LineageGraph, lineage_graph

PREFERRED_SIZE: tuple[int, int] = (1100, 720)
ZOOM_STEP = 1.2
MIN_SCALE = 0.25
MAX_SCALE = 2.5
MIN_READABLE_SCALE = 0.6
FIT_MARGIN = 30.0


def show_lineage(parent: QWidget, vial_id: int) -> None:
    """Load a vial's genealogy and show it modally; warn if the vial is gone."""
    try:
        with Session(engine) as session:
            graph = lineage_graph(session, vial_id)
    except LookupError as e:
        QMessageBox.warning(parent, "Genealogy", str(e))
        return
    dialog = LineageDialog(graph, parent)
    dialog.exec()
    dialog.deleteLater()  # parented to the window: free it, don't keep one per opening


class LineageDialog(QDialog):
    """Ancestors (left) → selected vial → descendants (right)."""

    def __init__(self, graph: LineageGraph, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        focus = graph.nodes[graph.focus_id]
        self.setWindowTitle(f"Genealogy — {focus.print_code}")
        self.setSizeGripEnabled(True)
        self.resize(*clamp_to_screen(*PREFERRED_SIZE, 0.92))

        self.scene = LineageScene(graph)
        self.scene.vial_activated.connect(self._open_trail)
        self.view = _ZoomView(self.scene)

        self.audit_btn = QPushButton("Audit trail…")
        self.audit_btn.setToolTip("Audit trail of the selected card (or of the highlighted vial)")
        self.audit_btn.clicked.connect(self._open_trail_for_selection)
        zoom_out = QPushButton("−")
        zoom_in = QPushButton("+")
        fit = QPushButton("Fit")
        for button in (zoom_out, zoom_in):
            button.setFixedWidth(button.sizeHint().height() + 8)
        # Bound methods, not lambdas: PySide keeps a lambda alive on the C++
        # side, and one closing over self would keep the dialog (and its whole
        # scene) alive until exit, where PySide 6.12 crashes tearing it down.
        zoom_out.clicked.connect(self._zoom_out)
        zoom_in.clicked.connect(self._zoom_in)
        fit.clicked.connect(self._fit)

        buttons = QHBoxLayout()
        for widget in (zoom_out, zoom_in, fit):
            buttons.addWidget(widget)
        buttons.addSpacing(12)
        buttons.addWidget(self.audit_btn)
        buttons.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        # Read-only dialog: start on Close, not on the first table cell / zoom button.
        close.button(QDialogButtonBox.StandardButton.Close).setFocus()
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(_summary_label(graph))
        layout.addWidget(_legend_label())
        layout.addWidget(self.view, stretch=1)
        layout.addLayout(buttons)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        self.view.fit(self.scene.focus_card)

    def _zoom_in(self) -> None:
        self.view.zoom_by(ZOOM_STEP)

    def _zoom_out(self) -> None:
        self.view.zoom_by(1 / ZOOM_STEP)

    def _open_trail_for_selection(self) -> None:
        selected = [item for item in self.scene.selectedItems() if isinstance(item, VialCard)]
        card = selected[0] if selected else self.scene.focus_card
        self._open_trail(card.node.vial_id)

    def _open_trail(self, vial_id: int) -> None:
        from ddb.gui.dialogs.audit_trail import show_audit_trail  # avoid import cycle

        show_audit_trail(self, vial_id, allow_genealogy=False)


class _ZoomView(QGraphicsView):
    """Drag to pan, Ctrl + wheel (or the buttons) to zoom."""

    def __init__(self, scene: LineageScene) -> None:
        super().__init__(scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def scale_factor(self) -> float:
        return self.transform().m11()

    def zoom_by(self, factor: float) -> None:
        current = self.scale_factor()
        target = min(max(current * factor, MIN_SCALE), MAX_SCALE)
        self.scale(target / current, target / current)

    def fit(self, focus: VialCard) -> None:
        """Fit the whole chart; never enlarge past 100 %, never shrink past readable."""
        rect = (
            self.scene()
            .itemsBoundingRect()
            .adjusted(-FIT_MARGIN, -FIT_MARGIN, FIT_MARGIN, FIT_MARGIN)
        )
        self.resetTransform()
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        scale = self.scale_factor()
        if scale > 1.0:
            self.resetTransform()
        elif scale < MIN_READABLE_SCALE:
            self.resetTransform()
            self.scale(MIN_READABLE_SCALE, MIN_READABLE_SCALE)
            self.centerOn(focus)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(ZOOM_STEP ** (event.angleDelta().y() / 120))
            event.accept()
            return
        super().wheelEvent(event)


def _summary_label(graph: LineageGraph) -> QLabel:
    focus = graph.nodes[graph.focus_id]
    n_up, n_down = len(graph.ancestor_ids), len(graph.descendant_ids)
    label = QLabel(
        f"<span style='font-size:large;'><b>{focus.print_code}</b></span>"
        f" &nbsp;·&nbsp; {focus.genotype_name or '—'}"
        f" &nbsp;·&nbsp; {n_up} ancestor{'s' if n_up != 1 else ''},"
        f" {n_down} descendant{'s' if n_down != 1 else ''}"
    )
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


def _legend_label() -> QLabel:
    def swatch(colour: str, text: str, glyph: str = "■") -> str:
        return f"<span style='color:{colour}; font-size:large;'>{glyph}</span>&nbsp;{text}"

    parts = [
        swatch(theme.LINEAGE_FOCUS, "selected vial"),
        swatch(theme.LINEAGE_ACTIVE, "active"),
        swatch(theme.LINEAGE_RETIRED, "retired", "□"),
        swatch(theme.LINEAGE_EDGE, "flipped into", "→"),
    ]
    label = QLabel(
        " &nbsp;&nbsp; ".join(parts)
        + f" &nbsp;&nbsp; <span style='color:{theme.LINEAGE_MUTED_TEXT};'>"
        "Double-click a card for its audit trail · drag to pan · Ctrl + scroll to zoom</span>"
    )
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    return label
