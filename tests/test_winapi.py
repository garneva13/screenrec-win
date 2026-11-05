"""The pure logic in winapi, plus one light live check.

Monitor.contains decides which screen a region belongs to, so its edge behaviour is what
keeps a rectangle dragged along a shared border from landing on the wrong output. Nothing
here needs a second monitor; the single live test is skipped if DXGI is unavailable.
"""
from __future__ import annotations

import unittest

from screenrec import winapi
from screenrec.winapi import Monitor

LANDSCAPE = Monitor(
    index=0, device_name=r"\\.\DISPLAY2", x=0, y=0,
    width=2560, height=1440, rotation=1, adapter="NVIDIA GeForce RTX 3070 Ti",
)
PORTRAIT = Monitor(
    index=1, device_name=r"\\.\DISPLAY1", x=2560, y=0,
    width=1440, height=2560, rotation=2, adapter="NVIDIA GeForce RTX 3070 Ti",
)


class ContainsTest(unittest.TestCase):
    def test_the_top_left_corner_is_inside(self) -> None:
        self.assertTrue(LANDSCAPE.contains(0, 0))
        self.assertTrue(PORTRAIT.contains(2560, 0))

    def test_the_bottom_right_corner_is_outside(self) -> None:
        """Right and bottom are exclusive, as a Windows RECT is."""
        self.assertFalse(LANDSCAPE.contains(2560, 1440))
        self.assertFalse(LANDSCAPE.contains(2560, 0))
        self.assertFalse(LANDSCAPE.contains(0, 1440))

    def test_the_last_pixel_inside_is_inside(self) -> None:
        self.assertTrue(LANDSCAPE.contains(2559, 1439))
        self.assertTrue(PORTRAIT.contains(2560 + 1439, 2559))

    def test_a_point_before_the_left_or_top_edge_is_outside(self) -> None:
        self.assertFalse(LANDSCAPE.contains(-1, 0))
        self.assertFalse(LANDSCAPE.contains(0, -1))
        self.assertFalse(PORTRAIT.contains(2559, 0))

    def test_adjacent_monitors_never_both_claim_a_point(self) -> None:
        for x, y in ((0, 0), (2559, 0), (2560, 0), (2560, 1439), (2560, 2559), (4000, 100)):
            with self.subTest(point=(x, y)):
                claims = [m for m in (LANDSCAPE, PORTRAIT) if m.contains(x, y)]
                self.assertLessEqual(len(claims), 1, f"{x},{y} claimed by {claims}")

    def test_monitor_at_picks_the_owning_screen(self) -> None:
        monitors = [LANDSCAPE, PORTRAIT]
        self.assertIs(winapi.monitor_at(monitors, 10, 10), LANDSCAPE)
        self.assertIs(winapi.monitor_at(monitors, 2559, 1439), LANDSCAPE)
        self.assertIs(winapi.monitor_at(monitors, 2560, 1439), PORTRAIT)
        self.assertIs(winapi.monitor_at(monitors, 3000, 2000), PORTRAIT)

    def test_monitor_at_returns_none_off_the_desktop(self) -> None:
        self.assertIsNone(winapi.monitor_at([LANDSCAPE, PORTRAIT], -100, -100))
        self.assertIsNone(winapi.monitor_at([], 0, 0))


class RotatedTest(unittest.TestCase):
    def test_rotation_values_zero_to_four(self) -> None:
        expected = {0: False, 1: False, 2: True, 3: False, 4: True}
        for rotation, is_rotated in expected.items():
            with self.subTest(rotation=rotation):
                monitor = Monitor(
                    index=0, device_name="d", x=0, y=0, width=100, height=100,
                    rotation=rotation, adapter="a",
                )
                self.assertEqual(monitor.rotated, is_rotated)

    def test_one_hundred_and_eighty_is_not_axis_swapped(self) -> None:
        """A 180-degree screen keeps its width and height, so `rotated` must be False.

        It still needs a filter - `recorder.rotation_filter(3)` supplies one - but the
        duplicated surface has the same shape as the desktop rectangle.
        """
        monitor = Monitor(index=0, device_name="d", x=0, y=0, width=2560, height=1440,
                          rotation=3, adapter="a")
        self.assertFalse(monitor.rotated)


class LabelTest(unittest.TestCase):
    def test_an_unrotated_screen_says_nothing_about_rotation(self) -> None:
        self.assertEqual(LANDSCAPE.label, "0: 2560×1440 at 0,0")
        unspecified = Monitor(index=0, device_name="d", x=0, y=0, width=800, height=600,
                              rotation=0, adapter="a")
        self.assertEqual(unspecified.label, "0: 800×600 at 0,0")

    def test_a_rotated_screen_names_the_angle(self) -> None:
        self.assertEqual(PORTRAIT.label, "1: 1440×2560 at 2560,0, rotated 90°")

    def test_the_other_two_angles(self) -> None:
        for rotation, text in ((3, "180"), (4, "270")):
            with self.subTest(rotation=rotation):
                monitor = Monitor(index=2, device_name="d", x=-1920, y=-200,
                                  width=1920, height=1080, rotation=rotation, adapter="a")
                self.assertEqual(monitor.label, f"2: 1920×1080 at -1920,-200, rotated {text}°")

    def test_the_label_starts_with_the_ddagrab_output_index(self) -> None:
        self.assertTrue(PORTRAIT.label.startswith("1:"))


class WindowInfoTest(unittest.TestCase):
    def test_label_names_the_window_the_process_and_the_size(self) -> None:
        info = winapi.WindowInfo(hwnd=123, title="Блокнот", process="notepad.exe",
                                 x=10, y=20, width=800, height=600)
        self.assertEqual(info.label, "Блокнот  —  notepad.exe  (800×600)")


class LiveMonitorTest(unittest.TestCase):
    """One real DXGI enumeration - read-only, and skipped where DXGI is not available."""

    def test_list_monitors_returns_usable_screens(self) -> None:
        try:
            monitors = winapi.list_monitors()
        except OSError as exc:  # no dxgi.dll, or CreateDXGIFactory1 failed
            raise unittest.SkipTest(f"DXGI is not available here: {exc}")
        if not monitors:
            raise unittest.SkipTest("DXGI reported no attached outputs")

        for monitor in monitors:
            with self.subTest(index=monitor.index):
                self.assertGreater(monitor.width, 0)
                self.assertGreater(monitor.height, 0)
                self.assertGreaterEqual(monitor.index, 0)
                self.assertIn(monitor.rotation, (0, 1, 2, 3, 4))
                self.assertTrue(monitor.device_name)
                self.assertTrue(monitor.label)

    def test_the_dxgi_indices_are_contiguous_from_zero(self) -> None:
        """ddagrab's output_idx is this index, so a gap would mean a wrong screen."""
        try:
            monitors = winapi.list_monitors()
        except OSError as exc:
            raise unittest.SkipTest(f"DXGI is not available here: {exc}")
        if not monitors:
            raise unittest.SkipTest("DXGI reported no attached outputs")
        self.assertEqual([m.index for m in monitors], list(range(len(monitors))))

    def test_cursor_is_on_one_of_the_listed_monitors(self) -> None:
        try:
            monitors = winapi.list_monitors()
        except OSError as exc:
            raise unittest.SkipTest(f"DXGI is not available here: {exc}")
        if not monitors:
            raise unittest.SkipTest("DXGI reported no attached outputs")
        x, y = winapi.cursor_pos()
        # ascii() rather than the labels themselves: Monitor.label contains U+00D7, and a
        # failure message is the one string that must never fail to print (see
        # tests/__init__.py).
        self.assertIsNotNone(
            winapi.monitor_at(monitors, x, y),
            f"the cursor at {x},{y} is on none of "
            f"{ascii([m.label for m in monitors])}",
        )


if __name__ == "__main__":
    unittest.main()
