"""Checks for `screenrec.annotate.textbox`.

Three things this module must not get wrong, and each has its own group below:

* **The alphabet.** The user writes in Russian. A text tool that builds its string from
  key codes drops every character of it, so the Cyrillic round trip is checked twice -
  once through ordinary key events and once through an input-method commit, which is the
  path a composed or IME-entered character takes.
* **Finishing.** Enter commits, Shift+Enter does not, Esc cancels, a click elsewhere
  commits, and an empty box is a cancel rather than an empty item.
* **Staying on screen.** The box is placed in physical desktop pixels on monitors that
  may start at a negative coordinate, and a click two pixels from the right edge must
  still open a whole editor.

No GPU, no recording and no visible window: the Qt part runs under the offscreen platform
plugin, which this file sets before PySide6 is imported.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from screenrec import i18n, winapi  # noqa: E402
from screenrec.annotate import model, textbox  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


#: Stand-ins for DXGI outputs, so nothing here depends on this machine's monitors.
#: `Monitor` is a frozen dataclass of plain numbers, so one can simply be built.
MAIN = winapi.Monitor(
    index=0,
    device_name="\\\\.\\DISPLAY1",
    x=0,
    y=0,
    width=2560,
    height=1440,
    rotation=0,
    adapter="test",
)

#: A screen up and to the LEFT of the primary one: negative coordinates, which the whole
#: coordinate contract turns on.
LEFT = winapi.Monitor(
    index=1,
    device_name="\\\\.\\DISPLAY2",
    x=-1920,
    y=-200,
    width=1920,
    height=1080,
    rotation=0,
    adapter="test",
)

CYRILLIC = "Привет, мир"
CYRILLIC_TWO_LINES = "Привет\nмир"


def type_text(widget: QtWidgets.QWidget, text: str) -> None:
    """Type `text` into `widget` the way a keyboard does: a key event carrying the text.

    **Not `QTest.keyClicks`.** Measured here on 2026-11-04 under the offscreen plugin:
    `QTest.keyClicks(editor, "Привет")` kills the interpreter outright - the run ends
    with exit code 9 and no traceback, because QTest maps each character through Latin-1
    to find a key code and qFatal()s on anything that is not in it. That is a limit of
    the test helper, not of the widget, and using it would make the Cyrillic case
    untestable.

    So each character is sent as the Windows plugin sends it on a Russian layout: a key
    code that is the character's uppercase code point, and `text` set to the character
    itself. A widget that reads `event.text()` (or lets `QTextEdit` do it) gets the right
    string; one that switches on the key code gets nothing, which is the failure this
    whole group exists to catch.
    """
    for character in text:
        key = int(QtCore.Qt.Key(ord(character.upper())))
        for kind in (
            QtCore.QEvent.Type.KeyPress,
            QtCore.QEvent.Type.KeyRelease,
        ):
            event = QtGui.QKeyEvent(
                kind, key, QtCore.Qt.KeyboardModifier.NoModifier, character
            )
            QtWidgets.QApplication.sendEvent(widget, event)


class _ClickRecorder(QtWidgets.QWidget):
    """Stands in for whatever the user clicked on while a note was open."""

    def __init__(self) -> None:
        super().__init__()
        self.presses = 0

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.presses += 1
        super().mousePressEvent(event)


class TextBoxTestCase(unittest.TestCase):
    """Shared fixture: one box per test, on a known two-monitor desktop."""

    settings = model.ToolSettings(colour="#FFFFFF", width=2, font_size=24)

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.box = textbox.TextBox(model.ToolSettings(**vars(self.settings)))
        # Fixed monitors, so the geometry checks are the same on any machine.
        self.box.monitors = [MAIN, LEFT]
        self.committed: list[str] = []
        self.cancelled: list[bool] = []
        self.box.committed.connect(self.committed.append)
        self.box.cancelled.connect(lambda: self.cancelled.append(True))
        self.addCleanup(self.box.deleteLater)
        self.addCleanup(self.box.hide)

    @property
    def editor(self) -> QtWidgets.QTextEdit:
        found = self.box.findChild(QtWidgets.QTextEdit)
        self.assertIsNotNone(found, "the text box has no editor")
        return found

    def type(self, text: str) -> None:
        type_text(self.editor, text)


# ------------------------------------------------------------------- Russian


class CyrillicTest(TextBoxTestCase):
    def test_a_cyrillic_string_survives_being_typed(self) -> None:
        self.box.open_at(600, 400)
        self.type(CYRILLIC)
        self.assertEqual(CYRILLIC, self.box.text())

    def test_a_cyrillic_string_survives_being_committed(self) -> None:
        self.box.open_at(600, 400)
        self.type(CYRILLIC)
        QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Return)
        self.assertEqual([CYRILLIC], self.committed)
        self.assertEqual([], self.cancelled)

    def test_cyrillic_through_an_input_method_commit(self) -> None:
        """The path a composed character takes - no key event carries the text at all."""
        self.box.open_at(600, 400)
        event = QtGui.QInputMethodEvent()
        event.setCommitString(CYRILLIC)
        QtWidgets.QApplication.sendEvent(self.editor, event)
        self.assertEqual(CYRILLIC, self.box.text())
        QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Return)
        self.assertEqual([CYRILLIC], self.committed)

    def test_the_box_grows_to_fit_cyrillic(self) -> None:
        self.box.open_at(600, 400)
        narrow = self.box.desktop_rect.width()
        self.type(CYRILLIC)
        self.assertGreater(self.box.desktop_rect.width(), narrow)


# ----------------------------------------------------------------- finishing


class FinishingTest(TextBoxTestCase):
    def test_enter_commits(self) -> None:
        self.box.open_at(600, 400)
        self.type("hello")
        QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Return)
        self.assertEqual(["hello"], self.committed)
        self.assertFalse(self.box.isVisible())

    def test_shift_enter_is_a_new_line_and_does_not_commit(self) -> None:
        self.box.open_at(600, 400)
        self.type("Привет")
        QTest.keyClick(
            self.editor,
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        self.type("мир")
        self.assertEqual([], self.committed, "Shift+Enter committed the note")
        self.assertEqual(CYRILLIC_TWO_LINES, self.box.text())
        self.assertTrue(self.box.isVisible())
        QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Return)
        self.assertEqual([CYRILLIC_TWO_LINES], self.committed)

    def test_the_keypad_enter_commits_too(self) -> None:
        self.box.open_at(600, 400)
        self.type("да")
        QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Enter)
        self.assertEqual(["да"], self.committed)

    def test_escape_cancels_and_emits_cancelled(self) -> None:
        self.box.open_at(600, 400)
        self.type(CYRILLIC)
        QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Escape)
        self.assertEqual([True], self.cancelled)
        self.assertEqual([], self.committed, "Esc committed the text")
        self.assertFalse(self.box.isVisible())

    def test_committing_an_empty_box_is_a_cancel(self) -> None:
        self.box.open_at(600, 400)
        QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Return)
        self.assertEqual([True], self.cancelled)
        self.assertEqual([], self.committed, "an empty item was made")

    def test_committing_whitespace_is_a_cancel(self) -> None:
        self.box.open_at(600, 400)
        self.type("   ")
        QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Return)
        self.assertEqual([True], self.cancelled)
        self.assertEqual([], self.committed)

    def test_clicking_elsewhere_commits(self) -> None:
        other = _ClickRecorder()
        self.addCleanup(other.deleteLater)
        self.box.open_at(600, 400)
        self.type(CYRILLIC)
        press = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(5, 5),
            QtCore.QPointF(5, 5),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(other, press)
        self.assertEqual([CYRILLIC], self.committed)
        # The press still arrived where it was aimed: an application-wide filter that
        # swallowed it would make the user click twice - once to finish the note and
        # again to do what they meant.
        self.assertEqual(1, other.presses, "the committing click never reached its target")

    def test_a_click_inside_the_box_does_not_commit(self) -> None:
        self.box.open_at(600, 400)
        self.type("мир")
        press = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(3, 3),
            QtCore.QPointF(3, 3),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(self.editor, press)
        self.assertEqual([], self.committed)
        self.assertTrue(self.box.isVisible())

    def test_a_real_click_inside_the_box_does_not_commit(self) -> None:
        """The same click, but delivered the way the platform plugin delivers it.

        `QApplication.sendEvent` to the editor skips the step that actually broke this:
        Qt gives an application-wide filter every press **twice**, first to the
        `QWidgetWindow` (a `QWindow`, not a `QWidget`) and only then to the widget.
        `QTest.mousePress` on the window handle goes through `QWindowSystemInterface`,
        which is the Windows plugin's own path, so both deliveries happen. Before the
        fix this committed the note on the first click in the editor.
        """
        self.box.open_at(600, 400)
        self.type(CYRILLIC)
        handle = self.box.windowHandle()
        self.assertIsNotNone(handle, "the box never got a window handle")
        # A point inside the editor itself, not in the box's padding, so the press is
        # also a caret click.
        inside = self.editor.mapTo(self.box, QtCore.QPoint(2, 2))
        QTest.mousePress(
            handle,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            inside,
        )
        QtWidgets.QApplication.instance().processEvents()
        self.assertEqual([], self.committed, "a click inside the box committed it")
        self.assertEqual([], self.cancelled)
        self.assertTrue(self.box.isVisible())
        self.assertEqual(CYRILLIC, self.box.text())

    def test_a_real_click_on_another_window_still_commits(self) -> None:
        """The fix must not make every window-level press "inside"."""
        other = _ClickRecorder()
        other.setGeometry(10, 10, 120, 80)
        other.show()
        self.addCleanup(other.deleteLater)
        self.addCleanup(other.hide)
        self.box.open_at(600, 400)
        self.type(CYRILLIC)
        handle = other.windowHandle()
        self.assertIsNotNone(handle, "the stand-in never got a window handle")
        QTest.mousePress(
            handle,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            QtCore.QPoint(5, 5),
        )
        QtWidgets.QApplication.instance().processEvents()
        self.assertEqual([CYRILLIC], self.committed)
        self.assertFalse(self.box.isVisible())

    def test_committing_twice_emits_once(self) -> None:
        self.box.open_at(600, 400)
        self.type("один")
        self.box.commit()
        self.box.commit()
        self.box.cancel()
        self.assertEqual(["один"], self.committed)
        self.assertEqual([], self.cancelled)

    def test_reopening_starts_empty(self) -> None:
        self.box.open_at(600, 400)
        self.type(CYRILLIC)
        self.box.commit()
        self.box.open_at(700, 500)
        self.assertEqual("", self.box.text())
        QTest.keyClick(self.editor, QtCore.Qt.Key.Key_Escape)
        self.assertEqual([CYRILLIC], self.committed)
        self.assertEqual([True], self.cancelled)


# ------------------------------------------------------------------ geometry


class GeometryTest(TextBoxTestCase):
    def test_the_anchor_is_where_it_was_asked_for_when_there_is_room(self) -> None:
        self.box.open_at(600, 400)
        self.assertEqual(QtCore.QPoint(600, 400), self.box.anchor)
        self.assertEqual(QtCore.QPoint(600, 400), self.box.requested_anchor)
        # The text starts PAD_X/PAD_Y inside the box, so the box starts before the point.
        self.assertEqual(600 - textbox.PAD_X, self.box.desktop_rect.left())
        self.assertEqual(400 - textbox.PAD_Y, self.box.desktop_rect.top())
        # Every screen is at ratio 1 under the offscreen plugin, so logical == physical.
        self.assertEqual(self.box.desktop_rect, self.box.geometry())

    def test_it_is_clamped_at_the_right_and_bottom_edges(self) -> None:
        self.box.open_at(MAIN.width - 2, MAIN.height - 3)
        rect = self.box.desktop_rect
        self.assertLessEqual(rect.left() + rect.width(), MAIN.x + MAIN.width)
        self.assertLessEqual(rect.top() + rect.height(), MAIN.y + MAIN.height)
        self.assertGreaterEqual(rect.left(), MAIN.x)
        self.assertGreaterEqual(rect.top(), MAIN.y)
        self.assertGreater(rect.width(), 0)
        self.assertGreater(rect.height(), 0)
        # It really did have to move: the anchor is left of where it was asked for.
        self.assertLess(self.box.anchor.x(), self.box.requested_anchor.x())
        self.assertLess(self.box.anchor.y(), self.box.requested_anchor.y())

    def test_it_stays_on_screen_while_the_text_grows(self) -> None:
        self.box.open_at(MAIN.width - 2, 400)
        self.type(CYRILLIC * 3)
        rect = self.box.desktop_rect
        self.assertLessEqual(rect.left() + rect.width(), MAIN.x + MAIN.width)
        self.assertGreaterEqual(rect.left(), MAIN.x)

    def test_a_monitor_with_negative_coordinates(self) -> None:
        self.box.open_at(LEFT.x + 4, LEFT.y + 4)
        rect = self.box.desktop_rect
        self.assertGreaterEqual(rect.left(), LEFT.x)
        self.assertGreaterEqual(rect.top(), LEFT.y)
        self.assertLess(rect.left(), 0, "the box is not on the left-hand monitor")
        self.assertLess(rect.top(), 0)

    def test_the_far_corner_of_a_negative_monitor(self) -> None:
        self.box.open_at(LEFT.x + LEFT.width - 1, LEFT.y + LEFT.height - 1)
        rect = self.box.desktop_rect
        self.assertLessEqual(rect.left() + rect.width(), LEFT.x + LEFT.width)
        self.assertLessEqual(rect.top() + rect.height(), LEFT.y + LEFT.height)

    def test_a_point_on_no_monitor_lands_on_the_nearest_one(self) -> None:
        """Between the two screens, or off the end: it must still be on a screen."""
        self.box.open_at(9000, 9000)
        rect = self.box.desktop_rect
        self.assertLessEqual(rect.left() + rect.width(), MAIN.x + MAIN.width)
        self.assertLessEqual(rect.top() + rect.height(), MAIN.y + MAIN.height)

    def test_with_no_monitors_at_all_it_still_opens_and_says_so(self) -> None:
        self.box.monitors = []
        self.box.open_at(600, 400)
        self.assertTrue(self.box.isVisible())
        self.assertEqual(QtCore.QPoint(600, 400), self.box.anchor)
        # "Says so" has to mean something: without a monitor list nothing is clamped,
        # so a box opened near an edge runs off it and the controller must hear why.
        self.assertIn("monitor list", self.box.problem)

    def test_a_failed_monitor_read_is_still_reported_after_open_at(self) -> None:
        """`open_at` used to blank `problem`, losing the constructor's warning.

        The reason is not a stale message from the last note: DXGI is still failing, the
        box is still unclamped, and this is the open where it matters.
        """
        with mock.patch.object(
            textbox.winapi, "list_monitors", side_effect=OSError("dxgi said no")
        ):
            box = textbox.TextBox(model.ToolSettings(colour="#FFFFFF", font_size=24))
        self.addCleanup(box.deleteLater)
        self.addCleanup(box.hide)
        self.assertIn("dxgi said no", box.problem)
        box.open_at(600, 400)
        self.assertIn("dxgi said no", box.problem)
        self.assertIn("dxgi said no", box.monitor_problem)

    def test_two_problems_are_both_reported(self) -> None:
        """A bad colour and a missing monitor list are independent failures."""
        box = textbox.TextBox(model.ToolSettings(colour="not a colour", font_size=20))
        self.addCleanup(box.deleteLater)
        self.addCleanup(box.hide)
        box.monitors = []
        box.open_at(600, 400)
        self.assertIn("monitor list", box.problem)
        self.assertIn("not a colour", box.problem)

    def test_a_problem_is_not_repeated_once_per_repaint(self) -> None:
        box = textbox.TextBox(model.ToolSettings(colour="not a colour", font_size=20))
        self.addCleanup(box.deleteLater)
        self.addCleanup(box.hide)
        box.monitors = [MAIN]
        box.open_at(600, 400)
        for _ in range(5):
            box.grab()
        # Counting the sentence, not the colour name: the name is quoted inside the
        # sentence, so "not a colour" occurs twice in a single copy of the message.
        self.assertEqual(
            1, box.problem.count("is not a colour the text box understands")
        )


# ------------------------------------------------------- look and the capture


class AppearanceTest(TextBoxTestCase):
    def test_the_editor_uses_the_settings_colour_and_size(self) -> None:
        settings = model.ToolSettings(colour="#F2564B", width=2, font_size=32)
        box = textbox.TextBox(settings)
        self.addCleanup(box.deleteLater)
        box.monitors = [MAIN]
        box.open_at(500, 500)
        editor = box.findChild(QtWidgets.QTextEdit)
        # At ratio 1 the physical font size is the logical one. Read through
        # `text_font()`, not `editor.font()`: with the application style sheet applied
        # the latter comes back at the sheet's 9 pt - see `_apply_settings`.
        self.assertEqual(32, box.text_font().pixelSize())
        self.assertIn("font-size: 32px", editor.styleSheet())
        self.assertIn("#f2564b", editor.styleSheet().lower())
        self.assertEqual(
            QtGui.QColor("#F2564B"),
            editor.palette().color(QtGui.QPalette.ColorRole.Text),
        )
        box.hide()

    def test_the_backing_is_the_more_readable_of_the_two_plates(self) -> None:
        dark = QtGui.QColor(textbox.theme.COLOURS["bg"])
        light = QtGui.QColor(textbox.theme.COLOURS["text"])
        white = textbox.backing_colour(QtGui.QColor("#FFFFFF"))
        black = textbox.backing_colour(QtGui.QColor("#111111"))
        self.assertEqual(dark.rgb(), QtGui.QColor(white.rgb()).rgb())
        self.assertEqual(light.rgb(), QtGui.QColor(black.rgb()).rgb())
        self.assertEqual(textbox.BACKING_ALPHA, white.alpha())

    def test_it_paints_in_every_state(self) -> None:
        for colour in model.PALETTE:
            with self.subTest(colour=colour):
                box = textbox.TextBox(
                    model.ToolSettings(colour=colour, width=2, font_size=18)
                )
                box.monitors = [MAIN]
                box.open_at(300, 300)
                self.assertFalse(box.grab().isNull(), "empty box painted nothing")
                type_text(box.findChild(QtWidgets.QTextEdit), CYRILLIC)
                self.assertFalse(box.grab().isNull(), "typed box painted nothing")
                box.cancel()
                self.assertFalse(box.grab().isNull(), "closed box painted nothing")
                box.deleteLater()

    def test_the_font_size_survives_the_application_style_sheet(self) -> None:
        """The regression that running the whole suite found.

        `theme.apply` puts ``QWidget { font-size: 9pt; }`` on the application, and a
        style sheet overwrites what `setFont` put on a widget: measured before the fix, a
        32 px note came back as 9 pt with ``pixelSize() == -1`` and the box was 28x22
        instead of 48x45. The size is therefore set in the editor's own sheet as well,
        which outranks the application's.
        """
        app = QtWidgets.QApplication.instance()
        previous = app.styleSheet()
        self.addCleanup(app.setStyleSheet, previous)
        app.setStyleSheet(textbox.theme.style_sheet())

        box = textbox.TextBox(model.ToolSettings(colour="#FFFFFF", font_size=32))
        self.addCleanup(box.deleteLater)
        box.monitors = [MAIN]
        box.open_at(500, 500)
        editor = box.findChild(QtWidgets.QTextEdit)
        self.assertEqual(32, editor.font().pixelSize())
        self.assertEqual(32, box.text_font().pixelSize())
        self.assertGreaterEqual(box.desktop_rect.height(), 32 + 2 * textbox.PAD_Y)
        box.hide()

    def test_the_grab_really_contains_the_text_in_its_colour(self) -> None:
        """Not just "it painted something": the ink and the right plate are in there.

        The plate colours come back a shade off their palette values because the backing
        is translucent and `grab()` composites it over nothing, so the plate is checked
        by which of the two it is nearer to, and the ink by its exact colour.
        """
        for colour, wants_dark_plate in (("#FFFFFF", True), ("#111111", False)):
            with self.subTest(colour=colour):
                box = textbox.TextBox(
                    model.ToolSettings(colour=colour, width=2, font_size=24)
                )
                box.monitors = [MAIN]
                box.open_at(600, 400)
                type_text(box.findChild(QtWidgets.QTextEdit), "Привет")
                image = box.grab().toImage().convertToFormat(
                    QtGui.QImage.Format.Format_ARGB32
                )
                counts: dict[str, int] = {}
                for y in range(image.height()):
                    for x in range(image.width()):
                        name = image.pixelColor(x, y).name()
                        counts[name] = counts.get(name, 0) + 1
                self.assertGreater(
                    counts.get(colour.lower(), 0), 50, "no ink of the chosen colour"
                )
                plate = QtGui.QColor(max(counts, key=counts.__getitem__))
                dark = QtGui.QColor(textbox.theme.COLOURS["bg"])
                light = QtGui.QColor(textbox.theme.COLOURS["text"])
                nearer_dark = abs(
                    textbox._relative_luminance(plate)
                    - textbox._relative_luminance(dark)
                ) < abs(
                    textbox._relative_luminance(plate)
                    - textbox._relative_luminance(light)
                )
                self.assertEqual(wants_dark_plate, nearer_dark)
                box.cancel()
                box.deleteLater()

    def test_an_invalid_colour_is_reported_not_swallowed(self) -> None:
        box = textbox.TextBox(model.ToolSettings(colour="not a colour", font_size=20))
        self.addCleanup(box.deleteLater)
        box.monitors = [MAIN]
        box.open_at(300, 300)
        self.assertIn("not a colour", box.problem)
        self.assertFalse(box.grab().isNull())
        box.hide()

    def test_the_exclusion_is_applied_when_there_is_a_window_to_apply_it_to(self) -> None:
        """Offscreen has no HWND, so the platform check stands in for a real screen."""
        with mock.patch.object(textbox.TextBox, "_platform_name", staticmethod(
            lambda: "windows"
        )), mock.patch.object(
            textbox.surfaces, "exclude_from_capture", return_value=True
        ) as excluded:
            box = textbox.TextBox(self.settings)
            self.addCleanup(box.deleteLater)
            box.monitors = [MAIN]
            box.open_at(400, 400)
            excluded.assert_called_once_with(box)
            self.assertTrue(box.excluded_from_capture)
            self.assertEqual("", box.problem)
            box.hide()

    def test_a_failed_exclusion_is_said_out_loud(self) -> None:
        with mock.patch.object(textbox.TextBox, "_platform_name", staticmethod(
            lambda: "windows"
        )), mock.patch.object(
            textbox.surfaces, "exclude_from_capture", return_value=False
        ):
            box = textbox.TextBox(self.settings)
            self.addCleanup(box.deleteLater)
            box.monitors = [MAIN]
            box.open_at(400, 400)
            self.assertFalse(box.excluded_from_capture)
            self.assertIn("WDA_EXCLUDEFROMCAPTURE", box.problem)
            box.hide()


# -------------------------------------------------------------- two languages


class LanguageTests(TextBoxTestCase):
    """The reasons the box gives are read by a person, so they are translated too."""

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(i18n.set_language, i18n.LANG_EN)

    def test_the_window_title_is_translated(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()
        self.assertIn("примечания", self.box.windowTitle())

    def test_a_reason_is_read_in_the_language_in_force_now(self) -> None:
        """It was collected in English and is read in Russian: the reason is kept as a
        template and the sentence is written when it is asked for."""
        box = textbox.TextBox(self.settings)
        self.addCleanup(box.deleteLater)
        box.monitors = []
        box.open_at(400, 400)
        self.addCleanup(box.hide)
        self.assertIn("monitor list", box.problem)
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()
        self.assertIn("Список мониторов", box.problem)
        self.assertNotIn("monitor list", box.problem)

    def test_a_failed_read_is_translated_and_keeps_the_error_as_it_came(self) -> None:
        with mock.patch.object(
            winapi, "list_monitors", side_effect=OSError("dxgi said no")
        ):
            box = textbox.TextBox(self.settings)
        self.addCleanup(box.deleteLater)
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()
        for text in (box.problem, box.monitor_problem):
            self.assertIn("Не удалось", text)
            self.assertIn("dxgi said no", text)

    def test_a_bad_colour_is_still_reported_once_in_russian(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        box = textbox.TextBox(model.ToolSettings(colour="not a colour", width=2))
        self.addCleanup(box.deleteLater)
        box.monitors = [MAIN]
        box.open_at(400, 400)
        self.addCleanup(box.hide)
        box.repaint()
        box.repaint()
        self.assertIn("не тот цвет", box.problem)
        self.assertEqual(1, box.problem.count("не тот цвет"))

    def test_two_reasons_are_still_two_in_russian(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        box = textbox.TextBox(model.ToolSettings(colour="not a colour", width=2))
        self.addCleanup(box.deleteLater)
        box.monitors = []
        box.open_at(400, 400)
        self.addCleanup(box.hide)
        self.assertIn("Список мониторов", box.problem)
        self.assertIn("не тот цвет", box.problem)

    def test_a_deleted_box_does_not_keep_the_signal_alive(self) -> None:
        box = textbox.TextBox(self.settings)
        box.deleteLater()
        self.app.processEvents()
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()  # a dangling connection would raise here


if __name__ == "__main__":
    unittest.main()
