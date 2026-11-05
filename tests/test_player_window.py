"""Checks for `screenrec.player.window`.

The player's whole reason to exist is that a screen which sat on the right appears on
the right, so that is checked first and with real numbers. After that, the things a
viewer would notice within a minute of using it:

* one view per recorded screen, each named;
* `arrange` keeps the arrangement - left of, right of, above, below - and keeps each
  screen's shape, scaled by one factor so the set fits with a margin;
* every keyboard shortcut reaches the engine, with the right argument, including the
  Shift variants and the fallback that turns a relative skip into an absolute seek when
  the engine has no `skip`;
* closing one view closes the rest and stops the engine;
* the transport and a view paint in both the playing and the paused state.

The engine is a stub that records what it was asked to do. It holds real `QVideoSink`s,
because a `ScreenView` connects to `videoFrameChanged` and a duck would not have one.

No GPU, no files, no visible window: the offscreen platform plugin is set before PySide6
is imported.
"""
from __future__ import annotations

import gc
import os
import sys
import unittest
import weakref
from pathlib import Path
from unittest import mock

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtMultimedia, QtTest, QtWidgets  # noqa: E402
# The only way to ask whether a Qt object's C++ side is still there. "Closed" and
# "deleted" are two different states of a `QWidget`, and §1's leak was the first one
# being mistaken for the second.
import shiboken6  # noqa: E402

from screenrec import i18n  # noqa: E402
from screenrec.player import window as player_window  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


#: The machine in docs/CONTRACT.md: a landscape screen with a portrait one to its right.
TWO_SCREENS = [(0, 0, 2560, 1440), (2560, 0, 1440, 2560)]
#: The same two, stacked instead, to check the other axis.
STACKED = [(0, 0, 1920, 1080), (0, 1080, 1920, 1080)]


class StubRecording:
    """What `engine.recording` has to look like from this module's side."""

    def __init__(self, rects: list[tuple[int, int, int, int]]) -> None:
        self.layout_rects = list(rects)
        self.screens = [
            {"file": f"take_screen{index}.mp4", "monitor": index,
             "desktop": list(rect), "primary": rect[0] == 0 and rect[1] == 0}
            for index, rect in enumerate(rects)
        ]


class StubEngine(QtCore.QObject):
    """An engine that plays nothing and remembers everything it was told to do."""

    position_changed = QtCore.Signal(int)
    duration_changed = QtCore.Signal(int)
    playing_changed = QtCore.Signal(bool)
    #: §6's two states, with the names `player.engine.SyncEngine` really publishes.
    #: They are on the stub rather than on a subclass because every arrangement test
    #: has to be able to say "and no seek happened", and an engine that cannot be
    #: asked about seeking would make that assertion weaker than it looks.
    seeking_changed = QtCore.Signal(bool)
    stream_ended = QtCore.Signal(int)

    def __init__(self, rects: list[tuple[int, int, int, int]] | None = None) -> None:
        super().__init__()
        rects = TWO_SCREENS if rects is None else rects
        self.recording = StubRecording(rects)
        self.sinks = [QtMultimedia.QVideoSink() for _ in rects]
        self.position_ms = 14_000
        self.duration_ms = 151_000
        self.is_playing = False
        self.is_muted = False
        self.speed = 1.0
        self.calls: list[tuple] = []
        self._seeking = False
        self._ended: set[int] = set()

    # -- the two §6 states, asked as well as announced

    def seeking(self) -> bool:
        return self._seeking

    def stream_has_ended(self, index: int) -> bool:
        return index in self._ended

    def begin_seeking(self) -> None:
        self._seeking = True
        self.seeking_changed.emit(True)

    def end_seeking(self) -> None:
        self._seeking = False
        self.seeking_changed.emit(False)

    def end_stream(self, index: int) -> None:
        self._ended.add(index)
        self.stream_ended.emit(index)

    # -- commands the window may call

    def toggle_play(self) -> None:
        self.is_playing = not self.is_playing
        self.calls.append(("toggle_play",))

    def step_frames(self, frames: int) -> None:
        self.calls.append(("step_frames", frames))

    def seek(self, milliseconds: int) -> None:
        self.position_ms = int(milliseconds)
        self.calls.append(("seek", int(milliseconds)))

    def skip(self, milliseconds: int) -> None:
        self.calls.append(("skip", int(milliseconds)))

    def set_speed(self, speed: float) -> None:
        self.speed = float(speed)
        self.calls.append(("set_speed", float(speed)))

    def set_muted(self, muted: bool) -> None:
        self.is_muted = bool(muted)
        self.calls.append(("set_muted", bool(muted)))

    def set_volume(self, volume: float) -> None:
        self.calls.append(("set_volume", round(float(volume), 3)))

    def close(self) -> None:
        self.calls.append(("close",))


class SourceEngine(StubEngine):
    """`StubEngine` plus everything `docs/PLAYER_SOURCES.md` adds to the contract.

    A **subclass**, deliberately. The plain `StubEngine` is now also the test for the
    other half of §1 and §2: an engine written before them has no `set_offset` and no
    `set_audio_source`, and the bar must open without a sync button and without an
    audio chip rather than with two controls that do nothing. Every existing test in
    this file goes on using `StubEngine` and goes on meaning what it meant.
    """

    stream_waiting = QtCore.Signal(int, bool)
    offsets_changed = QtCore.Signal()
    audio_changed = QtCore.Signal(int)

    #: 25 fps, so a frame is a round 40 ms and the nudge tests read as arithmetic
    #: rather than as a rounding. `window.FALLBACK_FRAMERATE` is 30, so a test that
    #: passes with this number is also a test that the rate was read at all.
    FRAMERATE = 25.0

    def __init__(self, rects: list[tuple[int, int, int, int]] | None = None) -> None:
        super().__init__(rects)
        self.recording.framerate = self.FRAMERATE
        self._offsets = [0] * len(self.sinks)
        self._waiting: set[int] = set()
        self.audio_index = 0

    # -- §2.1's offsets

    def offsets(self) -> list[int]:
        return list(self._offsets)

    def set_offset(self, index: int, milliseconds: int) -> None:
        self.calls.append(("set_offset", int(index), int(milliseconds)))
        if 0 <= int(index) < len(self._offsets):
            self._offsets[int(index)] = int(milliseconds)
        self.offsets_changed.emit()

    def stream_is_waiting(self, index: int) -> bool:
        return int(index) in self._waiting

    def begin_waiting(self, index: int) -> None:
        self._waiting.add(int(index))
        self.stream_waiting.emit(int(index), True)

    def end_waiting(self, index: int) -> None:
        self._waiting.discard(int(index))
        self.stream_waiting.emit(int(index), False)

    # -- §1.2's audio source

    def set_audio_source(self, index: int) -> None:
        self.calls.append(("set_audio_source", int(index)))
        self.audio_index = int(index)
        self.audio_changed.emit(int(index))


class EngineWithoutSkip(StubEngine):
    """A relative seek is optional in the contract; this engine does not offer one."""

    skip = None


class SilentEngine(StubEngine):
    """An engine with no signals at all, so the window has to poll for the clock."""

    def __init__(self) -> None:
        super().__init__()
        # Shadowing the class attributes with plain values hides the signals from
        # `_Engine.signal`, which is exactly the case being covered.
        self.__dict__["position_changed"] = None
        self.__dict__["duration_changed"] = None
        self.__dict__["playing_changed"] = None


def _key(windows: player_window.PlayerWindows, key: QtCore.Qt.Key,
         shift: bool = False) -> None:
    """Deliver a key press to the transport, through the real event filter."""
    modifiers = (
        QtCore.Qt.KeyboardModifier.ShiftModifier
        if shift
        else QtCore.Qt.KeyboardModifier.NoModifier
    )
    event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, int(key), modifiers)
    QtWidgets.QApplication.sendEvent(windows.transport, event)


class PlayerWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.made: list[player_window.PlayerWindows] = []

    def tearDown(self) -> None:
        for windows in self.made:
            windows.close_all()
        self.app.processEvents()

    def make(self, engine: StubEngine) -> player_window.PlayerWindows:
        windows = player_window.PlayerWindows(engine)
        self.made.append(windows)
        return windows

    # -- the views ---------------------------------------------------------

    def test_one_view_per_screen(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        self.assertEqual(len(windows.views), 2)
        self.assertEqual(
            [view.label.text() for view in windows.views],
            ["Screen 1 - primary", "Screen 2"],
        )
        for view in windows.views:
            self.assertTrue(
                view.windowFlags() & QtCore.Qt.WindowType.FramelessWindowHint
            )

    def test_set_label_renames_the_view(self) -> None:
        windows = self.make(StubEngine())
        windows.views[1].set_label("The portrait one")
        self.assertEqual(windows.views[1].label.text(), "The portrait one")
        self.assertIn("The portrait one", windows.views[1].windowTitle())

    def test_the_label_fades_and_comes_back(self) -> None:
        windows = self.make(StubEngine())
        view = windows.views[0]
        self.assertEqual(view.label.opacity(), 1.0)
        for _ in range(40):  # the fade, stepped by hand instead of waiting on a timer
            view._fade_step()
        self.assertEqual(view.label.opacity(), 0.0)
        # `isHidden`, not `isVisible`: the view itself is not shown in this test, and a
        # child of a hidden parent is never "visible" whatever its own state is.
        self.assertTrue(view.label.isHidden())
        view.wake_label()
        self.assertEqual(view.label.opacity(), 1.0)
        self.assertFalse(view.label.isHidden())

    def test_a_frame_from_the_engines_sink_reaches_the_views_picture(self) -> None:
        """The whole picture path: the engine owns the sink, the view owns the widget."""
        engine = StubEngine()
        windows = self.make(engine)
        view = windows.views[0]
        seen: list[bool] = []
        view.video.videoSink().videoFrameChanged.connect(
            lambda frame: seen.append(frame.isValid())
        )
        shape = QtMultimedia.QVideoFrameFormat(
            QtCore.QSize(16, 9),
            QtMultimedia.QVideoFrameFormat.PixelFormat.Format_ARGB8888,
        )
        engine.sinks[0].setVideoFrame(QtMultimedia.QVideoFrame(shape))
        self.assertEqual(seen, [True], "the engine's frame never reached the view")

    # -- moving and resizing -----------------------------------------------

    def _press(self, view, point: QtCore.QPointF) -> None:
        QtWidgets.QApplication.sendEvent(
            view,
            QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonPress, point,
                view.mapToGlobal(point),
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
            ),
        )

    def _move_to(self, view, point: QtCore.QPointF) -> None:
        QtWidgets.QApplication.sendEvent(
            view,
            QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseMove, point,
                view.mapToGlobal(point),
                QtCore.Qt.MouseButton.NoButton,
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
            ),
        )

    def test_a_view_is_dragged_by_its_body(self) -> None:
        windows = self.make(StubEngine())
        view = windows.views[0]
        view.setGeometry(100, 100, 640, 360)
        self._press(view, QtCore.QPointF(320, 180))
        self._move_to(view, QtCore.QPointF(360, 205))
        self.assertEqual((view.x(), view.y()), (140, 125))

    def test_a_view_is_resized_by_its_edge(self) -> None:
        """The manual fallback, which is what runs when the platform has no handle."""
        windows = self.make(StubEngine())
        view = windows.views[0]
        view.setGeometry(100, 100, 640, 360)
        self._press(view, QtCore.QPointF(2, 180))          # the left edge
        self._move_to(view, QtCore.QPointF(-28, 180))      # pulled 30 px to the left
        self.assertEqual(view.geometry(), QtCore.QRect(70, 100, 670, 360))

    def test_a_resize_is_not_read_as_a_click(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        view = windows.views[0]
        view.setGeometry(100, 100, 640, 360)
        engine.calls.clear()
        self._press(view, QtCore.QPointF(2, 180))
        QtWidgets.QApplication.sendEvent(
            view,
            QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonRelease, QtCore.QPointF(2, 180),
                view.mapToGlobal(QtCore.QPointF(2, 180)),
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.MouseButton.NoButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
            ),
        )
        self.assertEqual(engine.calls, [])

    # -- the arrangement ---------------------------------------------------

    def test_arrange_keeps_left_of_and_right_of(self) -> None:
        engine = StubEngine(TWO_SCREENS)
        windows = self.make(engine)
        windows.arrange()
        left, right = windows.views
        self.assertLess(
            left.x() + left.width(), right.x() + 2,
            f"the right-hand screen must not start before the left one ends: "
            f"{left.geometry()} then {right.geometry()}",
        )
        # The landscape screen stays landscape and the portrait one stays portrait.
        self.assertGreater(left.width(), left.height())
        self.assertGreater(right.height(), right.width())

    def test_arrange_keeps_above_and_below(self) -> None:
        windows = self.make(StubEngine(STACKED))
        windows.arrange()
        top, bottom = windows.views
        self.assertLess(top.y() + top.height(), bottom.y() + 2)
        self.assertEqual(top.x(), bottom.x())

    def test_placements_scale_by_one_factor_and_fit(self) -> None:
        area = QtCore.QRect(0, 0, 1600, 900)
        boxes = player_window.placements(TWO_SCREENS, area)
        margin = player_window.ARRANGE_MARGIN
        union = QtCore.QRect()
        for box in boxes:
            union = union.united(box)
        self.assertLessEqual(union.width(), area.width() - 2 * margin + 1)
        self.assertLessEqual(union.height(), area.height() - 2 * margin + 1)
        self.assertGreaterEqual(union.left(), area.left())
        self.assertLessEqual(union.right(), area.right())
        # One factor for the whole set: both screens are 1440 px tall in the recording's
        # x axis... the landscape is 2560 wide, the portrait 1440, so the ratio holds.
        self.assertAlmostEqual(
            boxes[0].width() / boxes[1].width(), 2560 / 1440, places=1
        )

    def test_placements_never_scale_up(self) -> None:
        boxes = player_window.placements(
            [(0, 0, 320, 200), (320, 0, 320, 200)], QtCore.QRect(0, 0, 4000, 3000)
        )
        self.assertEqual([box.size().toTuple() for box in boxes],
                         [(320, 200), (320, 200)])

    # -- where the transport bar lands -------------------------------------

    #: The user's own desk: a landscape screen with a taller portrait one beside it.
    #: The bottom centre of their union is in the empty corner below the landscape
    #: screen - which is exactly where the bar used to go, on no monitor at all.
    L_SHAPED = [QtCore.QRect(0, 0, 2560, 1400), QtCore.QRect(2560, 0, 1440, 2520)]

    def test_transport_lands_on_a_real_screen_on_an_l_shaped_desk(self) -> None:
        spot = player_window.transport_spot(
            [QtCore.QRect(44, 28, 2504, 1344)],
            self.L_SHAPED,
            QtCore.QSize(760, 52),
        )
        self.assertTrue(
            any(screen.contains(spot) for screen in self.L_SHAPED),
            f"{spot} is not wholly on any screen",
        )

    def test_transport_follows_the_screen_showing_the_first_view(self) -> None:
        spot = player_window.transport_spot(
            [QtCore.QRect(2600, 40, 1360, 2400)],
            self.L_SHAPED,
            QtCore.QSize(760, 52),
        )
        self.assertTrue(self.L_SHAPED[1].contains(spot))
        self.assertLess(abs(spot.center().x() - self.L_SHAPED[1].center().x()), 2)

    def test_transport_sits_at_the_bottom_with_a_margin(self) -> None:
        screen = QtCore.QRect(0, 0, 1920, 1080)
        spot = player_window.transport_spot(
            [QtCore.QRect(0, 0, 1920, 1080)], [screen], QtCore.QSize(760, 52)
        )
        self.assertEqual(spot.bottom(), screen.bottom() - player_window.ARRANGE_MARGIN)

    def test_transport_shrinks_rather_than_hanging_off_a_small_screen(self) -> None:
        screen = QtCore.QRect(0, 0, 640, 60)
        spot = player_window.transport_spot(
            [QtCore.QRect(0, 0, 640, 60)], [screen], QtCore.QSize(760, 52)
        )
        self.assertTrue(screen.contains(spot), f"{spot} hangs off {screen}")

    def test_transport_survives_having_no_screens_at_all(self) -> None:
        spot = player_window.transport_spot([], [], QtCore.QSize(760, 52))
        self.assertEqual(spot.size().toTuple(), (760, 52))

    def test_normalise_rects_takes_the_three_shapes_and_refuses_junk(self) -> None:
        self.assertEqual(
            player_window.normalise_rects([(0, 0, 10, 20)]), [(0, 0, 10, 20)]
        )
        self.assertEqual(
            player_window.normalise_rects([QtCore.QRect(1, 2, 3, 4)]), [(1, 2, 3, 4)]
        )
        self.assertEqual(
            player_window.normalise_rects({1: (5, 0, 1, 1), 0: (0, 0, 1, 1)}),
            [(0, 0, 1, 1), (5, 0, 1, 1)],
        )
        self.assertEqual(player_window.normalise_rects(["nonsense"]), [])
        self.assertEqual(player_window.normalise_rects(None), [])

    # -- the keyboard ------------------------------------------------------

    def test_every_shortcut_reaches_the_engine(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        engine.calls.clear()

        _key(windows, QtCore.Qt.Key.Key_Space)
        _key(windows, QtCore.Qt.Key.Key_Left)
        _key(windows, QtCore.Qt.Key.Key_Right)
        _key(windows, QtCore.Qt.Key.Key_Left, shift=True)
        _key(windows, QtCore.Qt.Key.Key_Right, shift=True)
        _key(windows, QtCore.Qt.Key.Key_Comma)
        _key(windows, QtCore.Qt.Key.Key_Period)
        _key(windows, QtCore.Qt.Key.Key_Home)
        _key(windows, QtCore.Qt.Key.Key_End)

        self.assertEqual(
            engine.calls,
            [
                ("toggle_play",),
                # §7: the arrows skip, with or without Shift, and the commas step.
                ("skip", -1000),
                ("skip", 1000),
                ("skip", -1000),
                ("skip", 1000),
                ("step_frames", -1),
                ("step_frames", 1),
                ("seek", 0),
                ("seek", 151_000),
            ],
        )

    def test_the_keyboard_table_is_the_one_the_stage_follows(self) -> None:
        """§7 has one table for the whole player, not one per half.

        The two halves are separate windows with separate key handlers, so the only
        thing stopping them drifting apart is a test that reads both. `→` meant
        "skip" on the stage and "one frame on" here, and `,` and `.` did nothing here
        at all - so the same key did two different things depending on which window
        the viewer had last clicked on.
        """
        from screenrec.player import stage as player_stage

        keys = (
            QtCore.Qt.Key.Key_Left,
            QtCore.Qt.Key.Key_Right,
            QtCore.Qt.Key.Key_Comma,
            QtCore.Qt.Key.Key_Period,
        )
        engine = StubEngine()
        windows = self.make(engine)
        engine.calls.clear()
        for key in keys:
            _key(windows, key)
        here = list(engine.calls)

        stage = player_stage.PlayerStage(engine)
        self.addCleanup(stage.deleteLater)
        self.addCleanup(stage.close)
        engine.calls.clear()
        for key in keys:
            stage.handle_key(
                QtGui.QKeyEvent(
                    QtCore.QEvent.Type.KeyPress,
                    int(key),
                    QtCore.Qt.KeyboardModifier.NoModifier,
                )
            )
        self.assertEqual(here, engine.calls)
        self.assertEqual(
            here,
            [
                ("skip", -player_window.JUMP_MS),
                ("skip", player_window.JUMP_MS),
                ("step_frames", -1),
                ("step_frames", 1),
            ],
        )

    def test_the_step_buttons_name_the_keys_that_step(self) -> None:
        """A tooltip naming a key that does something else is a control that lies."""
        windows = self.make(StubEngine())
        self.assertEqual(windows.transport.back_button.toolTip(), "One frame back (,)")
        self.assertEqual(
            windows.transport.forward_button.toolTip(), "One frame on (.)"
        )

    def test_a_shortcut_reaches_the_engine_from_a_view_too(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        engine.calls.clear()
        event = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            int(QtCore.Qt.Key.Key_Space),
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(windows.views[1], event)
        self.assertEqual(engine.calls, [("toggle_play",)])

    def test_escape_closes_everything(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        seen: list[int] = []
        windows.closed.connect(lambda: seen.append(1))
        _key(windows, QtCore.Qt.Key.Key_Escape)
        self.assertEqual(seen, [1])
        self.assertFalse(any(view.isVisible() for view in windows.views))
        self.assertIn(("close",), engine.calls)

    def test_an_unknown_key_is_not_swallowed(self) -> None:
        windows = self.make(StubEngine())
        event = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            int(QtCore.Qt.Key.Key_F7),
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        self.assertFalse(windows.handle_key(event))

    def test_skip_falls_back_to_an_absolute_seek(self) -> None:
        engine = EngineWithoutSkip()
        windows = self.make(engine)
        engine.calls.clear()
        _key(windows, QtCore.Qt.Key.Key_Right, shift=True)
        self.assertEqual(engine.calls, [("seek", 15_000)])  # 14 000 + 1 000

    # -- closing -----------------------------------------------------------

    def test_closing_one_view_closes_the_rest(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        windows.show()
        self.assertTrue(all(view.isVisible() for view in windows.views))
        seen: list[int] = []
        windows.closed.connect(lambda: seen.append(1))

        windows.views[0].close()

        self.assertEqual(seen, [1], "closing one view must close the set exactly once")
        self.assertFalse(windows.views[1].isVisible())
        self.assertFalse(windows.transport.isVisible())
        self.assertIn(("close",), engine.calls)

    def test_close_all_is_idempotent(self) -> None:
        windows = self.make(StubEngine())
        seen: list[int] = []
        windows.closed.connect(lambda: seen.append(1))
        windows.close_all()
        windows.close_all()
        self.assertEqual(seen, [1])

    # -- the transport -----------------------------------------------------

    def test_the_clock_reads_as_the_brief_asks(self) -> None:
        windows = self.make(StubEngine())
        self.assertEqual(windows.transport.clock.text(), "00:14 / 02:31")
        self.assertEqual(player_window.format_clock(3_751_000), "1:02:31")

    def test_a_click_on_a_view_plays_and_pauses(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        engine.calls.clear()
        view = windows.views[0]
        middle = QtCore.QPointF(view.width() / 2, view.height() / 2)
        for kind in (QtCore.QEvent.Type.MouseButtonPress,
                     QtCore.QEvent.Type.MouseButtonRelease):
            QtWidgets.QApplication.sendEvent(
                view,
                QtGui.QMouseEvent(
                    kind, middle, view.mapToGlobal(middle),
                    QtCore.Qt.MouseButton.LeftButton,
                    QtCore.Qt.MouseButton.LeftButton,
                    QtCore.Qt.KeyboardModifier.NoModifier,
                ),
            )
        self.assertEqual(engine.calls, [("toggle_play",)])

    def test_the_transport_controls_reach_the_engine(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        transport = windows.transport
        engine.calls.clear()

        transport.play_button.click()
        transport.back_button.click()
        transport.forward_button.click()
        transport.speed_chip.click()          # 1x -> 1.5x
        transport.volume_button.click()       # mute
        transport._scrub_moved(90_000)

        self.assertEqual(
            engine.calls,
            [
                ("toggle_play",),
                ("step_frames", -1),
                ("step_frames", 1),
                ("set_speed", 1.5),
                ("set_muted", True),
                ("seek", 90_000),
            ],
        )
        self.assertEqual(transport.speed_chip.text_value(), "1.5x")
        self.assertEqual(transport.clock.text(), "01:30 / 02:31")

    def test_the_wheel_is_the_volume_and_never_a_silent_scrub(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        transport = windows.transport
        transport.resize(760, player_window.TRANSPORT_HEIGHT)
        transport.set_duration(151_000)
        before = transport.scrubber.value()
        engine.calls.clear()

        def wheel(target: QtWidgets.QWidget) -> QtGui.QWheelEvent:
            point = QtCore.QPointF(target.rect().center())
            return QtGui.QWheelEvent(
                point, target.mapToGlobal(point),
                QtCore.QPoint(0, 0), QtCore.QPoint(0, 120),
                QtCore.Qt.MouseButton.NoButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
                QtCore.Qt.ScrollPhase.NoScrollPhase, False,
            )

        # The scrubber refuses the wheel outright, rather than walking its handle along
        # without telling the engine. (`sendEvent` does not run Qt's own propagation to
        # the parent, so the bar's half is checked separately, below.)
        on_the_slider = wheel(transport.scrubber)
        QtWidgets.QApplication.sendEvent(transport.scrubber, on_the_slider)
        self.assertFalse(on_the_slider.isAccepted())
        self.assertEqual(
            transport.scrubber.value(), before,
            "the wheel moved the scrubber without asking the engine to seek",
        )
        self.assertEqual(engine.calls, [])

        QtWidgets.QApplication.sendEvent(transport, wheel(transport))
        self.assertEqual(engine.calls, [("set_volume", 1.0)])

    def test_the_transport_may_take_focus_and_is_not_hidden_from_capture(self) -> None:
        windows = self.make(StubEngine())
        transport = windows.transport
        self.assertFalse(transport.hide_from_capture)
        self.assertNotEqual(
            transport.focusPolicy(), QtCore.Qt.FocusPolicy.NoFocus
        )
        self.assertFalse(
            transport.testAttribute(
                QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating
            )
        )

    def test_the_clock_is_polled_signals_or_no_signals(self) -> None:
        for engine in (SilentEngine(), StubEngine()):
            with self.subTest(engine=type(engine).__name__):
                windows = self.make(engine)
                self.assertTrue(windows._poll.isActive())
                engine.position_ms = 61_000
                windows._refresh()
                self.assertEqual(windows.transport.clock.text(), "01:01 / 02:31")

    # -- painting ----------------------------------------------------------

    def test_the_transport_paints_in_both_states(self) -> None:
        windows = self.make(StubEngine())
        transport = windows.transport
        transport.resize(760, player_window.TRANSPORT_HEIGHT)
        for playing in (True, False):
            transport.set_playing(playing)
            shot = transport.grab()
            self.assertFalse(
                shot.isNull(), f"the transport painted nothing (playing={playing})"
            )
            self.assertEqual(shot.width(), 760)

    def test_a_view_paints_in_both_states(self) -> None:
        windows = self.make(StubEngine())
        windows.arrange()
        view = windows.views[0]
        for playing in (True, False):
            windows.transport.set_playing(playing)
            shot = view.grab()
            self.assertFalse(
                shot.isNull(), f"the view painted nothing (playing={playing})"
            )
        # And with the label faded out, which is a different paint path.
        for _ in range(40):
            view._fade_step()
        self.assertFalse(view.grab().isNull())

    def test_every_player_glyph_draws(self) -> None:
        image = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QtGui.QPainter(image)
        try:
            for kind in player_window.PLAYER_GLYPHS:
                self.assertTrue(
                    player_window.draw_glyph(
                        painter, kind, QtCore.QRectF(4, 4, 24, 24), "#FFFFFF"
                    ),
                    f"{kind} drew nothing",
                )
            self.assertFalse(
                player_window.draw_glyph(
                    painter, "no_such_glyph", QtCore.QRectF(4, 4, 24, 24), "#FFFFFF"
                )
            )
        finally:
            painter.end()


class RealEngineContractTests(unittest.TestCase):
    """The names this module resolves, checked against the engine that really exists.

    The stub above is written to the contract in the module docstring; this is the other
    half of that promise - a `player.engine.SyncEngine` built here, with no media files,
    so a rename on either side shows up as a failure instead of as a dead control.
    """

    def setUp(self) -> None:
        self.app = _app()
        from screenrec.player import engine as engine_module

        self.engine_module = engine_module
        screens = [
            engine_module.Screen(
                file=Path(f"no_such_take_screen{index}.mp4"), monitor=index,
                desktop=desktop, size=(desktop[2], desktop[3]), rotation=0,
                primary=index == 0,
            )
            for index, desktop in enumerate(TWO_SCREENS)
        ]
        self.recording = engine_module.Recording(
            screens=screens, audio_file=screens[0].file, recorded="",
            folder=Path("."), framerate=60.0,
        )
        self.clock = engine_module.SyncEngine(self.recording)
        self.windows: player_window.PlayerWindows | None = None

    def tearDown(self) -> None:
        if self.windows is not None:
            self.windows.close_all()
        self.app.processEvents()

    def test_layout_rects_is_read_through_its_real_signature(self) -> None:
        self.assertTrue(callable(self.recording.layout_rects))
        self.assertEqual(
            player_window.recording_rects(self.recording),
            [(0, 0, 2560, 1440), (2560, 0, 1440, 2560)],
        )

    def test_every_name_this_module_needs_exists_on_the_engine(self) -> None:
        self.windows = player_window.PlayerWindows(self.clock)
        resolved = self.windows._engine._calls
        self.assertEqual(resolved["toggle"].__name__, "toggle")
        self.assertEqual(resolved["step"].__name__, "step")
        self.assertEqual(resolved["seek"].__name__, "seek")
        self.assertEqual(resolved["set_speed"].__name__, "set_speed")
        self.assertEqual(resolved["set_muted"].__name__, "set_muted")
        self.assertEqual(resolved["set_volume"].__name__, "set_volume")
        # `SyncEngine` has no `close`; `stop` is the one that ends it.
        self.assertEqual(resolved["close"].__name__, "stop")
        # And no relative seek, so the fallback in `_Engine.skip` is the live path.
        self.assertIsNone(resolved["skip"])
        for key in ("position", "duration", "playing"):
            self.assertIsNotNone(
                self.windows._engine.signal(key), f"no {key} signal was resolved"
            )

    def test_the_two_new_six_states_are_resolved_on_the_real_engine(self) -> None:
        """§6's `seeking_changed` and `stream_ended`, as the engine really spells them.

        Both are optional in `_Engine`, so a rename on the engine's side would leave
        the views silently undimmed and unbadged rather than raising. This is the
        check that turns that silence into a failure.
        """
        self.windows = player_window.PlayerWindows(self.clock)
        resolved = self.windows._engine
        self.assertIsNotNone(resolved.signal("seeking"))
        self.assertIsNotNone(resolved.signal("stream_ended"))
        self.assertIsNotNone(resolved._calls["stream_has_ended"])
        self.assertIs(resolved.seeking, False)
        self.assertFalse(resolved.stream_has_ended(0))

    def test_the_sources_contract_is_resolved_on_the_real_engine(self) -> None:
        """`docs/PLAYER_SOURCES.md`'s names, as `player.engine` really spells them.

        Every one of them is optional in `_Engine`, which is what lets this module
        open on an engine that predates the feature - and is also what would let a
        rename go unnoticed, as a sync panel that silently stopped being built. This
        is the check that turns that silence into a failure.
        """
        self.windows = player_window.PlayerWindows(self.clock)
        resolved = self.windows._engine
        self.assertTrue(resolved.can_sync(), "engine.set_offset was not resolved")
        self.assertIsNotNone(resolved.signal("offsets"))
        self.assertIsNotNone(resolved.signal("stream_waiting"))
        self.assertIsNotNone(resolved._calls["stream_is_waiting"])
        self.assertEqual(resolved.offsets(2), [0, 0])
        self.assertFalse(resolved.stream_is_waiting(0))
        # A recorder-made set has no offsets, so the panel opens showing zeroes and
        # the button is not lit. That is the ordinary case and it must stay quiet.
        self.assertFalse(self.windows.transport.sync_button.is_on())

    def test_the_audio_source_call_is_resolved_when_the_engine_has_one(self) -> None:
        """§1.2's control is built only if the clock can really move the sound.

        Skipped rather than failed while `player.engine` has no such call: the engine
        and this window layer are being written in the same round of work, and a
        window test that fails because a sibling has not landed says nothing about
        this module. The moment the call exists, its spelling is checked.
        """
        self.windows = player_window.PlayerWindows(self.clock)
        resolved = self.windows._engine
        if not resolved.can_choose_audio():
            self.assertTrue(
                self.windows.transport.audio_chip.isHidden(),
                "the chip is on the bar although the engine cannot move the sound",
            )
            self.skipTest(
                "player.engine has no set_audio_source / set_audio_index yet, so "
                "§1.2's chip is correctly absent"
            )
        self.assertFalse(self.windows.transport.audio_chip.isHidden())
        self.assertEqual(resolved.audio_index, self.recording.master_index())

    def test_a_real_engine_gives_one_view_per_screen_in_the_right_order(self) -> None:
        self.windows = player_window.PlayerWindows(self.clock)
        self.assertEqual(len(self.windows.views), 2)
        self.assertEqual(len(self.clock.sinks()), 2)
        self.windows.arrange()
        left, right = self.windows.views
        self.assertLess(left.x() + left.width(), right.x() + 2)

    def test_the_shortcuts_land_on_the_real_engines_methods(self) -> None:
        calls: list[tuple] = []
        for name in ("toggle", "step", "seek", "stop"):
            original = getattr(self.clock, name)

            def spy(*args, _name=name, _original=original):
                calls.append((_name,) + args)
                return _original(*args)

            setattr(self.clock, name, spy)

        self.windows = player_window.PlayerWindows(self.clock)
        _key(self.windows, QtCore.Qt.Key.Key_Space)
        _key(self.windows, QtCore.Qt.Key.Key_Period)
        _key(self.windows, QtCore.Qt.Key.Key_Right)
        _key(self.windows, QtCore.Qt.Key.Key_Home)
        _key(self.windows, QtCore.Qt.Key.Key_Escape)

        self.assertEqual(
            calls,
            [
                ("toggle",),
                ("step", 1),
                ("seek", 17),     # the engine's own frame step: one frame at 60 fps
                ("seek", 1000),   # no `skip` on the engine, so an absolute seek
                ("seek", 0),
                ("stop",),
            ],
        )


class SeparateWindowsTestCase(unittest.TestCase):
    """The scaffolding §5's checks share: a set of windows, and an invented desk.

    **The desk is invented rather than read.** This machine has one screen and the
    offscreen plugin reports one 800x600, so every rule about two monitors - the
    "tile on which monitor" choice, one window per monitor, the mapping, and a
    monitor unplugged mid-playback - would otherwise be unverifiable here. The module
    reads the display list through exactly two functions, `screen_areas` and
    `viewer_area`, and both are module-level by name, so replacing them is replacing
    the whole of what this module knows about the desk. Nothing deeper is faked: the
    windows, the geometry and the menu are real.
    """

    def setUp(self) -> None:
        self.app = _app()
        self.made: list[player_window.PlayerWindows] = []

    def tearDown(self) -> None:
        for windows in self.made:
            windows.close_all()
        self.app.processEvents()

    def make(self, engine: StubEngine) -> player_window.PlayerWindows:
        windows = player_window.PlayerWindows(engine)
        self.made.append(windows)
        return windows

    def desk(self, *areas: tuple[int, int, int, int]) -> list[QtCore.QRect]:
        """Pretend the viewer has these monitors until this test ends."""
        rects = [QtCore.QRect(*area) for area in areas]
        union = QtCore.QRect()
        for rect in rects:
            union = union.united(rect)
        previous_areas = player_window.screen_areas
        previous_viewer = player_window.viewer_area
        self.addCleanup(setattr, player_window, "screen_areas", previous_areas)
        self.addCleanup(setattr, player_window, "viewer_area", previous_viewer)
        player_window.screen_areas = lambda: [QtCore.QRect(r) for r in rects]
        player_window.viewer_area = lambda: QtCore.QRect(union)
        return rects


def menu_texts(menu: QtWidgets.QMenu) -> list[str]:
    """Every entry a menu offers, submenus flattened in with their parent's name.

    Flattened because the question the tests ask is "can the viewer reach this", and
    a submenu's title alone does not answer it.
    """
    out: list[str] = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        child = action.menu()
        if child is not None:
            out.append(action.text())
            out.extend(menu_texts(child))
            continue
        out.append(action.text())
    return out


def find_action(menu: QtWidgets.QMenu, text: str) -> QtGui.QAction | None:
    """The first action anywhere in `menu` whose text is exactly `text`."""
    for action in menu.actions():
        child = action.menu()
        if child is not None:
            found = find_action(child, text)
            if found is not None:
                return found
            continue
        if action.text() == text:
            return action
    return None


class ArrangementTests(SeparateWindowsTestCase):
    """§5's four schemes, the monitor they are applied to, and «Вернуть раскладку»."""

    def test_the_four_schemes_cover_the_monitor_with_no_gap_and_no_overlap(
        self,
    ) -> None:
        """The whole point of a scheme: two windows, one monitor, nothing showing.

        A one-pixel strip of desktop between two borderless windows reads as a
        rendering fault on a dark wallpaper and as a bright line down the middle of
        the recording on a light one.
        """
        area = self.desk((0, 0, 2561, 1441))[0]  # odd on both axes, on purpose
        windows = self.make(StubEngine())
        for tiling in player_window.layouts.TILINGS:
            with self.subTest(tiling=tiling):
                self.assertTrue(windows.apply_tiling(tiling))
                first, second = (view.geometry() for view in windows.views)
                self.assertFalse(
                    first.intersects(second), "the two windows overlap"
                )
                covered = first.united(second)
                self.assertEqual(covered, area, "the monitor is not fully covered")
                self.assertEqual(
                    first.width() * first.height()
                    + second.width() * second.height(),
                    area.width() * area.height(),
                    "the two windows do not add up to the monitor",
                )

    def test_a_scheme_is_the_same_arithmetic_as_the_menus_picture(self) -> None:
        """`apply_tiling` and `draw_tiling` both go through `layouts.tile_rects`."""
        area = self.desk((0, 0, 1200, 900))[0]
        windows = self.make(StubEngine())
        windows.apply_tiling(player_window.layouts.TILING_BIG_LEFT)
        self.assertEqual(
            [view.geometry() for view in windows.views],
            player_window.layouts.tile_rects(
                area, player_window.layouts.TILING_BIG_LEFT
            ),
        )

    def test_re_tiling_never_touches_the_engine(self) -> None:
        """§6: changing the arrangement must not seek, reload or restart anything."""
        self.desk((0, 0, 1920, 1080))
        engine = StubEngine()
        windows = self.make(engine)
        engine.is_playing = True
        before = engine.position_ms
        engine.calls.clear()

        for tiling in player_window.layouts.TILINGS:
            windows.apply_tiling(tiling)
        windows.restore_arrangement()
        windows.arrange()
        windows.hide_view(1)
        windows.show_view(1)

        self.assertEqual(engine.calls, [], "an arrangement spoke to the engine")
        self.assertEqual(engine.position_ms, before)
        self.assertTrue(engine.is_playing)

    def test_restore_puts_the_windows_back_after_they_have_been_dragged(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.apply_tiling(player_window.layouts.TILING_EQUAL_LR)
        placed = [QtCore.QRect(view.geometry()) for view in windows.views]

        windows.views[0].move(123, 77)
        windows.views[1].setGeometry(10, 10, 300, 200)
        self.assertNotEqual(
            [view.geometry() for view in windows.views], placed
        )

        windows.restore_arrangement()
        self.assertEqual([view.geometry() for view in windows.views], placed)

    def test_restore_before_any_scheme_puts_back_the_desktop_arrangement(self) -> None:
        """The menu entry works from the first press, not from the second."""
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.arrange()
        placed = [QtCore.QRect(view.geometry()) for view in windows.views]
        windows.views[1].move(4, 4)
        windows.restore_arrangement()
        self.assertEqual([view.geometry() for view in windows.views], placed)
        self.assertEqual(windows.tiling(), "")

    def test_tiling_changed_says_the_id_once_per_change(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        said: list[str] = []
        windows.tiling_changed.connect(said.append)

        windows.apply_tiling(player_window.layouts.TILING_BIG_RIGHT)
        windows.apply_tiling(player_window.layouts.TILING_BIG_RIGHT)  # the same one
        windows.apply_tiling(player_window.layouts.TILING_EQUAL_TB)

        self.assertEqual(
            said,
            [
                player_window.layouts.TILING_BIG_RIGHT,
                player_window.layouts.TILING_EQUAL_TB,
            ],
        )

    def test_the_desktop_arrangement_is_not_announced_as_a_scheme(self) -> None:
        """`config` would reject "" and write its default back, inventing a scheme."""
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.apply_tiling(player_window.layouts.TILING_EQUAL_LR)
        said: list[str] = []
        windows.tiling_changed.connect(said.append)
        windows.arrange()
        self.assertEqual(said, [])
        self.assertEqual(windows.tiling(), "")

    def test_an_unknown_scheme_moves_nothing(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.arrange()
        placed = [QtCore.QRect(view.geometry()) for view in windows.views]
        self.assertFalse(windows.apply_tiling("diagonally"))
        self.assertEqual([view.geometry() for view in windows.views], placed)

    def test_a_scheme_with_one_window_open_gives_it_the_whole_monitor(self) -> None:
        area = self.desk((0, 0, 1920, 1080))[0]
        windows = self.make(StubEngine())
        windows.hide_view(1)
        windows.apply_tiling(player_window.layouts.TILING_EQUAL_LR)
        self.assertEqual(windows.views[0].geometry(), area)

    def test_a_three_screen_recording_is_not_tiled_by_a_two_window_scheme(self) -> None:
        self.desk((0, 0, 1920, 1080))
        engine = StubEngine(
            [(0, 0, 1920, 1080), (1920, 0, 1920, 1080), (3840, 0, 1920, 1080)]
        )
        windows = self.make(engine)
        windows.arrange()
        placed = [QtCore.QRect(view.geometry()) for view in windows.views]
        self.assertFalse(
            windows.apply_tiling(player_window.layouts.TILING_EQUAL_LR)
        )
        self.assertEqual([view.geometry() for view in windows.views], placed)

    # -- which monitor ------------------------------------------------------

    def test_the_scheme_is_applied_to_the_chosen_monitor(self) -> None:
        first, second = self.desk((0, 0, 1920, 1080), (1920, 0, 1600, 900))
        windows = self.make(StubEngine())
        windows.apply_tiling(player_window.layouts.TILING_EQUAL_LR, monitor=1)
        covered = windows.views[0].geometry().united(windows.views[1].geometry())
        self.assertEqual(covered, second)
        self.assertNotEqual(covered, first)

    def test_choosing_a_monitor_re_applies_the_scheme_there(self) -> None:
        _first, second = self.desk((0, 0, 1920, 1080), (1920, 0, 1600, 900))
        windows = self.make(StubEngine())
        windows.apply_tiling(player_window.layouts.TILING_EQUAL_TB, monitor=0)
        windows.set_tiling_monitor(1)
        covered = windows.views[0].geometry().united(windows.views[1].geometry())
        self.assertEqual(covered, second)

    def test_a_monitor_that_is_not_there_is_refused(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.set_tiling_monitor(4)
        self.assertEqual(windows.tiling_area(), QtCore.QRect(0, 0, 1920, 1080))

    def test_a_chosen_monitor_that_has_since_gone_falls_back_to_a_real_one(self) -> None:
        """The stored index is checked against the live list on every use."""
        self.desk((0, 0, 1920, 1080), (1920, 0, 1600, 900))
        windows = self.make(StubEngine())
        windows.set_tiling_monitor(1)
        self.desk((0, 0, 1920, 1080))  # the second monitor is unplugged
        self.assertEqual(windows.tiling_area(), QtCore.QRect(0, 0, 1920, 1080))

    def test_choosing_a_monitor_moves_the_windows_before_a_scheme_is_picked(
        self,
    ) -> None:
        """The state the player opens in is the state a viewer meets the entry in.

        `set_tiling_monitor` used to re-apply only `self._tiling`, which is "" until
        a scheme has been chosen - so "Tile on monitor -> Monitor 2" stored the
        choice and moved nothing, which is exactly what its own docstring forbids.
        """
        _first, second = self.desk((0, 0, 1920, 1080), (1920, 0, 1600, 900))
        windows = self.make(StubEngine())
        windows.arrange()
        self.assertEqual(windows.tiling(), "")
        before = [QtCore.QRect(view.geometry()) for view in windows.views]

        windows.set_tiling_monitor(1)
        after = [view.geometry() for view in windows.views]
        self.assertNotEqual(after, before, "the entry moved nothing")
        for box in after:
            self.assertTrue(second.contains(box), f"{box} is not on monitor 2")

    def test_choosing_a_monitor_does_not_touch_the_engine(self) -> None:
        """§6: it is a move, so no seek and no restart however it is reached."""
        self.desk((0, 0, 1920, 1080), (1920, 0, 1600, 900))
        engine = StubEngine()
        windows = self.make(engine)
        windows.arrange()
        before = engine.position_ms
        engine.calls.clear()
        windows.set_tiling_monitor(1)
        self.assertEqual(engine.calls, [])
        self.assertEqual(engine.position_ms, before)

    def test_a_monitor_that_is_not_there_is_refused_by_apply_tiling_too(self) -> None:
        """Both doors onto the stored index say no to the same value.

        `apply_tiling` used to take it silently, after which `tiling_area` fell back
        on every later tile and the stored index could never be right again.
        """
        area = self.desk((0, 0, 1920, 1080))[0]
        windows = self.make(StubEngine())
        said: list[str] = []
        previous = player_window._report
        self.addCleanup(setattr, player_window, "_report", previous)
        player_window._report = said.append

        self.assertTrue(windows.apply_tiling("equal_lr", monitor=9))
        self.assertTrue(any("no such monitor" in line for line in said), said)
        # And the arrangement went on the monitor in force rather than nowhere.
        self.assertEqual(windows.tiling_area(), area)
        covered = windows.views[0].geometry().united(windows.views[1].geometry())
        self.assertEqual(covered, area)


class ReachableArrangementTests(SeparateWindowsTestCase):
    """§6: `arrange()` may not put a window where `reachable()` says nobody can.

    The desk in every case here is the user's own - a 2560x1440 with the taskbar
    taken off it, and the rotated 1440x2560 beside it - because an L-shaped desk is
    where the union of all the screens has a corner that is on no monitor, and a
    frameless window centred into that corner has no title bar to drag it back by.
    """

    #: The real desk: `availableGeometry`, so the taskbar is already gone.
    REAL_DESK = ((0, 0, 2560, 1400), (2560, 0, 1440, 2560))

    def test_arrange_leaves_nothing_where_rescue_windows_would_move_it(self) -> None:
        """The exact failure: `arrange` made the state `rescue_windows` exists to undo.

        Two stacked 1920x1080 screens scaled into the union of the real desk put the
        second view at 1040,1280 with 20.8 % of itself on any one monitor.
        """
        areas = self.desk(*self.REAL_DESK)
        windows = self.make(StubEngine(STACKED))
        windows.arrange()
        for index, view in enumerate(windows.views):
            with self.subTest(view=index):
                self.assertTrue(
                    player_window.reachable(view.geometry(), areas),
                    f"view {index} at {view.geometry()} is on no monitor",
                )
        # The other half of the same statement: there is now nothing left to rescue.
        self.assertEqual(windows.rescue_windows(), [])

    def test_the_whole_set_is_inside_one_monitor(self) -> None:
        """§6: one screen's available area, never the bounding box of several."""
        areas = self.desk(*self.REAL_DESK)
        windows = self.make(StubEngine(STACKED))
        windows.arrange()
        for index, view in enumerate(windows.views):
            with self.subTest(view=index):
                self.assertTrue(
                    any(area.contains(view.geometry()) for area in areas),
                    f"view {index} at {view.geometry()} spans two monitors",
                )

    def test_showing_the_set_is_what_arranging_it_was(self) -> None:
        """`show()` is the path the player really opens by; it must not strand either."""
        areas = self.desk(*self.REAL_DESK)
        windows = self.make(StubEngine(STACKED))
        windows.show()
        for view in windows.views:
            self.assertTrue(player_window.reachable(view.geometry(), areas))
        self.assertTrue(
            player_window.reachable(windows.transport.geometry(), areas)
        )

    def test_the_arrangement_is_still_the_arrangement(self) -> None:
        """Reachable is not enough: a screen that sat below must still sit below."""
        self.desk(*self.REAL_DESK)
        windows = self.make(StubEngine(STACKED))
        windows.arrange()
        top, bottom = (view.geometry() for view in windows.views)
        self.assertLess(top.y() + top.height(), bottom.y() + 2)
        self.assertEqual(top.size(), bottom.size())

    def test_a_desk_of_one_screen_is_unchanged_by_any_of_this(self) -> None:
        """The commonest case, and the one that must not get worse."""
        area = self.desk((0, 0, 2560, 1400))[0]
        windows = self.make(StubEngine())
        windows.arrange()
        self.assertEqual(
            [view.geometry() for view in windows.views],
            player_window.placements(windows.rects, area),
        )

    def test_arranging_never_touches_the_engine(self) -> None:
        """§6 again: moving windows is `setGeometry` and nothing else."""
        self.desk(*self.REAL_DESK)
        engine = StubEngine(STACKED)
        windows = self.make(engine)
        before = engine.position_ms
        engine.calls.clear()
        windows.arrange()
        windows.show()
        self.assertEqual(engine.calls, [])
        self.assertEqual(engine.position_ms, before)


class OnePerMonitorTests(SeparateWindowsTestCase):
    """§5's `one_per_monitor`: the same machinery, one window per monitor."""

    def test_each_window_fills_its_own_monitor(self) -> None:
        areas = self.desk((0, 0, 2560, 1440), (2560, 0, 1440, 2560))
        windows = self.make(StubEngine())
        self.assertTrue(windows.apply_one_per_monitor())
        placed = sorted(
            (view.geometry() for view in windows.views), key=lambda r: r.x()
        )
        self.assertEqual(placed, sorted(areas, key=lambda r: r.x()))

    def test_the_mapping_is_the_best_one_and_not_the_order_they_arrived_in(
        self,
    ) -> None:
        """`layouts.best_pairing`'s whole reason: nothing lines the two lists up.

        The sidecar names the portrait screen first here, so pairing by index puts
        each window on the other one's monitor.
        """
        self.desk((0, 0, 2560, 1440), (2560, 0, 1440, 2560))
        engine = StubEngine([(0, 0, 1440, 2560), (0, 0, 2560, 1440)])
        windows = self.make(engine)
        windows.apply_one_per_monitor()
        self.assertEqual(windows.pairing(), (1, 0))
        # The portrait stream is on the portrait monitor, pixel for pixel.
        self.assertEqual(
            windows.views[0].geometry(), QtCore.QRect(2560, 0, 1440, 2560)
        )

    def test_changing_one_screens_monitor_swaps_rather_than_stacking(self) -> None:
        self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        windows = self.make(StubEngine([(0, 0, 1920, 1080), (1920, 0, 1920, 1080)]))
        windows.apply_one_per_monitor([0, 1])
        self.assertTrue(windows.set_stream_monitor(0, 1))
        self.assertEqual(windows.pairing(), (1, 0))
        self.assertNotEqual(
            windows.views[0].geometry(), windows.views[1].geometry()
        )

    def test_one_screen_per_monitor_needs_a_monitor_each(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.arrange()
        placed = [QtCore.QRect(view.geometry()) for view in windows.views]
        self.assertFalse(windows.apply_one_per_monitor())
        self.assertEqual([view.geometry() for view in windows.views], placed)
        self.assertEqual(
            windows.transport.notice.text(), i18n._("Needs a second monitor")
        )

    def test_a_mapping_that_is_not_a_permutation_falls_back_to_the_best_one(
        self,
    ) -> None:
        """Both streams on one monitor would be one window exactly over the other."""
        self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        windows = self.make(StubEngine([(0, 0, 1920, 1080), (1920, 0, 1920, 1080)]))
        self.assertTrue(windows.apply_one_per_monitor([1, 1]))
        self.assertEqual(sorted(windows.pairing()), [0, 1])

    def test_it_brings_a_hidden_screen_back(self) -> None:
        """"One screen per monitor" with one of the screens away is not that."""
        self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.show()
        windows.hide_view(1)
        self.assertTrue(windows.is_hidden(1))
        windows.apply_one_per_monitor()
        self.assertFalse(windows.is_hidden(1))
        self.assertTrue(windows.views[1].isVisible())

    def test_restoring_the_arrangement_re_applies_it_rather_than_undoing_it(
        self,
    ) -> None:
        """§5: «Вернуть раскладку» puts the windows back where the last *choice* did.

        The choice was one window per monitor, and `restore_arrangement` used to
        fall through to `arrange()` - the scaled desktop mock-up inside one monitor,
        which is the opposite of what had just been asked for.
        """
        areas = self.desk((0, 0, 2560, 1400), (2560, 0, 1440, 2560))
        windows = self.make(StubEngine())
        self.assertTrue(windows.apply_one_per_monitor())
        placed = [QtCore.QRect(view.geometry()) for view in windows.views]
        self.assertEqual(sorted(placed, key=lambda r: r.x()),
                         sorted(areas, key=lambda r: r.x()))

        windows.views[0].move(120, 140)  # the viewer drags one somewhere
        windows.restore_arrangement()
        self.assertEqual([view.geometry() for view in windows.views], placed)

    def test_restoring_it_never_touches_the_engine(self) -> None:
        """§6: re-applying an arrangement is `setGeometry` and nothing else."""
        self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        engine = StubEngine()
        windows = self.make(engine)
        windows.apply_one_per_monitor()
        before = engine.position_ms
        engine.calls.clear()
        windows.restore_arrangement()
        windows.set_tiling_monitor(1)
        self.assertEqual(engine.calls, [])
        self.assertEqual(engine.position_ms, before)

    def test_restoring_it_keeps_the_mapping_the_viewer_chose(self) -> None:
        """§5 makes the mapping theirs; re-deriving the best one would undo it."""
        self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        windows = self.make(StubEngine([(0, 0, 1920, 1080), (1920, 0, 1920, 1080)]))
        windows.apply_one_per_monitor([0, 1])
        windows.set_stream_monitor(0, 1)
        self.assertEqual(windows.pairing(), (1, 0))
        windows.restore_arrangement()
        self.assertEqual(windows.pairing(), (1, 0))

    def test_the_menu_does_not_claim_the_desktop_arrangement_afterwards(self) -> None:
        """A menu reporting an arrangement that is not the one on the desk."""
        self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.apply_one_per_monitor()
        self.assertEqual(
            windows.arrangement(), player_window.layouts.LAYOUT_ONE_PER_MONITOR
        )
        menu = windows.arrange_menu()
        self.addCleanup(menu.deleteLater)
        desktop = find_action(menu, i18n._("As they sat on the desktop"))
        self.assertIsNotNone(desktop)
        assert desktop is not None
        self.assertFalse(desktop.isChecked())
        per_monitor = find_action(
            menu,
            player_window.layouts.title(
                player_window.layouts.LAYOUT_ONE_PER_MONITOR
            ),
        )
        self.assertIsNotNone(per_monitor)
        assert per_monitor is not None
        self.assertTrue(per_monitor.isChecked())

    def test_a_scheme_afterwards_leaves_one_per_monitor_behind(self) -> None:
        """Two arrangements cannot both be in force; the later one wins."""
        self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.apply_one_per_monitor()
        windows.apply_tiling(player_window.layouts.TILING_EQUAL_LR)
        self.assertEqual(
            windows.arrangement(), player_window.layouts.TILING_EQUAL_LR
        )
        windows.arrange()
        self.assertEqual(windows.arrangement(), "")

    def test_restoring_it_with_a_monitor_unplugged_still_places_the_windows(
        self,
    ) -> None:
        """The arrangement became impossible; the windows must still land somewhere."""
        areas = self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.apply_one_per_monitor()
        areas = self.desk((0, 0, 1920, 1080))  # the second monitor is unplugged
        windows.restore_arrangement()
        self.assertEqual(windows.arrangement(), "")
        for view in windows.views:
            self.assertTrue(player_window.reachable(view.geometry(), areas))

    def test_showing_the_set_keeps_an_arrangement_a_host_already_chose(self) -> None:
        """§2.1's dialog picks `one_per_monitor` before anything is on screen.

        `show()` used to call `arrange()` flat, so the host's choice was replaced by
        the desktop mock-up the instant the windows appeared.
        """
        areas = self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        windows = self.make(StubEngine())
        self.assertTrue(windows.apply_one_per_monitor())
        windows.show()
        self.assertEqual(
            sorted((v.geometry() for v in windows.views), key=lambda r: r.x()),
            sorted(areas, key=lambda r: r.x()),
        )


class ClosingAWindowTests(SeparateWindowsTestCase):
    """§5: the first window means stop; closing any other one puts it away."""

    def test_closing_the_second_window_hides_it_and_the_rest_plays_on(self) -> None:
        self.desk((0, 0, 1920, 1080))
        engine = StubEngine()
        windows = self.make(engine)
        windows.show()
        engine.is_playing = True
        ended: list[int] = []
        windows.closed.connect(lambda: ended.append(1))
        engine.calls.clear()

        windows.views[1].close()

        self.assertEqual(ended, [], "closing the second window ended the session")
        self.assertTrue(windows.is_hidden(1))
        self.assertFalse(windows.views[1].isVisible())
        self.assertTrue(windows.views[0].isVisible())
        self.assertTrue(windows.transport.isVisible())
        self.assertEqual(engine.calls, [], "the engine was told to stop or to seek")
        self.assertTrue(engine.is_playing)

    def test_the_hidden_window_is_still_being_fed_frames(self) -> None:
        """Hiding is not closing: the stream keeps playing behind the closed window."""
        engine = StubEngine()
        windows = self.make(engine)
        windows.show()
        windows.views[1].close()

        seen: list[QtMultimedia.QVideoFrame] = []
        windows.views[1].video.videoSink().videoFrameChanged.connect(seen.append)
        frame = QtMultimedia.QVideoFrame(
            QtMultimedia.QVideoFrameFormat(
                QtCore.QSize(16, 9),
                QtMultimedia.QVideoFrameFormat.PixelFormat.Format_ARGB8888,
            )
        )
        engine.sinks[1].setVideoFrame(frame)
        self.assertEqual(len(seen), 1)

    def test_the_transport_says_where_the_window_went(self) -> None:
        windows = self.make(StubEngine())
        windows.show()
        windows.views[1].close()
        self.assertEqual(
            windows.transport.notice.text(),
            i18n._("{name} is hidden — the Arrange menu brings it back.").format(
                name=windows.views[1].title()
            ),
        )

    def test_the_menu_then_offers_the_second_screen_back(self) -> None:
        windows = self.make(StubEngine())
        windows.show()
        windows.views[1].close()
        menu = windows.arrange_menu()
        action = find_action(menu, i18n._("Show the second screen"))
        self.assertIsNotNone(action, "there is no way back to the closed window")
        action.trigger()
        self.assertFalse(windows.is_hidden(1))
        self.assertTrue(windows.views[1].isVisible())

    def test_the_second_window_can_be_closed_again_after_being_brought_back(
        self,
    ) -> None:
        """`closed` is a once-only guard, and §5 made closing reversible."""
        windows = self.make(StubEngine())
        windows.show()
        windows.views[1].close()
        windows.show_view(1)
        windows.views[1].close()
        self.assertTrue(windows.is_hidden(1))

    def test_closing_the_first_window_still_closes_everything(self) -> None:
        """The rule that must not be weakened: the main window means stop."""
        engine = StubEngine()
        windows = self.make(engine)
        windows.show()
        ended: list[int] = []
        windows.closed.connect(lambda: ended.append(1))

        windows.views[0].close()

        self.assertEqual(ended, [1])
        self.assertFalse(windows.views[1].isVisible())
        self.assertFalse(windows.transport.isVisible())
        self.assertIn(("close",), engine.calls)

    def test_the_main_window_cannot_be_hidden_by_the_back_door(self) -> None:
        windows = self.make(StubEngine())
        windows.hide_view(0)
        self.assertFalse(windows.is_hidden(0))

    def test_a_third_screen_is_named_rather_than_called_the_second(self) -> None:
        windows = self.make(
            StubEngine([(0, 0, 1920, 1080), (1920, 0, 1920, 1080),
                        (3840, 0, 1920, 1080)])
        )
        windows.show()
        windows.views[2].close()
        menu = windows.arrange_menu()
        self.assertIsNotNone(
            find_action(
                menu, i18n._("Show {name}").format(name=windows.views[2].title())
            )
        )


class OneStreamTests(SeparateWindowsTestCase):
    """§1: "a recording with one stream never asks anything"."""

    def test_it_opens_with_one_view_and_no_arrange_button(self) -> None:
        windows = self.make(StubEngine([(0, 0, 1920, 1080)]))
        windows.show()
        self.assertEqual(len(windows.views), 1)
        self.assertTrue(
            windows.transport.arrange_button.isHidden(),
            "a one-screen recording is being asked how to arrange one window",
        )

    def test_its_menu_offers_nothing(self) -> None:
        windows = self.make(StubEngine([(0, 0, 1920, 1080)]))
        self.assertEqual(menu_texts(windows.arrange_menu()), [])

    def test_no_scheme_applies_to_it(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine([(0, 0, 1920, 1080)]))
        windows.arrange()
        placed = QtCore.QRect(windows.views[0].geometry())
        self.assertFalse(
            windows.apply_tiling(player_window.layouts.TILING_EQUAL_LR)
        )
        self.assertEqual(windows.views[0].geometry(), placed)

    def test_closing_its_only_window_closes_the_player(self) -> None:
        engine = StubEngine([(0, 0, 1920, 1080)])
        windows = self.make(engine)
        windows.show()
        ended: list[int] = []
        windows.closed.connect(lambda: ended.append(1))
        windows.views[0].close()
        self.assertEqual(ended, [1])

    def test_a_two_screen_recording_does_get_the_button(self) -> None:
        windows = self.make(StubEngine())
        self.assertFalse(windows.transport.arrange_button.isHidden())


class ArrangeMenuTests(SeparateWindowsTestCase):
    """What is in the menu, and what is deliberately not."""

    def test_the_four_schemes_are_offered_with_a_drawn_picture_each(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        menu = windows.arrange_menu()
        texts = menu_texts(menu)
        for tiling in player_window.layouts.TILINGS:
            title = player_window.layouts.tiling_title(tiling)
            self.assertIn(title, texts)
            action = find_action(menu, title)
            self.assertFalse(
                action.icon().isNull(), f"{tiling} has no drawn thumbnail"
            )

    def test_the_scheme_in_force_is_the_one_ticked(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.apply_tiling(player_window.layouts.TILING_BIG_LEFT)
        menu = windows.arrange_menu()
        ticked = [
            action.text() for action in menu.actions()
            if action.isCheckable() and action.isChecked()
        ]
        self.assertEqual(
            ticked,
            [player_window.layouts.tiling_title(
                player_window.layouts.TILING_BIG_LEFT
            )],
        )

    def test_one_monitor_is_not_asked_which_monitor(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        self.assertNotIn(
            i18n._("Tile on monitor"), menu_texts(windows.arrange_menu())
        )

    def test_two_monitors_are(self) -> None:
        self.desk((0, 0, 1920, 1080), (1920, 0, 1600, 900))
        windows = self.make(StubEngine())
        texts = menu_texts(windows.arrange_menu())
        self.assertIn(i18n._("Tile on monitor"), texts)
        self.assertIn(
            player_window.monitor_label(2, QtCore.QRect(1920, 0, 1600, 900)), texts
        )

    def test_one_screen_per_monitor_is_offered_but_greyed_on_one_monitor(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        menu = windows.arrange_menu()
        action = find_action(
            menu,
            player_window.layouts.title(
                player_window.layouts.LAYOUT_ONE_PER_MONITOR
            ),
        )
        self.assertIsNotNone(action)
        self.assertFalse(action.isEnabled())
        self.assertEqual(action.toolTip(), i18n._("Needs a second monitor"))

    def test_the_mapping_submenu_appears_only_with_the_monitors_for_it(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        self.assertNotIn(
            i18n._("Which screen on which monitor"),
            menu_texts(windows.arrange_menu()),
        )
        self.desk((0, 0, 1920, 1080), (1920, 0, 1920, 1080))
        self.assertIn(
            i18n._("Which screen on which monitor"),
            menu_texts(windows.arrange_menu()),
        )

    def test_a_menu_entry_really_moves_the_windows(self) -> None:
        """The press, not the method: a lambda bound wrongly is invisible otherwise."""
        area = self.desk((0, 0, 1200, 800))[0]
        windows = self.make(StubEngine())
        menu = windows.arrange_menu()
        action = find_action(
            menu,
            player_window.layouts.tiling_title(
                player_window.layouts.TILING_EQUAL_TB
            ),
        )
        action.trigger()
        self.assertEqual(
            [view.geometry() for view in windows.views],
            player_window.layouts.tile_rects(
                area, player_window.layouts.TILING_EQUAL_TB
            ),
        )

    def test_the_way_back_to_the_desktop_arrangement_is_in_it(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.apply_tiling(player_window.layouts.TILING_EQUAL_LR)
        menu = windows.arrange_menu()
        action = find_action(menu, i18n._("As they sat on the desktop"))
        self.assertIsNotNone(action)
        action.trigger()
        self.assertEqual(windows.tiling(), "")

    def test_the_menu_is_built_fresh_so_a_language_switch_reaches_it(self) -> None:
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)
        i18n.set_language(i18n.LANG_EN)
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        english = menu_texts(windows.arrange_menu())
        i18n.set_language(i18n.LANG_RU)
        russian = menu_texts(windows.arrange_menu())
        self.assertEqual(len(english), len(russian))
        self.assertNotEqual(english, russian)
        unchanged = [
            text for text, other in zip(english, russian) if text == other
        ]
        self.assertEqual(
            unchanged, [],
            "these menu entries are the same in Russian as in English",
        )

    def test_the_reason_on_a_disabled_entry_is_actually_shown(self) -> None:
        """§2.1: a disabled card says why. `QMenu.toolTipsVisible` defaults to False.

        The tooltip was set and never drawn, so the entry was a greyed line with no
        explanation - which is read as a fault in the player rather than as a desk
        with one monitor.
        """
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        menu = windows.arrange_menu()
        self.addCleanup(menu.deleteLater)
        self.assertTrue(
            menu.toolTipsVisible(),
            "the menu will not draw the reason written on its disabled entry",
        )
        action = find_action(
            menu,
            player_window.layouts.title(
                player_window.layouts.LAYOUT_ONE_PER_MONITOR
            ),
        )
        self.assertIsNotNone(action)
        assert action is not None
        self.assertFalse(action.isEnabled())
        self.assertEqual(action.toolTip(), i18n._("Needs a second monitor"))

    def test_three_screens_still_get_a_way_back_to_the_opening_arrangement(
        self,
    ) -> None:
        """§5 limits the four schemes to two windows, and nothing else.

        The menu used to hold the desktop arrangement and «Вернуть раскладку» inside
        the two-window test, so a three-screen recording on a one-monitor desk got a
        button whose whole menu was one greyed line.
        """
        self.desk((0, 0, 1920, 1080))
        windows = self.make(
            StubEngine(
                [(0, 0, 1920, 1080), (1920, 0, 1920, 1080), (3840, 0, 1920, 1080)]
            )
        )
        menu = windows.arrange_menu()
        self.addCleanup(menu.deleteLater)
        texts = menu_texts(menu)
        self.assertIn(i18n._("As they sat on the desktop"), texts)
        self.assertIn(i18n._("Restore the arrangement"), texts)
        # And still no scheme: those really are for two windows only.
        for tiling in player_window.layouts.TILINGS:
            self.assertNotIn(player_window.layouts.tiling_title(tiling), texts)

        windows.arrange()
        placed = [QtCore.QRect(view.geometry()) for view in windows.views]
        windows.views[1].move(17, 19)
        action = find_action(menu, i18n._("Restore the arrangement"))
        self.assertIsNotNone(action)
        assert action is not None
        action.trigger()
        self.assertEqual([view.geometry() for view in windows.views], placed)

    def test_pressing_the_button_opens_a_menu_and_closes_it_again(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.show()
        windows.transport.arrange_button.click()
        self.app.processEvents()
        popped = [
            child for child in windows.transport.findChildren(QtWidgets.QMenu)
            if child.isVisible()
        ]
        self.assertEqual(len(popped), 1, "the arrange button opened no menu")
        popped[0].close()


class DisplayChangeTests(SeparateWindowsTestCase):
    """§6: a monitor unplugged mid-playback must strand no window."""

    def test_a_window_on_the_lost_monitor_is_moved_onto_one_that_is_left(self) -> None:
        self.desk((0, 0, 1920, 1080), (1920, 0, 1600, 900))
        windows = self.make(StubEngine())
        windows.show()
        windows.apply_one_per_monitor([0, 1])
        self.assertEqual(
            windows.views[1].geometry(), QtCore.QRect(1920, 0, 1600, 900)
        )

        remaining = self.desk((0, 0, 1920, 1080))  # the second monitor is unplugged
        moved = windows._screen_removed(None)  # Qt's signal, delivered by hand

        rescued = windows.views[1].geometry()
        self.assertTrue(
            remaining[0].contains(rescued),
            f"{rescued} is still off the desk after the rescue",
        )
        self.assertIn(windows.views[1].title(), windows.transport.notice.text())
        self.assertIsNone(moved)  # the slot says nothing; `rescue_windows` does

    def test_the_rescue_names_every_window_it_moved(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.show()
        windows.views[1].setGeometry(5000, 5000, 800, 600)
        moved = windows.rescue_windows()
        self.assertEqual(moved, [windows.views[1].title()])
        self.assertIn(
            i18n._("A monitor is gone — {names} moved to one that is still there.")
            .format(names=windows.views[1].title()),
            windows.transport.notice.text(),
        )

    def test_a_window_that_is_still_reachable_is_left_exactly_where_it_was(self) -> None:
        """A desk where the *other* monitor went strands nothing, and moves nothing."""
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.apply_tiling(player_window.layouts.TILING_EQUAL_LR)
        placed = [QtCore.QRect(view.geometry()) for view in windows.views]
        self.assertEqual(windows.rescue_windows(), [])
        self.assertEqual([view.geometry() for view in windows.views], placed)

    def test_the_transport_is_rescued_too_and_said_so(self) -> None:
        """The bar carries every control; one left off the desk cannot be pressed."""
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.show()
        windows.transport.setGeometry(4000, 4000, 760, 52)
        moved = windows.rescue_windows()
        self.assertIn(i18n._("the controls"), moved)
        self.assertTrue(
            QtCore.QRect(0, 0, 1920, 1080).contains(windows.transport.geometry())
        )

    def test_a_rescued_window_keeps_its_size_where_there_is_room(self) -> None:
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.views[1].setGeometry(4000, 200, 640, 480)
        windows.rescue_windows()
        self.assertEqual(windows.views[1].size(), QtCore.QSize(640, 480))

    def test_a_rescued_window_is_shrunk_when_there_is_not(self) -> None:
        """Clamping without shrinking leaves the resize handles off the screen."""
        self.desk((0, 0, 800, 600))
        windows = self.make(StubEngine())
        windows.views[1].setGeometry(4000, 200, 1600, 900)
        windows.rescue_windows()
        self.assertEqual(windows.views[1].geometry(), QtCore.QRect(0, 0, 800, 600))

    def test_a_hidden_window_is_rescued_when_it_is_brought_back(self) -> None:
        """Nothing is moved while it is away; it is placed when it is shown."""
        self.desk((0, 0, 1920, 1080))
        windows = self.make(StubEngine())
        windows.show()
        windows.hide_view(1)
        windows.views[1].setGeometry(6000, 0, 640, 480)
        self.assertEqual(windows.rescue_windows(), [])
        windows.show_view(1)
        self.assertTrue(
            QtCore.QRect(0, 0, 1920, 1080).contains(windows.views[1].geometry())
        )

    def test_a_desk_with_no_screens_at_all_moves_nothing(self) -> None:
        """Qt reports an empty screen list during a display change; it passes."""
        self.desk()
        windows = self.make(StubEngine())
        self.assertEqual(windows.rescue_windows(), [])

    def test_reachable_wants_more_than_a_sliver(self) -> None:
        screens = [QtCore.QRect(0, 0, 1000, 1000)]
        self.assertTrue(
            player_window.reachable(QtCore.QRect(0, 0, 400, 400), screens)
        )
        self.assertFalse(
            player_window.reachable(QtCore.QRect(990, 0, 400, 400), screens)
        )


class SeekAndEndTests(SeparateWindowsTestCase):
    """§6's two states, wired into the views the way the stage wires its panes."""

    def test_a_seek_in_flight_dims_every_view(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        self.assertFalse(any(view.is_busy() for view in windows.views))
        engine.begin_seeking()
        self.assertTrue(all(view.is_busy() for view in windows.views))
        self.assertTrue(all(not view.veil.isHidden() for view in windows.views))
        engine.end_seeking()
        self.assertFalse(any(view.is_busy() for view in windows.views))

    def test_a_view_built_during_a_seek_opens_dimmed(self) -> None:
        """A late subscriber has to be able to ask, not only to be told."""
        engine = StubEngine()
        engine._seeking = True
        windows = self.make(engine)
        self.assertTrue(all(view.is_busy() for view in windows.views))

    def test_a_stream_that_has_run_out_is_badged_and_the_other_is_not(self) -> None:
        engine = StubEngine()
        windows = self.make(engine)
        engine.end_stream(1)
        self.assertTrue(windows.views[1].has_ended())
        self.assertFalse(windows.views[0].has_ended())
        self.assertEqual(
            windows.views[1].badge.text(),
            i18n._("{name} — the recording ends here").format(
                name=windows.views[1].title()
            ),
        )

    def test_a_stream_that_ran_out_before_the_window_opened_is_badged_too(self) -> None:
        engine = StubEngine()
        engine._ended.add(1)
        windows = self.make(engine)
        self.assertTrue(windows.views[1].has_ended())

    def test_a_seek_takes_the_ended_badge_away(self) -> None:
        """The engine clears its own ended flags in `seek`; the badge must follow."""
        engine = StubEngine()
        windows = self.make(engine)
        engine.end_stream(1)
        engine.begin_seeking()
        self.assertFalse(windows.views[1].has_ended())
        self.assertTrue(windows.views[1].badge.isHidden())

    def test_a_badge_follows_the_screens_name_and_the_language(self) -> None:
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)
        i18n.set_language(i18n.LANG_EN)
        engine = StubEngine()
        windows = self.make(engine)
        engine.end_stream(1)
        english = windows.views[1].badge.text()
        i18n.set_language(i18n.LANG_RU)
        self.assertNotEqual(windows.views[1].badge.text(), english)

    def test_a_badge_the_host_wrote_is_not_translated_back(self) -> None:
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)
        i18n.set_language(i18n.LANG_EN)
        windows = self.make(StubEngine())
        windows.views[1].set_ended(True, "the camera stopped")
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(windows.views[1].badge.text(), "the camera stopped")

    def test_an_engine_without_the_two_signals_still_opens(self) -> None:
        """Both are optional: a player built before §6 behaves as it always did."""
        engine = StubEngine()
        engine.__dict__["seeking_changed"] = None
        engine.__dict__["stream_ended"] = None
        engine.__dict__["seeking"] = None
        engine.__dict__["stream_has_ended"] = None
        windows = self.make(engine)
        self.assertFalse(any(view.is_busy() for view in windows.views))
        self.assertFalse(any(view.has_ended() for view in windows.views))
        self.assertEqual(windows.transport.notice.text(), "")

    def test_both_states_at_once_keep_the_veil_on_top(self) -> None:
        """A seek started over a finished stream: the last state set must not decide."""
        windows = self.make(StubEngine())
        view = windows.views[1]
        view.setGeometry(0, 0, 640, 480)
        view.set_ended(True)
        view.set_busy(True)
        self.assertGreater(
            view.children().index(view.veil),
            view.children().index(view.badge),
            "the badge is painted over the veil",
        )

    def test_a_view_paints_with_both_states_showing(self) -> None:
        windows = self.make(StubEngine())
        view = windows.views[1]
        view.resize(480, 270)
        view.set_ended(True)
        view.set_busy(True)
        for widget in (view, view.veil, view.badge):
            picture = QtGui.QPixmap(widget.size())
            picture.fill(QtGui.QColor("#000000"))
            widget.render(picture)
            self.assertFalse(picture.isNull())


class DrawnSchemeTests(unittest.TestCase):
    """The menu's pictures. They are `layouts.tile_rects`, so they cannot drift."""

    def setUp(self) -> None:
        self.app = _app()

    def test_every_scheme_draws(self) -> None:
        picture = QtGui.QPixmap(60, 40)
        picture.fill(QtGui.QColor("#000000"))
        painter = QtGui.QPainter(picture)
        try:
            for tiling in player_window.layouts.TILINGS:
                self.assertTrue(
                    player_window.draw_tiling(
                        painter, tiling, QtCore.QRectF(0, 0, 60, 40)
                    ),
                    tiling,
                )
            self.assertFalse(
                player_window.draw_tiling(
                    painter, "diagonally", QtCore.QRectF(0, 0, 60, 40)
                )
            )
            self.assertFalse(
                player_window.draw_tiling(
                    painter,
                    player_window.layouts.TILING_EQUAL_LR,
                    QtCore.QRectF(0, 0, 0, 0),
                )
            )
        finally:
            painter.end()

    def test_an_icon_is_produced_for_a_scheme_and_not_for_a_name(self) -> None:
        self.assertFalse(
            player_window.tiling_icon(
                player_window.layouts.TILING_BIG_LEFT
            ).isNull()
        )
        self.assertTrue(player_window.tiling_icon("diagonally").isNull())

    def test_the_arrange_glyph_is_one_of_the_players_own(self) -> None:
        self.assertIn("arrange", player_window.PLAYER_GLYPHS)
        picture = QtGui.QPixmap(24, 24)
        picture.fill(QtGui.QColor("#000000"))
        painter = QtGui.QPainter(picture)
        try:
            self.assertTrue(
                player_window.draw_glyph(
                    painter, "arrange", QtCore.QRectF(0, 0, 24, 24), "#FFFFFF"
                )
            )
        finally:
            painter.end()


class SeparateWindowsLanguageTests(unittest.TestCase):
    """§5's own sentences, in both languages.

    No Russian wording is written here, as in `LanguageTests`: each check compares a
    control against `i18n._()` of the same English key and against the English itself,
    so rewording a translation keeps the suite green while forgetting `_()` does not.
    """

    def setUp(self) -> None:
        self.app = _app()
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)
        i18n.set_language(i18n.LANG_EN)
        self.made: list[player_window.PlayerWindows] = []

    def tearDown(self) -> None:
        for windows in self.made:
            windows.close_all()
        self.app.processEvents()

    def windows(self) -> player_window.PlayerWindows:
        made = player_window.PlayerWindows(StubEngine())
        self.made.append(made)
        return made

    def test_every_sentence_this_mode_adds_has_russian(self) -> None:
        texts = [
            "As they sat on the desktop",
            "Tile on monitor",
            "Monitor {number} — {width}×{height}",
            "Which screen on which monitor",
            "Show {name}",
            "{name} is hidden — the Arrange menu brings it back.",
            "A monitor is gone — {names} moved to one that is still there.",
            "the controls",
            "{name}: no source of frames — this window will stay black.",
            "{name}: the source of frames has no videoFrameChanged signal "
            "— this window will stay black.",
        ]
        self.assertEqual(
            i18n.missing_translations(texts), [],
            "add it to app/screenrec/translations/player_windows.py",
        )

    def test_a_window_that_will_stay_black_says_why_in_both(self) -> None:
        """The sentence the host puts on the notice strip, re-read on a switch."""
        view = player_window.ScreenView("Screen 2", None)
        self.addCleanup(view.deleteLater)
        english = view.setup_problem
        self.assertIn("Screen 2", english)
        i18n.set_language(i18n.LANG_RU)
        view.retranslate()
        self.assertNotEqual(view.setup_problem, english)
        self.assertIn("Screen 2", view.setup_problem)

    def test_the_signal_name_stays_findable_in_the_russian_sentence(self) -> None:
        """A reader hunting the fault greps for `videoFrameChanged`, not for a word."""
        i18n.set_language(i18n.LANG_RU)
        view = player_window.ScreenView("Screen 2", object())
        self.addCleanup(view.deleteLater)
        self.assertIn("videoFrameChanged", view.setup_problem)

    def test_the_arrange_button_says_something_in_both(self) -> None:
        """Two streams, so the button names `L` - §7's key for the next arrangement."""
        windows = self.windows()
        english = windows.transport.arrange_button.toolTip()
        self.assertEqual(english, "Arrange (L)")
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(
            windows.transport.arrange_button.toolTip(), i18n._("Arrange (L)")
        )
        self.assertNotEqual(windows.transport.arrange_button.toolTip(), english)

    def test_the_arrange_button_only_names_l_where_l_works(self) -> None:
        """§5's schemes place two windows, so `L` does nothing on three screens.

        The button is still there - «Как на рабочем столе» and «Вернуть раскладку»
        work for any number of views - but it must not advertise a key that answers
        with silence, which is the rule the two step buttons' `(,)` and `(.)` follow.
        """
        for rects in (
            [(0, 0, 1920, 1080), (1920, 0, 1920, 1080), (3840, 0, 1920, 1080)],
            [(0, 0, 1920, 1080)],
        ):
            made = player_window.PlayerWindows(StubEngine(rects))
            self.made.append(made)
            self.assertEqual(
                made.transport.arrange_button.toolTip(), "Arrange",
                f"{len(rects)} screens",
            )

    def test_the_seek_veil_is_said_again_after_a_switch(self) -> None:
        windows = self.windows()
        english = windows.views[0].veil.text()
        self.assertEqual(english, "Seeking…")
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(windows.views[0].veil.text(), i18n._("Seeking…"))
        self.assertNotEqual(windows.views[0].veil.text(), english)

    def test_the_monitor_label_carries_its_size_in_both(self) -> None:
        area = QtCore.QRect(1920, 0, 1600, 900)
        english = player_window.monitor_label(2, area)
        self.assertIn("1600", english)
        self.assertIn("900", english)
        i18n.set_language(i18n.LANG_RU)
        russian = player_window.monitor_label(2, area)
        self.assertNotEqual(russian, english)
        self.assertIn("1600", russian)


class ScreenChoiceTests(unittest.TestCase):
    """`host_screen`, `reachable` and `rescue_into`: the rule, without any windows."""

    DESK = [QtCore.QRect(0, 0, 2560, 1440), QtCore.QRect(2560, 0, 1440, 2560)]

    def test_a_window_belongs_to_the_screen_showing_most_of_it(self) -> None:
        self.assertEqual(
            player_window.host_screen(QtCore.QRect(2400, 10, 800, 600), self.DESK),
            self.DESK[1],
        )
        self.assertEqual(
            player_window.host_screen(QtCore.QRect(2000, 10, 800, 600), self.DESK),
            self.DESK[0],
        )

    def test_with_no_screens_there_is_no_answer_rather_than_a_guess(self) -> None:
        """Qt reports an empty list during a display change; a guessed rectangle
        would mean nothing a moment later."""
        nowhere = player_window.host_screen(QtCore.QRect(0, 0, 10, 10), [])
        self.assertTrue(nowhere.isNull())
        self.assertEqual(
            player_window.rescue_into(QtCore.QRect(9000, 9000, 10, 10), []),
            QtCore.QRect(9000, 9000, 10, 10),
        )

    def test_a_window_with_a_usable_share_still_showing_is_left_alone(self) -> None:
        """The rescue is for windows nobody can reach, not for tidying the desk."""
        straddling = QtCore.QRect(2300, 100, 800, 600)  # 540 of its 800 px are on it
        self.assertEqual(
            player_window.rescue_into(straddling, [self.DESK[1]]), straddling
        )

    def test_a_window_with_only_a_sliver_showing_is_moved_and_keeps_its_size(
        self,
    ) -> None:
        stranded = QtCore.QRect(1800, 100, 800, 600)  # 40 px of it are on the screen
        rescued = player_window.rescue_into(stranded, [self.DESK[1]])
        self.assertTrue(self.DESK[1].contains(rescued))
        self.assertEqual(rescued.size(), stranded.size())

    def test_the_transport_spot_still_lands_on_a_real_screen(self) -> None:
        """`host_screen` was pulled out of this; the rule it carries is unchanged."""
        boxes = [QtCore.QRect(2600, 40, 1200, 2000)]
        spot = player_window.transport_spot(
            boxes, self.DESK, QtCore.QSize(760, 52)
        )
        self.assertTrue(self.DESK[1].contains(spot))

    def test_nothing_to_anchor_to_goes_to_the_primary_screen(self) -> None:
        """§6 names the primary monitor, and `screens()[0]` is not it.

        Qt promises nothing about the order of `QGuiApplication.screens()`. This
        machine has one screen, so the enumeration order cannot be reproduced here;
        what *is* checked is that `preferred_area` asks `primaryScreen()` at all and
        returns that screen when it is in the list, instead of index 0 unconditionally.
        """
        primary = QtGui.QGuiApplication.primaryScreen()
        self.assertIsNotNone(primary)
        assert primary is not None
        area = primary.availableGeometry()
        elsewhere = QtCore.QRect(-9000, -9000, 640, 480)

        # The primary listed second: index 0 is the wrong answer and the old code
        # gave it for both of these.
        desk = [elsewhere, area]
        self.assertEqual(player_window.preferred_area(desk), area)
        self.assertEqual(player_window.host_screen(None, desk), area)
        # A window on no screen at all is stranded, and §6 sends it to the primary.
        stranded = QtCore.QRect(50_000, 50_000, 400, 300)
        self.assertEqual(player_window.host_screen(stranded, desk), area)
        # A desk Qt does not recognise falls back to the first entry, which is the
        # only honest answer about an invented one - the tests depend on it.
        invented = [QtCore.QRect(0, 0, 123, 456), QtCore.QRect(123, 0, 456, 123)]
        self.assertEqual(player_window.preferred_area(invented), invented[0])

    def test_a_screen_showing_none_of_the_box_does_not_win(self) -> None:
        """The overlap loop used to start at -1, so zero overlap beat the primary."""
        far = QtCore.QRect(60_000, 60_000, 100, 100)
        self.assertEqual(
            player_window.host_screen(far, self.DESK),
            player_window.preferred_area(self.DESK),
        )


class LanguageTests(unittest.TestCase):
    """The pass that put this module's own sentences through `i18n`.

    Every one of these covers a string that shipped as a raw literal while its Russian
    sat in `translations/player.py` being reported as "translated but unused": the
    transport's seven controls, the bar's title, the speed chip's tooltip, the volume
    button's sentence, and the generated screen names.

    **No Russian wording is written here.** Each assertion compares a control against
    `i18n._()` of the same English key, and against the English itself, so rewording a
    translation keeps the suite green while forgetting to call `_()` does not. That is
    the rule the rest of the suite follows, and the reason this file holds no Cyrillic.
    """

    def setUp(self) -> None:
        self.app = _app()
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)
        i18n.set_language(i18n.LANG_EN)
        self.made: list[player_window.PlayerWindows] = []
        self.bars: list[player_window.TransportBar] = []

    def tearDown(self) -> None:
        for windows in self.made:
            windows.close_all()
        for bar in self.bars:
            bar.close()
            bar.deleteLater()
        self.app.processEvents()

    def bar(self) -> player_window.TransportBar:
        made = player_window.TransportBar()
        self.bars.append(made)
        return made

    def windows(self) -> player_window.PlayerWindows:
        made = player_window.PlayerWindows(StubEngine())
        self.made.append(made)
        return made

    @staticmethod
    def _said(bar: player_window.TransportBar) -> dict[str, str]:
        """Every sentence the bar is showing now, by the control showing it."""
        return {
            "title": bar.windowTitle(),
            "play": bar.play_button.toolTip(),
            "scrub": bar.scrubber.toolTip(),
            "back": bar.back_button.toolTip(),
            "forward": bar.forward_button.toolTip(),
            "speed": bar.speed_chip.toolTip(),
            "volume": bar.volume_button.toolTip(),
            "arrange": bar.arrange_button.toolTip(),
            "close": bar.close_button.toolTip(),
        }

    # -- the defect itself -------------------------------------------------

    def test_nothing_on_the_transport_is_left_in_english(self) -> None:
        """THE DEFECT: the whole bar was raw literals, so Russian never reached it.

        Written against the set of controls rather than against a list of sentences,
        so a control added later with a hard-coded tooltip fails here as well.
        """
        bar = self.bar()
        english = self._said(bar)
        i18n.set_language(i18n.LANG_RU)
        russian = self._said(bar)
        still_english = sorted(
            name for name, text in russian.items() if text == english[name]
        )
        self.assertEqual(
            still_english, [],
            "these controls say the same thing in Russian as in English, which means "
            "their text never went through i18n._()",
        )

    def test_the_switch_reaches_a_bar_that_is_already_built(self) -> None:
        """No reopen: `retranslate` is connected to `i18n.notifier.changed`."""
        bar = self.bar()
        self.assertEqual(bar.play_button.toolTip(), "Play (Space)")
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(bar.play_button.toolTip(), i18n._("Play (Space)"))
        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(bar.play_button.toolTip(), "Play (Space)")

    def test_a_bar_built_in_russian_opens_in_russian(self) -> None:
        """The constructor calls `_()` too, not only `retranslate`."""
        i18n.set_language(i18n.LANG_RU)
        bar = self.bar()
        self.assertEqual(bar.scrubber.toolTip(), i18n._("Scrub"))
        self.assertEqual(bar.close_button.toolTip(), i18n._("Close the player (Esc)"))
        self.assertEqual(bar.windowTitle(), i18n._("ScreenRec player"))

    # -- the two that change with the state --------------------------------

    def test_the_play_tooltip_follows_the_state_through_a_switch(self) -> None:
        """`set_playing` and `retranslate` have to agree about which word it is."""
        bar = self.bar()
        bar.set_playing(True)
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(bar.play_button.toolTip(), i18n._("Pause (Space)"))
        bar.set_playing(False)
        self.assertEqual(bar.play_button.toolTip(), i18n._("Play (Space)"))
        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(bar.play_button.toolTip(), "Play (Space)")

    def test_the_volume_tooltip_is_a_template_and_keeps_its_percentage(self) -> None:
        """It was an f-string, which can never be a catalogue key.

        The level is moved with the wheel, which is the only way a viewer changes it,
        so this covers the tooltip being rewritten as the volume moves as well.
        """
        bar = self.bar()
        wheel = QtGui.QWheelEvent(
            QtCore.QPointF(10, 10),
            QtCore.QPointF(10, 10),
            QtCore.QPoint(0, 0),
            QtCore.QPoint(0, -120),   # one notch down: 100% -> 95%
            QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            QtCore.Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QtWidgets.QApplication.sendEvent(bar, wheel)
        self.assertIn("95", bar.volume_button.toolTip())

        i18n.set_language(i18n.LANG_RU)
        muted = i18n._("Mute - volume {percent}%, scroll here to change it")
        self.assertEqual(bar.volume_button.toolTip(), muted.format(percent=95))
        self.assertIn("95", bar.volume_button.toolTip())

        bar.set_muted(True)
        unmuted = i18n._("Unmute - volume {percent}%, scroll here to change it")
        self.assertEqual(bar.volume_button.toolTip(), unmuted.format(percent=95))
        self.assertNotEqual(unmuted, muted)

    def test_the_volume_button_opens_saying_what_set_muted_would_say(self) -> None:
        """It used to open with 'Mute - scroll here…' and be re-worded by the host."""
        bar = self.bar()
        opening = bar.volume_button.toolTip()
        bar.set_muted(False)
        self.assertEqual(bar.volume_button.toolTip(), opening)

    def test_the_speed_chip_looks_after_its_own_tooltip(self) -> None:
        chip = player_window.SpeedChip()
        self.addCleanup(chip.deleteLater)
        self.assertEqual(chip.toolTip(), "Playback speed - click to cycle")
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(chip.toolTip(), i18n._("Playback speed - click to cycle"))

    # -- the names on the pictures -----------------------------------------

    def test_a_generated_screen_name_is_translated(self) -> None:
        recording = StubRecording(TWO_SCREENS)
        i18n.set_language(i18n.LANG_RU)
        names = player_window.screen_titles(recording, 2)
        self.assertEqual(
            names,
            [
                i18n._("Screen {number} - primary").format(number=1),
                i18n._("Screen {number}").format(number=2),
            ],
        )
        self.assertNotEqual(names, ["Screen 1 - primary", "Screen 2"])

    def test_a_name_the_recording_carries_is_never_translated(self) -> None:
        """`titles` is the viewer's own word for that screen, in their own language."""
        recording = StubRecording(TWO_SCREENS)
        recording.titles = ["Laptop", "The portrait one"]
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(
            player_window.screen_titles(recording, 2),
            ["Laptop", "The portrait one"],
        )

    def test_the_views_are_renamed_when_the_language_changes(self) -> None:
        windows = self.windows()
        self.assertEqual(
            [view.title() for view in windows.views],
            ["Screen 1 - primary", "Screen 2"],
        )
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(
            [view.title() for view in windows.views],
            [
                i18n._("Screen {number} - primary").format(number=1),
                i18n._("Screen {number}").format(number=2),
            ],
        )
        self.assertEqual(
            [view.label.text() for view in windows.views],
            [view.title() for view in windows.views],
        )

    def test_a_view_renamed_from_outside_keeps_that_name(self) -> None:
        """A caller's word beats a generated one, as a pane's label does."""
        windows = self.windows()
        windows.views[1].set_label("The portrait one")
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(windows.views[1].title(), "The portrait one")
        self.assertEqual(
            windows.views[0].title(),
            i18n._("Screen {number} - primary").format(number=1),
        )

    def test_a_switch_after_the_player_has_closed_does_not_raise(self) -> None:
        """The windows stay connected to the notifier until Qt collects them."""
        windows = self.windows()
        windows.close_all()
        i18n.set_language(i18n.LANG_RU)   # must not touch a closed view
        i18n.set_language(i18n.LANG_EN)


# ===========================================================================
# docs/PLAYER_SOURCES.md - opening files by hand, and lining two cameras up.
#
# Every test below reads the engine's position before and after whatever it
# does, because the one rule the whole feature hangs on is that none of this
# moves the timeline. The last class is the regression that matters most: a
# one-stream recording, and an engine that predates all of it.
# ===========================================================================


def _press(
    windows: player_window.PlayerWindows,
    key: QtCore.Qt.Key,
    modifiers: QtCore.Qt.KeyboardModifier = QtCore.Qt.KeyboardModifier.NoModifier,
) -> None:
    """A key press on the transport, with whatever modifiers, through the filter."""
    event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, int(key), modifiers)
    QtWidgets.QApplication.sendEvent(windows.transport, event)


def _mime(*paths: str) -> QtCore.QMimeData:
    data = QtCore.QMimeData()
    data.setUrls([QtCore.QUrl.fromLocalFile(path) for path in paths])
    return data


def _drop(widget: QtWidgets.QWidget, data: QtCore.QMimeData) -> None:
    """Deliver a drop to `widget`'s own handler.

    `QApplication.sendEvent` cannot be used here, and that is Qt's doing rather than
    a shortcut: `QApplication::notify` routes drag and drop events through the drag
    manager, which drops a synthesised one on the floor when no drag is actually in
    progress - measured, the handler is never reached and the test passes for the
    wrong reason. The handler is what this module owns, so the handler is what is
    called; whether Qt delivers a real drop to a widget with `setAcceptDrops(True)`
    is Qt's business and is checked here only by that flag being set.
    """
    event = QtGui.QDropEvent(
        QtCore.QPointF(10.0, 10.0),
        QtCore.Qt.DropAction.CopyAction,
        data,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    widget.dropEvent(event)


class SourcesTestCase(SeparateWindowsTestCase):
    """A two-stream player on an engine that can do everything §1 and §2 ask."""

    def windows(
        self, engine: StubEngine | None = None
    ) -> player_window.PlayerWindows:
        return self.make(engine if engine is not None else SourceEngine())


class WaitingStreamTests(SourcesTestCase):
    """§2.1: a stream the timeline has not reached holds black, with a badge."""

    def test_a_waiting_stream_is_badged(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        engine.begin_waiting(1)
        self.assertTrue(windows.views[1].is_waiting())
        self.assertFalse(windows.views[1].badge.isHidden())
        self.assertFalse(windows.views[0].is_waiting())

    def test_a_waiting_view_drops_the_frames_it_is_sent(self) -> None:
        """§2.1: a frozen first frame shown as if it were live is the thing to avoid."""
        engine = SourceEngine()
        windows = self.windows(engine)
        view = windows.views[1]
        engine.begin_waiting(1)
        sink = view.video.videoSink()
        self.assertIsNotNone(sink)
        before = sink.videoFrame()
        view._on_frame(QtMultimedia.QVideoFrame())
        self.assertEqual(sink.videoFrame().isValid(), before.isValid())

    def test_waiting_wins_over_ended_and_gives_it_back(self) -> None:
        view = player_window.ScreenView("Screen 2", QtMultimedia.QVideoSink())
        self.addCleanup(view.close)
        view.set_ended(True)
        ended_text = view.badge.text()
        view.set_waiting(True)
        self.assertNotEqual(view.badge.text(), ended_text)
        view.set_waiting(False)
        self.assertEqual(view.badge.text(), ended_text)

    def test_the_state_is_asked_for_at_start_up(self) -> None:
        """A set saved with an offset opens with one stream already waiting."""
        engine = SourceEngine()
        engine._waiting.add(1)
        windows = self.windows(engine)
        self.assertTrue(windows.views[1].is_waiting())

    def test_an_older_engine_leaves_every_view_alone(self) -> None:
        windows = self.make(StubEngine())
        self.assertFalse(any(view.is_waiting() for view in windows.views))


class DroppedFileTests(SourcesTestCase):
    """§1: drag and drop onto any player window, the bar included."""

    def test_two_files_dropped_on_a_view_arrive_together(self) -> None:
        windows = self.windows()
        seen: list[list[str]] = []
        windows.views[0].files_dropped.connect(seen.append)
        _drop(windows.views[0], _mime("B:/a.mp4", "B:/b.mp4"))
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 2)
        self.assertTrue(seen[0][0].endswith("a.mp4"))
        self.assertTrue(seen[0][1].endswith("b.mp4"))

    def test_a_drop_on_the_bar_arrives_too(self) -> None:
        windows = self.windows()
        seen: list[list[str]] = []
        windows.transport.files_dropped.connect(seen.append)
        _drop(windows.transport, _mime("B:/one.mp4"))
        self.assertEqual(len(seen), 1)

    def test_a_drop_reaches_the_one_door(self) -> None:
        windows = self.windows()
        with mock.patch.object(windows, "_replace_with") as replace:
            _drop(windows.views[1], _mime("B:/a.mp4", "B:/b.mp4"))
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(len(replace.call_args.args[0]), 2)

    def test_a_drag_with_no_files_is_refused(self) -> None:
        data = QtCore.QMimeData()
        data.setText("not a file")
        self.assertEqual(player_window.dropped_files(data), [])

    def test_a_url_that_is_not_a_local_file_is_left_out(self) -> None:
        data = QtCore.QMimeData()
        data.setUrls([QtCore.QUrl("https://example.invalid/clip.mp4")])
        self.assertEqual(player_window.dropped_files(data), [])


class OpenMenuTests(SourcesTestCase):
    """§1's menu: the dialog, the library, the second camera, saving the set."""

    def library(self, rows: list[object]) -> None:
        """Pretend the output folder holds `rows`, for as long as this test runs."""
        from screenrec.player import library

        self.addCleanup(
            setattr, library, "find_entries", library.find_entries
        )
        library.find_entries = lambda folder, durations=True: list(rows)

    def test_the_menu_offers_the_dialog_and_saving_a_pair(self) -> None:
        windows = self.windows()
        self.library([])
        menu = windows.open_menu()
        self.addCleanup(menu.deleteLater)
        texts = menu_texts(menu)
        self.assertIn(i18n.pick("windows.open.dialog", "Open…"), texts)
        self.assertIn(i18n.pick("windows.open.save", "Save this set"), texts)
        self.assertNotIn(
            i18n.pick("windows.open.second", "Add a second video…"), texts
        )

    def test_one_video_is_offered_a_second_one_and_not_a_set(self) -> None:
        """§1.1, and the shape `open.py` builds for a one-stream recording."""
        windows = self.make(SourceEngine([(0, 0, 1920, 1080)]))
        self.library([])
        menu = windows.open_menu()
        self.addCleanup(menu.deleteLater)
        texts = menu_texts(menu)
        self.assertIn(i18n.pick("windows.open.second", "Add a second video…"), texts)
        self.assertNotIn(i18n.pick("windows.open.save", "Save this set"), texts)

    def test_the_library_rows_are_in_the_menu_and_open_what_they_name(self) -> None:
        class Row:
            path = Path("B:/takes/yesterday.screenrec.json")
            name = "yesterday"
            recorded = "2026-11-03T18:20:00"
            size_bytes = 3 * 1024 * 1024
            screens = 2

        windows = self.windows()
        self.library([Row()])
        menu = windows.open_menu()
        self.addCleanup(menu.deleteLater)
        row = next(a for a in menu.actions() if "yesterday" in a.text())
        self.assertIn("2026-11-03", row.text())
        with mock.patch.object(windows, "_replace_with") as replace:
            row.trigger()
        self.assertEqual(replace.call_count, 1)
        self.assertIn("yesterday", str(replace.call_args.args[0]))

    def test_the_library_is_capped(self) -> None:
        class Row:
            def __init__(self, number: int) -> None:
                self.path = Path(f"B:/takes/take{number}.mp4")
                self.name = f"take{number}"
                self.recorded = ""
                self.size_bytes = 0
                self.screens = 1

        windows = self.windows()
        self.library([Row(n) for n in range(40)])
        self.assertEqual(
            len(windows._library_rows()), player_window.LIBRARY_ROWS
        )

    def test_a_folder_that_cannot_be_listed_still_opens_the_menu(self) -> None:
        from screenrec.player import library

        self.addCleanup(setattr, library, "find_entries", library.find_entries)

        def explode(folder, durations=True):
            raise OSError("the drive is gone")

        library.find_entries = explode
        windows = self.windows()
        menu = windows.open_menu()
        self.addCleanup(menu.deleteLater)
        self.assertIn(i18n.pick("windows.open.dialog", "Open…"), menu_texts(menu))

    def test_control_o_opens_the_menu(self) -> None:
        windows = self.windows()
        with mock.patch.object(windows, "_open_open_menu") as opened:
            _press(
                windows,
                QtCore.Qt.Key.Key_O,
                QtCore.Qt.KeyboardModifier.ControlModifier,
            )
        self.assertEqual(opened.call_count, 1)


class AudioSourceTests(SourcesTestCase):
    """§1.2: exactly one stream is heard, and the viewer says which."""

    def test_the_chip_opens_on_the_first_stream(self) -> None:
        windows = self.windows()
        self.assertFalse(windows.transport.audio_chip.isHidden())
        self.assertEqual(windows.transport.audio_source(), 0)
        self.assertEqual(
            windows.transport.audio_chip.label(), windows.views[0].title()
        )

    def test_choosing_the_other_stream_moves_the_sound_and_nothing_else(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        before = engine.position_ms
        titles = [view.title() for view in windows.views]
        windows.set_audio_source(1)
        self.assertIn(("set_audio_source", 1), engine.calls)
        self.assertEqual(engine.position_ms, before)
        self.assertEqual([view.title() for view in windows.views], titles)
        self.assertEqual(windows.transport.audio_chip.label(), titles[1])

    def test_off_mutes_rather_than_growing_a_second_kind_of_silence(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        windows.set_audio_source(-1)
        self.assertIn(("set_muted", True), engine.calls)
        self.assertNotIn(("set_audio_source", -1), engine.calls)
        self.assertEqual(windows.transport.audio_source(), -1)

    def test_choosing_a_stream_again_unmutes(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        windows.set_audio_source(-1)
        windows.set_audio_source(1)
        self.assertIn(("set_muted", False), engine.calls)

    def test_the_volume_button_puts_the_chip_back_on_its_stream(self) -> None:
        """Measured against the code as it stood: the sound came back, the chip did not.

        Choose «Звук выключен» (the chip reads -1, the engine is muted), then press
        the volume button: `_toggle_mute` unmuted the engine and left the chip at -1,
        so the bar said «Звук выключен» over a player that was playing sound, and the
        chip's tooltip said nothing was being heard. §1.2 makes «Выключен» *be* the
        mute rather than a second kind of silence, which is exactly why the two have
        to agree in both directions.
        """
        engine = SourceEngine()
        windows = self.windows(engine)
        titles = [view.title() for view in windows.views]
        windows.set_audio_source(-1)
        self.assertEqual(windows.transport.audio_source(), -1)

        windows._toggle_mute()          # the volume button

        self.assertFalse(engine.is_muted)
        self.assertEqual(windows.transport.audio_source(), 0)
        self.assertEqual(windows.transport.audio_chip.label(), titles[0])
        self.assertIn("click to change", windows.transport.audio_chip.toolTip())

    def test_the_volume_button_puts_the_chip_out_as_well(self) -> None:
        """The other direction: muting from the button is the same silence."""
        engine = SourceEngine()
        windows = self.windows(engine)

        windows._toggle_mute()

        self.assertTrue(engine.is_muted)
        self.assertEqual(windows.transport.audio_source(), -1)
        self.assertEqual(
            windows.transport.audio_chip.label(),
            i18n.pick("windows.audio.off", "No sound"),
        )

    def test_the_chip_follows_the_stream_it_came_back_to(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        windows.set_audio_source(1)
        windows._toggle_mute()
        self.assertEqual(windows.transport.audio_source(), -1)
        windows._toggle_mute()
        self.assertEqual(windows.transport.audio_source(), 1)

    def test_an_engine_announcing_a_stream_while_muted_does_not_light_the_chip(
        self,
    ) -> None:
        """Silence is this player's state, not the engine's, so it wins on the bar."""
        engine = SourceEngine()
        windows = self.windows(engine)
        windows.set_audio_source(-1)

        engine.audio_index = 1
        engine.audio_changed.emit(1)

        self.assertEqual(windows.transport.audio_source(), -1)
        windows._toggle_mute()
        self.assertEqual(windows.transport.audio_source(), 1)

    def test_re_tiling_does_not_move_the_sound(self) -> None:
        """§1.2: the picture and the sound are two questions and two controls."""
        self.desk((0, 0, 2560, 1440))
        engine = SourceEngine()
        windows = self.windows(engine)
        windows.set_audio_source(1)
        before = engine.position_ms
        windows.apply_tiling("big_left")
        self.assertEqual(windows.transport.audio_source(), 1)
        self.assertEqual(engine.calls.count(("set_audio_source", 1)), 1)
        self.assertEqual(engine.position_ms, before)

    def test_an_engine_that_cannot_choose_has_no_chip(self) -> None:
        windows = self.make(StubEngine())
        self.assertTrue(windows.transport.audio_chip.isHidden())

    def test_an_engine_that_cannot_say_is_asked_through_the_recording(self) -> None:
        """§1.3: re-saving a set must not move its sound to the first screen.

        `SyncEngine` publishes no `audio_index`, so the adapter's answer used to be a
        flat 0 - and that number goes straight into the `"audio"` of the index that
        Save writes. A recorder-made set whose sound is on screen 2 was re-saved as
        one whose sound is on screen 1, which hands the clock to the other camera on
        the next open. `Recording.master_index()` was there all along and knows.
        """
        engine = StubEngine()
        engine.recording.master_index = lambda: 1
        self.assertEqual(player_window._Engine(engine).audio_index, 1)

    def test_a_recording_that_cannot_say_either_is_still_stream_zero(self) -> None:
        """No `master_index` and no `audio_index` is the pre-§1.2 world: stream 0."""
        self.assertEqual(player_window._Engine(StubEngine()).audio_index, 0)

    def test_the_engines_own_answer_still_wins(self) -> None:
        """An engine that *can* say is believed, -1 (no sound at all) included."""
        engine = SourceEngine()
        engine.recording.master_index = lambda: 1
        engine.audio_index = -1
        self.assertEqual(player_window._Engine(engine).audio_index, -1)

    def test_a_recording_that_raises_is_said_rather_than_swallowed(self) -> None:
        def boom() -> int:
            raise RuntimeError("no screens")

        engine = StubEngine()
        engine.recording.master_index = boom
        resolved = player_window._Engine(engine)
        self.assertEqual(resolved.audio_index, 0)
        self.assertTrue(any("master_index" in note for note in resolved.notes))


class SyncPanelTests(SourcesTestCase):
    """§2.2: the offset moves, and the timeline does not."""

    def panel(self, windows: player_window.PlayerWindows) -> player_window.SyncPanel:
        return windows.transport.sync_panel

    def test_the_button_is_there_and_the_panel_is_shut(self) -> None:
        windows = self.windows()
        self.assertFalse(windows.transport.sync_button.isHidden())
        self.assertFalse(windows.transport.is_sync_open())

    def test_opening_the_panel_grows_the_bar_and_moves_nothing(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        before = engine.position_ms
        height = windows.transport.height()
        windows.transport.toggle_sync()
        self.assertTrue(windows.transport.is_sync_open())
        self.assertEqual(
            windows.transport.height(), height + player_window.SYNC_HEIGHT
        )
        self.assertEqual(engine.position_ms, before)
        self.assertNotIn("seek", [call[0] for call in engine.calls])

    def test_a_notice_over_an_open_panel_adds_to_it(self) -> None:
        windows = self.windows()
        windows.transport.toggle_sync()
        tall = windows.transport.height()
        windows.transport.say("something happened")
        self.assertEqual(
            windows.transport.height(), tall + player_window.NOTICE_HEIGHT
        )
        windows.transport.clear_notice()
        self.assertEqual(windows.transport.height(), tall)
        self.assertTrue(windows.transport.is_sync_open())

    def test_a_typed_value_reaches_the_engine_without_moving_the_timeline(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        before = engine.position_ms
        self.panel(windows).picker.changed.emit("1")
        self.panel(windows).field.setValue(-1480)
        self.assertIn(("set_offset", 1, -1480), engine.calls)
        self.assertEqual(engine.position_ms, before)

    def test_a_frame_nudge_uses_the_recordings_own_rate(self) -> None:
        engine = SourceEngine()           # 25 fps, so one frame is 40 ms
        windows = self.windows(engine)
        before = engine.position_ms
        self.panel(windows).frame_on.click()
        self.assertIn(("set_offset", 0, 40), engine.calls)
        self.panel(windows).frame_back.click()
        self.assertIn(("set_offset", 0, 0), engine.calls)
        self.assertEqual(engine.position_ms, before)

    def test_the_coarse_nudges_are_a_hundred_milliseconds(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        self.panel(windows).coarse_on.click()
        self.assertIn(("set_offset", 0, player_window.NUDGE_MS), engine.calls)

    def test_reset_puts_the_stream_back_in_step(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        before = engine.position_ms
        windows.set_offset(1, 900)
        self.panel(windows).picker.changed.emit("1")
        self.panel(windows).reset_button.click()
        self.assertIn(("set_offset", 1, 0), engine.calls)
        self.assertEqual(engine.position_ms, before)

    def test_the_shift_and_alt_arrows_move_the_offset(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        before = engine.position_ms
        _press(
            windows,
            QtCore.Qt.Key.Key_Right,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        _press(
            windows,
            QtCore.Qt.Key.Key_Left,
            QtCore.Qt.KeyboardModifier.AltModifier,
        )
        self.assertIn(("set_offset", 0, 40), engine.calls)
        self.assertIn(("set_offset", 0, 40 - player_window.NUDGE_MS), engine.calls)
        self.assertNotIn("skip", [call[0] for call in engine.calls])
        self.assertEqual(engine.position_ms, before)

    def test_the_engines_answer_is_what_the_field_shows(self) -> None:
        """A value the engine clamped must not be contradicted by the control."""
        engine = SourceEngine()
        windows = self.windows(engine)
        engine._offsets[0] = 250
        engine.offsets_changed.emit()
        self.assertEqual(self.panel(windows).field.value(), 250)
        # And taking it back does not bounce it at the engine again.
        self.assertNotIn(("set_offset", 0, 250), engine.calls)

    def test_the_summary_names_the_stream_that_has_moved(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        windows.set_offset(1, 1200)
        self.assertIn(windows.views[1].title(), self.panel(windows).summary.text())
        self.assertIn("1200", self.panel(windows).summary.text())

    def test_an_offset_lights_the_button_while_the_panel_is_shut(self) -> None:
        """§2.2: an offset that is silently non-zero looks like a broken recording."""
        engine = SourceEngine()
        windows = self.windows(engine)
        self.assertFalse(windows.transport.sync_button.is_on())
        windows.set_offset(1, 1200)
        self.assertFalse(windows.transport.is_sync_open())
        self.assertTrue(windows.transport.sync_button.is_on())

    def test_a_typed_value_is_clamped(self) -> None:
        windows = self.windows()
        panel = self.panel(windows)
        self.assertEqual(panel.field.minimum(), -player_window.OFFSET_LIMIT_MS)
        self.assertEqual(panel.field.maximum(), player_window.OFFSET_LIMIT_MS)

    def test_typing_a_number_reseeks_once_and_not_once_per_digit(self) -> None:
        """§2.2: "without stopping and without a visible restart".

        Qt's default keyboard tracking made «1480» four offsets - 1, 14, 148, 1480 -
        and every one of them a real `setPosition` that flushes the decode pipeline.
        Three of the four are places the viewer never asked for, while playing.
        """
        panel = player_window.SyncPanel()
        self.addCleanup(panel.deleteLater)
        panel.set_streams(["Screen 1", "Screen 2"], [0, 0], 30.0)
        self.assertFalse(panel.field.keyboardTracking())
        seen: list[tuple[int, int]] = []
        panel.offset_requested.connect(lambda i, ms: seen.append((i, ms)))
        panel.field.setFocus()
        panel.field.selectAll()  # or the typing lands beside the 0 already there
        QtTest.QTest.keyClicks(panel.field, "1480")
        self.assertEqual(seen, [])
        QtTest.QTest.keyClick(panel.field, QtCore.Qt.Key.Key_Return)
        self.assertEqual(seen, [(0, 1480)])

    def test_the_frame_tooltip_names_the_rate_it_was_given(self) -> None:
        """A rate read back out of the rounded step is not the camera's rate.

        30 fps is 33.333 ms, the nudge has to be whole milliseconds, and dividing
        1000 by the rounded 33 gives 30.303 - a number no camera on the desk has.
        """
        for rate, shown in ((25.0, "25"), (30.0, "30"), (29.97, "29.97"),
                            (60.0, "60")):
            panel = player_window.SyncPanel()
            self.addCleanup(panel.deleteLater)
            panel.set_streams(["Screen 1", "Screen 2"], [0, 0], rate)
            self.assertIn(shown + " fps", panel.frame_on.toolTip(),
                          f"{rate} fps was announced as {panel.frame_on.toolTip()!r}")

    def test_an_offset_bigger_than_the_typed_limit_is_shown_as_it_is(self) -> None:
        """An index may name an offset no one would type, and it still plays.

        `engine.Recording` keeps whatever `offset_ms` the index holds and `SyncEngine`
        applies it, so two sources started 70 minutes apart are a real recording. The
        field used to clamp it to `OFFSET_LIMIT_MS` and then disagree with the summary
        standing next to it - the one disagreement this panel exists to prevent.
        """
        far = -(player_window.OFFSET_LIMIT_MS + 600_000)
        panel = player_window.SyncPanel()
        self.addCleanup(panel.deleteLater)
        panel.set_streams(["Screen 1", "Screen 2"], [0, far], 30.0)
        panel._picked("1")
        self.assertEqual(panel.field.value(), far)
        self.assertIn(str(far), panel.summary.text())

    def test_a_nudge_past_that_limit_moves_by_the_nudge_not_to_the_limit(self) -> None:
        """«+100 мс» moved the video ten minutes: the clamp ate the difference."""
        far = -(player_window.OFFSET_LIMIT_MS + 600_000)
        panel = player_window.SyncPanel()
        self.addCleanup(panel.deleteLater)
        panel.set_streams(["Screen 1", "Screen 2"], [0, far], 30.0)
        panel._picked("1")
        seen: list[tuple[int, int]] = []
        panel.offset_requested.connect(lambda i, ms: seen.append((i, ms)))
        panel.nudge(milliseconds=player_window.NUDGE_MS)
        self.assertEqual(seen[-1], (1, far + player_window.NUDGE_MS))
        # And outwards too, or one of the button's two directions is a silent no-op.
        panel.nudge(milliseconds=-player_window.NUDGE_MS)
        self.assertEqual(seen[-1], (1, far - player_window.NUDGE_MS))

    def test_an_ordinary_recording_keeps_the_typed_limit(self) -> None:
        """The widening is for the index that needs it and for nothing else."""
        panel = player_window.SyncPanel()
        self.addCleanup(panel.deleteLater)
        panel.set_streams(["Screen 1", "Screen 2"], [0, -1480], 30.0)
        self.assertEqual(panel.field.minimum(), -player_window.OFFSET_LIMIT_MS)
        self.assertEqual(panel.field.maximum(), player_window.OFFSET_LIMIT_MS)
        panel.set_offsets([0, -(player_window.OFFSET_LIMIT_MS + 1000)])
        self.assertLess(panel.field.minimum(), -player_window.OFFSET_LIMIT_MS)
        # Back in step: the field closes again rather than staying open for ever.
        panel.set_offsets([0, 0])
        self.assertEqual(panel.field.minimum(), -player_window.OFFSET_LIMIT_MS)

    def test_a_new_recording_narrows_the_field_again(self) -> None:
        """A set that needed the wider field must not leave it open for the next one."""
        panel = player_window.SyncPanel()
        self.addCleanup(panel.deleteLater)
        far = -(player_window.OFFSET_LIMIT_MS + 600_000)
        panel.set_streams(["Screen 1", "Screen 2"], [0, far], 30.0)
        self.assertLess(panel.field.minimum(), -player_window.OFFSET_LIMIT_MS)
        panel.set_streams(["Camera A", "Camera B"], [0, -1480], 25.0)
        self.assertEqual(panel.field.minimum(), -player_window.OFFSET_LIMIT_MS)
        self.assertEqual(panel.field.maximum(), player_window.OFFSET_LIMIT_MS)

    def test_an_engine_that_cannot_sync_has_no_button(self) -> None:
        windows = self.make(StubEngine())
        self.assertTrue(windows.transport.sync_button.isHidden())
        self.assertFalse(windows.transport.is_sync_open())

    def test_the_bar_paints_with_the_panel_open(self) -> None:
        """The new widgets have painters of their own; a crash in one is silent."""
        windows = self.windows()
        bar = windows.transport
        bar.toggle_sync()
        bar.resize(900, bar.height())
        for offset in (0, -1480):
            windows.set_offset(1, offset)
            for hovered in (False, True):
                bar.sync_panel.frame_on.setDown(hovered)
                shot = bar.grab()
                self.assertFalse(shot.isNull(), "the bar painted nothing")
                self.assertEqual(shot.width(), 900)

    def test_three_screens_get_the_sound_control_but_not_the_panel(self) -> None:
        """§2.2 is two streams by name; the picker has no third cell to offer."""
        engine = SourceEngine(
            [(0, 0, 1920, 1080), (1920, 0, 1920, 1080), (3840, 0, 1920, 1080)]
        )
        windows = self.make(engine)
        self.assertEqual(len(windows.views), 3)
        self.assertTrue(windows.transport.sync_button.isHidden())
        self.assertFalse(windows.transport.audio_chip.isHidden())
        windows.transport.set_sync_open(True)
        self.assertFalse(windows.transport.is_sync_open())
        _press(
            windows,
            QtCore.Qt.Key.Key_Right,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertNotIn("set_offset", [call[0] for call in engine.calls])
        self.assertIn(("skip", player_window.JUMP_MS), engine.calls)


class OpeningSomethingElseTests(SourcesTestCase):
    """§1: the player opens another recording in place of itself."""

    def opener(self, result: tuple[object, str]) -> mock.MagicMock:
        """Stand in for `open.open_recording` and record what it was handed."""
        from screenrec.player import open as sources

        patcher = mock.patch.object(
            sources, "open_recording", return_value=result
        )
        self.addCleanup(patcher.stop)
        return patcher.start()

    def successor(self, engine: StubEngine | None = None) -> QtCore.QObject:
        """A stand-in for the player that is about to replace this one."""

        class Successor(QtCore.QObject):
            def __init__(self) -> None:
                super().__init__()
                self.engine = engine
                self.shown = 0
                self.said: list[str] = []

            def show(self) -> None:
                self.shown += 1

            def say(self, text: str) -> None:
                self.said.append(text)

        made = Successor()
        self.addCleanup(made.deleteLater)
        # `_replace_with` hands the new player to the module to hold (see
        # `window._OPEN_PLAYERS`), and a stand-in never closes, so it would sit
        # there for the rest of the run.
        self.addCleanup(player_window._forget_player, made)
        return made

    def test_one_path_goes_through_as_a_path(self) -> None:
        windows = self.windows()
        new = self.successor()
        opener = self.opener((new, ""))
        self.assertTrue(windows.open_paths("B:/takes/one.mp4"))
        self.assertEqual(opener.call_args.args[0], "B:/takes/one.mp4")

    def test_two_paths_go_through_as_a_pair(self) -> None:
        windows = self.windows()
        opener = self.opener((self.successor(), ""))
        windows.open_paths(["B:/a.mp4", "B:/b.mp4"])
        self.assertEqual(opener.call_args.args[0], ["B:/a.mp4", "B:/b.mp4"])

    def test_opening_does_not_ask_the_layout_question_again(self) -> None:
        """The library has to stay clickable: §2.1's dialog is not re-shown.

        It used to be. `open_paths` left `layout` at None, so `open.open_recording`
        ran `choose_layout` and put the modal dialog in front of every library row the
        viewer clicked, every time, unless they had ticked «Запомнить мой выбор».
        `PlayerStage.open_paths` documents the opposite and `add_second_video` already
        passed the layout through; this was the one path that diverged.
        """
        windows = self.windows()
        opener = self.opener((self.successor(), ""))
        windows.open_paths("B:/takes/one.mp4")
        self.assertEqual(
            opener.call_args.kwargs.get("layout"), windows._layout_in_force()
        )
        self.assertTrue(opener.call_args.kwargs.get("layout"))

    def test_the_layout_passed_on_is_the_one_on_the_desk(self) -> None:
        """One window per monitor survives an Open, as it survives adding a camera."""
        windows = self.windows()
        opener = self.opener((self.successor(), ""))
        with mock.patch.object(
            type(windows),
            "_layout_in_force",
            return_value=player_window.layouts.LAYOUT_ONE_PER_MONITOR,
        ):
            windows.open_paths(["B:/a.mp4", "B:/b.mp4"])
        self.assertEqual(
            opener.call_args.kwargs.get("layout"),
            player_window.layouts.LAYOUT_ONE_PER_MONITOR,
        )

    def test_the_new_player_is_shown_held_and_announced(self) -> None:
        """Held by the module, not by the player it replaces.

        `_successor` used to be what kept it alive, which chained every player of a
        session to the one before it - `OpeningLetsGoOfTheOldPlayerTests` measures
        what that cost. It is still set while this player is open and cleared by
        `close_all`, and `window._open_players()` is what owns the new one.
        """
        windows = self.windows()
        new = self.successor()
        self.opener((new, ""))
        seen: list[object] = []
        held: list[object] = []
        windows.opened.connect(seen.append)
        windows.opened.connect(
            lambda _new: held.append(windows._successor)
        )
        windows.open_paths("B:/one.mp4")
        self.assertEqual(new.shown, 1)
        self.assertEqual(seen, [new])
        self.assertEqual(held, [new], "the successor was not set before `opened`")
        self.assertIn(new, player_window._open_players())
        self.assertIsNone(
            windows._successor, "the chain to the new player was not cut"
        )

    def test_this_player_closes_after_the_new_one_is_up(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        self.opener((self.successor(), ""))
        windows.open_paths("B:/one.mp4")
        self.assertIn(("close",), engine.calls)
        self.assertTrue(windows.transport.isHidden())

    def test_a_refusal_leaves_the_player_open_and_says_so(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        self.opener((None, "There is no file at B:/gone.mp4."))
        self.assertFalse(windows.open_paths("B:/gone.mp4"))
        self.assertNotIn(("close",), engine.calls)
        self.assertEqual(
            windows.transport.notice.text(), "There is no file at B:/gone.mp4."
        )

    def test_a_cancelled_dialog_says_nothing(self) -> None:
        """`open.py` answers a cancel with `(None, "")`; arguing with it is wrong."""
        windows = self.windows()
        self.opener((None, ""))
        self.assertFalse(windows.open_paths("B:/takes"))
        self.assertEqual(windows.transport.notice.text(), "")

    def test_adding_a_second_video_keeps_the_layout_and_the_position(self) -> None:
        """§1.1: the one allowed rebuild, and what it must not cost."""
        engine = SourceEngine([(0, 0, 1920, 1080)])
        engine.position_ms = 14_000
        engine.is_playing = True
        windows = self.make(engine)
        fresh = SourceEngine([(0, 0, 1920, 1080), (1920, 0, 1920, 1080)])
        fresh.position_ms = 0
        fresh.is_playing = False
        opener = self.opener((self.successor(fresh), ""))

        self.assertTrue(windows.add_second_video("B:/camera_b.mp4"))
        given = opener.call_args.args[0]
        self.assertEqual(len(given), 2)
        self.assertTrue(str(given[0]).endswith("take_screen0.mp4"))
        self.assertTrue(str(given[1]).endswith("camera_b.mp4"))
        # §1.1: the chooser is not re-shown - the layout in force goes through.
        self.assertEqual(opener.call_args.kwargs.get("layout"), "separate")
        self.assertEqual(fresh.position_ms, 14_000)
        self.assertTrue(fresh.is_playing)

    def test_the_position_is_asked_for_again_once_the_clock_has_read_the_file(
        self,
    ) -> None:
        """A `setPosition` before `setSource` lands can be dropped; §1.1 cannot."""
        engine = SourceEngine([(0, 0, 1920, 1080)])
        engine.position_ms = 9_000
        windows = self.make(engine)
        fresh = SourceEngine([(0, 0, 1920, 1080), (1920, 0, 1920, 1080)])
        self.opener((self.successor(fresh), ""))
        windows.add_second_video("B:/camera_b.mp4")
        fresh.position_ms = 0          # as a player that was not ready would be
        fresh.duration_changed.emit(60_000)
        self.assertEqual(fresh.position_ms, 9_000)

    def test_opening_a_different_recording_does_not_carry_the_position(self) -> None:
        engine = SourceEngine()
        engine.position_ms = 14_000
        windows = self.windows(engine)
        fresh = SourceEngine()
        fresh.position_ms = 0
        self.opener((self.successor(fresh), ""))
        windows.open_paths("B:/another.mp4")
        self.assertEqual(fresh.position_ms, 0)

    def test_one_per_monitor_is_the_layout_that_is_carried(self) -> None:
        self.desk((0, 0, 2560, 1440), (2560, 0, 1440, 2560))
        engine = SourceEngine([(0, 0, 1920, 1080)])
        windows = self.make(engine)
        windows._one_per_monitor = True
        opener = self.opener((self.successor(), ""))
        windows.add_second_video("B:/camera_b.mp4")
        self.assertEqual(
            opener.call_args.kwargs.get("layout"), "one_per_monitor"
        )


class SavingTheSetTests(SourcesTestCase):
    """§1.3: two files, an audio choice and an offset are work worth keeping."""

    def saver(self, result: tuple[object, str]) -> mock.MagicMock:
        from screenrec.player import open as sources

        patcher = mock.patch.object(sources, "save_set", return_value=result)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_the_files_the_audio_choice_and_the_offsets_all_go(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        windows.set_audio_source(1)
        windows.set_offset(1, -1480)
        saver = self.saver((Path("B:/takes/camera_a.screenrec.json"), ""))
        self.assertTrue(windows.save_set())
        files = saver.call_args.args[0]
        self.assertEqual(len(files), 2)
        self.assertEqual(saver.call_args.kwargs["audio_index"], 1)
        self.assertEqual(saver.call_args.kwargs["offsets"], [0, -1480])

    def test_where_it_went_is_said(self) -> None:
        windows = self.windows()
        self.saver((Path("B:/takes/camera_a.screenrec.json"), ""))
        windows.save_set()
        self.assertIn("camera_a.screenrec.json", windows.transport.notice.text())

    def test_a_refusal_is_said_and_is_not_claimed_as_a_save(self) -> None:
        windows = self.windows()
        self.saver((None, "B:/gone is not a folder, so the set was not saved."))
        self.assertFalse(windows.save_set())
        self.assertIn("not a folder", windows.transport.notice.text())

    def test_one_video_has_no_set_to_save(self) -> None:
        windows = self.make(SourceEngine([(0, 0, 1920, 1080)]))
        saver = self.saver((Path("B:/nothing.json"), ""))
        self.assertFalse(windows.save_set())
        self.assertEqual(saver.call_count, 0)
        self.assertTrue(windows.transport.notice.text())


class OneVideoRegressionTests(SourcesTestCase):
    """The regression that matters most: a plain one-video recording.

    None of §1 or §2 may make it worse, so this asks the questions a viewer would
    notice within a minute - how wide the bar is, what is on it, and what the keys
    they already use still do.
    """

    def test_the_bar_keeps_its_own_width_and_its_own_controls(self) -> None:
        windows = self.make(SourceEngine([(0, 0, 1920, 1080)]))
        bar = windows.transport
        self.assertEqual(bar.minimumWidth(), player_window.TRANSPORT_MIN_WIDTH)
        self.assertTrue(bar.arrange_button.isHidden())
        self.assertTrue(bar.audio_chip.isHidden())
        self.assertTrue(bar.sync_button.isHidden())
        self.assertFalse(bar.open_button.isHidden())

    def test_the_bar_is_placed_no_wider_than_the_documented_ceiling(self) -> None:
        """`TRANSPORT_MAX_WIDTH` is the number applied, not a number beside it.

        It used to be neither: `_place_transport` wrote a bare 760 while the constant
        said 900 and was read by nothing, so the comment explaining how wide the bar
        may get described a cap that did not exist and gave the wrong figure for the
        one that did.
        """
        self.desk((0, 0, 3840, 2160))
        windows = self.make(SourceEngine([(0, 0, 1920, 1080)]))
        windows.show()
        # Far more room than the cap, so the cap is what decides.
        self.assertEqual(
            windows.transport.width(), player_window.TRANSPORT_MAX_WIDTH
        )
        self.assertGreaterEqual(
            player_window.TRANSPORT_MAX_WIDTH, player_window.TRANSPORT_MIN_WIDTH_PAIR
        )

    def test_shift_arrow_still_skips(self) -> None:
        """§2.2 takes Shift+arrow only where there is something to slide."""
        engine = SourceEngine([(0, 0, 1920, 1080)])
        windows = self.make(engine)
        _press(
            windows,
            QtCore.Qt.Key.Key_Right,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertIn(("skip", player_window.JUMP_MS), engine.calls)
        self.assertNotIn("set_offset", [call[0] for call in engine.calls])

    def test_the_panel_cannot_be_opened_on_one_stream(self) -> None:
        windows = self.make(SourceEngine([(0, 0, 1920, 1080)]))
        height = windows.transport.height()
        windows.transport.set_sync_open(True)
        self.assertFalse(windows.transport.is_sync_open())
        self.assertEqual(windows.transport.height(), height)

    def test_the_bare_arrows_and_space_are_untouched(self) -> None:
        engine = SourceEngine([(0, 0, 1920, 1080)])
        windows = self.make(engine)
        _press(windows, QtCore.Qt.Key.Key_Space)
        _press(windows, QtCore.Qt.Key.Key_Left)
        self.assertIn(("toggle_play",), engine.calls)
        self.assertIn(("skip", -player_window.JUMP_MS), engine.calls)


class SourcesLanguageTests(SourcesTestCase):
    """Every word §1 and §2 added says itself again after a switch."""

    def setUp(self) -> None:
        super().setUp()
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)

    def test_the_panel_and_the_chips_change_language(self) -> None:
        windows = self.windows()
        panel = windows.transport.sync_panel
        english = (
            panel.reset_button.text_value(),
            panel.frame_back.text_value(),
            windows.transport.sync_button.toolTip(),
        )
        i18n.set_language(i18n.LANG_RU)
        self.assertNotEqual(
            (
                panel.reset_button.text_value(),
                panel.frame_back.text_value(),
                windows.transport.sync_button.toolTip(),
            ),
            english,
        )

    def test_the_waiting_badge_follows_the_language(self) -> None:
        engine = SourceEngine()
        windows = self.windows(engine)
        engine.begin_waiting(1)
        english = windows.views[1].badge.text()
        i18n.set_language(i18n.LANG_RU)
        self.assertNotEqual(windows.views[1].badge.text(), english)

    def test_a_badge_the_host_wrote_is_not_translated_away(self) -> None:
        view = player_window.ScreenView("Screen 2", QtMultimedia.QVideoSink())
        self.addCleanup(view.close)
        view.set_waiting(True, "the director said so")
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(view.badge.text(), "the director said so")

    def test_the_menu_is_built_in_the_language_in_force(self) -> None:
        from screenrec.player import library

        self.addCleanup(setattr, library, "find_entries", library.find_entries)
        library.find_entries = lambda folder, durations=True: []
        windows = self.windows()
        i18n.set_language(i18n.LANG_RU)
        menu = windows.open_menu()
        self.addCleanup(menu.deleteLater)
        self.assertIn(
            i18n.pick("windows.open.save", "Save this set"), menu_texts(menu)
        )
        self.assertNotIn("Save this set", menu_texts(menu))


#: The letter keys §6 of `docs/PLAYER_LAYOUTS.md` gives the whole player, with the
#: ЙЦУКЕН text the same physical key reports. Qt sends the Latin key code either way;
#: it is the text that changes, which is why both halves match on the text.
SHARED_LETTERS: tuple[tuple[QtCore.Qt.Key, str, str], ...] = (
    (QtCore.Qt.Key.Key_S, "s", "ы"),
    (QtCore.Qt.Key.Key_L, "l", "д"),
)


def _press_letter(
    windows: player_window.PlayerWindows,
    key: QtCore.Qt.Key,
    text: str,
) -> None:
    """Press a letter key on the transport, with the text a keyboard would send.

    `_key` and `_press` build their events without text, which is enough for the
    arrows and the digits. The letters are matched on `event.text()` - that is what
    makes `ы` and `д` work - so an event with no text is not the event a key press
    makes, and a test using one would pass against a window that answers neither.
    """
    QtWidgets.QApplication.sendEvent(
        windows.transport,
        QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress, int(key),
            QtCore.Qt.KeyboardModifier.NoModifier, text,
        ),
    )


class OpeningLetsGoOfTheOldPlayerTests(SourcesTestCase):
    """§1: opening another recording must not leave the old player in memory."""

    def flush(self) -> None:
        """Run far enough for Qt's deferred deletions to actually happen.

        `processEvents` alone does not do it: `deleteLater` posts a `DeferredDelete`
        event, which an event loop only delivers as it unwinds to the level the
        object was deleted at. A test that never enters `exec` has to drain that
        queue by hand, and a test that forgets to would measure the leak it is
        checking for.
        """
        self.app.processEvents()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def live(self, kind: type) -> int:
        gc.collect()
        return sum(1 for obj in gc.get_objects() if type(obj) is kind)

    def test_ten_opens_leave_one_player_and_not_eleven(self) -> None:
        """Measured here against the code as it stood: 11 players, 22 views, 11 bars.

        Three things held them. `close_all` only `close()`d its windows, which hides
        a frameless window and leaves the C++ object - and with it every
        `ScreenView.closed` lambda, each of which holds the `PlayerWindows`, in a
        cycle Python's collector cannot see through because one end of it is inside
        Qt. `i18n.notifier.changed` and `QGuiApplication.screenRemoved` each held a
        bound method of the player as well. And `_successor` chained each player to
        the one that replaced it, so the first - which `open.run` keeps a local for -
        held all ten of the others.

        Counted as a difference rather than as a total, because this process has run
        other tests and the collector is shared. Ten opens now add exactly one
        player, its two views and its bar: what is in front of the viewer.
        """
        kinds = (
            player_window.PlayerWindows,
            player_window.ScreenView,
            player_window.TransportBar,
        )
        self.flush()
        before = {kind: self.live(kind) for kind in kinds}

        current = player_window.PlayerWindows(SourceEngine())
        player_window._hold_player(current)
        current.show()
        for _round in range(10):
            following = player_window.PlayerWindows(SourceEngine())
            with mock.patch(
                "screenrec.player.open.open_recording", return_value=(following, "")
            ):
                self.assertTrue(current.open_paths("B:/another.mp4"))
            self.flush()
            current = following
        self.addCleanup(player_window._forget_player, current)
        self.addCleanup(current.close_all)
        self.flush()

        self.assertEqual(
            {kind.__name__: self.live(kind) - before[kind] for kind in kinds},
            {"PlayerWindows": 1, "ScreenView": 2, "TransportBar": 1},
        )

    def test_both_sides_of_a_closed_player_really_go(self) -> None:
        """Python and C++ both, and the C++ side not until `closed` has been heard.

        `open._free_clock_with` hangs the clock's own `deleteLater` on `closed`, so a
        window deleted before that signal would be a deleted object inside somebody
        else's teardown - the crash this package has already paid for twice. So the
        windows are still valid as `closed` is emitted and gone one turn later.
        """
        windows = player_window.PlayerWindows(SourceEngine())
        windows.show()
        view = windows.views[0]
        bar = windows.transport
        alive_at_closed: list[bool] = []
        windows.closed.connect(
            lambda: alive_at_closed.append(shiboken6.Shiboken.isValid(bar))
        )

        windows.close_all()
        self.assertEqual(alive_at_closed, [True])
        self.assertTrue(shiboken6.Shiboken.isValid(view))
        self.flush()
        self.assertFalse(shiboken6.Shiboken.isValid(view), "the view's C++ side stayed")
        self.assertFalse(shiboken6.Shiboken.isValid(bar), "the bar's C++ side stayed")

        gone = weakref.ref(windows)
        del windows, view, bar
        gc.collect()
        self.assertIsNone(gone(), "the player itself was never collected")

    def test_a_closed_player_is_wired_to_nothing_outside_it(self) -> None:
        """The wires are the half of the leak the collector cannot see.

        `i18n.notifier` outlives every player, so a connection into it holds a bound
        method of the player, which holds the player. Counted on the notifier itself
        rather than trusted to `_bonds`, so that forgetting to record a wire fails
        here too.
        """
        # Qt's own count of who is connected. `receivers` takes the C++ signature
        # with the `2` that SIGNAL() prepends, not the Python signal object.
        signature = "2changed(QString)"
        self.flush()
        before = i18n.notifier.receivers(signature)
        windows = player_window.PlayerWindows(SourceEngine())
        self.assertTrue(windows._bonds, "no outside wire was recorded at all")
        self.assertGreater(i18n.notifier.receivers(signature), before)

        windows.close_all()
        self.flush()
        self.assertEqual(windows._bonds, [])
        self.assertEqual(
            i18n.notifier.receivers(signature), before,
            "the player, its bar or its views are still listening for a language",
        )

    def test_adding_a_camera_still_carries_the_position_across_the_close(self) -> None:
        """§1.1's carry is the one wire `close_all` deliberately leaves in flight.

        The second `setPosition` has to happen after the new clock has read its file,
        which is after this player has gone, so `_unbind` is not given that wire and
        `_drop_carry` takes it out when it lands. The regression this guards is a
        tidy-up that disconnects everything and throws the viewer back to 00:00.
        """
        engine = SourceEngine([(0, 0, 1920, 1080)])
        engine.position_ms = 9_000
        windows = self.make(engine)
        fresh = SourceEngine([(0, 0, 1920, 1080), (1920, 0, 1920, 1080)])
        following = player_window.PlayerWindows(fresh)
        self.addCleanup(player_window._forget_player, following)
        self.addCleanup(following.close_all)
        with mock.patch(
            "screenrec.player.open.open_recording", return_value=(following, "")
        ):
            self.assertTrue(windows.add_second_video("B:/camera_b.mp4"))

        fresh.position_ms = 0          # as a clock that was not ready would be
        fresh.duration_changed.emit(60_000)
        self.assertEqual(fresh.position_ms, 9_000)
        # And it lets go afterwards, so the old player is not held by the new clock.
        self.assertIsNone(windows._carry_bond)
        fresh.position_ms = 0
        fresh.duration_changed.emit(60_000)
        self.assertEqual(fresh.position_ms, 0, "the carry fired a second time")


class TheSecondWindowComesBackTests(SeparateWindowsTestCase):
    """§5: a window put away comes back into its share, not on top of the other."""

    def test_it_lands_in_its_half_of_the_scheme(self) -> None:
        """Measured against the code as it stood: 100 % of view 1 inside view 0.

        On one 2560x1400 monitor with `equal_lr` in force, closing the second window
        and pressing «Вернуть раскладку» gives the first the whole screen - which
        `apply_tiling` does on purpose, because a scheme describes two windows and
        half a screen is not an arrangement of a window that is not there. The second
        then came back to the half it had left and was entirely underneath: view 0
        (0, 0, 2560, 1400), view 1 (1280, 0, 1280, 1400), overlap 1.000. The same on
        `equal_tb`, where view 1 came back to (0, 700, 2560, 700).
        """
        self.desk((0, 0, 2560, 1400))
        whole = QtCore.QRect(0, 0, 2560, 1400)
        windows = self.make(StubEngine())
        windows.show()
        for tiling in ("equal_lr", "equal_tb"):
            halves = player_window.layouts.tile_rects(whole, tiling)
            windows.apply_tiling(tiling)
            windows._view_closed(1)
            windows.restore_arrangement()
            self.assertEqual(
                windows.views[0].geometry(), whole,
                "the one window left open is meant to get the whole monitor",
            )

            windows.show_view(1)

            self.assertEqual(windows.views[0].geometry(), halves[0], tiling)
            self.assertEqual(windows.views[1].geometry(), halves[1], tiling)
            self.assertEqual(
                windows.views[0].geometry().intersected(windows.views[1].geometry()),
                QtCore.QRect(),
                f"{tiling}: the two windows still overlap",
            )

    def test_bringing_it_back_says_nothing_to_the_engine(self) -> None:
        """§6: an arrangement is `setGeometry` and nothing else, this one included."""
        self.desk((0, 0, 2560, 1400))
        engine = StubEngine()
        windows = self.make(engine)
        windows.show()
        windows.apply_tiling("equal_lr")
        windows._view_closed(1)
        windows.restore_arrangement()
        before = engine.position_ms
        engine.calls.clear()

        windows.show_view(1)

        self.assertEqual(engine.calls, [])
        self.assertEqual(engine.position_ms, before)

    def test_one_screen_per_monitor_gives_it_its_monitor_back(self) -> None:
        self.desk((0, 0, 2560, 1400), (2560, 0, 1440, 2560))
        windows = self.make(StubEngine())
        windows.show()
        self.assertTrue(windows.apply_one_per_monitor())
        places = [view.geometry() for view in windows.views]
        windows._view_closed(1)
        windows.show_view(1)
        self.assertEqual([view.geometry() for view in windows.views], places)


class AMenuThatOutlivesItsPlayerTests(SeparateWindowsTestCase):
    """§5's menu can still be pressed after the player has gone. Nothing may happen."""

    def test_every_entry_is_refused_once_the_player_has_closed(self) -> None:
        """Measured against the code as it stood, on a two-monitor desk.

        `close_all` is called while the menu is open - it is popped with
        `WA_DeleteOnClose` and parented to the transport, so it is deleted with the
        bar one turn of the event loop later, and until then it takes presses. Every
        entry acted: «Показать второй экран» put a frameless video window back on the
        desk and left it there, and `apply_tiling('equal_tb')`,
        `set_stream_monitor(0, 1)` and `restore_arrangement()` all moved windows that
        had just been closed, the first two answering True.
        """
        self.desk((0, 0, 2560, 1400), (2560, 0, 1440, 2560))
        windows = self.make(StubEngine())
        windows.show()
        menu = windows.arrange_menu()
        self.addCleanup(menu.deleteLater)
        windows.hide_view(1)

        windows.close_all()
        places = [view.geometry() for view in windows.views]

        self.assertFalse(windows.apply_tiling("equal_tb"))
        self.assertFalse(windows.apply_one_per_monitor())
        self.assertFalse(windows.set_stream_monitor(0, 1))
        self.assertFalse(windows.swap_windows())
        self.assertFalse(windows.cycle_arrangement())
        windows.restore_arrangement()
        windows.arrange()
        windows.set_tiling_monitor(1)
        windows.show_view(1)
        windows.hide_view(1)

        self.assertEqual([view.geometry() for view in windows.views], places)
        self.assertFalse(windows.views[1].isVisible(), "a window came back")
        self.assertTrue(windows.is_hidden(1))

    def test_pressing_the_menu_itself_changes_nothing(self) -> None:
        """The whole menu, triggered entry by entry, through the real connections."""
        self.desk((0, 0, 2560, 1400), (2560, 0, 1440, 2560))
        windows = self.make(StubEngine())
        windows.show()
        menu = windows.arrange_menu()
        self.addCleanup(menu.deleteLater)
        windows.close_all()
        places = [view.geometry() for view in windows.views]
        shown = [view.isVisible() for view in windows.views]

        for action in _every_action(menu):
            action.trigger()

        self.assertEqual([view.geometry() for view in windows.views], places)
        self.assertEqual([view.isVisible() for view in windows.views], shown)
        self.assertFalse(any(shown), "the windows were not closed to begin with")


def _every_action(menu: QtWidgets.QMenu) -> list[QtGui.QAction]:
    """Every entry in a menu and in its submenus, separators left out."""
    found: list[QtGui.QAction] = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        child = action.menu()
        if child is not None:
            found.extend(_every_action(child))
            continue
        found.append(action)
    return found


class SeparateWindowsKeyboardTests(SeparateWindowsTestCase):
    """§6's table is one table: `S` and `L` mean here what they mean on the stage.

    They used to mean nothing here. `PlayerStage.handle_key` answered both and this
    window answered neither, so the same two keys worked or did not depending on
    which half of the same player had focus - the complaint `_sync_key`'s docstring
    already makes about Shift+arrow.

    The mirror of `test_every_shared_letter_is_answered` is
    `tests/test_player_stage.py`'s `KeyboardTests`, which presses the same table at
    the other half.
    """

    def test_s_swaps_the_two_windows(self) -> None:
        self.desk((0, 0, 2560, 1400))
        windows = self.make(StubEngine())
        windows.show()
        windows.apply_tiling("equal_lr")
        left, right = (view.geometry() for view in windows.views)
        self.assertNotEqual(left, right)

        _press_letter(windows, QtCore.Qt.Key.Key_S, "s")

        self.assertTrue(windows.is_swapped())
        self.assertEqual(windows.views[0].geometry(), right)
        self.assertEqual(windows.views[1].geometry(), left)

    def test_the_swap_survives_restoring_the_arrangement(self) -> None:
        """Otherwise «Вернуть раскладку» would quietly undo it."""
        self.desk((0, 0, 2560, 1400))
        windows = self.make(StubEngine())
        windows.show()
        windows.apply_tiling("big_left")
        _press_letter(windows, QtCore.Qt.Key.Key_S, "s")
        swapped = [view.geometry() for view in windows.views]

        windows.restore_arrangement()
        self.assertEqual([view.geometry() for view in windows.views], swapped)
        windows.apply_tiling("big_left")
        self.assertEqual([view.geometry() for view in windows.views], swapped)

    def test_the_swap_says_nothing_to_the_engine(self) -> None:
        self.desk((0, 0, 2560, 1400))
        engine = StubEngine()
        windows = self.make(engine)
        windows.show()
        windows.apply_tiling("equal_lr")
        before = engine.position_ms
        engine.calls.clear()

        _press_letter(windows, QtCore.Qt.Key.Key_S, "s")

        self.assertEqual(engine.calls, [])
        self.assertEqual(engine.position_ms, before)

    def test_s_under_one_screen_per_monitor_reverses_the_pairing(self) -> None:
        """There the mapping is the arrangement, so that is what has to change."""
        self.desk((0, 0, 2560, 1400), (2560, 0, 1440, 2560))
        windows = self.make(StubEngine())
        windows.show()
        self.assertTrue(windows.apply_one_per_monitor())
        order = windows.pairing()

        _press_letter(windows, QtCore.Qt.Key.Key_S, "s")

        self.assertEqual(windows.pairing(), (order[1], order[0]))
        windows.restore_arrangement()
        self.assertEqual(windows.pairing(), (order[1], order[0]))

    def test_s_with_one_window_away_says_so_and_moves_nothing(self) -> None:
        self.desk((0, 0, 2560, 1400))
        windows = self.make(StubEngine())
        windows.show()
        windows.apply_tiling("equal_lr")
        windows.hide_view(1)
        places = [view.geometry() for view in windows.views]

        _press_letter(windows, QtCore.Qt.Key.Key_S, "s")

        self.assertEqual([view.geometry() for view in windows.views], places)
        self.assertFalse(windows.is_swapped())
        self.assertEqual(
            windows.transport.notice.text(),
            i18n._("Both screens have to be open before they can change places."),
        )

    def test_l_steps_through_the_arrangements_and_wraps(self) -> None:
        self.desk((0, 0, 2560, 1400))
        windows = self.make(StubEngine())
        windows.show()
        self.assertEqual(windows.arrangement(), "")
        seen = []
        for _step in range(len(player_window.ARRANGEMENT_CYCLE) + 1):
            _press_letter(windows, QtCore.Qt.Key.Key_L, "l")
            seen.append(windows.arrangement())
        self.assertEqual(
            seen,
            list(player_window.ARRANGEMENT_CYCLE[1:])
            + list(player_window.ARRANGEMENT_CYCLE[:2]),
        )

    def test_l_from_one_screen_per_monitor_starts_the_cycle(self) -> None:
        """It is not in the cycle, so the next press begins it rather than doing

        nothing - which is what an unlisted state would otherwise mean.
        """
        self.desk((0, 0, 2560, 1400), (2560, 0, 1440, 2560))
        windows = self.make(StubEngine())
        windows.show()
        self.assertTrue(windows.apply_one_per_monitor())
        _press_letter(windows, QtCore.Qt.Key.Key_L, "l")
        self.assertEqual(windows.arrangement(), player_window.ARRANGEMENT_CYCLE[0])

    def test_the_russian_keyboard_reaches_the_same_two(self) -> None:
        """On ЙЦУКЕН those keys report `ы` and `д`, as `PlayerStage` already knows."""
        self.desk((0, 0, 2560, 1400))
        windows = self.make(StubEngine())
        windows.show()
        windows.apply_tiling("equal_lr")
        _press_letter(windows, QtCore.Qt.Key.Key_S, "ы")
        self.assertTrue(windows.is_swapped())
        _press_letter(windows, QtCore.Qt.Key.Key_L, "д")
        self.assertEqual(windows.arrangement(), player_window.ARRANGEMENT_CYCLE[2])

    def test_every_shared_letter_is_answered(self) -> None:
        """`handle_key` takes the key rather than handing it back to Qt."""
        self.desk((0, 0, 2560, 1400))
        windows = self.make(StubEngine())
        windows.show()
        for code, latin, cyrillic in SHARED_LETTERS:
            for text in (latin, cyrillic):
                event = QtGui.QKeyEvent(
                    QtCore.QEvent.Type.KeyPress, int(code),
                    QtCore.Qt.KeyboardModifier.NoModifier, text,
                )
                self.assertTrue(
                    windows.handle_key(event), f"{text!r} was not answered",
                )

    def test_a_one_stream_recording_is_asked_nothing(self) -> None:
        """§1: it opens as it always has, and neither key invents an arrangement."""
        windows = self.make(StubEngine([(0, 0, 1920, 1080)]))
        windows.show()
        place = windows.views[0].geometry()
        self.assertFalse(windows.swap_windows())
        self.assertFalse(windows.cycle_arrangement())
        self.assertEqual(windows.views[0].geometry(), place)
        self.assertEqual(windows.arrangement(), "")

    def test_three_screens_are_left_alone_too(self) -> None:
        """§5's schemes place two windows, so there is no next arrangement."""
        self.desk((0, 0, 2560, 1400))
        windows = self.make(
            StubEngine([(0, 0, 1920, 1080), (1920, 0, 1920, 1080),
                        (3840, 0, 1920, 1080)])
        )
        windows.show()
        places = [view.geometry() for view in windows.views]
        self.assertFalse(windows.swap_windows())
        self.assertFalse(windows.cycle_arrangement())
        self.assertEqual([view.geometry() for view in windows.views], places)

    def test_the_arrange_menu_writes_the_swap_down(self) -> None:
        """A control and its help move together: the key is in the entry's words."""
        windows = self.make(StubEngine())
        menu = windows.arrange_menu()
        self.addCleanup(menu.deleteLater)
        self.assertIn(i18n._("Swap the screens (S)"), menu_texts(menu))

    def test_a_three_screen_menu_offers_no_swap(self) -> None:
        windows = self.make(
            StubEngine([(0, 0, 1920, 1080), (1920, 0, 1920, 1080),
                        (3840, 0, 1920, 1080)])
        )
        menu = windows.arrange_menu()
        self.addCleanup(menu.deleteLater)
        self.assertNotIn(i18n._("Swap the screens (S)"), menu_texts(menu))


if __name__ == "__main__":
    unittest.main()
