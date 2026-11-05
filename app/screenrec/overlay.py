"""The two on-screen overlays: pick a region, and show where the mouse was clicked.

Both widgets span the whole desktop - the union of every ``winapi.list_monitors()``
rectangle, rotated screens included - because a selection may cross a monitor edge and a
click may land anywhere.

**Why the click highlight needs nothing from ffmpeg.** The ring is drawn by Windows onto
the real desktop, and the Desktop Duplication API duplicates the composited desktop. So
whatever DDA hands to ``ddagrab`` already contains the ring; there is no overlay filter,
no second input and no compositing step in the recorder for this feature.

**Coordinate spaces.** Qt works in *logical* units; the recorder, ``winapi`` and the mouse
hook all work in *physical* desktop pixels. On a display scaled to 100% the two are the
same number, and this machine runs both of its monitors at 100%, so the scaled case below
is reasoned out but **untested**: see ``_OverlayBase._to_desktop`` for the assumption.

A QScreen is paired with its DXGI output by *geometry*, not by name. Measured here with
Qt 6.12 on the windows plugin: ``QScreen.name()`` gives the monitor's friendly name
(``'Display 32 QHD'``, ``'Display 27 WQHD'``) while ``winapi.list_monitors()`` gives
``\\\\.\\DISPLAY2`` and ``\\\\.\\DISPLAY1``, so a name comparison never matches and the
per-screen conversion would quietly never run. See ``_OverlayBase._match_screen``.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass, field

from PySide6 import QtCore, QtGui, QtWidgets

from . import i18n, winapi
from .i18n import _

# --------------------------------------------------------------------- look

DIM_COLOUR = QtGui.QColor(8, 10, 14, 150)
ACCENT = QtGui.QColor(0x4A, 0xC8, 0xFF)
BADGE_BG = QtGui.QColor(16, 20, 26, 225)
BADGE_TEXT = QtGui.QColor(0xEC, 0xF2, 0xF8)

#: Ring colour per mouse button. An unknown button falls back to the left colour.
BUTTON_COLOURS: dict[str, QtGui.QColor] = {
    "left": QtGui.QColor(0x4A, 0xC8, 0xFF),
    "right": QtGui.QColor(0xFF, 0xB0, 0x3A),
    "middle": QtGui.QColor(0x5C, 0xE0, 0x9B),
}

RING_MS = 450.0
RING_START_RADIUS = 14.0
RING_END_RADIUS = 34.0
RING_TICK_MS = 16  # ~60 Hz

# --------------------------------------------------------------- native bits

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

user32 = ctypes.WinDLL("user32", use_last_error=True)


def _get_window_long(hwnd: int) -> int:
    """GetWindowLongPtrW, falling back to the 32-bit name on a 32-bit interpreter."""
    func = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    func.argtypes = [wintypes.HWND, ctypes.c_int]
    func.restype = ctypes.c_longlong if hasattr(user32, "GetWindowLongPtrW") else ctypes.c_long
    return int(func(wintypes.HWND(hwnd), GWL_EXSTYLE))


def _set_window_long(hwnd: int, value: int) -> int:
    func = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    func.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
    func.restype = ctypes.c_longlong if hasattr(user32, "SetWindowLongPtrW") else ctypes.c_long
    ctypes.set_last_error(0)
    return int(func(wintypes.HWND(hwnd), GWL_EXSTYLE, value))


def make_click_through(hwnd: int) -> str:
    """Add WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE to a window.

    Qt's ``WindowTransparentForInput`` already asks for this, but it is applied when the
    native window is created and a later ``show()`` or style change can lose it. Setting
    the bits ourselves after the window exists is the belt to Qt's braces: if a click does
    not reach the application underneath, the whole feature is worse than not having it.

    Returns an empty string, or the reason the styles could not be set.
    """
    if not hwnd:
        return "The overlay has no native window yet; click-through was not applied."
    current = _get_window_long(hwnd)
    wanted = current | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    if wanted == current:
        return ""
    result = _set_window_long(hwnd, wanted)
    if result == 0 and ctypes.get_last_error() != 0:
        return (
            "Click-through could not be enabled on the highlight overlay "
            f"(SetWindowLongPtrW error {ctypes.get_last_error()}); clicks would be eaten."
        )
    # Prove it took rather than trusting the return value, which is the *previous* style.
    if not _get_window_long(hwnd) & WS_EX_TRANSPARENT:
        return "Click-through did not stick: WS_EX_TRANSPARENT is still clear."
    return ""


# ------------------------------------------------------------------ geometry


def desktop_union() -> QtCore.QRect:
    """The bounding box of every DXGI output, in physical desktop pixels.

    An empty monitor list (no attached output, or DXGI unavailable) gives an empty
    rectangle rather than a guess, so the caller can see that something went wrong.
    """
    monitors = winapi.list_monitors()
    if not monitors:
        return QtCore.QRect()
    left = min(m.x for m in monitors)
    top = min(m.y for m in monitors)
    right = max(m.x + m.width for m in monitors)
    bottom = max(m.y + m.height for m in monitors)
    return QtCore.QRect(left, top, right - left, bottom - top)


class _OverlayBase(QtWidgets.QWidget):
    """Shared plumbing: desktop-wide geometry and the two coordinate conversions.

    Not part of the public API; `RegionSelector` and `ClickHighlight` are still plain
    `QtWidgets.QWidget` subclasses as far as any caller is concerned.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.monitors = winapi.list_monitors()
        self.desktop_rect = desktop_union()
        self.geometry_problem = ""
        #: Set when a QScreen could not be paired with a DXGI output and a coordinate had
        #: to go through the whole-desktop fallback. Not fatal - the numbers are right at
        #: 100% scaling - but the caller must be able to find out that it happened.
        self.mapping_problem = ""
        if self.desktop_rect.isEmpty():
            self.geometry_problem = (
                "No attached monitor was reported by DXGI; the overlay has no geometry."
            )
        self.setGeometry(self._window_rect())

    # -- geometry ----------------------------------------------------------

    def _window_rect(self) -> QtCore.QRect:
        """Where to put the window, in the units ``setGeometry`` wants (logical).

        At 100% scaling - every screen reporting devicePixelRatio 1.0, which is the case
        on this machine and under the offscreen platform - logical units *are* physical
        pixels, so the physical union is used directly and ``geometry()`` can be compared
        with ``winapi.list_monitors()`` straight off.

        If any screen is scaled, Qt's logical desktop is smaller than the physical one and
        handing it the physical union would make an oversized window. Then the union of the
        QScreen geometries is used instead. **This branch is untested**: both monitors here
        run at 100%.
        """
        screens = QtGui.QGuiApplication.screens()
        scaled = any(abs(s.devicePixelRatio() - 1.0) > 1e-6 for s in screens)
        if scaled and screens:
            logical = QtCore.QRect()
            for screen in screens:
                logical = logical.united(screen.geometry())
            return logical
        return QtCore.QRect(self.desktop_rect)

    # -- pairing a QScreen with a DXGI output ------------------------------
    #
    # Measured on this machine (Qt 6.12, windows plugin): ``QScreen.name()`` returns the
    # monitor's *friendly* name - 'Display 32 QHD', 'Display 27 WQHD' - while
    # ``winapi.list_monitors()`` reports '\\.\DISPLAY2' and '\\.\DISPLAY1'. Matching the
    # two by name therefore never succeeds here, so the pairing is done by geometry, with
    # the name kept only as a first try for a Qt build that does report the device name.

    def _pair_screens(self) -> tuple[list[tuple[QtGui.QScreen, winapi.Monitor]], list[str]]:
        """Pair every QScreen with its DXGI output. Returns (pairs, unpaired screen names)."""
        pairs: list[tuple[QtGui.QScreen, winapi.Monitor]] = []
        unpaired: list[str] = []
        taken: set[int] = set()
        for screen in QtGui.QGuiApplication.screens():
            monitor = self._match_screen(screen, taken)
            if monitor is None:
                unpaired.append(screen.name())
            else:
                taken.add(monitor.index)
                pairs.append((screen, monitor))
        return pairs, unpaired

    def _match_screen(self, screen: QtGui.QScreen, taken: set[int]) -> winapi.Monitor | None:
        """The DXGI output this QScreen is, or None if it cannot be told apart."""
        free = [m for m in self.monitors if m.index not in taken]
        name = screen.name()
        for monitor in free:
            if monitor.device_name == name:
                return monitor

        geometry = screen.geometry()
        for monitor in free:
            # Exact at 100% scaling, where Qt's logical desktop *is* the physical one.
            if (
                geometry.x() == monitor.x
                and geometry.y() == monitor.y
                and geometry.width() == monitor.width
                and geometry.height() == monitor.height
            ):
                return monitor

        # Scaled: Qt's logical origin is not the physical one, but logical size times the
        # device pixel ratio is the physical size. Accept it only when it is unambiguous.
        ratio = screen.devicePixelRatio() or 1.0
        width = int(round(geometry.width() * ratio))
        height = int(round(geometry.height() * ratio))
        candidates = [m for m in free if m.width == width and m.height == height]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _note_unpaired(self, names: list[str]) -> None:
        """Record that a conversion fell back, so it is not a silent wrong answer."""
        if not names:
            return
        self.mapping_problem = (
            "These screens could not be paired with a DXGI output, so points on them are "
            "converted with the whole-desktop fallback and will be wrong on a mixed-DPI "
            "desktop: " + ", ".join(names) + "."
        )

    def _monitor_for_screen(self, screen: QtGui.QScreen) -> winapi.Monitor | None:
        """The DXGI output a QScreen is, or None (and `mapping_problem` set) if unknown."""
        pairs, unpaired = self._pair_screens()
        for candidate, monitor in pairs:
            if candidate is screen:
                return monitor
        self._note_unpaired(unpaired or [screen.name()])
        return None

    def _to_desktop(self, local: QtCore.QPoint) -> QtCore.QPoint:
        """Widget-local logical point -> physical desktop pixel.

        Assumption, stated because it cannot be checked here: with per-monitor-v2 awareness
        Qt6 reports *logical* coordinates, and a point's physical position is the owning
        monitor's physical origin plus the logical offset into that screen multiplied by the
        screen's device pixel ratio. That is exact for the uniform case and is what Qt's own
        ``QHighDpi`` mapping does. Untested on a scaled display - this machine is at 100%,
        where the whole expression collapses to the identity.
        """
        global_point = self.mapToGlobal(local)
        screen = QtGui.QGuiApplication.screenAt(global_point)
        if screen is not None:
            monitor = self._monitor_for_screen(screen)
            if monitor is not None:
                ratio = screen.devicePixelRatio() or 1.0
                origin = screen.geometry().topLeft()
                return QtCore.QPoint(
                    int(round(monitor.x + (global_point.x() - origin.x()) * ratio)),
                    int(round(monitor.y + (global_point.y() - origin.y()) * ratio)),
                )
        # No screen owns the point (it is between monitors, or the platform plugin has no
        # real screens): scale about the overlay's own origin instead.
        ratio = self.devicePixelRatioF() or 1.0
        return QtCore.QPoint(
            int(round(self.desktop_rect.x() + local.x() * ratio)),
            int(round(self.desktop_rect.y() + local.y() * ratio)),
        )

    def _from_desktop(self, x: int, y: int) -> QtCore.QPointF:
        """Physical desktop pixel -> widget-local logical point. Inverse of `_to_desktop`."""
        for monitor in self.monitors:
            if not monitor.contains(x, y):
                continue
            screen = self._screen_for_monitor(monitor)
            if screen is None:
                break
            ratio = screen.devicePixelRatio() or 1.0
            origin = screen.geometry().topLeft()
            global_point = QtCore.QPoint(
                int(round(origin.x() + (x - monitor.x) / ratio)),
                int(round(origin.y() + (y - monitor.y) / ratio)),
            )
            local = self.mapFromGlobal(global_point)
            return QtCore.QPointF(local)
        ratio = self.devicePixelRatioF() or 1.0
        return QtCore.QPointF(
            (x - self.desktop_rect.x()) / ratio,
            (y - self.desktop_rect.y()) / ratio,
        )

    def _screen_for_monitor(self, monitor: winapi.Monitor) -> QtGui.QScreen | None:
        """The QScreen a DXGI output is. Inverse of `_monitor_for_screen`, same pairing."""
        pairs, unpaired = self._pair_screens()
        for screen, candidate in pairs:
            if candidate.index == monitor.index:
                return screen
        self._note_unpaired(unpaired or [monitor.label])
        return None


# ------------------------------------------------------------ region selector


class RegionSelector(_OverlayBase):
    """Full-desktop dim; drag to choose a rectangle. Esc cancels, Enter accepts.

    The emitted rectangle is in physical desktop pixels and is **not** rounded, snapped or
    made even - `recorder.even` is the caller's job, and doing it twice would move the
    edge the user placed.
    """

    selected = QtCore.Signal(int, int, int, int)  # desktop x, y, w, h
    cancelled = QtCore.Signal()

    #: Logical units; anything smaller is a click, not a drag.
    #:
    #: It is the floor on what can be chosen, and it is deliberately not the floor on
    #: what can be *recorded*, which belongs to the encoder and not to this widget.
    #: Measured on 2026-11-05 through the real `ddagrab -> encoder` pipeline on this
    #: machine: `h264_nvenc` refuses anything under 146x50 and `hevc_nvenc` under
    #: 130x34, while `libx264` encoded every shape tried from 2x2 upwards. A drag of
    #: three logical units reaches the recorder as 2x2 - `recorder.surface_crop` makes
    #: every dimension even and at least two - and that is the worst case, at the 100%
    #: scaling this desk runs and this was measured at; above 100% three logical units
    #: are more physical pixels, never fewer. `ui/app.py` records a frame below the
    #: chosen encoder's floor on the processor, so the smallest drag this allows is a
    #: rectangle that records rather than one that fails after the record button.
    #:
    #: Refusing here instead was the alternative and was rejected: the floor is a
    #: property of one graphics card, and the only cards anybody on this project can
    #: measure are NVIDIA's. See `ui/app.py:_encoder_for_frame`.
    MIN_DRAG = 3

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("ScreenRec - select a region"))
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._origin: QtCore.QPoint | None = None
        self._current: QtCore.QPoint | None = None
        self._dragging = False
        self._finished = False

        # setWindowFlags can recreate the native window and lose a geometry set before it,
        # so the desktop-wide rectangle is applied last (and again in show_overlay).
        self.setGeometry(self._window_rect())
        i18n.notifier.changed.connect(self.retranslate)

    # -- text --------------------------------------------------------------

    def retranslate(self) -> None:
        """Re-read the window title, which is the string this overlay holds.

        The hint and the size badge are not held anywhere: `paintEvent` builds them
        every time it draws, so whatever they say they will say again on the next
        repaint without being told.
        """
        self.setWindowTitle(_("ScreenRec - select a region"))

    # -- public ------------------------------------------------------------

    def show_overlay(self) -> str:
        """Show, come to the front and take the keyboard, without a window manager fight.

        The selector is reusable: every call starts a fresh pick, because a selector that
        kept `_finished` from the previous one would show an always-on-top dim window that
        answers neither a drag nor Esc, and the user would have no way out of it.

        Returns an empty string, or why the overlay cannot be shown - it is not shown at
        all in that case, since a 0x0 window that can never emit anything would look to
        `choose()`'s caller exactly like a cancel.
        """
        self._origin = None
        self._current = None
        self._dragging = False
        self._finished = False
        if self.desktop_rect.isEmpty():
            return self.geometry_problem
        self.setGeometry(self._window_rect())
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        return ""

    @staticmethod
    def choose(parent: QtWidgets.QWidget | None = None) -> tuple[int, int, int, int] | None:
        """Run the selector modally. Returns the desktop rectangle, or None if cancelled.

        `None` means the user cancelled, and nothing else: if there is no desktop to select
        on, this raises `RuntimeError` with the reason rather than returning a `None` the
        caller would have to read as a cancel. (It already raises for a missing
        QApplication, so a caller of this function is prepared for that.)
        """
        app = QtWidgets.QApplication.instance()
        if app is None:
            raise RuntimeError(
                "RegionSelector.choose() needs a running QApplication; create one first."
            )
        selector = RegionSelector(parent)
        result: list[tuple[int, int, int, int]] = []
        loop = QtCore.QEventLoop()

        def took(x: int, y: int, w: int, h: int) -> None:
            result.append((x, y, w, h))
            loop.quit()

        selector.selected.connect(took)
        selector.cancelled.connect(loop.quit)
        # Every other way the window can go away also has to end the loop, or the whole GUI
        # thread sits in loop.exec() for ever: closeEvent covers close()/Alt+F4, and this
        # covers the application quitting out from under the selector.
        app.aboutToQuit.connect(loop.quit)
        try:
            problem = selector.show_overlay()
            if problem:
                raise RuntimeError(problem)
            loop.exec()
        finally:
            try:
                app.aboutToQuit.disconnect(loop.quit)
            except (RuntimeError, TypeError) as exc:
                # Never fatal - the connection is going away with `loop` either way - but
                # it is not allowed to pass unseen.
                selector.mapping_problem = (
                    selector.mapping_problem
                    or f"aboutToQuit could not be disconnected from the selector loop: {exc}"
                )
            selector.close()
            selector.deleteLater()
        return result[0] if result else None

    def current_desktop_rect(self) -> tuple[int, int, int, int] | None:
        """The rectangle as it stands, in physical desktop pixels, or None if degenerate."""
        if self._origin is None or self._current is None:
            return None
        first = self._to_desktop(self._origin)
        second = self._to_desktop(self._current)
        x = min(first.x(), second.x())
        y = min(first.y(), second.y())
        width = abs(second.x() - first.x())
        height = abs(second.y() - first.y())
        if width < 1 or height < 1:
            return None
        return x, y, width, height

    # -- events ------------------------------------------------------------

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        self._origin = event.position().toPoint()
        self._current = self._origin
        self._dragging = True
        self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._dragging:
            return
        self._current = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        self._current = event.position().toPoint()
        if self._origin is None:
            self._cancel()
            return
        moved_x = abs(self._current.x() - self._origin.x())
        moved_y = abs(self._current.y() - self._origin.y())
        if moved_x < self.MIN_DRAG or moved_y < self.MIN_DRAG:
            # A click with no drag: the user changed their mind.
            self._cancel()
            return
        self._accept()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if key == QtCore.Qt.Key.Key_Escape:
            self._cancel()
            return
        if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            if self.current_desktop_rect() is None:
                self._cancel()
            else:
                self._accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            selection = self._local_selection()

            # Dim everything, then punch the selection out with the odd-even fill rule.
            path = QtGui.QPainterPath()
            path.addRect(QtCore.QRectF(self.rect()))
            if selection is not None:
                path.addRect(QtCore.QRectF(selection))
            painter.fillPath(path, DIM_COLOUR)

            if selection is not None:
                pen = QtGui.QPen(ACCENT)
                pen.setWidth(1)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                # adjusted(): a 1px pen straddles the edge, so sit it just inside.
                painter.drawRect(QtCore.QRectF(selection).adjusted(0.5, 0.5, -0.5, -0.5))
                self._paint_badge(painter, selection)
            else:
                self._paint_hint(painter)
        finally:
            # An exception escaping a live QPainter takes the interpreter down with it.
            painter.end()

    # -- painting helpers --------------------------------------------------

    def _local_selection(self) -> QtCore.QRect | None:
        if self._origin is None or self._current is None:
            return None
        rect = QtCore.QRect(self._origin, self._current).normalized()
        if rect.width() < 1 or rect.height() < 1:
            return None
        return rect

    def _paint_badge(self, painter: QtGui.QPainter, selection: QtCore.QRect) -> None:
        desktop = self.current_desktop_rect()
        if desktop is None:
            return
        text = f"{desktop[2]} × {desktop[3]}"
        font = painter.font()
        font.setPointSizeF(max(9.0, font.pointSizeF()))
        font.setBold(True)
        painter.setFont(font)
        metrics = QtGui.QFontMetricsF(font)
        pad_x, pad_y = 10.0, 5.0
        width = metrics.horizontalAdvance(text) + pad_x * 2
        height = metrics.height() + pad_y * 2

        anchor = self._current or selection.bottomRight()
        badge = QtCore.QRectF(anchor.x() + 14, anchor.y() + 16, width, height)
        bounds = QtCore.QRectF(self._screen_bounds(anchor))
        if badge.right() > bounds.right():
            badge.moveLeft(anchor.x() - 14 - width)
        if badge.bottom() > bounds.bottom():
            badge.moveTop(anchor.y() - 16 - height)
        if badge.left() < bounds.left():
            badge.moveLeft(bounds.left() + 4)
        if badge.top() < bounds.top():
            badge.moveTop(bounds.top() + 4)

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(BADGE_BG)
        painter.drawRoundedRect(badge, 4, 4)
        painter.setPen(BADGE_TEXT)
        painter.drawText(badge, QtCore.Qt.AlignmentFlag.AlignCenter, text)

    def _paint_hint(self, painter: QtGui.QPainter) -> None:
        text = _(
            "Drag to choose a region    •    Enter accepts"
            "    •    Esc cancels"
        )
        font = painter.font()
        font.setPointSizeF(max(11.0, font.pointSizeF() + 1.0))
        painter.setFont(font)
        metrics = QtGui.QFontMetricsF(font)
        bounds = QtCore.QRectF(self._screen_bounds(self._cursor_local()))
        width = metrics.horizontalAdvance(text) + 28
        height = metrics.height() + 16
        box = QtCore.QRectF(
            bounds.center().x() - width / 2, bounds.top() + 48, width, height
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(BADGE_BG)
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(BADGE_TEXT)
        painter.drawText(box, QtCore.Qt.AlignmentFlag.AlignCenter, text)

    def _cursor_local(self) -> QtCore.QPoint:
        return self.mapFromGlobal(QtGui.QCursor.pos())

    def _screen_bounds(self, local: QtCore.QPoint) -> QtCore.QRect:
        """The widget-local rectangle of the screen under `local`, for keeping text on it."""
        screen = QtGui.QGuiApplication.screenAt(self.mapToGlobal(local))
        if screen is None:
            return self.rect()
        geometry = screen.geometry()
        top_left = self.mapFromGlobal(geometry.topLeft())
        return QtCore.QRect(top_left, geometry.size()).intersected(self.rect())

    # -- finishing ---------------------------------------------------------

    def _accept(self) -> None:
        if self._finished:
            return
        rect = self.current_desktop_rect()
        if rect is None:
            self._cancel()
            return
        self._finished = True
        self.hide()
        self.selected.emit(*rect)

    def _cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.cancelled.emit()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Treat any other way of disappearing as a cancel.

        `close()` - Alt+F4, `QApplication.closeAllWindows()`, a parent window tearing down
        - reaches neither `_accept` nor `_cancel`, so without this the nested loop in
        `choose()` would wait for a signal that can never arrive and the GUI thread would
        never come back. `_cancel` is a no-op once the selection is finished, so the
        `close()` that `choose()` itself does at the end emits nothing extra.
        """
        self._cancel()
        super().closeEvent(event)


# ------------------------------------------------------------ click highlight


@dataclass
class _Ring:
    """One live ring, in physical desktop pixels plus the moment it was born."""

    x: int
    y: int
    colour: QtGui.QColor
    born: float = field(default_factory=time.monotonic)

    def age(self, now: float) -> float:
        """0.0 at birth, 1.0 when it should be gone."""
        return (now - self.born) / (RING_MS / 1000.0)


class ClickHighlight(_OverlayBase):
    """A click-through overlay that draws a fading ring wherever the mouse is pressed.

    Threading: `clicks.ClickWatcher` calls back on its own hook thread. Touching a widget
    from there is a crash waiting for a busy moment, so the callback does nothing but emit
    `_click_received`, which is connected with `Qt.ConnectionType.QueuedConnection` and
    therefore runs `flash` on the GUI thread.
    """

    _click_received = QtCore.Signal(int, int, str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("ScreenRec - click highlight"))
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.error = ""
        self._rings: list[_Ring] = []
        self._watcher = None  # clicks.ClickWatcher, imported lazily

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(RING_TICK_MS)
        self._timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

        self._click_received.connect(
            self._on_click_received, QtCore.Qt.ConnectionType.QueuedConnection
        )

        # As in RegionSelector: geometry after the flags, because the flags can recreate
        # the native window.
        self.setGeometry(self._window_rect())
        i18n.notifier.changed.connect(self.retranslate)

    # -- text --------------------------------------------------------------

    def retranslate(self) -> None:
        """Re-read the window title. This overlay draws rings and no words."""
        self.setWindowTitle(_("ScreenRec - click highlight"))

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> str:
        """Show the overlay and begin watching the mouse. Returns "" or why it failed."""
        if self.desktop_rect.isEmpty():
            # A 0x0 window can never draw a ring. Returning "" here would tell the window
            # code the highlight is on while nothing of it exists.
            self.error = self.geometry_problem
            return self.error

        try:
            from . import clicks
        except ImportError as exc:
            self.error = f"The click watcher is unavailable: {exc}"
            return self.error

        if self._watcher is not None and getattr(self._watcher, "active", False):
            return ""

        self.setGeometry(self._window_rect())
        self.show()
        self.raise_()

        problem = self._apply_click_through()
        if problem:
            # Eating the user's clicks is worse than having no highlight at all.
            self.hide()
            self.error = problem
            return problem

        self._watcher = clicks.ClickWatcher(self._on_click_thread)
        problem = self._watcher.start()
        if problem:
            self._watcher = None
            self.hide()
            self.error = problem
            return problem
        self.error = ""
        return ""

    def stop(self) -> None:
        """Stop watching, drop every ring and hide. Safe to call when not started."""
        if self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception as exc:  # a hook that will not come off must not strand us
                self.error = f"The click watcher did not stop cleanly: {exc}"
            self._watcher = None
        self._timer.stop()
        self._rings.clear()
        self.hide()

    @property
    def active(self) -> bool:
        return self._watcher is not None and bool(getattr(self._watcher, "active", False))

    @property
    def ring_count(self) -> int:
        return len(self._rings)

    @property
    def timer_running(self) -> bool:
        return self._timer.isActive()

    # -- the ring ----------------------------------------------------------

    def flash(self, x: int, y: int, button: str = "left") -> None:
        """Add a ring at a physical desktop pixel. Safe only on the GUI thread."""
        colour = BUTTON_COLOURS.get(button, BUTTON_COLOURS["left"])
        self._rings.append(_Ring(x=int(x), y=int(y), colour=colour))
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        self._rings = [ring for ring in self._rings if ring.age(now) < 1.0]
        if not self._rings:
            self._timer.stop()
        self.update()

    # -- the watcher -------------------------------------------------------

    def _on_click_thread(self, event) -> None:
        """Called on the hook thread. Emits only - never touches a widget from here."""
        try:
            self._click_received.emit(int(event.x), int(event.y), str(event.button))
        except Exception as exc:
            # The hook thread has nowhere to raise to; record it where start()'s caller
            # and the log can see it.
            self.error = f"A click could not be forwarded to the overlay: {exc}"

    @QtCore.Slot(int, int, str)
    def _on_click_received(self, x: int, y: int, button: str) -> None:
        self.flash(x, y, button)

    # -- native ------------------------------------------------------------

    def _apply_click_through(self) -> str:
        """Force the extended window styles on. Only meaningful on the windows plugin."""
        platform = QtGui.QGuiApplication.platformName()
        if platform != "windows":
            # offscreen/minimal have no HWND; there is no desktop to click through either.
            return ""
        return make_click_through(int(self.winId()))

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            now = time.monotonic()
            for ring in self._rings:
                progress = min(1.0, max(0.0, ring.age(now)))
                radius = RING_START_RADIUS + (RING_END_RADIUS - RING_START_RADIUS) * progress
                fade = 1.0 - progress
                colour = QtGui.QColor(ring.colour)
                colour.setAlphaF(0.85 * fade)
                pen = QtGui.QPen(colour)
                pen.setWidthF(1.5 + 2.0 * fade)
                painter.setPen(pen)
                fill = QtGui.QColor(ring.colour)
                fill.setAlphaF(0.18 * fade)
                painter.setBrush(fill)
                centre = self._from_desktop(ring.x, ring.y)
                painter.drawEllipse(centre, radius, radius)
        finally:
            painter.end()
