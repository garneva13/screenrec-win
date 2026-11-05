"""Checks for `screenrec.ui.captureframe`.

The one thing this module must never get wrong is *where* the outline is: anything drawn
inside the captured rectangle is composited onto the desktop and therefore recorded. So
the geometry is checked on all four sides against the rectangle it was given, the
whole-monitor case is checked to draw nothing at all, and painting is exercised in every
state the widget has.

No GPU, no recording and no visible window: the Qt part runs under the offscreen platform
plugin, which this file sets before PySide6 is imported.
"""
from __future__ import annotations

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

from PySide6 import QtCore, QtWidgets  # noqa: E402

from screenrec import winapi  # noqa: E402
from screenrec.ui import captureframe  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


#: A fixed stand-in for a DXGI output, so the checks do not depend on this machine's
#: monitors. The real `Monitor` is frozen and plain, so one can simply be built.
MONITOR = winapi.Monitor(
    index=0,
    device_name="\\\\.\\DISPLAY2",
    x=0,
    y=0,
    width=2560,
    height=1440,
    rotation=0,
    adapter="test",
)

#: A monitor to the right of it, with the desktop turned 90 degrees.
ROTATED = winapi.Monitor(
    index=1,
    device_name="\\\\.\\DISPLAY1",
    x=2560,
    y=0,
    width=1440,
    height=2560,
    rotation=2,
    adapter="test",
)


class CaptureFrameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.frame = captureframe.CaptureFrame()
        self.addCleanup(self.frame.deleteLater)
        self.addCleanup(self.frame.hide_frame)

    # ------------------------------------------------------------ geometry

    def test_window_is_outside_the_rectangle_on_all_four_sides(self) -> None:
        rect = (400, 300, 600, 400)
        self.assertTrue(self.frame.show_for(MONITOR, rect))
        self.assertEqual("", self.frame.problem)

        window = self.frame.window_rect
        x, y, width, height = rect
        self.assertLessEqual(window.left(), x - captureframe.PAD)
        self.assertLessEqual(window.top(), y - captureframe.PAD)
        self.assertGreaterEqual(window.right(), x + width - 1 + captureframe.PAD)
        self.assertGreaterEqual(window.bottom(), y + height - 1 + captureframe.PAD)
        # Under the offscreen plugin every screen is at ratio 1, so logical == physical
        # and the window Qt was actually given is the same rectangle.
        self.assertEqual(window, self.frame.geometry())
        self.assertTrue(self.frame.isVisible())

    def test_the_outline_never_touches_the_captured_rectangle(self) -> None:
        """The stroke lands on the window's outermost pixels, a clear GAP outside."""
        rect = (400, 300, 600, 400)
        self.assertTrue(self.frame.show_for(MONITOR, rect))
        window = self.frame.window_rect
        x, y, width, height = rect
        # Outermost THICKNESS pixels of the window, in desktop coordinates.
        outline_right_edge = window.left() + captureframe.THICKNESS - 1
        self.assertLess(outline_right_edge, x)
        outline_left_edge = window.right() - captureframe.THICKNESS + 1
        self.assertGreater(outline_left_edge, x + width - 1)
        self.assertGreater(
            window.bottom() - captureframe.THICKNESS + 1, y + height - 1
        )

    def test_the_tag_strip_is_outside_too(self) -> None:
        rect = (400, 300, 600, 400)
        self.frame.show_for(MONITOR, rect)
        above = self.frame.window_rect.top()
        self.assertLessEqual(
            above, 300 - captureframe.PAD - captureframe.TAG_HEIGHT - captureframe.TAG_GAP
        )

    def test_a_rectangle_at_the_top_edge_puts_the_tag_below(self) -> None:
        self.assertTrue(self.frame.show_for(MONITOR, (400, 0, 600, 400)))
        window = self.frame.window_rect
        # Nothing of the window may reach into the capture, top edge included.
        self.assertLess(window.top(), 0)
        self.assertGreater(
            window.bottom(), 400 - 1 + captureframe.PAD + captureframe.TAG_HEIGHT
        )

    def test_the_rectangle_is_clipped_to_the_monitor(self) -> None:
        """A rectangle hanging off the screen frames the part that is recorded."""
        self.assertTrue(self.frame.show_for(MONITOR, (2400, 1300, 600, 400)))
        self.assertEqual(
            (2400, 1300, 160, 140),
            (
                self.frame.capture_rect.x(),
                self.frame.capture_rect.y(),
                self.frame.capture_rect.width(),
                self.frame.capture_rect.height(),
            ),
        )

    def test_a_rotated_monitor_is_not_transposed_here(self) -> None:
        """The rectangle is already in desktop coordinates; it is used as it stands."""
        rect = (2600, 100, 400, 800)
        self.assertTrue(self.frame.show_for(ROTATED, rect))
        self.assertEqual(
            (2600, 100, 400, 800),
            (
                self.frame.capture_rect.x(),
                self.frame.capture_rect.y(),
                self.frame.capture_rect.width(),
                self.frame.capture_rect.height(),
            ),
        )
        window = self.frame.window_rect
        self.assertLessEqual(window.left(), 2600 - captureframe.PAD)
        self.assertGreaterEqual(window.right(), 2600 + 400 - 1 + captureframe.PAD)

    # ------------------------------------------------------ pairing with Qt

    def test_an_unpaired_monitor_on_a_scaled_desktop_is_reported(self) -> None:
        """The fallback to physical pixels must never be silent.

        It used to be: `_place` asked for the ratio, got 1.0 because the pairing failed,
        and `_logical_rect` then took its 100% fast path without ever reaching the branch
        that reports the fallback.
        """
        self.frame._screen_for = lambda monitor: None  # type: ignore[assignment]
        self.frame._desktop_is_scaled = lambda: True  # type: ignore[assignment]
        self.assertTrue(self.frame.show_for(MONITOR, (400, 300, 600, 400)))
        self.assertIn("could not be paired", self.frame.problem)

    def test_an_unpaired_monitor_at_100_percent_says_nothing(self) -> None:
        """At 100% the physical rectangle *is* the logical one, so there is no problem."""
        self.frame._screen_for = lambda monitor: None  # type: ignore[assignment]
        self.assertTrue(self.frame.show_for(MONITOR, (400, 300, 600, 400)))
        self.assertEqual("", self.frame.problem)

    def test_two_identical_scaled_screens_are_told_apart_by_origin(self) -> None:
        """A pair of matching monitors at the same scaling is the common ambiguous case."""
        from PySide6 import QtCore, QtGui

        class FakeScreen:
            def __init__(self, geometry: QtCore.QRect, ratio: float, name: str) -> None:
                self._geometry, self._ratio, self._name = geometry, ratio, name

            def geometry(self) -> QtCore.QRect:
                return self._geometry

            def devicePixelRatio(self) -> float:
                return self._ratio

            def name(self) -> str:
                return self._name

        left = FakeScreen(QtCore.QRect(0, 0, 1707, 960), 1.5, "Display 32 QHD")
        right = FakeScreen(QtCore.QRect(1707, 0, 1707, 960), 1.5, "Display 27 WQHD")
        second = winapi.Monitor(
            index=1,
            device_name="\\\\.\\DISPLAY1",
            x=2560,
            y=0,
            width=2560,
            height=1440,
            rotation=0,
            adapter="test",
        )
        original = QtGui.QGuiApplication.screens
        QtGui.QGuiApplication.screens = staticmethod(lambda: [left, right])
        try:
            self.assertIs(right, self.frame._screen_for(second))
            self.assertTrue(self.frame.show_for(second, (2960, 300, 600, 400)))
            self.assertEqual("", self.frame.problem)
            # Logical: the screen's own origin plus the offset into it over the ratio.
            self.assertEqual(QtCore.QRect(1970, 176, 407, 294), self.frame.geometry())
        finally:
            QtGui.QGuiApplication.screens = original

    # -------------------------------------------------------- the full screen

    def _pretend_windows_hides_it(self) -> None:
        """Make `_ensure_excluded` say yes, as it does on a real Windows 10 2004+ desk.

        Patched on the instance, not on `surfaces`: under the offscreen plugin there is
        no HWND to set the affinity on, so the real call can only ever answer no, and the
        inside placement would never be reached in a test.
        """
        def excluded() -> bool:
            self.frame.excluded_from_capture = True
            return True

        self.frame._ensure_excluded = excluded  # type: ignore[method-assign]

    def test_a_whole_monitor_is_framed_from_inside_when_windows_will_hide_it(
        self,
    ) -> None:
        self._pretend_windows_hides_it()
        self.assertTrue(self.frame.show_for(MONITOR, None))
        self.assertTrue(self.frame.isVisible())
        self.assertEqual("", self.frame.problem)
        # The window is the monitor, exactly: the outline is along its own edge.
        self.assertEqual(
            QtCore.QRect(MONITOR.x, MONITOR.y, MONITOR.width, MONITOR.height),
            self.frame.window_rect,
        )
        self.assertEqual(self.frame.window_rect, self.frame.capture_rect)

    def test_a_rectangle_covering_the_monitor_is_framed_from_inside_too(self) -> None:
        self._pretend_windows_hides_it()
        self.assertTrue(self.frame.show_for(MONITOR, (0, 0, 2560, 1440)))
        self.assertEqual(
            QtCore.QRect(0, 0, 2560, 1440), self.frame.window_rect
        )

    def test_the_inside_frame_paints_with_its_tag_inside_the_window(self) -> None:
        self._pretend_windows_hides_it()
        self.assertTrue(self.frame.show_for(MONITOR, None, "Recording screen 1"))
        image = self.frame.grab().toImage()
        self.assertFalse(image.isNull())
        self.assertEqual(MONITOR.width, image.width())

    def test_a_region_is_still_framed_from_outside_even_when_hiding_works(self) -> None:
        """The outside placement is the safer one, so it is not given up where it fits."""
        self._pretend_windows_hides_it()
        self.assertTrue(self.frame.show_for(MONITOR, (400, 300, 600, 400)))
        window = self.frame.window_rect
        self.assertLessEqual(window.left(), 400 - captureframe.PAD)
        self.assertGreaterEqual(window.right(), 400 + 600 - 1 + captureframe.PAD)

    def test_a_whole_monitor_capture_draws_nothing(self) -> None:
        self.assertFalse(self.frame.show_for(MONITOR, None))
        self.assertFalse(self.frame.isVisible())
        self.assertIn("whole", self.frame.problem.lower())
        self.assertTrue(self.frame.capture_rect.isEmpty())

    def test_a_rectangle_covering_the_monitor_draws_nothing(self) -> None:
        self.assertFalse(self.frame.show_for(MONITOR, (0, 0, 2560, 1440)))
        self.assertFalse(self.frame.isVisible())
        self.assertIn("whole", self.frame.problem.lower())

    def test_a_rectangle_larger_than_the_monitor_draws_nothing(self) -> None:
        self.assertFalse(self.frame.show_for(MONITOR, (-10, -10, 4000, 4000)))
        self.assertFalse(self.frame.isVisible())
        self.assertTrue(self.frame.problem)

    def test_a_rectangle_off_the_monitor_is_refused_in_words(self) -> None:
        self.assertFalse(self.frame.show_for(MONITOR, (5000, 5000, 200, 200)))
        self.assertFalse(self.frame.isVisible())
        self.assertIn("does not overlap", self.frame.problem)

    def test_a_malformed_rectangle_is_refused_in_words(self) -> None:
        self.assertFalse(self.frame.show_for(MONITOR, (1, 2, 3)))  # type: ignore[arg-type]
        self.assertFalse(self.frame.isVisible())
        self.assertTrue(self.frame.problem)

    # ------------------------------------------------------------- painting

    def test_it_paints_in_every_state(self) -> None:
        for rect, label in (
            ((400, 300, 600, 400), "Recording"),
            ((400, 0, 600, 400), "Recording"),  # tag below
            ((0, 0, 80, 40), ""),  # no tag at all
            ((2400, 1300, 600, 400), "A very long label that will have to be elided"),
        ):
            with self.subTest(rect=rect, label=label):
                self.assertTrue(self.frame.show_for(MONITOR, rect, label))
                image = self.frame.grab().toImage()
                self.assertFalse(image.isNull())
                self.assertGreater(image.width(), 0)

    def test_painting_while_hidden_raises_nothing(self) -> None:
        self.frame.hide_frame()
        image = self.frame.grab().toImage()
        self.assertFalse(image.isNull())

    # ------------------------------------------------------------ lifecycle

    def test_hide_frame_is_safe_before_anything_is_shown(self) -> None:
        fresh = captureframe.CaptureFrame()
        self.addCleanup(fresh.deleteLater)
        fresh.hide_frame()
        fresh.hide_frame()
        self.assertFalse(fresh.isVisible())
        self.assertTrue(fresh.capture_rect.isEmpty())

    def test_hide_frame_clears_the_rectangle(self) -> None:
        self.frame.show_for(MONITOR, (400, 300, 600, 400))
        self.frame.hide_frame()
        self.assertFalse(self.frame.isVisible())
        self.assertTrue(self.frame.capture_rect.isEmpty())
        self.assertTrue(self.frame.window_rect.isEmpty())

    def test_showing_a_second_rectangle_moves_the_frame(self) -> None:
        self.frame.show_for(MONITOR, (400, 300, 600, 400))
        first = self.frame.window_rect
        self.frame.show_for(MONITOR, (100, 100, 200, 200))
        self.assertNotEqual(first, self.frame.window_rect)
        self.assertLessEqual(self.frame.window_rect.left(), 100 - captureframe.PAD)

    def test_a_refused_rectangle_after_a_shown_one_hides_it(self) -> None:
        self.assertTrue(self.frame.show_for(MONITOR, (400, 300, 600, 400)))
        self.assertFalse(self.frame.show_for(MONITOR, None))
        self.assertFalse(self.frame.isVisible())

    def test_it_never_takes_focus(self) -> None:
        from PySide6 import QtCore

        self.assertEqual(QtCore.Qt.FocusPolicy.NoFocus, self.frame.focusPolicy())
        self.assertTrue(
            self.frame.testAttribute(
                QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents
            )
        )
        self.assertTrue(
            self.frame.testAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        )
        self.assertTrue(
            bool(
                self.frame.windowFlags()
                & QtCore.Qt.WindowType.WindowTransparentForInput
            )
        )


if __name__ == "__main__":
    unittest.main()
