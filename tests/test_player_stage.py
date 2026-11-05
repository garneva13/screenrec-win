"""Checks for `screenrec.player.stage`.

The stage is the half of `docs/PLAYER_LAYOUTS.md` that puts both recorded screens in
one window, so the things that can be wrong here are the things a viewer would call
bugs rather than the things a reader would call mistakes. Three of them are checked
first and hardest, in the order §4 and §6 rank them.

* **A click is not a drag.** §4: "the single most annoying bug this feature can have".
  A press that slid across a pane must not be read as a press on it, and one that did
  not travel must be. Both are played out through real mouse events, through the real
  handlers, and what they did is counted at the engine rather than guessed at.
* **Nothing in this module may seek.** §6: "Changing the layout, swapping the main
  stream or re-tiling must not seek, reload or restart anything." The fake engine
  records *every* call made to it, so these tests do not assert that the position
  happens to be unchanged - they assert that the engine was not spoken to at all,
  which is the only version of the property that cannot be satisfied by a seek to the
  same millisecond.
* **The name follows the stream.** §3: "After a swap, Экран 1 is still Экран 1, now on
  the right." Checked on the labels of the panes themselves.

After those: the splitter's `MIN_PANE`, the three layouts and their chrome, the two §6
states arriving from the engine, §7's keyboard, the three signals the host saves from,
the clamp into a real screen, and both languages.

The engine and the sinks are fakes. No file is opened, no codec is touched and no
`QMediaPlayer` exists anywhere in this file; the Qt part runs under the offscreen
platform plugin, which is set before PySide6 is imported.
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Iterator

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtMultimedia, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from screenrec import i18n  # noqa: E402
from screenrec.player import engine as engine_module  # noqa: E402
from screenrec.player import layouts, stage as stage_module  # noqa: E402
# The stage's §2.2 panel, its limits and its nudge are `window`'s: one control on two
# transports. The numbers are read from there so that a change to them fails here too.
from screenrec.player import window as window_module  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


@contextmanager
def _in(code: str) -> Iterator[None]:
    """Run the block with `code` in force, and put the language back afterwards."""
    before = i18n.language()
    i18n.set_language(code)
    try:
        yield
    finally:
        i18n.set_language(before)


# --------------------------------------------------------------- the fakes


class FakeSink(QtCore.QObject):
    """Everything a pane asks of the engine's sink: one signal carrying frames."""

    videoFrameChanged = QtCore.Signal(QtMultimedia.QVideoFrame)

    def emit_frame(self, frame: QtMultimedia.QVideoFrame) -> None:
        self.videoFrameChanged.emit(frame)


def _frame(width: int = 16, height: int = 9) -> QtMultimedia.QVideoFrame:
    shape = QtMultimedia.QVideoFrameFormat(
        QtCore.QSize(width, height),
        QtMultimedia.QVideoFrameFormat.PixelFormat.Format_ARGB8888,
    )
    return QtMultimedia.QVideoFrame(shape)


@dataclass
class FakeScreen:
    """What `window.screen_titles` and `stage.streams_from` read off a screen."""

    monitor: int
    size: tuple[int, int]
    desktop: tuple[int, int, int, int]
    primary: bool = False
    #: What `PlayerStage._files` reads, and therefore what «Сохранить набор» would
    #: be asked to write. Last and defaulted, so every existing use of this stands.
    file: Path = Path("")


@dataclass
class FakeEntry:
    """One row of `library.find_entries`, as the Open menu reads it.

    The real `Entry` carries two more fields the menu never looks at. This is written
    out rather than imported so that a row the library stops producing fails the menu
    test here rather than passing it by inheriting a default.

    `duration_ms` is here and is deliberately never set: the menu lists the folder
    with `durations=False`, because a length costs an `ffprobe` per row and a menu
    has to appear on the click that asked for it.
    """

    path: Path
    name: str
    recorded: str
    size_bytes: int = 0
    duration_ms: int = 0
    screens: int = 1


@dataclass
class FakeRecording:
    screens: list[FakeScreen] = field(default_factory=list)
    framerate: float = 60.0
    #: Which screen the sidecar's `"audio"` names, as `Recording.master_index`
    #: answers it. §1.2's control opens on this and only ever moves it.
    audio_index: int = 0

    def master_index(self) -> int:
        return self.audio_index


class FakeEngine(QtCore.QObject):
    """One clock, recorded rather than run.

    Every call that could move the playhead, reload a file or change the playing state
    is appended to `calls`, so a test can assert that a layout change or a swap spoke
    to the engine **not at all** - which is the strong form of §6's demand and the only
    one a seek back to the same millisecond cannot pass.

    It publishes `seeking_changed` and `stream_ended` because the real `SyncEngine`
    does; `EnginelessStage` below leaves them out to prove the stage survives an engine
    that does not.
    """

    position_changed = QtCore.Signal(int)
    duration_changed = QtCore.Signal(int)
    state_changed = QtCore.Signal(bool)
    seeking_changed = QtCore.Signal(bool)
    stream_ended = QtCore.Signal(int)
    stream_waiting = QtCore.Signal(int, bool)
    offsets_changed = QtCore.Signal()
    finished = QtCore.Signal()
    problem = QtCore.Signal(str)

    def __init__(self, count: int = 2, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._sinks = [FakeSink(self) for _ in range(count)]
        self.recording = FakeRecording(
            screens=[
                FakeScreen(
                    monitor=index,
                    size=(2560, 1440) if index == 0 else (1440, 2560),
                    desktop=(index * 2560, 0, 2560, 1440),
                    primary=index == 0,
                    file=Path(f"camera_{index}.mp4"),
                )
                for index in range(count)
            ]
        )
        #: §2.1's offsets, and §1.2's audio source, as the real engine holds them:
        #: one list index-aligned with the screens, and one index.
        self._offsets = [0] * count
        self._waiting: set[int] = set()
        self.audio_index = 0
        self.calls: list[tuple[str, Any, ...]] = []
        self._position = 4200
        self._playing = False
        self._speed = 1.0
        self._muted = False
        self._ended: set[int] = set()
        self._seeking = False

    # -- what the stage reads

    def sinks(self) -> list[FakeSink]:
        return list(self._sinks)

    def position(self) -> int:
        return self._position

    def duration(self) -> int:
        return 60000

    def playing(self) -> bool:
        return self._playing

    def muted(self) -> bool:
        return self._muted

    def speed(self) -> float:
        return self._speed

    def seeking(self) -> bool:
        return self._seeking

    def stream_has_ended(self, index: int) -> bool:
        return index in self._ended

    def stream_duration(self, index: int) -> int:
        return 60000 if index == 0 else 40000

    def offset(self, index: int) -> int:
        return self._offsets[index] if 0 <= index < len(self._offsets) else 0

    def offsets(self) -> list[int]:
        return list(self._offsets)

    def stream_is_waiting(self, index: int) -> bool:
        return index in self._waiting

    # -- what the stage calls

    def toggle(self) -> None:
        self.calls.append(("toggle",))
        self._playing = not self._playing
        self.state_changed.emit(self._playing)

    def play(self) -> None:
        self.calls.append(("play",))
        self._playing = True
        self.state_changed.emit(True)

    def pause(self) -> None:
        self.calls.append(("pause",))
        self._playing = False
        self.state_changed.emit(False)

    def seek(self, milliseconds: int) -> None:
        self.calls.append(("seek", int(milliseconds)))
        self._position = int(milliseconds)
        self.position_changed.emit(self._position)

    def step(self, frames: int) -> None:
        self.calls.append(("step", int(frames)))

    def set_speed(self, rate: float) -> None:
        self.calls.append(("set_speed", float(rate)))
        self._speed = float(rate)

    def set_muted(self, muted: bool) -> None:
        self.calls.append(("set_muted", bool(muted)))
        self._muted = bool(muted)

    def set_volume(self, value: float) -> None:
        self.calls.append(("set_volume", float(value)))

    def close(self) -> None:
        self.calls.append(("close",))

    def set_offset(self, index: int, milliseconds: int) -> None:
        """§2.2's own call. Like the real one, it does **not** move the timeline.

        The position is deliberately left exactly where it was: the whole of §2.2 is
        that sliding one video against the other is not scrubbing, and a fake that
        quietly moved the playhead here would make the test that checks it pass for
        the wrong reason.
        """
        self.calls.append(("set_offset", int(index), int(milliseconds)))
        if 0 <= index < len(self._offsets):
            self._offsets[index] = int(milliseconds)
        self.offsets_changed.emit()

    def set_audio_source(self, index: int) -> None:
        self.calls.append(("set_audio_source", int(index)))
        self.audio_index = int(index)

    # -- what a test makes it say

    def begin_waiting(self, index: int) -> None:
        """The timeline is before this stream's first frame (§2.1)."""
        self._waiting.add(int(index))
        self.stream_waiting.emit(int(index), True)

    def end_waiting(self, index: int) -> None:
        self._waiting.discard(int(index))
        self.stream_waiting.emit(int(index), False)

    def begin_seek(self) -> None:
        self._seeking = True
        self.seeking_changed.emit(True)

    def land(self) -> None:
        self._seeking = False
        self.seeking_changed.emit(False)

    def end_stream(self, index: int) -> None:
        self._ended.add(index)
        self.stream_ended.emit(index)

    def scrub_to(self, milliseconds: int) -> None:
        """A seek as `SyncEngine` performs one, ended flags and all.

        `SyncEngine.seek` calls `_reset_ended_state` before it moves the players, so
        afterwards the engine no longer believes any stream has run out - and
        `stream_ended` carries only an index, so nothing it publishes will ever say
        so again. A stage that does not clear its badges on `seeking_changed` has no
        other chance to.
        """
        self.begin_seek()
        self._ended.clear()
        self.seek(milliseconds)
        self.land()

    def drift_to(self, milliseconds: int) -> None:
        """Where the master player actually is, without anyone having asked for it.

        `SyncEngine.position()` reads a real `QMediaPlayer` back, and the engine's
        own docstring measures it landing 33-67 ms from the number it was given. A
        transport that takes its `_position` from the poll therefore never recognises
        the millisecond its own drag asked for.
        """
        self._position = int(milliseconds)

    def names(self) -> list[str]:
        """Every call made, without its arguments."""
        return [entry[0] for entry in self.calls]


class QuietEngine(FakeEngine):
    """An engine with neither §6 signal, as a one-stream recording's engine may be.

    `SyncEngine` publishes both today, but the stage resolves them with `getattr` on
    purpose - §6's own answer for a single stream is that neither state exists for it -
    and a resolver that only ever meets an engine that has them is not a resolver.
    """

    seeking_changed = None  # type: ignore[assignment]
    stream_ended = None  # type: ignore[assignment]
    stream_waiting = None  # type: ignore[assignment]
    offsets_changed = None  # type: ignore[assignment]


class DeafEngine(FakeEngine):
    """A clock that cannot move its audio output between streams.

    `SyncEngine` gives the audio output to its master when it is built and has no
    call that moves it afterwards, so this is not a hypothetical engine: it is the
    one in the next module along, and §1.2's menu has to be honest about it rather
    than offer a row that does nothing.
    """

    set_audio_source = None  # type: ignore[assignment]


class UnmovableEngine(FakeEngine):
    """A clock with no offsets at all, as every engine before §2.1 was."""

    set_offset = None  # type: ignore[assignment]


# -------------------------------------------------------------- the fixture


class StageTestCase(unittest.TestCase):
    """A built, sized stage, and the mouse and the keyboard to drive it with."""

    #: Wide enough for two `MIN_PANE` panes with room to spare, in both directions.
    WIDTH = 960
    HEIGHT = 720

    def setUp(self) -> None:
        self.app = _app()
        i18n.set_language(i18n.LANG_EN)
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        self.made: list[QtWidgets.QWidget] = []

    def tearDown(self) -> None:
        for widget in reversed(self.made):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def make(
        self,
        engine: FakeEngine | None = None,
        **options: Any,
    ) -> stage_module.PlayerStage:
        """A stage with a real size, laid out, ready to be clicked on.

        `resize` before `show` rather than after: `show` places the window inside a
        screen, and a stage asked for a size afterwards would be reporting a geometry
        nothing had clamped - which is the one thing `place_on_screen` exists to stop
        happening by accident.
        """
        self.engine = engine if engine is not None else FakeEngine()
        stage = stage_module.PlayerStage(self.engine, **options)
        self.made.append(stage)
        stage.resize(self.WIDTH, self.HEIGHT)
        stage.show()
        self.app.processEvents()
        return stage

    def patch(self, target: Any, name: str, value: Any) -> None:
        """Put `value` on `target.name` for this test, and put the old one back.

        `unittest.mock` would do this; it is not used anywhere in this suite and one
        import for one line of `setattr` is not a dependency worth adding to a file
        that otherwise runs on nothing but the application's own modules.
        """
        before = getattr(target, name)
        setattr(target, name, value)
        self.addCleanup(setattr, target, name, before)

    # -- the mouse, in global coordinates, through the real handlers -------

    def _send(
        self,
        widget: QtWidgets.QWidget,
        kind: QtCore.QEvent.Type,
        local: QtCore.QPointF,
        buttons: QtCore.Qt.MouseButton,
    ) -> None:
        QtWidgets.QApplication.sendEvent(
            widget,
            QtGui.QMouseEvent(
                kind,
                local,
                QtCore.QPointF(widget.mapToGlobal(local.toPoint())),
                QtCore.Qt.MouseButton.LeftButton,
                buttons,
                QtCore.Qt.KeyboardModifier.NoModifier,
            ),
        )

    def press(self, widget: QtWidgets.QWidget, local: QtCore.QPointF) -> None:
        self._send(
            widget,
            QtCore.QEvent.Type.MouseButtonPress,
            local,
            QtCore.Qt.MouseButton.LeftButton,
        )

    def move_to(self, widget: QtWidgets.QWidget, local: QtCore.QPointF) -> None:
        self._send(
            widget,
            QtCore.QEvent.Type.MouseMove,
            local,
            QtCore.Qt.MouseButton.LeftButton,
        )

    def release(self, widget: QtWidgets.QWidget, local: QtCore.QPointF) -> None:
        self._send(
            widget,
            QtCore.QEvent.Type.MouseButtonRelease,
            local,
            QtCore.Qt.MouseButton.NoButton,
        )

    def tap(self, widget: QtWidgets.QWidget, local: QtCore.QPointF) -> None:
        """Press and release without travelling: a click by §4's own definition."""
        self.press(widget, local)
        self.release(widget, local)

    # -- the keyboard ------------------------------------------------------

    def key(
        self,
        widget: QtWidgets.QWidget,
        code: QtCore.Qt.Key,
        text: str = "",
    ) -> None:
        QtWidgets.QApplication.sendEvent(
            widget,
            QtGui.QKeyEvent(
                QtCore.QEvent.Type.KeyPress,
                code,
                QtCore.Qt.KeyboardModifier.NoModifier,
                text,
            ),
        )

    def modified_key(
        self,
        widget: QtWidgets.QWidget,
        code: QtCore.Qt.Key,
        modifier: QtCore.Qt.KeyboardModifier,
        text: str = "",
    ) -> None:
        """§2.2's keyboard is the plain arrows with Shift or Alt held."""
        QtWidgets.QApplication.sendEvent(
            widget,
            QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, code, modifier, text),
        )


# ============================================================ §4: click vs drag


class ClickIsNotADragTests(StageTestCase):
    """The first test written, because §4 says this is the worst bug available.

    With the floating thumbnail gone the gesture this protects is the one left on a
    pane: a click on the picture plays or pauses, and a press that slid across it on
    the way to the splitter handle must not. The engine is the witness, because
    "nothing happened" is the whole claim.
    """

    def test_a_clean_press_on_a_pane_reaches_the_engine(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        self.tap(stage.main, QtCore.QPointF(40.0, 40.0))
        self.app.processEvents()
        self.assertEqual(
            [call[0] for call in self.engine.calls],
            ["toggle"],
            "a click on the picture did not reach the transport",
        )

    def test_a_press_that_travelled_is_not_a_click(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        start = QtCore.QPointF(40.0, 40.0)
        self.press(stage.main, start)
        # One- and two-pixel steps, as a real mouse delivers them: travel is measured
        # from the press and never from the previous point, or none of these would
        # ever clear the slop.
        for step in range(1, layouts.DRAG_SLOP + 3):
            self.move_to(stage.main, QtCore.QPointF(start.x() + step, start.y()))
        self.release(
            stage.main,
            QtCore.QPointF(start.x() + layouts.DRAG_SLOP + 2, start.y()),
        )
        self.app.processEvents()
        self.assertEqual(self.engine.calls, [], "a drag was read as a click")

    def test_a_press_inside_the_slop_is_still_a_click(self) -> None:
        """The other end of the same rule: a hand that is not perfectly still."""
        stage = self.make()
        self.engine.calls.clear()
        start = QtCore.QPointF(40.0, 40.0)
        self.press(stage.main, start)
        self.move_to(stage.main, QtCore.QPointF(start.x() + 1, start.y()))
        self.release(stage.main, QtCore.QPointF(start.x() + 1, start.y()))
        self.app.processEvents()
        self.assertEqual([call[0] for call in self.engine.calls], ["toggle"])


# ================================================== §6: nothing here may seek


class NothingSeeksTests(StageTestCase):
    """The engine is not spoken to at all by anything in §6's list."""

    def test_switching_layout_never_touches_the_engine(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        before = self.engine.position()
        for name in stage_module.STAGE_ORDER + stage_module.STAGE_ORDER:
            stage.set_layout(name)
            self.app.processEvents()
        self.assertEqual(
            self.engine.calls, [], "a layout change called the engine"
        )
        self.assertEqual(self.engine.position(), before)

    def test_swapping_while_paused_moves_nothing(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        before = self.engine.position()
        stage.swap()
        self.app.processEvents()
        self.assertEqual(self.engine.calls, [], "a swap called the engine")
        self.assertEqual(self.engine.position(), before)
        self.assertFalse(self.engine.playing())

    def test_swapping_while_playing_leaves_it_playing(self) -> None:
        stage = self.make()
        self.engine.play()
        self.engine.calls.clear()
        before = self.engine.position()
        stage.swap()
        self.app.processEvents()
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.engine.position(), before)
        self.assertTrue(self.engine.playing(), "a swap stopped playback")

    def test_moving_the_splitter_never_touches_the_engine(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        stage.set_split(0.7)
        stage.equalise()
        self.app.processEvents()
        self.assertEqual(self.engine.calls, [])


# ================================================ §3: the name follows the stream


class SwapTests(StageTestCase):
    def test_the_label_travels_with_the_stream(self) -> None:
        stage = self.make()
        first, second = stage.main.label.text(), stage.side.label.text()
        self.assertNotEqual(first, second)
        stage.swap()
        self.app.processEvents()
        self.assertEqual(
            stage.main.label.text(), second, "the main pane kept the old name"
        )
        self.assertEqual(stage.side.label.text(), first)

    def test_the_panes_show_the_other_stream_after_a_swap(self) -> None:
        """Counted at the widget's own sink, not at the pane's bookkeeping."""
        stage = self.make()
        delivered: list[int] = []
        stage.main.video.videoSink().videoFrameChanged.connect(
            lambda frame: delivered.append(frame.width())
        )
        stage.swap()
        self.app.processEvents()

        self.engine.sinks()[0].emit_frame(_frame(32, 18))
        self.assertEqual(delivered, [], "the main pane still listens to the old stream")
        self.engine.sinks()[1].emit_frame(_frame(48, 27))
        self.assertEqual(delivered, [48], "the main pane never heard the new stream")

    def test_swapped_is_reported_once_and_says_which_way(self) -> None:
        stage = self.make()
        said: list[bool] = []
        stage.swapped.connect(said.append)
        stage.swap()
        stage.swap()
        self.assertEqual(said, [True, False])
        self.assertFalse(stage.is_swapped())

    def test_the_digits_pick_a_main_stream_and_do_not_toggle(self) -> None:
        stage = self.make()
        said: list[bool] = []
        stage.swapped.connect(said.append)
        stage.set_main_stream(0)  # already the main one
        self.assertEqual(said, [], "asking for the stream that is already main swapped")
        stage.set_main_stream(1)
        stage.set_main_stream(1)
        self.assertEqual(said, [True], "the second request swapped back")

    def test_a_stream_that_is_not_on_the_stage_is_refused(self) -> None:
        stage = self.make()
        stage.set_main_stream(7)
        self.assertEqual(stage.main_stream(), 0)

    def test_set_swapped_restores_a_remembered_choice(self) -> None:
        stage = self.make(swapped=True)
        self.assertTrue(stage.is_swapped())
        self.assertEqual(stage.main.stream_index(), 1)
        stage.set_swapped(False)
        self.assertFalse(stage.is_swapped())

    def test_a_one_stream_stage_has_nothing_to_swap(self) -> None:
        """The constructor says in as many words that it opens on fewer than two."""
        stage = self.make(FakeEngine(count=1))
        said: list[bool] = []
        stage.swapped.connect(said.append)

        stage.swap()
        self.app.processEvents()

        self.assertEqual(stage.main_stream(), 0, "a stream the recording has not")
        self.assertEqual(stage.main.stream_index(), 0)
        self.assertFalse(stage.is_swapped(), "a one-stream stage reported a swap")
        self.assertEqual(said, [], "a host would have stored player_swapped = True")
        self.assertEqual(stage.side.title(), "", "the only screen was duplicated")

    def test_a_remembered_swap_cannot_reverse_a_single_stream(self) -> None:
        stage = self.make(FakeEngine(count=1), swapped=True)
        self.assertFalse(stage.is_swapped())
        self.assertEqual(stage.main.stream_index(), 0)
        self.assertEqual(stage.main.title(), stage.streams()[0].title)

    def test_a_one_stream_stage_offers_no_swap_control(self) -> None:
        stage = self.make(FakeEngine(count=1), layout=layouts.LAYOUT_FOCUS)
        self.assertFalse(stage.swap_button.isEnabled())
        self.assertFalse(stage.switch.isVisible())

    def test_streams_with_a_gap_in_their_indices_keep_their_own_sinks(self) -> None:
        """A host may hand the stage screens 0 and 2 of a longer recording.

        `_order` used to be the positions 0 and 1 whatever the streams were called,
        so the side pane was given sink 1 - a stream the stage does not believe it
        is showing - and an untouched stage reported itself swapped.
        """
        engine = FakeEngine(count=3)
        stage = self.make(engine, streams=[
            layouts.Stream(0, "A", (1920, 1080)),
            layouts.Stream(2, "C", (1280, 720)),
        ])
        self.assertEqual(stage.side.title(), "C")
        self.assertFalse(stage.is_swapped(), "an untouched stage reported a swap")

        delivered: list[int] = []
        stage.side.video.videoSink().videoFrameChanged.connect(
            lambda frame: delivered.append(frame.width())
        )
        engine.sinks()[1].emit_frame(_frame(32, 18))
        self.assertEqual(delivered, [], "the side pane listens to the wrong stream")
        engine.sinks()[2].emit_frame(_frame(48, 27))
        self.assertEqual(delivered, [48], "the side pane never heard its own stream")

        stage.swap()
        self.app.processEvents()
        self.assertEqual(stage.main.title(), "C")
        self.assertEqual(stage.side.title(), "A")
        self.assertTrue(stage.is_swapped())


# ================================================================ the layouts


class LayoutTests(StageTestCase):
    def test_side_by_side_splits_across(self) -> None:
        stage = self.make(layout=layouts.LAYOUT_SIDE_BY_SIDE)
        self.assertEqual(
            stage.splitter.orientation(), QtCore.Qt.Orientation.Horizontal
        )
        self.assertTrue(stage.side.isVisible())
        self.assertTrue(stage.even_button.isVisible())
        self.assertFalse(stage.switch.isVisible())

    def test_stacked_splits_down(self) -> None:
        stage = self.make(layout=layouts.LAYOUT_STACKED)
        self.assertEqual(stage.splitter.orientation(), QtCore.Qt.Orientation.Vertical)
        self.assertTrue(stage.side.isVisible())

    def test_focus_shows_one_pane_and_the_switch(self) -> None:
        stage = self.make(layout=layouts.LAYOUT_FOCUS)
        self.assertFalse(stage.side.isVisible())
        self.assertTrue(stage.switch.isVisible())
        self.assertFalse(stage.even_button.isVisible())

    def test_an_unknown_layout_is_refused_rather_than_applied(self) -> None:
        stage = self.make()
        said: list[str] = []
        stage.layout_changed.connect(said.append)
        stage.set_layout("in_a_grid")
        self.assertEqual(stage.layout_id(), layouts.LAYOUT_SIDE_BY_SIDE)
        self.assertEqual(said, [])

    def test_an_unknown_layout_in_the_constructor_opens_the_default(self) -> None:
        stage = self.make(layout="all_at_once")
        self.assertEqual(stage.layout_id(), stage_module.STAGE_ORDER[0])

    def test_cycling_visits_all_four_and_comes_back(self) -> None:
        stage = self.make()
        said: list[str] = []
        stage.layout_changed.connect(said.append)
        for _ in stage_module.STAGE_ORDER:
            stage.cycle_layout()
        self.assertEqual(said, list(stage_module.STAGE_ORDER[1:]) + [
            stage_module.STAGE_ORDER[0]
        ])
        self.assertEqual(stage.layout_id(), stage_module.STAGE_ORDER[0])

    def test_the_same_layout_again_announces_nothing(self) -> None:
        stage = self.make(layout=layouts.LAYOUT_STACKED)
        said: list[str] = []
        stage.layout_changed.connect(said.append)
        stage.set_layout(layouts.LAYOUT_STACKED)
        self.assertEqual(said, [])

    def test_the_menu_offers_the_three_stage_layouts_in_order(self) -> None:
        stage = self.make()
        texts = [action.text() for action in stage.layout_menu.actions()]
        self.assertEqual(
            texts, [layouts.title(name) for name in stage_module.STAGE_ORDER]
        )

    def test_a_menu_entry_switches_the_layout(self) -> None:
        stage = self.make()
        stage.layout_menu.actions()[2].trigger()
        self.assertEqual(stage.layout_id(), stage_module.STAGE_ORDER[2])


# ================================================================ the splitter


class SplitterTests(StageTestCase):
    def test_a_fraction_below_the_floor_still_leaves_min_pane(self) -> None:
        stage = self.make()
        stage.set_split(0.0)
        self.app.processEvents()
        sizes = stage.splitter.sizes()
        self.assertGreaterEqual(
            sizes[0], layouts.MIN_PANE, f"{sizes} ignores MIN_PANE"
        )
        self.assertGreaterEqual(sizes[1], layouts.MIN_PANE)

    def test_a_fraction_above_the_ceiling_still_leaves_min_pane(self) -> None:
        stage = self.make()
        stage.set_split(1.0)
        self.app.processEvents()
        sizes = stage.splitter.sizes()
        self.assertGreaterEqual(sizes[0], layouts.MIN_PANE)
        self.assertGreaterEqual(sizes[1], layouts.MIN_PANE, f"{sizes} ignores MIN_PANE")

    def test_the_stored_fraction_is_held_to_the_contracts_range(self) -> None:
        stage = self.make()
        stage.set_split(4.0)
        self.assertEqual(stage.split(), stage_module.SPLIT_MAX)
        stage.set_split(-1.0)
        self.assertEqual(stage.split(), stage_module.SPLIT_MIN)

    def test_a_fraction_that_is_not_a_number_falls_back_to_half(self) -> None:
        stage = self.make()
        stage.set_split(float("nan"))
        self.assertEqual(stage.split(), 0.5)
        stage.set_split("three quarters")  # type: ignore[arg-type]
        self.assertEqual(stage.split(), 0.5)

    def test_a_settings_file_full_of_nonsense_still_opens_a_stage(self) -> None:
        """Every one of these is a value a hand-edited settings file can hold."""
        stage = self.make(
            layout="",
            split="0,7",  # type: ignore[arg-type]
        )
        self.app.processEvents()
        self.assertEqual(stage.layout_id(), stage_module.STAGE_ORDER[0])
        self.assertEqual(stage.split(), 0.5)

    def test_a_remembered_fraction_is_applied_when_the_stage_has_a_size(self) -> None:
        stage = self.make(split=0.7)
        self.app.processEvents()
        sizes = stage.splitter.sizes()
        total = sum(sizes)
        self.assertGreater(total, 0)
        self.assertAlmostEqual(sizes[0] / total, 0.7, delta=0.03)

    def test_even_up_returns_to_halves_and_says_so(self) -> None:
        stage = self.make(split=0.75)
        self.app.processEvents()
        said: list[float] = []
        stage.split_changed.connect(said.append)
        stage.equalise()
        self.app.processEvents()
        sizes = stage.splitter.sizes()
        self.assertAlmostEqual(sizes[0] / sum(sizes), 0.5, delta=0.03)
        self.assertEqual(said, [0.5])

    def test_a_double_click_on_the_handle_evens_them_up(self) -> None:
        stage = self.make(split=0.8)
        self.app.processEvents()
        handle = stage.splitter.handle(1)
        self.assertIsNotNone(handle, "the splitter grew no handle")
        QtWidgets.QApplication.sendEvent(
            handle,
            QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseButtonDblClick,
                QtCore.QPointF(2.0, 2.0),
                QtCore.QPointF(handle.mapToGlobal(QtCore.QPoint(2, 2))),
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
            ),
        )
        self.app.processEvents()
        self.assertEqual(stage.split(), 0.5)


# ============================================================ the two §6 states


class EngineStateTests(StageTestCase):
    def test_a_seek_in_flight_dims_every_pane(self) -> None:
        stage = self.make()
        self.assertFalse(any(pane.is_busy() for pane in stage._panes()))
        self.engine.begin_seek()
        self.app.processEvents()
        self.assertTrue(
            all(pane.is_busy() for pane in stage._panes()),
            "a pane was left showing the old moment",
        )
        self.engine.land()
        self.app.processEvents()
        self.assertFalse(any(pane.is_busy() for pane in stage._panes()))

    def test_a_stage_built_during_a_seek_opens_dimmed(self) -> None:
        engine = FakeEngine()
        engine.begin_seek()
        stage = self.make(engine)
        self.assertTrue(all(pane.is_busy() for pane in stage._panes()))

    def test_a_stream_that_ran_out_is_badged_on_the_pane_showing_it(self) -> None:
        stage = self.make()
        self.engine.end_stream(1)
        self.app.processEvents()
        self.assertFalse(stage.main.has_ended(), "the wrong pane was badged")
        self.assertTrue(stage.side.has_ended())

    def test_the_badge_survives_a_swap(self) -> None:
        """`set_stream` clears it on purpose; the stage has to put it back."""
        stage = self.make()
        self.engine.end_stream(1)
        self.app.processEvents()
        stage.swap()
        self.app.processEvents()
        self.assertTrue(stage.main.has_ended(), "the badge did not follow the stream")
        self.assertFalse(stage.side.has_ended())

    def test_a_seek_takes_the_ended_badge_away(self) -> None:
        """§6: the engine clears its own ended flags on a seek, and says no more.

        `stream_ended` carries an index and there is no signal that a stream has
        *un*-ended, so a badge left standing here sits over a picture that is
        playing again for the rest of the session. A later swap repaired it by
        accident, because `_apply_streams` asks the engine again - which is exactly
        why the defect read as working.
        """
        stage = self.make()
        self.engine.end_stream(1)
        self.app.processEvents()
        self.assertTrue(stage.side.has_ended())

        self.engine.scrub_to(20000)
        self.app.processEvents()

        self.assertFalse(self.engine.stream_has_ended(1))
        self.assertFalse(stage.side.has_ended(), "the badge outlived the recording")
        self.assertFalse(stage.side.badge.isVisible())

    def test_a_stream_that_is_still_over_is_badged_again_after_a_seek(self) -> None:
        """Clearing is for the seek, not for the state: the engine is asked again."""
        stage = self.make()
        self.engine.end_stream(1)
        self.app.processEvents()
        self.engine.begin_seek()
        self.app.processEvents()
        self.assertFalse(stage.side.has_ended(), "the veil did not clear the badge")
        self.engine.land()
        self.engine.end_stream(1)
        self.app.processEvents()
        self.assertTrue(stage.side.has_ended())

    def test_a_stage_built_after_a_stream_ended_still_badges_it(self) -> None:
        engine = FakeEngine()
        engine.end_stream(1)
        stage = self.make(engine)
        self.assertTrue(stage.side.has_ended())

    def test_an_engine_with_neither_signal_is_not_an_error(self) -> None:
        stage = self.make(QuietEngine())
        self.assertFalse(any(pane.is_busy() for pane in stage._panes()))
        self.assertFalse(any(pane.has_ended() for pane in stage._panes()))

    def test_the_engines_complaints_reach_the_notice_strip(self) -> None:
        stage = self.make()
        self.engine.problem.emit("Screen 2 will not stay in step.")
        self.app.processEvents()
        self.assertTrue(stage.notice.isVisible())
        self.assertIn("will not stay in step", stage.notice.text())
        stage.clear_notice()
        self.assertFalse(stage.notice.isVisible())


# ================================================================== §7: the keys


class KeyboardTests(StageTestCase):
    def test_space_reaches_the_engine(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        self.key(stage, QtCore.Qt.Key.Key_Space, " ")
        self.assertEqual(self.engine.names(), ["toggle"])

    def test_the_arrows_skip(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        self.key(stage, QtCore.Qt.Key.Key_Right)
        self.key(stage, QtCore.Qt.Key.Key_Left)
        # The fake publishes no `skip`, so `_Engine` falls back to an absolute seek -
        # the same path a real engine without one would take.
        self.assertEqual(self.engine.names(), ["seek", "seek"])
        self.assertEqual(self.engine.calls[0][1], 4200 + stage_module.JUMP_MS)

    def test_comma_and_full_stop_step_one_frame(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        self.key(stage, QtCore.Qt.Key.Key_Comma, ",")
        self.key(stage, QtCore.Qt.Key.Key_Period, ".")
        self.assertEqual(self.engine.calls, [("step", -1), ("step", 1)])

    def test_the_digits_choose_the_main_stream(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        self.key(stage, QtCore.Qt.Key.Key_2, "2")
        self.assertEqual(stage.main_stream(), 1)
        self.key(stage, QtCore.Qt.Key.Key_1, "1")
        self.assertEqual(stage.main_stream(), 0)
        self.assertEqual(self.engine.calls, [], "picking a stream called the engine")

    def test_s_swaps_and_l_cycles_the_layout(self) -> None:
        stage = self.make()
        self.key(stage, QtCore.Qt.Key.Key_S, "s")
        self.assertTrue(stage.is_swapped())
        self.key(stage, QtCore.Qt.Key.Key_L, "l")
        self.assertEqual(stage.layout_id(), stage_module.STAGE_ORDER[1])

    def test_the_russian_keyboard_reaches_the_same_two_shortcuts(self) -> None:
        """On ЙЦУКЕН those keys report `ы` and `д`, not `Key_S` and `Key_L`."""
        stage = self.make()
        self.key(stage, QtCore.Qt.Key(0x44B), "ы")
        self.assertTrue(stage.is_swapped())
        self.key(stage, QtCore.Qt.Key(0x434), "д")
        self.assertEqual(stage.layout_id(), stage_module.STAGE_ORDER[1])

    def test_every_shared_letter_is_answered(self) -> None:
        """§6's table is one table, and the other half is pressed in the same words.

        `tests/test_player_window.py`'s `SeparateWindowsKeyboardTests` sends this
        same pair at the separate-windows half. The two used to disagree: `S` and
        `L` worked here and did nothing there, so what a key meant depended on which
        window of one player had the focus.
        """
        stage = self.make()
        for code, latin, cyrillic in (
            (QtCore.Qt.Key.Key_S, "s", "ы"),
            (QtCore.Qt.Key.Key_L, "l", "д"),
        ):
            for typed in (latin, cyrillic):
                event = QtGui.QKeyEvent(
                    QtCore.QEvent.Type.KeyPress, int(code),
                    QtCore.Qt.KeyboardModifier.NoModifier, typed,
                )
                self.assertTrue(
                    stage.handle_key(event), f"{typed!r} was not answered"
                )

    def test_the_stream_switch_names_the_two_keys_that_move_it(self) -> None:
        """§6 marks `1` and `2` "(stage layouts)", so this is where they are written.

        The separate-windows half has no main stream to choose - every window there
        is already showing its own - so the two keys belong to this window alone and
        the control they move is the one that has to say so.
        """
        stage = self.make(layout=layouts.LAYOUT_FOCUS)
        self.assertIn("1, 2", stage.switch.toolTip())

    def test_home_and_end_seek_to_the_two_ends(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        self.key(stage, QtCore.Qt.Key.Key_Home)
        self.key(stage, QtCore.Qt.Key.Key_End)
        self.assertEqual(self.engine.calls, [("seek", 0), ("seek", 60000)])

    def test_escape_closes_the_stage(self) -> None:
        stage = self.make()
        said: list[int] = []
        stage.closed.connect(lambda: said.append(1))
        self.key(stage, QtCore.Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertEqual(said, [1])

    def test_escape_leaves_full_screen_before_it_closes(self) -> None:
        stage = self.make()
        said: list[int] = []
        stage.closed.connect(lambda: said.append(1))
        stage.toggle_full_screen()
        self.app.processEvents()
        if not stage.isFullScreen():
            self.skipTest("the offscreen plugin refused full screen")
        self.key(stage, QtCore.Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(stage.isFullScreen())
        self.assertEqual(said, [], "Esc closed the player instead of the full screen")

    def test_an_unused_key_is_handed_back(self) -> None:
        stage = self.make()
        event = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Q,
            QtCore.Qt.KeyboardModifier.NoModifier,
            "q",
        )
        self.assertFalse(stage.handle_key(event))

    def test_the_focus_switch_does_not_eat_space(self) -> None:
        """§3: "`Tab` moves to it, `Space` still plays"."""
        stage = self.make(layout=layouts.LAYOUT_FOCUS)
        stage.switch.setFocus(QtCore.Qt.FocusReason.TabFocusReason)
        self.app.processEvents()
        self.engine.calls.clear()
        self.key(stage.switch, QtCore.Qt.Key.Key_Space, " ")
        self.app.processEvents()
        self.assertEqual(stage.main_stream(), 0, "Space moved the switch")

    def test_the_focus_switch_answers_the_arrows(self) -> None:
        stage = self.make(layout=layouts.LAYOUT_FOCUS)
        self.key(stage.switch, QtCore.Qt.Key.Key_Right)
        self.app.processEvents()
        self.assertEqual(stage.main_stream(), 1)
        self.key(stage.switch, QtCore.Qt.Key.Key_Left)
        self.app.processEvents()
        self.assertEqual(stage.main_stream(), 0)

    def test_the_switch_follows_a_swap_made_elsewhere(self) -> None:
        stage = self.make(layout=layouts.LAYOUT_FOCUS)
        stage.swap()
        self.assertEqual(stage.switch.current, "1")


# ============================================================== the transport


class TransportTests(StageTestCase):
    def test_the_clock_and_the_scrubber_open_on_the_engines_numbers(self) -> None:
        stage = self.make()
        self.assertEqual(stage.scrubber.maximum(), 60000)
        self.assertIn("01:00", stage.clock.text())
        self.assertIn("00:04", stage.clock.text())

    def test_play_pause_and_the_frame_steps_reach_the_engine(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        stage.play_button.click()
        stage.back_button.click()
        stage.forward_button.click()
        self.assertEqual(
            self.engine.calls, [("toggle",), ("step", -1), ("step", 1)]
        )

    def test_the_play_glyph_follows_the_engine(self) -> None:
        stage = self.make()
        self.engine.play()
        self.app.processEvents()
        self.assertEqual(stage.play_button.kind, "pause")
        self.engine.pause()
        self.app.processEvents()
        self.assertEqual(stage.play_button.kind, "play")

    def test_the_speed_chip_reaches_the_engine(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        stage.speed_chip.click()
        self.assertEqual(self.engine.names(), ["set_speed"])

    def test_the_volume_button_mutes_and_unmutes(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        stage.volume_button.click()
        self.assertEqual(self.engine.calls, [("set_muted", True)])
        self.assertEqual(stage.volume_button.kind, "volume_off")
        stage.volume_button.click()
        self.assertEqual(stage.volume_button.kind, "volume")

    def test_the_wheel_is_the_volume(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        QtWidgets.QApplication.sendEvent(
            stage,
            QtGui.QWheelEvent(
                QtCore.QPointF(10.0, 10.0),
                QtCore.QPointF(stage.mapToGlobal(QtCore.QPoint(10, 10))),
                QtCore.QPoint(0, 0),
                QtCore.QPoint(0, -120),
                QtCore.Qt.MouseButton.NoButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
                QtCore.Qt.ScrollPhase.NoScrollPhase,
                False,
            ),
        )
        self.assertEqual(self.engine.names(), ["set_volume"])
        self.assertAlmostEqual(self.engine.calls[0][1], 0.95)

    def test_dragging_the_scrubber_seeks_once(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        stage.scrubber.setSliderDown(True)
        stage.scrubber.setSliderPosition(30000)
        stage.scrubber.setSliderDown(False)
        self.assertEqual(self.engine.calls, [("seek", 30000)])

    def test_releasing_the_scrubber_does_not_seek_a_second_time(self) -> None:
        """`_scrub_ended`'s own comment: a repeat is a visible hitch and buys nothing.

        The poll timer calls `transport_refresh` throughout the drag, and the engine
        reports where the master player *actually* is rather than the millisecond it
        was given, so a `_position` written from the poll never matches the released
        value and the guard in `_scrub_ended` can never hold.
        """
        stage = self.make()
        self.engine.calls.clear()
        stage.scrubber.setSliderDown(True)
        stage.scrubber.setSliderPosition(30000)
        self.engine.drift_to(30033)
        stage.transport_refresh()  # the 100 ms poll, mid-drag
        stage.scrubber.setSliderDown(False)
        self.assertEqual(
            [call for call in self.engine.calls if call[0] == "seek"],
            [("seek", 30000)],
            "the release sent a second seek to the place the drag had reached",
        )

    def test_the_clock_follows_the_drag_rather_than_the_player(self) -> None:
        stage = self.make()
        stage.scrubber.setSliderDown(True)
        stage.scrubber.setSliderPosition(30000)
        self.engine.drift_to(4200)
        stage.transport_refresh()
        self.assertIn("00:30", stage.clock.text(), "the clock argued with the handle")

    def test_clicking_a_pane_plays_and_pauses(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        self.tap(stage.main, QtCore.QPointF(40.0, 40.0))
        self.assertEqual(self.engine.names(), ["toggle"])


# ===================================================== §6: on a real screen


class PlacementTests(StageTestCase):
    """`window.transport_spot`'s lesson, on a window with a whole player in it."""

    #: Two monitors with a gap no screen covers - the desk that put the transport bar
    #: at 1620,2480 on no monitor at all.
    LEFT = QtCore.QRect(0, 0, 2560, 1400)
    BELOW = QtCore.QRect(-1400, 1400, 1200, 1900)

    def test_a_fresh_stage_is_inside_one_real_screen(self) -> None:
        stage = stage_module.PlayerStage(FakeEngine())
        self.made.append(stage)
        box = stage.place_on_screen([self.LEFT, self.BELOW])
        self.assertTrue(
            self.LEFT.contains(box) or self.BELOW.contains(box),
            f"{box} is on neither {self.LEFT} nor {self.BELOW}",
        )

    def test_it_is_never_placed_in_the_gap_between_two_screens(self) -> None:
        stage = stage_module.PlayerStage(FakeEngine())
        self.made.append(stage)
        box = stage.place_on_screen([self.LEFT, self.BELOW])
        union = self.LEFT.united(self.BELOW)
        self.assertTrue(union.contains(box))
        self.assertFalse(
            box.center().x() < 0 and box.center().y() < 1400,
            f"{box} is centred in the empty corner of {union}",
        )

    def test_a_remembered_position_on_a_lost_monitor_comes_back(self) -> None:
        stage = stage_module.PlayerStage(FakeEngine())
        self.made.append(stage)
        stage.setGeometry(9000, 9000, 900, 700)
        box = stage.place_on_screen([self.LEFT])
        self.assertTrue(self.LEFT.contains(box), f"{box} is off {self.LEFT}")

    def test_a_stage_bigger_than_the_screen_is_shrunk_rather_than_moved(self) -> None:
        stage = stage_module.PlayerStage(FakeEngine())
        self.made.append(stage)
        tiny = QtCore.QRect(0, 0, 800, 600)
        stage.setGeometry(0, 0, 4000, 3000)
        box = stage.place_on_screen([tiny])
        self.assertTrue(tiny.contains(box), f"{box} is off {tiny}")

    def test_no_screens_at_all_leaves_the_window_alone(self) -> None:
        stage = stage_module.PlayerStage(FakeEngine())
        self.made.append(stage)
        stage.setGeometry(10, 20, 640, 480)
        box = stage.place_on_screen([])
        self.assertEqual(box, QtCore.QRect(10, 20, 640, 480))

    def test_show_places_before_it_shows(self) -> None:
        stage = stage_module.PlayerStage(FakeEngine())
        self.made.append(stage)
        stage.show()
        self.app.processEvents()
        areas = [
            screen.availableGeometry()
            for screen in QtGui.QGuiApplication.screens()
        ]
        self.assertTrue(
            any(area.contains(stage.geometry()) for area in areas),
            f"{stage.geometry()} is on none of {areas}",
        )


# ============================================= §6: a monitor unplugged mid-play


class DisplayChangeTests(StageTestCase):
    """§6: "No window may ever end up where it cannot be reached."

    This machine has one monitor and nobody can unplug it, so the desks are handed
    in. What the fakes cannot answer is whether Windows relocates the window first;
    the contract asks the player to do it, and the player now does.
    """

    #: A desk of two, and the one that is about to be unplugged.
    LEFT = QtCore.QRect(0, 0, 2560, 1400)
    GONE = QtCore.QRect(2560, 0, 1440, 2560)

    def test_a_stage_on_the_lost_monitor_is_moved_and_says_so(self) -> None:
        stage = self.make()
        stage.setGeometry(2700, 400, 1200, 900)
        self.assertTrue(stage.rescue_window([self.LEFT]))
        self.assertTrue(
            self.LEFT.contains(stage.geometry()),
            f"{stage.geometry()} is off {self.LEFT}",
        )
        self.assertTrue(stage.notice.isVisible(), "the window moved without a word")

    def test_a_stage_still_reachable_is_left_exactly_where_it_was(self) -> None:
        stage = self.make()
        stage.setGeometry(100, 100, 900, 700)
        before = QtCore.QRect(stage.geometry())
        self.assertFalse(stage.rescue_window([self.LEFT, self.GONE]))
        self.assertEqual(stage.geometry(), before)
        self.assertFalse(stage.notice.isVisible(), "nothing moved; nothing to say")

    def test_no_screen_at_all_moves_nothing(self) -> None:
        stage = self.make()
        before = QtCore.QRect(stage.geometry())
        self.assertFalse(stage.rescue_window([]))
        self.assertEqual(stage.geometry(), before)

    def test_screen_removed_actually_reaches_the_rescue(self) -> None:
        """A rescue nothing calls is the defect; the arithmetic is tested above.

        Watched at the call rather than at the geometry, because the offscreen
        platform reports one screen smaller than a stage's own minimum size - so a
        rescue on this machine cannot land the window inside it however right it is.
        """
        stage = self.make()
        asked: list[Any] = []

        def spy(areas: Any = None) -> bool:
            asked.append(areas)
            return False

        stage.rescue_window = spy  # type: ignore[method-assign]
        QtGui.QGuiApplication.instance().screenRemoved.emit(None)
        self.app.processEvents()
        self.assertEqual(len(asked), 1, "nothing in the stage listens for it")

    def test_a_window_shown_without_show_is_clamped_too(self) -> None:
        """`QWidget::show` is not virtual; `setVisible` is, and hosts use both."""
        stage = stage_module.PlayerStage(FakeEngine())
        self.made.append(stage)
        stage.setGeometry(9000, 9000, 900, 700)
        stage.setVisible(True)
        self.app.processEvents()
        self.assertTrue(
            self._on_a_real_screen(stage), f"{stage.geometry()} is on no screen"
        )

    def _on_a_real_screen(self, widget: QtWidgets.QWidget) -> bool:
        areas = [
            screen.availableGeometry() for screen in QtGui.QGuiApplication.screens()
        ]
        return any(area.contains(widget.geometry()) for area in areas)


# ============================================================== the streams


class StreamTests(StageTestCase):
    def test_the_names_come_from_the_recording_when_none_are_given(self) -> None:
        stage = self.make()
        self.assertEqual(
            [stream.title for stream in stage.streams()],
            ["Screen 1 - primary", "Screen 2"],
        )

    def test_the_native_sizes_come_from_the_sidecar(self) -> None:
        stage = self.make()
        self.assertEqual(stage.main.native_size(), (2560, 1440))
        self.assertEqual(stage.side.native_size(), (1440, 2560))

    def test_a_recording_with_three_screens_shows_two_and_says_so(self) -> None:
        stage = self.make(FakeEngine(count=3))
        self.app.processEvents()
        self.assertEqual(len(stage.streams()), 2)
        self.assertTrue(
            stage.notice.isVisible(), "the third screen vanished without a word"
        )
        self.assertIn("Screen 3", stage.notice.text())

    def test_a_recording_with_one_screen_says_the_pane_will_be_black(self) -> None:
        stage = self.make(FakeEngine(count=1))
        self.app.processEvents()
        self.assertTrue(stage.notice.isVisible())
        self.assertIn("second pane", stage.notice.text())

    def test_a_host_may_name_the_streams_itself(self) -> None:
        given = [
            layouts.Stream(0, "Левый", (1920, 1080)),
            layouts.Stream(1, "Правый", (1920, 1080)),
        ]
        stage = self.make(streams=given)
        self.assertEqual(stage.main.label.text(), "Левый")
        self.assertEqual(stage.side.label.text(), "Правый")


# ============================================================== two languages


class LanguageTests(StageTestCase):
    """The keys, not the wording: a reworded Russian sentence must not fail a test."""

    def test_every_chrome_string_turns_russian_with_the_switch(self) -> None:
        stage = self.make()
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertEqual(
                stage.swap_button.text(), i18n.CATALOGUE["Swap the screens"]["ru"]
            )
            self.assertEqual(
                stage.even_button.text(), i18n.CATALOGUE["Even them up"]["ru"]
            )
            self.assertEqual(
                stage.layout_chip.setToolTip
                and stage.layout_chip.toolTip(),
                i18n.CATALOGUE["Arrange (L)"]["ru"],
            )
            self.assertEqual(
                stage.back_button.toolTip(),
                i18n.CATALOGUE["One frame back (,)"]["ru"],
            )
            self.assertEqual(
                stage.full_button.toolTip(), i18n.CATALOGUE["Full screen"]["ru"]
            )
            self.assertIn(
                i18n.CATALOGUE["Playing in step"]["ru"], stage.footer.text()
            )

    def test_the_layout_menu_and_the_chip_follow_the_switch(self) -> None:
        stage = self.make(layout=layouts.LAYOUT_STACKED)
        english = stage.layout_chip.label()
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertNotEqual(stage.layout_chip.label(), english)
            self.assertEqual(
                stage.layout_chip.label(), layouts.title(layouts.LAYOUT_STACKED)
            )
            self.assertEqual(
                [action.text() for action in stage.layout_menu.actions()],
                [layouts.title(name) for name in stage_module.STAGE_ORDER],
            )

    def test_the_generated_screen_names_are_said_again_in_russian(self) -> None:
        stage = self.make()
        english = stage.main.label.text()
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertNotEqual(
                stage.main.label.text(), english, "the pane kept its English name"
            )

    def test_a_hosts_own_names_survive_a_language_switch(self) -> None:
        """§3's rule in `PlayerWindows.retranslate`: a caller's word wins over ours."""
        given = [
            layouts.Stream(0, "Ноутбук", (1920, 1080)),
            layouts.Stream(1, "Монитор", (1920, 1080)),
        ]
        stage = self.make(streams=given)
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertEqual(stage.main.label.text(), "Ноутбук")
        self.assertEqual(stage.main.label.text(), "Ноутбук")

    def test_the_footer_counts_the_streams_in_the_form_the_number_takes(self) -> None:
        """«1 видеопотока» was the *few* form applied to one of anything."""
        one = self.make(FakeEngine(count=1))
        two = self.make(FakeEngine())
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertIn(
                i18n.CATALOGUE["stage.streams.one"]["ru"].format(count=1),
                one.footer.text(),
            )
            self.assertIn(
                i18n.CATALOGUE["{count} video streams"]["ru"].format(count=2),
                two.footer.text(),
            )

    def test_the_notice_about_a_third_screen_is_russian_throughout(self) -> None:
        """It reaches the strip the viewer reads, so it is not a log line."""
        key = (
            "This recording has {screens} and this window shows two. "
            "Not on it: {names}. Separate windows show them all."
        )
        with _in(i18n.LANG_RU):
            stage = self.make(FakeEngine(count=3))
            self.app.processEvents()
            said = stage.notice.text()
        self.assertIn(i18n.CATALOGUE[key]["ru"].split("{")[0], said)
        self.assertNotIn("This recording has", said, "the sentence stayed English")

    def test_the_notice_about_a_black_pane_is_russian_throughout(self) -> None:
        key = (
            "This window shows two screens and this recording has {screens}; "
            "the second pane will stay black."
        )
        with _in(i18n.LANG_RU):
            stage = self.make(FakeEngine(count=1))
            self.app.processEvents()
            said = stage.notice.text()
        self.assertIn(i18n.CATALOGUE[key]["ru"].split("{")[0], said)
        self.assertIn(
            i18n.CATALOGUE["stage.screens.one"]["ru"].format(count=1),
            said,
            "«1 экрана» is the few form applied to one screen",
        )

    def test_the_volume_sentence_carries_the_level_in_both_languages(self) -> None:
        stage = self.make()
        self.assertIn("100", stage.volume_button.toolTip())
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertIn("100", stage.volume_button.toolTip())
            self.assertEqual(
                stage.volume_button.toolTip(),
                i18n.CATALOGUE[
                    "Mute - volume {percent}%, scroll here to change it"
                ]["ru"].format(percent=100),
            )

    # -- `docs/PLAYER_SOURCES.md`'s controls, in both languages ------------
    #
    # Read against the catalogue by key, never against a Russian sentence written
    # out here: a reworded translation must not fail a test. Most of the keys are
    # `windows.*` on purpose - these are the floating transport's own controls,
    # borrowed rather than respelled - and that is itself worth pinning, because
    # a copy made here later would quietly stop being covered by this.

    def test_the_open_menu_speaks_russian(self) -> None:
        stage = self.make()
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            stage.open_menu.aboutToShow.emit()
            self.assertEqual(
                stage.open_action.text(),
                i18n.CATALOGUE["windows.open.dialog"]["ru"],
            )
            self.assertEqual(
                stage.save_action.text(),
                i18n.CATALOGUE["windows.open.save"]["ru"],
            )
            self.assertEqual(
                stage.library_menu.title(), i18n.CATALOGUE["stage.library"]["ru"]
            )

    def test_the_second_video_item_speaks_russian(self) -> None:
        stage = self.make(FakeEngine(1))
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertEqual(
                stage.second_action.text(),
                i18n.CATALOGUE["windows.open.second"]["ru"],
            )

    def test_the_sound_chip_names_the_screen_in_russian(self) -> None:
        stage = self.make()
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertEqual(stage.sound_chip.label(), stage.streams()[0].title)
            self.assertIn("Экран", stage.sound_chip.label())
            stage.set_audio_source(-1)
            self.assertEqual(
                stage.sound_chip.label(), i18n.CATALOGUE["windows.audio.off"]["ru"]
            )

    def test_the_sync_chip_speaks_russian_with_and_without_an_offset(self) -> None:
        stage = self.make()
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertEqual(
                stage.sync_chip.label(), i18n.CATALOGUE["windows.sync.in.step"]["ru"]
            )
            stage.set_offset(1, -1480)
            self.app.processEvents()
            self.assertIn("1480", stage.sync_chip.label())
            self.assertEqual(
                stage.sync_chip.toolTip(),
                i18n.CATALOGUE["windows.sync.button.moved"]["ru"].format(
                    offset=stage_module.format_offset(-1480)
                ),
            )

    def test_the_waiting_badge_is_said_again_after_a_language_switch(self) -> None:
        """§2.1's badge carries a name, and a pane never re-words one a host wrote."""
        stage = self.make()
        self.engine.begin_waiting(1)
        self.app.processEvents()
        english = stage.side.badge.text()
        with _in(i18n.LANG_RU):
            self.app.processEvents()
            self.assertNotEqual(stage.side.badge.text(), english)
            self.assertEqual(
                stage.side.badge.text(),
                i18n.CATALOGUE["windows.waiting"]["ru"].format(
                    name=stage.streams()[1].title
                ),
            )
            self.assertFalse(stage.side.video.isVisible(), "the picture came back")


# ================================================ §1: opening your own files


class OpeningTests(StageTestCase):
    """§1 of `docs/PLAYER_SOURCES.md`: the dialog, the library and the drop.

    The file dialog is replaced throughout. A modal dialog cannot be opened under the
    offscreen platform plugin without stopping the test that opened it, and what is
    worth checking is not that Qt can show a dialog but what this window does with the
    answer - which is why `stage.ask_for_files` is a module-level function.
    """

    def setUp(self) -> None:
        super().setUp()
        self.asked: list[tuple[str, bool, bool]] = []
        self.answer: list[str] = []
        self.patch(stage_module, "ask_for_files", self._ask)

    def _ask(
        self,
        parent: Any,
        title: str,
        *,
        multiple: bool = False,
        folder: str = "",
        videos_only: bool = False,
    ) -> list[str]:
        self.asked.append((title, multiple, videos_only))
        return list(self.answer)

    def menu_texts(self, menu: QtWidgets.QMenu) -> list[str]:
        """What the menu offers, as a reader of it would see it."""
        return [action.text() for action in menu.actions()]

    def open_menu(self, stage: stage_module.PlayerStage) -> list[str]:
        """Fill the Open menu the way showing it would, and read it."""
        stage.open_menu.aboutToShow.emit()
        return self.menu_texts(stage.open_menu)

    def library(self, count: int) -> list[Any]:
        return [
            FakeEntry(
                path=Path(f"D:/takes/Take_{index}.screenrec.json"),
                name=f"Take_{index}",
                recorded=f"2026-11-0{index % 9 + 1}T18:20:00",
                size_bytes=(index + 1) * 1024 * 1024,
                screens=2,
            )
            for index in range(count)
        ]

    def rows(self, found: list[Any]) -> Any:
        """`library.find_entries`, with the keyword the menu passes it."""

        def find(folder: Any, *, durations: bool = True) -> list[Any]:
            self.durations = durations
            return found

        return find

    # -- the dialog --------------------------------------------------------

    def test_ctrl_o_is_on_the_window_and_not_only_in_the_menu(self) -> None:
        stage = self.make()
        self.assertIn(stage.open_action, stage.actions())
        self.assertEqual(
            stage.open_action.shortcut(), QtGui.QKeySequence("Ctrl+O")
        )

    def test_the_open_action_asks_for_a_file_and_opens_what_it_names(self) -> None:
        stage = self.make()
        opened: list[list[str]] = []
        stage.open_paths = lambda paths: opened.append([str(p) for p in paths])
        self.answer = ["D:/shoot/camera_a.mp4"]
        stage.open_action.trigger()
        self.assertEqual(len(self.asked), 1)
        self.assertTrue(self.asked[0][1], "Open… must allow a pair to be chosen")
        self.assertEqual(opened, [["D:/shoot/camera_a.mp4"]])

    def test_a_cancelled_dialog_opens_nothing(self) -> None:
        stage = self.make()
        opened: list[Any] = []
        stage.open_paths = lambda paths: opened.append(paths)
        self.answer = []
        stage.open_dialog()
        self.assertEqual(opened, [])
        self.assertEqual(self.engine.calls, [], "a cancel reached the clock")

    def test_adding_a_second_video_asks_for_a_video_and_not_for_an_index(self) -> None:
        stage = self.make(FakeEngine(1))
        self.answer = []
        stage.add_second_video()
        self.assertEqual(len(self.asked), 1)
        self.assertFalse(self.asked[0][1], "a second screen is one file, not two")
        self.assertTrue(self.asked[0][2], "a .screenrec.json cannot be added to a video")

    # -- the menu ----------------------------------------------------------

    def test_a_lone_video_is_offered_a_second_one(self) -> None:
        stage = self.make(FakeEngine(1))
        texts = self.open_menu(stage)
        self.assertIn(stage.second_action.text(), texts)
        self.assertNotIn(stage.save_action.text(), texts)

    def test_a_pair_is_offered_saving_rather_than_adding(self) -> None:
        stage = self.make()
        texts = self.open_menu(stage)
        self.assertIn(stage.save_action.text(), texts)
        self.assertNotIn(stage.second_action.text(), texts)

    def test_adding_a_second_video_to_a_pair_is_refused_out_loud(self) -> None:
        stage = self.make()
        stage.add_second_video()
        self.assertEqual(self.asked, [], "a pair was asked for a third file")
        self.assertFalse(stage.notice.isHidden())

    # -- the library -------------------------------------------------------

    def test_the_library_lists_the_folder_newest_first_with_its_numbers(self) -> None:
        stage = self.make()
        self.patch(stage_module.library, "find_entries", self.rows(self.library(3)))
        self.open_menu(stage)
        texts = self.menu_texts(stage.library_menu)
        self.assertEqual(len(texts), 3)
        self.assertTrue(texts[0].startswith("Take_0"))
        self.assertIn("2026-11-01", texts[0])
        self.assertIn("1", texts[0], "the size is not on the row")

    def test_the_library_is_listed_without_probing_every_file(self) -> None:
        """A row costs a `stat`, never an `ffprobe`: the menu has to open at once."""
        stage = self.make()
        self.durations = True
        self.patch(stage_module.library, "find_entries", self.rows(self.library(2)))
        self.open_menu(stage)
        self.assertFalse(self.durations, "the menu measured every file's length")

    def test_clicking_a_library_row_opens_that_recording(self) -> None:
        stage = self.make()
        self.patch(stage_module.library, "find_entries", self.rows(self.library(2)))
        opened: list[list[str]] = []
        stage.open_paths = lambda paths: opened.append([str(p) for p in paths])
        self.open_menu(stage)
        stage.library_menu.actions()[1].trigger()
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0][0].endswith("Take_1.screenrec.json"), opened[0][0])

    def test_a_long_library_is_capped_at_the_rows_a_menu_can_hold(self) -> None:
        stage = self.make()
        rows = self.library(stage_module.LIBRARY_ROWS + 4)
        self.patch(stage_module.library, "find_entries", self.rows(rows))
        self.open_menu(stage)
        self.assertEqual(
            len(stage.library_menu.actions()), stage_module.LIBRARY_ROWS
        )

    def test_an_empty_library_says_so_and_offers_nothing_to_click(self) -> None:
        stage = self.make()
        self.patch(stage_module.library, "find_entries", self.rows([]))
        self.open_menu(stage)
        actions = stage.library_menu.actions()
        self.assertEqual(len(actions), 1)
        self.assertFalse(actions[0].isEnabled())

    def test_a_library_that_cannot_be_read_leaves_the_menu_usable(self) -> None:
        stage = self.make()

        def explode(folder: Any, *, durations: bool = True) -> list[Any]:
            raise OSError("the drive has gone")

        self.patch(stage_module.library, "find_entries", explode)
        self.open_menu(stage)
        self.assertEqual(len(stage.library_menu.actions()), 1)
        self.assertIn(stage.open_action, stage.open_menu.actions())

    # -- what opening does, and does not, do -------------------------------

    def test_nothing_named_is_refused_without_touching_the_clock(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        self.assertFalse(stage.open_paths([]))
        self.assertEqual(self.engine.calls, [])
        self.assertFalse(stage.notice.isHidden())

    def test_a_file_that_is_not_there_leaves_the_recording_playing(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        was = stage.engine
        self.assertFalse(stage.open_paths([str(Path(self.id()) / "nope.mp4")]))
        self.assertIs(stage.engine, was, "a failed open replaced the clock")
        self.assertEqual(self.engine.calls, [])
        self.assertFalse(stage.notice.isHidden())

    # -- the drop ----------------------------------------------------------

    def drop(self, stage: stage_module.PlayerStage, paths: list[str]) -> None:
        """Deliver a drop to the stage's own handler.

        `QApplication.sendEvent` cannot be used, and that is Qt's doing rather than a
        shortcut: `QApplication::notify` routes drag and drop through the drag manager,
        which drops a synthesised event on the floor when no drag is in progress -
        measured here, the handler is never reached and the test passes for the wrong
        reason. The handler is what this module owns, so the handler is what is called,
        and that Qt will deliver a real drop at all is checked by `acceptDrops`.
        """
        mime = QtCore.QMimeData()
        mime.setUrls([QtCore.QUrl.fromLocalFile(name) for name in paths])
        # Kept alive by hand: a `QDropEvent` does not own its mime data, and a
        # collected QMimeData under a live event is a crash rather than a failure.
        self._mime = mime
        stage.dropEvent(
            QtGui.QDropEvent(
                QtCore.QPointF(10.0, 10.0),
                QtCore.Qt.DropAction.CopyAction,
                mime,
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
            )
        )

    def test_the_window_takes_drops_at_all(self) -> None:
        self.assertTrue(self.make().acceptDrops())

    def test_one_file_dropped_opens_it(self) -> None:
        stage = self.make()
        opened: list[list[str]] = []
        stage.open_paths = lambda paths: opened.append([str(p) for p in paths])
        self.drop(stage, ["D:/shoot/one.mp4"])
        self.assertEqual(len(opened), 1)
        self.assertEqual(len(opened[0]), 1)

    def test_two_files_dropped_open_as_a_pair(self) -> None:
        stage = self.make()
        opened: list[list[str]] = []
        stage.open_paths = lambda paths: opened.append([str(p) for p in paths])
        self.drop(stage, ["D:/shoot/a.mp4", "D:/shoot/b.mp4"])
        self.assertEqual(len(opened), 1)
        self.assertEqual(len(opened[0]), 2, "the two cameras must arrive together")

    def test_three_files_dropped_open_the_first_two_and_say_so(self) -> None:
        stage = self.make()
        opened: list[list[str]] = []
        stage.open_paths = lambda paths: opened.append([str(p) for p in paths])
        self.drop(stage, ["D:/a.mp4", "D:/b.mp4", "D:/c.mp4"])
        self.assertEqual(len(opened[0]), 2)
        self.assertFalse(stage.notice.isHidden(), "the third file went silently")

    def test_a_drag_with_no_files_in_it_is_refused(self) -> None:
        stage = self.make()
        mime = QtCore.QMimeData()
        mime.setText("https://example.invalid/clip.mp4")
        self._mime = mime
        event = QtGui.QDragEnterEvent(
            QtCore.QPoint(10, 10),
            QtCore.Qt.DropAction.CopyAction,
            mime,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        stage.dragEnterEvent(event)
        self.assertFalse(event.isAccepted())

    def test_a_drag_carrying_files_is_taken(self) -> None:
        stage = self.make()
        mime = QtCore.QMimeData()
        mime.setUrls([QtCore.QUrl.fromLocalFile("D:/shoot/a.mp4")])
        self._mime = mime
        event = QtGui.QDragEnterEvent(
            QtCore.QPoint(10, 10),
            QtCore.Qt.DropAction.CopyAction,
            mime,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        stage.dragEnterEvent(event)
        self.assertTrue(event.isAccepted())

    # -- §1.3: saving the pair ---------------------------------------------

    def test_saving_hands_open_the_files_the_sound_and_the_offsets(self) -> None:
        stage = self.make()
        stage.set_audio_source(1)
        stage.set_offset(1, -1480)
        seen: list[dict[str, Any]] = []

        def save(files: Any, *, audio_index: int, offsets: Any) -> tuple[Path, str]:
            seen.append(
                {
                    "files": [Path(f).name for f in files],
                    "audio_index": audio_index,
                    "offsets": list(offsets),
                }
            )
            return Path("D:/shoot/camera_0.screenrec.json"), ""

        self.patch(stage_module.sources, "save_set", save)
        self.assertTrue(stage.save_this_set())
        self.assertEqual(
            seen,
            [
                {
                    "files": ["camera_0.mp4", "camera_1.mp4"],
                    "audio_index": 1,
                    "offsets": [0, -1480],
                }
            ],
        )
        self.assertFalse(stage.notice.isHidden(), "nothing said where it was saved")

    def test_a_lone_video_is_not_a_set_to_save(self) -> None:
        stage = self.make(FakeEngine(1))
        saved: list[Any] = []
        self.patch(
            stage_module.sources,
            "save_set",
            lambda *a, **k: saved.append(a) or (None, ""),
        )
        self.assertFalse(stage.save_this_set())
        self.assertEqual(saved, [])

    def test_a_refused_save_says_why(self) -> None:
        stage = self.make()
        self.patch(
            stage_module.sources,
            "save_set",
            lambda *a, **k: (
                None, "There is nothing to save: none of those files are there."
            ),
        )
        self.assertFalse(stage.save_this_set())
        self.assertIn("nothing to save", stage.notice.text())


# ================================================ §1.2: where the sound comes from


class SoundTests(StageTestCase):
    """§1.2: one audio source, chosen by the viewer, moving nothing else.

    The two halves of that paragraph are checked against each other on purpose:
    changing the sound must not move the picture, and swapping the picture must not
    move the sound. Each is trivial on its own and the pair of them is the feature.

    **Muting is not choosing no sound.** The volume button silences what is playing;
    `-1` says no file is being played for its audio at all. The tests keep them apart
    because the controls do.
    """

    def menu_of(self, stage: stage_module.PlayerStage) -> QtWidgets.QMenu:
        """Open the sound menu and hand it back, closed again on the way out."""
        before = set(stage.findChildren(QtWidgets.QMenu))
        stage._open_sound_menu()
        made = [
            menu for menu in stage.findChildren(QtWidgets.QMenu)
            if menu not in before
        ]
        self.assertEqual(len(made), 1, "the sound menu was not built")
        self.addCleanup(made[0].close)
        return made[0]

    def test_the_sound_opens_where_the_recording_says_it_is(self) -> None:
        engine = FakeEngine()
        engine.audio_index = 1
        stage = self.make(engine)
        self.assertEqual(stage.audio_source(), 1)
        self.assertEqual(stage.sound_chip.label(), stage.streams()[1].title)

    def test_choosing_the_other_screen_moves_the_sound_and_nothing_else(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        before = (stage.main_stream(), stage.is_swapped(), self.engine.position())
        stage.set_audio_source(1)
        self.assertEqual(stage.audio_source(), 1)
        self.assertEqual(
            (stage.main_stream(), stage.is_swapped(), self.engine.position()), before,
            "the audio control moved the picture",
        )
        self.assertEqual(
            [call[0] for call in self.engine.calls], ["set_audio_source"]
        )

    def test_swapping_the_picture_leaves_the_sound_where_it_was(self) -> None:
        stage = self.make()
        stage.set_audio_source(1)
        self.engine.calls.clear()
        stage.swap()
        self.assertEqual(stage.audio_source(), 1)
        self.assertEqual(
            [call[0] for call in self.engine.calls], [], "a swap spoke to the clock"
        )

    def test_no_sound_at_all_is_not_the_same_as_muting(self) -> None:
        stage = self.make()
        stage.set_audio_source(-1)
        self.assertEqual(stage.audio_source(), -1)
        self.assertEqual(stage.sound_chip.kind, "speaker_off")
        self.assertNotIn(
            "set_muted", [call[0] for call in self.engine.calls],
            "choosing no source muted the player instead",
        )
        self.assertEqual(stage.volume_button.kind, "volume", "the volume moved")

    def test_the_menu_names_every_stream_and_offers_no_sound(self) -> None:
        stage = self.make()
        menu = self.menu_of(stage)
        actions = [action for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(
            [action.text() for action in actions][:2],
            [stream.title for stream in stage.streams()],
        )
        self.assertTrue(actions[0].isChecked())
        actions[-1].trigger()
        self.assertEqual(stage.audio_source(), -1)

    def test_a_menu_row_asks_for_its_own_stream(self) -> None:
        stage = self.make()
        menu = self.menu_of(stage)
        actions = [action for action in menu.actions() if not action.isSeparator()]
        actions[1].trigger()
        self.assertEqual(stage.audio_source(), 1)

    def test_a_clock_that_cannot_move_the_sound_has_no_chip_at_all(self) -> None:
        stage = self.make(DeafEngine())
        self.assertFalse(
            stage.sound_chip.isVisible(), "a control that cannot do what it says"
        )
        stage.set_audio_source(1)
        self.assertFalse(stage.notice.isHidden(), "the refusal was silent")

    def test_a_lone_video_has_no_sound_chip_either(self) -> None:
        stage = self.make(FakeEngine(1))
        self.assertFalse(stage.sound_chip.isVisible())


# ======================================================== §2.2: lining them up


class SyncTests(StageTestCase):
    """§2.2's panel and keyboard, and the one thing none of them may do.

    "The adjustment must not move the timeline position: the viewer is sliding one
    video against the other, not scrubbing. Read the position before and after and
    assert it." Both forms are checked here - the position is read before and after,
    **and** the clock is asked whether anybody said `seek` to it, which is the form a
    seek back to the same millisecond cannot pass.

    The panel itself is `window.SyncPanel` and has its own tests next door; what is
    checked here is that this transport is wired to it and to the clock.
    """

    def offsets_of(self, stage: stage_module.PlayerStage) -> list[int]:
        return [stage.offset_of(stream.index) for stream in stage.streams()]

    def test_the_panel_opens_folded_and_the_chip_opens_it(self) -> None:
        stage = self.make()
        self.assertFalse(stage.sync_panel.isVisible())
        self.assertTrue(stage.sync_chip.isVisible())
        stage.show_sync_panel(True)
        self.assertTrue(stage.sync_panel.isVisible())

    def test_a_lone_video_has_nothing_to_line_up(self) -> None:
        stage = self.make(FakeEngine(1))
        self.assertFalse(stage.sync_chip.isVisible())
        stage.show_sync_panel(True)
        self.assertFalse(stage.sync_panel.isVisible())

    def test_a_clock_with_no_offsets_offers_no_panel_and_says_so(self) -> None:
        stage = self.make(UnmovableEngine())
        self.assertFalse(stage.sync_chip.isVisible())
        stage.set_offset(1, 500)
        self.assertFalse(stage.notice.isHidden())
        self.assertEqual(self.offsets_of(stage), [0, 0])

    def test_a_frame_nudge_is_the_recordings_own_frame(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        stage.nudge_offset(frames=1)
        # 60 fps, so one frame is 17 ms - the engine's own arithmetic, rounded.
        self.assertEqual(self.engine.calls, [("set_offset", 0, 17)])

    def test_a_nudge_moves_the_offset_and_not_the_playhead(self) -> None:
        stage = self.make()
        before_position = self.engine.position()
        before_clock = stage.clock.text()
        self.engine.calls.clear()
        stage.nudge_offset(milliseconds=-100)
        stage.nudge_offset(milliseconds=-100)
        self.assertEqual(self.offsets_of(stage), [-200, 0])
        self.assertEqual(self.engine.position(), before_position)
        self.assertEqual(stage.clock.text(), before_clock)
        self.assertNotIn("seek", [call[0] for call in self.engine.calls])

    def test_an_adjustment_while_playing_does_not_stop_anything(self) -> None:
        stage = self.make()
        self.engine.play()
        self.engine.calls.clear()
        stage.nudge_offset(milliseconds=100)
        self.assertTrue(self.engine.playing())
        self.assertEqual(
            [call[0] for call in self.engine.calls], ["set_offset"],
            "lining up while playing stopped or sought something",
        )

    def test_a_typed_number_reaches_the_clock(self) -> None:
        stage = self.make()
        stage.sync_panel.field.setValue(-1480)
        self.assertEqual(self.offsets_of(stage), [-1480, 0])

    def test_the_panel_shows_what_the_clock_took_rather_than_what_was_asked(
        self,
    ) -> None:
        stage = self.make()
        stage.set_offset(0, 640)
        self.app.processEvents()
        self.assertEqual(stage.sync_panel.current(), 640)

    def test_a_number_past_the_limit_is_held(self) -> None:
        stage = self.make()
        stage.sync_panel.nudge(milliseconds=window_module.OFFSET_LIMIT_MS * 4)
        self.assertEqual(stage.offset_of(0), window_module.OFFSET_LIMIT_MS)

    def test_the_footer_stops_claiming_they_are_in_step(self) -> None:
        """The window may not contradict itself about the only thing §2 changes.

        The footer used to be written once and say «Синхронное воспроизведение» for
        ever - beside a chip reading «+1200 ms», on a stage the viewer had just slid
        apart by hand.
        """
        stage = self.make()
        together = stage.footer.text()
        stage.set_offset(0, 1200)
        self.app.processEvents()
        apart = stage.footer.text()
        self.assertNotEqual(apart, together)
        self.assertIn("Lined up by hand", apart)
        stage.reset_offset()
        self.app.processEvents()
        self.assertEqual(stage.footer.text(), together)

    def test_reset_puts_the_picked_stream_back_to_zero(self) -> None:
        stage = self.make()
        stage.set_offset(0, 900)
        self.app.processEvents()
        stage.reset_offset()
        self.assertEqual(self.offsets_of(stage), [0, 0])

    def test_the_picker_chooses_which_stream_the_nudges_move(self) -> None:
        stage = self.make()
        self.assertEqual(stage.sync_index(), 0)
        stage.sync_panel.picker.changed.emit("1")
        self.assertEqual(stage.sync_index(), 1)
        stage.nudge_offset(milliseconds=100)
        self.assertEqual(self.offsets_of(stage), [0, 100])

    def test_a_stream_this_stage_does_not_show_is_not_moved(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        stage.set_offset(7, 100)
        self.assertEqual(self.engine.calls, [])

    def test_the_value_is_visible_with_the_panel_folded_away(self) -> None:
        stage = self.make()
        stage.set_offset(1, -1480)
        self.app.processEvents()
        self.assertFalse(stage.sync_panel.isVisible())
        self.assertIn("1480", stage.sync_chip.label())

    def test_the_keyboard_nudges_by_a_frame_and_by_100_ms(self) -> None:
        stage = self.make()
        self.modified_key(
            stage, QtCore.Qt.Key.Key_Right, QtCore.Qt.KeyboardModifier.ShiftModifier
        )
        self.app.processEvents()
        self.assertEqual(stage.offset_of(0), 17)
        self.modified_key(
            stage, QtCore.Qt.Key.Key_Left, QtCore.Qt.KeyboardModifier.AltModifier
        )
        self.app.processEvents()
        self.assertEqual(stage.offset_of(0), 17 - 100)

    def test_the_plain_arrows_still_skip(self) -> None:
        stage = self.make()
        self.engine.calls.clear()
        self.key(stage, QtCore.Qt.Key.Key_Right)
        # The fake has no `skip`, so `_Engine` falls back to an absolute seek - which
        # is the point: the arrows reach the *transport*, not the sync panel.
        self.assertEqual([call[0] for call in self.engine.calls], ["seek"])
        self.assertEqual(self.offsets_of(stage), [0, 0])

    def test_a_modified_arrow_on_a_lone_video_skips_as_it_always_did(self) -> None:
        """One stream has nothing to slide, so the modifier means nothing (§2.2).

        The key is left to the plain arrow below it, which is what Shift+Right did
        here before §2.2 existed and what `PlayerWindows._sync_key` does with the
        same key on the same recording - `test_shift_arrow_still_skips` next door is
        the other half of this pair. Swallowing it on the stage instead made one key
        mean two things, chosen by which half of the player had focus, which is the
        disagreement neither docstring wanted.
        """
        stage = self.make(FakeEngine(1))
        before = self.engine.position()
        self.engine.calls.clear()
        self.modified_key(
            stage, QtCore.Qt.Key.Key_Right, QtCore.Qt.KeyboardModifier.ShiftModifier
        )
        self.modified_key(
            stage, QtCore.Qt.Key.Key_Left, QtCore.Qt.KeyboardModifier.AltModifier
        )
        # The fake has no `skip`, so `_Engine` falls back to an absolute seek - the
        # same fallback `test_the_plain_arrows_still_skip` reads the arrows through.
        self.assertEqual(
            self.engine.calls,
            [
                ("seek", before + window_module.JUMP_MS),
                ("seek", before),
            ],
            "a modified arrow on a lone video stopped skipping",
        )

    def test_an_offset_moved_elsewhere_reaches_the_panel_and_the_chip(self) -> None:
        stage = self.make()
        self.engine.set_offset(1, 640)
        self.app.processEvents()
        stage.sync_panel.picker.changed.emit("1")
        self.assertEqual(stage.sync_panel.current(), 640)
        self.assertIn("640", stage.sync_chip.label())

    def test_a_set_that_opens_out_of_step_shows_the_panel_at_once(self) -> None:
        engine = FakeEngine()
        engine.set_offset(1, -1480)
        stage = self.make(engine)
        self.assertTrue(
            stage.sync_panel.isVisible(),
            "a non-zero offset must explain itself without being hunted for",
        )


# ================================ §2.1: a stream the timeline has not reached


class WaitingTests(StageTestCase):
    """§2.1: black with a badge, never a frozen first frame.

    The badge is §6's with other words, and the *clearing* of it is where the two
    differ: a seek clears every ended badge, and a stream whose content has not
    started is still waiting on the other side of that seek.
    """

    def test_a_waiting_stream_holds_black_with_a_badge(self) -> None:
        stage = self.make()
        self.engine.begin_waiting(1)
        self.app.processEvents()
        self.assertTrue(stage.side.has_ended(), "no badge over the waiting stream")
        self.assertFalse(stage.side.video.isVisible(), "a frozen first frame is shown")
        self.assertTrue(stage.main.video.isVisible(), "the other stream went dark too")

    def test_the_picture_comes_back_when_the_stream_starts(self) -> None:
        stage = self.make()
        self.engine.begin_waiting(1)
        self.app.processEvents()
        self.engine.end_waiting(1)
        self.app.processEvents()
        self.assertTrue(stage.side.video.isVisible())
        self.assertFalse(stage.side.has_ended())

    def test_the_badge_survives_a_seek(self) -> None:
        stage = self.make()
        self.engine.begin_waiting(1)
        self.app.processEvents()
        self.engine.scrub_to(1200)
        self.app.processEvents()
        self.assertTrue(stage.side.has_ended(), "the waiting badge died in a seek")
        self.assertFalse(stage.side.video.isVisible())

    def test_the_badge_follows_the_stream_across_a_swap(self) -> None:
        stage = self.make()
        self.engine.begin_waiting(1)
        self.app.processEvents()
        stage.swap()
        self.assertFalse(stage.main.video.isVisible(), "the badge stayed on the pane")
        self.assertTrue(stage.side.video.isVisible())

    def test_a_stage_built_over_a_waiting_stream_is_told_at_once(self) -> None:
        engine = FakeEngine()
        engine.begin_waiting(1)
        stage = self.make(engine)
        self.assertFalse(stage.side.video.isVisible())

    def test_an_engine_with_no_waiting_signal_badges_nothing(self) -> None:
        stage = self.make(QuietEngine())
        self.assertTrue(stage.side.video.isVisible())
        self.assertFalse(stage.side.has_ended())


# ======================================== §1.1: the one place a clock is rebuilt


class StubPlayer(QtCore.QObject):
    """A `QMediaPlayer`-shaped object that decodes nothing and remembers everything.

    The rebuild is checked against a **real** `SyncEngine` - §1.1 is about what
    happens to the position and the paused state when the clock is thrown away, and a
    fake engine would be checking this test's own arithmetic. What is stubbed is the
    layer below it, so that no codec, no audio device and no real file are needed.
    """

    errorOccurred = QtCore.Signal(int, str)
    mediaStatusChanged = QtCore.Signal(object)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.source: Any = None
        self.audio_output: Any = "unset"
        self.state = "stopped"
        self._position = 0

    def setSource(self, url: Any) -> None:  # noqa: N802 - Qt's spelling
        self.source = url

    def setVideoSink(self, sink: Any) -> None:  # noqa: N802
        self.sink = sink

    def setAudioOutput(self, output: Any) -> None:  # noqa: N802
        self.audio_output = output

    def setPlaybackRate(self, rate: float) -> None:  # noqa: N802
        self.rate = float(rate)

    def setPosition(self, milliseconds: int) -> None:  # noqa: N802
        self._position = min(int(milliseconds), 45_000)

    def position(self) -> int:
        return self._position

    def duration(self) -> int:
        return 45_000

    def play(self) -> None:
        self.state = "playing"

    def pause(self) -> None:
        self.state = "paused"

    def stop(self) -> None:
        self.state = "stopped"
        self._position = 0


class SlowStubPlayer(StubPlayer):
    """`StubPlayer` with `QMediaPlayer`'s one piece of asynchrony put back in.

    A real `setSource` returns before the file has been read: a `setPosition` issued
    in that window is dropped on the floor, and `duration()` answers 0 until the
    decoder has been through the header. `StubPlayer` is synchronous, which is right
    for every other test in this class and is exactly why none of them could catch
    §1.1's carried position being lost - the carry's *first* attempt always stuck
    there, so what the second attempt was wired to never mattered.

    `land` is the decoder finishing its read. Nothing here is timed: a test says when
    the file lands, so the check is about the order of events and not about a machine
    being fast enough on the day.
    """

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._ready = False

    def setSource(self, url: Any) -> None:  # noqa: N802 - Qt's spelling
        super().setSource(url)
        self._ready = False
        self._position = 0

    def land(self) -> None:
        """The file has been read: positions stick and the length is known."""
        self._ready = True

    def setPosition(self, milliseconds: int) -> None:  # noqa: N802
        if not self._ready:
            return
        super().setPosition(milliseconds)

    def duration(self) -> int:
        return super().duration() if self._ready else 0


class StubAudio(QtCore.QObject):
    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.muted = False
        self.volume = 1.0

    def setMuted(self, muted: bool) -> None:  # noqa: N802
        self.muted = bool(muted)

    def setVolume(self, value: float) -> None:  # noqa: N802
        self.volume = float(value)


class RebuildTests(StageTestCase):
    """Adding a second video: the clock is rebuilt and the viewer's place is not lost.

    Written first of everything in this round, because §1.1 says so and because it is
    the thing a viewer notices immediately if it is wrong: you add the second camera
    four minutes into a take and the player starts again from the beginning.
    """

    def setUp(self) -> None:
        super().setUp()
        self.folder = Path(tempfile.mkdtemp(prefix="screenrec-stage-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.first = self.video("camera_a.mp4")
        self.second = self.video("camera_b.mp4")
        self.players: list[StubPlayer] = []
        self.audios: list[StubAudio] = []

    def video(self, name: str) -> Path:
        """A file that exists and is not a video. ffprobe says so and we carry on.

        `open.recording_from_files` probes every file it is given; a file it cannot
        measure still opens, at the fallback size, with a sentence about it. That is
        the path this test takes, and it costs one `ffprobe` per file rather than a
        sample recording in the repository.
        """
        path = self.folder / name
        path.write_bytes(b"\x00" * 64)
        return path

    def clock_over(self, files: list[Path], offsets: list[int] | None = None) -> Any:
        shifts = offsets or [0] * len(files)
        recording = engine_module.Recording(
            screens=[
                engine_module.Screen(
                    file=path,
                    monitor=index,
                    desktop=(index * 1920, 0, 1920, 1080),
                    size=(1920, 1080),
                    rotation=0,
                    primary=index == 0,
                    offset_ms=shifts[index],
                )
                for index, path in enumerate(files)
            ],
            audio_file=files[0],
            recorded="2026-11-04T18:20:00",
            folder=self.folder,
        )
        return self.build(recording)

    def build(self, recording: Any, kind: type = StubPlayer) -> Any:
        def player(parent: Any) -> StubPlayer:
            made = kind(parent)
            self.players.append(made)
            return made

        def audio(parent: Any) -> StubAudio:
            made = StubAudio(parent)
            self.audios.append(made)
            return made

        return engine_module.SyncEngine(
            recording, None, player_factory=player, audio_factory=audio
        )

    def rebuilding(self, kind: type = StubPlayer) -> None:
        """Make the stage build its next clock over stubs rather than over codecs."""
        self.patch(
            stage_module,
            "build_clock",
            lambda recording, parent: self.build(recording, kind),
        )

    def settle(self, done: Callable[[], bool], limit_ms: int = 3000) -> None:
        """Let the stage's own 100 ms poll run until `done`, or give up and say so.

        The poll is a real `QTimer` on a real event loop, so the thing under test
        here is the wiring and not a hand-called slot: a retry connected to nothing
        would leave this spinning until the limit. The limit is generous and the loop
        exits on the first true, so a passing run costs one or two ticks.
        """
        deadline = monotonic() + limit_ms / 1000.0
        while monotonic() < deadline:
            if done():
                return
            self.app.processEvents()
            QTest.qWait(10)
        self.fail(f"the stage never settled within {limit_ms} ms")

    # -- the headline ------------------------------------------------------

    def test_adding_a_second_video_keeps_the_position(self) -> None:
        clock = self.clock_over([self.first])
        stage = self.make(clock)
        clock.seek(4200)
        self.assertEqual(clock.position(), 4200)

        self.rebuilding()
        self.assertTrue(stage.add_video(self.second))

        self.assertIsNot(stage.engine, clock, "the clock was not rebuilt")
        self.assertEqual(len(stage.streams()), 2, "the second video is not on the stage")
        self.assertEqual(stage.engine.position(), 4200, "the viewer lost their place")
        self.assertFalse(stage.engine.playing(), "a paused player started itself")

    def test_adding_a_second_video_while_playing_keeps_it_playing(self) -> None:
        clock = self.clock_over([self.first])
        stage = self.make(clock)
        clock.seek(4200)
        clock.play()

        self.rebuilding()
        self.assertTrue(stage.add_video(self.second))

        self.assertTrue(stage.engine.playing(), "playback stopped on the rebuild")
        self.assertEqual(stage.engine.position(), 4200)

    def test_adding_a_second_video_while_paused_keeps_the_position(self) -> None:
        """§1.1's headline, over a clock that is asynchronous like the real one.

        `test_adding_a_second_video_keeps_the_position` cannot fail: `StubPlayer`
        takes a position the instant it is given one, so the carry's first attempt
        always sticks and the retry is never reached. This is the case a viewer
        actually meets - pause on the clapperboard, press «Добавить второй экран» -
        and it is the one state the retry could not be served in, because it was
        hung on `SyncEngine.duration_changed`, which is emitted from a tick that
        does not run while paused. The place came back only when play was pressed,
        as a jump a second later.
        """
        clock = self.clock_over([self.first])
        stage = self.make(clock)
        clock.seek(5000)
        self.assertFalse(clock.playing(), "this test is about the paused case")

        self.rebuilding(SlowStubPlayer)
        self.assertTrue(stage.add_video(self.second))
        self.assertEqual(
            stage.engine.position(), 0, "the stub took a position before its read"
        )

        for player in self.players[-2:]:
            player.land()
        self.settle(lambda: stage._carry is None)

        self.assertEqual(stage.engine.position(), 5000, "the viewer lost their place")
        self.assertFalse(stage.engine.playing(), "a paused player started itself")
        self.assertEqual(len(stage.streams()), 2, "the second video is not on the stage")

    def test_a_paused_rebuild_needs_no_duration_signal_to_land(self) -> None:
        """The retry is this window's poll, not the clock's `duration_changed`.

        Said as its own check because the two routes are indistinguishable from the
        outside once both work: with the signal silenced, a carry that still lands
        can only have landed on the timer.
        """
        clock = self.clock_over([self.first])
        stage = self.make(clock)
        clock.seek(3300)

        self.rebuilding(SlowStubPlayer)
        self.assertTrue(stage.add_video(self.second))
        heard: list[int] = []
        stage.engine.duration_changed.connect(heard.append)

        for player in self.players[-2:]:
            player.land()
        self.settle(lambda: stage._carry is None)

        self.assertEqual(heard, [], "the clock announced a duration while paused")
        self.assertEqual(stage.engine.position(), 3300)

    def test_a_clock_that_never_reads_its_files_gives_the_place_up(self) -> None:
        """A carry held for ever is a seek that can still land minutes later.

        A file that never opens never reports a length, so the retry's own condition
        never comes true. `CARRY_PATIENCE_MS` bounds the wait and the give-up is said
        on stderr rather than being silent, which is the only way the lost position
        is ever explained.
        """
        said: list[str] = []
        self.patch(stage_module, "_window_report", said.append)
        clock = self.clock_over([self.first])
        stage = self.make(clock)
        clock.seek(5000)

        self.rebuilding(SlowStubPlayer)
        self.assertTrue(stage.add_video(self.second))
        self.assertIsNotNone(stage._carry, "nothing was waiting to be restored")
        # The patience is spent; no file has landed. Set rather than waited out, so
        # the check costs a tick instead of `CARRY_PATIENCE_MS`.
        stage._carry_deadline = 0.0
        self.settle(lambda: stage._carry is None)

        self.assertTrue(
            any("5000" in line for line in said),
            f"the given-up position was never explained: {said!r}",
        )

    def test_the_offsets_are_carried_over_the_rebuild(self) -> None:
        clock = self.clock_over([self.first], offsets=[-700])
        stage = self.make(clock)
        self.rebuilding()
        self.assertTrue(stage.add_video(self.second))
        self.assertEqual(stage.engine.offset(0), -700)

    def test_the_rebuilt_clock_has_exactly_one_audio_output(self) -> None:
        clock = self.clock_over([self.first])
        stage = self.make(clock)
        self.rebuilding()
        stage.add_video(self.second)
        fresh = self.players[-2:]
        with_sound = [
            player for player in fresh
            if player.audio_output not in (None, "unset")
        ]
        self.assertEqual(len(with_sound), 1, "two voices, or none")
        self.assertEqual(len(fresh), 2)

    def test_the_mute_and_the_speed_survive_the_rebuild(self) -> None:
        clock = self.clock_over([self.first])
        stage = self.make(clock)
        stage.toggle_mute()
        stage.speed_chip.changed.emit(1.5)

        self.rebuilding()
        stage.add_video(self.second)

        self.assertEqual(stage.engine.speed(), 1.5)
        self.assertTrue(self.audios[-1].muted, "the sound came back on by itself")

    def test_the_panel_and_the_chips_follow_the_new_set(self) -> None:
        clock = self.clock_over([self.first])
        stage = self.make(clock)
        self.assertFalse(stage.sync_chip.isVisible())

        self.rebuilding()
        stage.add_video(self.second)

        self.assertTrue(stage.sync_chip.isVisible(), "a pair with nothing to line up")
        self.assertEqual(stage.sync_index(), 0, "the picker addresses a stream")
        self.assertEqual(stage.audio_source(), 0)
        stage.open_menu.aboutToShow.emit()
        texts = [action.text() for action in stage.open_menu.actions()]
        self.assertIn(stage.save_action.text(), texts, "a pair cannot be saved")

    def test_the_old_clock_is_stopped_and_let_go_of_safely(self) -> None:
        """What `open._build_windows` does with its clock must still work afterwards.

        The host ties `clock.deleteLater` to this window's `closed` signal. The stage
        replaces that clock, so the rule it follows is: delete only the engines it
        built itself, and leave the host's one alive to be deleted by the host. If
        that rule is ever dropped, this test is where the crash appears - in
        somebody else's teardown, which is the worst place to find one.
        """
        clock = self.clock_over([self.first])
        stage = self.make(clock)
        stage.closed.connect(clock.deleteLater)

        self.rebuilding()
        stage.add_video(self.second)
        built = stage.engine
        self.assertIsNot(built, clock)
        self.assertEqual(self.players[0].state, "stopped", "the old clock plays on")

        gone: list[str] = []
        clock.destroyed.connect(lambda *_: gone.append("host"))
        built.destroyed.connect(lambda *_: gone.append("built"))
        stage.close()
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(sorted(gone), ["built", "host"])

    def test_a_recording_that_will_not_build_leaves_the_window_playing(self) -> None:
        clock = self.clock_over([self.first])
        stage = self.make(clock)

        def explode(recording: Any, parent: Any) -> Any:
            raise RuntimeError("no decoder on this machine")

        self.patch(stage_module, "build_clock", explode)
        self.assertFalse(stage.add_video(self.second))
        self.assertIs(stage.engine, clock, "the window lost its clock to a failure")
        self.assertFalse(stage.notice.isHidden())


# ============================================================ the glyphs


class GlyphTests(unittest.TestCase):
    """`draw_stage_glyph` must never raise in a live painter, whatever it is given."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def _paint(self, kind: str) -> bool:
        image = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QtGui.QPainter(image)
        try:
            return stage_module.draw_stage_glyph(
                painter, kind, QtCore.QRectF(0, 0, 32, 32), "#FFFFFF"
            )
        finally:
            painter.end()

    def test_every_named_glyph_draws(self) -> None:
        for kind in stage_module.STAGE_GLYPHS:
            with self.subTest(kind=kind):
                self.assertTrue(self._paint(kind))

    def test_an_unknown_name_draws_nothing_and_does_not_raise(self) -> None:
        self.assertFalse(self._paint("rewind"))

    def test_an_empty_rectangle_draws_nothing(self) -> None:
        image = QtGui.QImage(8, 8, QtGui.QImage.Format.Format_ARGB32)
        painter = QtGui.QPainter(image)
        try:
            self.assertFalse(
                stage_module.draw_stage_glyph(
                    painter, "fullscreen", QtCore.QRectF(0, 0, 0, 0), "#FFFFFF"
                )
            )
        finally:
            painter.end()


# ============================================================ the house rules


class ModuleShapeTests(unittest.TestCase):
    """The two rules of this feature that a reader has to be able to check by hand."""

    SOURCE = (
        Path(__file__).resolve().parents[1]
        / "app" / "screenrec" / "player" / "stage.py"
    )

    def test_no_line_is_longer_than_the_house_limit(self) -> None:
        long = [
            (number, line)
            for number, line in enumerate(
                self.SOURCE.read_text(encoding="utf-8").splitlines(), start=1
            )
            if len(line) > 89
        ]
        self.assertEqual(long, [], "lines over 89 characters")

    def test_the_stage_builds_no_player_of_its_own(self) -> None:
        """§6: "none of this may create a second one".

        Read off the parse tree rather than out of the text, so the module may go on
        explaining in its docstring which `QMediaPlayer` it is *not* building. A grep
        over the source would make the honest comment the thing that fails the test,
        and the first fix anyone reaches for then is to delete the comment.
        """
        tree = ast.parse(self.SOURCE.read_text(encoding="utf-8"))
        used = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in ("QMediaPlayer", "QAudioOutput", "setSource", "setVideoSink"):
            self.assertNotIn(forbidden, used, f"the stage calls {forbidden}")


if __name__ == "__main__":
    unittest.main()
