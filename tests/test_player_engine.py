"""Checks for `screenrec.player.engine`.

Nothing here opens real media. The sidecars are written by hand into a temporary folder
and the players are a stub with the handful of methods and signals the engine uses, so
the checks run anywhere, in a second, and test the engine's decisions rather than Media
Foundation's.

The decisions worth pinning are the ones the measurements paid for: a follower that is
inside the threshold must be left alone (two players on one file were measured a constant
33 ms apart, worst sample 67 ms, so correcting at that distance would stutter for
nothing), a follower outside it must be pulled into line, a seek must land on every
player, and stop must leave nothing running.

No GPU, no visible window: the Qt part runs under the offscreen platform plugin, which
this file sets before PySide6 is imported.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtMultimedia, QtWidgets  # noqa: E402

from screenrec.player import engine  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    return QtWidgets.QApplication([])


# --------------------------------------------------------------------------- the stub


class StubPlayer(QtCore.QObject):
    """A QMediaPlayer-shaped object that decodes nothing and remembers everything."""

    errorOccurred = QtCore.Signal(int, str)
    mediaStatusChanged = QtCore.Signal(object)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.source = None
        self.sink = None
        self.audio_output: object | None = "unset"
        self.rate = 1.0
        self.state = "stopped"
        self._position = 0
        self._duration = 45_000
        self.seeks: list[int] = []
        self.calls: list[str] = []
        #: A file that will not seek. The requests are still recorded, which is how a
        #: real one behaves too: `setPosition` returns and the playhead does not move.
        self.refuse_seeks = False

    # -- the API the engine uses ------------------------------------------

    def setSource(self, url) -> None:  # noqa: N802 - Qt's spelling
        self.source = url
        self.calls.append("setSource")

    def setVideoSink(self, sink) -> None:  # noqa: N802
        self.sink = sink

    def setAudioOutput(self, output) -> None:  # noqa: N802
        self.audio_output = output

    def setPlaybackRate(self, rate: float) -> None:  # noqa: N802
        self.rate = float(rate)

    def setPosition(self, milliseconds: int) -> None:  # noqa: N802
        self.seeks.append(int(milliseconds))
        if self.refuse_seeks:
            return
        # A real player cannot run past its own last frame, and the seeking state has
        # to cope with a target no stream of this length can reach.
        self._position = min(int(milliseconds), self._duration)

    def position(self) -> int:
        return self._position

    def duration(self) -> int:
        return self._duration

    def play(self) -> None:
        self.state = "playing"
        self.calls.append("play")

    def pause(self) -> None:
        self.state = "paused"
        self.calls.append("pause")

    def stop(self) -> None:
        self.state = "stopped"
        self._position = 0
        self.calls.append("stop")

    # -- test helpers ------------------------------------------------------

    def place(self, milliseconds: int) -> None:
        """Move the playhead the way decoding would, without recording a seek."""
        self._position = int(milliseconds)

    def set_length(self, milliseconds: int) -> None:
        """Make this stream a different length from the others. §6's unequal lengths."""
        self._duration = int(milliseconds)


class EndOfFilePlayer(StubPlayer):
    """`StubPlayer` plus the three measured things a real player does at the far end.

    `StubPlayer` models a decoder that can be put anywhere and never runs out of file,
    which is what most of this suite is about. These three are what Qt actually does,
    and they are the whole subject of the checks below.

    Measured on 2026-11-05 in bare Qt, one of this project's own files, no ScreenRec
    code anywhere in it:

        at EndOfMedia   position = 10267   state = StoppedState   frame: invalid
        after pause()   position =     0   state = PausedState    frame: 0

    so **`pause()` on a player at the end rewinds it to zero**, and a player that has
    run out is *stopped* where it is rather than paused there. The third was measured
    through the real engine against `scratch/twofinal`: scrubbed to 10267 of 10267 and
    then told to play, **the player decodes nothing and answers `EndOfMedia`** - the
    position stayed at 10267 and `playing()` came back False.

    Only the checks about the end use this. Giving `StubPlayer` these three would
    change what every other check in this file is about.
    """

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._at_end = False

    def mediaStatus(self):  # noqa: N802 - Qt's spelling
        status = QtMultimedia.QMediaPlayer.MediaStatus
        return status.EndOfMedia if self._at_end else status.BufferedMedia

    def play(self) -> None:
        if self._position >= self._duration:
            # Told to play while it is on its last frame: nothing is decoded and the
            # status goes to EndOfMedia. The engine hears that on the next turn of the
            # event loop, which these checks spell `sync._tick()`.
            self._at_end = True
            self.state = "stopped"
            self.calls.append("play")
            return
        self._at_end = False
        super().play()

    def pause(self) -> None:
        if self._at_end:
            self._position = 0
            self._at_end = False
        super().pause()

    def stop(self) -> None:
        self._at_end = False
        super().stop()

    def setPosition(self, milliseconds: int) -> None:  # noqa: N802
        super().setPosition(milliseconds)
        if self._position < self._duration:
            self._at_end = False

    # -- test helpers ------------------------------------------------------

    def run_to_the_end(self) -> None:
        """What playing to the last frame leaves behind: stopped there, not paused."""
        self._position = self._duration
        self._at_end = True
        self.state = "stopped"


class LoadingPlayer(StubPlayer):
    """`StubPlayer` plus the part of opening a file that `StubPlayer` skips.

    `setSource` is asynchronous. `StubPlayer` knows its length from the start and can
    be positioned at once, and both of those hide a real fault, so the checks about
    opening a file use this instead. Measured on 2026-11-05 in bare Qt with one of
    this project's own files, no ScreenRec code in it, three runs alike - the status a
    `setPosition(5000)` was made from, and where the player settled:

        LoadingMedia  -> 0       and the length is already reported here
        LoadedMedia   -> 5000
        BufferedMedia -> never arrives at all for a player that is only paused

    So: no length and every seek dropped until `finish_loading`, which reports the
    length and then says `LoadedMedia`, in that order, as Qt does.
    """

    durationChanged = QtCore.Signal(int)  # noqa: N815 - Qt's spelling

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._duration = 0  # nothing is known until the decoder has read the file
        self.refuse_seeks = True  # and nothing can be positioned either

    def report_length(self, milliseconds: int) -> None:
        """What the decoder does a few hundred milliseconds after `setSource`."""
        self._duration = int(milliseconds)
        self.durationChanged.emit(self._duration)

    def finish_loading(self, milliseconds: int = 45_000) -> None:
        """The length, and then the status that means the file can be positioned."""
        self.report_length(milliseconds)
        self.refuse_seeks = False
        self.mediaStatusChanged.emit(
            QtMultimedia.QMediaPlayer.MediaStatus.LoadedMedia
        )


class StubAudio(QtCore.QObject):
    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.muted = False
        self.volume = 1.0

    def setMuted(self, muted: bool) -> None:  # noqa: N802
        self.muted = bool(muted)

    def setVolume(self, value: float) -> None:  # noqa: N802
        self.volume = float(value)


# --------------------------------------------------------------------------- fixtures


def write_set(
    folder: Path,
    screens: list[dict],
    *,
    audio: str | None = None,
    present: list[str] | None = None,
    stem: str = "Take",
    version: int = 1,
    offsets: list[object] | None = None,
) -> Path:
    """Write a sidecar by hand, plus whichever of its files should exist.

    The default is a **version 1** index with no offsets anywhere, because that is what
    every recording this program has made looks like and most of this file is about
    that case still behaving exactly as it did. `offsets` adds the version 2 field
    without touching the module-level screen constants, which are shared.
    """
    names = [s["file"] for s in screens]
    for name in names if present is None else present:
        (folder / name).write_bytes(b"not really an mp4")
    entries = [dict(screen) for screen in screens]
    if offsets is not None:
        for entry, offset in zip(entries, offsets):
            entry["offset_ms"] = offset
    document = {
        "version": version if offsets is None else max(version, 2),
        "recorded": "2026-11-04T03:00:00",
        "audio": audio if audio is not None else names[0],
        "screens": entries,
    }
    sidecar = folder / f"{stem}.screenrec.json"
    sidecar.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return sidecar


#: Two landscape screens side by side, the second to the RIGHT of the primary one.
SIDE_BY_SIDE = [
    {
        "file": "Take_screen0.mp4",
        "monitor": 0,
        "desktop": [0, 0, 2560, 1440],
        "size": [2560, 1440],
        "rotation": 0,
        "primary": True,
    },
    {
        "file": "Take_screen1.mp4",
        "monitor": 1,
        "desktop": [2560, 0, 1440, 2560],
        "size": [1440, 2560],
        "rotation": 2,
        "primary": False,
    },
]

#: One screen: the common case, and the one §6's two new states must be inert for.
ONE_SCREEN = [SIDE_BY_SIDE[0]]

#: The same pair with the second screen to the LEFT, which Windows gives a negative x.
LEFT_OF_PRIMARY = [
    {
        "file": "Take_screen0.mp4",
        "monitor": 0,
        "desktop": [0, 0, 2560, 1440],
        "size": [2560, 1440],
        "rotation": 0,
        "primary": True,
    },
    {
        "file": "Take_screen1.mp4",
        "monitor": 1,
        "desktop": [-1920, -200, 1920, 1080],
        "size": [1920, 1080],
        "rotation": 0,
        "primary": False,
    },
]


class OffsetLimitTests(unittest.TestCase):
    """The one number that lives in two modules on purpose.

    `player.window` names no engine: its whole contract with the clock is the
    duck-typed adapter its docstring sets out, and importing `engine` for one
    constant would be the first crack in that. The cost is a number written twice -
    the field's clamp and the engine's - and two clamps that drift apart are an
    offset the viewer can type and the engine then silently refuses. So they are
    pinned together here instead.
    """

    def test_the_panel_and_the_clock_clamp_at_the_same_number(self) -> None:
        from screenrec.player import window as player_window

        self.assertEqual(player_window.OFFSET_LIMIT_MS, engine.OFFSET_LIMIT_MS)


class RecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_loads_a_hand_written_set(self) -> None:
        sidecar = write_set(self.folder, SIDE_BY_SIDE)
        recording, problem = engine.Recording.load(sidecar)
        self.assertIsNotNone(recording)
        self.assertEqual(problem, "")
        assert recording is not None
        self.assertEqual(len(recording.screens), 2)
        self.assertEqual(recording.screens[1].monitor, 1)
        self.assertEqual(recording.screens[1].desktop, (2560, 0, 1440, 2560))
        self.assertEqual(recording.screens[1].rotation, 2)
        self.assertTrue(recording.screens[0].primary)
        self.assertEqual(recording.recorded, "2026-11-04T03:00:00")
        self.assertEqual(recording.folder, self.folder)

    def test_the_master_is_the_audio_file(self) -> None:
        sidecar = write_set(self.folder, SIDE_BY_SIDE, audio="Take_screen1.mp4")
        recording, problem = engine.Recording.load(sidecar)
        assert recording is not None
        self.assertEqual(problem, "")
        self.assertEqual(recording.audio_file, self.folder / "Take_screen1.mp4")
        self.assertEqual(recording.master_index(), 1)

    def test_a_missing_file_is_reported_not_raised(self) -> None:
        sidecar = write_set(
            self.folder, SIDE_BY_SIDE, present=["Take_screen0.mp4"]
        )
        recording, problem = engine.Recording.load(sidecar)
        self.assertIsNotNone(recording)
        assert recording is not None
        self.assertIn("Take_screen1.mp4", problem)
        self.assertEqual([s.file.name for s in recording.screens], ["Take_screen0.mp4"])

    def test_a_missing_audio_file_hands_the_clock_over(self) -> None:
        sidecar = write_set(
            self.folder,
            SIDE_BY_SIDE,
            audio="Take_screen1.mp4",
            present=["Take_screen0.mp4"],
        )
        recording, problem = engine.Recording.load(sidecar)
        assert recording is not None
        self.assertIn("Take_screen1.mp4", problem)
        self.assertEqual(recording.audio_file, self.folder / "Take_screen0.mp4")
        self.assertEqual(recording.master_index(), 0)

    def test_nothing_there_at_all(self) -> None:
        sidecar = write_set(self.folder, SIDE_BY_SIDE, present=[])
        recording, problem = engine.Recording.load(sidecar)
        self.assertIsNone(recording)
        self.assertTrue(problem)

    def test_a_sidecar_that_is_not_one(self) -> None:
        bad = self.folder / "Broken.screenrec.json"
        bad.write_text("{ this is not json", encoding="utf-8")
        recording, problem = engine.Recording.load(bad)
        self.assertIsNone(recording)
        self.assertIn("Broken.screenrec.json", problem)

    def test_a_sidecar_that_is_not_there(self) -> None:
        recording, problem = engine.Recording.load(self.folder / "Nope.screenrec.json")
        self.assertIsNone(recording)
        self.assertTrue(problem)

    # -- two files that did not start together, §2.1 -----------------------

    def test_a_version_1_index_has_no_offsets_and_says_nothing_about_it(self) -> None:
        # Every recording this program has ever made. A missing `offset_ms` is 0 and is
        # not worth a note: it is the ordinary case, not a fault.
        sidecar = write_set(self.folder, SIDE_BY_SIDE)
        recording, problem = engine.Recording.load(sidecar)
        assert recording is not None
        self.assertEqual(problem, "")
        self.assertEqual([s.offset_ms for s in recording.screens], [0, 0])

    def test_a_version_2_index_carries_the_offsets_through(self) -> None:
        sidecar = write_set(self.folder, SIDE_BY_SIDE, offsets=[0, -1480])
        recording, problem = engine.Recording.load(sidecar)
        assert recording is not None
        self.assertEqual(problem, "")
        self.assertEqual([s.offset_ms for s in recording.screens], [0, -1480])

    def test_an_unreadable_offset_is_said_out_loud_and_not_swallowed(self) -> None:
        sidecar = write_set(self.folder, SIDE_BY_SIDE, offsets=[0, "soon"])
        recording, problem = engine.Recording.load(sidecar)
        assert recording is not None
        # The file still plays - an offset is alignment, not content - but the viewer
        # who spent time lining the two up is told it did not survive.
        self.assertEqual([s.offset_ms for s in recording.screens], [0, 0])
        self.assertIn("Take_screen1.mp4", problem)
        self.assertEqual(len(recording.screens), 2)

    def test_an_absolute_path_is_opened_where_it_points(self) -> None:
        # Two cameras are not guaranteed to live in one folder, so §1.3 lets a "file"
        # be an absolute path. A bare name beside it must still mean what it always did.
        elsewhere = self.folder / "elsewhere"
        elsewhere.mkdir()
        far = elsewhere / "camera_b.mp4"
        far.write_bytes(b"not really an mp4")
        screens = [
            dict(SIDE_BY_SIDE[0]),
            dict(SIDE_BY_SIDE[1], file=str(far)),
        ]
        sidecar = write_set(
            self.folder, screens, present=[SIDE_BY_SIDE[0]["file"]], offsets=[0, 250]
        )
        recording, problem = engine.Recording.load(sidecar)
        assert recording is not None
        self.assertEqual(problem, "")
        self.assertEqual(recording.screens[0].file, self.folder / "Take_screen0.mp4")
        self.assertEqual(recording.screens[1].file, far)

    def test_an_absolute_path_that_has_moved_away_is_named_in_full(self) -> None:
        gone = self.folder / "elsewhere" / "camera_b.mp4"
        screens = [dict(SIDE_BY_SIDE[0]), dict(SIDE_BY_SIDE[1], file=str(gone))]
        sidecar = write_set(self.folder, screens, present=[SIDE_BY_SIDE[0]["file"]])
        recording, problem = engine.Recording.load(sidecar)
        assert recording is not None
        self.assertIn(str(gone), problem)  # the name alone would not say where
        self.assertEqual(len(recording.screens), 1)

    def test_a_set_that_names_no_monitor_still_opens(self) -> None:
        # What `multi.write_set` writes: two files the viewer paired, which never came
        # off a screen. A nominal side-by-side arrangement stands in for the rectangles.
        screens = [
            {"file": "camera_a.mp4", "monitor": 0, "offset_ms": 0},
            {"file": "camera_b.mp4", "monitor": 1, "offset_ms": -1480},
        ]
        sidecar = write_set(self.folder, screens)
        recording, problem = engine.Recording.load(sidecar)
        assert recording is not None
        self.assertEqual(problem, "")
        self.assertEqual(len(recording.screens), 2)
        self.assertEqual(recording.screens[0].desktop, (0, 0, 1920, 1080))
        self.assertEqual(recording.screens[1].desktop, (1920, 0, 1920, 1080))
        self.assertEqual([s.offset_ms for s in recording.screens], [0, -1480])

    def test_a_desktop_rectangle_that_is_broken_is_still_a_refusal(self) -> None:
        # Absent is a set the player saved; present and unusable is a damaged index,
        # and a rectangle that was meant to say where a screen was and does not is
        # worse than none at all.
        screens = [dict(SIDE_BY_SIDE[0]), dict(SIDE_BY_SIDE[1], desktop=[0, 0, 0, 0])]
        sidecar = write_set(self.folder, screens)
        recording, problem = engine.Recording.load(sidecar)
        assert recording is not None
        self.assertEqual(len(recording.screens), 1)
        self.assertIn("Take_screen1.mp4", problem)

    # -- layout ------------------------------------------------------------

    def test_layout_keeps_right_of(self) -> None:
        sidecar = write_set(self.folder, SIDE_BY_SIDE)
        recording, _ = engine.Recording.load(sidecar)
        assert recording is not None
        rects = recording.layout_rects(0.25)
        self.assertEqual(rects, [(0, 0, 640, 360), (640, 0, 360, 640)])
        self.assertGreater(rects[1][0], rects[0][0])  # still to the right

    def test_layout_copes_with_a_negative_origin(self) -> None:
        sidecar = write_set(self.folder, LEFT_OF_PRIMARY)
        recording, _ = engine.Recording.load(sidecar)
        assert recording is not None
        rects = recording.layout_rects(0.5)
        # Everything is shifted by the bounding box's own origin (-1920, -200), so no
        # window has a negative coordinate and the second screen is still to the left.
        self.assertEqual(rects, [(960, 100, 1280, 720), (0, 0, 960, 540)])
        self.assertLess(rects[1][0], rects[0][0])
        self.assertTrue(all(x >= 0 and y >= 0 for x, y, _, _ in rects))

    def test_layout_refuses_to_collapse_windows(self) -> None:
        sidecar = write_set(self.folder, SIDE_BY_SIDE)
        recording, _ = engine.Recording.load(sidecar)
        assert recording is not None
        self.assertEqual(recording.layout_rects(0), recording.layout_rects(1.0))
        for _, _, width, height in recording.layout_rects(0.0001):
            self.assertGreaterEqual(width, 1)
            self.assertGreaterEqual(height, 1)


class EngineFixture:
    """A temporary sidecar and an engine built on stubs. Shared by the classes below.

    A mixin rather than a base `TestCase`: inheriting from one would run every test in
    it again under each subclass's name, which is how a suite quietly triples in size.
    """

    def setUp(self) -> None:
        _app()
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)
        self.made: list[StubPlayer] = []
        self.audio = StubAudio()
        #: Which stub `_engine` builds. The classes about the end of a file set this to
        #: `EndOfFilePlayer`; everything else wants the plain one.
        self.player_class: type[StubPlayer] = StubPlayer

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _engine(self, screens=SIDE_BY_SIDE, **kwargs) -> engine.SyncEngine:
        sidecar = write_set(self.folder, screens, **kwargs)
        recording, _ = engine.Recording.load(sidecar)
        assert recording is not None

        def make_player(parent):
            player = self.player_class(parent)
            self.made.append(player)
            return player

        return engine.SyncEngine(
            recording,
            player_factory=make_player,
            audio_factory=lambda parent: self.audio,
        )


class FirstFrameTests(EngineFixture, unittest.TestCase):
    """A recording nobody has played must still have a picture.

    Qt delivers a frame to a `QVideoSink` on `pause()`, not on `setSource` and not on
    `setPosition`. Measured on 2026-11-05 with a bare `QMediaPlayer` and one of this
    project's own files:

        after setSource      frames: []
        after setPosition    frames: []
        after pause()        frames: [3983]

    Without the pause the player opens as two black panes whose scrubber moves while
    the picture does not - and §2.2 of `docs/PLAYER_SOURCES.md`, nudging one stream a
    frame at a time until two clapperboards line up, cannot work at all until somebody
    has pressed play once.
    """

    def test_every_player_is_paused_once_while_the_clock_is_built(self) -> None:
        sync = self._engine()
        for index, player in enumerate(sync._players):
            with self.subTest(index):
                self.assertIn("pause", player.calls)
                self.assertLess(
                    player.calls.index("setSource"), player.calls.index("pause"),
                    "the pause has to come after the source, or there is nothing to "
                    "decode a frame from",
                )

    def test_it_does_not_leave_the_clock_playing(self) -> None:
        sync = self._engine()
        self.assertFalse(sync.playing())



class SyncEngineTests(EngineFixture, unittest.TestCase):
    # -- construction ------------------------------------------------------

    def test_one_sink_per_screen_and_only_the_master_has_sound(self) -> None:
        sync = self._engine()
        self.assertEqual(len(sync.sinks()), 2)
        self.assertEqual(len(self.made), 2)
        self.assertIs(self.made[0].audio_output, self.audio)
        self.assertIsNone(self.made[1].audio_output)  # muted at the source
        self.assertIs(self.made[0].sink, sync.sinks()[0])
        self.assertIs(self.made[1].sink, sync.sinks()[1])

    def test_a_file_that_will_not_load_is_reported_and_the_rest_play(self) -> None:
        heard: list[str] = []
        sync = self._engine(present=["Take_screen0.mp4"])
        sync.problem.connect(heard.append)
        self.assertEqual(len(sync.sinks()), 1)
        sync.play()
        self.assertTrue(sync.playing())
        self.assertEqual(self.made[0].state, "playing")
        self.assertEqual(heard, [])  # the missing file was dropped at load time

    def test_a_missing_master_hands_the_clock_to_a_follower(self) -> None:
        # The sidecar says screen 1 carries the sound but only screen 0 is on disk, so
        # the engine must still have a clock - and must say the sound is gone.
        sync = self._engine(audio="Take_screen1.mp4", present=["Take_screen0.mp4"])
        self.assertEqual(sync.recording.master_index(), 0)
        sync.play()
        self.assertTrue(sync.playing())

    # -- transport ---------------------------------------------------------

    def test_seek_applies_to_every_screen(self) -> None:
        sync = self._engine()
        positions: list[int] = []
        sync.position_changed.connect(positions.append)
        sync.seek(7_500)
        for player in self.made:
            self.assertEqual(player.position(), 7_500)
            self.assertEqual(player.seeks, [7_500])
        self.assertEqual(positions, [7_500])
        self.assertEqual(sync.position(), 7_500)

    def test_seek_lands_on_everything_before_playback_resumes(self) -> None:
        sync = self._engine()
        sync.play()
        for player in self.made:
            player.calls.clear()
        sync.seek(3_000)
        # Each player was paused, moved, and only then started again; the master is
        # first in both halves of that.
        for player in self.made:
            self.assertEqual(player.calls, ["pause", "play"])
            self.assertEqual(player.position(), 3_000)
        self.assertTrue(sync.playing())

    def test_seek_is_clamped_to_the_recording(self) -> None:
        sync = self._engine()
        sync.seek(-5_000)
        self.assertEqual(sync.position(), 0)
        sync.seek(10 ** 9)
        self.assertEqual(sync.position(), 45_000)  # the stub's duration

    def test_step_moves_whole_frames_both_ways(self) -> None:
        sync = self._engine()
        sync.seek(1_000)
        sync.step(3)
        # A frame at 60 fps rounds to 17 ms - the same one frame the two recordings
        # were measured to start apart.
        self.assertEqual(sync.position(), 1_051)
        sync.step(-5)
        self.assertEqual(sync.position(), 966)
        sync.play()
        sync.step(1)
        self.assertFalse(sync.playing())  # stepping implies pausing

    def test_speed_goes_to_every_player_and_is_clamped(self) -> None:
        heard: list[str] = []
        sync = self._engine()
        sync.problem.connect(heard.append)
        sync.set_speed(2.0)
        self.assertEqual([p.rate for p in self.made], [2.0, 2.0])
        sync.set_speed(99.0)
        self.assertEqual([p.rate for p in self.made], [4.0, 4.0])
        self.assertEqual(len(heard), 1)
        self.assertIn("4x", heard[0])

    def test_sound_reaches_the_one_audio_output(self) -> None:
        sync = self._engine()
        sync.set_muted(True)
        self.assertTrue(self.audio.muted)
        sync.set_volume(2.5)
        self.assertEqual(self.audio.volume, 1.0)  # clamped

    def test_stop_leaves_nothing_running(self) -> None:
        sync = self._engine()
        states: list[bool] = []
        sync.state_changed.connect(states.append)
        sync.play()
        self.assertTrue(sync._timer.isActive())
        sync.stop()
        self.assertFalse(sync.playing())
        self.assertFalse(sync._timer.isActive())
        self.assertEqual(states, [True, False])
        for player in self.made:
            self.assertEqual(player.state, "stopped")
        self.assertEqual(sync.position(), 0)

    def test_pause_stops_the_clock(self) -> None:
        sync = self._engine()
        sync.play()
        sync.pause()
        self.assertFalse(sync._timer.isActive())
        for player in self.made:
            self.assertEqual(player.state, "paused")

    # -- the correction ----------------------------------------------------

    def test_a_follower_inside_the_threshold_is_left_alone(self) -> None:
        sync = self._engine()
        sync.play()
        master, follower = self.made
        master.place(10_000)
        follower.place(10_000 + engine.CORRECTION_MS - 1)
        follower.seeks.clear()
        sync._tick()
        self.assertEqual(follower.seeks, [])
        # 67 ms was the worst sample measured between two players on one file.
        follower.place(10_067)
        sync._tick()
        self.assertEqual(follower.seeks, [])

    def test_a_follower_outside_the_threshold_is_pulled_into_line(self) -> None:
        sync = self._engine()
        sync.play()
        master, follower = self.made
        master.place(10_000)
        follower.place(10_000 + engine.CORRECTION_MS + 1)
        follower.seeks.clear()
        sync._tick()
        self.assertEqual(follower.seeks, [10_000])
        self.assertEqual(follower.position(), 10_000)

    def test_a_follower_behind_is_corrected_too(self) -> None:
        sync = self._engine()
        sync.play()
        master, follower = self.made
        master.place(20_000)
        follower.place(19_000)
        follower.seeks.clear()
        sync._tick()
        self.assertEqual(follower.seeks, [20_000])

    def test_a_corrected_follower_is_left_to_settle(self) -> None:
        sync = self._engine()
        sync.play()
        master, follower = self.made
        master.place(10_000)
        follower.place(12_000)
        follower.seeks.clear()
        sync._tick()
        self.assertEqual(len(follower.seeks), 1)
        follower.place(12_000)  # still wrong, but it was only just moved
        sync._tick()
        self.assertEqual(len(follower.seeks), 1)

    def test_a_follower_that_will_not_follow_is_given_up_on_out_loud(self) -> None:
        heard: list[str] = []
        sync = self._engine()
        sync.problem.connect(heard.append)
        sync.play()
        master, follower = self.made
        master.place(10_000)
        for _ in range(engine.GIVE_UP_AFTER * (engine.SETTLE_MS // engine.TICK_MS + 1)):
            follower.place(30_000)  # nothing lands; it refuses to move
            sync._tick()
        self.assertEqual(len(heard), 1)
        self.assertIn("will not stay in step", heard[0])
        before = len(follower.seeks)
        follower.place(30_000)
        sync._tick()
        self.assertEqual(len(follower.seeks), before)  # left to run on its own

    def test_drift_is_one_number_per_screen(self) -> None:
        sync = self._engine()
        master, follower = self.made
        master.place(5_000)
        follower.place(5_033)
        self.assertEqual(sync.drift(), [0, 33])

    def test_the_end_is_reported_once(self) -> None:
        ends: list[int] = []
        sync = self._engine()
        sync.finished.connect(lambda: ends.append(1))
        sync.play()
        for player in self.made:
            player.place(45_000)
        sync._tick()
        sync._tick()
        self.assertEqual(ends, [1])
        self.assertFalse(sync.playing())
        self.assertFalse(sync._timer.isActive())


class SeekingStateTests(EngineFixture, unittest.TestCase):
    """§6's loading state: dimmed until every player has actually arrived.

    The whole point of the signal is the gap between `seek()` returning and the
    decoders catching up, so nothing here is allowed to conclude that a seek has landed
    merely because the call came back. `_check_landing` is driven by hand, exactly as
    `_tick` is in the class above, because a `QTimer` needs an event loop nobody here
    wants to spin.
    """

    def _land(self, sync: engine.SyncEngine, times: int = 1) -> None:
        for _ in range(times):
            sync._check_landing()

    def test_a_seek_dims_and_stays_dimmed_until_the_players_arrive(self) -> None:
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        # Both stubs refuse to move, so the seek is in flight and stays there.
        for player in self.made:
            player.refuse_seeks = True
        sync.seek(7_500)
        self.assertEqual(heard, [True])
        self.assertTrue(sync.seeking())
        self._land(sync)
        self.assertEqual(heard, [True])  # still nowhere near the target
        self.assertTrue(sync.seeking())

    def test_the_state_ends_once_every_player_has_landed(self) -> None:
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        follower = self.made[1]
        follower.refuse_seeks = True
        sync.seek(7_500)
        self._land(sync)
        self.assertEqual(heard, [True])  # the master is there, the follower is not
        follower.place(7_500)
        self._land(sync)
        self.assertEqual(heard, [True, False])
        self.assertFalse(sync.seeking())

    def test_landing_is_judged_by_the_measured_threshold(self) -> None:
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        follower = self.made[1]
        follower.refuse_seeks = True
        sync.seek(7_500)
        # 33 ms apart is what two pipelines on one file were measured at; demanding
        # the exact millisecond would run into the timeout on every real seek.
        follower.place(7_500 + engine.CORRECTION_MS)
        self._land(sync)
        self.assertEqual(heard, [True, False])

    def test_a_file_that_cannot_seek_is_given_up_on_rather_than_dimmed_for_ever(
        self,
    ) -> None:
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        for player in self.made:
            player.refuse_seeks = True
        sync.seek(20_000)
        checks = engine.SEEK_TIMEOUT_MS // engine.TICK_MS
        self._land(sync, checks - 1)
        self.assertEqual(heard, [True])  # still waiting, inside the timeout
        self._land(sync)
        self.assertEqual(heard, [True, False])
        self.assertFalse(sync.seeking())
        self.assertFalse(sync._landing.isActive())

    def test_a_second_seek_inside_the_first_does_not_flash(self) -> None:
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        for player in self.made:
            player.refuse_seeks = True
        sync.seek(5_000)
        sync.seek(9_000)
        sync.seek(12_000)
        self.assertEqual(heard, [True])  # one dim for a whole scrubber drag
        for player in self.made:
            player.place(12_000)
        self._land(sync)
        self.assertEqual(heard, [True, False])

    def test_a_seek_that_takes_a_badge_away_is_heard_in_the_middle_of_a_drag(
        self,
    ) -> None:
        """`seeking_changed(True)` is the only thing that clears «запись закончилась»
        in the views, and the check above has this method keeping quiet while a seek is
        in flight. A scrubber drag out of the tail of a shorter stream is seeks one
        `sliderMoved` apart, far inside `SEEK_TIMEOUT_MS`, so the badge was left over a
        stream playing its middle. Measured on the take in `scratch/twofinal` with the
        second stream slid 7000 ms: `seek(9000)` badged it, `seek(2000)` 60 ms later
        left `stream_has_ended(1)` False and `seeking_changed` having said True once.
        """
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        self.made[1].set_length(20_000)

        sync.seek(30_000)
        self.assertTrue(sync.stream_has_ended(1))
        self.assertEqual(heard, [True])

        sync.seek(5_000)  # the drag goes on, with the first seek still in flight

        self.assertFalse(sync.stream_has_ended(1))
        self.assertEqual(heard, [True, True])  # said again; never a False between

    def test_the_landing_timer_is_precise_like_the_one_that_drives_the_clock(
        self,
    ) -> None:
        # Left coarse, it ran `SEEK_TIMEOUT_MS` long by the coarse timer's own
        # lateness: 538.5, 541.9 and 544.5 ms of wall clock for 500 ms, measured on
        # 2026-11-05 against a real pair with `_has_landed` answering no. Three runs of
        # this body: 499.7, 499.8, 499.8 ms.
        sync = self._engine()
        self.assertEqual(
            sync._landing.timerType(), QtCore.Qt.TimerType.PreciseTimer
        )

    def test_the_dimming_gives_up_on_the_clock_and_not_on_a_count_of_ticks(
        self,
    ) -> None:
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        for player in self.made:
            player.refuse_seeks = True
        sync.seek(20_000)

        # Half a second of real time with one check in it, which is what a timer late
        # by a tenth, a busy main thread or a dropped check all look like from here.
        time.sleep(engine.SEEK_TIMEOUT_MS / 1000.0 + 0.05)
        sync._check_landing()

        self.assertEqual(heard, [True, False])
        self.assertFalse(sync.seeking())

    def test_the_wait_restarts_with_each_new_target(self) -> None:
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        for player in self.made:
            player.refuse_seeks = True
        checks = engine.SEEK_TIMEOUT_MS // engine.TICK_MS
        sync.seek(5_000)
        self._land(sync, checks - 1)
        sync.seek(9_000)  # a drag that is still going must not time out mid-way
        self._land(sync, checks - 1)
        self.assertEqual(heard, [True])
        self._land(sync)
        self.assertEqual(heard, [True, False])

    def test_a_stream_too_short_for_the_target_counts_as_arrived(self) -> None:
        heard: list[bool] = []
        sync = self._engine()
        self.made[1].set_length(20_000)
        sync.seeking_changed.connect(heard.append)
        sync.seek(40_000)
        # The short stream is sitting on its last frame at 20 s and will never be at
        # 40 s. Waiting out the timeout on every seek into the tail would dim the
        # picture for half a second each time for nothing.
        self.assertEqual(self.made[1].position(), 20_000)
        self._land(sync)
        self.assertEqual(heard, [True, False])

    def test_a_slid_stream_lands_on_its_own_target(self) -> None:
        heard: list[bool] = []
        sync = self._engine(offsets=[0, 2_000])
        sync.seeking_changed.connect(heard.append)
        sync.seek(7_500)
        # Both are exactly where they belong: 7 500 and 9 500. Judging the second one
        # against the bare timeline would leave the picture dimmed until the timeout
        # on every single seek of a pair the viewer has lined up.
        self._land(sync)
        self.assertEqual(heard, [True, False])

    def test_a_waiting_stream_does_not_hold_the_picture_dimmed(self) -> None:
        heard: list[bool] = []
        sync = self._engine(offsets=[0, -5_000])
        sync.seeking_changed.connect(heard.append)
        sync.seek(1_000)
        # Its target is -4 000 and it is parked at 0, which is as arrived as a stream
        # that has not started can be. §2.1 wants it black with a badge, not a timeout.
        self._land(sync)
        self.assertEqual(heard, [True, False])

    def test_stop_puts_the_picture_back(self) -> None:
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        for player in self.made:
            player.refuse_seeks = True
        sync.seek(7_500)
        sync.stop()
        self.assertEqual(heard, [True, False])
        self.assertFalse(sync.seeking())

    def test_a_one_stream_recording_never_dims(self) -> None:
        heard: list[bool] = []
        sync = self._engine(ONE_SCREEN)
        sync.seeking_changed.connect(heard.append)
        self.made[0].refuse_seeks = True
        sync.seek(7_500)
        sync.step(1)
        self.assertEqual(heard, [])
        self.assertFalse(sync.seeking())
        self.assertFalse(sync._landing.isActive())


class UnequalLengthTests(EngineFixture, unittest.TestCase):
    """§6: the transport runs to the longest stream, the shorter one holds its frame.

    The case that matters most is the one that reads like a corner and is not: the
    master is the file carrying the *sound*, so it is perfectly ordinary for it to be
    the shorter of the two, and the session must not end when it runs out.
    """

    def test_duration_is_the_longest_stream_and_each_has_its_own(self) -> None:
        sync = self._engine()
        self.made[0].set_length(45_000)
        self.made[1].set_length(60_000)
        self.assertEqual(sync.duration(), 60_000)
        self.assertEqual(sync.stream_duration(0), 45_000)
        self.assertEqual(sync.stream_duration(1), 60_000)
        self.assertEqual(sync.stream_duration(7), 0)  # not a screen of this recording

    def test_the_shorter_stream_is_announced_and_the_other_carries_on(self) -> None:
        ended: list[int] = []
        finishes: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        sync.finished.connect(lambda: finishes.append(1))
        self.made[1].set_length(20_000)
        sync.play()
        self.made[0].place(20_000)
        self.made[1].place(20_000)
        sync._tick()
        self.assertEqual(ended, [1])
        self.assertEqual(finishes, [])
        self.assertTrue(sync.playing())
        self.assertTrue(sync.stream_has_ended(1))
        self.assertFalse(sync.stream_has_ended(0))
        sync._tick()
        self.assertEqual(ended, [1])  # said once, not every tick

    def test_an_ended_stream_is_not_dragged_back_into_step(self) -> None:
        heard: list[str] = []
        sync = self._engine()
        sync.problem.connect(heard.append)
        self.made[1].set_length(20_000)
        master, short = self.made
        sync.play()
        master.place(20_000)
        short.place(20_000)
        sync._tick()
        short.seeks.clear()
        # The master runs on for another twenty seconds. Without the exemption the
        # correction would seek the ended stream past its own end on every tick and
        # then announce it as a file that will not stay in step.
        for step in range(1, 40):
            master.place(20_000 + step * 500)
            sync._tick()
        self.assertEqual(short.seeks, [])
        self.assertEqual(heard, [])

    def test_a_master_that_runs_out_first_does_not_end_the_session(self) -> None:
        ended: list[int] = []
        finishes: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        sync.finished.connect(lambda: finishes.append(1))
        self.made[0].set_length(20_000)  # the master carries the sound, not the length
        self.made[1].set_length(60_000)
        sync.play()
        self.made[0].place(20_000)
        self.made[1].place(20_000)
        self.made[0].mediaStatusChanged.emit(
            QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia
        )
        self.assertEqual(ended, [0])
        self.assertEqual(finishes, [])
        self.assertTrue(sync.playing())
        self.assertTrue(sync._timer.isActive())

    def test_finished_waits_for_the_last_stream(self) -> None:
        ended: list[int] = []
        finishes: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        sync.finished.connect(lambda: finishes.append(1))
        self.made[0].set_length(20_000)
        self.made[1].set_length(60_000)
        sync.play()
        for player in self.made:
            player.place(20_000)
        sync._tick()
        self.assertEqual(ended, [0])
        self.assertEqual(finishes, [])
        self.made[1].place(60_000)
        sync._tick()
        self.assertEqual(ended, [0])  # the last one is `finished`, not a badge
        self.assertEqual(finishes, [1])
        self.assertFalse(sync.playing())

    def test_two_equal_streams_end_together_with_no_badge_flashed(self) -> None:
        ended: list[int] = []
        finishes: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        sync.finished.connect(lambda: finishes.append(1))
        sync.play()
        for player in self.made:
            player.place(45_000)
        sync._tick()
        self.assertEqual(ended, [])
        self.assertEqual(finishes, [1])

    def test_a_pair_one_frame_apart_ends_without_a_badge_either(self) -> None:
        """The pair `test_two_equal_streams...` above is not: a recording's two screens
        are a *frame* apart, not equal. Measured on the take `scratch/twofinal` held on
        2026-11-05: 10267 ms and 10250 ms, 17 ms, one frame at 60 fps.

        A guard and not a reproduction: both ends fall inside one tick's reading of
        `_check_ends`, so this path was batched and quiet already. The two paths that
        are not batched are the check below and
        `test_a_stream_that_ends_with_the_recording_is_not_badged_before_it`, and those
        two are what `_covers_the_end` was written for.
        """
        ended: list[int] = []
        finishes: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        sync.finished.connect(lambda: finishes.append(1))
        self.made[1].set_length(45_000 - 17)
        sync.play()
        for player in self.made:
            player.place(player.duration())
        sync._tick()
        self.assertEqual(ended, [])
        self.assertEqual(finishes, [1])

    def test_the_masters_own_end_is_not_badged_over_the_last_frame_of_the_other(
        self,
    ) -> None:
        """The other way into `_note_ends`, and the one batching cannot help with:
        `_on_status` carries the master alone, so the stream beside it is unmarked
        whatever its position is. Measured on a real pair cut one frame apart: with the
        old body the master's own `EndOfMedia` badged screen 1 while the second stream
        sat on its last frame, 17 ms from the end of the recording.
        """
        ended: list[int] = []
        finishes: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        sync.finished.connect(lambda: finishes.append(1))
        self.made[1].set_length(45_000 - 17)
        sync.play()
        for player in self.made:
            player.place(player.duration())

        sync._on_status(QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia)

        self.assertEqual(ended, [])
        self.assertEqual(finishes, [])  # the other one is still to be looked at
        sync._tick()
        self.assertEqual(ended, [])
        self.assertEqual(finishes, [1])

    def test_a_stream_that_really_runs_out_early_is_badged_as_it_always_was(
        self,
    ) -> None:
        # The guard on the two above: the width is two frames, not an amnesty. The
        # take re-recorded into `scratch/twofinal` on 2026-11-05 is 10217 ms and
        # 9717 ms - half a second apart - and its second screen is badged.
        ended: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        self.made[1].set_length(44_500)
        sync.play()
        for player in self.made:
            player.place(44_500)
        sync._tick()
        self.assertEqual(ended, [1])
        self.assertTrue(sync.stream_has_ended(1))

    def test_a_stream_holding_its_last_frame_is_not_called_drifting(self) -> None:
        """`drift()` measured an ended stream against a place that has no frame in it,
        so the number grew by the length of every tick. Measured on 2026-11-05 on a
        real 10.217 s stream beside a real 4.0 s one, half a second apart each reading:
        -433, -933, -1483, -1983, -2433, -2933, -3433. `tools/verify_player.py` reads
        this list to decide whether the screens are in step, and on the two-screen take
        now in `scratch/twofinal` - whose streams are half a second apart - it failed
        on that growth, 133 ms against a threshold of 120. With the target clamped
        where `setPosition` clamps, the same run reports a worst drift of 17 ms, which
        is one frame.
        """
        sync = self._engine()
        short = self.made[1]
        short.set_length(20_000)
        sync.play()
        self.made[0].place(30_000)
        short.place(20_000)  # its last frame, where §6 says it should be
        sync._tick()

        self.assertTrue(sync.stream_has_ended(1))
        self.assertEqual(sync.drift(), [0, 0])

    def test_a_stream_qt_rewound_and_let_replay_still_reads_as_wrong(self) -> None:
        # The guard, and the fault §13.2 was: an ended stream that Qt rewound to zero
        # and started again is *not* holding its last frame, and the clamp moves the
        # target rather than the reading, so it still shows up as the seconds it is
        # out by.
        sync = self._engine()
        short = self.made[1]
        short.set_length(20_000)
        sync.play()
        self.made[0].place(30_000)
        short.place(20_000)
        sync._tick()

        short.place(1_433)  # replaying its opening seconds against a timeline at 30 s

        self.assertEqual(sync.drift(), [0, 1_433 - 20_000])

    def test_seeking_back_un_ends_a_stream(self) -> None:
        ended: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        self.made[1].set_length(20_000)
        sync.play()
        for player in self.made:
            player.place(20_000)
        sync._tick()
        self.assertEqual(ended, [1])
        sync.seek(1_000)
        self.assertFalse(sync.stream_has_ended(1))
        self.made[0].place(20_000)
        self.made[1].place(20_000)
        sync._tick()
        self.assertEqual(ended, [1, 1])  # it ends again, and is said again

    def test_a_length_not_reported_yet_is_not_an_end(self) -> None:
        ended: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        self.made[1].set_length(0)  # the decoder has not said how long it is
        sync.play()
        self.made[0].place(5_000)
        sync._tick()
        self.assertEqual(ended, [])
        self.assertFalse(sync.stream_has_ended(1))

    def test_a_one_stream_recording_says_nothing_about_ends(self) -> None:
        ended: list[int] = []
        finishes: list[int] = []
        sync = self._engine(ONE_SCREEN)
        sync.stream_ended.connect(ended.append)
        sync.finished.connect(lambda: finishes.append(1))
        self.assertEqual(sync.duration(), 45_000)
        self.assertEqual(sync.stream_duration(0), 45_000)
        sync.play()
        self.made[0].place(45_000)
        sync._tick()
        sync._tick()
        self.assertEqual(ended, [])  # there is no "others still play" for one stream
        self.assertEqual(finishes, [1])
        self.assertFalse(sync.playing())


class OffsetTests(EngineFixture, unittest.TestCase):
    """§2.1: two files that did not start together, and one line that governs it.

        position inside stream i  =  timeline position + offset_ms[i]

    The first two checks are the ones that matter most and are deliberately first: a
    recorder-made set and a plain one-video recording must behave exactly as they did
    before any of this existed - same duration, no waiting, and not one extra seek.
    """

    # -- the regression that matters most ----------------------------------

    def test_a_recorder_made_set_is_untouched_by_any_of_this(self) -> None:
        waits: list[tuple[int, bool]] = []
        sync = self._engine()
        sync.stream_waiting.connect(lambda index, on: waits.append((index, on)))

        self.assertEqual(sync.offsets(), [0, 0])
        self.assertEqual(sync.duration(), 45_000)
        # Building the engine issued no seek at all: the players are where a freshly
        # opened file sits, and the timeline's 0 is their 0.
        self.assertEqual([player.seeks for player in self.made], [[], []])

        sync.play()
        for player in self.made:
            player.place(10_000)
        sync._tick()
        self.assertEqual(sync.position(), 10_000)
        self.assertEqual([player.seeks for player in self.made], [[], []])
        self.assertEqual(waits, [])
        self.assertFalse(sync.stream_is_waiting(0))
        self.assertFalse(sync.stream_is_waiting(1))

    def test_a_one_video_recording_is_untouched_by_any_of_this(self) -> None:
        waits: list[tuple[int, bool]] = []
        sync = self._engine(ONE_SCREEN)
        sync.stream_waiting.connect(lambda index, on: waits.append((index, on)))
        self.assertEqual(sync.offsets(), [0])
        self.assertEqual(sync.duration(), 45_000)
        sync.play()
        self.made[0].place(5_000)
        sync._tick()
        self.assertEqual(sync.position(), 5_000)
        self.assertEqual(self.made[0].seeks, [])
        self.assertEqual(waits, [])

    # -- the rule ----------------------------------------------------------

    def test_the_offsets_are_read_from_the_index_and_applied_at_once(self) -> None:
        sync = self._engine(offsets=[0, 2_000])
        self.assertEqual(sync.offsets(), [0, 2_000])
        self.assertEqual(sync.offset(1), 2_000)
        # Timeline 0 is 2 s into the stream that was started earlier. A player sits at
        # its own 0 once its source is set, so somebody has to say so.
        self.assertEqual(self.made[0].position(), 0)
        self.assertEqual(self.made[1].position(), 2_000)

    def test_seeking_puts_each_stream_at_its_own_place(self) -> None:
        sync = self._engine(offsets=[0, 2_000])
        for player in self.made:
            player.seeks.clear()
        sync.seek(7_500)
        self.assertEqual(self.made[0].seeks, [7_500])
        self.assertEqual(self.made[1].seeks, [9_500])
        self.assertEqual(sync.position(), 7_500)  # the timeline, not either file

    def test_the_timeline_is_read_through_the_masters_own_offset(self) -> None:
        # §2.1 is explicit that there is no privileged stream: the file carrying the
        # sound may be the one that was slid, and then its playhead is not the clock.
        sync = self._engine(offsets=[3_000, 0])
        sync.play()
        self.made[0].place(13_000)
        self.made[1].place(10_000)
        sync._tick()
        self.assertEqual(sync.position(), 10_000)

    def test_the_correction_aims_at_the_offset_target_not_at_equality(self) -> None:
        sync = self._engine(offsets=[0, 2_000])
        sync.play()
        master, follower = self.made
        master.place(10_000)
        follower.place(12_000)  # exactly where it is meant to be
        follower.seeks.clear()
        sync._tick()
        # Aiming at equality would drag it back to 10 000 here, every tick, and undo
        # the viewer's own adjustment. That is the failure §2.1 names.
        self.assertEqual(follower.seeks, [])
        self.assertEqual(sync.drift(), [0, 0])

        follower.place(12_000 + engine.CORRECTION_MS + 1)
        sync._tick()
        self.assertEqual(follower.seeks, [12_000])

    def test_the_duration_is_what_the_offsets_leave_coverable(self) -> None:
        sync = self._engine(offsets=[0, 2_000])
        # A stream wound forward by 2 s has 2 s less to give at the end, and the other
        # one still has all of its own.
        self.assertEqual(sync.duration(), 45_000)
        self.made[0].set_length(40_000)
        self.assertEqual(sync.duration(), 43_000)
        sync.set_offset(1, -5_000)
        self.assertEqual(sync.duration(), 50_000)  # one slid late makes it longer
        self.assertEqual(sync.stream_duration(1), 45_000)  # the file's own length

    def test_a_duration_the_offsets_have_eaten_is_zero_not_negative(self) -> None:
        sync = self._engine(ONE_SCREEN, offsets=[90_000])
        self.assertEqual(sync.duration(), 0)

    def test_the_length_is_announced_when_an_offset_changes_it(self) -> None:
        lengths: list[int] = []
        sync = self._engine()
        sync.duration_changed.connect(lengths.append)
        sync._announce_duration()  # whatever the transport was told at the start
        lengths.clear()
        sync.set_offset(1, -4_000)
        self.assertEqual(lengths, [49_000])

    # -- the control, §2.2 -------------------------------------------------

    def test_setting_an_offset_while_paused_moves_only_that_stream(self) -> None:
        changed: list[int] = []
        sync = self._engine()
        sync.offsets_changed.connect(lambda: changed.append(1))
        sync.seek(10_000)
        before = sync.position()
        master_seeks = list(self.made[0].seeks)
        self.made[1].seeks.clear()

        sync.set_offset(1, 500)

        self.assertEqual(sync.position(), before)  # §2.2: not a scrub
        self.assertEqual(self.made[1].seeks, [10_500])
        self.assertEqual(self.made[0].seeks, master_seeks)
        self.assertEqual(sync.offset(1), 500)
        self.assertEqual(changed, [1])

    def test_setting_an_offset_while_playing_stops_nothing(self) -> None:
        sync = self._engine()
        sync.play()
        for player in self.made:
            player.place(10_000)
        sync._tick()
        before = sync.position()
        for player in self.made:
            player.calls.clear()
            player.seeks.clear()

        sync.set_offset(1, -1_500)

        self.assertEqual(sync.position(), before)
        self.assertEqual(self.made[1].seeks, [8_500])
        self.assertEqual(self.made[1].calls, ["play"])  # applied without a pause
        self.assertEqual(self.made[0].calls, [])  # the other one never heard about it
        self.assertTrue(sync.playing())

    def test_sliding_the_master_does_not_move_the_timeline(self) -> None:
        sync = self._engine()
        sync.seek(10_000)
        sync.set_offset(0, 2_000)
        self.assertEqual(sync.position(), 10_000)
        self.assertEqual(self.made[0].seeks[-1], 12_000)

    def test_an_offset_does_not_dim_the_picture(self) -> None:
        # `seeking_changed` dims every view. Doing that on each press of "+1 frame"
        # is the visible restart §2.2 says this adjustment must not cause.
        heard: list[bool] = []
        sync = self._engine()
        sync.seeking_changed.connect(heard.append)
        sync.set_offset(1, 250)
        self.assertEqual(heard, [])
        self.assertFalse(sync.seeking())

    def test_setting_the_offset_it_already_has_costs_nothing(self) -> None:
        changed: list[int] = []
        sync = self._engine(offsets=[0, 2_000])
        sync.offsets_changed.connect(lambda: changed.append(1))
        for player in self.made:
            player.seeks.clear()
        sync.set_offset(1, 2_000)
        self.assertEqual(self.made[1].seeks, [])
        self.assertEqual(changed, [])

    def test_resetting_to_zero_puts_it_back_in_step(self) -> None:
        sync = self._engine(offsets=[0, 2_000])
        sync.seek(10_000)
        sync.set_offset(1, 0)
        self.assertEqual(sync.offsets(), [0, 0])
        self.assertEqual(self.made[1].seeks[-1], 10_000)
        self.assertEqual(sync.position(), 10_000)

    def test_a_screen_that_is_not_there_is_said_rather_than_ignored(self) -> None:
        heard: list[str] = []
        sync = self._engine()
        sync.problem.connect(heard.append)
        sync.set_offset(7, 100)
        sync.set_offset(1, "soon")
        self.assertEqual(len(heard), 2)
        self.assertEqual(sync.offsets(), [0, 0])

    # -- before a stream has started, §2.1 ---------------------------------

    def test_a_stream_the_timeline_has_not_reached_is_waiting(self) -> None:
        sync = self._engine(offsets=[0, -5_000])
        self.assertTrue(sync.stream_is_waiting(1))
        self.assertFalse(sync.stream_is_waiting(0))
        # Held at its first frame and *paused*: left running it would play its opening
        # seconds early and then be yanked back by the correction.
        self.assertEqual(self.made[1].position(), 0)
        self.assertEqual(self.made[1].state, "paused")

    def test_the_waiting_state_is_announced_as_the_timeline_crosses_it(self) -> None:
        waits: list[tuple[int, bool]] = []
        sync = self._engine(offsets=[0, -5_000])
        sync.stream_waiting.connect(lambda index, on: waits.append((index, on)))

        sync.seek(6_000)
        self.assertEqual(waits, [(1, False)])
        self.assertEqual(self.made[1].seeks[-1], 1_000)

        sync.seek(1_000)
        self.assertEqual(waits, [(1, False), (1, True)])
        self.assertEqual(self.made[1].seeks[-1], 0)  # parked, never a negative position

    def test_a_waiting_stream_is_not_played_and_not_corrected(self) -> None:
        heard: list[str] = []
        sync = self._engine(offsets=[0, -5_000])
        sync.problem.connect(heard.append)
        sync.play()
        self.assertEqual(self.made[0].state, "playing")
        self.assertEqual(self.made[1].state, "paused")

        self.made[1].seeks.clear()
        for elapsed in range(500, 5_000, 500):
            self.made[0].place(elapsed)
            sync._tick()
        self.assertEqual(self.made[1].seeks, [])
        self.assertEqual(heard, [])  # never complained about as a file out of step

    def test_a_waiting_stream_starts_the_moment_the_timeline_reaches_it(self) -> None:
        waits: list[tuple[int, bool]] = []
        sync = self._engine(offsets=[0, -5_000])
        sync.stream_waiting.connect(lambda index, on: waits.append((index, on)))
        sync.play()
        self.made[1].seeks.clear()

        self.made[0].place(6_000)
        sync._tick()

        self.assertEqual(waits, [(1, False)])
        self.assertEqual(self.made[1].seeks, [1_000])
        self.assertEqual(self.made[1].state, "playing")
        self.assertFalse(sync.stream_is_waiting(1))

    def test_the_clock_keeps_time_while_every_stream_still_waits(self) -> None:
        heard: list[str] = []
        # A lead-in longer than the stall warning, so that the warning is given the
        # chance to fire and must not take it.
        sync = self._engine(offsets=[-5_000, -6_000])
        sync.problem.connect(heard.append)
        sync.play()
        self.assertEqual(sync.position(), 0)

        ticks = engine.STALL_WARNING_MS // engine.TICK_MS + 5
        for _ in range(ticks):
            sync._tick()
        self.assertTrue(sync.stream_is_waiting(0))  # still nothing to show
        # Nothing has a frame to show yet, so no playhead can be read; the lead-in is
        # still time passing, and the master is not stalled - it has not started.
        self.assertGreaterEqual(sync.position(), engine.STALL_WARNING_MS)
        self.assertEqual(heard, [])

    def test_the_clock_goes_back_to_a_real_playhead_once_one_starts(self) -> None:
        sync = self._engine(offsets=[-2_000, -3_000])
        sync.play()
        for _ in range(20):
            sync._tick()
        self.assertEqual(sync.position(), 2_000)
        self.assertFalse(sync.stream_is_waiting(0))
        self.assertTrue(sync.stream_is_waiting(1))
        # From here the master's own playhead is the clock again, counted by nobody.
        self.made[0].place(1_500)
        self.assertEqual(sync.position(), 3_500)

    def test_stop_puts_a_slid_pair_back_on_the_same_instant(self) -> None:
        sync = self._engine(offsets=[0, 2_000])
        sync.play()
        for player in self.made:
            player.place(20_000)
        sync._tick()
        sync.stop()
        self.assertEqual(sync.position(), 0)
        self.assertEqual(self.made[0].position(), 0)
        self.assertEqual(self.made[1].position(), 2_000)

    # -- §2.2's invariant where it used to break ---------------------------

    def test_sliding_a_stream_past_its_own_end_does_not_move_the_timeline(self) -> None:
        """§2.2: "The adjustment must **not** move the timeline position."

        The place it broke is the tail. `setPosition` clamps at the file's own
        duration - the stub does it at the same line a real `QMediaPlayer` does - so
        the stream lands short of where it was asked for while the whole offset is
        still subtracted from it, and the timeline reads back short by the overshoot.
        The scrubber walked *backwards* while the viewer nudged one video forwards.
        """
        sync = self._engine()
        sync.seek(44_900)
        before = sync.position()

        for nudge in (100, 200, 300):
            sync.set_offset(0, nudge)
            self.assertEqual(sync.position(), before, f"+{nudge} ms moved the timeline")
        self.assertEqual(self.made[0].position(), 45_000)  # clamped, as a real one is

        sync.set_offset(0, 5_000)  # a typed value: the jump used to be the whole 5 s
        self.assertEqual(sync.position(), before)

        sync.set_offset(0, 0)  # «Сбросить» gives the time back, not a different time
        self.assertEqual(sync.position(), before)

    def test_an_offset_further_than_two_files_can_be_out_is_clamped(self) -> None:
        heard: list[str] = []
        sync = self._engine()
        sync.problem.connect(heard.append)

        sync.set_offset(1, 10 ** 19)

        # Past a qint64 this used to escape `_call(player, 'setPosition', ...)`, which
        # guards nothing on purpose, as an OverflowError out of a public method.
        self.assertEqual(sync.offset(1), engine.OFFSET_LIMIT_MS)
        self.assertEqual(len(heard), 1)
        self.assertIn("3600000", heard[0])

    def test_an_offset_of_infinity_is_a_sentence_and_not_an_exception(self) -> None:
        heard: list[str] = []
        sync = self._engine()
        sync.problem.connect(heard.append)

        # `int(float('inf'))` raises OverflowError, which was not in the tuple that
        # turns 'abc', None, [1] and nan into a sentence.
        sync.set_offset(1, float("inf"))

        self.assertEqual(sync.offsets(), [0, 0])
        self.assertEqual(len(heard), 1)

    def test_an_index_that_names_an_impossible_offset_still_opens(self) -> None:
        """An `offset_ms` of 1e20 used to be a window that never opened at all.

        It reaches Qt as a qint64 position out of `__init__`'s own `_place_positions`,
        and leaves again inside `duration()`, which a transport hands to a scrubber's
        int32 `setRange`. Both were an `OverflowError` in front of a viewer, and the
        second one left the half-built transport's poll timer printing for ever.
        """
        sync = self._engine(offsets=[0, 10 ** 20])
        self.assertEqual(sync.offsets(), [0, engine.OFFSET_LIMIT_MS])
        self.assertLess(sync.duration(), 2 ** 31)
        self.assertGreaterEqual(sync.duration(), 0)


class ClockReadingTests(EngineFixture, unittest.TestCase):
    """Which playhead the timeline may be read off, and what happens when none will do.

    `setPosition` clamps at a file's own duration, so a stream that has run out - or one
    the viewer has slid past its own end - reports a constant for ever. The timeline was
    read off the first stream that was not *waiting*, master first, with no equivalent
    skip at the far end: so the clock froze the moment the master ran out, although
    `duration()` correctly promised more, and `_correct` then dragged the healthy stream
    back to the dead instant until it gave up and blamed the file.

    The guard on the fix is the last test here: a stream that covers the end of the
    timeline is never skipped, which is what leaves the one-video case and the ordinary
    recorder-made pair reading exactly as they did.
    """

    def test_a_short_master_does_not_freeze_the_timeline(self) -> None:
        # The master is the file carrying the *sound*, so it being the shorter of the
        # two is ordinary rather than a corner case - `_on_status`'s own comment says
        # so - and `PLAYER_LAYOUTS.md` §6 says the other stream carries on.
        sync = self._engine()
        self.made[0].set_length(20_000)
        sync.play()
        self.made[0].place(20_000)  # the master is at its own last frame and stays there

        for moment in (20_500, 30_000, 44_000):
            self.made[1].place(moment)
            sync._tick()
            self.assertEqual(sync.position(), moment)
        self.assertEqual(sync.duration(), 45_000)

    def test_a_healthy_follower_is_not_dragged_back_to_an_ended_master(self) -> None:
        # The complaint this used to produce - "will not stay in step … it is being
        # left to run on its own" - was about a file that was behaving perfectly. The
        # clock was dead, and `_master_advancing` latches True for the session.
        heard: list[str] = []
        sync = self._engine()
        sync.problem.connect(heard.append)
        self.made[0].set_length(20_000)
        sync.play()
        for player in self.made:
            player.place(10_000)
        sync._tick()  # the clock has moved, so the correction is armed

        self.made[0].place(20_000)
        self.made[1].seeks.clear()
        for step in range(1, 40):
            self.made[1].place(20_000 + step * 500)
            sync._tick()

        self.assertEqual(self.made[1].seeks, [])
        self.assertEqual(heard, [])

    def test_the_clock_reaches_the_end_an_offset_made_reachable(self) -> None:
        # Two cameras of one length and one slid late: §2.1's duration is longer than
        # either file, so the master runs out with timeline left to cover. This is the
        # case the two-camera feature exists for, and it froze two seconds early.
        sync = self._engine(offsets=[0, -2_000])
        self.assertEqual(sync.duration(), 47_000)
        sync.seek(40_000)
        sync.play()
        self.made[0].place(45_000)  # the master is at its own last frame

        for raw in (43_000, 44_000, 45_000):
            self.made[1].place(raw)
            sync._tick()
            self.assertEqual(sync.position(), raw + 2_000)

    def test_a_lead_in_longer_than_the_other_stream_still_keeps_time(self) -> None:
        # A 10 s clip beside a 45 s one slid a minute late - inside the panel's own
        # clamp. Nothing could tell the time: one stream was waiting and the other was
        # parked on its last frame, and `_all_waiting()` saw only the first of those.
        sync = self._engine()
        self.made[0].set_length(10_000)
        sync.set_offset(1, -60_000)
        self.assertEqual(sync.duration(), 105_000)
        sync.play()
        self.made[0].place(10_000)

        for _ in range(30):
            sync._tick()

        self.assertEqual(sync.position(), 3_000)
        self.assertTrue(sync.stream_is_waiting(1))

    def test_the_stream_that_covers_the_end_drives_the_clock_to_its_last_frame(
        self,
    ) -> None:
        # The guard on all of the above. A stream whose own length reaches `duration()`
        # is the longest thing in the recording and its last frame *is* the timeline's,
        # so it is never skipped however close to its end it sits. Skipping it would
        # make every one-video recording freeze in its final 80 ms.
        sync = self._engine(ONE_SCREEN)
        sync.play()
        for moment in (44_000, 44_950, 45_000):
            self.made[0].place(moment)
            sync._tick()
            self.assertEqual(sync.position(), moment)


class AudioSourceTests(EngineFixture, unittest.TestCase):
    """§1.2: which file the sound comes from, and what moving it may not cost.

    Two cameras both recorded sound and one of them recorded it better, so the choice
    is the viewer's. One output, *moved* rather than multiplied - §5 of
    `PLAYER_LAYOUTS.md` is unchanged and two copies of one voice must never reach the
    speakers - and §1.2's own line that this control and the picture swap are two
    questions with two controls: neither may do the other's work.
    """

    def test_the_default_is_the_stream_the_sidecar_names(self) -> None:
        sync = self._engine(audio="Take_screen1.mp4")
        self.assertEqual(sync.audio_index(), 1)
        self.assertIs(self.made[1].audio_output, self.audio)
        self.assertIsNone(self.made[0].audio_output)

    def test_moving_the_sound_moves_exactly_one_output(self) -> None:
        said: list[int] = []
        sync = self._engine()
        sync.audio_changed.connect(said.append)

        sync.set_audio_source(1)

        self.assertIsNone(self.made[0].audio_output)
        self.assertIs(self.made[1].audio_output, self.audio)
        self.assertEqual(sync.audio_index(), 1)
        self.assertEqual(said, [1])

    def test_moving_the_sound_does_not_move_the_picture(self) -> None:
        sync = self._engine(offsets=[0, 2_000])
        sync.seek(10_000)
        before = sync.position()
        seeks = [list(player.seeks) for player in self.made]

        sync.set_audio_source(1)

        self.assertEqual(sync.position(), before)
        self.assertEqual([list(p.seeks) for p in self.made], seeks)
        self.assertEqual(sync.offsets(), [0, 2_000])

    def test_the_stream_carrying_the_sound_is_never_the_one_corrected(self) -> None:
        # The clock goes with the sound, for the reason the module docstring opens
        # with: a follower is the stream the correction is allowed to seek, and a seek
        # in the audio track is the one seam a listener hears.
        sync = self._engine()
        sync.set_audio_source(1)
        sync.play()
        self.made[1].place(10_000)
        self.made[0].place(10_000 + engine.CORRECTION_MS + 1)
        for player in self.made:
            player.seeks.clear()

        sync._tick()

        self.assertEqual(self.made[1].seeks, [])
        self.assertEqual(self.made[0].seeks, [10_000])

    def test_silencing_leaves_the_recording_where_it_was(self) -> None:
        said: list[int] = []
        sync = self._engine()
        sync.audio_changed.connect(said.append)
        sync.seek(5_000)

        sync.set_audio_source(-1)

        self.assertEqual(sync.audio_index(), -1)
        self.assertIsNone(self.made[0].audio_output)
        self.assertIsNone(self.made[1].audio_output)
        self.assertEqual(sync.position(), 5_000)
        self.assertEqual(said, [-1])

    def test_the_sound_comes_back_with_the_volume_it_had(self) -> None:
        # One output for the engine's life, handed from player to player: the viewer's
        # volume and mute survive a trip through «Выключен» rather than being reset to
        # a device default in the middle of a sentence.
        sync = self._engine()
        sync.set_volume(0.25)
        sync.set_muted(True)

        sync.set_audio_source(-1)
        sync.set_audio_source(1)

        self.assertIs(self.made[1].audio_output, self.audio)
        self.assertEqual(self.audio.volume, 0.25)
        self.assertTrue(self.audio.muted)

    def test_asking_for_the_source_it_already_has_costs_nothing(self) -> None:
        said: list[int] = []
        sync = self._engine()
        sync.audio_changed.connect(said.append)

        sync.set_audio_source(0)

        self.assertEqual(said, [])
        self.assertIs(self.made[0].audio_output, self.audio)

    def test_a_screen_this_recording_has_not_got_is_said_not_ignored(self) -> None:
        heard: list[str] = []
        sync = self._engine()
        sync.problem.connect(heard.append)

        sync.set_audio_source(7)
        sync.set_audio_source("screen 2")

        self.assertEqual(len(heard), 2)
        self.assertEqual(sync.audio_index(), 0)
        self.assertIs(self.made[0].audio_output, self.audio)

    def test_a_one_video_recording_can_still_be_silenced_and_heard_again(self) -> None:
        sync = self._engine(ONE_SCREEN)
        self.assertEqual(sync.audio_index(), 0)
        sync.set_audio_source(-1)
        self.assertIsNone(self.made[0].audio_output)
        sync.set_audio_source(0)
        self.assertIs(self.made[0].audio_output, self.audio)


class PlayingAtTheEndTests(EngineFixture, unittest.TestCase):
    """What the transport does once the timeline has run past the files.

    Everything here is about one Qt fact and one decision.

    The fact, measured on 2026-11-05 and modelled by `EndOfFilePlayer`: a player that
    has reached the end of its file is *stopped* there, `pause()` on it rewinds it to
    zero, and `play()` on it decodes nothing and answers `EndOfMedia`.

    The decision, which `SyncEngine.play`'s docstring argues: **pressing play at the
    very end starts the recording again.** The code always said so and only did it when
    `_finished` happened to be set, which is the difference between playing to the end
    and scrubbing to it - two ways of being on the same frame.
    """

    def setUp(self) -> None:
        super().setUp()
        self.player_class = EndOfFilePlayer

    # -- §13.1: press play on the last frame -------------------------------

    def test_play_on_a_scrubbed_last_frame_starts_again_rather_than_nothing(
        self,
    ) -> None:
        """Measured against `scratch/twofinal` before the fix: scrubbed to 10267 of
        10267 and played, the position stayed at 10267, `playing()` came back False
        and nothing was decoded. A second press was needed before it would start.
        """
        sync = self._engine()
        sync.seek(sync.duration())
        self.assertEqual(sync.position(), 45_000)
        for player in self.made:
            player.calls.clear()

        sync.play()
        sync._tick()  # the turn of the loop on which Qt's EndOfMedia used to arrive

        self.assertTrue(sync.playing())
        self.assertEqual(sync.position(), 0)
        for index, player in enumerate(self.made):
            with self.subTest(index):
                self.assertEqual(player.state, "playing")
                self.assertEqual(player.position(), 0)

    def test_the_two_ways_of_reaching_the_end_restart_the_same_way(self) -> None:
        # The guard on the one above: playing to the end already restarted, and the
        # whole complaint was that the other way of being on the last frame did not.
        sync = self._engine()
        sync.play()
        for player in self.made:
            player.run_to_the_end()
        sync._tick()
        self.assertFalse(sync.playing())

        sync.play()
        sync._tick()
        self.assertTrue(sync.playing())
        self.assertEqual(sync.position(), 0)

    def test_a_recording_that_still_has_frames_left_is_not_restarted(self) -> None:
        # The other guard: pressing play in the middle resumes, it does not rewind.
        sync = self._engine()
        sync.seek(20_000)
        sync.play()
        self.assertEqual(sync.position(), 20_000)
        self.assertTrue(sync.playing())

    def test_play_a_few_frames_from_the_end_plays_them_instead_of_rewinding(
        self,
    ) -> None:
        """"The very end" was 80 ms wide, which is five frames at 60 fps. Measured on
        2026-11-05 against the two-screen take in `scratch/twofinal`, 10217 ms long,
        paused at each spot and then played for 400 ms, old body then this one:

            seek(9917)  -> 10217 | 10217     seek(10137) -> 383 | 10217
            seek(10200) ->   383 | 10217     seek(10217) -> 383 |   383

        so the last line, §13.1's restart, is the only one that should rewind.
        """
        sync = self._engine()
        sync.seek(45_000 - 50)
        for player in self.made:
            player.calls.clear()

        sync.play()

        self.assertTrue(sync.playing())
        self.assertEqual(sync.position(), 44_950)
        for index, player in enumerate(self.made):
            with self.subTest(index):
                self.assertEqual(player.state, "playing")

    def test_a_frame_step_back_from_the_end_then_play_does_not_rewind(self) -> None:
        # The same fault by the route a viewer most often takes to it: «-1 кадр» from
        # the last frame and then play. Measured on the same take: 333 before, 10217
        # after.
        sync = self._engine()
        sync.seek(sync.duration())
        sync.step(-1)
        self.assertEqual(sync.position(), 45_000 - 17)

        sync.play()

        self.assertTrue(sync.playing())
        self.assertEqual(sync.position(), 45_000 - 17)

    def test_the_last_fifty_milliseconds_of_a_shorter_stream_are_played(self) -> None:
        # The same width from `_resume_streams`' side: a stream with 50 ms left used
        # to be skipped as "nothing there to start". Measured on a real 10.217 s
        # stream beside a real 4.0 s one: play from 3950 ran the timeline on to 4550
        # with both streams started.
        sync = self._engine()
        self.made[1].set_length(20_000)
        sync.seek(19_950)
        for player in self.made:
            player.calls.clear()

        sync.play()

        self.assertIn("play", self.made[1].calls)

    # -- §13.2: pause while one stream has already ended --------------------

    def _one_stream_already_ended(self) -> tuple[engine.SyncEngine, StubPlayer, StubPlayer]:
        """Play into the tail of an unequal pair: 30 s of timeline, a 20 s follower."""
        sync = self._engine()
        master, short = self.made
        short.set_length(20_000)
        sync.play()
        master.place(30_000)
        short.run_to_the_end()
        sync._tick()
        assert sync.stream_has_ended(1)
        return sync, master, short

    def test_pausing_does_not_rewind_a_stream_that_has_already_ended(self) -> None:
        """Measured before the fix with a real 10 s and a real 4 s clip: paused at
        7466 ms, the short stream went 4000 -> 0.
        """
        sync, _master, short = self._one_stream_already_ended()
        sync.pause()
        self.assertEqual(short.position(), 20_000)  # its last frame, where it was

    def test_resuming_does_not_replay_an_ended_stream_against_the_timeline(self) -> None:
        """And the half that the viewer actually sees. Measured before the fix: 1.5 s
        after resuming, the short stream was at 1433 ms against a timeline of 8933 -
        `drift()` -7500 ms, the wrong seconds of the right file, and nothing said.
        """
        sync, master, short = self._one_stream_already_ended()
        sync.pause()
        short.calls.clear()

        sync.play()

        self.assertNotIn("play", short.calls)  # there is no frame there to start
        self.assertEqual(short.position(), 20_000)
        self.assertEqual(master.state, "playing")
        self.assertTrue(sync.playing())

    def test_an_ended_stream_is_said_once_across_a_pause_and_a_resume(self) -> None:
        ended: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        master, short = self.made
        short.set_length(20_000)
        sync.play()
        master.place(30_000)
        short.run_to_the_end()
        sync._tick()
        self.assertEqual(ended, [1])

        sync.pause()
        sync.play()
        master.place(31_000)
        sync._tick()

        self.assertEqual(ended, [1])  # told once, not again on every resume

    def test_a_stream_still_running_is_paused_as_it_always_was(self) -> None:
        # `_park` leaves alone only what Qt has already stopped. Everything else is
        # paused exactly as before, or pause would stop pausing.
        sync = self._engine()
        sync.play()
        for player in self.made:
            player.place(10_000)
        sync._tick()
        sync.pause()
        for index, player in enumerate(self.made):
            with self.subTest(index):
                self.assertEqual(player.state, "paused")
                self.assertEqual(player.position(), 10_000)


class BadgingWhilePausedTests(EngineFixture, unittest.TestCase):
    """§13.9's first: the badge on a stream scrubbed into the tail of.

    `_check_ends` is driven by `_tick`, and `_tick` does not run while playback does
    not - so a scrub made while paused left an ended stream with no badge at all,
    although the mirror-image case, scrubbing back into the lead-in of a stream slid
    late, is badged on the same scrub by `_update_waiting`.
    """

    def test_scrubbing_into_the_tail_of_a_shorter_stream_badges_it(self) -> None:
        ended: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        self.made[1].set_length(20_000)

        sync.seek(30_000)  # paused, never played

        self.assertEqual(ended, [1])
        self.assertTrue(sync.stream_has_ended(1))
        self.assertFalse(sync.stream_has_ended(0))

    def test_the_mirror_image_case_is_still_badged_the_same_way(self) -> None:
        # The comparison the complaint was made against: this half always worked.
        waits: list[tuple[int, bool]] = []
        sync = self._engine(offsets=[0, -5_000])
        sync.stream_waiting.connect(lambda index, on: waits.append((index, on)))
        sync.seek(6_000)
        sync.seek(1_000)
        self.assertEqual(waits, [(1, False), (1, True)])
        self.assertTrue(sync.stream_is_waiting(1))

    def test_scrubbing_back_out_of_the_tail_takes_the_badge_away(self) -> None:
        ended: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        self.made[1].set_length(20_000)
        sync.seek(30_000)
        self.assertEqual(ended, [1])

        sync.seek(5_000)

        self.assertFalse(sync.stream_has_ended(1))

    def test_the_last_frames_of_a_shorter_stream_are_still_frames(self) -> None:
        """How wide "the tail" is. It used to be `CORRECTION_MS`, which is a sync
        distance and not a width at the end of a file, so the last 80 ms of a stream -
        five frames at 60 fps - were badged as gone while they were still there.
        Measured on a real 10.217 s stream beside a real 4.0 s one: a paused
        `seek(3990)` says nothing on this body, `seek(4000)` badges.
        """
        ended: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        self.made[1].set_length(20_000)

        sync.seek(19_950)  # 50 ms of that stream still to show

        self.assertEqual(ended, [])
        self.assertFalse(sync.stream_has_ended(1))

        sync.seek(20_000)  # and now its last frame is behind us

        self.assertEqual(ended, [1])
        self.assertTrue(sync.stream_has_ended(1))

    def test_a_stream_that_ends_with_the_recording_is_not_badged_before_it(
        self,
    ) -> None:
        # The scrub into the one frame by which a recording's two screens differ.
        # The engine marks it - the correction must leave it alone - and says nothing,
        # because the news for a stream that ran out *with* the recording is
        # `finished`. `_covers_the_end` is the test and `stream_has_ended` asks it too,
        # so a pane built a moment later does not badge it either.
        ended: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        self.made[1].set_length(45_000 - 17)

        sync.seek(44_990)

        self.assertEqual(ended, [])
        self.assertFalse(sync.stream_has_ended(1))

    def test_a_scrub_past_every_stream_badges_none_of_them(self) -> None:
        # `stream_ended` is defined as a stream that ran out while at least one other
        # is still playing; the end of them all is `finished`, and a scrub made while
        # paused is not that.
        ended: list[int] = []
        finishes: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        sync.finished.connect(lambda: finishes.append(1))

        sync.seek(45_000)

        self.assertEqual(ended, [])
        self.assertEqual(finishes, [])
        self.assertFalse(sync.stream_has_ended(0))

    def test_a_length_not_reported_yet_is_not_a_badge_either(self) -> None:
        ended: list[int] = []
        sync = self._engine()
        sync.stream_ended.connect(ended.append)
        self.made[1].set_length(0)  # the decoder has not said how long it is
        sync.seek(30_000)
        self.assertEqual(ended, [])


class TimelineCountingTests(EngineFixture, unittest.TestCase):
    """§13.9's second: the lead-in before a negative offset ran about 10% long.

    While no stream can tell the time - a lead-in made by sliding every file late, or a
    tail where everything has run out - `_tick` counts the timeline itself. It used to
    add `TICK_MS` per tick whatever the real gap was, and Qt's default timer type is
    coarse.

    Measured on 2026-11-05 under the offscreen platform with a real event loop, 3 s a
    run: a coarse 100 ms timer fires every **109.45 ms** on average (26 gaps, 105.74
    min, 122.73 max); a precise one every **99.99 ms** (29 gaps, 99.04 min, 101.25
    max). Driving the real engine over a real clip with a -5000 ms offset, the black
    run-up took **5.456, 5.471 and 5.479 s** before the fix and **5.000, 5.001 and
    5.001 s** after it.
    """

    def test_the_tick_timer_is_precise_because_a_coarse_one_runs_late(self) -> None:
        sync = self._engine()
        self.assertEqual(
            sync._timer.timerType(), QtCore.Qt.TimerType.PreciseTimer
        )

    def test_a_late_tick_advances_the_timeline_by_the_time_that_really_passed(
        self,
    ) -> None:
        sync = self._engine(offsets=[-5_000, -6_000])
        sync.play()
        self.assertEqual(sync.position(), 0)

        # A quarter of a second with no tick in it, which is what a coarse timer, a
        # busy main thread or a dropped tick all look like from here.
        time.sleep(0.25)
        sync._tick()

        # 245 and not 200: `time.sleep` does not return early, so anything short of
        # the quarter second is the tick counting its own interval instead of the time
        # that passed - which is the whole of what this fix is. The upper bound is
        # loose on purpose: a sleep can overrun on a busy machine, and over-counting
        # by a tick is a different fault with its own check.
        self.assertGreaterEqual(sync.position(), 245)
        self.assertLess(sync.position(), 1_000)

    def test_a_tick_called_by_hand_still_counts_one_interval(self) -> None:
        # The guard, and its reason had to be corrected on 2026-11-05: a precise timer
        # *does* fire early - two 3 s runs gave 15 of 28 and 15 of 29 gaps under 100 ms,
        # min 98.65 - it simply never fires much early, while a tick nobody's timer
        # sent comes back 0 ms later (1000 back-to-back reads of a `QElapsedTimer`,
        # min 0, max 0). That gap is what the floor rests on, and it is how this file
        # drives the engine.
        sync = self._engine(offsets=[-5_000, -6_000])
        sync.play()
        for _ in range(10):
            sync._tick()
        self.assertEqual(sync.position(), 10 * engine.TICK_MS)

    def test_a_stalled_clock_is_counted_in_real_time_and_not_in_ticks(self) -> None:
        # The counter behind «не воспроизводится: не двигается N с», which is quoted to
        # the viewer in seconds. Counting ticks of a timer late by a tenth would have
        # said "3 s" after 3.3 s of a frozen picture.
        sync = self._engine()
        sync.play()  # nothing moves the stubs, so the master is stuck at 0

        time.sleep(0.25)
        sync._tick()

        self.assertGreaterEqual(sync._stalled_ms, 200)

    def test_a_settling_follower_waits_half_a_second_and_not_five_ticks(self) -> None:
        # `SETTLE_MS` is half a second of the viewer's time: the stretch a player is
        # left alone after being pulled into line. It was counted in ticks too.
        sync = self._engine()
        master, follower = self.made
        sync.play()
        master.place(10_000)
        follower.place(10_000 - 3 * engine.CORRECTION_MS)
        sync._tick()
        self.assertEqual(follower.seeks[-1], 10_000)  # pulled into line, now settling

        follower.refuse_seeks = True  # and it stays out of step
        follower.seeks.clear()
        master.place(11_000)
        time.sleep(engine.SETTLE_MS / 1000.0 + 0.05)
        sync._tick()  # this one spends the settle: more than SETTLE_MS has passed
        sync._tick()  # so this one is free to correct again

        self.assertEqual(follower.seeks, [11_000])


class LengthAnnouncedTests(EngineFixture, unittest.TestCase):
    """§1.1's other half: a clock that is opened and not played still has a length.

    `setSource` is asynchronous and a decoder reports its length a few hundred
    milliseconds later, on `durationChanged`. `_announce_duration` was reached from
    `_tick` and from `set_offset` and from nowhere else, and `_tick` runs only while
    playback does - so a player opened paused never said how long its recording was.
    Measured on 2026-11-05 against the take in `scratch/twofinal`: a freshly built
    engine left paused for 3 s emitted `duration_changed` not once, while `duration()`
    answered 10217 the whole time.

    What hangs on it is the position carried into a newly opened player: it is applied
    once at once and once on the first length the new clock announces, because the
    first `setPosition` can be dropped while the source is still being read. Opened
    from a paused player, that second attempt never came and the new window came up at
    00:00.
    """

    def setUp(self) -> None:
        super().setUp()
        self.player_class = LoadingPlayer

    def test_a_clock_that_is_never_played_still_announces_its_length(self) -> None:
        said: list[int] = []
        sync = self._engine()
        sync.duration_changed.connect(said.append)
        self.assertEqual(sync.duration(), 0)  # nothing has been decoded yet

        for player in self.made:
            player.report_length(30_000)

        self.assertEqual(said, [30_000])
        self.assertFalse(sync.playing())

    def test_the_length_is_said_once_however_many_streams_report_it(self) -> None:
        said: list[int] = []
        sync = self._engine()
        sync.duration_changed.connect(said.append)
        self.made[0].report_length(30_000)
        self.made[1].report_length(20_000)  # shorter: the recording is no longer
        self.assertEqual(said, [30_000])
        self.made[1].report_length(40_000)  # longer: and now it is
        self.assertEqual(said, [30_000, 40_000])

    def test_a_player_without_the_signal_is_not_an_error(self) -> None:
        # The stub the rest of this file uses has no `durationChanged`, exactly as a
        # Qt build missing something would not - and it still opens and still plays.
        self.player_class = StubPlayer
        sync = self._engine()
        self.assertEqual(sync.duration(), 45_000)
        sync.play()
        self.assertTrue(sync.playing())

    def test_a_seek_made_before_the_file_was_read_is_not_lost(self) -> None:
        """§1.1 again, and the half of it the carry cannot do for itself: a
        `setPosition` made while Qt is still opening the file is dropped, so the
        viewer's place was simply gone. Measured on 2026-11-05 against
        `scratch/twofinal`: `seek(5000)` on an engine built a moment earlier left the
        recording at 0, three runs of three, and still 0 after 3 s.
        """
        sync = self._engine()
        sync.seek(5_000)
        self.assertEqual(sync.position(), 0)  # Qt drops it; the engine knows better

        for player in self.made:
            player.finish_loading()

        self.assertEqual(sync.position(), 5_000)
        for index, player in enumerate(self.made):
            with self.subTest(index):
                self.assertEqual(player.position(), 5_000)

    def test_the_offsets_of_a_slid_set_survive_the_opening_too(self) -> None:
        # The same drop on the set that applies a position before anyone has touched
        # it: `__init__` puts a slid pair in its places, and those seeks were dropped
        # as well.
        sync = self._engine(offsets=[0, 3_000])
        for player in self.made:
            player.finish_loading()
        self.assertEqual(self.made[0].position(), 0)
        self.assertEqual(self.made[1].position(), 3_000)

    def test_a_recording_nobody_has_moved_is_not_seeked_by_any_of_this(self) -> None:
        # The guard: the common case must cost exactly the seeks it costs today, and
        # that number is none.
        sync = self._engine()
        for player in self.made:
            player.seeks.clear()
            player.finish_loading()
        self.assertEqual([player.seeks for player in self.made], [[], []])
        self.assertEqual(sync.position(), 0)


class EmptyTimelineTests(EngineFixture, unittest.TestCase):
    """§13.9's third: every stream slid past the end leaves a timeline 0 ms long.

    `duration()` is `max(0, max_i(length_i - offset_ms[i]))`, so an offset larger than
    a stream's own length takes that stream's whole contribution away. When every
    stream's does there is no instant of the timeline with a frame on it. Measured on
    2026-11-05 with a real 10 s and a real 4 s clip, both given an offset of 20 000 ms:
    `duration()` 0, `seek(5000)` accepted, `position()` then 5000 on a timeline 0 ms
    long, and pressing play started both files from their own zero.
    """

    def test_a_timeline_the_offsets_have_emptied_takes_no_seek_elsewhere(self) -> None:
        sync = self._engine(ONE_SCREEN, offsets=[90_000])
        self.assertEqual(sync.duration(), 0)

        sync.seek(5_000)

        self.assertEqual(sync.position(), 0)  # the one instant such a timeline has

    def test_playing_an_emptied_timeline_says_why_rather_than_starting_at_zero(
        self,
    ) -> None:
        heard: list[str] = []
        sync = self._engine(offsets=[90_000, 90_000])
        sync.problem.connect(heard.append)
        for player in self.made:
            player.calls.clear()

        sync.play()

        self.assertFalse(sync.playing())
        self.assertEqual(len(heard), 1)
        for index, player in enumerate(self.made):
            with self.subTest(index):
                self.assertNotIn("play", player.calls)

    def test_a_length_the_decoder_has_not_reported_is_not_an_empty_timeline(
        self,
    ) -> None:
        # The guard, and the reason the clamp in `seek` is not simply unconditional:
        # `duration()` is also 0 for every engine until its decoders answer, which is
        # the first few hundred milliseconds of every recording ever opened.
        sync = self._engine()
        for player in self.made:
            player.set_length(0)
            player.seeks.clear()
        self.assertEqual(sync.duration(), 0)

        sync.seek(5_000)

        # Asked for where it was told, not clamped to the one instant an *emptied*
        # timeline has. The stub's own playhead stays at 0 because `setPosition`
        # clamps at the length it is advertising, which is what a real one does too.
        self.assertEqual([player.seeks for player in self.made], [[5_000], [5_000]])
        sync.play()
        self.assertTrue(sync.playing())


class RussianReachesTheStripTests(EngineFixture, unittest.TestCase):
    """§13.10's last corner: the clock's own sentences, in a Russian interface.

    The engine shows nothing itself, which is how it was missed: every sentence it has
    leaves on `problem`, and the stage and the separate-windows player put that
    sentence straight onto the strip the viewer reads. There were 21 of them and not
    one `_()` in the file, so `check_translations.py` could not see them either - it
    follows `_()` and `pick()`.

    The checks pin the mechanism and not the words: each compares what the viewer is
    handed with the catalogue's own entry, so rewording the Russian is a one-file
    change exactly as it is everywhere else.
    """

    def setUp(self) -> None:
        super().setUp()
        from screenrec import i18n

        self.i18n = i18n
        self.addCleanup(i18n.set_language, i18n.language())

    def _said(self, call) -> str:
        sync = self._engine(ONE_SCREEN)
        heard: list[str] = []
        sync.problem.connect(heard.append)
        call(sync)
        self.assertEqual(len(heard), 1, heard)
        return heard[0]

    def test_a_refusal_from_the_clock_is_said_in_russian(self) -> None:
        from screenrec import translations

        self.i18n.set_language(self.i18n.LANG_RU)
        said = self._said(lambda sync: sync.set_volume("loud"))
        self.assertEqual(
            said,
            translations.RUSSIAN["{value} is not a volume."].format(value=repr("loud")),
        )

    def test_a_sentence_built_out_of_two_is_russian_in_both_halves(self) -> None:
        # `_hand_over_clock` puts one translated sentence inside another; the viewer
        # gets one line and both halves of it have to be the same language.
        from screenrec import translations

        self.i18n.set_language(self.i18n.LANG_RU)
        sync = self._engine(ONE_SCREEN)
        heard: list[str] = []
        sync.problem.connect(heard.append)

        sync._on_status(QtMultimedia.QMediaPlayer.MediaStatus.InvalidMedia)

        self.assertEqual(len(heard), 1, heard)
        name = sync.recording.screens[0].file.name
        why = translations.RUSSIAN[
            "{name} is not something this machine can play."
        ].format(name=name)
        self.assertEqual(
            heard[0],
            translations.RUSSIAN[
                "{why} There is nothing else in this set to play."
            ].format(why=why),
        )

    def test_english_is_still_english(self) -> None:
        # The guard: the fallback is the source text, so an English interface reads
        # exactly as it did before any of this.
        self.i18n.set_language(self.i18n.LANG_EN)
        said = self._said(lambda sync: sync.set_volume("loud"))
        self.assertEqual(said, "'loud' is not a volume.")


class NothingMovesTests(EngineFixture, unittest.TestCase):
    """§6's other promise, from the clock's side: reconnecting a sink is not a seek.

    The layouts move a stream between panes by connecting a different widget to the
    engine's sink. These check what that is allowed to cost - nothing - by doing it and
    then asking the engine where it is.
    """

    def test_taking_a_sink_elsewhere_moves_no_playhead(self) -> None:
        sync = self._engine()
        sync.play()
        for player in self.made:
            player.place(12_345)
        sync._tick()
        before = sync.position()
        seeks = [list(p.seeks) for p in self.made]
        playing = sync.playing()

        # What a layout change, a swap and a hide all are: each sink's frames routed to
        # some other widget. `panes.VideoPane.set_stream` is a disconnect and a connect
        # on exactly these signals, so that is what is done here, twice over.
        frames: list[object] = []
        sinks = sync.sinks()
        for sink in sinks:
            sink.videoFrameChanged.connect(frames.append)
        for sink in reversed(sinks):
            sink.videoFrameChanged.disconnect(frames.append)
            sink.videoFrameChanged.connect(frames.append)

        self.assertEqual(sync.position(), before)
        self.assertEqual([list(p.seeks) for p in self.made], seeks)
        self.assertEqual(sync.playing(), playing)
        self.assertFalse(sync.seeking())


if __name__ == "__main__":
    unittest.main()
