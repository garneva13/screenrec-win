"""The shells every floating thing in this app is made of.

The whole interface is three kinds of surface and nothing else:

* `FloatingSurface` - a frameless, always-on-top, painted plane. The bar, the popovers
  and the settings panel are all this, at different radii, so they read as one design.
* `IconButton` - a 32 px square with a stroked icon, the unit the bar is built from.
* `Chip` - a labelled button with an icon and a chevron, which is how a choice that has
  a current value is shown.

**No drop shadows, anywhere.** Qt will draw one on a translucent frameless window
without rounding its corners, which leaves a square shadow behind a round panel. The
reference this is modelled on hit the same thing and came to the same answer: a hairline
border plus the step in surface colour carries the elevation instead.

Every `paintEvent` here wraps its body in `try/finally: painter.end()`. An exception
escaping a live QPainter takes the interpreter down, and catching it outside the paint
does not help.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import ModuleType

from PySide6 import QtCore, QtGui, QtWidgets

from .. import winapi
from . import icons, theme

BUTTON = 32
ICON = 17

user32 = ctypes.WinDLL("user32", use_last_error=True)
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11


def exclude_from_capture(widget: QtWidgets.QWidget, exclude: bool = True) -> bool:
    """Make a window visible to the user and invisible to screen capture.

    This is what keeps the app's own chrome out of the recording. Measured on this
    machine against the real capture path: a panel with this set contributed **zero**
    pixels to a ddagrab frame while an identical panel beside it contributed 3551.

    It is why the annotation layer needs no special handling in the encoder at all - the
    drawings are an ordinary overlay that the Desktop Duplication API picks up with
    everything else, and only the toolbar is hidden. It also means the recording bar and
    the capture frame can sit on the screen being recorded without appearing in it.

    Needs Windows 10 version 2004 or newer. Returns whether it took effect, and the
    caller must handle False rather than assume - on an older build the window WILL be
    recorded, and the honest answer is to move it or hide it instead.
    """
    handle = int(widget.winId())
    if not handle:
        return False
    try:
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        user32.GetWindowDisplayAffinity.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    except AttributeError:
        return False
    wanted = WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE
    if not user32.SetWindowDisplayAffinity(wintypes.HWND(handle), wanted):
        return False
    # Read it back: the call can succeed and the affinity not stick on older builds.
    current = wintypes.DWORD()
    if not user32.GetWindowDisplayAffinity(wintypes.HWND(handle), ctypes.byref(current)):
        return False
    return current.value == wanted


def _no_activate(widget: QtWidgets.QWidget) -> bool:
    """Keep a surface out of Alt+Tab and stop it taking focus when clicked.

    Qt's `Tool` flag covers most of this, but a click still activates the window, which
    pulls the user out of whatever they were about to demonstrate. The extended style is
    the part Qt does not expose. Reported rather than assumed: the caller can check.
    """
    handle = int(widget.winId())
    if not handle:
        return False
    try:
        user32.GetWindowLongPtrW.restype = ctypes.c_longlong
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = ctypes.c_longlong
        user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND, ctypes.c_int, ctypes.c_longlong
        ]
    except AttributeError:
        return False
    current = user32.GetWindowLongPtrW(wintypes.HWND(handle), GWL_EXSTYLE)
    wanted = current | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    if wanted == current:
        return True
    return user32.SetWindowLongPtrW(wintypes.HWND(handle), GWL_EXSTYLE, wanted) != 0


class FloatingSurface(QtWidgets.QWidget):
    """A painted, rounded, always-on-top plane with a hairline border."""

    def __init__(
        self,
        radius: int = theme.RADIUS["surface"],
        fill: str = "panel_raised",
        border: str = "line",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.radius = radius
        self.fill_key = fill
        self.border_key = border
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    #: Chrome is kept out of the recording; set False on a surface that is meant to be
    #: recorded (the annotation canvas is the only one).
    hide_from_capture = True

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if not getattr(self, "_styled", False):
            self._styled = _no_activate(self)
        if self.hide_from_capture and not getattr(self, "_hidden_from_capture", False):
            # False on Windows 10 before 2004: the surface will be in the recording,
            # and the caller is told so it can move it out of the way instead.
            self._hidden_from_capture = exclude_from_capture(self)

    @property
    def excluded_from_capture(self) -> bool:
        """Whether this surface really is invisible to a screen capture."""
        return bool(getattr(self, "_hidden_from_capture", False))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            painter.setPen(QtGui.QPen(QtGui.QColor(theme.COLOURS[self.border_key]), 1))
            painter.setBrush(QtGui.QColor(theme.COLOURS[self.fill_key]))
            painter.drawRoundedRect(box, self.radius, self.radius)
        finally:
            painter.end()


class IconButton(QtWidgets.QAbstractButton):
    """A square icon button: hover fill, pressed state, an optional "on" accent.

    `role` picks the resting colour family: "quiet" for chrome, "accent" for a toggle
    that is on, "record" for the one red control.
    """

    def __init__(
        self,
        kind: str,
        tooltip: str = "",
        role: str = "quiet",
        size: int = BUTTON,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self.role = role
        self._on = False
        self.setFixedSize(size, size)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        if tooltip:
            self.setToolTip(tooltip)

    def set_on(self, value: bool) -> None:
        if value != self._on:
            self._on = value
            self.update()

    def is_on(self) -> bool:
        return self._on

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
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
        box = QtCore.QRectF(0, 0, self.width(), self.height())
        hovered = self.underMouse() and self.isEnabled()
        pressed = self.isDown()

        if self.role == "record":
            fill = colours["record"]
            if pressed:
                fill = colours["record_press"]
            elif hovered:
                fill = colours["record_hover"]
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(fill))
            painter.drawEllipse(box.adjusted(0.5, 0.5, -0.5, -0.5))
            ink = colours["record_text"]
        else:
            background = None
            if pressed:
                background = colours["line"]
            elif self._on:
                background = colours["hover"]
            elif hovered:
                background = colours["hover"]
            if background is not None:
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(QtGui.QColor(background))
                painter.drawRoundedRect(
                    box, theme.RADIUS["small"], theme.RADIUS["small"]
                )
            if not self.isEnabled():
                ink = colours["disabled"]
            elif self._on:
                ink = colours["accent"]
            elif hovered:
                ink = colours["text"]
            else:
                ink = colours["text_dim"]

        inset = (self.width() - ICON) / 2
        icons.draw(
            painter, self.kind,
            QtCore.QRectF(inset, inset, ICON, ICON),
            ink,
        )

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class Chip(QtWidgets.QAbstractButton):
    """A current value you can click to change: icon, label, chevron."""

    def __init__(
        self,
        kind: str = "monitor",
        text: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self._label = text
        self._open = False
        self.setFixedHeight(BUTTON)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Fixed
        )

    def set_value(self, kind: str, text: str) -> None:
        self.kind = kind
        self._label = text
        self.updateGeometry()
        self.update()

    def set_open(self, value: bool) -> None:
        if value != self._open:
            self._open = value
            self.update()

    def label(self) -> str:
        return self._label

    def _font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(9.5)
        return font

    def sizeHint(self) -> QtCore.QSize:
        metrics = QtGui.QFontMetrics(self._font())
        width = 11 + ICON + 8 + metrics.horizontalAdvance(self._label) + 6 + 14 + 9
        return QtCore.QSize(max(96, min(width, 320)), BUTTON)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(96, BUTTON)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        hovered = self.underMouse() and self.isEnabled()

        painter.setPen(
            QtGui.QPen(QtGui.QColor(colours["line_strong" if hovered else "line"]), 1)
        )
        painter.setBrush(
            QtGui.QColor(colours["hover"] if (hovered or self._open) else colours["bg"])
        )
        painter.drawRoundedRect(box, theme.RADIUS["chip"], theme.RADIUS["chip"])

        ink = colours["text"] if (hovered or self._open) else colours["text_soft"]
        icons.draw(
            painter, self.kind,
            QtCore.QRectF(9, (self.height() - ICON) / 2, ICON, ICON),
            ink,
        )

        painter.setFont(self._font())
        painter.setPen(QtGui.QColor(ink))
        text_left = 9 + ICON + 8
        text_right = self.width() - 9 - 14 - 4
        metrics = QtGui.QFontMetricsF(self._font())
        available = max(10.0, text_right - text_left)
        shown = metrics.elidedText(
            self._label, QtCore.Qt.TextElideMode.ElideMiddle, int(available)
        )
        painter.drawText(
            QtCore.QRectF(text_left, 0, available, self.height()),
            int(
                QtCore.Qt.AlignmentFlag.AlignVCenter
                | QtCore.Qt.AlignmentFlag.AlignLeft
            ),
            shown,
        )
        icons.draw(
            painter,
            "chevron_up" if self._open else "chevron_down",
            QtCore.QRectF(self.width() - 9 - 14, (self.height() - 14) / 2, 14, 14),
            colours["text_dim"],
            weight=1.7,
        )

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class ToggleGroup(QtWidgets.QWidget):
    """Buttons that belong together, inside one hairline outline.

    The outline is deliberately an outline and not a tinted pill: a second filled
    surface inside the bar competes with the record button for attention.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(2)

    def add(self, button: QtWidgets.QWidget) -> None:
        self._layout.addWidget(button)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QtGui.QPen(QtGui.QColor(theme.COLOURS["line"]), 1))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
                theme.RADIUS["control"],
                theme.RADIUS["control"],
            )
        finally:
            painter.end()


class Segmented(QtWidgets.QWidget):
    """Two to four exclusive choices, shown side by side rather than in a list.

    For a choice with few options and no current-value-to-elide problem, a segmented
    control states all of them at once; a combo box hides two thirds of the answer
    behind a click.
    """

    changed = QtCore.Signal(str)

    def __init__(
        self,
        options: list[tuple[str, str]],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.options = options
        self.current = options[0][0] if options else ""
        self._hover = -1
        self.setFixedHeight(30)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def set_current(self, key: str) -> None:
        if any(k == key for k, _ in self.options) and key != self.current:
            self.current = key
            self.update()

    def _font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(9.0)
        return font

    def sizeHint(self) -> QtCore.QSize:
        metrics = QtGui.QFontMetrics(self._font())
        width = 6 + sum(metrics.horizontalAdvance(label) + 24 for _, label in self.options)
        return QtCore.QSize(width, 30)

    def _rects(self) -> list[QtCore.QRectF]:
        metrics = QtGui.QFontMetricsF(self._font())
        widths = [metrics.horizontalAdvance(label) + 24 for _, label in self.options]
        total = sum(widths)
        spare = max(0.0, self.width() - 6 - total)
        share = spare / len(widths) if widths else 0
        rects: list[QtCore.QRectF] = []
        x = 3.0
        for width in widths:
            rects.append(QtCore.QRectF(x, 3, width + share, self.height() - 6))
            x += width + share
        return rects

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(colours["bg"]))
        painter.drawRoundedRect(
            QtCore.QRectF(0, 0, self.width(), self.height()),
            theme.RADIUS["chip"], theme.RADIUS["chip"],
        )
        painter.setFont(self._font())
        for index, (rect, (key, label)) in enumerate(zip(self._rects(), self.options)):
            selected = key == self.current
            if selected:
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(QtGui.QColor(colours["line"]))
                painter.drawRoundedRect(
                    rect, theme.RADIUS["small"], theme.RADIUS["small"]
                )
            elif index == self._hover:
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(QtGui.QColor(colours["hover"]))
                painter.drawRoundedRect(
                    rect, theme.RADIUS["small"], theme.RADIUS["small"]
                )
            painter.setPen(
                QtGui.QColor(colours["text"] if selected else colours["text_dim"])
            )
            painter.drawText(
                rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), label
            )

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        index = self._index_at(event.position())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        index = self._index_at(event.position())
        if index < 0:
            return
        key = self.options[index][0]
        if key != self.current:
            self.current = key
            self.update()
            self.changed.emit(key)

    def _index_at(self, point: QtCore.QPointF) -> int:
        for index, rect in enumerate(self._rects()):
            if rect.contains(point):
                return index
        return -1


def label(text: str, role: str = "text", size: float = 9.5) -> QtWidgets.QLabel:
    """A plain label in one of the text weights, with no stylesheet involved."""
    widget = QtWidgets.QLabel(text)
    font = QtGui.QFont(widget.font())
    font.setPointSizeF(size)
    widget.setFont(font)
    widget.setStyleSheet(f"color: {theme.COLOURS[role]}; background: transparent;")
    return widget


# ------------------------------------------------------------------- dragging
#
# A frameless window has no title bar, so moving it is the application's job. `Bar` and
# the annotation `Toolbar` each grew their own copy of the same handler; this is that
# handler, once, for every other surface that needs it.


def _report(message: str) -> None:
    """Say something on a failure path, without pulling in a logging setup."""
    print(f"surfaces: {message}")


def _bar_geometry() -> ModuleType:
    """`ui.bar`'s clamping and snapping, imported late and on purpose.

    `ui.bar` imports this module - `bar.ToggleButton` subclasses `IconButton` at class
    definition time - so importing it at the top of this file is a cycle that fails
    while this module is still half built. After the first call it is a dict lookup in
    ``sys.modules``, and this runs once per press and once per mouse move.
    """
    from . import bar

    return bar


#: Controls that must keep a press to themselves, so a drag never starts on one. Most
#: of them accept the press and never let it reach the surface anyway; `Segmented` is
#: the reason this list exists, because it implements only *release* and *move*, so its
#: press propagates up to the surface and would otherwise read as bare chrome.
DRAG_BLOCKERS: tuple[type, ...] = (
    QtWidgets.QAbstractButton,
    QtWidgets.QComboBox,
    QtWidgets.QLineEdit,
    QtWidgets.QAbstractSpinBox,
    QtWidgets.QAbstractSlider,
    QtWidgets.QAbstractScrollArea,
    Segmented,
)


def control_at(
    surface: QtWidgets.QWidget, point: QtCore.QPoint
) -> QtWidgets.QWidget | None:
    """The control under `point`, or None when that is bare surface.

    `point` is in `surface` coordinates. The whole chain from the deepest child up to
    the surface is checked, not just the deepest one, because a control may well have
    a plain child of its own under the pointer.
    """
    child = surface.childAt(point)
    while child is not None and child is not surface:
        if isinstance(child, DRAG_BLOCKERS):
            return child
        child = child.parentWidget()
    return None


class SurfaceDrag:
    """Move a frameless surface with the mouse, the way the bar is moved.

    Lifted from `bar.Bar`'s own three handlers rather than written again, and it keeps
    their decisions:

    * the offset is taken at the press and the travel is measured **from the press**,
      not from the last position - a real mouse delivers 1-2 px steps, so comparing
      against the current position never clears `bar.DRAG_SLOP` on a slow drag and the
      move would never be reported;
    * the target snaps to a screen edge within `bar.SNAP`, per axis, so a corner snaps
      on both;
    * and it is then clamped, so the surface cannot be parked where it cannot be
      reached.

    Geometry is `winapi.list_monitors()`, in physical desktop pixels - the same space
    `QWidget.mapToGlobal` reports once DPI awareness is on, which is the assumption
    `bar.Bar` already makes and the one measured on this machine (both screens report
    a device pixel ratio of 1.0). Per-monitor scaling would need the conversion in
    `annotate.toolbar`; it is not needed here and is not pretended to be.

    This owns no window and no Qt state: a surface that does not take focus and is
    excluded from capture keeps both, because nothing here calls `raise_()`,
    `activateWindow()` or `setFocus()`.
    """

    def __init__(self, surface: QtWidgets.QWidget) -> None:
        self._surface = surface
        self._offset: QtCore.QPoint | None = None
        self._press: QtCore.QPoint | None = None
        self._travelled = False
        self._monitors: list[winapi.Monitor] = []

    # -- state

    def active(self) -> bool:
        """Whether a press is being held on the draggable part of the surface."""
        return self._offset is not None

    # -- the drag

    def begin(self, global_point: QtCore.QPoint) -> None:
        """Start from a press at `global_point`. The screens are re-read here."""
        self._monitors = self._screens()
        self._offset = global_point - self._surface.frameGeometry().topLeft()
        self._press = QtCore.QPoint(global_point)
        self._travelled = False

    def to(self, global_point: QtCore.QPoint) -> None:
        """Follow the pointer. Does nothing when no drag is in progress."""
        if self._offset is None:
            return
        geometry = _bar_geometry()
        if (
            self._press is not None
            and (global_point - self._press).manhattanLength() > geometry.DRAG_SLOP
        ):
            self._travelled = True
        target = global_point - self._offset
        width, height = self._surface.width(), self._surface.height()
        x, y = geometry.snap_to_edges(
            target.x(), target.y(), width, height, self._monitors
        )
        # Snap first, then clamp: snapping can only ever move the surface towards an
        # edge, and the clamp is what guarantees it is still wholly on a screen.
        x, y = geometry.clamp_to_monitors(x, y, width, height, self._monitors)
        if (x, y) != (self._surface.x(), self._surface.y()):
            self._surface.move(x, y)

    def end(self) -> bool:
        """Finish. True when the press really was a drag and the surface moved."""
        travelled = self._travelled
        self._offset = None
        self._press = None
        self._travelled = False
        return travelled

    def cancel(self) -> None:
        """Forget a press without treating it as a drag - for a hide mid-drag."""
        self._offset = None
        self._press = None
        self._travelled = False

    def _screens(self) -> list[winapi.Monitor]:
        try:
            return winapi.list_monitors()
        except Exception as exc:  # DXGI fails on a remote session or a driver reload
            _report(
                f"list_monitors() failed ({exc!r}); the drag will not snap or clamp."
            )
            return []
