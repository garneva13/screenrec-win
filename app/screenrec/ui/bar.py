"""The bar: a floating strip that *is* the application.

The old window was a settings form - four cards, twelve controls, all at one level of
importance. Recording is an action, so this is built round the action: the source you
are about to record, the devices that will be in it, two settings worth changing often,
and the red button. Everything else lives behind the gear.

Three decisions are worth stating, because they are the ones that are easy to undo by
accident:

* **Two layouts, one widget.** Idle and recording are two child containers; switching
  hides one and shows the other and then resizes the surface to its own ``sizeHint``.
  A layout ignores a hidden child, so the hidden half contributes nothing - but only if
  the resize actually happens, which is why every state setter ends in ``_fit()``.
  Leaving the old width behind is the whole bug class this guards against.
* **Nothing takes focus.** ``FloatingSurface`` sets ``WA_ShowWithoutActivating``, the
  ``Tool`` flag and ``WS_EX_NOACTIVATE``. Nothing here calls ``raise_()``,
  ``activateWindow()`` or ``setFocus()``: clicking Record must not pull the user out of
  whatever they were about to demonstrate.
* **There is no pause.** ffmpeg cannot pause a capture cleanly, and a button that lies
  is worse than one that is missing. Stop, then record again.

The drag handling, the 28 px edge snapping, ``move_off`` and the elapsed-clock format
are carried over from the island this replaces; they were proven there.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n, winapi
from ..i18n import _, pick
from . import icons, surfaces, theme

#: How close to a screen edge the bar has to be dragged before it parks on it.
SNAP = 28
#: The gap the bar keeps from an edge it has parked on.
MARGIN = 18
#: A press that moves further than this is a drag; anything shorter is a click.
DRAG_SLOP = 2

RECORD_BUTTON = 36
TOGGLE_WIDTH = 46
#: The left share of a toggle button that toggles; the rest opens its menu.
TOGGLE_SPLIT = 0.70

METER_BARS = 5
METER_INTERVAL_MS = 50
METER_DECAY = 0.12
#: Ticks of silence before the meter's one timer switches itself off.
METER_IDLE_TICKS = 12


# --------------------------------------------------------------------- geometry


def format_elapsed(seconds: float) -> str:
    """``mm:ss`` under an hour, ``h:mm:ss`` over it - the island's format."""
    minutes, secs = divmod(int(max(0.0, seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _detail_text(megabytes: float, fps: float) -> str:
    """The size and rate line of the recording strip, in the current language."""
    return _("{size} MB · {fps} fps").format(
        size=f"{megabytes:.0f}", fps=f"{fps:.0f}"
    )


def _dropped_text(count: int) -> str:
    """The warning that replaces it while frames are being lost."""
    return _("{count} dropped").format(
        count=count,
        word=i18n.plural(
            count,
            pick("plural.chrome.frame.one", "frame"),
            pick("plural.chrome.frame.few", "frames"),
            pick("plural.chrome.frame.many", "frames"),
        ),
    )


def monitor_for(
    monitors: list[winapi.Monitor], x: int, y: int, width: int, height: int
) -> winapi.Monitor | None:
    """The monitor the bar's centre lands on, or None when it is off every screen."""
    return winapi.monitor_at(monitors, x + width // 2, y + height // 2)


def clamp_to_monitors(
    x: int, y: int, width: int, height: int, monitors: list[winapi.Monitor]
) -> tuple[int, int]:
    """Keep the bar wholly on a screen.

    A position whose centre is on no screen at all - a monitor was unplugged since the
    position was saved - is pulled to the bottom right of the first monitor rather than
    left where it cannot be clicked.
    """
    if not monitors:
        return x, y
    target = monitor_for(monitors, x, y, width, height)
    if target is None:
        target = monitors[0]
        x = target.x + target.width - width - MARGIN
        y = target.y + target.height - height - MARGIN
    x = max(target.x, min(target.x + target.width - width, x))
    y = max(target.y, min(target.y + target.height - height, y))
    return x, y


def snap_to_edges(
    x: int,
    y: int,
    width: int,
    height: int,
    monitors: list[winapi.Monitor],
    snap: int = SNAP,
    margin: int = MARGIN,
) -> tuple[int, int]:
    """Pull to a screen edge when within `snap`, so the bar parks tidily.

    Each axis is decided on its own, so a corner snaps on both. A position that is on no
    screen is returned unchanged - there is no edge to measure against.
    """
    target = monitor_for(monitors, x, y, width, height)
    if target is None:
        return x, y
    left, top = target.x + margin, target.y + margin
    right = target.x + target.width - width - margin
    bottom = target.y + target.height - height - margin
    if abs(x - left) < snap:
        x = left
    elif abs(x - right) < snap:
        x = right
    if abs(y - top) < snap:
        y = top
    elif abs(y - bottom) < snap:
        y = bottom
    return x, y


def shorten_path(path: str, parts: int = 2) -> str:
    """The tail of a path, for a button that has to fit on a strip."""
    text = str(path).replace("/", "\\").rstrip("\\")
    if not text:
        return ""
    bits = [bit for bit in text.split("\\") if bit]
    if len(bits) <= parts:
        return text
    return "…\\" + "\\".join(bits[-parts:])


# ----------------------------------------------------------------------- pieces


class _HLine(QtWidgets.QWidget):
    """The hairline between the main row and the footer."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.fillRect(self.rect(), QtGui.QColor(theme.COLOURS["line"]))
        finally:
            painter.end()


class _VSep(QtWidgets.QWidget):
    """A short vertical hairline separating two groups on the row."""

    def __init__(self, height: int = 20, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(1)
        self._height = height
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed
        )

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(1, self._height)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.fillRect(self.rect(), QtGui.QColor(theme.COLOURS["line"]))
        finally:
            painter.end()


class LevelMeter(QtWidgets.QWidget):
    """Five bars that light in "live" green with what the microphone is hearing.

    Fed by `set_mic_level`, which may arrive at any rate at all - so each value is taken
    as a **peak**, never as a level to hold. The rise is immediate (a peak that is not
    shown is a peak that did not help) and the fall is a fixed step per tick, driven by
    the clock rather than by the caller, which is what stops the thing flickering on
    speech.

    Clock-driven decay is the whole point: the mic feed stops when recording stops, when
    the device is unplugged, or whenever the controller stops polling, and in every one
    of those cases nothing arrives to say "zero". A meter that waits for an explicit zero
    latches on the last value it saw and sits there fully lit, repainting 20 times a
    second beside a running screen capture, showing a signal that is not arriving.

    One `QTimer(self)` drives the whole decay, and it stops itself once the level has
    been zero for `METER_IDLE_TICKS` ticks: this widget sits next to a running screen
    capture and must not repaint when there is nothing to show.
    """

    BAR_WIDTH = 3
    BAR_GAP = 3

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._quiet = 0
        self._active = True
        self.setFixedSize(
            METER_BARS * self.BAR_WIDTH + (METER_BARS - 1) * self.BAR_GAP, 20
        )
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(METER_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    # -- state

    def set_mic_level(self, level: float) -> None:
        """Push a peak, 0..1.

        Values outside the range are clamped rather than refused; a meter must not
        raise. NaN (`level != level`) reads as silence. A value only ever *raises* the
        displayed level - the fall belongs to `_tick`, so a caller that stops feeding us
        sees the bars drop instead of a frozen full signal.
        """
        value = 0.0 if level != level else max(0.0, min(1.0, float(level)))
        if value > self._level:
            self._level = value
            self.update()
        if value > 0.0:
            self._quiet = 0
            if not self._timer.isActive():
                self._timer.start()

    def set_active(self, active: bool) -> None:
        """Off means the microphone is not armed; the bars go grey, not green."""
        if active != self._active:
            self._active = active
            self.update()

    def level(self) -> float:
        return self._level

    def lit_bars(self) -> int:
        """Half-up, not `round`: at exactly 0.5 a banker's round lights two of five."""
        return min(METER_BARS, int(self._level * METER_BARS + 0.5))

    def timer_running(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        """One step of the fall. The level ages whether or not anyone is feeding us."""
        self._level = max(0.0, self._level - METER_DECAY)
        if self._level <= 0.001:
            self._level = 0.0
            self._quiet += 1
            if self._quiet >= METER_IDLE_TICKS:
                self._timer.stop()
                self._quiet = 0
        else:
            self._quiet = 0
        self.update()

    # -- paint

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        lit = self.lit_bars()
        on_colour = QtGui.QColor(
            theme.COLOURS["live" if self._active else "disabled"]
        )
        off_colour = QtGui.QColor(theme.COLOURS["line"])
        bottom = float(self.height() - 2)
        for index in range(METER_BARS):
            tall = 7.0 + index * 2.6
            left = index * (self.BAR_WIDTH + self.BAR_GAP)
            painter.setBrush(on_colour if index < lit else off_colour)
            painter.drawRoundedRect(
                QtCore.QRectF(left, bottom - tall, self.BAR_WIDTH, tall), 1.5, 1.5
            )


class Keycap(QtWidgets.QWidget):
    """The hotkey as a key, not as a sentence: a rounded box with a hairline border.

    The width comes from `QFontMetrics`, so "Ctrl+Alt+R" and "F9" both fit exactly.
    """

    PAD_X = 7
    PAD_Y = 3

    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed
        )

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.updateGeometry()
            self.update()

    def text(self) -> str:
        return self._text

    def _font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(8.5)
        return font

    def sizeHint(self) -> QtCore.QSize:
        metrics = QtGui.QFontMetrics(self._font())
        return QtCore.QSize(
            metrics.horizontalAdvance(self._text) + 2 * self.PAD_X,
            metrics.height() + 2 * self.PAD_Y,
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        if not self._text:
            return
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.COLOURS["line_strong"]), 1))
        painter.setBrush(QtGui.QColor(theme.COLOURS["bg"]))
        painter.drawRoundedRect(box, 6, 6)
        painter.setFont(self._font())
        painter.setPen(QtGui.QColor(theme.COLOURS["text_dim"]))
        painter.drawText(
            QtCore.QRectF(0, 0, self.width(), self.height()),
            int(QtCore.Qt.AlignmentFlag.AlignCenter),
            self._text,
        )


class Pill(QtWidgets.QAbstractButton):
    """A small text button: outlined for a quick setting, flat for the footer."""

    MAX_WIDTH = 240

    def __init__(
        self,
        text: str = "",
        kind: str = "",
        outlined: bool = True,
        height: int = 26,
        point_size: float = 9.0,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self.kind = kind
        self.outlined = outlined
        self.point_size = point_size
        self.setFixedHeight(height)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed
        )

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.updateGeometry()
            self.update()

    def text(self) -> str:
        return self._text

    def _font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(self.point_size)
        return font

    def _pad(self) -> int:
        return 10 if self.outlined else 6

    def _icon_span(self) -> int:
        return (14 + 6) if self.kind else 0

    def sizeHint(self) -> QtCore.QSize:
        metrics = QtGui.QFontMetrics(self._font())
        width = (
            2 * self._pad() + self._icon_span() + metrics.horizontalAdvance(self._text)
        )
        return QtCore.QSize(min(width, self.MAX_WIDTH), self.height())

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        enabled = self.isEnabled()
        hovered = self.underMouse() and enabled
        pressed = self.isDown() and enabled
        box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        if self.outlined:
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(colours["line_strong" if hovered else "line"]), 1
                )
            )
            painter.setBrush(
                QtGui.QColor(colours["hover"] if (hovered or pressed) else colours["bg"])
            )
            painter.drawRoundedRect(box, theme.RADIUS["chip"], theme.RADIUS["chip"])
        elif hovered or pressed:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(colours["hover"]))
            painter.drawRoundedRect(box, theme.RADIUS["small"], theme.RADIUS["small"])

        if not enabled:
            ink = colours["disabled"]
        elif hovered:
            ink = colours["text"]
        else:
            ink = colours["text_soft"] if self.outlined else colours["text_dim"]

        left = float(self._pad())
        if self.kind:
            icons.draw(
                painter,
                self.kind,
                QtCore.QRectF(left, (self.height() - 14) / 2, 14, 14),
                ink,
                weight=1.7,
            )
            left += 14 + 6

        painter.setFont(self._font())
        painter.setPen(QtGui.QColor(ink))
        available = max(8.0, self.width() - left - self._pad())
        metrics = QtGui.QFontMetricsF(self._font())
        shown = metrics.elidedText(
            self._text, QtCore.Qt.TextElideMode.ElideMiddle, int(available)
        )
        painter.drawText(
            QtCore.QRectF(left, 0, available, self.height()),
            int(
                QtCore.Qt.AlignmentFlag.AlignVCenter
                | QtCore.Qt.AlignmentFlag.AlignLeft
            ),
            shown,
        )

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class ToggleButton(surfaces.IconButton):
    """A toggle with a menu on its right edge: one widget, two zones.

    Two widgets jammed together would need their own outline to look like one control,
    and would hand the user two tooltips and two hover states for one thing. This is one
    button: the left `TOGGLE_SPLIT` of it toggles the device, the right part opens its
    device list. The chevron is drawn, and the hover highlight follows the zone under the
    pointer, so the split is visible before it is clicked.
    """

    menu_requested = QtCore.Signal()

    def __init__(
        self,
        kind: str,
        tooltip: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(kind, tooltip=tooltip, role="quiet", parent=parent)
        self.setFixedSize(TOGGLE_WIDTH, surfaces.BUTTON)
        self.setMouseTracking(True)
        self._zone = ""
        self._press_zone = ""

    # -- zones

    def split_x(self) -> float:
        return self.width() * TOGGLE_SPLIT

    def zone_at(self, position: QtCore.QPointF | QtCore.QPoint) -> str:
        return "menu" if float(position.x()) >= self.split_x() else "toggle"

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(TOGGLE_WIDTH, surfaces.BUTTON)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._press_zone = self.zone_at(event.position())
        if self._press_zone == "menu":
            # Handled here, not by QAbstractButton: the menu zone must not leave the
            # button looking pressed, and must not emit `clicked`.
            event.accept()
            self.update()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self._press_zone == "menu":
            self._press_zone = ""
            if self.rect().contains(event.position().toPoint()):
                self.menu_requested.emit()
            event.accept()
            self.update()
            return
        self._press_zone = ""
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        zone = self.zone_at(event.position())
        if zone != self._zone:
            self._zone = zone
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._zone = ""
        super().leaveEvent(event)

    # -- paint

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        enabled = self.isEnabled()
        hovered = self.underMouse() and enabled
        radius = theme.RADIUS["small"]
        split = self.split_x()
        full = QtCore.QRectF(0, 0, self.width(), self.height())

        if self.is_on() and enabled:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(colours["hover"]))
            painter.drawRoundedRect(full, radius, radius)
        if hovered and self._zone:
            zone_rect = (
                QtCore.QRectF(0, 0, split, self.height())
                if self._zone == "toggle"
                else QtCore.QRectF(split, 0, self.width() - split, self.height())
            )
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(
                QtGui.QColor(colours["line"] if self.isDown() else colours["hover"])
            )
            painter.drawRoundedRect(zone_rect, radius, radius)

        if not enabled:
            ink = colours["disabled"]
        elif self.is_on():
            ink = colours["accent"]
        elif hovered:
            ink = colours["text"]
        else:
            ink = colours["text_dim"]

        icon_size = surfaces.ICON
        icons.draw(
            painter,
            self.kind,
            QtCore.QRectF(
                (split - icon_size) / 2,
                (self.height() - icon_size) / 2,
                icon_size,
                icon_size,
            ),
            ink,
        )
        chevron = 12.0
        icons.draw(
            painter,
            "chevron_down",
            QtCore.QRectF(
                split + (self.width() - split - chevron) / 2,
                (self.height() - chevron) / 2,
                chevron,
                chevron,
            ),
            colours["disabled"] if not enabled else colours["text_dim"],
            weight=1.6,
        )


class Clock(QtWidgets.QWidget):
    """The elapsed time in tabular figures.

    Every digit is drawn in a cell as wide as the widest digit, so the seconds ticking
    over cannot shuffle the rest of the row sideways. The width is reserved for
    ``0:00:00`` from the start, so crossing an hour does not resize the bar either.
    """

    RESERVE = "0:00:00"

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = format_elapsed(0.0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed
        )

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.update()

    def set_dim(self, dim: bool) -> None:
        """A paused clock is drawn dim, so it cannot be read as a running one.

        Mistaking a paused recording for a running one is how the end of a take gets
        lost, and a clock standing still is easy to miss at a glance.
        """
        if dim != getattr(self, "_dim", False):
            self._dim = dim
            self.update()

    def text(self) -> str:
        return self._text

    def _font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(12.5)
        font.setWeight(QtGui.QFont.Weight.DemiBold)
        return font

    def _cell(self, metrics: QtGui.QFontMetricsF) -> float:
        return max(metrics.horizontalAdvance(digit) for digit in "0123456789")

    def _width_of(self, text: str, metrics: QtGui.QFontMetricsF) -> float:
        cell = self._cell(metrics)
        return sum(
            cell if character.isdigit() else metrics.horizontalAdvance(character)
            for character in text
        )

    def sizeHint(self) -> QtCore.QSize:
        metrics = QtGui.QFontMetricsF(self._font())
        width = max(
            self._width_of(self._text, metrics), self._width_of(self.RESERVE, metrics)
        )
        return QtCore.QSize(int(width + 2), int(metrics.height() + 2))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        font = self._font()
        painter.setFont(font)
        painter.setPen(QtGui.QColor(theme.COLOURS["text"]))
        metrics = QtGui.QFontMetricsF(font)
        cell = self._cell(metrics)
        x = 0.0
        for character in self._text:
            width = cell if character.isdigit() else metrics.horizontalAdvance(character)
            painter.drawText(
                QtCore.QRectF(x, 0, width, self.height()),
                int(QtCore.Qt.AlignmentFlag.AlignCenter),
                character,
            )
            x += width


# -------------------------------------------------------------------------- bar


class Bar(surfaces.FloatingSurface):
    """The floating control strip. Idle it offers the recording; recording it reports it."""

    record_requested = QtCore.Signal()
    stop_requested = QtCore.Signal()
    pause_requested = QtCore.Signal()
    quit_requested = QtCore.Signal()
    discard_requested = QtCore.Signal()
    source_clicked = QtCore.Signal()
    settings_clicked = QtCore.Signal()
    mic_toggled = QtCore.Signal(bool)
    system_toggled = QtCore.Signal(bool)
    clicks_toggled = QtCore.Signal(bool)
    mic_menu_requested = QtCore.Signal()
    system_menu_requested = QtCore.Signal()
    fps_clicked = QtCore.Signal()
    format_clicked = QtCore.Signal()
    folder_clicked = QtCore.Signal()
    last_file_clicked = QtCore.Signal()
    moved = QtCore.Signal(int, int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(
            radius=theme.RADIUS["bar"], fill="panel", border="line_strong", parent=parent
        )
        self.setObjectName("ScreenRecBar")
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

        self._recording = False
        self._in_shot = False
        self._paused = False
        self._countdown = 0
        self._status = ""
        self._status_warn = False
        self._last_file = ""
        self._folder = ""
        self._hotkey = ""
        #: The last numbers `set_progress` was given, kept so the detail line can be
        #: written again in another language without waiting for the next tick.
        self._megabytes = 0.0
        self._fps = 0.0
        self._dropped = 0
        self._drag_from: QtCore.QPoint | None = None
        self._press_global: QtCore.QPoint | None = None
        self._dragged = False
        #: The monitors the bar was last placed against, so a resize can re-anchor
        #: without another DXGI enumeration. Empty until the bar has been placed.
        self._monitors: list[winapi.Monitor] = []
        self._placed = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._idle = self._build_idle()
        self._rec = self._build_recording()
        root.addWidget(self._idle)
        root.addWidget(self._rec)
        self._rec.hide()

        # One line under both layouts for whatever the application has to say. It is
        # inside the bar rather than a toast, because a message that floats away is a
        # message the user did not read.
        self.status_label = surfaces.label("", role="text_dim", size=8.5)
        self.status_label.setWordWrap(True)
        self.status_label.setContentsMargins(13, 0, 13, 7)
        self.status_label.setMaximumWidth(520)
        self.status_label.hide()
        root.addWidget(self.status_label)
        i18n.notifier.changed.connect(self.retranslate)
        self._fit()

    # ------------------------------------------------------------ construction

    def _build_idle(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self)
        column = QtWidgets.QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        row = QtWidgets.QWidget(container)
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(9, 8, 9, 8)
        line.setSpacing(7)

        self.source_chip = surfaces.Chip(
            "monitor", pick("source.screen_number", "Screen {number}").format(number=1)
        )
        self.source_chip.setToolTip(_("Choose what to record"))
        self.source_chip.clicked.connect(self.source_clicked.emit)
        line.addWidget(self.source_chip)

        self.toggles = surfaces.ToggleGroup(row)
        # Three of the tooltips here are also row labels in the settings panel, which is
        # another area's catalogue block, and a key may live in only one block. `pick`
        # keeps each area's copy its own, exactly as it does for a word with two senses.
        self.mic_button = ToggleButton(
            "microphone_off", pick("bar.microphone", "Microphone")
        )
        self.system_button = ToggleButton(
            "speaker_off", pick("bar.system_sound", "System sound")
        )
        self.clicks_button = ToggleButton("pointer", _("Cursor and clicks"))
        for button in (self.mic_button, self.system_button, self.clicks_button):
            self.toggles.add(button)
        self.mic_button.clicked.connect(
            lambda: self._toggle(self.mic_button, self.mic_toggled)
        )
        self.system_button.clicked.connect(
            lambda: self._toggle(self.system_button, self.system_toggled)
        )
        self.clicks_button.clicked.connect(
            lambda: self._toggle(self.clicks_button, self.clicks_toggled)
        )
        self.mic_button.menu_requested.connect(self.mic_menu_requested.emit)
        self.system_button.menu_requested.connect(self.system_menu_requested.emit)
        # The pointer toggle has no device list, so its chevron opens the settings
        # panel, where "Show the cursor" and "Highlight clicks" live.
        self.clicks_button.menu_requested.connect(self.settings_clicked.emit)
        line.addWidget(self.toggles)

        self.meter = LevelMeter(row)
        self.meter.setToolTip(_("Microphone level"))
        line.addWidget(self.meter)

        line.addWidget(_VSep(20, row))

        self.fps_pill = Pill(_("{rate} fps").format(rate=60))
        self.fps_pill.setToolTip(pick("bar.frame_rate", "Frame rate"))
        self.fps_pill.clicked.connect(self.fps_clicked.emit)
        line.addWidget(self.fps_pill)

        self.format_pill = Pill("MP4 · H.264")
        self.format_pill.setToolTip(_("Format and quality"))
        self.format_pill.clicked.connect(self.format_clicked.emit)
        line.addWidget(self.format_pill)

        self.record_button = surfaces.IconButton(
            "record", _("Start recording"), role="record", size=RECORD_BUTTON
        )
        self.record_button.clicked.connect(self.record_requested.emit)
        line.addWidget(self.record_button)

        self.settings_button = surfaces.IconButton("settings", _("Settings"))
        self.settings_button.clicked.connect(self.settings_clicked.emit)
        line.addWidget(self.settings_button)

        # A visible way out. The first version had none at all - no close button and no
        # tray icon - so the only way to stop the program was Task Manager, and a second
        # launch quietly started a rival copy.
        self.quit_button = surfaces.IconButton("close", _("Quit ScreenRec"))
        self.quit_button.clicked.connect(self.quit_requested.emit)
        line.addWidget(self.quit_button)

        column.addWidget(row)
        column.addWidget(_HLine(container))

        footer = QtWidgets.QWidget(container)
        strip = QtWidgets.QHBoxLayout(footer)
        strip.setContentsMargins(10, 5, 11, 7)
        strip.setSpacing(4)
        self.folder_button = Pill(
            "", kind="folder", outlined=False, height=24, point_size=8.5
        )
        self.folder_button.setToolTip(_("Open the output folder"))
        self.folder_button.clicked.connect(self.folder_clicked.emit)
        strip.addWidget(self.folder_button)

        self.last_file_button = Pill(
            _("Last file"), outlined=False, height=24, point_size=8.5
        )
        self.last_file_button.setEnabled(False)
        self.last_file_button.setToolTip(_("Nothing recorded yet"))
        self.last_file_button.clicked.connect(self.last_file_clicked.emit)
        strip.addWidget(self.last_file_button)

        strip.addStretch(1)
        self.keycap = Keycap("", footer)
        self.keycap.setToolTip(_("Start or stop from anywhere"))
        strip.addWidget(self.keycap)
        column.addWidget(footer)
        self._footer = footer
        return container

    def _build_recording(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self)
        line = QtWidgets.QHBoxLayout(container)
        line.setContentsMargins(9, 8, 9, 8)
        line.setSpacing(8)

        self.stop_button = surfaces.IconButton(
            "stop", _("Stop recording"), role="record", size=RECORD_BUTTON
        )
        self.stop_button.clicked.connect(self.stop_requested.emit)
        line.addWidget(self.stop_button)

        # Pause really pauses: the segment ends, a new one starts on resume, and the
        # two are joined losslessly when the recording stops. One file, no gap in the
        # clock, no re-encoding.
        self.pause_button = surfaces.IconButton("pause", _("Pause"))
        self.pause_button.clicked.connect(self.pause_requested.emit)
        line.addWidget(self.pause_button)

        self.clock = Clock(container)
        line.addWidget(self.clock)

        self.detail = surfaces.label(_detail_text(0.0, 0.0), role="text_dim", size=8.5)
        metrics = QtGui.QFontMetrics(self.detail.font())
        self.detail.setMinimumWidth(
            metrics.horizontalAdvance(_detail_text(8888.0, 240.0))
        )
        line.addWidget(self.detail)
        line.addStretch(1)

        self.discard_button = surfaces.IconButton("trash", _("Stop and discard"))
        self.discard_button.clicked.connect(self.discard_requested.emit)
        line.addWidget(self.discard_button)

        self.folder_button_rec = surfaces.IconButton(
            "folder", _("Open the output folder")
        )
        self.folder_button_rec.clicked.connect(self.folder_clicked.emit)
        line.addWidget(self.folder_button_rec)
        return container

    def _toggle(self, button: ToggleButton, signal: QtCore.SignalInstance) -> None:
        button.set_on(not button.is_on())
        if button is self.mic_button:
            button.set_kind("microphone" if button.is_on() else "microphone_off")
            self.meter.set_active(button.is_on())
        elif button is self.system_button:
            button.set_kind("speaker" if button.is_on() else "speaker_off")
        signal.emit(button.is_on())

    # ------------------------------------------------------------------- state

    def set_source(self, kind: str, text: str) -> None:
        """`kind` is an icon name - monitor, monitor_rotated, window or region."""
        if kind not in icons.KINDS:
            # Not fatal, but it would be a blank chip, and silence would hide the typo.
            print(f"bar: unknown source icon {kind!r}; falling back to 'monitor'.")
            kind = "monitor"
        self.source_chip.set_value(kind, text)
        self._fit()

    def set_source_open(self, open_: bool) -> None:
        """Turn the chip's chevron while its popover is up."""
        self.source_chip.set_open(open_)

    def set_toggles(self, mic: bool, system: bool, clicks: bool) -> None:
        self.mic_button.set_on(mic)
        self.mic_button.set_kind("microphone" if mic else "microphone_off")
        self.system_button.set_on(system)
        self.system_button.set_kind("speaker" if system else "speaker_off")
        self.clicks_button.set_on(clicks)
        self.meter.set_active(mic)

    def set_quick(self, fps_text: str, format_text: str) -> None:
        self.fps_pill.set_text(fps_text)
        self.format_pill.set_text(format_text)
        self._fit()

    def set_footer(self, folder: str, last_file: str, hotkey: str) -> None:
        self._folder = folder
        self._last_file = last_file
        self._hotkey = hotkey
        self.folder_button.set_text(shorten_path(folder))
        self.folder_button.setToolTip(folder or _("No output folder set"))
        self.last_file_button.setEnabled(bool(last_file))
        self.last_file_button.setToolTip(last_file or _("Nothing recorded yet"))
        self.keycap.set_text(hotkey)
        self.keycap.setVisible(bool(hotkey))
        self._fit()

    def set_recording(self, recording: bool, in_shot: bool = False) -> None:
        """`in_shot` means the bar itself is on a screen that is being recorded."""
        self._recording = bool(recording)
        self._in_shot = bool(recording and in_shot)
        self._idle.setVisible(not self._recording)
        self._rec.setVisible(self._recording)
        # In shot the bar shrinks to the stop button and the clock, and fades, because
        # every extra pixel of it is in the file.
        self.detail.setVisible(self._recording and not self._in_shot)
        self.discard_button.setVisible(self._recording and not self._in_shot)
        self.folder_button_rec.setVisible(self._recording and not self._in_shot)
        self.setWindowOpacity(0.55 if self._in_shot else 1.0)
        if not self._recording:
            self.clock.set_text(format_elapsed(0.0))
            self._megabytes = 0.0
            self._fps = 0.0
            self._dropped = 0
            self._render_detail()
        self._fit()

    def set_paused(self, paused: bool) -> None:
        """Show that the clock has stopped, so a paused recording cannot be mistaken
        for a running one - which is how people lose the end of a take."""
        self._paused = paused
        self.pause_button.set_kind("record" if paused else "pause")
        self.pause_button.setToolTip(
            _("Carry on recording") if paused else _("Pause")
        )
        self.pause_button.set_on(paused)
        self.clock.set_dim(paused)
        self.update()

    def set_countdown(self, seconds: int) -> None:
        """Count down on the record button itself, where the user is already looking."""
        self._countdown = seconds
        if seconds > 0:
            self.record_button.setToolTip(
                _("Starting in {count}\u2026 click to cancel").format(count=seconds)
            )
        else:
            self.record_button.setToolTip(_("Start recording"))
        self.update()

    def set_status(self, text: str, warn: bool = False) -> None:
        """One line under the bar. Empty text hides it again."""
        self._status = text
        self._status_warn = warn
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            "color: {}; background: transparent;".format(
                theme.COLOURS["warn" if warn else "text_dim"]
            )
        )
        self.status_label.setVisible(bool(text))
        self._fit()

    def set_progress(
        self, seconds: float, megabytes: float, fps: float, dropped: int = 0
    ) -> None:
        self.clock.set_text(format_elapsed(seconds))
        self._megabytes = float(megabytes)
        self._fps = float(fps)
        self._dropped = int(dropped)
        self._render_detail()

    def _render_detail(self) -> None:
        """Write the detail line from the numbers last given, in today's language."""
        if self._dropped:
            self._set_detail(_dropped_text(self._dropped), dropped=True)
        else:
            self._set_detail(
                _detail_text(self._megabytes, self._fps), dropped=False
            )

    def _set_detail(self, text: str, dropped: bool) -> None:
        colour = theme.COLOURS["warn" if dropped else "text_dim"]
        self.detail.setText(text)
        self.detail.setStyleSheet(f"color: {colour}; background: transparent;")

    def set_mic_level(self, level: float) -> None:
        self.meter.set_mic_level(level)

    def is_recording(self) -> bool:
        return self._recording

    # --------------------------------------------------------------- language

    def retranslate(self, code: str = "") -> None:
        """Re-read every string on the bar. Connected to `i18n.notifier.changed`.

        Two strings are not here: the source chip's label and the status line arrive
        already worded from the controller, which is the only thing that knows what
        they say. `ui/app.py` connects to this same signal and re-words both - see its
        `_retranslate`, which rebuilds the chip, the pills and the footer, and `_say`,
        which keeps what produced the status line so it can be run again.
        """
        self.source_chip.setToolTip(_("Choose what to record"))
        self.mic_button.setToolTip(pick("bar.microphone", "Microphone"))
        self.system_button.setToolTip(pick("bar.system_sound", "System sound"))
        self.clicks_button.setToolTip(_("Cursor and clicks"))
        self.meter.setToolTip(_("Microphone level"))
        self.fps_pill.setToolTip(pick("bar.frame_rate", "Frame rate"))
        self.format_pill.setToolTip(_("Format and quality"))
        self.settings_button.setToolTip(_("Settings"))
        self.quit_button.setToolTip(_("Quit ScreenRec"))
        self.folder_button.setToolTip(_("Open the output folder"))
        self.last_file_button.set_text(_("Last file"))
        self.keycap.setToolTip(_("Start or stop from anywhere"))
        self.stop_button.setToolTip(_("Stop recording"))
        self.discard_button.setToolTip(_("Stop and discard"))
        self.folder_button_rec.setToolTip(_("Open the output folder"))
        # The reserved width is a measurement of the text, so it has to be measured
        # again: a longer language would otherwise shuffle the row as the size changes.
        metrics = QtGui.QFontMetrics(self.detail.font())
        self.detail.setMinimumWidth(
            metrics.horizontalAdvance(_detail_text(8888.0, 240.0))
        )
        self._render_detail()
        self.set_countdown(self._countdown)  # the record button's tooltip
        self.set_paused(self._paused)  # the pause button's tooltip
        self.set_footer(self._folder, self._last_file, self._hotkey)

    # ---------------------------------------------------------------- geometry

    def _fit(self) -> None:
        """Resize to what the visible half of the bar actually needs.

        The whole point of the two-layout design, and the one thing that breaks it: a
        switch that does not resize leaves the recording strip wearing the idle width.

        Measured here on 2026-11-03, offscreen: invalidating the layouts is **not**
        enough. The root layout holds a cached item per child widget, and that cache is
        cleared by the child's own `updateGeometry()`, not by `QLayout.invalidate()` -
        Qt otherwise clears it from a *posted* LayoutRequest, which has not run yet when
        a setter resizes in the same call. With invalidation alone the bar came out one
        state behind: hiding the detail, trash and folder for the in-shot strip left it
        at the full recording width of 458 px instead of 183. So every descendant is
        asked to update its geometry first. There are a few dozen and this runs only on
        a state change.

        A resize is then followed by `_reanchor`, because growing the bar in place is
        how a control ends up off the edge of the screen.
        """
        # Read first: measured offscreen on 2026-11-04, `QLayout.activate()` on a
        # top-level widget resizes that widget to the layout's hint by itself, so by the
        # time the explicit `resize` below runs, `self.size()` is no longer the size the
        # bar was wearing when the setter was called. Re-anchoring against that already
        # changed size left the bar 18 px off the corner it had been parked in.
        before = self.size()
        for child in self.findChildren(QtWidgets.QWidget):
            child.updateGeometry()
        for nested in self.findChildren(QtWidgets.QLayout):
            nested.invalidate()
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        hint = self.sizeHint()
        if hint.isValid() and hint != self.size():
            self.resize(hint)
        if hint.isValid() and hint != before:
            self._reanchor(before, hint)

    def _reanchor(self, before: QtCore.QSize, after: QtCore.QSize) -> None:
        """Keep a resized bar where it was parked, and wholly on a screen.

        A size change is not a move, so Qt leaves the top-left corner where it was and
        grows the bar to the right and downwards. That is wrong in both directions. A
        longer source name grows the idle strip, and the record button and the gear -
        the two rightmost controls - walk off the right of the screen where they cannot
        be clicked. Going the other way, `set_recording` shrinks the strip and a bar
        parked in the bottom right corner suddenly sits a couple of hundred pixels away
        from the corner it was put in.

        So the gap to whichever edge the bar is nearer is what is preserved, per axis,
        and the result is clamped. The monitor list is the one the bar was last placed
        against: this runs on every state change and must not pay for a DXGI
        enumeration, and before the bar has been placed there is nothing to anchor to.
        """
        if not self._placed or not self._monitors:
            return
        x, y = self.x(), self.y()
        target = monitor_for(self._monitors, x, y, before.width(), before.height())
        if target is not None:
            if (x - target.x) > (target.x + target.width - (x + before.width())):
                x += before.width() - after.width()
            if (y - target.y) > (target.y + target.height - (y + before.height())):
                y += before.height() - after.height()
        x, y = clamp_to_monitors(x, y, after.width(), after.height(), self._monitors)
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def place(
        self, x: int, y: int, monitors: list[winapi.Monitor] | None = None
    ) -> None:
        """Move the bar, keeping it wholly on a screen.

        `monitors` is accepted so a caller that already has the list - and a test - does
        not pay for another DXGI enumeration.
        """
        screens = monitors if monitors is not None else winapi.list_monitors()
        # Remembered so `_reanchor` can keep a later resize on screen without asking
        # DXGI again on every state change.
        self._monitors = list(screens)
        self._placed = True
        x, y = clamp_to_monitors(x, y, self.width(), self.height(), screens)
        self.move(x, y)

    def place_default(self, monitors: list[winapi.Monitor] | None = None) -> None:
        """Bottom right of the first monitor, clear of the task bar."""
        screens = monitors if monitors is not None else winapi.list_monitors()
        if not screens:
            print("bar: no monitors reported; parking the bar at 80,80.")
            self._monitors = []
            self._placed = False
            self.move(80, 80)
            return
        monitor = screens[0]
        self.place(
            monitor.x + monitor.width - self.width() - MARGIN,
            monitor.y + monitor.height - self.height() - MARGIN * 3,
            screens,
        )

    def move_off(self, recorded_index: int, monitors: list[winapi.Monitor]) -> bool:
        """Step onto a screen that is not being recorded. True if it managed to.

        False means there is nowhere to go - one screen, or all of them in the shot - and
        the caller then knows the bar will be in the picture and can go compact instead.
        """
        here = monitor_for(monitors, self.x(), self.y(), self.width(), self.height())
        if here is not None and here.index != recorded_index:
            return True
        spare = next((m for m in monitors if m.index != recorded_index), None)
        if spare is None:
            return False
        self.place(
            spare.x + spare.width - self.width() - MARGIN,
            spare.y + spare.height - self.height() - MARGIN * 3,
            monitors,
        )
        return True

    # ------------------------------------------------------------------- input

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._press_global = event.globalPosition().toPoint()
        self._drag_from = self._press_global - self.frameGeometry().topLeft()
        self._dragged = False
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_from is None:
            return
        here = event.globalPosition().toPoint()
        target = here - self._drag_from
        # Measured from the *press*, not from the last move. A real mouse at 500-1000 Hz
        # delivers 1-2 px deltas, so comparing against `self.pos()` never clears the slop
        # on an ordinary slow drag: the bar travels the whole way and `moved` never
        # fires, so the new position is silently not saved. Measuring from the press also
        # kills the opposite false positive, where a wiggle inside the snap zone looks
        # like a jump because `self.pos()` has been pulled to the snapped edge.
        if self._press_global is not None and (
            (here - self._press_global).manhattanLength() > DRAG_SLOP
        ):
            self._dragged = True
        monitors = winapi.list_monitors()
        if monitors:
            self._monitors = monitors
            self._placed = True
        x, y = snap_to_edges(
            target.x(), target.y(), self.width(), self.height(), monitors
        )
        # Snap pulls towards an edge; it does not stop a drag from going past one.
        # Without the clamp the bar can be dropped almost entirely off the desktop -
        # measured at 3980,2535 on a 4000x2560 desktop, leaving a 20x25 sliver and no
        # way to grab it back.
        x, y = clamp_to_monitors(x, y, self.width(), self.height(), monitors)
        self.move(x, y)
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        dragged = self._dragged
        self._drag_from = None
        self._press_global = None
        self._dragged = False
        if dragged:
            self.moved.emit(self.x(), self.y())
        # A press that did not drag is a click, and a click on the body of the bar is
        # not a command: every control is a child widget and got the event first.
        event.accept()

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(theme.style_sheet())
        if self._recording:
            menu.addAction(_("Stop recording"), self.stop_requested.emit)
            menu.addAction(_("Stop and discard"), self.discard_requested.emit)
        else:
            menu.addAction(_("Start recording"), self.record_requested.emit)
            menu.addAction(_("Choose what to record…"), self.source_clicked.emit)
        menu.addSeparator()
        open_folder = menu.addAction(
            _("Open the output folder"), self.folder_clicked.emit
        )
        open_folder.setEnabled(bool(self._folder))
        last = menu.addAction(
            _("Watch the last recording"), self.last_file_clicked.emit
        )
        last.setEnabled(bool(self._last_file))
        menu.addSeparator()
        menu.addAction(_("Settings…"), self.settings_clicked.emit)
        menu.addSeparator()
        menu.addAction(_("Quit ScreenRec"), self.quit_requested.emit)
        menu.exec(event.globalPos())
