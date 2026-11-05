"""Checks for `screenrec.annotate.toolbar`.

The panel is the only part of the annotation layer the user touches directly, so what is
checked here is what would be noticed first:

* it builds and paints in every state it has - full bar, collapsed pill, each tool
  selected, drawings hidden, history exhausted - and both popovers paint too;
* every signal arrives, with the right payload, from the interaction that should fire it,
  including the chevron zone of the shapes button, which is one widget and so cannot be
  told apart by connecting to two buttons;
* `avoid()` really clears the rectangle it was given, really stays on a screen, and comes
  **back** to the position the user chose once the obstruction is gone;
* the collapsed pill still carries the mode button. Collapsing must never be able to trap
  the user in a mode where their clicks do not reach their own application, so that one is
  asserted rather than assumed;
* the eight swatches are `model.PALETTE`, in order, and editing one writes into the
  document rather than into the toolbar;
* tooltips name the tool **and** its shortcut.

Monitor geometry is written out here, negative origin included, instead of being read from
whatever is plugged in while the suite runs. No GPU, no recording, no visible window: the
offscreen platform plugin is set before PySide6 is imported.
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

from screenrec import i18n, winapi  # noqa: E402
from screenrec.annotate import model  # noqa: E402
from screenrec.annotate import toolbar as toolbar_module  # noqa: E402
from screenrec.ui import theme  # noqa: E402

Toolbar = toolbar_module.Toolbar

#: A two-monitor desktop whose second screen is to the **left** of the primary, so every
#: coordinate on it is negative. Nothing in the toolbar may assume an origin at (0, 0).
LEFT = winapi.Monitor(
    index=1, device_name="\\\\.\\DISPLAY1", x=-1920, y=-120, width=1920, height=1080,
    rotation=0, adapter="test",
)
MAIN = winapi.Monitor(
    index=0, device_name="\\\\.\\DISPLAY2", x=0, y=0, width=2560, height=1440,
    rotation=0, adapter="test",
)
MONITORS = [MAIN, LEFT]

#: A screen barely bigger than the panel, for the case where nothing clears the rectangle.
CRAMPED = [
    winapi.Monitor(
        index=0, device_name="\\\\.\\DISPLAY9", x=0, y=0, width=700, height=130,
        rotation=0, adapter="test",
    )
]


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    created = QtWidgets.QApplication([])
    theme.apply(created)
    return created


class _Catch:
    """Record everything a signal emits, arguments included."""

    def __init__(self, signal: QtCore.SignalInstance) -> None:
        self.calls: list[tuple] = []
        signal.connect(self._hit)

    def _hit(self, *args: object) -> None:
        self.calls.append(args)

    @property
    def count(self) -> int:
        return len(self.calls)


def _press(widget: QtWidgets.QWidget, local: QtCore.QPointF) -> None:
    event = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress,
        local,
        widget.mapToGlobal(local.toPoint()).toPointF(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(widget, event)


def _release(widget: QtWidgets.QWidget, local: QtCore.QPointF) -> None:
    event = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonRelease,
        local,
        widget.mapToGlobal(local.toPoint()).toPointF(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(widget, event)


def _click(widget: QtWidgets.QWidget, local: QtCore.QPointF | None = None) -> None:
    point = local if local is not None else QtCore.QPointF(
        widget.width() / 2, widget.height() / 2
    )
    _press(widget, point)
    _release(widget, point)


def _escape(target: QtWidgets.QWidget) -> None:
    event = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress,
        QtCore.Qt.Key.Key_Escape,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(target, event)


class ToolbarTestCase(unittest.TestCase):
    """A toolbar on a known two-monitor desktop, torn down after every test."""

    def setUp(self) -> None:
        self.app = _app()
        self.document = model.Document()
        self._monitor_patch = mock.patch.object(
            winapi, "list_monitors", return_value=list(MONITORS)
        )
        self._monitor_patch.start()
        self.addCleanup(self._monitor_patch.stop)
        self.toolbar = Toolbar(self.document)
        self.addCleanup(self.toolbar.deleteLater)
        self.addCleanup(self.toolbar.close)
        self.toolbar.show()
        self.app.processEvents()

    def place(self, x: int, y: int) -> None:
        self.toolbar.place(x, y, list(MONITORS))


# ------------------------------------------------------------------ building


class BuildTests(ToolbarTestCase):
    def test_builds_with_every_control(self) -> None:
        bar = self.toolbar
        self.assertTrue(bar.isVisible())
        for tool in (
            model.TOOL_PEN, model.TOOL_MARKER, model.TOOL_ARROW,
            model.TOOL_TEXT, model.TOOL_ERASER,
        ):
            self.assertIn(tool, bar.tool_buttons)
        for widget in (
            bar.grip, bar.mode_button, bar.shapes_button, bar.colour_button,
            bar.width_chip, bar.undo_button, bar.redo_button, bar.eye_button,
            bar.clear_button, bar.collapse_button,
        ):
            self.assertIsNotNone(widget)

    def test_paints_in_every_state(self) -> None:
        bar = self.toolbar
        states = []
        for tool in model.ALL_TOOLS:
            bar.set_tool(tool)
            for mode in (toolbar_module.MODE_PASSTHROUGH, toolbar_module.MODE_DRAW):
                bar.set_mode(mode)
                states.append(bar.grab())
        bar.set_history(False, False)
        states.append(bar.grab())
        bar.set_history(True, True)
        states.append(bar.grab())
        bar.set_hidden_drawings(True)
        states.append(bar.grab())
        bar.set_hidden_drawings(False)
        states.append(bar.grab())
        for pixmap in states:
            self.assertFalse(pixmap.isNull())
            self.assertGreater(pixmap.width(), 300)
            self.assertEqual(pixmap.height(), bar.height())

    def test_grabs_the_collapsed_pill(self) -> None:
        bar = self.toolbar
        wide = bar.width()
        bar.set_collapsed(True)
        self.app.processEvents()
        pixmap = bar.grab()
        self.assertFalse(pixmap.isNull())
        self.assertLess(bar.width(), wide)
        self.assertEqual(pixmap.width(), bar.width())

    def test_grabs_both_popovers(self) -> None:
        bar = self.toolbar
        for tool in (model.TOOL_PEN, model.TOOL_MARKER, model.TOOL_TEXT, model.TOOL_ERASER):
            bar.set_tool(tool)
            bar.settings_popover.open_for(tool, bar.colour_button)
            self.app.processEvents()
            pixmap = bar.settings_popover.grab()
            self.assertFalse(pixmap.isNull(), tool)
            self.assertGreater(pixmap.width(), 100, tool)
            bar.settings_popover.close_popover()
        bar.shapes_popover.open_under(bar.shapes_button)
        self.app.processEvents()
        self.assertFalse(bar.shapes_popover.grab().isNull())
        bar.shapes_popover.close_popover()

    def test_every_glyph_the_toolbar_asks_for_is_known(self) -> None:
        image = QtGui.QPixmap(32, 32)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(image)
        try:
            wanted = set(toolbar_module.TOOL_GLYPHS.values()) | {
                "shapes", "undo", "redo", "eye", "eye_off", "dots",
                "cursor", "trash", "chevron_down", "chevron_up", "chevron_right",
            }
            for kind in sorted(wanted):
                self.assertTrue(
                    toolbar_module._glyph(
                        painter, kind, QtCore.QRectF(0, 0, 32, 32), "#FFFFFF"
                    ),
                    f"{kind} draws nothing",
                )
            self.assertFalse(
                toolbar_module._glyph(
                    painter, "no_such_glyph", QtCore.QRectF(0, 0, 32, 32), "#FFFFFF"
                )
            )
        finally:
            painter.end()


# ------------------------------------------------------------------- signals


class SignalTests(ToolbarTestCase):
    def test_tool_buttons_emit_tool_changed(self) -> None:
        caught = _Catch(self.toolbar.tool_changed)
        for tool in (
            model.TOOL_PEN, model.TOOL_MARKER, model.TOOL_ARROW,
            model.TOOL_TEXT, model.TOOL_ERASER,
        ):
            _click(self.toolbar.tool_buttons[tool])
        self.assertEqual(
            [call[0] for call in caught.calls],
            [model.TOOL_PEN, model.TOOL_MARKER, model.TOOL_ARROW,
             model.TOOL_TEXT, model.TOOL_ERASER],
        )
        self.assertEqual(self.toolbar.tool(), model.TOOL_ERASER)

    def test_shapes_body_picks_the_shape_and_the_chevron_opens_the_list(self) -> None:
        bar = self.toolbar
        caught = _Catch(bar.tool_changed)
        _click(bar.shapes_button, QtCore.QPointF(10, 16))
        self.assertEqual([c[0] for c in caught.calls], [model.TOOL_RECT])
        self.assertFalse(bar.shapes_popover.isVisible())

        chevron = bar.shapes_button.chevron_rect().center()
        _click(bar.shapes_button, chevron)
        self.app.processEvents()
        self.assertTrue(bar.shapes_popover.isVisible())
        # The chevron opens the list; it does not also pick a tool.
        self.assertEqual([c[0] for c in caught.calls], [model.TOOL_RECT])

        _click(bar.shapes_popover.buttons[model.TOOL_ELLIPSE])
        self.assertEqual(
            [c[0] for c in caught.calls], [model.TOOL_RECT, model.TOOL_ELLIPSE]
        )
        self.assertEqual(bar.tool(), model.TOOL_ELLIPSE)
        self.assertFalse(bar.shapes_popover.isVisible())
        # And the bar's shapes button now shows the ellipse.
        self.assertEqual(
            bar.shapes_button.kind, toolbar_module.TOOL_GLYPHS[model.TOOL_ELLIPSE]
        )

    def test_mode_undo_redo_clear_and_eye(self) -> None:
        bar = self.toolbar
        mode = _Catch(bar.mode_toggled)
        undo = _Catch(bar.undo_requested)
        redo = _Catch(bar.redo_requested)
        clear = _Catch(bar.clear_requested)
        eye = _Catch(bar.visibility_toggled)

        bar.set_history(True, True)
        _click(bar.mode_button)
        _click(bar.undo_button)
        _click(bar.redo_button)
        _click(bar.clear_button)
        _click(bar.eye_button)
        self.assertEqual(
            (mode.count, undo.count, redo.count, clear.count, eye.count),
            (1, 1, 1, 1, 1),
        )
        self.assertEqual(mode.calls[0], ())

    def test_history_buttons_are_disabled_when_there_is_no_history(self) -> None:
        bar = self.toolbar
        undo = _Catch(bar.undo_requested)
        bar.set_history(False, False)
        self.assertFalse(bar.undo_button.isEnabled())
        self.assertFalse(bar.redo_button.isEnabled())
        _click(bar.undo_button)
        self.assertEqual(undo.count, 0)
        bar.set_history(True, False)
        self.assertTrue(bar.undo_button.isEnabled())
        self.assertFalse(bar.redo_button.isEnabled())

    def test_collapse_chevron_emits_collapsed_changed_both_ways(self) -> None:
        bar = self.toolbar
        caught = _Catch(bar.collapsed_changed)
        _click(bar.collapse_button)
        self.app.processEvents()
        self.assertTrue(bar.is_collapsed())
        _click(bar.pill_expand_button)
        self.app.processEvents()
        self.assertFalse(bar.is_collapsed())
        self.assertEqual([c[0] for c in caught.calls], [True, False])

    def test_setters_are_silent(self) -> None:
        """A controller reflecting a signal back must not be able to start a loop."""
        bar = self.toolbar
        caught = [
            _Catch(bar.tool_changed),
            _Catch(bar.mode_toggled),
            _Catch(bar.collapsed_changed),
            _Catch(bar.visibility_toggled),
            _Catch(bar.moved),
        ]
        bar.set_tool(model.TOOL_MARKER)
        bar.set_mode(toolbar_module.MODE_DRAW)
        bar.set_collapsed(True)
        bar.set_hidden_drawings(True)
        bar.place(300, 300, list(MONITORS))
        self.assertEqual([c.count for c in caught], [0, 0, 0, 0, 0])

    def test_a_bad_tool_or_mode_is_refused_in_words(self) -> None:
        bar = self.toolbar
        bar.set_tool(model.TOOL_MARKER)
        with mock.patch.object(toolbar_module, "_reported", set()):
            with mock.patch("builtins.print") as printed:
                bar.set_tool("banana")
                bar.set_mode("sideways")
        self.assertEqual(bar.tool(), model.TOOL_MARKER)
        self.assertEqual(printed.call_count, 2)


# ------------------------------------------------------- settings and popovers


class SettingsTests(ToolbarTestCase):
    def test_the_eight_swatches_are_the_model_palette(self) -> None:
        swatches = self.toolbar.settings_popover.swatches
        self.assertEqual(len(swatches), 8)
        self.assertEqual([s.colour for s in swatches], list(model.PALETTE))

    def test_a_swatch_writes_into_the_document_and_says_which_tool(self) -> None:
        bar = self.toolbar
        caught = _Catch(bar.settings_changed)
        bar.set_tool(model.TOOL_MARKER)
        bar.settings_popover.open_for(model.TOOL_MARKER, bar.colour_button)
        green = next(s for s in bar.settings_popover.swatches if s.colour == "#22C55E")
        _click(green)
        self.assertEqual([c[0] for c in caught.calls], [model.TOOL_MARKER])
        self.assertEqual(self.document.settings_for(model.TOOL_MARKER).colour, "#22C55E")
        # The pen keeps its own colour: settings are per tool, and the toolbar holds none.
        self.assertNotEqual(self.document.settings_for(model.TOOL_PEN).colour, "#22C55E")
        self.assertEqual(bar.colour_button.colour(), "#22C55E")

    def test_thickness_opacity_and_font_size_all_reach_the_document(self) -> None:
        bar = self.toolbar
        caught = _Catch(bar.settings_changed)

        bar.set_tool(model.TOOL_PEN)
        bar.settings_popover.open_for(model.TOOL_PEN, bar.width_chip)
        bar.settings_popover.width_slider.setValue(11)
        self.assertEqual(self.document.settings_for(model.TOOL_PEN).width, 11)
        self.assertEqual(bar.width_chip.text_value(), "11 px")

        bar.set_tool(model.TOOL_MARKER)
        bar.settings_popover.load(model.TOOL_MARKER)
        self.assertTrue(bar.settings_popover.opacity_row.isVisibleTo(bar.settings_popover))
        bar.settings_popover.opacity_slider.setValue(50)
        self.assertAlmostEqual(
            self.document.settings_for(model.TOOL_MARKER).opacity, 0.5, places=6
        )

        bar.set_tool(model.TOOL_TEXT)
        bar.settings_popover.load(model.TOOL_TEXT)
        self.assertTrue(bar.settings_popover.font_row.isVisibleTo(bar.settings_popover))
        bar.settings_popover.font_slider.setValue(40)
        self.assertEqual(self.document.settings_for(model.TOOL_TEXT).font_size, 40)

        self.assertEqual(
            [c[0] for c in caught.calls],
            [model.TOOL_PEN, model.TOOL_MARKER, model.TOOL_TEXT],
        )

    def test_loading_a_tool_does_not_count_as_an_edit(self) -> None:
        bar = self.toolbar
        caught = _Catch(bar.settings_changed)
        for tool in model.ALL_TOOLS:
            bar.settings_popover.load(tool)
        self.assertEqual(caught.count, 0)

    def test_the_eraser_has_no_colour_row(self) -> None:
        bar = self.toolbar
        bar.set_tool(model.TOOL_ERASER)
        bar.settings_popover.load(model.TOOL_ERASER)
        self.assertFalse(
            bar.settings_popover._swatch_row.isVisibleTo(bar.settings_popover)
        )
        self.assertFalse(bar.colour_button.isEnabled())
        # The thickness control stays: that is the eraser's size.
        self.assertTrue(bar.width_chip.isEnabled())

    def test_the_popover_opens_under_its_control_and_closes_on_escape(self) -> None:
        bar = self.toolbar
        self.place(600, 400)
        self.app.processEvents()
        _click(bar.colour_button)
        self.app.processEvents()
        popover = bar.settings_popover
        self.assertTrue(popover.isVisible())
        self.assertIs(popover.anchor(), bar.colour_button)
        anchor_bottom = bar.colour_button.mapToGlobal(
            QtCore.QPoint(0, bar.colour_button.height())
        )
        self.assertGreaterEqual(popover.y(), anchor_bottom.y())
        _escape(bar.colour_button)
        self.assertFalse(popover.isVisible())

    def test_a_click_elsewhere_closes_the_popover(self) -> None:
        bar = self.toolbar
        _click(bar.width_chip)
        self.app.processEvents()
        self.assertTrue(bar.settings_popover.isVisible())
        stranger = QtWidgets.QWidget()
        self.addCleanup(stranger.deleteLater)
        _press(stranger, QtCore.QPointF(1, 1))
        self.assertFalse(bar.settings_popover.isVisible())

    def test_a_click_inside_the_popover_keeps_it_open(self) -> None:
        bar = self.toolbar
        _click(bar.colour_button)
        self.app.processEvents()
        _click(bar.settings_popover.swatches[0])
        self.assertTrue(bar.settings_popover.isVisible())

    def test_clicking_the_anchor_again_closes_it(self) -> None:
        bar = self.toolbar
        _click(bar.colour_button)
        self.app.processEvents()
        self.assertTrue(bar.settings_popover.isVisible())
        _click(bar.colour_button)
        self.assertFalse(bar.settings_popover.isVisible())

    def test_popovers_are_floating_surfaces_so_capture_exclusion_is_not_lost(self) -> None:
        from screenrec.ui import surfaces

        for popover in (self.toolbar.settings_popover, self.toolbar.shapes_popover):
            self.assertIsInstance(popover, surfaces.FloatingSurface)
            self.assertTrue(popover.hide_from_capture)

    def test_the_popover_closes_when_the_panel_collapses(self) -> None:
        bar = self.toolbar
        _click(bar.colour_button)
        self.app.processEvents()
        self.assertTrue(bar.settings_popover.isVisible())
        bar.set_collapsed(True)
        self.assertFalse(bar.settings_popover.isVisible())


# ------------------------------------------------------------- collapsed pill


class CollapsedTests(ToolbarTestCase):
    def test_the_pill_keeps_a_way_out_of_draw_mode(self) -> None:
        bar = self.toolbar
        bar.set_mode(toolbar_module.MODE_DRAW)
        bar.set_collapsed(True)
        self.app.processEvents()

        self.assertTrue(bar.pill_mode_button.isVisibleTo(bar))
        self.assertTrue(bar.pill_mode_button.isEnabled())
        self.assertFalse(bar.mode_button.isVisibleTo(bar))

        caught = _Catch(bar.mode_toggled)
        _click(bar.pill_mode_button)
        self.assertEqual(caught.count, 1)

    def test_the_pill_shows_the_current_tool_and_colour(self) -> None:
        bar = self.toolbar
        bar.set_tool(model.TOOL_ARROW)
        settings = self.document.settings_for(model.TOOL_ARROW).copy()
        settings.colour = "#38BDF8"
        self.document.set_settings(model.TOOL_ARROW, settings)
        bar.set_tool(model.TOOL_ARROW)
        bar.set_collapsed(True)
        self.app.processEvents()
        self.assertEqual(
            bar.pill_tool_button.kind, toolbar_module.TOOL_GLYPHS[model.TOOL_ARROW]
        )
        self.assertEqual(bar.pill_colour_button.colour(), "#38BDF8")
        self.assertTrue(bar.pill_grip.isVisibleTo(bar))

    def test_the_pill_colour_button_opens_the_same_popover(self) -> None:
        bar = self.toolbar
        bar.set_collapsed(True)
        self.app.processEvents()
        _click(bar.pill_colour_button)
        self.app.processEvents()
        self.assertTrue(bar.settings_popover.isVisible())
        self.assertIs(bar.settings_popover.anchor(), bar.pill_colour_button)


# ---------------------------------------------------------------- the tooltips


class TooltipTests(ToolbarTestCase):
    def test_every_tool_names_itself_and_its_shortcut(self) -> None:
        bar = self.toolbar
        for tool, button in bar.tool_buttons.items():
            tip = button.toolTip()
            self.assertIn(toolbar_module.TOOL_LABELS[tool], tip, tool)
            self.assertIn(f"({toolbar_module.SHORTCUTS[tool]})", tip, tool)

    def test_the_other_controls_name_their_shortcut_too(self) -> None:
        bar = self.toolbar
        pairs = [
            (bar.mode_button, "mode"),
            (bar.shapes_button, model.TOOL_RECT),
            (bar.colour_button, "colour"),
            (bar.width_chip, "width"),
            (bar.undo_button, "undo"),
            (bar.redo_button, "redo"),
            (bar.eye_button, "hide"),
            (bar.clear_button, "clear"),
            (bar.collapse_button, "collapse"),
            (bar.pill_mode_button, "mode"),
            (bar.pill_expand_button, "collapse"),
            (bar.pill_colour_button, "colour"),
        ]
        for widget, key in pairs:
            self.assertIn(
                f"({toolbar_module.SHORTCUTS[key]})", widget.toolTip(), key
            )

    def test_the_mode_tooltip_says_what_the_click_will_do(self) -> None:
        bar = self.toolbar
        bar.set_mode(toolbar_module.MODE_PASSTHROUGH)
        self.assertIn("click to draw", bar.mode_button.toolTip())
        bar.set_mode(toolbar_module.MODE_DRAW)
        self.assertIn("work with the application", bar.mode_button.toolTip())


# ------------------------------------------------------- placing and dragging


class GeometryTests(ToolbarTestCase):
    def test_place_and_physical_pos_round_trip_including_a_negative_origin(self) -> None:
        bar = self.toolbar
        self.place(1000, 600)
        self.assertEqual(bar.physical_pos(), (1000, 600))
        # The screen to the left of the primary is at a negative x.
        self.place(-1800, -100)
        self.assertEqual(bar.physical_pos(), (-1800, -100))
        self.assertEqual(bar.home(), (-1800, -100))

    def test_place_keeps_the_panel_on_a_screen(self) -> None:
        bar = self.toolbar
        width, height = bar.physical_size()
        self.place(9000, 9000)
        x, y = bar.physical_pos()
        self.assertLessEqual(x + width, MAIN.x + MAIN.width)
        self.assertLessEqual(y + height, MAIN.y + MAIN.height)

    def test_dragging_the_grip_moves_it_and_emits_moved_once(self) -> None:
        bar = self.toolbar
        self.place(1000, 600)
        caught = _Catch(bar.moved)
        start = QtCore.QPointF(8, 16)
        _press(bar.grip, start)
        move = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseMove,
            QtCore.QPointF(108, 66),
            bar.grip.mapToGlobal(QtCore.QPoint(108, 66)).toPointF(),
            QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(bar.grip, move)
        _release(bar.grip, QtCore.QPointF(108, 66))

        self.assertEqual(bar.physical_pos(), (1100, 650))
        self.assertEqual(caught.count, 1)
        self.assertEqual(caught.calls[0], (1100, 650))
        self.assertEqual(bar.home(), (1100, 650))

    def test_a_press_that_did_not_move_is_not_a_move(self) -> None:
        bar = self.toolbar
        self.place(1000, 600)
        caught = _Catch(bar.moved)
        _press(bar.grip, QtCore.QPointF(8, 16))
        _release(bar.grip, QtCore.QPointF(8, 16))
        self.assertEqual(caught.count, 0)
        self.assertEqual(bar.physical_pos(), (1000, 600))

    def test_dragging_near_an_edge_snaps_to_it(self) -> None:
        bar = self.toolbar
        self.place(1000, 600)
        bar.begin_drag(QtCore.QPoint(1000, 600))
        bar.drag_to(QtCore.QPoint(MAIN.x + 6, 600))
        x, _y = bar.physical_pos()
        self.assertEqual(x, MAIN.x + toolbar_module.bar_geometry.MARGIN)
        bar.end_drag()


# ------------------------------------------------------------------- avoid()


class AvoidTests(ToolbarTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.place(1000, 600)
        self.width, self.height = self.toolbar.physical_size()

    def panel_rect(self) -> tuple[int, int, int, int]:
        x, y = self.toolbar.physical_pos()
        return (x, y, self.width, self.height)

    def assert_clear_of(self, rect: tuple[int, int, int, int]) -> None:
        px, py, pw, ph = self.panel_rect()
        rx, ry, rw, rh = rect
        self.assertFalse(
            toolbar_module._overlaps(px, py, pw, ph, rx, ry, rw, rh),
            f"panel {self.panel_rect()} still overlaps {rect}",
        )

    def assert_on_screen(self) -> None:
        px, py, pw, ph = self.panel_rect()
        self.assertTrue(
            toolbar_module._fully_on_screen(px, py, pw, ph, list(MONITORS)),
            f"panel {self.panel_rect()} is not wholly on one monitor",
        )

    def test_it_clears_the_rectangle_along_the_shortest_axis(self) -> None:
        bar = self.toolbar
        rect = (1200, 610, 100, 100)
        self.assertTrue(bar.avoid(rect, list(MONITORS)))
        self.assert_clear_of(rect)
        self.assert_on_screen()
        # Up is the smallest step here, so x is untouched and y is above the rectangle.
        x, y = bar.physical_pos()
        self.assertEqual(x, 1000)
        self.assertEqual(y, 610 - toolbar_module.AVOID_GAP - self.height)
        self.assertTrue(bar.is_avoiding())

    def test_it_goes_back_to_where_the_user_put_it(self) -> None:
        bar = self.toolbar
        bar.avoid((1200, 610, 100, 100), list(MONITORS))
        self.assertNotEqual(bar.physical_pos(), (1000, 600))
        self.assertTrue(bar.avoid(None, list(MONITORS)))
        self.assertEqual(bar.physical_pos(), (1000, 600))
        self.assertFalse(bar.is_avoiding())

    def test_a_rectangle_that_is_no_longer_in_the_way_also_brings_it_home(self) -> None:
        bar = self.toolbar
        bar.avoid((1200, 610, 100, 100), list(MONITORS))
        self.assertTrue(bar.avoid((50, 50, 60, 60), list(MONITORS)))
        self.assertEqual(bar.physical_pos(), (1000, 600))

    def test_a_rectangle_well_away_moves_nothing(self) -> None:
        bar = self.toolbar
        self.assertFalse(bar.avoid((20, 1200, 80, 80), list(MONITORS)))
        self.assertEqual(bar.physical_pos(), (1000, 600))

    def test_repeated_calls_do_not_walk_it_across_the_desktop(self) -> None:
        bar = self.toolbar
        rect = (1200, 610, 100, 100)
        bar.avoid(rect, list(MONITORS))
        settled = bar.physical_pos()
        for _ in range(5):
            bar.avoid(rect, list(MONITORS))
        self.assertEqual(bar.physical_pos(), settled)

    def test_it_will_not_step_off_the_screen_to_take_the_shortest_route(self) -> None:
        """At the top of the screen the short step is up, and up is off the desktop."""
        bar = self.toolbar
        self.place(0, 0)
        rect = (0, 0, 2560, 200)
        self.assertTrue(bar.avoid(rect, list(MONITORS)))
        self.assert_clear_of(rect)
        self.assert_on_screen()
        self.assertEqual(bar.physical_pos()[1], 200 + toolbar_module.AVOID_GAP)

    def test_it_works_on_a_monitor_with_negative_coordinates(self) -> None:
        bar = self.toolbar
        self.place(LEFT.x + 40, LEFT.y + 500)
        rect = (LEFT.x + 200, LEFT.y + 510, 120, 120)
        self.assertTrue(bar.avoid(rect, list(MONITORS)))
        self.assert_clear_of(rect)
        px, py, pw, ph = self.panel_rect()
        self.assertTrue(
            toolbar_module._fully_on_screen(px, py, pw, ph, [LEFT]),
            f"panel {self.panel_rect()} left the negative-origin monitor",
        )
        self.assertTrue(bar.avoid(None, list(MONITORS)))
        self.assertEqual(bar.physical_pos(), (LEFT.x + 40, LEFT.y + 500))

    def test_nowhere_to_go_stays_put_and_says_so(self) -> None:
        bar = self.toolbar
        bar.place(0, 0, list(CRAMPED))
        before = bar.physical_pos()
        with mock.patch.object(toolbar_module, "_reported", set()):
            with mock.patch("builtins.print") as printed:
                moved = bar.avoid((0, 0, 700, 130), list(CRAMPED))
        self.assertFalse(moved)
        self.assertEqual(bar.physical_pos(), before)
        self.assertEqual(printed.call_count, 1)
        self.assertIn("staying where it is", printed.call_args[0][0])

    def test_dragging_while_avoiding_makes_the_new_place_home(self) -> None:
        bar = self.toolbar
        bar.avoid((1200, 610, 100, 100), list(MONITORS))
        self.assertTrue(bar.is_avoiding())
        bar.begin_drag(QtCore.QPoint(*bar.physical_pos()))
        bar.drag_to(QtCore.QPoint(1400, 900))
        bar.end_drag()
        self.assertFalse(bar.is_avoiding())
        self.assertEqual(bar.home(), (1400, 900))
        self.assertFalse(bar.avoid(None, list(MONITORS)))
        self.assertEqual(bar.physical_pos(), (1400, 900))


# -------------------------------------------------------------- two languages


class LanguageTests(ToolbarTestCase):
    """The panel is read in Russian too, and it changes over while it is open."""

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(i18n.set_language, i18n.LANG_EN)

    def test_the_tool_table_is_not_frozen_at_import_time(self) -> None:
        """`TOOL_LABELS` holds the English keys; the words come from `tool_label`."""
        self.assertEqual("Pen", toolbar_module.TOOL_LABELS[model.TOOL_PEN])
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual("Карандаш", toolbar_module.tool_label(model.TOOL_PEN))
        self.assertEqual("Pen", toolbar_module.TOOL_LABELS[model.TOOL_PEN])

    def test_the_shortcut_survives_translation(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(
            "Карандаш (P)", toolbar_module.tool_tip(model.TOOL_PEN)
        )

    def test_switching_rewrites_the_tooltips_of_a_panel_already_built(self) -> None:
        bar = self.toolbar
        self.assertIn("Pen", bar.tool_buttons[model.TOOL_PEN].toolTip())
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()
        self.assertIn("Карандаш", bar.tool_buttons[model.TOOL_PEN].toolTip())
        self.assertIn("(P)", bar.tool_buttons[model.TOOL_PEN].toolTip())
        self.assertIn("Цвет", bar.colour_button.toolTip())
        self.assertIn("Толщина", bar.width_chip.toolTip())
        self.assertIn("Отменить", bar.undo_button.toolTip())
        self.assertIn("Стереть всё", bar.clear_button.toolTip())
        self.assertIn("Свернуть панель", bar.collapse_button.toolTip())
        self.assertIn("Развернуть панель", bar.pill_expand_button.toolTip())
        self.assertIn("переместить", bar.grip.toolTip())

    def test_switching_rewrites_the_state_dependent_tooltips(self) -> None:
        bar = self.toolbar
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()
        bar.set_mode(toolbar_module.MODE_DRAW)
        self.assertIn("Рисование", bar.mode_button.toolTip())
        bar.set_mode(toolbar_module.MODE_PASSTHROUGH)
        self.assertIn("Курсор", bar.mode_button.toolTip())
        bar.set_hidden_drawings(True)
        self.assertIn("Показать рисунки", bar.eye_button.toolTip())
        bar.set_hidden_drawings(False)
        self.assertIn("Скрыть рисунки", bar.eye_button.toolTip())

    def test_the_popovers_follow_the_language_too(self) -> None:
        bar = self.toolbar
        popover = bar.settings_popover
        popover.load(model.TOOL_MARKER)
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()
        self.assertEqual("Маркер", popover._title.text())
        headings = [heading.text() for heading, _source in popover._row_titles]
        self.assertIn("Толщина", headings)
        self.assertIn("Непрозрачность", headings)
        self.assertIn("Размер текста", headings)
        self.assertEqual("Фигура", bar.shapes_popover._title.text())
        self.assertIn(
            "Прямоугольник", bar.shapes_popover.buttons[model.TOOL_RECT].toolTip()
        )

    def test_a_popover_still_shows_the_values_it_showed_before(self) -> None:
        """Re-reading the words must not quietly re-read the numbers as well."""
        bar = self.toolbar
        settings = self.document.settings_for(model.TOOL_PEN).copy()
        settings.width = 11
        self.document.set_settings(model.TOOL_PEN, settings)
        bar.settings_popover.load(model.TOOL_PEN)
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()
        self.assertEqual(11, bar.settings_popover.width_slider.value())
        self.assertEqual("11 px", bar.settings_popover.width_value.text())
        self.assertEqual("11 px", bar.width_chip.text_value())

    def test_switching_the_language_edits_nothing(self) -> None:
        """`retranslate` reads the document; it must never write to it."""
        bar = self.toolbar
        edits: list[str] = []
        bar.settings_changed.connect(edits.append)
        before = self.document.settings_for(model.TOOL_PEN).copy()
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()
        after = self.document.settings_for(model.TOOL_PEN)
        self.assertEqual([], edits)
        self.assertEqual(before.colour, after.colour)
        self.assertEqual(before.width, after.width)

    def test_a_closed_panel_does_not_keep_the_signal_alive(self) -> None:
        """A deleted toolbar must not be called back into when the language changes."""
        bar = Toolbar(model.Document())
        bar.close()
        bar.deleteLater()
        self.app.processEvents()
        i18n.set_language(i18n.LANG_RU)
        self.app.processEvents()  # a dangling connection would raise here


if __name__ == "__main__":
    unittest.main()
