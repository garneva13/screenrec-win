"""Checks for `screenrec.annotate.canvas`.

Five things here are load-bearing, and each has its own group below.

* **The window is not recreated by a mode switch.** A rebuilt top-level window gets a new
  handle and a new place in the z-order, which in the middle of a recording is a visible
  flicker. `winId()` is compared before and after, both ways round.
* **The dirty rectangle really bounds the stroke, and is small.** The whole object model
  exists so that a mouse move costs one small rectangle instead of a 2560x1440 repaint,
  so the rectangle is checked to contain the stroke and to be a tiny fraction of the
  desktop.
* **A negative origin.** A monitor to the left of the primary one starts at a negative x.
  The monitor list is replaced with fakes for these checks, so the numbers do not depend
  on what is plugged into this machine.
* **Shift constrains**, through the model's own functions and not a second copy of them.
* **Painting never raises**, with every item kind present at once, including a translucent
  marker and multi-line Cyrillic text.

No GPU, no recording and no visible window: the Qt part runs under the offscreen platform
plugin, which this file sets before PySide6 is imported.
"""
from __future__ import annotations

import math
import os
import sys
import unittest
from pathlib import Path

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import winapi  # noqa: E402
from screenrec.annotate import canvas as canvas_module  # noqa: E402
from screenrec.annotate import model  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


def _monitor(index: int, x: int, y: int, width: int, height: int) -> winapi.Monitor:
    return winapi.Monitor(
        index=index,
        device_name=f"\\\\.\\DISPLAY{index + 1}",
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=0,
        adapter="test",
    )


#: A primary screen with a second one to its LEFT, so the desktop union starts at a
#: negative x. This is the arrangement the coordinate code is most likely to get wrong.
LEFT_OF_PRIMARY = [
    _monitor(0, 0, 0, 2560, 1440),
    _monitor(1, -1920, -200, 1920, 1080),
]

#: One ordinary screen at the origin.
SINGLE = [_monitor(0, 0, 0, 2560, 1440)]


class _CanvasCase(unittest.TestCase):
    """Builds a canvas over a fake monitor list, so nothing depends on this machine."""

    monitors: list[winapi.Monitor] = SINGLE

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.monitor_list = list(self.monitors)
        self._real_list_monitors = winapi.list_monitors
        winapi.list_monitors = lambda: list(self.monitor_list)
        self.addCleanup(setattr, winapi, "list_monitors", self._real_list_monitors)

        self.document = model.Document()
        self.canvas = canvas_module.Canvas(self.document)
        self.addCleanup(self.canvas.deleteLater)
        self.addCleanup(self.canvas.hide)

    def press(self, x: float, y: float, modifiers=QtCore.Qt.KeyboardModifier.NoModifier):
        self._send(QtCore.QEvent.Type.MouseButtonPress, x, y, modifiers)

    def move(self, x: float, y: float, modifiers=QtCore.Qt.KeyboardModifier.NoModifier):
        self._send(QtCore.QEvent.Type.MouseMove, x, y, modifiers)

    def release(self, x: float, y: float, modifiers=QtCore.Qt.KeyboardModifier.NoModifier):
        self._send(QtCore.QEvent.Type.MouseButtonRelease, x, y, modifiers)

    def _send(self, kind, x: float, y: float, modifiers) -> None:
        """Post a left-button event at a *widget-local* point."""
        local = QtCore.QPointF(x, y)
        buttons = (
            QtCore.Qt.MouseButton.NoButton
            if kind == QtCore.QEvent.Type.MouseButtonRelease
            else QtCore.Qt.MouseButton.LeftButton
        )
        event = QtGui.QMouseEvent(
            kind,
            local,
            self.canvas.mapToGlobal(local),
            QtCore.Qt.MouseButton.LeftButton,
            buttons,
            modifiers,
        )
        QtWidgets.QApplication.sendEvent(self.canvas, event)


# ------------------------------------------------------- the window identity


class ModeSwitchTest(_CanvasCase):
    def test_switching_modes_does_not_recreate_the_window(self) -> None:
        before = int(self.canvas.winId())
        self.assertEqual(canvas_module.Canvas.MODE_PASSTHROUGH, self.canvas.mode())

        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        in_draw = int(self.canvas.winId())
        self.canvas.set_mode(canvas_module.Canvas.MODE_PASSTHROUGH)
        back = int(self.canvas.winId())

        self.assertEqual(before, in_draw, "the window was recreated entering draw mode")
        self.assertEqual(before, back, "the window was recreated leaving draw mode")
        self.assertEqual("", self.canvas.input_problem)

    def test_switching_modes_while_shown_keeps_the_window_and_its_visibility(self) -> None:
        self.canvas.show()
        before = int(self.canvas.winId())
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.assertEqual(before, int(self.canvas.winId()))
        self.assertTrue(
            self.canvas.isVisible(),
            "setWindowFlags leaves a visible widget hidden; the canvas must re-show it",
        )
        self.canvas.set_mode(canvas_module.Canvas.MODE_PASSTHROUGH)
        self.assertEqual(before, int(self.canvas.winId()))
        self.assertTrue(self.canvas.isVisible())

    def test_the_mode_sets_the_qt_flag_and_the_mouse_attribute(self) -> None:
        transparent = QtCore.Qt.WindowType.WindowTransparentForInput
        attribute = QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents

        self.assertTrue(bool(self.canvas.windowFlags() & transparent))
        self.assertTrue(self.canvas.testAttribute(attribute))

        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.assertFalse(bool(self.canvas.windowFlags() & transparent))
        self.assertFalse(self.canvas.testAttribute(attribute))
        self.assertEqual(
            QtCore.Qt.FocusPolicy.StrongFocus,
            self.canvas.focusPolicy(),
            "Esc has to reach keyPressEvent, so draw mode needs the keyboard",
        )

    def test_an_unknown_mode_is_refused_in_words(self) -> None:
        self.canvas.set_mode("sideways")
        self.assertEqual(canvas_module.Canvas.MODE_PASSTHROUGH, self.canvas.mode())
        self.assertIn("sideways", self.canvas.input_problem)

    def test_the_canvas_is_not_hidden_from_capture(self) -> None:
        # The one surface in the application that must be in the video.
        self.assertFalse(canvas_module.Canvas.hide_from_capture)
        self.assertFalse(hasattr(self.canvas, "excluded_from_capture"))


# ------------------------------------------------------------ the geometry


class NegativeOriginTest(_CanvasCase):
    monitors = LEFT_OF_PRIMARY

    def test_the_canvas_spans_the_union_including_the_negative_part(self) -> None:
        desktop = self.canvas.desktop_rect()
        self.assertEqual(-1920, desktop.x())
        self.assertEqual(-200, desktop.y())
        self.assertEqual(1920 + 2560, desktop.width())
        self.assertEqual(200 + 1440, desktop.height())

    def test_desktop_to_widget_is_offset_by_the_negative_origin(self) -> None:
        # Under the offscreen plugin no QScreen pairs with a fake DXGI output, so the
        # uniform fallback mapping is used - and it says so.
        self.assertIn("uniform coordinate mapping", self.canvas.mapping_problem)

        origin = self.canvas.to_widget(-1920, -200)
        self.assertAlmostEqual(0.0, origin.x())
        self.assertAlmostEqual(0.0, origin.y())

        primary = self.canvas.to_widget(0, 0)
        self.assertAlmostEqual(1920.0, primary.x())
        self.assertAlmostEqual(200.0, primary.y())

        far = self.canvas.to_widget(2559, 1439)
        self.assertAlmostEqual(4479.0, far.x())
        self.assertAlmostEqual(1639.0, far.y())

    def test_widget_to_desktop_is_the_exact_inverse(self) -> None:
        for x, y in ((-1920, -200), (-1, -1), (0, 0), (1000, 900), (2559, 1439)):
            widget = self.canvas.to_widget(x, y)
            point = self.canvas.to_desktop(widget)
            self.assertAlmostEqual(float(x), point.x, msg=f"x of ({x}, {y})")
            self.assertAlmostEqual(float(y), point.y, msg=f"y of ({x}, {y})")

    def test_a_press_on_the_negative_screen_is_stored_in_desktop_pixels(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        # Widget point (100, 50) is desktop (-1820, -150) with this arrangement.
        self.press(100, 50)
        self.release(140, 90)
        items = self.document.items
        self.assertEqual(1, len(items))
        self.assertAlmostEqual(-1820.0, items[0].points[0].x)
        self.assertAlmostEqual(-150.0, items[0].points[0].y)

    def test_a_desktop_rectangle_maps_onto_the_widget(self) -> None:
        rect = self.canvas.widget_rect_for((-1900.0, -180.0, 100.0, 60.0))
        pad = canvas_module.UPDATE_PAD
        self.assertEqual(20 - pad, rect.left())
        self.assertEqual(20 - pad, rect.top())
        self.assertGreaterEqual(rect.width(), 100)
        self.assertGreaterEqual(rect.height(), 60)
        self.assertTrue(self.canvas.rect().contains(rect))


class RefreshGeometryTest(_CanvasCase):
    monitors = LEFT_OF_PRIMARY

    def test_unplugging_a_monitor_reshapes_the_canvas(self) -> None:
        self.assertEqual(-1920, self.canvas.desktop_rect().x())
        self.monitor_list = list(SINGLE)
        self.canvas.refresh_geometry()
        self.assertEqual("", self.canvas.geometry_problem)
        self.assertEqual(QtCore.QRect(0, 0, 2560, 1440), self.canvas.desktop_rect())
        self.assertAlmostEqual(0.0, self.canvas.to_widget(0, 0).x())

    def test_plugging_one_in_reshapes_it_back(self) -> None:
        self.monitor_list = list(SINGLE)
        self.canvas.refresh_geometry()
        self.monitor_list = list(LEFT_OF_PRIMARY)
        self.canvas.refresh_geometry()
        self.assertEqual(-1920, self.canvas.desktop_rect().x())
        self.assertAlmostEqual(1920.0, self.canvas.to_widget(0, 0).x())

    def test_losing_every_monitor_keeps_the_geometry_and_says_so(self) -> None:
        kept = self.canvas.desktop_rect()
        self.monitor_list = []
        self.canvas.refresh_geometry()
        self.assertEqual(kept, self.canvas.desktop_rect(), "a 0x0 canvas draws nothing")
        self.assertIn("No attached monitor", self.canvas.geometry_problem)
        self.assertEqual([], self.canvas.monitors())
        # And it still answers a conversion rather than raising at 60 fps.
        self.canvas.to_widget(0, 0)
        self.canvas.to_desktop(QtCore.QPointF(10.0, 10.0))

    def test_a_monitor_list_that_changes_mid_stroke_does_not_raise(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.press(100, 100)
        self.move(140, 130)
        self.monitor_list = list(SINGLE)
        self.canvas.refresh_geometry()
        self.move(60, 40)
        self.release(60, 40)
        self.assertEqual(1, len(self.document.items))


# -------------------------------------------------------- the dirty rectangle


class DirtyRectangleTest(_CanvasCase):
    monitors = SINGLE

    def test_the_dirty_rectangles_together_bound_the_stroke_and_each_is_tiny(
        self,
    ) -> None:
        """No ink may be left un-repainted, and no single step may be large.

        The rectangles are taken step by step and united here, which is what the screen
        sees: the canvas repaints the new segment on each move, so the stroke is covered
        by the union of those, not by any one of them. Checking only the last one -
        which is what this test did while the canvas handed the document's whole-item
        mark to `update` - cannot tell a correct small rectangle from a missing one.
        """
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.set_tool(model.TOOL_PEN)

        covered = QtCore.QRect()
        self.press(400, 300)
        covered = covered.united(self.canvas.last_update_rect)
        for step in range(1, 21):
            self.move(400 + step * 2, 300 + step)
            rect = self.canvas.last_update_rect
            self.assertFalse(rect.isEmpty(), f"step {step} repainted nothing")
            self.assertLess(rect.width(), 40, f"step {step} dirtied {rect}")
            self.assertLess(rect.height(), 40, f"step {step} dirtied {rect}")
            covered = covered.united(rect)
        self.release(440, 320)
        covered = covered.united(self.canvas.last_update_rect)

        # Between them they contain the whole stroke, with room for the round cap.
        stroke = QtCore.QRect(400, 300, 40, 20)
        self.assertTrue(
            covered.contains(stroke),
            f"{covered} does not contain the stroke {stroke}",
        )

        # And the whole of it is a tiny fraction of the desktop - that is the point.
        desktop = self.canvas.rect()
        share = (covered.width() * covered.height()) / (
            desktop.width() * desktop.height()
        )
        self.assertLess(share, 0.01, f"{covered} is {share:.3%} of {desktop}")
        self.assertLess(covered.width(), 120)
        self.assertLess(covered.height(), 120)

    def test_a_long_stroke_never_dirties_more_than_one_segment(self) -> None:
        """The rectangle must not grow with the stroke.

        Measured before this was fixed, over this 2560x1440 fake monitor: 30x25 at the
        first move, 420x220 at the fortieth, 1220x620 at the hundred-and-twentieth and
        2420x1220 - 80% of the desktop - at the two-hundred-and-fortieth, because
        `Document.extend` marks the whole item's bounding box twice and the canvas
        handed that straight to `update`. The limit below is absolute, not a share of
        the desktop: a share passes on a big screen for the wrong reason.
        """
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.set_tool(model.TOOL_PEN)

        self.press(90, 90)
        widest = QtCore.QRect()
        for step in range(1, 241):
            self.move(90 + step * 10, 90 + step * 5)
            rect = self.canvas.last_update_rect
            if rect.width() * rect.height() > widest.width() * widest.height():
                widest = QtCore.QRect(rect)
        self.release(90 + 240 * 10, 90 + 240 * 5)
        last = self.canvas.last_update_rect

        self.assertLess(widest.width(), 48, f"a 240-step stroke dirtied {widest}")
        self.assertLess(widest.height(), 48, f"a 240-step stroke dirtied {widest}")
        # Committing the stroke is not a reason to repaint all of it either.
        self.assertLess(last.width(), 48, f"the release dirtied {last}")
        self.assertLess(last.height(), 48, f"the release dirtied {last}")
        self.assertEqual(1, len(self.document.items))
        self.assertGreater(len(self.document.items[0].points), 200)

    def test_holding_shift_through_a_pen_stroke_repaints_nothing_extra(self) -> None:
        """Shift autorepeat must not drag the whole stroke's rectangle back in."""
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.set_tool(model.TOOL_PEN)
        shift = QtCore.Qt.KeyboardModifier.ShiftModifier
        self.press(500, 500, shift)
        for step in range(1, 61):
            self.move(500 + step * 8, 500 + step * 4, shift)
        before = QtCore.QRect(self.canvas.last_update_rect)
        for _ in range(5):  # what the keyboard's autorepeat sends
            QtWidgets.QApplication.sendEvent(
                self.canvas,
                QtGui.QKeyEvent(
                    QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Shift, shift
                ),
            )
        self.assertEqual(before, self.canvas.last_update_rect)
        self.release(500 + 60 * 8, 500 + 60 * 4, shift)

    def test_a_dragged_shape_still_repaints_its_whole_extent(self) -> None:
        """The opposite case: a rectangle redrawn from two corners must paint out the
        outline it had a moment ago, so its own extent is the right dirty rectangle."""
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.set_tool(model.TOOL_RECT)
        self.press(300, 300)
        self.move(900, 700)
        rect = self.canvas.last_update_rect
        self.assertTrue(
            rect.contains(QtCore.QRect(300, 300, 600, 400)),
            f"{rect} does not cover the shape being dragged",
        )
        self.release(900, 700)

    def test_one_mouse_move_only_dirties_that_step(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.press(1000, 700)
        self.move(1004, 703)
        rect = self.canvas.last_update_rect
        # Two samples four pixels apart, a width-4 pen and the model's pad of 4.
        self.assertLess(rect.width(), 40, f"one step dirtied {rect}")
        self.assertLess(rect.height(), 40, f"one step dirtied {rect}")
        self.release(1004, 703)

    def test_a_second_stroke_elsewhere_does_not_dirty_the_first(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.press(100, 100)
        self.release(120, 120)
        self.press(2000, 1200)
        self.release(2020, 1220)
        rect = self.canvas.last_update_rect
        self.assertFalse(rect.intersects(QtCore.QRect(90, 90, 40, 40)))

    def test_nothing_to_repaint_gives_an_empty_rectangle(self) -> None:
        self.document.clear_dirty()
        self.assertEqual(QtCore.QRect(), self.canvas.repaint_dirty())

    def test_the_painter_is_clipped_to_the_exposed_rectangle(self) -> None:
        """The clip must survive the desktop-to-widget transform set after it."""
        self.document.add(
            model.Item(
                kind=model.TOOL_PEN,
                points=[model.Point(100, 100), model.Point(300, 100)],
                colour="#FFFFFF",
                width=10,
            )
        )
        image = QtGui.QImage(500, 300, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(image)
        try:
            # Exposed area: only the left half of the stroke.
            self.canvas._paint(painter, QtCore.QRect(0, 0, 200, 300))
        finally:
            painter.end()
        # qAlpha, not QColor(...).alpha(): QColor built from a QRgb drops the alpha and
        # reports 255 for every pixel, which would make this check pass on anything.
        self.assertNotEqual(
            0, QtGui.qAlpha(image.pixel(150, 100)), "inside the exposed area"
        )
        self.assertEqual(
            0, QtGui.qAlpha(image.pixel(250, 100)), "outside the exposed area"
        )


# -------------------------------------------------- hit testing and z-order


#: A small fake monitor, so the windows-plugin checks below put a 320x240 window in a
#: corner rather than an overlay across the whole desktop.
SMALL = [_monitor(0, 40, 40, 320, 240)]


def _require_windows_plugin(case: unittest.TestCase) -> None:
    """Skip unless this is the real windows platform plugin.

    Hit testing and the z-order are properties of a native window, and the offscreen
    plugin has neither an HWND nor a desktop, so these checks cannot run in the headless
    suite. They are kept here because they are the only place the two defects they cover
    are visible at all: everything else about both was green while they were live.
    """
    if QtGui.QGuiApplication.platformName() != "windows":
        case.skipTest(
            "needs the real windows platform plugin: offscreen has no HWND to hit-test"
        )


def _window_from_point(x: int, y: int) -> int:
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    return int(user32.WindowFromPoint(POINT(x, y)) or 0)


class HitTestTest(_CanvasCase):
    """Draw mode must own its own empty pixels."""

    monitors = SMALL

    def test_draw_mode_paints_a_hit_testable_backing(self) -> None:
        # WS_EX_LAYERED is set in both modes, and Windows hit-tests a layered window by
        # its per-pixel alpha, so alpha 0 where nothing is drawn means the press goes to
        # the application underneath. The alpha is the whole mechanism; check the alpha.
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        image = self.canvas.grab().toImage()
        self.assertEqual(
            1,
            QtGui.qAlpha(image.pixel(200, 150)),
            "draw mode left an empty pixel fully transparent: a press there would go "
            "through to the application below instead of starting a stroke",
        )
        self.assertEqual("", self.canvas.paint_problem)

    def test_pass_through_mode_paints_no_backing(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.grab()
        self.canvas.set_mode(canvas_module.Canvas.MODE_PASSTHROUGH)
        image = self.canvas.grab().toImage()
        self.assertEqual(
            0,
            QtGui.qAlpha(image.pixel(200, 150)),
            "pass-through mode must leave the desktop untouched",
        )

    def test_the_backing_is_invisible_next_to_the_ink(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.document.add(
            model.Item(
                kind=model.TOOL_PEN,
                points=[model.Point(60, 60), model.Point(300, 220)],
                colour="#FFFFFF",
                width=16,
            )
        )
        image = self.canvas.grab().toImage()
        # The image is in widget units; this monitor starts at desktop (40, 40), so the
        # stroke's midpoint (180, 140) is at (140, 100) here.
        self.assertGreater(QtGui.qAlpha(image.pixel(140, 100)), 200, "on the ink")
        self.assertEqual(1, QtGui.qAlpha(image.pixel(260, 20)), "off the ink")

    def test_the_whole_window_is_repainted_on_a_mode_change(self) -> None:
        # The backing store only holds what has been painted into it, so entering draw
        # mode has to invalidate all of it - otherwise the parts that happen not to be
        # repainted afterwards stay click-through.
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.assertEqual(self.canvas.rect(), self.canvas.last_update_rect)

    def test_the_window_is_hit_testable_where_nothing_is_drawn(self) -> None:
        _require_windows_plugin(self)
        self.canvas.show()
        QtWidgets.QApplication.processEvents()
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.repaint()
        QtWidgets.QApplication.processEvents()
        try:
            hwnd = int(self.canvas.winId())
            empty = self.canvas.mapToGlobal(QtCore.QPoint(200, 150))
            self.assertEqual(
                hwnd,
                _window_from_point(empty.x(), empty.y()),
                "an empty part of the canvas is click-through in draw mode",
            )
            self.canvas.set_mode(canvas_module.Canvas.MODE_PASSTHROUGH)
            self.canvas.repaint()
            QtWidgets.QApplication.processEvents()
            self.assertNotEqual(
                hwnd,
                _window_from_point(empty.x(), empty.y()),
                "pass-through mode must let the click reach the application below",
            )
        finally:
            self.canvas.hide()


class RaiseTest(_CanvasCase):
    """Entering draw mode may raise this window; reasserting the mode may not."""

    monitors = SMALL

    def setUp(self) -> None:
        super().setUp()
        self.raises: list[int] = []
        self.canvas.raised.connect(lambda: self.raises.append(1))

    def test_entering_draw_mode_announces_the_raise(self) -> None:
        self.canvas.show()
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.assertEqual(
            1,
            len(self.raises),
            "the canvas covers the desktop; whoever owns the toolbar has to be told "
            "that it is now underneath",
        )

    def test_reasserting_the_same_mode_does_not_raise_or_take_focus(self) -> None:
        self.canvas.show()
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.raises.clear()
        self.canvas.clearFocus()

        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)

        self.assertEqual([], self.raises, "a reassert put the canvas over the toolbar")
        self.assertFalse(
            self.canvas.hasFocus(),
            "a reassert took the keyboard back from whatever had it - the text box",
        )
        # It is still a reassert: the styles were applied again without complaint.
        self.assertEqual("", self.canvas.input_problem)
        self.assertEqual(canvas_module.Canvas.MODE_DRAW, self.canvas.mode())

    def test_a_reassert_does_not_repaint_the_whole_window(self) -> None:
        self.canvas.show()
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.last_update_rect = QtCore.QRect()
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.assertEqual(QtCore.QRect(), self.canvas.last_update_rect)

    def test_the_toolbar_can_be_put_back_on_top(self) -> None:
        _require_windows_plugin(self)
        import ctypes
        from ctypes import wintypes

        from screenrec.ui import surfaces

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetTopWindow.argtypes = [wintypes.HWND]
        user32.GetTopWindow.restype = wintypes.HWND
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND

        def depth(hwnd: int) -> int:
            """How far down the z-order a window is; smaller is further in front."""
            current = user32.GetTopWindow(None)
            index = 0
            while current:
                if int(current) == hwnd:
                    return index
                current = user32.GetWindow(current, 2)  # GW_HWNDNEXT
                index += 1
            return 10**6

        toolbar = surfaces.FloatingSurface()
        toolbar.resize(1, 1)
        self.addCleanup(toolbar.deleteLater)
        self.addCleanup(toolbar.hide)
        try:
            self.canvas.show()
            toolbar.show()
            toolbar.raise_()
            QtWidgets.QApplication.processEvents()
            self.assertLess(depth(int(toolbar.winId())), depth(int(self.canvas.winId())))

            self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
            QtWidgets.QApplication.processEvents()
            # The canvas is in front now - that is why `raised` exists.
            self.assertEqual(1, len(self.raises))
            toolbar.raise_()  # what the owner does on that signal
            QtWidgets.QApplication.processEvents()
            self.assertLess(
                depth(int(toolbar.winId())),
                depth(int(self.canvas.winId())),
                "the toolbar could not be put back in front of the canvas",
            )

            # And a reassert must not undo that again.
            self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
            QtWidgets.QApplication.processEvents()
            self.assertLess(
                depth(int(toolbar.winId())),
                depth(int(self.canvas.winId())),
                "a reassert of the same mode buried the toolbar",
            )
        finally:
            toolbar.hide()
            self.canvas.hide()


# ----------------------------------------------------------------- the tools


class ShiftTest(_CanvasCase):
    monitors = SINGLE

    def _drag(self, tool: str, modifiers) -> model.Item:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.set_tool(tool)
        self.press(200, 200, modifiers)
        self.move(500, 340, modifiers)
        self.release(500, 340, modifiers)
        items = self.document.items
        self.assertEqual(1, len(items))
        return items[-1]

    def test_shift_makes_a_rectangle_square(self) -> None:
        free = self._drag(model.TOOL_RECT, QtCore.Qt.KeyboardModifier.NoModifier)
        self.assertAlmostEqual(300.0, free.points[-1].x - free.points[0].x)
        self.assertAlmostEqual(140.0, free.points[-1].y - free.points[0].y)

        self.document = model.Document()
        self.canvas._document = self.document
        square = self._drag(model.TOOL_RECT, QtCore.Qt.KeyboardModifier.ShiftModifier)
        width = square.points[-1].x - square.points[0].x
        height = square.points[-1].y - square.points[0].y
        self.assertAlmostEqual(width, height)
        self.assertAlmostEqual(300.0, width)
        # It is exactly what the model would have said: no second copy of the maths.
        expected = model.constrain_shape(square.points[0], model.Point(500.0, 340.0))
        self.assertAlmostEqual(expected.x, square.points[-1].x)
        self.assertAlmostEqual(expected.y, square.points[-1].y)

    def test_shift_snaps_a_line_to_fifteen_degrees(self) -> None:
        line = self._drag(model.TOOL_LINE, QtCore.Qt.KeyboardModifier.ShiftModifier)
        expected = model.constrain_angle(model.Point(200.0, 200.0), model.Point(500.0, 340.0))
        self.assertAlmostEqual(expected.x, line.points[-1].x)
        self.assertAlmostEqual(expected.y, line.points[-1].y)
        angle = math.degrees(
            math.atan2(
                line.points[-1].y - line.points[0].y,
                line.points[-1].x - line.points[0].x,
            )
        )
        remainder = angle % 15.0
        # Either just above a multiple of 15 or just below the next one.
        self.assertLess(min(remainder, 15.0 - remainder), 1e-6, f"{angle} degrees")

    def test_shift_does_not_constrain_the_pen(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.set_tool(model.TOOL_PEN)
        shift = QtCore.Qt.KeyboardModifier.ShiftModifier
        self.press(100, 100, shift)
        self.move(160, 103, shift)
        self.release(160, 103, shift)
        item = self.document.items[-1]
        self.assertAlmostEqual(160.0, item.points[-1].x)
        self.assertAlmostEqual(103.0, item.points[-1].y)

    def test_releasing_shift_mid_drag_releases_the_snap(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.set_tool(model.TOOL_RECT)
        shift = QtCore.Qt.KeyboardModifier.ShiftModifier
        self.press(200, 200, shift)
        self.move(500, 340, shift)
        # 300 across and 140 down, snapped square: the corner is driven to (500, 500).
        self.assertAlmostEqual(500.0, self.document.pending.points[-1].y)
        # Letting go of Shift without moving the mouse must unsnap the shape.
        QtWidgets.QApplication.sendEvent(
            self.canvas,
            QtGui.QKeyEvent(
                QtCore.QEvent.Type.KeyRelease,
                QtCore.Qt.Key.Key_Shift,
                QtCore.Qt.KeyboardModifier.NoModifier,
            ),
        )
        self.assertAlmostEqual(340.0, self.document.pending.points[-1].y)


class ToolTest(_CanvasCase):
    monitors = SINGLE

    def test_the_text_tool_asks_for_an_editor_and_draws_nothing(self) -> None:
        seen: list[tuple[int, int]] = []
        self.canvas.text_requested.connect(lambda x, y: seen.append((x, y)))
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.canvas.set_tool(model.TOOL_TEXT)
        self.press(640, 480)
        self.release(640, 480)
        self.assertEqual([(640, 480)], seen)
        self.assertTrue(self.document.is_empty)

    def test_a_finished_stroke_is_announced(self) -> None:
        seen: list[model.Item] = []
        self.canvas.stroke_finished.connect(seen.append)
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.press(10, 10)
        self.move(60, 40)
        self.release(60, 40)
        self.assertEqual(1, len(seen))
        self.assertEqual(model.TOOL_PEN, seen[0].kind)

    def test_the_eraser_removes_what_it_touches_and_nothing_else(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.press(100, 100)
        self.move(300, 100)
        self.release(300, 100)
        self.press(100, 600)
        self.move(300, 600)
        self.release(300, 600)
        self.assertEqual(2, len(self.document.items))

        self.canvas.set_tool(model.TOOL_ERASER)
        self.press(200, 101)
        self.release(200, 101)
        self.assertEqual(1, len(self.document.items))
        self.assertAlmostEqual(600.0, self.document.items[0].points[0].y)

    def test_escape_in_draw_mode_reports_and_drops_the_stroke(self) -> None:
        seen: list[int] = []
        self.canvas.escaped.connect(lambda: seen.append(1))
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.press(100, 100)
        self.move(200, 200)
        self.assertIsNotNone(self.document.pending)
        QtWidgets.QApplication.sendEvent(
            self.canvas,
            QtGui.QKeyEvent(
                QtCore.QEvent.Type.KeyPress,
                QtCore.Qt.Key.Key_Escape,
                QtCore.Qt.KeyboardModifier.NoModifier,
            ),
        )
        self.assertEqual([1], seen)
        self.assertIsNone(self.document.pending)
        self.assertTrue(self.document.is_empty)

    def test_pass_through_mode_draws_nothing_from_a_press(self) -> None:
        self.press(100, 100)
        self.move(200, 200)
        self.release(200, 200)
        self.assertTrue(self.document.is_empty)

    def test_an_unknown_tool_is_refused_in_words(self) -> None:
        self.canvas.set_tool("crayon")
        self.assertEqual(model.TOOL_PEN, self.canvas.tool())
        self.assertIn("crayon", self.canvas.input_problem)


# ------------------------------------------------------------------ painting


class PaintTest(_CanvasCase):
    monitors = SINGLE

    def _populate(self) -> None:
        add = self.document.add
        add(
            model.Item(
                kind=model.TOOL_PEN,
                points=[model.Point(100 + i * 7, 100 + (i % 3) * 5) for i in range(30)],
                colour="#F2564B",
                width=4,
            )
        )
        add(
            model.Item(
                kind=model.TOOL_MARKER,
                points=[model.Point(300 + i * 9, 200) for i in range(20)],
                colour="#FACC15",
                width=16,
                opacity=0.35,
            )
        )
        add(
            model.Item(
                kind=model.TOOL_LINE,
                points=[model.Point(100, 400), model.Point(500, 460)],
                colour="#22C55E",
                width=6,
            )
        )
        add(
            model.Item(
                kind=model.TOOL_ARROW,
                points=[model.Point(600, 400), model.Point(900, 520)],
                colour="#38BDF8",
                width=2,
            )
        )
        add(
            model.Item(
                kind=model.TOOL_RECT,
                points=[model.Point(1000, 100), model.Point(1300, 300)],
                colour="#FFFFFF",
                width=10,
            )
        )
        add(
            model.Item(
                kind=model.TOOL_ELLIPSE,
                points=[model.Point(1400, 100), model.Point(1700, 300)],
                colour="#A78BFA",
                width=4,
            )
        )
        add(
            model.Item(
                kind=model.TOOL_TEXT,
                points=[model.Point(200, 700)],
                text="Привет, мир\nвторая строка\nthird line",
                colour="#FFFFFF",
                font_size=32,
            )
        )
        # Degenerate shapes the model can produce mid-drag: one point only.
        add(model.Item(kind=model.TOOL_RECT, points=[model.Point(50, 50)], width=4))
        add(model.Item(kind=model.TOOL_PEN, points=[model.Point(60, 60)], width=4))
        add(
            model.Item(
                kind=model.TOOL_ARROW,
                points=[model.Point(70, 70), model.Point(70, 70)],
                width=4,
            )
        )
        add(model.Item(kind=model.TOOL_TEXT, points=[model.Point(80, 80)], text=""))

    def test_painting_every_kind_at_once_does_not_raise(self) -> None:
        self._populate()
        self.canvas.resize(2560, 1440)
        pixmap = self.canvas.grab()
        self.assertFalse(pixmap.isNull())
        self.assertEqual("", self.canvas.paint_problem)

    def test_a_stroke_in_progress_is_painted_too(self) -> None:
        self.canvas.set_mode(canvas_module.Canvas.MODE_DRAW)
        self.press(100, 100)
        self.move(300, 100)
        self.assertIsNotNone(self.document.pending)
        self.canvas.grab()
        self.assertEqual("", self.canvas.paint_problem)

    def test_a_hidden_document_paints_nothing_and_does_not_raise(self) -> None:
        self._populate()
        self.document.set_hidden(True)
        self.canvas.grab()
        self.assertEqual("", self.canvas.paint_problem)

    def test_the_marker_is_translucent(self) -> None:
        self.document.add(
            model.Item(
                kind=model.TOOL_MARKER,
                points=[model.Point(20, 60), model.Point(200, 60)],
                colour="#FFFFFF",
                width=16,
                opacity=0.35,
            )
        )
        image = QtGui.QImage(300, 120, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(image)
        try:
            self.canvas._paint(painter, QtCore.QRect(0, 0, 300, 120))
        finally:
            painter.end()
        alpha = QtGui.qAlpha(image.pixel(100, 60))
        self.assertGreater(alpha, 0, "the marker was not drawn at all")
        self.assertLess(alpha, 200, "the marker is opaque; what is under it is lost")
        # 0.35 of 255 is 89; allow for the antialiased edge by sampling the middle.
        self.assertAlmostEqual(89, alpha, delta=3)

    def test_a_colour_qt_cannot_read_is_reported_not_swallowed(self) -> None:
        self.document.add(
            model.Item(
                kind=model.TOOL_PEN,
                points=[model.Point(10, 10), model.Point(80, 80)],
                colour="not a colour",
                width=4,
            )
        )
        self.canvas.grab()
        self.assertIn("not a colour", self.canvas.paint_problem)


if __name__ == "__main__":
    unittest.main()
