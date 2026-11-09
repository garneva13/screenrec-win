"""The pieces the settings window is built from, kept apart from the window itself.

`settings_panel.py` is long enough with the settings in it. Everything here is a widget
with no opinion about `config.Settings`: it paints, it reports what the user did, and
the panel decides what that means. That split is also what lets each one be tested on
its own - the camera diagram needs no camera, and the hotkey box needs no recorder.

Five of them:

* `Switch` - the toggle used all over the window. It was in `settings_panel` until the
  window was rebuilt on 2026-11-08; it is re-exported from there so nothing that
  already imports it has to change.
* `NavButton` - one row of the sidebar. Auto-exclusive, so Qt gives the arrow keys for
  free: `QAbstractButton` moves the checked state between auto-exclusive siblings.
* `InfoButton` - the small (i). A tool tip that only appears under the pointer is
  invisible to anyone using the keyboard, so this one shows the same text on a press
  and on Space or Enter.
* `HotkeyEdit` - reads a real key press instead of asking somebody to type the words
  "Ctrl+Alt+R". It is a box that goes into a waiting state, and **the window has to
  suspend the global hotkeys first**: a combination that is registered with
  `RegisterHotKey` is swallowed by Windows and never reaches this widget at all, so
  without that the box could not read back the shortcut it is supposed to be editing -
  and the recording would start while you edited the shortcut for starting it.
* `CameraDiagram` - a drawn screen with the camera block in it. Click a quarter or use
  the arrow keys. It paints from three strings and never opens a camera: a settings
  window that turned the webcam light on just by being opened would be worse than no
  preview at all.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import config as config_mod
from .. import hotkeys as hotkeys_mod
from ..i18n import _
from . import icons, surfaces, theme

__all__ = [
    "Switch",
    "NavButton",
    "InfoButton",
    "HotkeyEdit",
    "CameraDiagram",
    "FieldGrid",
    "hairline",
]


class Switch(QtWidgets.QAbstractButton):
    """A real toggle switch: a painted pill with a knob that travels across it.

    A check box says "this is a form". A switch says "this is on or off right now", which
    is what every one of these settings is. It is a `QAbstractButton` with
    ``setCheckable(True)``, so `toggled` arrives for free and the keyboard works the way
    Qt users expect - Space toggles it, Tab reaches it.

    The travel is animated over `DURATION_MS` by one `QVariantAnimation` held on the
    instance. Holding it matters: an animation that is only referenced by the call that
    started it is collected mid-flight and the knob stops halfway.
    """

    WIDTH = 38
    HEIGHT = 22
    KNOB = 16
    DURATION_MS = 120

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

        #: 0.0 = knob fully left (off), 1.0 = knob fully right (on).
        self._travel = 1.0 if self.isChecked() else 0.0
        self._animation = QtCore.QVariantAnimation(self)
        self._animation.setDuration(self.DURATION_MS)
        self._animation.setEasingCurve(QtCore.QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._on_travel)
        self.toggled.connect(self._on_toggled)

    # ------------------------------------------------------------- animation

    def _on_toggled(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(float(self._travel))
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _on_travel(self, value: object) -> None:
        try:
            self._travel = max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # QVariantAnimation interpolates floats here; anything else is a programming
            # error, and snapping to the settled state beats painting a broken knob.
            self._travel = 1.0 if self.isChecked() else 0.0
        self.update()

    def set_checked_now(self, checked: bool) -> None:
        """Set the state with no animation - for loading settings into the panel."""
        self._animation.stop()
        was_blocked = self.blockSignals(True)
        try:
            self.setChecked(checked)
        finally:
            self.blockSignals(was_blocked)
        self._travel = 1.0 if checked else 0.0
        self.update()

    def travel(self) -> float:
        """Where the knob is, 0..1. Exposed so a test can watch the animation."""
        return self._travel

    # ---------------------------------------------------------------- paint

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.WIDTH, self.HEIGHT)

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
        radius = self.height() / 2.0
        track = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        if not enabled:
            # Dim: the track keeps its shape so the state is still readable, but neither
            # the accent nor the hairline is at full strength.
            fill = colours["line"] if self.isChecked() else colours["panel"]
            edge = colours["line"]
            knob_ink = colours["disabled"]
        elif self.isChecked():
            fill = colours["accent_hover"] if hovered else colours["accent"]
            edge = fill
            knob_ink = colours["text"]
        else:
            fill = colours["hover"] if hovered else colours["line"]
            edge = colours["line_strong"] if hovered else colours["line"]
            knob_ink = colours["text_soft"]

        painter.setPen(QtGui.QPen(QtGui.QColor(edge), 1))
        painter.setBrush(QtGui.QColor(fill))
        painter.drawRoundedRect(track, radius, radius)

        margin = (self.height() - self.KNOB) / 2.0
        span = self.width() - self.KNOB - 2 * margin
        centre = QtCore.QPointF(
            margin + self.KNOB / 2.0 + span * self._travel, self.height() / 2.0
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(knob_ink))
        painter.drawEllipse(centre, self.KNOB / 2.0, self.KNOB / 2.0)

        # On is not only violet: the knob is at the right-hand end and there is a tick
        # inside it. Colour alone would leave the two states identical to anybody who
        # cannot tell violet from grey.
        if self.isChecked():
            icons.draw(
                painter,
                "check",
                QtCore.QRectF(
                    centre.x() - self.KNOB / 2.0,
                    centre.y() - self.KNOB / 2.0,
                    self.KNOB,
                    self.KNOB,
                ),
                colours["accent"] if enabled else colours["panel"],
                weight=2.4,
            )

        if self.hasFocus() and enabled:
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["accent_light"]), 1))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                track.adjusted(-1.5, -1.5, 1.5, 1.5), radius + 1.5, radius + 1.5
            )

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class NavButton(QtWidgets.QAbstractButton):
    """One row of the sidebar: an outline icon, a word, and a state worth seeing.

    `setAutoExclusive(True)` does two jobs. Qt unchecks the others when this one is
    checked, and `QAbstractButton::keyPressEvent` moves the checked state between
    auto-exclusive siblings on the arrow keys - which is the whole of the keyboard
    support the sidebar needs, and none of it is written here.
    """

    HEIGHT = 40
    ICON = 18

    def __init__(
        self, kind: str, text: str, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.setMinimumHeight(self.HEIGHT)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

    def setText(self, text: str) -> None:  # noqa: N802 - Qt's spelling
        super().setText(text)
        # Screen readers announce this, and the icon is decoration beside it.
        self.setAccessibleName(text)
        self.update()

    def sizeHint(self) -> QtCore.QSize:
        metrics = QtGui.QFontMetrics(self.font())
        return QtCore.QSize(
            self.ICON + 12 + metrics.horizontalAdvance(self.text()) + 28, self.HEIGHT
        )

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
        radius = theme.RADIUS["small"]
        hovered = self.underMouse() and self.isEnabled()

        if self.isChecked():
            fill = QtGui.QColor(colours["accent"])
            fill.setAlpha(38)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(box, radius, radius)
            ink = colours["accent_light"]
        elif hovered:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(colours["hover"]))
            painter.drawRoundedRect(box, radius, radius)
            ink = colours["text"]
        else:
            ink = colours["text_soft"]
        if not self.isEnabled():
            ink = colours["disabled"]

        icon_box = QtCore.QRectF(12, (self.height() - self.ICON) / 2.0,
                                 self.ICON, self.ICON)
        icons.draw(painter, self.kind, icon_box, ink, weight=1.7)

        painter.setPen(QtGui.QColor(ink))
        font = QtGui.QFont(self.font())
        font.setPointSizeF(9.8)
        if self.isChecked():
            font.setWeight(QtGui.QFont.Weight.DemiBold)
        painter.setFont(font)
        text_box = QtCore.QRectF(
            icon_box.right() + 10, 0, self.width() - icon_box.right() - 18, self.height()
        )
        painter.drawText(
            text_box,
            int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
            self.text(),
        )

        if self.hasFocus():
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["accent_light"]), 1))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, radius, radius)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class InfoButton(QtWidgets.QAbstractButton):
    """A small (i) that shows its sentence to the mouse **and** to the keyboard.

    `setToolTip` alone reaches only the pointer. This one takes focus, answers Space and
    Enter, and puts the same text on screen with `QToolTip.showText`, so the explanation
    is not a mouse-only feature.
    """

    SIZE = 18

    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.set_text(text)
        self.clicked.connect(self.show_text)

    def set_text(self, text: str) -> None:
        self._text = text
        self.setToolTip(text)
        self.setAccessibleName(_("What this means"))
        self.setAccessibleDescription(text)

    def text_shown(self) -> str:
        """What this button explains. Named so it cannot collide with `QAbstractButton.text`."""
        return self._text

    def show_text(self) -> None:
        if self._text:
            QtWidgets.QToolTip.showText(
                self.mapToGlobal(QtCore.QPoint(0, self.height())), self._text, self
            )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            colours = theme.COLOURS
            ink = colours["text_dim"]
            if self.hasFocus() or (self.underMouse() and self.isEnabled()):
                ink = colours["accent_light"]
            if not self.isEnabled():
                ink = colours["disabled"]
            icons.draw(
                painter,
                "help",
                QtCore.QRectF(0, 0, self.width(), self.height()),
                ink,
                weight=1.6,
            )
        finally:
            painter.end()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class HotkeyEdit(QtWidgets.QLineEdit):
    """A box that reads a real key press instead of being typed into.

    Read-only until it is armed: a click, Space or Enter puts it into a waiting state,
    and the next complete combination is taken. Escape leaves it as it was.

    **`capturing` has to be answered before this can work.** While a shortcut is
    registered with Windows through `RegisterHotKey`, Windows takes it and no
    application sees the keys - so editing Ctrl+Alt+R would start a recording and the
    box would read nothing. The panel passes this signal to the application, which
    stops the listeners while a box is armed and starts them again afterwards.
    """

    #: True when this box has started waiting for a key, False when it has stopped.
    capturing = QtCore.Signal(bool)
    #: A finished shortcut, in `hotkeys.format_hotkey`'s spelling.
    captured = QtCore.Signal(str)

    #: Keys that are only ever part of a combination, never the whole of one.
    _MODIFIER_KEYS = {
        QtCore.Qt.Key.Key_Control, QtCore.Qt.Key.Key_Shift, QtCore.Qt.Key.Key_Alt,
        QtCore.Qt.Key.Key_Meta, QtCore.Qt.Key.Key_AltGr, QtCore.Qt.Key.Key_Super_L,
        QtCore.Qt.Key.Key_Super_R,
    }

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self._armed = False
        self._before = ""

    def is_armed(self) -> bool:
        return self._armed

    def arm(self) -> None:
        """Start waiting for a key. Says so, and tells the panel to stand the hotkeys down."""
        if self._armed:
            return
        self._armed = True
        self._before = self.text()
        self.setText("")
        self.setPlaceholderText(_("Press a shortcut, Esc to cancel"))
        self.setProperty("armed", True)
        self._restyle()
        self.capturing.emit(True)

    def disarm(self, restore: bool = True) -> None:
        if not self._armed:
            return
        self._armed = False
        if restore:
            self.setText(self._before)
        self.setPlaceholderText("")
        self.setProperty("armed", False)
        self._restyle()
        self.capturing.emit(False)

    def _restyle(self) -> None:
        """Re-ask the style sheet, which keys off the `armed` property."""
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()

    # ------------------------------------------------------------------ events

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and not self._armed:
            self.arm()
            event.accept()
            return
        super().mousePressEvent(event)

    def focusOutEvent(self, event: QtGui.QFocusEvent) -> None:
        # Clicking elsewhere mid-capture is a cancel, not a half-read shortcut.
        self.disarm()
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = QtCore.Qt.Key(event.key())
        if not self._armed:
            if key in (QtCore.Qt.Key.Key_Space, QtCore.Qt.Key.Key_Return,
                       QtCore.Qt.Key.Key_Enter):
                self.arm()
                event.accept()
                return
            super().keyPressEvent(event)
            return

        if key == QtCore.Qt.Key.Key_Escape:
            self.disarm()
            event.accept()
            return
        if key in self._MODIFIER_KEYS:
            # Still waiting for the key the modifiers belong to.
            event.accept()
            return

        text = self._spell(event)
        if not text:
            event.accept()
            return
        parsed = hotkeys_mod.parse_hotkey(text)
        if parsed is None:
            event.accept()
            return
        canonical = hotkeys_mod.format_hotkey(*parsed)
        self._armed = False
        self.setPlaceholderText("")
        self.setProperty("armed", False)
        self._restyle()
        self.setText(canonical)
        self.capturing.emit(False)
        self.captured.emit(canonical)
        event.accept()

    def _spell(self, event: QtGui.QKeyEvent) -> str:
        """The press as `hotkeys.parse_hotkey` spells it, or "" for an unusable one."""
        modifiers = event.modifiers()
        parts: list[str] = []
        if modifiers & QtCore.Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if modifiers & QtCore.Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if modifiers & QtCore.Qt.KeyboardModifier.MetaModifier:
            parts.append("Win")
        if not parts:
            # `hotkeys.parse_hotkey` refuses a bare key, and so should the box: a
            # shortcut with no modifier would fire while somebody was typing.
            return ""
        name = QtGui.QKeySequence(event.key()).toString()
        if not name:
            return ""
        parts.append(name)
        return "+".join(parts)


class FieldGrid(QtWidgets.QWidget):
    """Two columns of fields that become one when there is not room for two.

    The window has a fixed width, so this is not for a user dragging an edge - it is
    for a long translation and for Windows at 150% and 200%, where the same two
    columns are the same number of logical pixels but the text inside them is not.
    """

    #: Below this, two columns would each be narrower than a combo box's own minimum.
    NARROW = 430

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("formRow")
        self._fields: list[QtWidgets.QWidget] = []
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(14)
        self._columns = 2

    def add(self, field: QtWidgets.QWidget) -> None:
        self._fields.append(field)
        self._relayout(self._columns)

    def columns(self) -> int:
        return self._columns

    def _relayout(self, columns: int) -> None:
        for field in self._fields:
            self._grid.removeWidget(field)
        for index, field in enumerate(self._fields):
            self._grid.addWidget(field, index // columns, index % columns)
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self._columns = columns

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        wanted = 1 if self.width() < self.NARROW else 2
        if wanted != self._columns:
            self._relayout(wanted)
        super().resizeEvent(event)


class CameraDiagram(QtWidgets.QWidget):
    """A drawn screen with the camera block on it: position, size and shape at a glance.

    Nothing here opens a camera. The diagram is three strings - corner, size, shape -
    and a rectangle, which is the whole point: a settings window that lit the webcam up
    just because somebody opened it would be a worse thing than having no preview.

    The size is `config.WEBCAM_SIZE_FRACTIONS`, the same fractions `webcam.WebcamOverlay`
    lays the real picture out with, so the diagram cannot quietly drift from the thing
    it describes.

    A click picks the nearest corner; so do the arrow keys, which is why it takes focus.
    """

    #: Emitted with a `config.WEBCAM_CORNERS` value when the user picks one here.
    cornerPicked = QtCore.Signal(str)

    HEIGHT = 220

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("formRow")
        self.setMinimumHeight(self.HEIGHT)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._corner = config_mod.DEFAULT_WEBCAM_CORNER
        self._size = config_mod.DEFAULT_WEBCAM_SIZE
        self._shape = config_mod.DEFAULT_WEBCAM_SHAPE
        self.setAccessibleName(_("Where the camera sits"))

    def set_placement(self, corner: str, size: str, shape: str) -> None:
        self._corner = corner if corner in config_mod.WEBCAM_CORNERS else self._corner
        self._size = size if size in config_mod.WEBCAM_SIZES else self._size
        self._shape = shape if shape in config_mod.WEBCAM_SHAPES else self._shape
        self.update()

    def placement(self) -> tuple[str, str, str]:
        return self._corner, self._size, self._shape

    # ------------------------------------------------------------------ geometry

    def screen_rect(self) -> QtCore.QRectF:
        """The 16:9 screen inside this widget, centred."""
        margin = 6.0
        box = QtCore.QRectF(self.rect()).adjusted(margin, margin, -margin, -margin)
        height = min(box.height(), box.width() * 9.0 / 16.0)
        width = height * 16.0 / 9.0
        return QtCore.QRectF(
            box.center().x() - width / 2.0,
            box.center().y() - height / 2.0,
            width,
            height,
        )

    def camera_rect(self) -> QtCore.QRectF:
        """Where the camera block lands, by the same rules the real overlay uses."""
        screen = self.screen_rect()
        fraction = config_mod.WEBCAM_SIZE_FRACTIONS.get(
            self._size, config_mod.WEBCAM_SIZE_FRACTIONS[config_mod.DEFAULT_WEBCAM_SIZE]
        )
        width = screen.width() * fraction
        height = width if self._shape == "circle" else width * 9.0 / 16.0
        inset = screen.width() * 0.035
        left = (
            screen.left() + inset
            if self._corner.endswith("left")
            else screen.right() - inset - width
        )
        top = (
            screen.top() + inset
            if self._corner.startswith("top")
            else screen.bottom() - inset - height
        )
        return QtCore.QRectF(left, top, width, height)

    def corner_at(self, point: QtCore.QPointF) -> str:
        centre = self.screen_rect().center()
        vertical = "top" if point.y() < centre.y() else "bottom"
        horizontal = "left" if point.x() < centre.x() else "right"
        return f"{vertical}_{horizontal}"

    # -------------------------------------------------------------------- input

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
        self._pick(self.corner_at(event.position()))
        event.accept()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = QtCore.Qt.Key(event.key())
        vertical, horizontal = self._corner.split("_")
        if key == QtCore.Qt.Key.Key_Up:
            vertical = "top"
        elif key == QtCore.Qt.Key.Key_Down:
            vertical = "bottom"
        elif key == QtCore.Qt.Key.Key_Left:
            horizontal = "left"
        elif key == QtCore.Qt.Key.Key_Right:
            horizontal = "right"
        else:
            super().keyPressEvent(event)
            return
        self._pick(f"{vertical}_{horizontal}")
        event.accept()

    def _pick(self, corner: str) -> None:
        if corner not in config_mod.WEBCAM_CORNERS or corner == self._corner:
            return
        self._corner = corner
        self.update()
        self.cornerPicked.emit(corner)

    # -------------------------------------------------------------------- paint

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        screen = self.screen_rect()
        radius = theme.RADIUS["small"]

        painter.setPen(QtGui.QPen(QtGui.QColor(colours["line"]), 1))
        painter.setBrush(QtGui.QColor(colours["bg"]))
        painter.drawRoundedRect(screen, radius, radius)

        self._paint_desktop(painter, screen)

        box = self.camera_rect()
        painter.setBrush(QtGui.QColor(colours["panel_raised"]))
        painter.setPen(QtGui.QPen(QtGui.QColor(colours["accent"]), 1.6))
        if self._shape == "circle":
            painter.drawEllipse(box)
        elif self._shape == "rectangle":
            painter.drawRect(box)
        else:
            corner = box.width() * 0.16
            painter.drawRoundedRect(box, corner, corner)

        head = min(box.width(), box.height()) * 0.52
        icons.draw(
            painter,
            "camera",
            QtCore.QRectF(
                box.center().x() - head / 2.0, box.center().y() - head / 2.0, head, head
            ),
            colours["text_dim"],
            weight=1.6,
        )

        if self.hasFocus():
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["accent_light"]), 1))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                screen.adjusted(-2, -2, 2, 2), radius + 2, radius + 2
            )

    def _paint_desktop(self, painter: QtGui.QPainter, screen: QtCore.QRectF) -> None:
        """A few grey shapes so the screen reads as a screen and not as a blank box."""
        colours = theme.COLOURS
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(colours["line"]))
        unit = screen.width() / 100.0

        for index in range(3):  # icons down the left edge
            painter.drawRoundedRect(
                QtCore.QRectF(
                    screen.left() + 4 * unit,
                    screen.top() + (6 + index * 9) * unit,
                    5 * unit,
                    5 * unit,
                ),
                unit, unit,
            )
        window = QtCore.QRectF(
            screen.left() + 16 * unit, screen.top() + 8 * unit,
            62 * unit, 38 * unit,
        )
        painter.setBrush(QtGui.QColor(colours["panel"]))
        painter.drawRoundedRect(window, unit * 1.5, unit * 1.5)
        painter.setBrush(QtGui.QColor(colours["line"]))
        painter.drawRoundedRect(
            QtCore.QRectF(window.left() + 4 * unit, window.top() + 10 * unit,
                          22 * unit, 22 * unit),
            unit, unit,
        )
        for index in range(3):  # lines of "text" beside it
            painter.drawRoundedRect(
                QtCore.QRectF(
                    window.left() + 31 * unit, window.top() + (11 + index * 7) * unit,
                    26 * unit, 3 * unit,
                ),
                unit * 0.8, unit * 0.8,
            )
        painter.drawRoundedRect(  # a task bar
            QtCore.QRectF(screen.left(), screen.bottom() - 7 * unit,
                          screen.width(), 7 * unit),
            unit, unit,
        )


def hairline(parent: QtWidgets.QWidget | None = None) -> QtWidgets.QFrame:
    """One pixel of `line`, for the edges between the window's three bands."""
    rule = QtWidgets.QFrame(parent)
    rule.setFixedHeight(1)
    rule.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    rule.setStyleSheet(f"background: {theme.COLOURS['line']};")
    return rule


def caption(text: str, role: str = "text_dim", size: float = 9.0) -> QtWidgets.QLabel:
    """The small label above a field."""
    return surfaces.label(text, role, size)
