"""The in-place text editor: type a note where you clicked.

This is the one annotation surface that is **chrome**, not drawing. While the user is
typing there is a caret blinking, a box round the text and a faint backing plate behind
it - none of which belongs in the video. So this widget is marked
``WDA_EXCLUDEFROMCAPTURE`` (`ui.surfaces.exclude_from_capture`), which keeps it on the
screen and takes it out of the capture; measured on this machine against the real capture
path, an excluded panel contributed zero pixels to a ddagrab frame. The canvas is the
opposite case and is deliberately *not* excluded: the committed text becomes a
`model.Item` that the canvas paints, and the canvas is the surface that must be recorded.
So the user sees an editing box that the recording never does, and the recording gets the
finished text with nothing around it.

**Russian must work.** Not one character is assembled from key codes here. The text is
held by a `QTextEdit`, which receives it through Qt's own input path - ``event.text()``
for a plain keystroke, ``QInputMethodEvent`` for anything composed - so a Cyrillic,
Greek or CJK layout arrives exactly as typed. `_Editor.keyPressEvent` looks at exactly
two keys, Return and Escape, which Qt reports as ``Key_Return``/``Key_Escape`` whatever
the layout, and hands every other event straight to `QTextEdit` untouched.

**Coordinates.** `open_at` takes a point in *physical desktop pixels*, the space
`winapi.list_monitors()` reports and the space `model` stores. Qt's geometry is in
logical units, which are the same number only while every screen is at 100%. The
conversion is done once, in `_place`, and the physical rectangle the box ended up at is
kept in `desktop_rect` and `anchor` so no caller has to convert it back. Monitors can
start at a negative x or y - a screen to the left of the primary one - so nothing here
assumes an origin at (0, 0); the box is clamped into the monitor it was opened on, which
is how a click two pixels from the right edge still gets a whole editor.

*The scaled branch is untested*: both screens on this machine run at 100%, where the
conversion collapses to the identity. It follows the mapping `ui.captureframe` already
uses, and the pairing of a DXGI output with a `QScreen` is the same three steps for the
same reason - matching by name does not work here, because `QScreen.name()` gives the
monitor's friendly name while `winapi` gives ``\\\\.\\DISPLAY2``.

**This surface takes focus, and it is the only one that does.** The house rule is that
nothing pulls the user out of what they are demonstrating, and every other floating
surface carries ``WS_EX_NOACTIVATE``. A text editor that cannot be typed into is not a
text editor, so this window activates on purpose and gives the focus back by closing.
"""
from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n, winapi
from ..i18n import _
from ..ui import surfaces, theme
from . import model

__all__ = ["TextBox", "PAD_X", "PAD_Y", "CARET_ROOM", "backing_colour"]

# The reasons the box can give for not working properly, as English templates rather
# than finished sentences. A reason is kept with the fields it was raised with and the
# sentence is built when it is read, so a reason collected before the language was
# switched is still read in the language in force now - a message about the recording
# is exactly the kind of thing the user goes back and re-reads.

MONITORS_UNREADABLE = (
    "The monitor list could not be read ({error}), so the text box cannot be kept on "
    "screen and will open wherever it was asked to."
)
MONITORS_UNREADABLE_AGAIN = "The monitor list could not be re-read: {error}"
MONITORS_EMPTY = (
    "The monitor list is empty, so the text box cannot be kept on screen and will open "
    "wherever it was asked to."
)
NO_APPLICATION = (
    "There is no QApplication, so a click outside the text box cannot be noticed and "
    "will not commit it."
)
NO_CAPTURE_EXCLUSION = (
    "This build of Windows does not support WDA_EXCLUDEFROMCAPTURE (it needs Windows 10 "
    "2004 or newer), so the text box and its caret WILL appear in the recording while "
    "the note is being typed."
)
BAD_COLOUR = (
    "{colour} is not a colour the text box understands; the note is being shown in "
    "white."
)
UNPAIRED_MONITOR = (
    "Monitor {index} could not be paired with a Qt screen, so the text box is placed in "
    "physical pixels and will be misplaced on a scaled desktop."
)

#: Space between the box's edge and the text, in *logical* units. The horizontal one is
#: also the offset from the box's left edge to the text anchor, so it is what `open_at`
#: subtracts to turn an anchor into a window position.
PAD_X = 8
PAD_Y = 4

#: A little room past the last glyph so the caret at the end of a line is not clipped.
CARET_ROOM = 3.0

#: How opaque the backing plate is. Solid would hide what is being annotated; any less
#: and a busy wallpaper shows through the text.
BACKING_ALPHA = 180
#: The box's hairline is the ink colour, faded - it ties the editor to the colour the
#: text will be drawn in without adding a second accent to the screen.
BORDER_ALPHA = 130


# --------------------------------------------------------------------- colour


def _relative_luminance(colour: QtGui.QColor) -> float:
    """WCAG 2.1 relative luminance of an sRGB colour, by the formula."""

    def channel(value: int) -> float:
        srgb = value / 255.0
        if srgb <= 0.04045:
            return srgb / 12.92
        return ((srgb + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(colour.red())
        + 0.7152 * channel(colour.green())
        + 0.0722 * channel(colour.blue())
    )


def _contrast(first: QtGui.QColor, second: QtGui.QColor) -> float:
    light, dark = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


def backing_colour(ink: QtGui.QColor) -> QtGui.QColor:
    """The plate to put behind `ink` so it is legible on any wallpaper.

    Two candidates, both already in the palette: the sunken surface (``bg``) and the
    brightest text colour used as a surface. The one with the better contrast ratio
    against the ink wins - computed, not guessed, because the pen palette runs from
    ``#FFFFFF`` to ``#111111`` and a single dark plate would lose the black pen. The
    chosen plate is then made translucent, so the measured ratio is an upper bound and
    `BACKING_ALPHA` is high enough to keep most of it.
    """
    dark = QtGui.QColor(theme.COLOURS["bg"])
    light = QtGui.QColor(theme.COLOURS["text"])
    plate = dark if _contrast(ink, dark) >= _contrast(ink, light) else light
    plate.setAlpha(BACKING_ALPHA)
    return plate


# --------------------------------------------------------------------- editor


class _Editor(QtWidgets.QTextEdit):
    """The text field itself. Enter commits, Shift+Enter is a new line, Esc cancels.

    `QTextEdit` is what makes the alphabet a non-issue: it owns the document, the caret
    and the input-method state, so a keystroke in any layout and a composed character
    both arrive by Qt's own path. Nothing here reads a key code to build a string.
    """

    commit_requested = QtCore.Signal()
    cancel_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setAcceptRichText(False)  # pasted formatting would not survive the item
        self.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTabChangesFocus(False)
        # A QAbstractScrollArea asks for room for a frame and two scroll bars it is not
        # going to have. Measured: that floor made the window 84x76 where the text needs
        # 40x36, so the box stood well clear of the text and the backing plate was twice
        # the size of the note. The size here is computed from the font metrics instead.
        self.setMinimumSize(0, 0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored
        )
        # The context menu is a separate top-level window with its own HWND, which no
        # display affinity of ours covers - it would appear in the recording. Ctrl+C,
        # Ctrl+V and Ctrl+Z all still work from the keyboard.
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        self.document().setDocumentMargin(0.0)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                # A new line in the note, not the end of it.
                super().keyPressEvent(event)
            else:
                self.commit_requested.emit()
            return
        if key == QtCore.Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            return
        # Everything else - every character of every alphabet, every shortcut - belongs
        # to QTextEdit. Deciding here what a key "is" is exactly how a text tool ends up
        # dropping Cyrillic.
        super().keyPressEvent(event)


# -------------------------------------------------------------------- the box


class TextBox(QtWidgets.QWidget):
    """An editor that opens at a desktop point, in the colour and size of the text.

    `committed` carries the text as typed. It is never emitted with an empty string:
    committing nothing is a cancel, so `cancelled` is emitted instead and no item is
    made. `cancelled` also covers Esc.
    """

    committed = QtCore.Signal(str)
    cancelled = QtCore.Signal()

    def __init__(
        self,
        settings: model.ToolSettings,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("ScreenRec - annotation text"))
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # No WA_ShowWithoutActivating and no WS_EX_NOACTIVATE here, unlike every other
        # floating surface: this one has to receive the keyboard. See the module note.
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        self.settings = settings

        #: Empty while everything is fine; otherwise what went wrong, in words the
        #: controller can show. Never a silent failure. Written only through `_report`,
        #: which collects every reason instead of letting the last one overwrite the
        #: rest - a bad colour and a missing monitor list are two separate problems and
        #: the controller has to hear about both.
        self.problem = ""
        #: Every reason as (English template, fields) rather than a finished
        #: sentence, so `retranslate` can write them all out again in another
        #: language.
        self._problems: list[tuple[str, dict[str, str]]] = []
        #: Why the monitor list is unusable, if it is. Unlike `problem` this one
        #: **survives an `open_at`**: a DXGI enumeration that failed when the box was
        #: built is still failing when it opens, and blanking the message on every open
        #: hid it exactly when the box could not be clamped onto a screen.
        self.monitor_problem = ""
        self._monitor_problem: tuple[str, dict[str, str]] | None = None
        #: Whether this window really is invisible to a screen capture. False on Windows
        #: 10 before 2004 and under a non-Windows platform plugin - the caller must be
        #: able to say so rather than record its own caret.
        self.excluded_from_capture = False

        #: The point `open_at` was given, in physical desktop pixels.
        self.requested_anchor = QtCore.QPoint()
        #: Where the text actually starts, in physical desktop pixels, after the box was
        #: clamped onto the screen. The top-left of the first line, which is the anchor
        #: `model.Item.bounds` expects for a text item.
        self.anchor = QtCore.QPoint()
        #: The whole box, in physical desktop pixels. `geometry()` is logical units.
        self.desktop_rect = QtCore.QRect()

        try:
            self.monitors = winapi.list_monitors()
        except OSError as exc:  # no DXGI: clamping has nothing to clamp to
            self.monitors = []
            self._set_monitor_problem(MONITORS_UNREADABLE, error=str(exc))
            self._report(MONITORS_UNREADABLE, error=str(exc))

        self._finished = True
        self._filtering = False
        #: Replaced by `_apply_settings` before anything measures with it; set here so
        #: the attribute exists for the whole life of the object.
        self._text_font = QtGui.QFont(self.font())

        self._editor = _Editor(self)
        self._editor.commit_requested.connect(self.commit)
        self._editor.cancel_requested.connect(self.cancel)
        self._editor.textChanged.connect(self._on_text_changed)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAD_X, PAD_Y, PAD_X, PAD_Y)
        layout.setSpacing(0)
        layout.addWidget(self._editor)
        layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
        self.setMinimumSize(0, 0)

        self._apply_settings()
        i18n.notifier.changed.connect(self.retranslate)

    # ------------------------------------------------------------------ API

    def retranslate(self) -> None:
        """Re-read every string this box owns, the reasons among them.

        There is no label of its own to rewrite - the box is a frame round the user's
        own text - so this is the window title and `problem`, which the controller may
        be showing at this moment.
        """
        self.setWindowTitle(_("ScreenRec - annotation text"))
        self._rebuild_problem()
        if self._monitor_problem is not None:
            template, fields = self._monitor_problem
            self.monitor_problem = _(template).format(**fields)

    def open_at(self, x: int, y: int) -> None:
        """Open an empty editor whose first line starts at the desktop pixel (x, y)."""
        # Last time's problems are gone, but a missing monitor list is not a problem of
        # "last time": it is the state the box is opening in, and it is reported again.
        self._problems = []
        self.problem = ""
        self._report_monitor_reason()
        self._finished = False
        self.requested_anchor = QtCore.QPoint(int(x), int(y))

        self._editor.blockSignals(True)
        try:
            self._editor.clear()
        finally:
            self._editor.blockSignals(False)

        self._apply_settings()
        self._place()

        self.show()
        self.raise_()
        self.activateWindow()
        self._editor.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

        app = QtWidgets.QApplication.instance()
        if app is None:
            self._report(NO_APPLICATION)
        elif not self._filtering:
            # A press anywhere else in this process - the canvas, the toolbar - commits.
            # A press in another application is not a Qt event at all and is caught by
            # WindowDeactivate in `event` instead.
            app.installEventFilter(self)
            self._filtering = True

    def text(self) -> str:
        """What is in the editor right now."""
        return self._editor.toPlainText()

    def text_font(self) -> QtGui.QFont:
        """The font the note is shown in - `font_size` converted to logical pixels.

        Read this rather than `QWidget.font()`: the application style sheet overwrites a
        widget's font, so the widget's own font is not a safe answer. See
        `_apply_settings`.
        """
        return QtGui.QFont(self._text_font)

    def set_settings(self, settings: model.ToolSettings) -> None:
        """Use a different colour or font size from the next `open_at` onwards."""
        self.settings = settings
        self._apply_settings()
        if self.isVisible():
            self._place()
            self.update()

    def commit(self) -> None:
        """Finish. Emits `committed` with the text, or `cancelled` if there is none."""
        if self._finished:
            return
        self._finished = True
        text = self._editor.toPlainText()
        self._close()
        if not text.strip():
            # An empty box is somebody who changed their mind, not an empty item: an item
            # with no text draws nothing and can still be hit by the eraser.
            self.cancelled.emit()
            return
        self.committed.emit(text)

    def cancel(self) -> None:
        """Throw the text away and emit `cancelled`."""
        if self._finished:
            return
        self._finished = True
        self._close()
        self.cancelled.emit()

    def refresh_monitors(self) -> None:
        """Re-read the monitor list after the desktop layout changed."""
        try:
            self.monitors = winapi.list_monitors()
        except OSError as exc:
            self._set_monitor_problem(MONITORS_UNREADABLE_AGAIN, error=str(exc))
            self._report(MONITORS_UNREADABLE_AGAIN, error=str(exc))
        else:
            # It reads now, so the old reason is no longer the truth about this box.
            self.monitor_problem = ""
            self._monitor_problem = None

    # --------------------------------------------------------------- problems

    def _report(self, template: str, **fields: str) -> None:
        """Add a reason to `problem`, keeping the ones already there.

        Collected rather than assigned because the reasons are independent - a colour
        the box cannot parse says nothing about whether the monitor list could be read -
        and because the last writer used to win: `_apply_settings` and `_place` both run
        on every open and every keystroke, so a single assignment anywhere wiped the
        rest. Repeats are dropped, which matters because `_ink` is called again from
        every `paintEvent` and would otherwise grow the string without end. A repeat
        is recognised by template *and* fields, so two monitors that cannot be paired
        are two reasons while one of them reported twice is one.
        """
        if not template or (template, fields) in self._problems:
            return
        self._problems.append((template, fields))
        self._rebuild_problem()

    def _rebuild_problem(self) -> None:
        """Write `problem` out from the reasons, in the language in force now."""
        self.problem = "  ".join(
            _(template).format(**fields) for template, fields in self._problems
        )

    def _set_monitor_problem(self, template: str, **fields: str) -> None:
        """Remember why the monitor list is unusable, and say it in words."""
        self._monitor_problem = (template, fields)
        self.monitor_problem = _(template).format(**fields)

    def _report_monitor_reason(self) -> None:
        """Say why the box cannot be clamped, when it cannot be."""
        if self.monitors:
            return
        if self._monitor_problem is not None:
            template, fields = self._monitor_problem
            self._report(template, **fields)
            return
        # No exception, just nothing to clamp to - still worth saying, because the box
        # is about to open wherever it was asked to and may run off the screen edge.
        self._report(MONITORS_EMPTY)

    # ---------------------------------------------------------------- events

    def event(self, event: QtCore.QEvent) -> bool:
        if (
            event.type() == QtCore.QEvent.Type.WindowDeactivate
            and not self._finished
            and self.isVisible()
        ):
            # The user clicked into another application: that is "clicking elsewhere".
            self.commit()
        return super().event(event)

    def _belongs_to_the_box(self, watched: QtCore.QObject) -> bool:
        """Is this filter receiver the box itself, or something belonging to it?

        Qt hands one mouse press to an application-wide filter **twice**: first to the
        `QWidgetWindow` that owns the HWND - a `QWindow`, *not* a `QWidget` - and only
        then to the widget the press lands on. Measured offscreen on 2026-11-04 with
        `QTest.mousePress` on `windowHandle()`, which goes through
        `QWindowSystemInterface` exactly as the Windows plugin does; the trace for one
        press at (5, 5) inside the box was ``QWindow`` and then ``TextBox``. Treating
        every non-widget receiver as "elsewhere" therefore committed the note on the
        window-level delivery, before the widget-level one could be seen as inside: the
        very first click in the editor ended the note, so the caret could not be placed,
        nothing could be selected and a double click was impossible.
        """
        if isinstance(watched, QtWidgets.QWidget):
            return watched is self or self.isAncestorOf(watched)
        if isinstance(watched, QtGui.QWindow):
            handle = self.windowHandle()
            if handle is None:
                return False
            # `isAncestorOf` includes transient children, so a popup of the box's own
            # window counts as inside as well.
            return watched is handle or handle.isAncestorOf(watched)
        return False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if self._finished or not self.isVisible():
            return False
        if event.type() in (
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QEvent.Type.NonClientAreaMouseButtonPress,
        ):
            if self._belongs_to_the_box(watched):
                return False
            self.commit()
        # Never swallowed: the click that commits the note is also the click that starts
        # the next stroke, and eating it would make the user click twice.
        return False

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._exclude_from_capture()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._stop_filtering()
        super().hideEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Any other way of disappearing is a cancel, not a silent loss of the text."""
        self.cancel()
        super().closeEvent(event)

    def _on_text_changed(self) -> None:
        if self.isVisible():
            self._place()

    def _close(self) -> None:
        self._stop_filtering()
        self._editor.blockSignals(True)
        try:
            self._editor.clear()
        finally:
            self._editor.blockSignals(False)
        self.hide()

    def _stop_filtering(self) -> None:
        if not self._filtering:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._filtering = False

    # ---------------------------------------------------------------- native

    @staticmethod
    def _platform_name() -> str:
        """The Qt platform plugin's name.

        Its own method so a test can stand in for it: under the offscreen plugin there is
        no HWND to exclude, and the exclusion is the one part of this widget that cannot
        otherwise be exercised without a visible window on the user's screen.
        """
        return QtGui.QGuiApplication.platformName()

    def _exclude_from_capture(self) -> None:
        """Take this window out of the capture, and say so if it could not be done."""
        if self._platform_name() != "windows":
            # offscreen and minimal have no HWND, and nothing is capturing them either.
            self.excluded_from_capture = False
            return
        self.excluded_from_capture = surfaces.exclude_from_capture(self)
        if not self.excluded_from_capture:
            self._report(NO_CAPTURE_EXCLUSION)

    # -------------------------------------------------------------- settings

    def _ink(self) -> QtGui.QColor:
        colour = QtGui.QColor(self.settings.colour)
        if not colour.isValid():
            # A bad colour must not paint invisible text; say so and fall back.
            self._report(BAD_COLOUR, colour=repr(self.settings.colour))
            colour = QtGui.QColor(theme.COLOURS["text"])
        return colour

    def _apply_settings(self) -> None:
        """Dress the editor in the colour and size the text will really be drawn at."""
        ink = self._ink()
        pixels = max(1, self._logical_font_px())
        font = QtGui.QFont(self.font())
        font.setPixelSize(pixels)
        #: The font the text is really drawn in. Kept because `QWidget.font()` is not a
        #: reliable way to read it back: measured on 2026-11-04, with the application's
        #: style sheet applied (`theme.apply`), the `QWidget { font-size: 9pt; }` rule
        #: overwrites what `setFont` put there, and the editor came back at 9 pt with
        #: `pixelSize() == -1` - a 32 px note shown 12 px high. The size is therefore
        #: *also* set in the widget's own sheet below, which outranks the application's,
        #: and every measurement here uses this object rather than the widget's font.
        self._text_font = font
        self._editor.setFont(font)
        self._editor.document().setDefaultFont(font)
        # A widget-level sheet beats the application one for this widget, which is how
        # the field background and border the theme gives every QTextEdit are removed -
        # and how the font size survives.
        self._editor.setStyleSheet(
            "QTextEdit {"
            " background: transparent;"
            " border: none;"
            f" font-size: {pixels}px;"
            f" color: {ink.name()};"
            f" selection-background-color: {theme.COLOURS['accent']};"
            f" selection-color: {theme.COLOURS['accent_text']};"
            "}"
        )
        self._editor.viewport().setAutoFillBackground(False)
        palette = self._editor.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Text, ink)
        # The caret is drawn in the Text role, so it is the colour of the ink too.
        self._editor.setPalette(palette)

    def _logical_font_px(self) -> int:
        """`font_size` is physical pixels; Qt sizes a font in logical ones.

        Dividing by the ratio of the screen the box opened on makes the glyphs the size
        the canvas will draw them at. At 100% - this machine, and the offscreen plugin -
        the two are the same number. **Untested above 100%.**
        """
        ratio = self._ratio_for(self._monitor_for(self.requested_anchor))
        return int(round(self.settings.font_size / ratio))

    # -------------------------------------------------------------- geometry

    def _monitor_for(self, point: QtCore.QPoint) -> winapi.Monitor | None:
        """The monitor the anchor is on, the nearest one if it is on none, else None."""
        if not self.monitors:
            return None
        found = winapi.monitor_at(self.monitors, point.x(), point.y())
        if found is not None:
            return found
        # Between two screens, or off the end of the desktop: the nearest monitor by the
        # distance to its centre. A box with nowhere to be is worse than one next door.
        return min(
            self.monitors,
            key=lambda m: (point.x() - (m.x + m.width / 2)) ** 2
            + (point.y() - (m.y + m.height / 2)) ** 2,
        )

    def _bounds_for(self, monitor: winapi.Monitor | None) -> QtCore.QRect:
        """The physical rectangle the box has to stay inside, empty when unknown."""
        if monitor is None:
            return QtCore.QRect()
        return QtCore.QRect(monitor.x, monitor.y, monitor.width, monitor.height)

    def _place(self) -> None:
        """Size the box to its text and put it on the screen, clamped to the monitor."""
        anchor = self.requested_anchor
        monitor = self._monitor_for(anchor)
        screen = self._screen_for(monitor) if monitor is not None else None
        if screen is None:
            ratio = 1.0
            if monitor is not None and self._desktop_is_scaled():
                # At 100% the physical rectangle *is* the logical one and the fallback is
                # exact, so there is nothing to report; only a scaled desktop makes an
                # unpaired monitor a wrong answer.
                self._report(UNPAIRED_MONITOR, index=str(monitor.index))
        else:
            ratio = float(screen.devicePixelRatio() or 1.0)

        size = self._wanted_size()
        pad_x = int(round(PAD_X * ratio))
        pad_y = int(round(PAD_Y * ratio))
        width = int(math.ceil(size.width() * ratio))
        height = int(math.ceil(size.height() * ratio))

        bounds = self._bounds_for(monitor)
        left = anchor.x() - pad_x
        top = anchor.y() - pad_y
        if not bounds.isEmpty():
            # A box wider than the screen is cut to the screen before it is moved, or the
            # clamp below would push its left edge off the other side.
            width = min(width, bounds.width())
            height = min(height, bounds.height())
            left = min(max(left, bounds.left()), bounds.left() + bounds.width() - width)
            top = min(max(top, bounds.top()), bounds.top() + bounds.height() - height)

        self.desktop_rect = QtCore.QRect(left, top, width, height)
        self.anchor = QtCore.QPoint(left + pad_x, top + pad_y)
        self.setGeometry(self._logical_rect(self.desktop_rect, monitor, ratio, screen))

    def _wanted_size(self) -> QtCore.QSizeF:
        """The box's size in logical units: the text, plus the padding, plus the caret."""
        metrics = QtGui.QFontMetricsF(self.text_font())
        text = self._editor.toPlainText()
        lines = text.split("\n") if text else [""]
        widest = max(metrics.horizontalAdvance(line) for line in lines)
        # An empty box still has to be visible and clickable, so it is at least one
        # em wide - about the width of the caret plus a character.
        widest = max(widest + CARET_ROOM, metrics.horizontalAdvance("M"))
        height = metrics.lineSpacing() * max(1, len(lines))
        return QtCore.QSizeF(widest + 2 * PAD_X, height + 2 * PAD_Y)

    # Pairing a DXGI output with a QScreen. The same three steps, in the same order, as
    # `ui.captureframe.CaptureFrame._screen_for` and `overlay._OverlayBase._match_screen`,
    # because it is the same decision; it is repeated rather than imported because both
    # of those are methods on classes this widget is not. Measured there: `QScreen.name()`
    # is the friendly monitor name here, so the name test never matches on this machine
    # and geometry is what actually pairs them.

    def _screen_for(self, monitor: winapi.Monitor) -> QtGui.QScreen | None:
        screens = QtGui.QGuiApplication.screens()
        for screen in screens:
            if screen.name() == monitor.device_name:
                return screen
        for screen in screens:
            geometry = screen.geometry()
            if (
                geometry.x() == monitor.x
                and geometry.y() == monitor.y
                and geometry.width() == monitor.width
                and geometry.height() == monitor.height
            ):
                return screen
        candidates: list[tuple[QtGui.QScreen, QtCore.QRect, float]] = []
        for screen in screens:
            geometry = screen.geometry()
            ratio = float(screen.devicePixelRatio() or 1.0)
            if (
                int(round(geometry.width() * ratio)) == monitor.width
                and int(round(geometry.height() * ratio)) == monitor.height
            ):
                candidates.append((screen, geometry, ratio))
        if len(candidates) == 1:
            return candidates[0][0]
        by_origin = [
            screen
            for screen, geometry, ratio in candidates
            if int(round(geometry.x() * ratio)) == monitor.x
            and int(round(geometry.y() * ratio)) == monitor.y
        ]
        return by_origin[0] if len(by_origin) == 1 else None

    def _ratio_for(self, monitor: winapi.Monitor | None) -> float:
        if monitor is None:
            return 1.0
        screen = self._screen_for(monitor)
        if screen is None:
            return 1.0
        return float(screen.devicePixelRatio() or 1.0)

    @staticmethod
    def _desktop_is_scaled() -> bool:
        return any(
            abs(float(screen.devicePixelRatio() or 1.0) - 1.0) > 1e-6
            for screen in QtGui.QGuiApplication.screens()
        )

    def _logical_rect(
        self,
        rect: QtCore.QRect,
        monitor: winapi.Monitor | None,
        ratio: float,
        screen: QtGui.QScreen | None,
    ) -> QtCore.QRect:
        """Physical desktop pixels -> the logical units `setGeometry` wants.

        At 100% the two are identical, which is the case here and under the offscreen
        plugin, so the physical rectangle goes straight through. **The scaled branch is
        untested**; it is Qt's own mapping - the screen's logical origin plus the offset
        into that screen divided by its ratio - and matches `ui.captureframe`.
        """
        if abs(ratio - 1.0) <= 1e-6 or monitor is None or screen is None:
            return QtCore.QRect(rect)
        origin = screen.geometry()
        return QtCore.QRect(
            origin.x() + int(round((rect.x() - monitor.x) / ratio)),
            origin.y() + int(round((rect.y() - monitor.y) / ratio)),
            max(1, int(math.ceil(rect.width() / ratio))),
            max(1, int(math.ceil(rect.height() / ratio))),
        )

    # --------------------------------------------------------------- drawing

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            # An exception escaping a live QPainter takes the interpreter down.
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        ink = self._ink()
        border = QtGui.QColor(ink)
        border.setAlpha(BORDER_ALPHA)
        box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QtGui.QPen(border, 1))
        painter.setBrush(backing_colour(ink))
        radius = float(theme.RADIUS["small"])
        painter.drawRoundedRect(box, radius, radius)
