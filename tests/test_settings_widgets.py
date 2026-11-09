"""Checks for `screenrec.ui.settings_widgets`.

Only the parts that can be wrong in a way nobody would see until it mattered: where the
camera block lands, that the diagram describes the real overlay rather than a prettier
one, and that the two-column grid really falls back to one. What the widgets look like
is not tested here - a test that pins a colour or a margin fails on every tidy-up and
proves nothing.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from screenrec import config as config_mod  # noqa: E402
from screenrec.ui import settings_widgets  # noqa: E402
from screenrec.ui import theme  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if isinstance(existing, QtWidgets.QApplication):
        return existing
    app = QtWidgets.QApplication([])
    theme.apply(app)
    return app


class TheCameraDiagram(unittest.TestCase):
    """It is a drawing of a setting, so the only thing that can be wrong is whether it
    agrees with what the recording will actually do."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = _app()

    def setUp(self) -> None:
        self.view = settings_widgets.CameraDiagram()
        self.view.resize(480, 220)
        self.addCleanup(self.view.deleteLater)

    def test_the_sizes_are_the_ones_the_real_overlay_uses(self) -> None:
        """`config` spells the fractions again so that this widget need not import
        QtMultimedia. If the two ever disagree the diagram lies."""
        from screenrec import webcam as webcam_mod

        self.assertEqual(
            dict(config_mod.WEBCAM_SIZE_FRACTIONS), dict(webcam_mod.SIZE_FRACTIONS)
        )

    def test_the_block_lands_in_the_corner_it_is_told(self) -> None:
        screen = self.view.screen_rect()
        for corner in config_mod.WEBCAM_CORNERS:
            with self.subTest(corner=corner):
                self.view.set_placement(corner, "medium", "rounded")
                box = self.view.camera_rect()
                self.assertTrue(screen.contains(box))
                near_top = box.center().y() < screen.center().y()
                near_left = box.center().x() < screen.center().x()
                self.assertEqual(near_top, corner.startswith("top"))
                self.assertEqual(near_left, corner.endswith("left"))

    def test_a_circle_is_square_and_the_others_are_not(self) -> None:
        self.view.set_placement("bottom_right", "medium", "circle")
        box = self.view.camera_rect()
        self.assertAlmostEqual(box.width(), box.height())
        for shape in ("rounded", "rectangle"):
            with self.subTest(shape=shape):
                self.view.set_placement("bottom_right", "medium", shape)
                box = self.view.camera_rect()
                self.assertGreater(box.width(), box.height())

    def test_a_larger_size_really_is_larger(self) -> None:
        widths = []
        for size in config_mod.WEBCAM_SIZES:
            self.view.set_placement("bottom_right", size, "rounded")
            widths.append(self.view.camera_rect().width())
        self.assertEqual(widths, sorted(widths))
        self.assertLess(widths[0], widths[-1])

    def test_clicking_a_quarter_picks_that_corner(self) -> None:
        heard: list[str] = []
        self.view.cornerPicked.connect(heard.append)
        screen = self.view.screen_rect()
        point = QtCore.QPoint(
            int(screen.left() + screen.width() * 0.2),
            int(screen.top() + screen.height() * 0.2),
        )
        QTest.mouseClick(
            self.view, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier, point,
        )
        self.assertEqual(heard, ["top_left"])
        self.assertEqual(self.view.placement()[0], "top_left")

    def test_the_arrow_keys_move_it_too(self) -> None:
        """The diagram is the obvious way to set this, so it cannot be mouse-only."""
        self.view.set_placement("bottom_right", "medium", "rounded")
        heard: list[str] = []
        self.view.cornerPicked.connect(heard.append)
        QTest.keyClick(self.view, QtCore.Qt.Key.Key_Up)
        QTest.keyClick(self.view, QtCore.Qt.Key.Key_Left)
        self.assertEqual(heard, ["top_right", "top_left"])

    def test_picking_the_corner_it_is_already_on_says_nothing(self) -> None:
        self.view.set_placement("top_left", "medium", "rounded")
        heard: list[str] = []
        self.view.cornerPicked.connect(heard.append)
        QTest.keyClick(self.view, QtCore.Qt.Key.Key_Up)
        QTest.keyClick(self.view, QtCore.Qt.Key.Key_Left)
        self.assertEqual(heard, [])

    def test_a_value_it_does_not_know_is_ignored_not_drawn(self) -> None:
        self.view.set_placement("top_left", "medium", "rounded")
        self.view.set_placement("middle", "enormous", "trapezoid")
        self.assertEqual(self.view.placement(), ("top_left", "medium", "rounded"))

    def test_it_paints_without_a_camera(self) -> None:
        """Opening the settings must not light the webcam up."""
        image = self.view.grab().toImage()
        self.assertFalse(image.isNull())
        self.assertEqual(image.width(), self.view.width())


class TheFieldGrid(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = _app()

    def _grid(self, count: int) -> settings_widgets.FieldGrid:
        grid = settings_widgets.FieldGrid()
        for _index in range(count):
            field = QtWidgets.QLabel("x")
            grid.add(field)
        self.addCleanup(grid.deleteLater)
        return grid

    def test_two_columns_when_there_is_room(self) -> None:
        grid = self._grid(4)
        grid.resize(600, 200)
        self.qt_app.processEvents()
        self.assertEqual(grid.columns(), 2)

    def test_one_column_when_there_is_not(self) -> None:
        """At 150% and 200% Windows scaling the captions grow and the columns do not.

        Shown, because a hidden widget is not sent the resize event that triggers it -
        and a hidden grid is not a grid anybody is reading.
        """
        grid = self._grid(4)
        grid.resize(600, 200)
        grid.show()
        self.addCleanup(grid.hide)
        self.qt_app.processEvents()
        self.assertEqual(grid.columns(), 2)
        grid.resize(settings_widgets.FieldGrid.NARROW - 40, 200)
        self.qt_app.processEvents()
        self.assertEqual(grid.columns(), 1)

    def test_no_field_is_dropped_on_the_way(self) -> None:
        grid = self._grid(5)
        grid.resize(300, 200)
        grid.show()
        self.addCleanup(grid.hide)
        self.qt_app.processEvents()
        grid.resize(700, 200)
        self.qt_app.processEvents()
        self.assertEqual(grid.columns(), 2)
        self.assertEqual(grid.layout().count(), 5)


class TheNavButton(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = _app()

    def test_only_one_of_a_row_is_checked(self) -> None:
        holder = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(holder)
        buttons = [settings_widgets.NavButton("record", f"Page {i}") for i in range(3)]
        for button in buttons:
            layout.addWidget(button)
        self.addCleanup(holder.deleteLater)

        buttons[0].setChecked(True)
        buttons[2].setChecked(True)
        self.assertEqual([b.isChecked() for b in buttons], [False, False, True])

    def test_the_word_is_the_accessible_name(self) -> None:
        """The icon beside it is decoration; a screen reader reads this."""
        button = settings_widgets.NavButton("camera", "Camera")
        self.assertEqual(button.accessibleName(), "Camera")
        button.setText("Камера")
        self.assertEqual(button.accessibleName(), "Камера")
        button.deleteLater()

    def test_checked_and_unchecked_are_not_the_same_picture(self) -> None:
        button = settings_widgets.NavButton("speaker", "Sound")
        button.resize(176, 40)
        off = button.grab().toImage()
        button.setChecked(True)
        on = button.grab().toImage()
        self.assertNotEqual(off, on)
        button.deleteLater()


class TheInfoButton(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = _app()

    def test_the_keyboard_can_reach_the_explanation(self) -> None:
        """A tool tip is a mouse-only feature; this one answers Space as well."""
        button = settings_widgets.InfoButton("What 4:4:4 means")
        self.addCleanup(button.deleteLater)
        self.assertEqual(button.toolTip(), "What 4:4:4 means")
        self.assertEqual(button.accessibleDescription(), "What 4:4:4 means")
        self.assertNotEqual(
            button.focusPolicy(), QtCore.Qt.FocusPolicy.NoFocus,
            "a control the keyboard cannot reach cannot be read from the keyboard",
        )
        seen: list[int] = []
        button.clicked.connect(lambda: seen.append(1))
        button.setFocus()
        QTest.keyClick(button, QtCore.Qt.Key.Key_Space)
        self.assertEqual(seen, [1])


class TheSwitchIsNotOnlyAColour(unittest.TestCase):
    """Telling on from off must not depend on seeing violet."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = _app()

    def test_the_knob_moves_and_the_shape_inside_it_changes(self) -> None:
        switch = settings_widgets.Switch()
        switch.resize(switch.sizeHint())
        self.addCleanup(switch.deleteLater)
        switch.set_checked_now(False)
        off = switch.grab().toImage()
        switch.set_checked_now(True)
        on = switch.grab().toImage()

        self.assertEqual(switch.travel(), 1.0)
        # In grey, which is what somebody who cannot tell the colours apart sees.
        grey_off = off.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
        grey_on = on.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
        self.assertNotEqual(grey_off, grey_on)


if __name__ == "__main__":
    unittest.main()
