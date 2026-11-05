"""The panel the drawing is driven from - and the one thing that must never be recorded.

It is a `surfaces.FloatingSurface`, so `WDA_EXCLUDEFROMCAPTURE` is applied for it on the
first `showEvent` and it is absent from the video while staying on the user's screen.
That was measured against the real capture path (docs/ANNOTATE_CONTRACT.md); nothing here
re-decides it, and the popovers below are `FloatingSurface`s for exactly the same reason.
If `excluded_from_capture` comes back False - Windows 10 before 2004 - the panel *will* be
in the recording, and the controller is the one that has to say so.

**It owns almost no state.** Which tool is current and whether it is collapsed; that is
all. Every per-tool setting lives in `model.Document` (`settings_for` / `set_settings`),
so the colour the pen is using cannot drift from the colour the canvas draws with. When
the toolbar changes one it writes it into the document and emits `settings_changed(tool)`.

**Coordinates.** `place`, `moved` and `avoid` speak **physical desktop pixels**, the same
space as `winapi.list_monitors()` and `model`. Qt's `move()` and `pos()` are in *logical*
units, which differ on a scaled display, so the two conversions at the bottom of this
module are the only place the two spaces meet. Both monitors on this machine run at 100%,
where the conversion collapses to the identity, so **the scaled branch is written from
Qt's documented mapping and is untested here** - the same caveat `overlay._to_desktop`
carries, and the conversion is deliberately the same shape so the two can be fixed
together. Placing a popover under its button never crosses that boundary: a widget
relative to another widget is pure Qt space, and it is done in Qt space.

Monitors can start at a negative x or y. Nothing here assumes an origin at (0, 0); the
clamping and snapping come from `ui.bar`, which is already tested against a two-monitor
desktop, rather than being written a second time.

**Shortcuts are named, not bound.** The tooltips advertise `SHORTCUTS` so the user can
learn them, but a toolbar that never takes focus cannot receive a key press; the canvas
and the controller own the actual bindings. A shortcut named here and not bound there is
a lie, so the two lists have to be kept together.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n, winapi
from ..i18n import _
from ..ui import bar as bar_geometry
from ..ui import icons, surfaces, theme
from . import model

#: Must match `canvas.Canvas.MODE_*`. Stated here rather than imported so the toolbar can
#: be built and tested without the canvas existing.
MODE_PASSTHROUGH = "passthrough"
MODE_DRAW = "draw"

#: How far the panel keeps away from a rectangle it is dodging, in physical pixels.
AVOID_GAP = 14

#: The gap a popover leaves between itself and the control it belongs to (logical units).
POPOVER_GAP = 8
#: How close a popover may come to the edge of its screen.
POPOVER_EDGE = 10

SWATCH = 24
GRIP_WIDTH = 16
SEPARATOR_WIDTH = 9
CHEVRON_ZONE = 11

#: What each control's tooltip says its shortcut is. The controller binds these; see the
#: module docstring.
SHORTCUTS: dict[str, str] = {
    "mode": "Ctrl+Alt+D",
    model.TOOL_PEN: "P",
    model.TOOL_MARKER: "M",
    model.TOOL_ARROW: "A",
    model.TOOL_LINE: "L",
    model.TOOL_RECT: "R",
    model.TOOL_ELLIPSE: "O",
    model.TOOL_TEXT: "T",
    model.TOOL_ERASER: "E",
    "colour": "C",
    "width": "W",
    "undo": "Ctrl+Z",
    "redo": "Ctrl+Y",
    "hide": "H",
    "clear": "Ctrl+Delete",
    "collapse": "Ctrl+Alt+C",
}

#: What each tool is called, **in English**: these are the catalogue's keys, not the
#: words that reach the screen. Translating the table here would freeze whichever
#: language was in force when the module was imported into every tooltip for the rest of
#: the run, so every reader goes through `tool_label` and translates at the moment it
#: asks.
TOOL_LABELS: dict[str, str] = {
    model.TOOL_PEN: "Pen",
    model.TOOL_MARKER: "Marker",
    model.TOOL_LINE: "Line",
    model.TOOL_ARROW: "Arrow",
    model.TOOL_RECT: "Rectangle",
    model.TOOL_ELLIPSE: "Ellipse",
    model.TOOL_TEXT: "Text",
    model.TOOL_ERASER: "Eraser",
}


def tool_label(tool: str) -> str:
    """A tool's name in the language in force now. See `TOOL_LABELS`."""
    return _(TOOL_LABELS.get(tool, tool.title()))


def with_shortcut(text: str, key: str) -> str:
    """`"Thickness (W)"` - an already translated label, then the key that reaches it.

    The key is never translated and never moves: a user reading the Russian interface
    still presses W. Sentences that have to read round the key - the two mode tooltips -
    carry it inside the translated text instead, which is why this takes a label and not
    a sentence.
    """
    shortcut = SHORTCUTS.get(key, "")
    return f"{text} ({shortcut})" if shortcut else text


def tool_tip(tool: str) -> str:
    """What a tool's button says when you hover it: its name and its key."""
    return with_shortcut(tool_label(tool), tool)


def px_text(value: int) -> str:
    """A thickness as the chip and the slider show it."""
    return _("{value} px").format(value=int(value))


def percent_text(value: int) -> str:
    """An opacity as the slider shows it."""
    return _("{value} %").format(value=int(value))


def pt_text(value: int) -> str:
    """A text size as the slider shows it."""
    return _("{value} pt").format(value=int(value))

#: The glyph each tool is drawn with.
TOOL_GLYPHS: dict[str, str] = {
    model.TOOL_PEN: "pen",
    model.TOOL_MARKER: "marker",
    model.TOOL_LINE: "line",
    model.TOOL_ARROW: "arrow",
    model.TOOL_RECT: "rect",
    model.TOOL_ELLIPSE: "ellipse",
    model.TOOL_TEXT: "text",
    model.TOOL_ERASER: "eraser",
}

#: The shapes the chevron on the shapes button offers, in the order it offers them.
SHAPE_TOOLS: tuple[str, ...] = (model.TOOL_RECT, model.TOOL_ELLIPSE, model.TOOL_LINE)

#: The thickness slider's range, in pixels. The user's mock-up draws a continuous slider
#: reading "4 px" rather than the five presets in `model.WIDTHS`, so this is continuous;
#: `model.WIDTHS` stays the canvas's idea of sensible defaults.
WIDTH_MIN, WIDTH_MAX = 1, 32
OPACITY_MIN, OPACITY_MAX = 10, 100
FONT_MIN, FONT_MAX = 10, 72

_reported: set[str] = set()


def _report(message: str) -> None:
    """Say something once. A failure that repeats per mouse move must not flood the log."""
    if message not in _reported:
        _reported.add(message)
        print(f"annotate.toolbar: {message}")


def monitors_now() -> list[winapi.Monitor]:
    """The DXGI outputs, or an empty list with a reason printed."""
    try:
        return winapi.list_monitors()
    except Exception as exc:  # DXGI can fail on a remote session or a driver reload
        _report(f"list_monitors() failed ({exc!r}); geometry falls back to Qt's screens.")
        return []


# --------------------------------------------------------------------------- glyphs
#
# `icons.KINDS` has no pen, marker, line, arrow, rect, ellipse, text, eraser, undo, redo
# or eye, and `ui/icons.py` is not this module's to change, so the annotation glyphs are
# described here on the same 24x24 grid and `_glyph` falls through to `icons.draw` for
# every name that module already knows. One dispatcher, so a caller never has to care
# which half a name comes from.

_OWN_KINDS: frozenset[str] = frozenset(
    {
        "pen", "marker", "line", "arrow", "rect", "ellipse", "shapes", "text",
        "eraser", "undo", "redo", "eye", "eye_off", "dots",
    }
)


def _pen(colour: QtGui.QColor, width: float) -> QtGui.QPen:
    pen = QtGui.QPen(colour, width)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    return pen


def _polyline(painter: QtGui.QPainter, points: tuple[tuple[float, float], ...]) -> None:
    painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in points]))


def _glyph(
    painter: QtGui.QPainter,
    kind: str,
    rect: QtCore.QRectF | QtCore.QRect,
    colour: QtGui.QColor | str,
    weight: float = 1.9,
) -> bool:
    """Stroke `kind` into `rect`. False for a name neither this module nor `icons` knows.

    Never raises: an exception with a live `QPainter` on the stack takes the interpreter
    down, and a mistyped glyph name is not worth that. A wrong name draws nothing here and
    `icons.draw` paints its hollow placeholder square.
    """
    box = QtCore.QRectF(rect)
    if box.isEmpty():
        return False
    if kind not in _OWN_KINDS:
        return icons.draw(painter, kind, box, colour, weight)

    ink = QtGui.QColor(colour)
    scale = min(box.width(), box.height()) / icons.GRID
    painter.save()
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.translate(
            box.center().x() - icons.GRID * scale / 2,
            box.center().y() - icons.GRID * scale / 2,
        )
        painter.scale(scale, scale)
        painter.setPen(_pen(ink, weight))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        _own_shape(painter, kind, ink, weight)
        return True
    finally:
        painter.restore()


def _own_shape(
    painter: QtGui.QPainter, kind: str, ink: QtGui.QColor, weight: float
) -> None:
    if kind == "pen":
        path = QtGui.QPainterPath()
        path.moveTo(3.6, 20.4)
        path.lineTo(5.2, 15.4)
        path.lineTo(15.8, 4.8)
        path.lineTo(19.2, 8.2)
        path.lineTo(8.6, 18.8)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QtCore.QPointF(5.2, 15.4), QtCore.QPointF(8.6, 18.8))
        return

    if kind == "marker":
        # A fat barrel and a chisel nib, not a thin one: at 17 px the only thing that
        # tells a highlighter from a pen is the width of the body and the wet bar of ink
        # under it, so both are drawn deliberately broad.
        barrel = QtGui.QPainterPath()
        barrel.moveTo(9.2, 10.0)
        barrel.lineTo(15.2, 3.2)
        barrel.lineTo(20.0, 7.4)
        barrel.lineTo(14.0, 14.2)
        barrel.closeSubpath()
        painter.drawPath(barrel)
        nib = QtGui.QPainterPath()
        nib.moveTo(9.2, 10.0)
        nib.lineTo(14.0, 14.2)
        nib.lineTo(10.2, 16.6)
        nib.lineTo(5.4, 12.4)
        nib.closeSubpath()
        painter.drawPath(nib)
        painter.setPen(_pen(ink, weight * 1.5))
        painter.drawLine(QtCore.QPointF(4.0, 20.0), QtCore.QPointF(20.0, 20.0))
        return

    if kind == "line":
        painter.drawLine(QtCore.QPointF(4.4, 19.6), QtCore.QPointF(19.6, 4.4))
        return

    if kind == "arrow":
        painter.drawLine(QtCore.QPointF(4.6, 19.4), QtCore.QPointF(18.6, 5.4))
        _polyline(painter, ((11.4, 5.4), (18.6, 5.4), (18.6, 12.6)))
        return

    if kind == "rect":
        painter.drawRoundedRect(QtCore.QRectF(4.0, 6.5, 16.0, 11.0), 2.2, 2.2)
        return

    if kind == "ellipse":
        painter.drawEllipse(QtCore.QPointF(12, 12), 8.0, 6.2)
        return

    if kind == "shapes":
        painter.drawRoundedRect(QtCore.QRectF(3.6, 4.6, 11.0, 11.0), 2.0, 2.0)
        painter.drawEllipse(QtCore.QPointF(15.2, 15.2), 5.0, 5.0)
        return

    if kind == "text":
        painter.drawLine(QtCore.QPointF(5.8, 5.6), QtCore.QPointF(18.2, 5.6))
        painter.drawLine(QtCore.QPointF(12.0, 5.6), QtCore.QPointF(12.0, 18.8))
        painter.drawLine(QtCore.QPointF(8.8, 18.8), QtCore.QPointF(15.2, 18.8))
        return

    if kind == "eraser":
        # Tilted the opposite way to the marker, and rubbing only the right-hand part of
        # the line. Both are a slanted block over a horizontal stroke, so at 17 px the
        # direction of the slant and the length of the stroke are the whole difference.
        painter.save()
        try:
            painter.translate(11.6, 10.6)
            painter.rotate(42)
            painter.drawRoundedRect(QtCore.QRectF(-7.6, -4.2, 15.2, 8.4), 1.8, 1.8)
            painter.drawLine(QtCore.QPointF(1.0, -4.2), QtCore.QPointF(1.0, 4.2))
        finally:
            painter.restore()
        painter.drawLine(QtCore.QPointF(10.0, 20.6), QtCore.QPointF(20.8, 20.6))
        return

    if kind in ("undo", "redo"):
        painter.save()
        try:
            if kind == "redo":  # the same arrow, mirrored; the strokes are symmetric
                painter.translate(icons.GRID, 0)
                painter.scale(-1, 1)
            painter.drawArc(QtCore.QRectF(5, 7, 14, 12), 8 * 16, 198 * 16)
            _polyline(painter, ((2.8, 13.2), (6.0, 16.4), (9.6, 13.8)))
        finally:
            painter.restore()
        return

    if kind in ("eye", "eye_off"):
        path = QtGui.QPainterPath()
        # The control points are well outside the shape: a flatter lens leaves no room
        # between the lid and the pupil and the whole thing fills in at 17 px.
        path.moveTo(2.6, 12.0)
        path.quadTo(12.0, 2.6, 21.4, 12.0)
        path.quadTo(12.0, 21.4, 2.6, 12.0)
        painter.drawPath(path)
        painter.drawEllipse(QtCore.QPointF(12, 12), 2.4, 2.4)
        if kind == "eye_off":
            painter.setPen(_pen(ink, weight * 1.05))
            painter.drawLine(QtCore.QPointF(4.5, 3.6), QtCore.QPointF(19.5, 20.4))
        return

    if kind == "dots":  # the six-dot drag grip from the user's mock-up
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for x in (9.2, 14.8):
            for y in (6.8, 12.0, 17.2):
                painter.drawEllipse(QtCore.QPointF(x, y), 1.5, 1.5)
        return


# --------------------------------------------------------------------------- controls


class ToolButton(surfaces.IconButton):
    """An `IconButton` that can also draw the annotation glyphs.

    Hover, pressed and the "on" state are the parent's; only the glyph routing and the
    violet selected fill from the user's mock-up are added. The selected fill is the
    accent at low alpha plus a hairline accent border rather than a solid violet block,
    which would out-shout the record button in the bar above it.
    """

    def __init__(
        self,
        kind: str,
        tooltip: str = "",
        size: int = surfaces.BUTTON,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(kind, tooltip=tooltip, role="quiet", size=size, parent=parent)

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        hovered = self.underMouse() and self.isEnabled()
        pressed = self.isDown()
        radius = theme.RADIUS["small"]

        if self.is_on():
            fill = QtGui.QColor(colours["accent"])
            fill.setAlpha(64 if not hovered else 86)
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["accent"]), 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(box, radius, radius)
            ink = colours["accent_light"]
        else:
            background = colours["line"] if pressed else (
                colours["hover"] if hovered else None
            )
            if background is not None:
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(QtGui.QColor(background))
                painter.drawRoundedRect(box, radius, radius)
            if not self.isEnabled():
                ink = colours["disabled"]
            elif hovered:
                ink = colours["text"]
            else:
                ink = colours["text_dim"]

        inset = (self.width() - surfaces.ICON) / 2
        _glyph(
            painter,
            self.kind,
            QtCore.QRectF(inset, inset, surfaces.ICON, surfaces.ICON),
            ink,
        )


class SplitToolButton(ToolButton):
    """A tool button with a chevron strip on its right edge that opens a popover.

    One widget rather than two, because two buttons cannot share a selected fill without
    a seam down the middle. The same split the recording bar uses for its device menus.
    """

    menu_requested = QtCore.Signal()

    def __init__(
        self,
        kind: str,
        tooltip: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(kind, tooltip=tooltip, size=surfaces.BUTTON, parent=parent)
        self.setFixedWidth(surfaces.BUTTON + CHEVRON_ZONE)
        self._in_chevron = False

    def chevron_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self.width() - CHEVRON_ZONE, 0, CHEVRON_ZONE, self.height())

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        hovered = self.underMouse() and self.isEnabled()
        pressed = self.isDown()
        radius = theme.RADIUS["small"]

        if self.is_on():
            fill = QtGui.QColor(colours["accent"])
            fill.setAlpha(64 if not hovered else 86)
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["accent"]), 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(box, radius, radius)
            ink = colours["accent_light"]
        else:
            background = colours["line"] if pressed else (
                colours["hover"] if hovered else None
            )
            if background is not None:
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(QtGui.QColor(background))
                painter.drawRoundedRect(box, radius, radius)
            ink = colours["text"] if hovered else colours["text_dim"]
        if not self.isEnabled():
            ink = colours["disabled"]

        inset = (surfaces.BUTTON - surfaces.ICON) / 2
        _glyph(
            painter,
            self.kind,
            QtCore.QRectF(
                inset, (self.height() - surfaces.ICON) / 2,
                surfaces.ICON, surfaces.ICON,
            ),
            ink,
        )
        chevron = self.chevron_rect()
        icons.draw(
            painter,
            "chevron_down",
            QtCore.QRectF(chevron.x() - 1, (self.height() - 13) / 2, 13, 13),
            colours["text_dim"],
            weight=1.7,
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self._in_chevron = self.chevron_rect().contains(event.position())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        chevron = self._in_chevron and self.chevron_rect().contains(event.position())
        if chevron and event.button() == QtCore.Qt.MouseButton.LeftButton:
            # Swallow the click so `clicked` does not fire as well: the chevron opens the
            # list, it does not also pick the tool.
            self.setDown(False)
            self._in_chevron = False
            self.update()
            event.accept()
            self.menu_requested.emit()
            return
        self._in_chevron = False
        super().mouseReleaseEvent(event)


class ColourButton(QtWidgets.QAbstractButton):
    """The current colour as a filled chip, with a chevron saying it opens something."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = model.PALETTE[2]
        self._open = False
        self.setFixedSize(surfaces.BUTTON + CHEVRON_ZONE, surfaces.BUTTON)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def colour(self) -> str:
        return self._colour

    def set_colour(self, colour: str) -> None:
        if colour != self._colour:
            self._colour = colour
            self.update()

    def set_open(self, value: bool) -> None:
        if value != self._open:
            self._open = value
            self.update()

    def sizeHint(self) -> QtCore.QSize:
        return self.size()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        hovered = self.underMouse() and self.isEnabled()
        if hovered or self._open:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(colours["hover"]))
            painter.drawRoundedRect(
                QtCore.QRectF(0, 0, self.width(), self.height()),
                theme.RADIUS["small"], theme.RADIUS["small"],
            )
        size = 18.0
        chip = QtCore.QRectF(
            (surfaces.BUTTON - size) / 2, (self.height() - size) / 2, size, size
        )
        ink = colours["disabled"] if not self.isEnabled() else self._colour
        painter.setPen(QtGui.QPen(QtGui.QColor(colours["line_strong"]), 1))
        painter.setBrush(QtGui.QColor(ink))
        painter.drawRoundedRect(chip, 5, 5)
        icons.draw(
            painter,
            "chevron_up" if self._open else "chevron_down",
            QtCore.QRectF(
                self.width() - CHEVRON_ZONE - 1, (self.height() - 13) / 2, 13, 13
            ),
            colours["text_dim"],
            weight=1.7,
        )

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class ValueChip(QtWidgets.QAbstractButton):
    """A small pill reading a current value - "4 px" - with a chevron."""

    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._open = False
        self.setFixedHeight(surfaces.BUTTON - 6)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self._refit()

    def text_value(self) -> str:
        return self._text

    def set_text_value(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._refit()
            self.update()

    def set_open(self, value: bool) -> None:
        if value != self._open:
            self._open = value
            self.update()

    def _font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(9.0)
        return font

    def _refit(self) -> None:
        metrics = QtGui.QFontMetrics(self._font())
        self.setFixedWidth(10 + metrics.horizontalAdvance(self._text) + 4 + 13 + 6)

    def sizeHint(self) -> QtCore.QSize:
        return self.size()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        hovered = self.underMouse() and self.isEnabled()
        box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(
            QtGui.QPen(QtGui.QColor(colours["line_strong" if hovered else "line"]), 1)
        )
        painter.setBrush(
            QtGui.QColor(colours["hover"] if (hovered or self._open) else colours["bg"])
        )
        painter.drawRoundedRect(box, theme.RADIUS["chip"], theme.RADIUS["chip"])

        ink = colours["text"] if (hovered or self._open) else colours["text_soft"]
        if not self.isEnabled():
            ink = colours["disabled"]
        painter.setFont(self._font())
        painter.setPen(QtGui.QColor(ink))
        painter.drawText(
            QtCore.QRectF(10, 0, self.width() - 10 - 17, self.height()),
            int(
                QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft
            ),
            self._text,
        )
        icons.draw(
            painter,
            "chevron_up" if self._open else "chevron_down",
            QtCore.QRectF(self.width() - 17, (self.height() - 13) / 2, 13, 13),
            colours["text_dim"],
            weight=1.7,
        )

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class Swatch(QtWidgets.QAbstractButton):
    """One colour in the popover's row of eight."""

    def __init__(self, colour: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.colour = colour
        self._selected = False
        self.setFixedSize(SWATCH, SWATCH)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.setToolTip(colour.upper())

    def set_selected(self, value: bool) -> None:
        if value != self._selected:
            self._selected = value
            self.update()

    def is_selected(self) -> bool:
        return self._selected

    def sizeHint(self) -> QtCore.QSize:
        return self.size()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            colours = theme.COLOURS
            inset = 3.0 if self._selected else 1.5
            chip = QtCore.QRectF(
                inset, inset, self.width() - inset * 2, self.height() - inset * 2
            )
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["line_strong"]), 1))
            painter.setBrush(QtGui.QColor(self.colour))
            painter.drawRoundedRect(chip, 6, 6)
            if self._selected:
                painter.setPen(QtGui.QPen(QtGui.QColor(colours["accent"]), 1.6))
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(
                    QtCore.QRectF(0.8, 0.8, self.width() - 1.6, self.height() - 1.6), 8, 8
                )
            elif self.underMouse():
                painter.setPen(QtGui.QPen(QtGui.QColor(colours["text_dim"]), 1.2))
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(
                    QtCore.QRectF(0.8, 0.8, self.width() - 1.6, self.height() - 1.6), 8, 8
                )
        finally:
            painter.end()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class StrokePreview(QtWidgets.QWidget):
    """A sunken well with a live sample of what the current settings will draw."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = model.PALETTE[2]
        self._width = 4
        self._opacity = 1.0
        self.setFixedHeight(52)
        self.setMinimumWidth(120)

    def set_stroke(self, colour: str, width: int, opacity: float) -> None:
        self._colour, self._width, self._opacity = colour, width, opacity
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            colours = theme.COLOURS
            box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["line"]), 1))
            painter.setBrush(QtGui.QColor(colours["bg"]))
            painter.drawRoundedRect(box, theme.RADIUS["small"], theme.RADIUS["small"])

            ink = QtGui.QColor(self._colour)
            ink.setAlphaF(max(0.05, min(1.0, self._opacity)))
            # Never let a 32 px stroke overflow a 52 px well; it is a sample, not a ruler.
            drawn = max(1.0, min(float(self._width), self.height() - 18.0))
            pen = _pen(ink, drawn)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            middle = self.height() / 2
            reach = self.height() / 4
            path = QtGui.QPainterPath()
            path.moveTo(14, middle + reach)
            path.cubicTo(
                self.width() * 0.33, middle - reach * 2.0,
                self.width() * 0.62, middle + reach * 2.0,
                self.width() - 14, middle - reach,
            )
            painter.drawPath(path)
        finally:
            painter.end()


class Separator(QtWidgets.QWidget):
    """The hairline that groups the bar into mode / tools / style / history."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(SEPARATOR_WIDTH)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Expanding
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setPen(QtGui.QPen(QtGui.QColor(theme.COLOURS["line"]), 1))
            x = self.width() / 2
            painter.drawLine(
                QtCore.QPointF(x, 7.0), QtCore.QPointF(x, max(8.0, self.height() - 7.0))
            )
        finally:
            painter.end()


class DragGrip(QtWidgets.QWidget):
    """The six dots. Pressing them drags the panel; it owns no behaviour of its own."""

    def __init__(self, toolbar: Toolbar, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._toolbar = toolbar
        self.setFixedSize(GRIP_WIDTH, surfaces.BUTTON)
        self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
        self.retranslate()
        i18n.notifier.changed.connect(self.retranslate)

    def retranslate(self) -> None:
        """Re-read the one string this widget owns."""
        self.setToolTip(_("Drag to move the toolbar"))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            colour = theme.COLOURS["text" if self.underMouse() else "text_faint"]
            _glyph(
                painter, "dots",
                QtCore.QRectF(
                    (self.width() - surfaces.ICON) / 2,
                    (self.height() - surfaces.ICON) / 2,
                    surfaces.ICON, surfaces.ICON,
                ),
                colour,
            )
        finally:
            painter.end()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._toolbar.begin_drag(event.globalPosition().toPoint())
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._toolbar.drag_to(event.globalPosition().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._toolbar.end_drag()
        event.accept()


# -------------------------------------------------------------------------- popovers


class Popover(surfaces.FloatingSurface):
    """A small surface under the control it belongs to.

    It is a `FloatingSurface`, so it is kept out of the capture for free - do not undo
    that by reparenting it into the canvas.

    **Closing.** The surface never takes focus (that is the whole point of
    `WA_ShowWithoutActivating` plus `WS_EX_NOACTIVATE`), so there is no `focusOutEvent`
    and no key press ever arrives here. Both Esc and "clicked somewhere else" therefore
    come from an event filter installed on the application while the popover is open, and
    it is removed again on close. The honest limit of that: a click into *another*
    application produces no Qt event for us, so the popover stays open until the pointer
    comes back. In draw mode the canvas covers the desktop and every click is ours, which
    is the mode this is used in.
    """

    closed = QtCore.Signal()

    def __init__(self, title: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(
            radius=theme.RADIUS["surface"], fill="panel_raised", border="line_strong",
            parent=parent,
        )
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._anchor: QtWidgets.QWidget | None = None
        self._filtering = False

        self._root = QtWidgets.QVBoxLayout(self)
        self._root.setContentsMargins(13, 11, 13, 13)
        self._root.setSpacing(9)
        self._title = surfaces.label(title, role="text", size=10.0)
        font = QtGui.QFont(self._title.font())
        font.setBold(True)
        self._title.setFont(font)
        self._root.addWidget(self._title)
        i18n.notifier.changed.connect(self.retranslate)

    def retranslate(self) -> None:
        """Re-read every string in this popover.

        Empty here because the bare surface keeps no source text - the title it was
        handed was already translated by whoever built it. Each subclass knows which
        strings it owns and overrides this; the connection is made once, in this
        constructor, so an override is reached through it.
        """

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def anchor(self) -> QtWidgets.QWidget | None:
        return self._anchor

    # -- showing and hiding ------------------------------------------------

    def open_under(self, anchor: QtWidgets.QWidget) -> None:
        """Show this popover under `anchor`, flipping above it when there is no room."""
        self._anchor = anchor
        self.adjustSize()
        self.move(self._position_under(anchor))
        self.show()
        self.raise_()
        self._start_filtering()

    def close_popover(self) -> None:
        if not self.isVisible() and not self._filtering:
            return
        self._stop_filtering()
        self.hide()
        self._anchor = None
        self.closed.emit()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        # Hidden by anything at all - including the toolbar going away - must still take
        # the application-wide filter back off, or it outlives the window it belongs to.
        self._stop_filtering()
        super().hideEvent(event)

    def _start_filtering(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            _report("no QApplication; the popover cannot be closed by Esc or a click.")
            return
        if not self._filtering:
            app.installEventFilter(self)
            self._filtering = True

    def _stop_filtering(self) -> None:
        if not self._filtering:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._filtering = False

    def _owns(self, obj: QtCore.QObject) -> bool:
        """True for this popover - window or widget - and for the control that opened it.

        **A real press is seen twice.** The platform delivers it to the ``QWindow``
        first, and Qt only then turns it into a press on the child widget, so an
        application-wide filter runs once with the popover's ``QWidgetWindow`` and once
        with the widget inside it. The window pass carries no ``QWidget`` at all, and a
        test that only recognises a ``QWidget`` therefore skips the ownership question
        entirely and dismisses the popover on its *own* click: reaching for the thickness
        slider made the whole surface vanish. `QTest.mouseClick(widget, …)` posts straight
        to the widget and never goes through the window, which is why 475 offscreen tests
        could not see it.

        The same bug was found and fixed in `ui.sources.SourcePicker._owns`; this is
        deliberately the same shape, so a correction to one can be applied to the other.

        The anchor counts too, or the click that closes the popover would be seen by the
        anchor as well and reopen it at once - one click, two state changes, a flicker.
        Only the anchor itself and its children, though, and not the whole toolbar:
        pressing the eraser while the colour popover is open should put the popover away.
        """
        if isinstance(obj, QtGui.QWindow):
            handle = self.windowHandle()
            if handle is not None and obj is handle:
                return True
            anchor = self._anchor
            if anchor is None:
                return False
            try:
                anchor_window = anchor.window()
            except RuntimeError:
                # The anchor was deleted out from under us; it cannot own anything now.
                self._anchor = None
                return False
            anchor_handle = (
                anchor_window.windowHandle() if anchor_window is not None else None
            )
            # The anchor's whole window, because this pass cannot say which widget in it
            # was pressed. The widget pass that follows makes that distinction, so
            # pressing another control in the toolbar still dismisses the popover.
            return anchor_handle is not None and obj is anchor_handle
        if not isinstance(obj, QtWidgets.QWidget):
            return False
        if obj is self or self.isAncestorOf(obj) or obj.window() is self:
            return True
        anchor = self._anchor
        if anchor is None:
            return False
        try:
            return obj is anchor or anchor.isAncestorOf(obj)
        except RuntimeError:
            # The anchor was deleted out from under us; it cannot own anything now.
            self._anchor = None
            return False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        kind = event.type()
        if kind == QtCore.QEvent.Type.KeyPress:
            escape = QtCore.Qt.Key.Key_Escape
            if isinstance(event, QtGui.QKeyEvent) and event.key() == escape:
                self.close_popover()
                return True
            return False
        if kind in (
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QEvent.Type.NonClientAreaMouseButtonPress,
        ):
            if self.isVisible() and not self._owns(watched):
                # Dismiss, but let the press through: the user was pressing something.
                self.close_popover()
        return False

    # -- placement ---------------------------------------------------------

    def _position_under(self, anchor: QtWidgets.QWidget) -> QtCore.QPoint:
        """Qt-space placement: a widget relative to a widget never leaves logical units."""
        top_left = anchor.mapToGlobal(QtCore.QPoint(0, 0))
        width, height = self.width(), self.height()
        screen = QtGui.QGuiApplication.screenAt(
            top_left + QtCore.QPoint(anchor.width() // 2, anchor.height() // 2)
        )
        if screen is None:
            screen = anchor.screen() or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            _report("no QScreen; the popover is placed straight under its button.")
            return QtCore.QPoint(top_left.x(), top_left.y() + anchor.height() + POPOVER_GAP)
        area = screen.availableGeometry()

        y = top_left.y() + anchor.height() + POPOVER_GAP
        if y + height > area.bottom() - POPOVER_EDGE:
            above = top_left.y() - POPOVER_GAP - height
            y = above if above >= area.top() + POPOVER_EDGE else max(
                area.top() + POPOVER_EDGE, area.bottom() - POPOVER_EDGE - height
            )
        # Line the popover's left edge up with the control, then pull it back on screen.
        x = top_left.x()
        x = min(x, area.right() - POPOVER_EDGE - width)
        x = max(x, area.left() + POPOVER_EDGE)
        return QtCore.QPoint(int(x), int(y))


class SettingsPopover(Popover):
    """Colour, thickness, and whatever else the current tool has: opacity, text size.

    It edits `document.settings_for(tool)` in place and puts it back with
    `document.set_settings`, then says which tool changed. It keeps no copy.
    """

    changed = QtCore.Signal(str)

    def __init__(
        self, document: model.Document, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(tool_label(model.TOOL_PEN), parent)
        self.document = document
        self._tool = model.TOOL_PEN
        self._loading = False
        #: Each slider's heading with the English source it was built from, so a
        #: language change can re-read it without rebuilding the popover.
        self._row_titles: list[tuple[QtWidgets.QLabel, str]] = []
        self.setFixedWidth(282)

        self._swatch_row = QtWidgets.QWidget(self)
        row = QtWidgets.QHBoxLayout(self._swatch_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.swatches: list[Swatch] = []
        for colour in model.PALETTE:
            swatch = Swatch(colour, self._swatch_row)
            swatch.clicked.connect(lambda _checked=False, c=colour: self._pick_colour(c))
            row.addWidget(swatch)
            self.swatches.append(swatch)
        row.addStretch(1)
        self._root.addWidget(self._swatch_row)

        self.width_row, self.width_slider, self.width_value = self._slider_row(
            "Thickness", WIDTH_MIN, WIDTH_MAX
        )
        self.width_slider.valueChanged.connect(self._width_moved)

        self.opacity_row, self.opacity_slider, self.opacity_value = self._slider_row(
            "Opacity", OPACITY_MIN, OPACITY_MAX
        )
        self.opacity_slider.valueChanged.connect(self._opacity_moved)

        self.font_row, self.font_slider, self.font_value = self._slider_row(
            "Text size", FONT_MIN, FONT_MAX
        )
        self.font_slider.valueChanged.connect(self._font_moved)

        self.preview = StrokePreview(self)
        self._root.addWidget(self.preview)

    def _slider_row(
        self, title: str, low: int, high: int
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QSlider, QtWidgets.QLabel]:
        """One heading, one value and one slider. `title` is the English source."""
        holder = QtWidgets.QWidget(self)
        column = QtWidgets.QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        head = QtWidgets.QWidget(holder)
        line = QtWidgets.QHBoxLayout(head)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(6)
        heading = surfaces.label(_(title), role="text_dim", size=9.0)
        self._row_titles.append((heading, title))
        line.addWidget(heading)
        line.addStretch(1)
        value = surfaces.label("", role="text", size=9.0)
        value.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        line.addWidget(value)
        column.addWidget(head)

        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, holder)
        slider.setRange(low, high)
        slider.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        column.addWidget(slider)

        self._root.addWidget(holder)
        return holder, slider, value

    # -- loading and writing ----------------------------------------------

    def open_for(self, tool: str, anchor: QtWidgets.QWidget) -> None:
        self.load(tool)
        self.open_under(anchor)

    def load(self, tool: str) -> None:
        """Show `tool`'s own settings. No signal: this is reading, not editing."""
        self._tool = tool
        settings = self.document.settings_for(tool)
        self._loading = True
        try:
            self.set_title(tool_label(tool))
            has_colour = tool != model.TOOL_ERASER
            self._swatch_row.setVisible(has_colour)
            for swatch in self.swatches:
                swatch.set_selected(
                    has_colour and swatch.colour.lower() == settings.colour.lower()
                )
            self.width_slider.setValue(max(WIDTH_MIN, min(WIDTH_MAX, int(settings.width))))
            self.width_value.setText(px_text(self.width_slider.value()))

            self.opacity_row.setVisible(tool == model.TOOL_MARKER)
            percent = int(round(max(0.0, min(1.0, settings.opacity)) * 100))
            self.opacity_slider.setValue(max(OPACITY_MIN, min(OPACITY_MAX, percent)))
            self.opacity_value.setText(percent_text(self.opacity_slider.value()))

            self.font_row.setVisible(tool == model.TOOL_TEXT)
            self.font_slider.setValue(max(FONT_MIN, min(FONT_MAX, int(settings.font_size))))
            self.font_value.setText(pt_text(self.font_slider.value()))

            self.preview.setVisible(tool != model.TOOL_ERASER)
            self._refresh_preview()
        finally:
            self._loading = False
        self.adjustSize()

    def tool(self) -> str:
        return self._tool

    def retranslate(self) -> None:
        """Headings, title and the three value readouts, in the new language.

        `load` is what writes the title and the readouts, so it is called again rather
        than copied; it reads the document and changes nothing, and `_loading` keeps it
        from emitting an edit nobody made.
        """
        for heading, source in self._row_titles:
            heading.setText(_(source))
        self.load(self._tool)

    def _refresh_preview(self) -> None:
        settings = self.document.settings_for(self._tool)
        self.preview.set_stroke(settings.colour, settings.width, settings.opacity)

    def _write(self, **fields: object) -> None:
        if self._loading:
            return
        settings = self.document.settings_for(self._tool).copy()
        for name, value in fields.items():
            setattr(settings, name, value)
        self.document.set_settings(self._tool, settings)
        self._refresh_preview()
        self.changed.emit(self._tool)

    def _pick_colour(self, colour: str) -> None:
        for swatch in self.swatches:
            swatch.set_selected(swatch.colour == colour)
        self._write(colour=colour)

    def _width_moved(self, value: int) -> None:
        self.width_value.setText(px_text(value))
        self._write(width=int(value))

    def _opacity_moved(self, value: int) -> None:
        self.opacity_value.setText(percent_text(value))
        self._write(opacity=value / 100.0)

    def _font_moved(self, value: int) -> None:
        self.font_value.setText(pt_text(value))
        self._write(font_size=int(value))


class ShapesPopover(Popover):
    """Which shape the shapes button draws: rectangle, ellipse or line."""

    chosen = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(_("Shape"), parent)
        self._root.setSpacing(6)
        self.buttons: dict[str, ToolButton] = {}
        row = QtWidgets.QWidget(self)
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(6)
        for tool in SHAPE_TOOLS:
            button = ToolButton(TOOL_GLYPHS[tool], tool_tip(tool), parent=row)
            button.clicked.connect(lambda _checked=False, t=tool: self._pick(t))
            line.addWidget(button)
            self.buttons[tool] = button
        line.addStretch(1)
        self._root.addWidget(row)

    def retranslate(self) -> None:
        """The heading and the three shape names."""
        self.set_title(_("Shape"))
        for tool, button in self.buttons.items():
            button.setToolTip(tool_tip(tool))

    def set_current(self, tool: str) -> None:
        for key, button in self.buttons.items():
            button.set_on(key == tool)

    def _pick(self, tool: str) -> None:
        self.set_current(tool)
        self.close_popover()
        self.chosen.emit(tool)


# --------------------------------------------------------------------------- toolbar


class Toolbar(surfaces.FloatingSurface):
    """The annotation panel: one row of controls, or a pill when it is collapsed.

    Signals are emitted for *interactions*. The `set_*` methods are the caller telling the
    toolbar what is already true and are deliberately silent, so a controller that reflects
    a signal back cannot start a loop. `set_collapsed` is the one with a visible pair:
    clicking the chevron goes through `toggle_collapsed`, which emits.
    """

    tool_changed = QtCore.Signal(str)
    mode_toggled = QtCore.Signal()
    undo_requested = QtCore.Signal()
    redo_requested = QtCore.Signal()
    clear_requested = QtCore.Signal()
    visibility_toggled = QtCore.Signal()
    settings_changed = QtCore.Signal(str)
    collapsed_changed = QtCore.Signal(bool)
    moved = QtCore.Signal(int, int)

    def __init__(
        self, document: model.Document, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(
            radius=theme.RADIUS["bar"], fill="panel", border="line_strong", parent=parent
        )
        self.setObjectName("AnnotateToolbar")
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

        self.document = document
        self._tool = model.TOOL_PEN
        self._shape_tool = model.TOOL_RECT
        self._mode = MODE_PASSTHROUGH
        self._collapsed = False
        self._hidden_drawings = False

        self._monitors: list[winapi.Monitor] = []
        self._drag_offset: tuple[int, int] | None = None
        self._dragged = False
        #: Where the user last put it, in physical pixels. `avoid` always measures from
        #: here and comes back to it, so dodging never steals the position they chose.
        self._home: tuple[int, int] | None = None
        self._avoiding = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._full = self._build_full()
        self._pill = self._build_pill()
        root.addWidget(self._full)
        root.addWidget(self._pill)
        self._pill.hide()

        self.settings_popover = SettingsPopover(document, self)
        self.settings_popover.changed.connect(self._settings_edited)
        self.settings_popover.closed.connect(self._popover_closed)
        self.shapes_popover = ShapesPopover(self)
        self.shapes_popover.chosen.connect(self._shape_chosen)
        self.shapes_popover.closed.connect(self._popover_closed)

        self._apply_tooltips()
        self._sync()
        self._fit()
        i18n.notifier.changed.connect(self.retranslate)

    # ------------------------------------------------------------ construction

    def _tool_button(
        self, tool: str, parent: QtWidgets.QWidget
    ) -> ToolButton:
        button = ToolButton(TOOL_GLYPHS[tool], tool_tip(tool), parent=parent)
        button.clicked.connect(lambda _checked=False, t=tool: self._choose_tool(t))
        return button

    def _build_full(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self)
        line = QtWidgets.QHBoxLayout(container)
        line.setContentsMargins(8, 8, 8, 8)
        line.setSpacing(3)

        self.grip = DragGrip(self, container)
        line.addWidget(self.grip)

        self.mode_button = ToolButton("cursor", "", parent=container)
        self.mode_button.clicked.connect(self._mode_clicked)
        line.addWidget(self.mode_button)
        line.addWidget(Separator(container))

        self.tool_buttons: dict[str, ToolButton] = {}
        for tool in (model.TOOL_PEN, model.TOOL_MARKER, model.TOOL_ARROW):
            button = self._tool_button(tool, container)
            self.tool_buttons[tool] = button
            line.addWidget(button)

        self.shapes_button = SplitToolButton(
            TOOL_GLYPHS[model.TOOL_RECT], tool_tip(model.TOOL_RECT), parent=container
        )
        self.shapes_button.clicked.connect(self._shapes_clicked)
        self.shapes_button.menu_requested.connect(self._open_shapes_popover)
        line.addWidget(self.shapes_button)

        for tool in (model.TOOL_TEXT, model.TOOL_ERASER):
            button = self._tool_button(tool, container)
            self.tool_buttons[tool] = button
            line.addWidget(button)

        line.addWidget(Separator(container))

        self.colour_button = ColourButton(container)
        self.colour_button.clicked.connect(
            lambda: self._toggle_settings_popover(self.colour_button)
        )
        line.addWidget(self.colour_button)

        self.width_chip = ValueChip(px_text(4), container)
        self.width_chip.clicked.connect(
            lambda: self._toggle_settings_popover(self.width_chip)
        )
        line.addWidget(self.width_chip)

        line.addWidget(Separator(container))

        self.undo_button = ToolButton("undo", parent=container)
        self.undo_button.clicked.connect(self.undo_requested.emit)
        line.addWidget(self.undo_button)

        self.redo_button = ToolButton("redo", parent=container)
        self.redo_button.clicked.connect(self.redo_requested.emit)
        line.addWidget(self.redo_button)

        self.eye_button = ToolButton("eye", parent=container)
        self.eye_button.clicked.connect(self.visibility_toggled.emit)
        line.addWidget(self.eye_button)

        self.clear_button = ToolButton("trash", parent=container)
        self.clear_button.clicked.connect(self.clear_requested.emit)
        line.addWidget(self.clear_button)

        self.collapse_button = ToolButton(
            "chevron_right", size=surfaces.BUTTON - 6, parent=container
        )
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        line.addWidget(self.collapse_button)
        return container

    def _build_pill(self) -> QtWidgets.QWidget:
        """Collapsed: grip, current tool, colour, the way out of draw mode, and expand.

        The mode button is here on purpose and must stay: collapsing the panel must never
        leave the user in a mode where their clicks do not reach their own application.

        The **expand chevron** is here for the same class of reason. The pill used to be
        expanded by clicking its tool button, which is not an affordance but a guess: the
        user collapsed the panel once and could not get it back, and the click harness
        could not find the control either. So expanding is now a button of its own that
        says what it does, mirroring the collapse chevron on the full bar, and the tool
        button is left meaning the tool - which is the only thing its pencil ever claimed.
        """
        container = QtWidgets.QWidget(self)
        line = QtWidgets.QHBoxLayout(container)
        line.setContentsMargins(8, 8, 8, 8)
        line.setSpacing(3)

        self.pill_grip = DragGrip(self, container)
        line.addWidget(self.pill_grip)

        self.pill_tool_button = ToolButton(
            TOOL_GLYPHS[model.TOOL_PEN], tool_tip(model.TOOL_PEN), parent=container
        )
        self.pill_tool_button.clicked.connect(self._pill_tool_clicked)
        line.addWidget(self.pill_tool_button)

        self.pill_colour_button = ColourButton(container)
        self.pill_colour_button.clicked.connect(
            lambda: self._toggle_settings_popover(self.pill_colour_button)
        )
        line.addWidget(self.pill_colour_button)

        self.pill_mode_button = ToolButton("cursor", "", parent=container)
        self.pill_mode_button.clicked.connect(self._mode_clicked)
        line.addWidget(self.pill_mode_button)

        #: The way back. Same glyph, same size and the same far-right place as
        #: `collapse_button`, so the pair reads as one control in two states.
        self.pill_expand_button = ToolButton(
            "chevron_right", size=surfaces.BUTTON - 6, parent=container
        )
        self.pill_expand_button.clicked.connect(self.toggle_collapsed)
        line.addWidget(self.pill_expand_button)
        return container

    def _apply_tooltips(self) -> None:
        """Every tooltip whose words do not depend on the state; `_sync` does the rest.

        Kept out of the two builders so that a language change can run it again over
        controls that are already on the screen.
        """
        for tool, button in self.tool_buttons.items():
            button.setToolTip(tool_tip(tool))
        for colour_button in (self.colour_button, self.pill_colour_button):
            colour_button.setToolTip(with_shortcut(_("Colour"), "colour"))
        self.width_chip.setToolTip(with_shortcut(_("Thickness"), "width"))
        self.undo_button.setToolTip(with_shortcut(_("Undo"), "undo"))
        self.redo_button.setToolTip(with_shortcut(_("Redo"), "redo"))
        self.clear_button.setToolTip(with_shortcut(_("Erase everything"), "clear"))
        self.collapse_button.setToolTip(
            with_shortcut(_("Collapse the toolbar"), "collapse")
        )
        self.pill_expand_button.setToolTip(
            with_shortcut(_("Expand the panel"), "collapse")
        )

    def retranslate(self) -> None:
        """Re-read every string on both forms of the panel.

        The popovers are left alone on purpose: each is connected to the same signal and
        re-reads itself, so doing it here as well would only do it twice. `_fit` is the
        last step because a translated thickness chip is not the width the old one was.
        """
        self._apply_tooltips()
        self._sync()
        self._fit()

    def _fit(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        hint = self.sizeHint()
        if hint.isValid() and hint != self.size():
            self.resize(hint)

    # ------------------------------------------------------------------ state

    def tool(self) -> str:
        return self._tool

    def mode(self) -> str:
        return self._mode

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_tool(self, tool: str) -> None:
        """Show `tool` as current. Silent - see the class docstring."""
        if tool not in model.ALL_TOOLS:
            _report(f"set_tool({tool!r}) is not a tool; ignored.")
            return
        self._tool = tool
        if tool in SHAPE_TOOLS:
            self._shape_tool = tool
        self._sync()
        if self.settings_popover.isVisible():
            self.settings_popover.load(tool)

    def set_mode(self, mode: str) -> None:
        if mode not in (MODE_PASSTHROUGH, MODE_DRAW):
            _report(
                f"set_mode({mode!r}) is neither {MODE_PASSTHROUGH!r} "
                f"nor {MODE_DRAW!r}."
            )
            return
        self._mode = mode
        self._sync()

    def set_history(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_button.setEnabled(bool(can_undo))
        self.redo_button.setEnabled(bool(can_redo))

    def set_hidden_drawings(self, hidden: bool) -> None:
        self._hidden_drawings = bool(hidden)
        self._sync()

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._close_popovers()
        self._full.setVisible(not collapsed)
        self._pill.setVisible(collapsed)
        self._sync()
        self._fit()

    def toggle_collapsed(self) -> None:
        """The chevron, and the pill's tool button. This one does emit."""
        self.set_collapsed(not self._collapsed)
        self.collapsed_changed.emit(self._collapsed)

    def _sync(self) -> None:
        """Push the current state onto every control, in both forms."""
        drawing = self._mode == MODE_DRAW
        for button in (self.mode_button, self.pill_mode_button):
            # The icon is the one the contract names for the action the click performs:
            # a cursor to go back to the application, a pencil to start drawing. Reading
            # it the other way round - icon = current mode - puts a violet pencil here
            # and an identical violet pencil two buttons to the right, which is also not
            # what the user's mock-up shows: there the panel is plainly in drawing mode
            # (the pen tool is highlighted) and this button is a plain cursor.
            button.set_kind("cursor" if drawing else "pen")
            button.set_on(False)
            button.setToolTip(
                with_shortcut(
                    _("Drawing - click to work with the application")
                    if drawing
                    else _("Pointer - click to draw"),
                    "mode",
                )
            )

        for tool, button in self.tool_buttons.items():
            button.set_on(drawing and tool == self._tool)
        self.shapes_button.set_kind(TOOL_GLYPHS[self._shape_tool])
        self.shapes_button.setToolTip(tool_tip(self._shape_tool))
        self.shapes_button.set_on(drawing and self._tool in SHAPE_TOOLS)
        self.shapes_popover.set_current(self._shape_tool)

        settings = self.document.settings_for(self._tool)
        has_colour = self._tool != model.TOOL_ERASER
        for button in (self.colour_button, self.pill_colour_button):
            button.set_colour(settings.colour if has_colour else theme.COLOURS["disabled"])
            button.setEnabled(has_colour)
        self.width_chip.set_text_value(px_text(int(settings.width)))

        self.eye_button.set_kind("eye_off" if self._hidden_drawings else "eye")
        self.eye_button.set_on(self._hidden_drawings)
        self.eye_button.setToolTip(
            with_shortcut(
                _("Show the drawings") if self._hidden_drawings
                else _("Hide the drawings"),
                "hide",
            )
        )

        self.pill_tool_button.set_kind(TOOL_GLYPHS[self._tool])
        self.pill_tool_button.set_on(drawing)
        # It is the tool, so it says the tool - the same words the full bar's button for
        # this tool uses. Expanding is `pill_expand_button`'s job and nobody else's.
        self.pill_tool_button.setToolTip(tool_tip(self._tool))

    # --------------------------------------------------------------- actions

    def _choose_tool(self, tool: str) -> None:
        self._close_popovers()
        self.set_tool(tool)
        self.tool_changed.emit(tool)

    def _shapes_clicked(self) -> None:
        self._choose_tool(self._shape_tool)

    def _pill_tool_clicked(self) -> None:
        """The pill's tool button picks the tool it is showing, like the full bar's.

        It no longer expands the panel: that is `pill_expand_button`. Re-choosing the
        current tool is not a no-op to the controller - it is the same `tool_changed` the
        full bar emits when you press the tool you are already using, which is what puts
        the canvas back on that tool if something else moved it.
        """
        self._choose_tool(self._tool)

    def _shape_chosen(self, tool: str) -> None:
        self._shape_tool = tool
        self.set_tool(tool)
        self.tool_changed.emit(tool)

    def _mode_clicked(self) -> None:
        self._close_popovers()
        self.mode_toggled.emit()

    def _settings_edited(self, tool: str) -> None:
        self._sync()
        self.settings_changed.emit(tool)

    def _popover_closed(self) -> None:
        for button in (self.colour_button, self.pill_colour_button):
            button.set_open(False)
        self.width_chip.set_open(False)

    def _toggle_settings_popover(self, anchor: QtWidgets.QWidget) -> None:
        if self.settings_popover.isVisible() and self.settings_popover.anchor() is anchor:
            self.settings_popover.close_popover()
            return
        self.shapes_popover.close_popover()
        self.settings_popover.open_for(self._tool, anchor)
        if isinstance(anchor, ColourButton):
            anchor.set_open(True)
        elif isinstance(anchor, ValueChip):
            anchor.set_open(True)

    def _open_shapes_popover(self) -> None:
        if self.shapes_popover.isVisible():
            self.shapes_popover.close_popover()
            return
        self.settings_popover.close_popover()
        self.shapes_popover.set_current(self._shape_tool)
        self.shapes_popover.open_under(self.shapes_button)

    def _close_popovers(self) -> None:
        self.settings_popover.close_popover()
        self.shapes_popover.close_popover()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._close_popovers()
        super().closeEvent(event)

    # -------------------------------------------------------------- geometry

    def _screens(
        self, monitors: list[winapi.Monitor] | None = None
    ) -> list[winapi.Monitor]:
        if monitors is not None:
            self._monitors = monitors
        elif not self._monitors:
            self._monitors = monitors_now()
        return self._monitors

    def refresh_monitors(self) -> None:
        """Re-read the desktop after a screen was plugged in or unplugged."""
        self._monitors = monitors_now()

    def physical_size(self) -> tuple[int, int]:
        ratio = self.devicePixelRatioF() or 1.0
        return int(round(self.width() * ratio)), int(round(self.height() * ratio))

    def physical_pos(self) -> tuple[int, int]:
        return logical_to_physical(self.pos(), self._screens())

    def _move_physical(self, x: int, y: int) -> None:
        self.move(physical_to_logical(int(x), int(y), self._screens()))

    def place(
        self, x: int, y: int, monitors: list[winapi.Monitor] | None = None
    ) -> None:
        """Put the panel at a **physical desktop pixel**, wholly on a screen.

        This is the position the user chose, so it becomes the home `avoid` returns to.
        No `moved` signal: the caller asked for this and already knows.
        """
        screens = self._screens(monitors)
        width, height = self.physical_size()
        x, y = bar_geometry.clamp_to_monitors(int(x), int(y), width, height, screens)
        self._move_physical(x, y)
        self._home = (x, y)
        self._avoiding = False

    def place_default(self, monitors: list[winapi.Monitor] | None = None) -> None:
        """Top centre of the first monitor - out of the way of most of the screen."""
        screens = self._screens(monitors)
        if not screens:
            _report("no monitors reported; parking the toolbar at 80,80.")
            self.move(80, 80)
            self._home = (80, 80)
            return
        monitor = screens[0]
        width, _height = self.physical_size()
        self.place(
            monitor.x + (monitor.width - width) // 2,
            monitor.y + bar_geometry.MARGIN * 2,
            screens,
        )

    # -- dragging ----------------------------------------------------------

    def begin_drag(self, global_point: QtCore.QPoint) -> None:
        self.refresh_monitors()
        px, py = logical_to_physical(global_point, self._monitors)
        x, y = self.physical_pos()
        self._drag_offset = (px - x, py - y)
        self._dragged = False

    def drag_to(self, global_point: QtCore.QPoint) -> None:
        if self._drag_offset is None:
            return
        px, py = logical_to_physical(global_point, self._monitors)
        width, height = self.physical_size()
        target_x = px - self._drag_offset[0]
        target_y = py - self._drag_offset[1]
        here_x, here_y = self.physical_pos()
        if abs(target_x - here_x) + abs(target_y - here_y) > bar_geometry.DRAG_SLOP:
            self._dragged = True
        target_x, target_y = bar_geometry.snap_to_edges(
            target_x, target_y, width, height, self._monitors
        )
        # And then clamp. Snapping only attracts the panel to an edge; it is the clamp
        # that stops a drag carrying it off the desktop entirely, which was measured
        # leaving a 20x25 sliver on screen and nothing left to grab.
        target_x, target_y = bar_geometry.clamp_to_monitors(
            target_x, target_y, width, height, self._monitors
        )
        self._move_physical(target_x, target_y)

    def end_drag(self) -> None:
        if self._drag_offset is None:
            return
        self._drag_offset = None
        if not self._dragged:
            return  # a press that did not move is a click, and the body has no command
        self._dragged = False
        x, y = self.physical_pos()
        self._home = (x, y)
        self._avoiding = False
        self._close_popovers()
        self.moved.emit(x, y)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.begin_drag(event.globalPosition().toPoint())
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self.drag_to(event.globalPosition().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.end_drag()
        event.accept()

    # -- getting out of the way --------------------------------------------

    def home(self) -> tuple[int, int] | None:
        """Where the user last put it, in physical pixels, or None if never placed."""
        return self._home

    def is_avoiding(self) -> bool:
        return self._avoiding

    def avoid(
        self,
        rect: tuple[int, int, int, int] | None,
        monitors: list[winapi.Monitor] | None = None,
    ) -> bool:
        """Step out of the way of a **physical desktop** rectangle. True if it moved.

        Called with the point a stroke is starting from and with the text box's box: the
        panel must not be under the pointer when a stroke begins, and must not cover what
        is being typed.

        Two rules make it bearable to use rather than merely correct:

        * it always measures from `home` - the place the user put it - and moves along
          whichever single axis clears the rectangle with the smallest step, so repeated
          calls do not walk it across the desktop;
        * it goes **back** to home as soon as the obstruction is gone, including when
          `rect` is None. Dodging borrows the position; it does not take it.

        A candidate that would leave the panel off every screen is refused, so the panel
        cannot be pushed somewhere it cannot be clicked. If none of the four candidates
        is fully on a screen it stays put and says so - half-covered beats unreachable.
        """
        screens = self._screens(monitors)
        if self._home is None:
            self._home = self.physical_pos()
        home_x, home_y = self._home
        width, height = self.physical_size()

        if rect is None:
            return self._go_home(screens)
        left, top, box_w, box_h = (int(round(float(v))) for v in rect)
        if box_w < 0:
            left, box_w = left + box_w, -box_w
        if box_h < 0:
            top, box_h = top + box_h, -box_h

        if not _overlaps(
            home_x, home_y, width, height, left, top, box_w, box_h, AVOID_GAP
        ):
            return self._go_home(screens)

        candidates = (
            (left - AVOID_GAP - width, home_y),
            (left + box_w + AVOID_GAP, home_y),
            (home_x, top - AVOID_GAP - height),
            (home_x, top + box_h + AVOID_GAP),
        )
        best: tuple[int, int, int] | None = None
        for cx, cy in candidates:
            if not _fully_on_screen(cx, cy, width, height, screens):
                continue
            distance = abs(cx - home_x) + abs(cy - home_y)
            if best is None or distance < best[0]:
                best = (distance, cx, cy)
        if best is None:
            _report(
                "no position clears the rectangle and stays on a screen; the toolbar is "
                "staying where it is rather than moving off the desktop."
            )
            return False

        _distance, new_x, new_y = best
        here = self.physical_pos()
        self._avoiding = True
        if (new_x, new_y) == here:
            return False
        self._close_popovers()
        self._move_physical(new_x, new_y)
        return True

    def _go_home(self, screens: list[winapi.Monitor]) -> bool:
        if not self._avoiding or self._home is None:
            return False
        self._avoiding = False
        home_x, home_y = self._home
        if (home_x, home_y) == self.physical_pos():
            return False
        self._move_physical(home_x, home_y)
        return True


# ------------------------------------------------------------------- geometry helpers


def _overlaps(
    ax: int, ay: int, aw: int, ah: int,
    bx: int, by: int, bw: int, bh: int,
    gap: int = 0,
) -> bool:
    """Do the two rectangles touch, once the first is grown by `gap` on every side?"""
    return (
        ax - gap < bx + bw
        and bx < ax + aw + gap
        and ay - gap < by + bh
        and by < ay + ah + gap
    )


def _fully_on_screen(
    x: int, y: int, width: int, height: int, monitors: list[winapi.Monitor]
) -> bool:
    """Is this rectangle wholly inside **one** monitor?

    One, not the union: a panel straddling the seam between two screens is split in half
    by the bezel and is worse than one that stayed put. With no monitor list at all the
    answer is True, because refusing every candidate would make `avoid` a no-op and the
    caller has already been told the enumeration failed.
    """
    if not monitors:
        return True
    for monitor in monitors:
        if (
            x >= monitor.x
            and y >= monitor.y
            and x + width <= monitor.x + monitor.width
            and y + height <= monitor.y + monitor.height
        ):
            return True
    return False


# --------------------------------------------------------- logical <-> physical
#
# The whole model is in physical desktop pixels and Qt's window geometry is in logical
# units. At 100% - both screens on this machine - the two are the same number and every
# function below is the identity, which is exactly why the scaled branch cannot be tested
# here. It is written from Qt's documented high-DPI mapping and mirrors
# `overlay._OverlayBase._to_desktop` deliberately, so a correction found on a scaled
# display can be applied to both at once.
#
# A QScreen is paired with a DXGI output by *geometry*, not by name: measured in
# `overlay`, `QScreen.name()` gives the monitor's friendly name ('Display 32 QHD') while
# `winapi.list_monitors()` gives '\\.\DISPLAY2', so a name comparison never matches.
#
# Both directions share these two messages word for word, so the one `_reported` set
# covers them: a desktop whose screens cannot be paired says it once, not once per
# direction and not once per mouse move.

_UNPAIRED = (
    "a QScreen could not be paired with a DXGI output; toolbar positions are "
    "being used unconverted, which is right at 100% scaling and wrong above it."
)
_OFF_DESKTOP = (
    "a toolbar position lies off every monitor - a screen unplugged since it was "
    "saved, most likely; it is being used unconverted, which is right at 100% "
    "scaling and wrong above it."
)


def _pair_screens(
    monitors: list[winapi.Monitor],
) -> list[tuple[QtGui.QScreen, winapi.Monitor]]:
    pairs: list[tuple[QtGui.QScreen, winapi.Monitor]] = []
    taken: set[int] = set()
    for screen in QtGui.QGuiApplication.screens():
        free = [m for m in monitors if m.index not in taken]
        monitor = _match_screen(screen, free)
        if monitor is None:
            continue
        taken.add(monitor.index)
        pairs.append((screen, monitor))
    return pairs


def _match_screen(
    screen: QtGui.QScreen, free: list[winapi.Monitor]
) -> winapi.Monitor | None:
    name = screen.name()
    for monitor in free:
        if monitor.device_name == name:
            return monitor
    geometry = screen.geometry()
    for monitor in free:
        if (
            geometry.x() == monitor.x
            and geometry.y() == monitor.y
            and geometry.width() == monitor.width
            and geometry.height() == monitor.height
        ):
            return monitor
    ratio = screen.devicePixelRatio() or 1.0
    width = int(round(geometry.width() * ratio))
    height = int(round(geometry.height() * ratio))
    candidates = [m for m in free if m.width == width and m.height == height]
    return candidates[0] if len(candidates) == 1 else None


def logical_to_physical(
    point: QtCore.QPoint, monitors: list[winapi.Monitor]
) -> tuple[int, int]:
    """A Qt global point -> physical desktop pixels."""
    screen = QtGui.QGuiApplication.screenAt(point)
    if screen is not None and monitors:
        for candidate, monitor in _pair_screens(monitors):
            if candidate is screen:
                ratio = screen.devicePixelRatio() or 1.0
                origin = screen.geometry().topLeft()
                return (
                    int(round(monitor.x + (point.x() - origin.x()) * ratio)),
                    int(round(monitor.y + (point.y() - origin.y()) * ratio)),
                )
        _report(_UNPAIRED)
    return (point.x(), point.y())


def physical_to_logical(
    x: int, y: int, monitors: list[winapi.Monitor]
) -> QtCore.QPoint:
    """Physical desktop pixels -> a Qt global point.

    The inverse of `logical_to_physical`, and it reports the same way: whenever the
    conversion cannot be made the number is handed to Qt unchanged, which is right at
    100% scaling and silently wrong above it, so both fall-throughs say so. An empty
    monitor list is not one of them - `monitors_now` has already printed why it is empty.
    """
    containing = next((m for m in monitors if m.contains(x, y)), None)
    if containing is None:
        if monitors:
            _report(_OFF_DESKTOP)
        return QtCore.QPoint(int(x), int(y))
    for screen, candidate in _pair_screens(monitors):
        if candidate.index != containing.index:
            continue
        ratio = screen.devicePixelRatio() or 1.0
        origin = screen.geometry().topLeft()
        return QtCore.QPoint(
            int(round(origin.x() + (x - containing.x) / ratio)),
            int(round(origin.y() + (y - containing.y) / ratio)),
        )
    _report(_UNPAIRED)
    return QtCore.QPoint(int(x), int(y))
