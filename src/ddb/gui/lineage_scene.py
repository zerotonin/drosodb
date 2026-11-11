# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — gui.lineage_scene                                         ║
# ║  « the genealogy flow chart as a graphics scene »                ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Vial cards and curved parent→child arrows laid out by           ║
# ║  ddb.lineage_layout. Double-clicking a card reports its vial id. ║
# ╚══════════════════════════════════════════════════════════════════╝
"""QGraphicsScene that draws a :class:`ddb.lineage.LineageGraph`."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsSimpleTextItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from ddb.gui import theme
from ddb.lineage import LineageEdge, LineageGraph, LineageNode
from ddb.lineage_layout import Placement, layout_layers

COLUMN_GAP = 70.0
ROW_GAP = 20.0
CARD_PADDING = 9.0
CARD_RADIUS = 9.0
ACCENT_BAR = 5.0
ARROW_SIZE = 7.0
HEADER_GAP = 14.0
CROSS_ROLE_MARKS: dict[str, str] = {"mother": "♀", "father": "♂"}


@dataclass(frozen=True)
class CardFonts:
    """Fonts and card geometry derived from the app font (honours font scale)."""

    code: QFont
    body: QFont
    small: QFont
    width: float
    height: float

    @classmethod
    def from_app(cls) -> CardFonts:
        body = QFont(QApplication.font())
        code = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        code.setPointSizeF(body.pointSizeF() * 1.15)
        code.setBold(True)
        small = QFont(body)
        small.setPointSizeF(body.pointSizeF() * 0.85)
        body_m, code_m, small_m = QFontMetrics(body), QFontMetrics(code), QFontMetrics(small)
        width = max(176.0, body_m.horizontalAdvance("M") * 15.0)
        height = 2 * CARD_PADDING + code_m.height() + body_m.height() + small_m.height() + 6
        return cls(code=code, body=body, small=small, width=width, height=height)


# ┌────────────────────────────────────────────────────────────┐
# │ Cards  « one vial: code, generation, genotype, date »      │
# └────────────────────────────────────────────────────────────┘


class VialCard(QGraphicsObject):
    """Rounded card for one vial. Emits ``activated(vial_id)`` on double-click."""

    activated = Signal(int)

    def __init__(self, node: LineageNode, fonts: CardFonts, *, is_focus: bool) -> None:
        super().__init__()
        self.node = node
        self._fonts = fonts
        self._is_focus = is_focus
        self._hovered = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(_card_tooltip(node))
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(14)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 55))
        self.setGraphicsEffect(shadow)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-2, -2, self._fonts.width + 4, self._fonts.height + 4)

    def left_anchor(self) -> QPointF:
        return self.pos() + QPointF(0, self._fonts.height / 2)

    def right_anchor(self) -> QPointF:
        return self.pos() + QPointF(self._fonts.width, self._fonts.height / 2)

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self._fonts.width, self._fonts.height)
        border, fill, accent = self._colours()

        pen = QPen(QColor(border), 3.0 if self._is_focus else 1.4)
        if not self.node.is_active and not self._is_focus:
            pen.setStyle(Qt.PenStyle.DashLine)
        if self._hovered:
            pen.setWidthF(pen.widthF() + 1.0)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(fill)))
        painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

        if accent is not None:
            bar = QPainterPath()
            bar.addRoundedRect(
                QRectF(0, 0, ACCENT_BAR * 2, rect.height()), CARD_RADIUS, CARD_RADIUS
            )
            clip = QPainterPath()
            clip.addRect(QRectF(0, 0, ACCENT_BAR, rect.height()))
            painter.fillPath(bar.intersected(clip), QColor(accent))

        if self.isSelected():
            painter.setPen(QPen(QColor(theme.ACCENT), 2.0, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), CARD_RADIUS + 3, CARD_RADIUS + 3)

        self._paint_text(painter, rect)

    def _colours(self) -> tuple[str, str, str | None]:
        """Border, fill and left accent bar for this card's state."""
        if self._is_focus:
            return theme.LINEAGE_FOCUS, theme.LINEAGE_FOCUS_FILL, theme.LINEAGE_FOCUS
        if self.node.is_active:
            return theme.LINEAGE_ACTIVE, theme.LINEAGE_CARD_FILL, theme.LINEAGE_ACTIVE
        return theme.LINEAGE_RETIRED, theme.LINEAGE_RETIRED_FILL, None

    def _paint_text(self, painter: QPainter, rect: QRectF) -> None:
        f = self._fonts
        left = CARD_PADDING + ACCENT_BAR
        inner_width = rect.width() - left - CARD_PADDING
        text_colour = QColor(
            theme.LINEAGE_TEXT
            if self.node.is_active or self._is_focus
            else theme.LINEAGE_MUTED_TEXT
        )
        y = CARD_PADDING

        painter.setFont(f.code)
        painter.setPen(text_colour)
        code_h = QFontMetrics(f.code).height()
        painter.drawText(
            QRectF(left, y, inner_width, code_h), Qt.AlignmentFlag.AlignLeft, self.node.print_code
        )

        status = "active" if self.node.is_active else "retired"
        painter.setFont(f.small)
        painter.setPen(
            QColor(theme.LINEAGE_ACTIVE if self.node.is_active else theme.LINEAGE_RETIRED)
        )
        painter.drawText(
            QRectF(left, y + 2, inner_width, code_h),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            status,
        )
        y += code_h + 3

        painter.setFont(f.body)
        painter.setPen(text_colour)
        body_m = QFontMetrics(f.body)
        genotype = body_m.elidedText(
            self.node.genotype_name or "—", Qt.TextElideMode.ElideRight, int(inner_width)
        )
        painter.drawText(
            QRectF(left, y, inner_width, body_m.height()), Qt.AlignmentFlag.AlignLeft, genotype
        )
        y += body_m.height() + 3

        painter.setFont(f.small)
        painter.setPen(QColor(theme.LINEAGE_MUTED_TEXT))
        meta = f"Gen {self.node.generation} · {self.node.created_at.date().isoformat()}"
        painter.drawText(
            QRectF(left, y, inner_width, QFontMetrics(f.small).height()),
            Qt.AlignmentFlag.AlignLeft,
            meta,
        )

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self.activated.emit(self.node.vial_id)
        event.accept()


def _card_tooltip(node: LineageNode) -> str:
    lines = [
        f"<b>{node.print_code}</b> — generation {node.generation}",
        node.genotype_name or "",
        f"Created {node.created_at.date().isoformat()}",
    ]
    if node.decommissioned_at is not None:
        lines.append(f"Retired {node.decommissioned_at.date().isoformat()}")
    lines.append("<i>Double-click for the audit trail</i>")
    return "<br>".join(line for line in lines if line)


# ┌────────────────────────────────────────────────────────────┐
# │ Arrows  « curved parent → child links »                    │
# └────────────────────────────────────────────────────────────┘


class LinkArrow(QGraphicsPathItem):
    """S-curve from a parent card's right edge to a child card's left edge."""

    def __init__(self, start: QPointF, end: QPointF, edge: LineageEdge) -> None:
        super().__init__()
        is_cross = edge.kind != "flip"
        colour = QColor(theme.LINEAGE_CROSS_EDGE if is_cross else theme.LINEAGE_EDGE)
        pen = QPen(colour, 1.6)
        if is_cross:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setZValue(-1)

        tip = end - QPointF(ARROW_SIZE, 0)
        bend = max((tip.x() - start.x()) / 2, 20.0)
        path = QPainterPath(start)
        path.cubicTo(start + QPointF(bend, 0), tip - QPointF(bend, 0), tip)
        head = QPolygonF(
            [end, tip + QPointF(0, -ARROW_SIZE / 1.6), tip + QPointF(0, ARROW_SIZE / 1.6)]
        )
        path.addPolygon(head)
        path.closeSubpath()
        self.setPath(path)
        self._head = head
        self._colour = colour
        mark = CROSS_ROLE_MARKS.get(edge.kind)
        if mark is not None:
            label = QGraphicsSimpleTextItem(mark, self)
            label.setBrush(colour)
            label.setPos(tip - QPointF(18, 18))

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        super().paint(painter, option, widget)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._colour)
        painter.drawPolygon(self._head)


# ┌────────────────────────────────────────────────────────────┐
# │ Scene  « cards + arrows + generation headers »             │
# └────────────────────────────────────────────────────────────┘


class LineageScene(QGraphicsScene):
    """Lays out and draws a lineage graph. Re-emits card double-clicks."""

    vial_activated = Signal(int)

    def __init__(self, graph: LineageGraph) -> None:
        super().__init__()
        self.graph = graph
        self.setBackgroundBrush(QColor(theme.LINEAGE_BACKGROUND))
        self.cards: dict[int, VialCard] = {}
        self._build()

    @property
    def focus_card(self) -> VialCard:
        return self.cards[self.graph.focus_id]

    def _build(self) -> None:
        fonts = CardFonts.from_app()
        nodes = self.graph.nodes
        placements = layout_layers(
            nodes,
            [(e.parent_id, e.child_id) for e in self.graph.edges],
            order_key=lambda n: (nodes[n].created_at, n),
        )
        for vial_id, place in placements.items():
            card = VialCard(nodes[vial_id], fonts, is_focus=vial_id == self.graph.focus_id)
            card.setPos(
                place.layer * (fonts.width + COLUMN_GAP), place.slot * (fonts.height + ROW_GAP)
            )
            card.activated.connect(self.vial_activated)
            self.addItem(card)
            self.cards[vial_id] = card

        for edge in self.graph.edges:
            parent, child = self.cards[edge.parent_id], self.cards[edge.child_id]
            self.addItem(LinkArrow(parent.right_anchor(), child.left_anchor(), edge))

        self._add_layer_headers(placements, fonts)

    def _add_layer_headers(self, placements: dict[int, Placement], fonts: CardFonts) -> None:
        layers: dict[int, set[int]] = {}
        for vial_id, place in placements.items():
            layers.setdefault(place.layer, set()).add(self.graph.nodes[vial_id].generation)
        focus_layer = placements[self.graph.focus_id].layer
        header_font = QFont(fonts.small)
        header_font.setBold(True)
        top = min(card.pos().y() for card in self.cards.values())
        for layer, generations in layers.items():
            text = (
                f"Generation {next(iter(generations))}"
                if len(generations) == 1
                else f"Step {layer + 1}"
            )
            item = QGraphicsSimpleTextItem(text.upper())
            item.setFont(header_font)
            is_focus_layer = layer == focus_layer
            item.setBrush(
                QColor(theme.LINEAGE_FOCUS if is_focus_layer else theme.LINEAGE_MUTED_TEXT)
            )
            width = item.boundingRect().width()
            x = layer * (fonts.width + COLUMN_GAP) + (fonts.width - width) / 2
            item.setPos(x, top - item.boundingRect().height() - HEADER_GAP)
            self.addItem(item)
