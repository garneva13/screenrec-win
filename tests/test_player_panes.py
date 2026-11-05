"""Checks for `screenrec.player.panes`.

Two things in this module can be wrong in ways nobody notices until a viewer is using
it, so they are checked first and hardest.

* **The swap must leave exactly one connection.** A pane still listening to the stream
  it used to show, as well as to the one it shows now, paints both desktops interleaved
  at the sum of their frame rates. That reads as a broken decoder rather than as a
  signal connected twice, so it is checked by counting frames out of the pane's own
  widget sink and not by looking at the pane's bookkeeping.
* **A drag is not a click.** §4 of `docs/PLAYER_LAYOUTS.md` calls this the single most
  annoying bug the feature can have: a press that slid across a pane must not also be
  read as a press on it. The gesture is played out through real mouse events, through
  the real handlers.

After those: a stream's size is learnt, forgotten and re-learnt correctly across a
swap, and both states of §6 paint.

Two of those deserve naming, because each was shipped wrong once and neither shows up
as an exception anywhere.

* **A swap takes the stream's state with it.** The ended badge of §6 belongs to one
  recording that ran out; the busy veil belongs to a seek and applies to every pane at
  once. A badge carried across `set_stream` is not merely stale - `set_label` rewords
  it, so the pane announces that the stream which has just arrived has finished.
* **A swap moves the pictures, not only the labels.** Reconnecting the sinks is right
  and is all §6 allows, but a signal carries the *next* frame and a paused stream has
  no next frame - so the reconnection alone left the main pane showing screen 1 under a
  label reading «Экран 2» until playback resumed. The picture is therefore read back
  out of each pane's own widget sink after a swap with no frame following it.

The sink is a fake with one signal, because that is all a pane ever asks of one - no
file is opened and no codec is touched anywhere in this file. No GPU and no visible
window either: the offscreen platform plugin is set before PySide6 is imported.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtMultimedia, QtWidgets  # noqa: E402

from screenrec import i18n  # noqa: E402
from screenrec.player import layouts, panes  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


@contextmanager
def _in(code: str) -> Iterator[None]:
    """Run the block with `code` in force, and put the language back afterwards.

    The language is process-wide, so a test that left it in Russian would fail the
    next file's English assertions in a module that never touched it.
    """
    before = i18n.language()
    i18n.set_language(code)
    try:
        yield
    finally:
        i18n.set_language(before)


class FakeSink(QtCore.QObject):
    """Everything a pane asks of the engine's sink: one signal carrying frames.

    `VideoPane` connects by signal name rather than by class - `window.ScreenView`
    already does the same - so this is not a weaker test than a real `QVideoSink`
    would give. It is a stronger one, because it cannot quietly start decoding.
    """

    videoFrameChanged = QtCore.Signal(QtMultimedia.QVideoFrame)

    def emit_frame(self, frame: QtMultimedia.QVideoFrame) -> None:
        self.videoFrameChanged.emit(frame)


class HoldingSink(FakeSink):
    """A sink that keeps its last frame, which is the one way `QVideoSink` differs.

    It matters for exactly one thing, and that thing is §4's swap: a pane pointed at
    this sink while paused can ask what is already on screen instead of waiting for a
    `videoFrameChanged` that a paused stream will never send again.

    `FakeSink` deliberately stays without the accessor rather than growing one, so the
    pane's other path - "a sink that cannot be asked is left alone, not blanked" -
    keeps a test, and so the connection-counting tests go on measuring signals only.
    """

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._held = QtMultimedia.QVideoFrame()

    def videoFrame(self) -> QtMultimedia.QVideoFrame:
        return self._held

    def emit_frame(self, frame: QtMultimedia.QVideoFrame) -> None:
        self._held = frame
        super().emit_frame(frame)


def _frame(width: int = 16, height: int = 9) -> QtMultimedia.QVideoFrame:
    """A valid frame of a given shape. Valid matters: an invalid one has no size."""
    shape = QtMultimedia.QVideoFrameFormat(
        QtCore.QSize(width, height),
        QtMultimedia.QVideoFrameFormat.PixelFormat.Format_ARGB8888,
    )
    return QtMultimedia.QVideoFrame(shape)


def _shown(pane: panes.VideoPane) -> tuple[int, int]:
    """The size of the frame the pane's own `QVideoWidget` is holding right now.

    Read at the widget's sink rather than at anything the pane records, so every
    assertion built on it is about the picture a viewer is looking at and not about
    bookkeeping that agreed with itself. `(-1, -1)` is Qt's "no frame at all": a
    default-constructed `QVideoFrame` has no size.
    """
    size = pane.video.videoSink().videoFrame().size()
    return (size.width(), size.height())


class PaneTestCase(unittest.TestCase):
    """Shared set-up, and the mouse."""

    def setUp(self) -> None:
        self.app = _app()
        # English in force before each test, whatever ran before it left behind, and
        # again afterwards: `i18n` holds one language for the whole process.
        i18n.set_language(i18n.LANG_EN)
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        #: Panes are children of this, so each one has a host to be laid out in.
        self.host = QtWidgets.QWidget()
        self.host.resize(960, 540)
        self.made: list[QtWidgets.QWidget] = [self.host]

    def tearDown(self) -> None:
        for widget in reversed(self.made):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def keep(self, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self.made.append(widget)
        return widget

    def stacking(self, parent: QtWidgets.QWidget) -> list[QtWidgets.QWidget]:
        """`parent`'s child widgets from the bottom of the pile to the top.

        Qt keeps sibling stacking order in the child list - `raise_()` moves a child to
        the end of it - so this is the real answer to "what is drawn over what", which
        a screenshot under the offscreen plugin would only let us guess at.
        """
        return [
            child
            for child in parent.children()
            if isinstance(child, QtWidgets.QWidget)
        ]

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


class VideoPaneTests(PaneTestCase):
    def make(self, sink: FakeSink | None = None) -> panes.VideoPane:
        pane = panes.VideoPane(0, "Экран 1", sink, (1920, 1080), self.host)
        pane.resize(640, 360)
        return pane

    # -- the frame bridge --------------------------------------------------

    def test_a_frame_from_the_sink_reaches_the_panes_widget(self) -> None:
        sink = FakeSink()
        pane = self.make(sink)
        seen: list[bool] = []
        pane.video.videoSink().videoFrameChanged.connect(
            lambda frame: seen.append(frame.isValid())
        )
        sink.emit_frame(_frame())
        self.assertEqual(seen, [True], "the sink's frame never reached the pane")

    def test_set_stream_swaps_the_sink_and_leaves_exactly_one_connection(self) -> None:
        """The interleaved-frames bug, counted rather than asserted about."""
        first, second = FakeSink(), FakeSink()
        pane = self.make(first)
        delivered: list[int] = []
        pane.video.videoSink().videoFrameChanged.connect(
            lambda frame: delivered.append(frame.width())
        )

        pane.set_stream(1, "Экран 2", second)
        self.assertEqual(pane.stream_index(), 1)
        self.assertEqual(pane.label.text(), "Экран 2")

        first.emit_frame(_frame(32, 18))
        self.assertEqual(
            delivered, [], "the old stream is still being shown after the swap"
        )
        second.emit_frame(_frame(64, 36))
        self.assertEqual(
            delivered, [64], "the new stream arrived twice: two live connections"
        )

    # -- the picture after a swap, with no frame following it --------------

    def test_a_swap_while_paused_moves_the_pictures_and_not_only_the_labels(
        self,
    ) -> None:
        """§4's click, with nothing decoding afterwards - the defect, both ways round.

        Paused, `videoFrameChanged` never fires again, so reconnecting the sinks left
        the main pane displaying the 640x360 frame of screen 1 under a label that now
        read «Экран 2», and the thumbnail the 360x640 frame of screen 2 under «Экран
        1 — главный». Both panes are read, because a repair that only pushed into the
        pane that was clicked would simply cross them the other way round.
        """
        first, second = HoldingSink(), HoldingSink()
        main = self.make(first)
        side = panes.VideoPane(1, "Экран 2", second, (1080, 1920), self.host)
        first.emit_frame(_frame(640, 360))
        second.emit_frame(_frame(360, 640))
        self.assertEqual((_shown(main), _shown(side)), ((640, 360), (360, 640)))

        # Exactly what `PlayerStage._apply_streams` does, and nothing else.
        main.set_stream(1, "Экран 2", second)
        side.set_stream(0, "Экран 1", first)

        self.assertEqual(
            (_shown(main), _shown(side)),
            ((360, 640), (640, 360)),
            "the labels swapped and the pictures stayed where they were",
        )
        self.assertEqual(
            (main.label.text(), side.label.text()), ("Экран 2", "Экран 1")
        )

    def test_a_swap_onto_a_stream_with_nothing_clears_the_old_picture(self) -> None:
        """A pane that has nothing of its own to show must not show somebody else's.

        The sink is real enough to be asked and is simply holding nothing - a stream
        that has not decoded a frame yet. Blank is the truthful picture; the previous
        stream's last frame under the new stream's name is the defect above.
        """
        first, second = HoldingSink(), HoldingSink()
        pane = self.make(first)
        first.emit_frame(_frame(640, 360))
        self.assertEqual(_shown(pane), (640, 360))

        pane.set_stream(1, "Экран 2", second)
        self.assertEqual(
            _shown(pane),
            (-1, -1),
            "the pane kept the previous stream's picture under the new name",
        )

    def test_the_frame_qt_pushes_at_the_end_does_not_wipe_the_picture(self) -> None:
        """§5 of PLAYER_LAYOUTS: a stream that has ended holds its last frame.

        Measured on 2026-11-05 against this project's own recording: the last thing Qt
        gives a sink when a file runs out is a frame with `isValid() == False`, and
        painting it is exactly what `_blank()` does on purpose. So the picture went
        black at the end of every recording, and the two-camera case lost both pictures
        at once.
        """
        sink = HoldingSink()
        pane = self.make(sink)
        sink.emit_frame(_frame(640, 360))
        self.assertEqual(_shown(pane), (640, 360))

        sink.emit_frame(QtMultimedia.QVideoFrame())
        self.assertEqual(
            _shown(pane), (640, 360),
            "the end-of-file frame wiped the picture the pane had ended on",
        )

    def test_a_deliberate_clear_still_clears(self) -> None:
        """The same empty frame means something else when a pane changes stream.

        `_show_held_frame` asks the new sink what it is holding, and a stream that has
        decoded nothing answers with an invalid frame. There it is the truthful
        picture, so it must still be painted - the two cases have the same shape and
        opposite meanings, and that is why they go in by different doors.
        """
        first, second = HoldingSink(), HoldingSink()
        pane = self.make(first)
        first.emit_frame(_frame(640, 360))
        pane.set_stream(1, "Экран 2", second)
        self.assertEqual(_shown(pane), (-1, -1))

    def test_the_held_frame_is_pushed_once_and_leaves_one_connection(self) -> None:
        """The repair must not become the interleaved-frames bug by another road."""
        first, second = HoldingSink(), HoldingSink()
        pane = self.make(first)
        first.emit_frame(_frame(32, 18))
        second.emit_frame(_frame(64, 36))
        delivered: list[int] = []
        pane.video.videoSink().videoFrameChanged.connect(
            lambda frame: delivered.append(frame.width())
        )

        pane.set_stream(1, "Экран 2", second)
        self.assertEqual(delivered, [64], "the held frame was pushed more than once")
        first.emit_frame(_frame(96, 54))
        self.assertEqual(
            delivered, [64], "the old stream is still being shown after the swap"
        )

    def test_the_held_frame_tells_the_pane_the_new_streams_shape(self) -> None:
        """`set_stream` forgets the size; a host with no sidecar size never says it.

        The held frame is pushed through the same path a decoded one takes, so
        `_adopt_size` sees it and the pane is not left at `(0, 0)`, which every
        caller of `native_size()` reads as "this shape is not known".
        """
        first, second = HoldingSink(), HoldingSink()
        pane = self.make(first)
        second.emit_frame(_frame(1440, 2560))
        pane.set_stream(1, "Экран 2", second)
        self.assertEqual(pane.native_size(), (1440, 2560))

    def test_a_sink_that_cannot_be_asked_keeps_the_picture_it_had(self) -> None:
        """A double carrying the signal and nothing else is left alone, on purpose.

        `VideoPane` connects by signal name rather than by class, so a sink without
        `videoFrame` is a legitimate caller and not a mistake. Nothing is known about
        what such a sink holds, and blanking the pane on that guess would be worse
        than the single frame of staleness a playing stream clears in 16 ms.
        """
        first = HoldingSink()
        pane = self.make(first)
        first.emit_frame(_frame(640, 360))
        pane.set_stream(1, "Экран 2", FakeSink())
        self.assertEqual(_shown(pane), (640, 360))

    def test_a_sink_that_will_not_give_up_its_frame_does_not_take_the_swap_down(
        self,
    ) -> None:
        """The swap runs from a mouse release; a raise here would be a dead player."""

        class Deleted(FakeSink):
            def videoFrame(self) -> QtMultimedia.QVideoFrame:
                raise RuntimeError("the sink's C++ side is already gone")

        pane = self.make(HoldingSink())
        pane.set_stream(1, "Экран 2", Deleted())
        self.assertEqual(pane.stream_index(), 1)
        self.assertEqual(pane.label.text(), "Экран 2")
        self.assertEqual(
            pane.setup_problem, "", "a sink that is connected is not a black pane"
        )

    def test_set_stream_with_no_sink_says_why_in_words(self) -> None:
        pane = self.make(FakeSink())
        self.assertEqual(pane.setup_problem, "")
        pane.set_stream(1, "Экран 2", None)
        self.assertIn("Экран 2", pane.setup_problem)
        self.assertTrue(
            pane.setup_problem.strip(), "a pane that will stay black said nothing"
        )

    def test_a_pane_built_without_a_sink_says_why(self) -> None:
        pane = self.make(None)
        self.assertIn("Экран 1", pane.setup_problem)

    def test_a_sink_without_the_signal_is_refused_in_words(self) -> None:
        pane = self.make(FakeSink())
        pane.set_stream(0, "Экран 1", object())
        self.assertIn("videoFrameChanged", pane.setup_problem)

    def test_a_swap_takes_the_ended_badge_with_the_stream_it_was_about(self) -> None:
        """§4's swap is "a signal reconnection"; the host calls nothing else.

        So the pane has to drop what belonged to the stream that left. Keeping it was
        worse than stale: `set_label` rewords a default badge, so the pane that had
        just been handed Экран 1 said «Экран 1 — запись закончилась» over a stream
        playing normally underneath it.
        """
        pane = self.make(FakeSink())
        pane.set_stream(1, "Экран 2", FakeSink())
        pane.set_ended(True)
        self.assertIn("Экран 2", pane.badge.text())

        pane.set_stream(0, "Экран 1", FakeSink())
        self.assertFalse(pane.has_ended(), "the swap kept the old stream's end")
        self.assertTrue(
            pane.badge.isHidden(),
            f"the badge survived the swap, now reading {pane.badge.text()!r}",
        )
        self.assertNotIn(
            "Экран 1", pane.badge.text(),
            "the badge was re-worded to say the new stream had finished",
        )

    def test_a_swap_leaves_the_seek_veil_alone(self) -> None:
        """The other half of the rule, and the reason it is not symmetric.

        A seek is the player's state and dims every pane at once, so clearing it here
        would un-dim one pane in the middle of a seek that is still in flight.
        """
        pane = self.make(FakeSink())
        pane.set_busy(True)
        pane.set_stream(1, "Экран 2", FakeSink())
        self.assertTrue(pane.is_busy())
        self.assertFalse(pane.veil.isHidden())

    def test_a_swap_forgets_the_streams_size(self) -> None:
        """`native_size()` must not answer for the stream that has gone.

        A host asking a pane how big its stream is must not be told the old one's
        size, and the host may not have the new one yet - the sidecar sizes arrive
        asynchronously, and on a paused recording the next frame is a long time
        coming.
        """
        pane = self.make(FakeSink())
        self.assertEqual(pane.native_size(), (1920, 1080))
        pane.set_stream(1, "Экран 2", FakeSink())
        self.assertEqual(
            pane.native_size(), (0, 0),
            "the pane reported the previous stream's dimensions after a swap",
        )

    def test_the_stream_shape_is_learnt_from_the_first_frame(self) -> None:
        """A host that never calls `set_native_size` still gets the right shape."""
        sink = FakeSink()
        pane = panes.VideoPane(0, "Экран 1", sink, (0, 0), self.host)
        sink.emit_frame(_frame(1440, 2560))
        self.assertEqual(pane.native_size(), (1440, 2560))

    def test_a_refused_size_keeps_the_one_it_had(self) -> None:
        pane = self.make(FakeSink())
        pane.set_native_size((0, 1080))
        self.assertEqual(pane.native_size(), (1920, 1080))

    # -- the click ---------------------------------------------------------

    def test_a_clean_press_and_release_is_a_click(self) -> None:
        pane = self.make(FakeSink())
        clicks: list[int] = []
        pane.clicked.connect(lambda: clicks.append(1))
        self.press(pane, QtCore.QPointF(300, 180))
        self.release(pane, QtCore.QPointF(300, 180))
        self.assertEqual(clicks, [1])

    def test_a_press_that_travelled_is_not_a_click(self) -> None:
        pane = self.make(FakeSink())
        clicks: list[int] = []
        pane.clicked.connect(lambda: clicks.append(1))
        far = layouts.DRAG_SLOP + 2
        self.press(pane, QtCore.QPointF(300, 180))
        self.move_to(pane, QtCore.QPointF(300 + far, 180))
        self.release(pane, QtCore.QPointF(300 + far, 180))
        self.assertEqual(
            clicks, [], f"{far} px of travel should not read as a click"
        )

    # -- the two states of §6 ---------------------------------------------

    def test_busy_dims_and_clears(self) -> None:
        pane = self.make(FakeSink())
        self.assertFalse(pane.is_busy())
        pane.set_busy(True)
        self.assertTrue(pane.is_busy())
        self.assertFalse(pane.veil.isHidden())
        pane.set_busy(False)
        self.assertFalse(pane.is_busy())
        self.assertTrue(pane.veil.isHidden())

    def test_ended_shows_a_badge_naming_the_stream(self) -> None:
        pane = self.make(FakeSink())
        pane.set_stream(1, "Экран 2", FakeSink())
        pane.set_ended(True)
        self.assertTrue(pane.has_ended())
        self.assertIn("Экран 2", pane.badge.text())
        self.assertIn("the recording ends here", pane.badge.text())
        pane.set_ended(False)
        self.assertTrue(pane.badge.isHidden())

    def test_an_ended_badge_can_be_given_its_own_words(self) -> None:
        pane = self.make(FakeSink())
        pane.set_ended(True, "Экран 1 — запись закончилась раньше")
        self.assertEqual(pane.badge.text(), "Экран 1 — запись закончилась раньше")
        # And renaming the stream does not replace them with the default sentence.
        pane.set_label("Экран 1 (ноутбук)")
        self.assertEqual(pane.badge.text(), "Экран 1 — запись закончилась раньше")

    def test_every_state_paints(self) -> None:
        """Each state is a different paint path, and a paint that raises is fatal."""
        pane = self.make(FakeSink())
        pane.show()
        for busy in (False, True):
            for ended in (False, True):
                pane.set_busy(busy)
                pane.set_ended(ended)
                self.assertFalse(
                    pane.grab().isNull(),
                    f"nothing painted (busy={busy}, ended={ended})",
                )
        for _ in range(40):  # the label faded right out: another path again
            pane._fade_step()
        self.assertFalse(pane.grab().isNull())

    def test_a_pane_is_a_child_and_not_a_window(self) -> None:
        pane = self.make(FakeSink())
        self.assertIs(pane.parentWidget(), self.host)
        self.assertFalse(pane.isWindow())
        self.assertFalse(
            pane.windowFlags() & QtCore.Qt.WindowType.FramelessWindowHint
        )


class LanguageTests(PaneTestCase):
    """Everything a viewer reads here, in both languages, switched while it is open.

    A pane is built once and lives for the whole session, so a string read at
    construction and never read again is the one the settings switch cannot reach.
    Each of them is therefore checked *after* the switch, on the pane that was already
    standing - not on a fresh one built in the second language, which would pass with
    no `retranslate` at all.
    """

    def make(self, sink: FakeSink | None = None) -> panes.VideoPane:
        pane = panes.VideoPane(0, "Экран 1", sink, (1920, 1080), self.host)
        pane.resize(640, 360)
        return pane

    def test_the_seek_veil_changes_language_under_an_open_pane(self) -> None:
        pane = self.make(FakeSink())
        pane.set_busy(True)
        self.assertEqual(pane.veil.text(), "Seeking…")
        with _in(i18n.LANG_RU):
            self.assertEqual(pane.veil.text(), "Перемотка…")
        self.assertEqual(pane.veil.text(), "Seeking…")

    def test_the_ended_badge_changes_language_while_it_is_showing(self) -> None:
        pane = self.make(FakeSink())
        pane.set_ended(True)
        self.assertEqual(pane.badge.text(), "Экран 1 — the recording ends here")
        with _in(i18n.LANG_RU):
            self.assertEqual(pane.badge.text(), "Экран 1 — запись закончилась")

    def test_a_badge_the_host_wrote_is_not_translated_over(self) -> None:
        # The host's own sentence is the host's; `retranslate` rewords only the badge
        # this module wrote, exactly as `set_label` does.
        pane = self.make(FakeSink())
        pane.set_ended(True, "Экран 1 — запись закончилась раньше")
        with _in(i18n.LANG_RU):
            self.assertEqual(pane.badge.text(), "Экран 1 — запись закончилась раньше")
        self.assertEqual(pane.badge.text(), "Экран 1 — запись закончилась раньше")

    def test_the_label_is_the_streams_name_and_is_never_translated(self) -> None:
        pane = self.make(FakeSink())
        with _in(i18n.LANG_RU):
            self.assertEqual(pane.label.text(), "Экран 1")
        self.assertEqual(pane.label.text(), "Экран 1")

    def test_a_black_panes_complaint_is_said_again_in_the_other_language(self) -> None:
        pane = self.make(None)
        self.assertIn("no source of frames", pane.setup_problem)
        self.assertIn("Экран 1", pane.setup_problem)
        with _in(i18n.LANG_RU):
            self.assertIn("нет источника кадров", pane.setup_problem)
            self.assertIn("Экран 1", pane.setup_problem)
        self.assertIn("no source of frames", pane.setup_problem)

    def test_a_sink_without_the_signal_keeps_the_signals_name_in_both(self) -> None:
        pane = self.make(FakeSink())
        pane.set_stream(0, "Экран 1", object())
        for code in (i18n.LANG_EN, i18n.LANG_RU):
            with self.subTest(language=code), _in(code):
                self.assertIn("videoFrameChanged", pane.setup_problem)

    def test_a_pane_with_nothing_wrong_says_nothing_in_either(self) -> None:
        pane = self.make(FakeSink())
        for code in (i18n.LANG_EN, i18n.LANG_RU):
            with self.subTest(language=code), _in(code):
                self.assertEqual(pane.setup_problem, "")

    def test_a_swap_clears_the_complaint_in_both(self) -> None:
        pane = self.make(None)
        self.assertTrue(pane.setup_problem)
        pane.set_stream(1, "Экран 2", FakeSink())
        with _in(i18n.LANG_RU):
            self.assertEqual(pane.setup_problem, "")

    def test_a_closed_pane_does_not_make_the_next_switch_raise(self) -> None:
        """`notifier` outlives every pane, so a dead one must leave the connection.

        PySide6 drops a connection to a bound method when the receiving QObject is
        destroyed, which is why `retranslate` is a method and not a lambda: a lambda
        would keep the connection, keep the Python wrapper alive, and call
        `set_text` on a deleted C++ widget the next time the language changed - a
        crash in the settings panel, reported from the player.
        """
        doomed = self.make(FakeSink())
        doomed.set_busy(True)
        survivor = self.make(FakeSink())
        doomed.setParent(None)
        doomed.deleteLater()
        self.app.processEvents()
        with _in(i18n.LANG_RU):
            self.assertEqual(survivor.veil.text(), "Перемотка…")

    def test_every_string_this_module_shows_has_a_russian_entry(self) -> None:
        """The catalogue's half, read out of the source rather than listed here.

        `tests/test_i18n.py` does this for the whole program; this is the same check
        narrowed to one file, so a string added to `panes.py` without a translation
        fails in the module it was added to.
        """
        tree = ast.parse(Path(panes.__file__).read_text(encoding="utf-8"))
        shown = [
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ]
        self.assertTrue(shown, "no translated strings were found in panes.py")
        self.assertEqual(i18n.missing_translations(shown), [])


class ModuleHeaderTests(unittest.TestCase):
    """The header is read as a claim about what the module touches.

    `panes`'s whole argument is that a pane owns no player and reaches no media
    machinery - "a pane holds nothing but a signal connection". `QtMultimedia` sat in
    the import line next to `QtMultimediaWidgets` without a single use, which tells the
    next reader to go looking for the player this module deliberately does not have.

    Checked here rather than in `tools/static_review.py`, which has no unused-import
    rule: giving it one is worth doing and is its own job, because it would report a
    dozen files this change has no business touching.
    """

    def test_panes_imports_nothing_it_does_not_use(self) -> None:
        tree = ast.parse(Path(panes.__file__).read_text(encoding="utf-8"))
        imported: dict[str, int] = {}
        for node in tree.body:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                if name != "annotations":  # `from __future__`: never a name in the body
                    imported[name] = node.lineno
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertEqual(
            sorted(f"{name} (line {imported[name]})"
                   for name in imported if name not in used),
            [],
        )


if __name__ == "__main__":
    unittest.main()
