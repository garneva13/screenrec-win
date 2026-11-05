"""Icons, stroked into a QPainter rather than loaded from files.

Three reasons they are drawn and not shipped as images:

* A ``QIcon`` keeps the colour it was painted with. Strokes read the palette at paint
  time, so a widget that changes state - a toggle going green, a chip going dim - needs
  no cache to invalidate and no second asset.
* There is no icon font to depend on, and no licence to carry.
* They are tiny. Every one here is described on a 24x24 grid and scaled to whatever
  rectangle it is given, so one definition serves the 17 px glyph on a bar button and
  the 26 px one on a source card.

The grid is 24x24 with the drawing inset to roughly 3..21, which leaves the optical
margin a 32 px button wants without the caller doing arithmetic.

`draw` never raises for an unknown name - it draws a placeholder and returns False. An
exception escaping a live QPainter takes the interpreter down, and a mistyped icon name
is not worth a crash; the caller can assert on the return value in a test instead.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui

GRID = 24.0

#: Every name `draw` knows. A test walks this, so adding one here is enough to have it
#: checked for a silent no-op.
KINDS: tuple[str, ...] = (
    "monitor",
    "monitor_rotated",
    "window",
    "region",
    "microphone",
    "microphone_off",
    "speaker",
    "speaker_off",
    "pointer",
    "cursor",
    "settings",
    "chevron_down",
    "chevron_up",
    "chevron_right",
    "record",
    "stop",
    "pause",
    "trash",
    "refresh",
    "check",
    "close",
    "folder",
    "grip",
    "help",
    "warning",
    "camera",
)


def _pen(colour: QtGui.QColor, width: float) -> QtGui.QPen:
    pen = QtGui.QPen(colour, width)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    return pen


def draw(
    painter: QtGui.QPainter,
    kind: str,
    rect: QtCore.QRectF | QtCore.QRect,
    colour: QtGui.QColor | str,
    weight: float = 1.9,
) -> bool:
    """Stroke `kind` inside `rect`. Returns False for a name it does not know.

    The painter's transform is saved and restored, so the caller's state survives.
    """
    box = QtCore.QRectF(rect)
    if box.isEmpty():
        return False
    ink = QtGui.QColor(colour)
    scale = min(box.width(), box.height()) / GRID

    painter.save()
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.translate(
            box.center().x() - GRID * scale / 2, box.center().y() - GRID * scale / 2
        )
        painter.scale(scale, scale)
        painter.setPen(_pen(ink, weight))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        known = _shape(painter, kind, ink, weight)
        if not known:
            # A hollow square, so a wrong name is visible but harmless.
            painter.drawRect(QtCore.QRectF(5, 5, 14, 14))
        return known
    finally:
        painter.restore()


def _shape(painter: QtGui.QPainter, kind: str, ink: QtGui.QColor, weight: float) -> bool:
    if kind == "monitor":
        painter.drawRoundedRect(QtCore.QRectF(3, 4.5, 18, 12.5), 2.2, 2.2)
        painter.drawLine(QtCore.QPointF(8.5, 20.5), QtCore.QPointF(15.5, 20.5))
        painter.drawLine(QtCore.QPointF(12, 17), QtCore.QPointF(12, 20.5))
        return True

    if kind == "monitor_rotated":
        painter.drawRoundedRect(QtCore.QRectF(6, 3, 12.5, 18), 2.2, 2.2)
        painter.drawLine(QtCore.QPointF(3.5, 8.5), QtCore.QPointF(3.5, 15.5))
        return True

    if kind == "window":
        painter.drawRoundedRect(QtCore.QRectF(3, 4.5, 18, 15), 2.2, 2.2)
        painter.drawLine(QtCore.QPointF(3, 9), QtCore.QPointF(21, 9))
        painter.setPen(_pen(ink, weight * 0.8))
        for x in (5.8, 8.2, 10.6):
            painter.drawPoint(QtCore.QPointF(x, 6.8))
        return True

    if kind == "region":
        pen = _pen(ink, weight)
        pen.setDashPattern([2.6, 2.2])
        painter.setPen(pen)
        painter.drawRect(QtCore.QRectF(3.5, 5, 17, 14))
        painter.setPen(_pen(ink, weight))
        for point in (
            QtCore.QPointF(3.5, 5), QtCore.QPointF(20.5, 5),
            QtCore.QPointF(3.5, 19), QtCore.QPointF(20.5, 19),
        ):
            painter.drawEllipse(point, 1.5, 1.5)
        return True

    if kind in ("microphone", "microphone_off"):
        painter.drawRoundedRect(QtCore.QRectF(9, 2.5, 6, 11), 3, 3)
        painter.drawArc(QtCore.QRectF(5.5, 8, 13, 10), 180 * 16, 180 * 16)
        painter.drawLine(QtCore.QPointF(12, 18), QtCore.QPointF(12, 21.5))
        if kind.endswith("_off"):
            _strike(painter, ink, weight)
        return True

    if kind in ("speaker", "speaker_off"):
        path = QtGui.QPainterPath()
        path.moveTo(3.5, 9.5)
        path.lineTo(7, 9.5)
        path.lineTo(11.5, 5.5)
        path.lineTo(11.5, 18.5)
        path.lineTo(7, 14.5)
        path.lineTo(3.5, 14.5)
        path.closeSubpath()
        painter.drawPath(path)
        if kind == "speaker":
            painter.drawArc(QtCore.QRectF(11, 7.5, 7, 9), -70 * 16, 140 * 16)
            painter.drawArc(QtCore.QRectF(12.5, 4.5, 10, 15), -65 * 16, 130 * 16)
        else:
            _strike(painter, ink, weight)
        return True

    if kind in ("pointer", "cursor"):
        path = QtGui.QPainterPath()
        path.moveTo(6, 3.5)
        path.lineTo(6, 17.5)
        path.lineTo(9.8, 14.2)
        path.lineTo(12.4, 20.5)
        path.lineTo(15.2, 19.2)
        path.lineTo(12.7, 13.2)
        path.lineTo(17.5, 12.6)
        path.closeSubpath()
        painter.drawPath(path)
        if kind == "pointer":
            painter.setPen(_pen(ink, weight * 0.8))
            painter.drawArc(QtCore.QRectF(12.5, 2, 9, 9), 300 * 16, 120 * 16)
        return True

    if kind == "settings":
        # A ring with eight teeth drawn as a filled outline, not eight spokes: spokes
        # on a circle read as a sun, which is what the first version looked like.
        import math

        path = QtGui.QPainterPath()
        inner, outer, half = 6.1, 9.3, math.radians(10.5)
        for index in range(8):
            centre = math.radians(index * 45)
            points = [
                (inner, centre - math.radians(20)),
                (outer, centre - half),
                (outer, centre + half),
                (inner, centre + math.radians(20)),
            ]
            for position, (radius, angle) in enumerate(points):
                point = QtCore.QPointF(
                    12 + radius * math.cos(angle), 12 + radius * math.sin(angle)
                )
                if index == 0 and position == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
        path.closeSubpath()
        painter.setPen(_pen(ink, weight * 0.85))
        painter.drawPath(path)
        painter.drawEllipse(QtCore.QPointF(12, 12), 2.9, 2.9)
        return True

    if kind in ("chevron_down", "chevron_up", "chevron_right"):
        points = {
            "chevron_down": ((7.5, 10), (12, 14.5), (16.5, 10)),
            "chevron_up": ((7.5, 14.5), (12, 10), (16.5, 14.5)),
            "chevron_right": ((10, 7.5), (14.5, 12), (10, 16.5)),
        }[kind]
        painter.drawPolyline(
            QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in points])
        )
        return True

    if kind == "record":
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawEllipse(QtCore.QPointF(12, 12), 6.5, 6.5)
        return True

    if kind == "stop":
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawRoundedRect(QtCore.QRectF(7, 7, 10, 10), 2, 2)
        return True

    if kind == "pause":
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawRoundedRect(QtCore.QRectF(7.5, 6, 3.4, 12), 1.4, 1.4)
        painter.drawRoundedRect(QtCore.QRectF(13.1, 6, 3.4, 12), 1.4, 1.4)
        return True

    if kind == "trash":
        painter.drawLine(QtCore.QPointF(4, 6.5), QtCore.QPointF(20, 6.5))
        painter.drawPolyline(
            QtGui.QPolygonF([
                QtCore.QPointF(6.5, 6.5), QtCore.QPointF(7.4, 20),
                QtCore.QPointF(16.6, 20), QtCore.QPointF(17.5, 6.5),
            ])
        )
        painter.drawPolyline(
            QtGui.QPolygonF([
                QtCore.QPointF(9.5, 6.5), QtCore.QPointF(9.5, 4),
                QtCore.QPointF(14.5, 4), QtCore.QPointF(14.5, 6.5),
            ])
        )
        return True

    if kind == "refresh":
        painter.drawArc(QtCore.QRectF(4.5, 4.5, 15, 15), 40 * 16, 270 * 16)
        painter.drawPolyline(
            QtGui.QPolygonF([
                QtCore.QPointF(15.5, 3.5), QtCore.QPointF(19.4, 6.6),
                QtCore.QPointF(16.2, 10.2),
            ])
        )
        return True

    if kind == "check":
        painter.drawPolyline(
            QtGui.QPolygonF([
                QtCore.QPointF(5, 12.5), QtCore.QPointF(10, 17.5),
                QtCore.QPointF(19, 6.5),
            ])
        )
        return True

    if kind == "close":
        painter.drawLine(QtCore.QPointF(6.5, 6.5), QtCore.QPointF(17.5, 17.5))
        painter.drawLine(QtCore.QPointF(17.5, 6.5), QtCore.QPointF(6.5, 17.5))
        return True

    if kind == "folder":
        path = QtGui.QPainterPath()
        path.moveTo(3.5, 7)
        path.lineTo(9, 7)
        path.lineTo(11, 9.5)
        path.lineTo(20.5, 9.5)
        path.lineTo(20.5, 18.5)
        path.lineTo(3.5, 18.5)
        path.closeSubpath()
        painter.drawPath(path)
        return True

    if kind == "grip":
        for y in (8.5, 12, 15.5):
            painter.drawLine(QtCore.QPointF(6, y), QtCore.QPointF(18, y))
        return True

    if kind == "help":
        painter.drawEllipse(QtCore.QPointF(12, 12), 8.5, 8.5)
        painter.drawArc(QtCore.QRectF(9, 6.5, 6, 6), 200 * 16, -230 * 16)
        painter.drawLine(QtCore.QPointF(12, 13), QtCore.QPointF(12, 14.5))
        painter.setPen(_pen(ink, weight * 1.25))
        painter.drawPoint(QtCore.QPointF(12, 17.5))
        return True

    if kind == "warning":
        path = QtGui.QPainterPath()
        path.moveTo(12, 3.5)
        path.lineTo(21.5, 19.5)
        path.lineTo(2.5, 19.5)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QtCore.QPointF(12, 9.5), QtCore.QPointF(12, 14))
        painter.setPen(_pen(ink, weight * 1.25))
        painter.drawPoint(QtCore.QPointF(12, 16.8))
        return True

    if kind == "camera":
        painter.drawRoundedRect(QtCore.QRectF(3, 6.5, 18, 12), 2.5, 2.5)
        painter.drawEllipse(QtCore.QPointF(12, 12.5), 3.6, 3.6)
        return True

    return False


def _strike(painter: QtGui.QPainter, ink: QtGui.QColor, weight: float) -> None:
    """The diagonal that turns an icon into its 'off' twin."""
    painter.setPen(_pen(ink, weight * 1.05))
    painter.drawLine(QtCore.QPointF(4.5, 3.5), QtCore.QPointF(19.5, 20.5))


def pixmap(
    kind: str, size: int, colour: QtGui.QColor | str, weight: float = 1.9
) -> QtGui.QPixmap:
    """A standalone pixmap, for the rare place that needs a QIcon after all.

    Painting is wrapped so the painter is always ended: an exception with a live painter
    on a pixmap kills the process when the two are collected, and catching it outside
    does not help.
    """
    image = QtGui.QPixmap(size, size)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    try:
        draw(painter, kind, QtCore.QRectF(0, 0, size, size), colour, weight)
    finally:
        painter.end()
    return image
