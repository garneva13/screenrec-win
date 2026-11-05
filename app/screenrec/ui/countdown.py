"""The countdown you can see, over the screen that is about to be recorded.

The first version counted down in a tooltip on the record button. Nobody saw it, and the
user reported the countdown as missing - correctly, because a number that only exists in
a tooltip does not exist. It is now a large ring on the screen being recorded.

It is **excluded from screen capture**, so the countdown itself never lands in the file,
and it is **click-through**, so nothing is blocked while it runs. Clicking the bar or
pressing the hotkey still cancels, because those are what the user will reach for.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n, winapi
from ..i18n import _
from . import surfaces, theme

SIZE = 220
RING = 9

user32 = ctypes.WinDLL("user32", use_last_error=True)
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000


class Countdown(QtWidgets.QWidget):
    """A ring that empties as the seconds run out."""

    finished = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowTransparentForInput
            | QtCore.Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.resize(SIZE, SIZE)

        self._total = 0
        self._left = 0.0
        self._tick = QtCore.QTimer(self)
        self._tick.setInterval(40)
        self._tick.timeout.connect(self._advance)
        i18n.notifier.changed.connect(self.retranslate)

    # -- language ----------------------------------------------------------

    def retranslate(self, code: str = "") -> None:
        """The one word here is read inside `_paint`, so a repaint is the whole job."""
        self.update()

    # -- lifecycle ---------------------------------------------------------

    def start(self, seconds: int, monitor: winapi.Monitor | None = None) -> None:
        """Count `seconds` down in the middle of `monitor` (or the primary one)."""
        if seconds <= 0:
            self.finished.emit()
            return
        self._total = seconds
        self._left = float(seconds)

        screens = winapi.list_monitors()
        target = monitor or (screens[0] if screens else None)
        if target is not None:
            self.move(
                target.x + (target.width - SIZE) // 2,
                target.y + (target.height - SIZE) // 2,
            )
        self.show()
        self._make_click_through()
        self._tick.start()
        self.update()

    def cancel(self) -> None:
        self._tick.stop()
        self.hide()

    @property
    def running(self) -> bool:
        return self._tick.isActive()

    def seconds_left(self) -> int:
        return max(0, int(self._left + 0.999))

    def _advance(self) -> None:
        self._left -= self._tick.interval() / 1000
        if self._left <= 0:
            self._tick.stop()
            self.hide()
            self.finished.emit()
            return
        self.update()

    def _make_click_through(self) -> bool:
        """Qt's flag plus the extended style, because only the pair is reliable."""
        handle = int(self.winId())
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
        wanted = current | WS_EX_TRANSPARENT | WS_EX_LAYERED
        user32.SetWindowLongPtrW(wintypes.HWND(handle), GWL_EXSTYLE, wanted)
        # The countdown is chrome: it must not be in the recording it is counting into.
        return surfaces.exclude_from_capture(self)

    # -- drawing -----------------------------------------------------------

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        box = QtCore.QRectF(RING, RING, SIZE - RING * 2, SIZE - RING * 2)

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        backdrop = QtGui.QColor(colours["panel"])
        backdrop.setAlphaF(0.88)
        painter.setBrush(backdrop)
        painter.drawEllipse(box)

        track = QtGui.QPen(QtGui.QColor(colours["line_strong"]), RING)
        painter.setPen(track)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box)

        fraction = max(0.0, min(1.0, self._left / max(1, self._total)))
        arc = QtGui.QPen(QtGui.QColor(colours["record"]), RING)
        arc.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(box, 90 * 16, -int(360 * 16 * fraction))

        font = QtGui.QFont(self.font())
        font.setPointSizeF(64)
        font.setWeight(QtGui.QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(colours["text"]))
        painter.drawText(
            box, int(QtCore.Qt.AlignmentFlag.AlignCenter), str(self.seconds_left())
        )

        font.setPointSizeF(11)
        font.setWeight(QtGui.QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(colours["text_dim"]))
        painter.drawText(
            QtCore.QRectF(0, SIZE - 54, SIZE, 24),
            int(QtCore.Qt.AlignmentFlag.AlignCenter),
            _("recording starts"),
        )
