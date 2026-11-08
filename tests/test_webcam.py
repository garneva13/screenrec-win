"""The camera in the corner of the recording.

What is checked here is the part that has to be right whatever hardware is present:
where the picture goes, which camera is chosen, and that nothing raises when there is
no camera at all. Whether a real camera delivers frames is not a unit test - it was
measured on 2026-11-08 with a c922 (106 frames in 6 s) and, more to the point, by
recording the screen twice, with the overlay and without, and comparing the corner:
100 % of the sampled pixels differed, so the overlay really does reach the file.
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

from screenrec import config, webcam  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


AREA = QtCore.QRect(0, 0, 2000, 1000)


class ConstantsTest(unittest.TestCase):
    """`config` spells the corners and sizes out a second time, on purpose: it must not
    import a module that needs QtMultimedia just to validate a string. The price of
    that is that the two can drift, so they are pinned here."""

    def test_the_corners_agree(self) -> None:
        self.assertEqual(tuple(config.WEBCAM_CORNERS), webcam.CORNERS)

    def test_the_sizes_agree(self) -> None:
        self.assertEqual(tuple(config.WEBCAM_SIZES), webcam.SIZES)
        self.assertEqual(set(config.WEBCAM_SIZES), set(webcam.SIZE_FRACTIONS))

    def test_the_shapes_agree(self) -> None:
        self.assertEqual(tuple(config.WEBCAM_SHAPES), webcam.SHAPES)

    def test_the_defaults_are_ones_that_exist(self) -> None:
        self.assertIn(config.DEFAULT_WEBCAM_CORNER, webcam.CORNERS)
        self.assertIn(config.DEFAULT_WEBCAM_SIZE, webcam.SIZES)
        self.assertIn(config.DEFAULT_WEBCAM_SHAPE, webcam.SHAPES)


class PlacementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.view = webcam.WebcamOverlay()
        self.view.set_area(AREA)
        self.addCleanup(self.view.deleteLater)

    def test_every_corner_lands_inside_the_recorded_area(self) -> None:
        for corner in webcam.CORNERS:
            for size in webcam.SIZES:
                with self.subTest(corner=corner, size=size):
                    self.view.set_placement(corner, size)
                    box = self.view.picture_rect()
                    self.assertTrue(
                        AREA.contains(box),
                        f"{box} is not inside the recorded {AREA}",
                    )

    def test_each_corner_is_the_corner_it_says(self) -> None:
        middle_x = AREA.center().x()
        middle_y = AREA.center().y()
        for corner, left, top in (
            (webcam.CORNER_TOP_LEFT, True, True),
            (webcam.CORNER_TOP_RIGHT, False, True),
            (webcam.CORNER_BOTTOM_LEFT, True, False),
            (webcam.CORNER_BOTTOM_RIGHT, False, False),
        ):
            with self.subTest(corner=corner):
                self.view.set_placement(corner, "medium")
                box = self.view.picture_rect()
                self.assertEqual(box.center().x() < middle_x, left)
                self.assertEqual(box.center().y() < middle_y, top)

    def test_the_sizes_are_in_order_and_are_fractions_of_the_area(self) -> None:
        widths = []
        for size in ("small", "medium", "large"):
            self.view.set_placement(webcam.CORNER_TOP_LEFT, size)
            widths.append(self.view.picture_rect().width())
        self.assertEqual(widths, sorted(widths))
        self.assertLess(widths[-1], AREA.width() // 3, "even 'large' is a corner, not a half")

    def test_a_tiny_area_still_gets_a_usable_picture(self) -> None:
        """A region of a few hundred pixels would give a camera 30 px wide, which is
        not a camera. The floor keeps it visible, even if it then overflows a very
        small region - which is the honest outcome of asking for both."""
        self.view.set_area(QtCore.QRect(0, 0, 400, 300))
        self.view.set_placement(webcam.CORNER_TOP_LEFT, "small")
        self.assertGreaterEqual(self.view.picture_rect().width(), 96)

    def test_an_empty_area_is_ignored_rather_than_taken(self) -> None:
        before = self.view.picture_rect()
        self.view.set_placement(webcam.CORNER_TOP_LEFT, "medium")
        marker = self.view.picture_rect()
        self.view.set_area(QtCore.QRect())
        self.assertEqual(self.view.picture_rect(), marker)
        self.assertNotEqual(before, QtCore.QRect())

    def test_an_unknown_corner_or_size_falls_back(self) -> None:
        self.view.set_placement("sideways", "enormous")
        self.view.set_area(AREA)
        box = self.view.picture_rect()
        self.assertTrue(AREA.contains(box))


class FakeFormat:
    """Enough of a QCameraFormat for `choose_format` to judge."""

    def __init__(self, width: int, height: int, rate: float) -> None:
        self._size = QtCore.QSize(width, height)
        self._rate = rate

    def resolution(self) -> QtCore.QSize:
        return self._size

    def maxFrameRate(self) -> float:
        return self._rate


class FakeDevice:
    def __init__(self, formats) -> None:
        self._formats = formats

    def videoFormats(self):
        return self._formats

    def description(self) -> str:
        return "Fake"

    def isDefault(self) -> bool:
        return True


#: The c922's real list, shortened. The first two are the point of the whole exercise:
#: the largest modes run at 2 fps, measured on 2026-11-08.
C922 = FakeDevice([
    FakeFormat(2304, 1536, 2.0),
    FakeFormat(2304, 1296, 2.0),
    FakeFormat(1920, 1080, 30.0),
    FakeFormat(1280, 720, 60.0),
    FakeFormat(800, 448, 30.0),
    FakeFormat(640, 360, 30.0),
    FakeFormat(320, 180, 30.0),
])


class ChoosingAModeTest(unittest.TestCase):
    def test_the_two_frame_per_second_monsters_are_never_chosen(self) -> None:
        """The reason this function weighs frame rate before size. "Biggest" would
        hand somebody 2 fps in the corner of their lesson."""
        for width in (200, 435, 900, 1500, 4000):
            with self.subTest(width=width):
                chosen = webcam.choose_format(C922, "", width)
                self.assertGreaterEqual(chosen.maxFrameRate(), webcam.USABLE_FRAME_RATE)

    def test_it_takes_the_smallest_mode_that_still_covers_the_picture(self) -> None:
        self.assertEqual(webcam.choose_format(C922, "", 200).resolution().width(), 320)
        self.assertEqual(webcam.choose_format(C922, "", 435).resolution().width(), 640)
        self.assertEqual(webcam.choose_format(C922, "", 614).resolution().width(), 800)

    def test_a_picture_bigger_than_any_mode_gets_the_biggest_usable_one(self) -> None:
        chosen = webcam.choose_format(C922, "", 9000)
        self.assertEqual(chosen.resolution().width(), 1920)
        self.assertGreaterEqual(chosen.maxFrameRate(), webcam.USABLE_FRAME_RATE)

    def test_an_explicit_choice_is_honoured_even_when_it_is_the_slow_one(self) -> None:
        """Somebody who picks 2304x1536 from a list that says "2 fps" has been told."""
        chosen = webcam.choose_format(C922, "2304x1536", 400)
        self.assertEqual(chosen.resolution().width(), 2304)

    def test_a_mode_the_camera_no_longer_has_falls_back_rather_than_failing(self) -> None:
        chosen = webcam.choose_format(C922, "9999x9999", 435)
        self.assertEqual(chosen.resolution().width(), 640)

    def test_a_camera_that_lists_nothing_gives_none(self) -> None:
        self.assertIsNone(webcam.choose_format(FakeDevice([]), "", 400))

    def test_the_list_for_a_person_is_largest_first_and_carries_the_frame_rate(self) -> None:
        modes = webcam.resolutions_of(C922)
        self.assertEqual(modes[0][:2], (2304, 1536))
        self.assertEqual(modes[0][2], 2.0)
        areas = [w * h for w, h, _rate in modes]
        self.assertEqual(areas, sorted(areas, reverse=True))


class ShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.view = webcam.WebcamOverlay()
        self.view.set_area(AREA)
        self.addCleanup(self.view.deleteLater)

    def test_a_circle_is_square(self) -> None:
        self.view.set_placement(webcam.CORNER_TOP_LEFT, "medium", webcam.SHAPE_CIRCLE)
        box = self.view.picture_rect()
        self.assertEqual(box.width(), box.height())

    def test_the_other_two_follow_the_camera_shape(self) -> None:
        for shape in (webcam.SHAPE_ROUNDED, webcam.SHAPE_RECTANGLE):
            with self.subTest(shape=shape):
                self.view.set_placement(webcam.CORNER_TOP_LEFT, "medium", shape)
                box = self.view.picture_rect()
                self.assertNotEqual(box.width(), box.height())

    def test_every_shape_still_lands_inside_the_recorded_area(self) -> None:
        for shape in webcam.SHAPES:
            for corner in webcam.CORNERS:
                with self.subTest(shape=shape, corner=corner):
                    self.view.set_placement(corner, "large", shape)
                    self.assertTrue(AREA.contains(self.view.picture_rect()))

    def test_a_circle_takes_the_middle_square_of_the_frame_not_a_squashed_one(self) -> None:
        """Squashing a 16:9 face into a circle gives a different face."""
        wide = QtGui.QImage(QtCore.QSize(640, 360), QtGui.QImage.Format.Format_ARGB32)
        wide.fill(QtCore.Qt.GlobalColor.blue)
        self.view._image = wide
        self.view.set_placement(webcam.CORNER_TOP_LEFT, "medium", webcam.SHAPE_CIRCLE)
        cropped = self.view._source_rect_for(QtCore.QRectF(self.view.picture_rect()))
        self.assertEqual(cropped.width(), cropped.height())
        self.assertEqual(cropped.width(), 360)

    def test_the_other_shapes_draw_the_whole_frame(self) -> None:
        wide = QtGui.QImage(QtCore.QSize(640, 360), QtGui.QImage.Format.Format_ARGB32)
        wide.fill(QtCore.Qt.GlobalColor.blue)
        self.view._image = wide
        self.view.set_placement(webcam.CORNER_TOP_LEFT, "medium", webcam.SHAPE_ROUNDED)
        self.assertEqual(
            self.view._source_rect_for(QtCore.QRectF(self.view.picture_rect())).size(),
            wide.size(),
        )

    def test_an_unknown_shape_falls_back_to_rounded(self) -> None:
        self.view.set_placement(webcam.CORNER_TOP_LEFT, "medium", "trapezoid")
        box = self.view.picture_rect()
        self.assertNotEqual(box.width(), box.height())


class ChoosingACameraTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()

    def test_no_cameras_means_none(self) -> None:
        self.addCleanup(setattr, webcam, "cameras", webcam.cameras)
        webcam.cameras = lambda: []
        self.assertIsNone(webcam.camera_named("whatever"))

    def test_a_name_that_is_there_is_taken(self) -> None:
        class Fake:
            def __init__(self, name): self._name = name
            def description(self): return self._name
            def isDefault(self): return False

        wanted = Fake("c922 Pro Stream Webcam")
        self.addCleanup(setattr, webcam, "cameras", webcam.cameras)
        webcam.cameras = lambda: [Fake("Integrated Camera"), wanted]
        self.assertIs(webcam.camera_named("c922 Pro Stream Webcam"), wanted)

    def test_a_name_that_is_gone_falls_back_to_the_first(self) -> None:
        """A camera unplugged since the setting was saved must not stop the recording
        having a camera at all."""
        class Fake:
            def __init__(self, name): self._name = name
            def description(self): return self._name
            def isDefault(self): return False

        first = Fake("Integrated Camera")
        self.addCleanup(setattr, webcam, "cameras", webcam.cameras)
        webcam.cameras = lambda: [first]
        self.assertIs(webcam.camera_named("a camera that left"), first)


class FailingSoftlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()

    def test_starting_without_a_camera_says_so_and_does_not_raise(self) -> None:
        self.addCleanup(setattr, webcam, "cameras", webcam.cameras)
        webcam.cameras = lambda: []
        view = webcam.WebcamOverlay()
        self.addCleanup(view.deleteLater)
        problem = view.start()
        self.assertTrue(problem)
        self.assertEqual(problem, view.error)
        self.assertFalse(view.running())

    def test_stop_is_safe_when_nothing_was_started(self) -> None:
        view = webcam.WebcamOverlay()
        self.addCleanup(view.deleteLater)
        view.stop()
        view.stop()
        self.assertFalse(view.running())

    def test_painting_with_no_frame_yet_draws_nothing_and_does_not_raise(self) -> None:
        view = webcam.WebcamOverlay()
        self.addCleanup(view.deleteLater)
        view.set_area(AREA)
        view.resize(AREA.size())
        image = QtGui.QImage(AREA.size(), QtGui.QImage.Format.Format_ARGB32)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        view.render(image)
        self.assertEqual(view.paint_problem, "")

    def test_a_frame_that_cannot_be_converted_is_dropped_quietly(self) -> None:
        """`toImage` is somebody else's code on somebody else's hardware. A frame that
        raises must cost that frame and nothing more."""
        view = webcam.WebcamOverlay()
        self.addCleanup(view.deleteLater)

        class Awkward:
            def toImage(self):
                raise RuntimeError("the camera said no")

        view._on_frame(Awkward())
        self.assertEqual(view.frames_drawn, 0)
        self.assertEqual(view.paint_problem, "")


if __name__ == "__main__":
    unittest.main()
