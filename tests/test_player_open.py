"""Tests for ``player/open.py`` - what a path is, and whether it opens.

Everything here runs offscreen and leaves nothing on the screen. The video files are
real, made once with the bundled ffmpeg (``testsrc``, one second, 160x90), because the
two cases worth proving are exactly the ones a fake file cannot prove: a hand-written
index whose two files exist really does come back as two views, and a lone ``.mp4``
really does come back as one.

What is *not* covered here: whether the picture appears. QtMultimedia decoding is the
window layer's business and needs a real frame to look at; this file only asks whether
`open_recording` built something and whether a failure is a sentence instead of a
traceback.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from screenrec import config, i18n, multi, paths  # noqa: E402
from screenrec.player import engine, layouts, open as player_open  # noqa: E402

CREATE_NO_WINDOW = 0x08000000

_app: QtWidgets.QApplication | None = None
_media_dir: Path | None = None
_clips: list[Path] = []
_ffmpeg_problem = ""


def _make_clip(target: Path, rate: int = 30, size: str = "160x90") -> str:
    """One second of ``testsrc`` at 160x90. Returns a problem, or ''.

    ``rate`` is a real frame rate written into a real file, which is the only way to
    prove what the player reads back out of one: two cameras at 25 and 30 fps are
    exactly the case ``docs/PLAYER_SOURCES.md`` §2.1 is about.

    ``size`` is there for the same reason. A **portrait** clip is the one shape that
    `FALLBACK_SIZE` cannot imitate, so a saved set that reopens 16:9 has demonstrably
    thrown a measurement away rather than merely rounded it.
    """
    try:
        ffmpeg = paths.ffmpeg_exe()
    except paths.BinaryNotFound as exc:
        return str(exc)
    command = [
        str(ffmpeg),
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi",
        "-i", f"testsrc=size={size}:rate={rate}:duration=1",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-an",
        str(target),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"ffmpeg could not be run: {exc}"
    if result.returncode != 0 or not target.is_file():
        return f"ffmpeg failed ({result.returncode}): {result.stderr.strip()}"
    return ""


def setUpModule() -> None:
    global _app, _media_dir, _ffmpeg_problem
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    _media_dir = Path(tempfile.mkdtemp(prefix="screenrec_player_"))
    for name in ("take_screen0.mp4", "take_screen1.mp4", "lonely.mp4"):
        clip = _media_dir / name
        problem = _make_clip(clip)
        if problem:
            _ffmpeg_problem = problem
            return
        _clips.append(clip)


def tearDownModule() -> None:
    if _media_dir is not None:
        # QMediaPlayer may still hold a handle on Windows; the temp folder is not worth
        # failing a run over.
        shutil.rmtree(_media_dir, ignore_errors=True)


def _views(opened: object) -> list[object]:
    """The per-screen views of whatever the window layer returned.

    `open_recording` is typed `object` on purpose - this module does not own the window
    class - so the count is read from whichever of these the window layer exposes.
    Since §2.1's dialog was wired in, "the window layer" is two classes rather than
    one: `PlayerWindows` carries a `views` list, and `PlayerStage` answers `streams()`
    with its own count of the screens it is showing.
    """
    for name in ("views", "windows", "screens", "panes"):
        found = getattr(opened, name, None)
        if isinstance(found, (list, tuple)):
            return list(found)
    streams = getattr(opened, "streams", None)
    if callable(streams):
        return list(streams())
    raise AssertionError(
        f"{type(opened).__name__} exposes no views/windows/screens sequence, so the "
        "number of views cannot be checked."
    )


def _close(opened: object) -> None:
    for name in ("close", "deleteLater"):
        method = getattr(opened, name, None)
        if callable(method):
            method()
    if _app is not None:
        _app.processEvents()


class RecordingFolder(unittest.TestCase):
    """A scratch folder with the clips in it, and nothing else.

    No tests of its own: it is the setUp and the four builders that every class below
    needs. They inherit *this* rather than each other, because a class that inherits a
    class with tests in it runs those tests a second time - and the second run picks up
    whatever the first left behind, which is how a language leaked out of
    `test_run_exits_zero_on_a_cancel` and made an unrelated assertion read Russian.
    """

    def setUp(self) -> None:
        if _ffmpeg_problem:
            self.skipTest(f"no test clips: {_ffmpeg_problem}")
        self.folder = Path(tempfile.mkdtemp(prefix="screenrec_case_"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.before = len(QtWidgets.QApplication.topLevelWidgets())

    def assertNoWindowAppeared(self) -> None:
        self.assertEqual(
            len(QtWidgets.QApplication.topLevelWidgets()),
            self.before,
            "a failed open left a window behind",
        )

    def copy_clip(self, index: int, name: str) -> Path:
        target = self.folder / name
        shutil.copyfile(_clips[index], target)
        return target

    def write_sidecar(self, name: str, document: object) -> Path:
        target = self.folder / name
        text = document if isinstance(document, str) else json.dumps(document, indent=2)
        target.write_text(text, encoding="utf-8")
        return target

    def two_screen_set(self) -> Path:
        self.copy_clip(0, "take_screen0.mp4")
        self.copy_clip(1, "take_screen1.mp4")
        return self.write_sidecar(
            f"take{multi.SIDECAR_SUFFIX}",
            {
                "version": multi.SIDECAR_VERSION,
                "recorded": "2026-11-04T01:30:00",
                "audio": "take_screen0.mp4",
                "screens": [
                    {
                        "file": "take_screen0.mp4",
                        "monitor": 0,
                        "desktop": [0, 0, 2560, 1440],
                        "size": [2560, 1440],
                        "rotation": 1,
                        "primary": True,
                    },
                    {
                        "file": "take_screen1.mp4",
                        "monitor": 1,
                        "desktop": [2560, 0, 1440, 2560],
                        "size": [1440, 2560],
                        "rotation": 2,
                        "primary": False,
                    },
                ],
            },
        )


class PlayerOpenTest(RecordingFolder):
    # -- the failures ------------------------------------------------------

    def test_missing_file_is_a_sentence(self) -> None:
        opened, problem = player_open.open_recording(self.folder / "nothing.mp4")
        self.assertIsNone(opened)
        self.assertIn("nothing.mp4", problem)
        self.assertTrue(problem.endswith("."), problem)
        self.assertNoWindowAppeared()

    def test_empty_folder_is_a_sentence(self) -> None:
        opened, problem = player_open.open_recording(self.folder)
        self.assertIsNone(opened)
        self.assertIn("no recording", problem.lower())
        self.assertIn(str(self.folder), problem)
        self.assertNoWindowAppeared()

    def test_malformed_sidecar_is_a_sentence(self) -> None:
        broken = self.write_sidecar(f"bad{multi.SIDECAR_SUFFIX}", "{ this is not json")
        opened, problem = player_open.open_recording(broken)
        self.assertIsNone(opened)
        self.assertIn("bad" + multi.SIDECAR_SUFFIX, problem)
        self.assertNoWindowAppeared()

    def test_sidecar_naming_files_that_are_not_there(self) -> None:
        sidecar = self.write_sidecar(
            f"gone{multi.SIDECAR_SUFFIX}",
            {
                "version": 1,
                "screens": [
                    {"file": "gone_screen0.mp4", "desktop": [0, 0, 100, 100]},
                ],
            },
        )
        opened, problem = player_open.open_recording(sidecar)
        self.assertIsNone(opened)
        self.assertIn("gone_screen0.mp4", problem)
        self.assertNoWindowAppeared()

    def test_a_file_that_is_not_a_recording(self) -> None:
        note = self.folder / "notes.txt"
        note.write_text("not a video", encoding="utf-8")
        opened, problem = player_open.open_recording(note)
        self.assertIsNone(opened)
        self.assertIn("notes.txt", problem)
        self.assertIn(multi.SIDECAR_SUFFIX, problem)
        self.assertNoWindowAppeared()

    # -- the openings ------------------------------------------------------

    def test_a_valid_set_opens_with_two_views(self) -> None:
        sidecar = self.two_screen_set()
        opened, problem = player_open.open_recording(sidecar)
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(problem, "")
        self.assertEqual(len(_views(opened)), 2)

    def test_a_plain_mp4_opens_with_one_view(self) -> None:
        lonely = self.copy_clip(2, "lonely.mp4")
        opened, problem = player_open.open_recording(lonely)
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(len(_views(opened)), 1)

    def test_a_folder_opens_the_set_in_it(self) -> None:
        self.two_screen_set()
        opened, problem = player_open.open_recording(self.folder)
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(len(_views(opened)), 2)

    # -- §2: the layout is chosen here, and nowhere else -------------------

    def test_a_two_screen_set_is_asked_which_layout(self) -> None:
        """The whole of §2 used to be unreachable: nothing ever built the chooser.

        `choose_layout` is the only door onto it, and both ways into the player come
        through this module - so a layout answered anywhere else would be answered
        nowhere. Offscreen there is nobody to click, so `chooser.choose` takes the
        recommendation and says so; what is proved here is that it was *asked*.
        """
        recording, note = player_open.load(self.two_screen_set())
        self.assertIsNotNone(recording, note)
        assert recording is not None
        answer = player_open.choose_layout(recording, config.Settings())
        self.assertIsNotNone(answer, "no layout was asked for at all")
        assert answer is not None
        layout, remember = answer
        self.assertIn(layout, layouts.LAYOUTS)
        self.assertFalse(remember, "nobody could answer, so nothing is remembered")

    def test_a_one_screen_recording_is_never_asked(self) -> None:
        """§1: "A recording with one stream never asks anything.\""""
        recording, note = player_open.load(self.copy_clip(2, "lonely.mp4"))
        self.assertIsNotNone(recording, note)
        assert recording is not None
        self.assertIsNone(player_open.choose_layout(recording, config.Settings()))

    def test_a_stage_layout_opens_the_stage_and_a_window_layout_the_windows(
        self,
    ) -> None:
        """§1's two halves reach two different classes; neither used to be reachable."""
        from screenrec.player import stage as player_stage, window as player_window

        sidecar = self.two_screen_set()
        for layout in (layouts.LAYOUT_STACKED, layouts.LAYOUT_SIDE_BY_SIDE):
            with self.subTest(layout=layout):
                opened, problem = player_open.open_recording(sidecar, layout=layout)
                self.assertIsNotNone(opened, problem)
                self.addCleanup(_close, opened)
                self.assertIsInstance(opened, player_stage.PlayerStage)
                self.assertEqual(opened.layout_id(), layout)

        opened, problem = player_open.open_recording(
            sidecar, layout=layouts.LAYOUT_SEPARATE
        )
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertIsInstance(opened, player_window.PlayerWindows)
        self.assertEqual(len(_views(opened)), 2)

    def test_one_screen_per_monitor_is_in_force_before_anything_is_shown(self) -> None:
        """§5's arrangement is set on the way in, so the windows do not jump."""
        opened, problem = player_open.open_recording(
            self.two_screen_set(), layout=layouts.LAYOUT_ONE_PER_MONITOR
        )
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        # This machine has one screen, so the arrangement is refused and said on the
        # bar - which is the answer §5 gives for a desk that cannot do it. What is
        # checked is that it was attempted at all rather than quietly dropped.
        self.assertIn(
            opened.arrangement(), ("", layouts.LAYOUT_ONE_PER_MONITOR)
        )

    def test_one_clock_whichever_window_the_layout_asks_for(self) -> None:
        """§6: "One clock for both streams... none of this may create a second one."

        The layout decides which widget listens to the clock and nothing else, so the
        count must be one for a stage layout and one for a window layout alike.
        """
        built: list[object] = []
        real = engine.SyncEngine

        def counted(recording, parent=None):
            clock = real(recording, parent)
            built.append(clock)
            return clock

        self.addCleanup(setattr, engine, "SyncEngine", real)
        engine.SyncEngine = counted
        sidecar = self.two_screen_set()
        for layout in (layouts.LAYOUT_STACKED, layouts.LAYOUT_SEPARATE):
            with self.subTest(layout=layout):
                built.clear()
                opened, problem = player_open.open_recording(sidecar, layout=layout)
                self.assertIsNotNone(opened, problem)
                self.addCleanup(_close, opened)
                self.assertEqual(len(built), 1)
                self.assertIs(opened.engine, built[0])

    def test_a_remembered_scheme_comes_back_only_as_part_of_separate(self) -> None:
        """§5's `player_tiling` has no "none" value, so it needs the layout beside it.

        `config` rejects "" and writes `equal_lr` back, so the stored scheme alone
        cannot be told from a default - and tiling two windows because of a default
        is an arrangement nobody chose.
        """
        sidecar = self.two_screen_set()
        settings = config.Settings()
        settings.player_tiling = layouts.TILING_EQUAL_TB
        settings.player_display_signature = layouts.current_signature()

        settings.player_layout = layouts.LAYOUT_SEPARATE
        opened, problem = player_open.open_recording(
            sidecar, layout=layouts.LAYOUT_SEPARATE, settings=settings
        )
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(opened.tiling(), layouts.TILING_EQUAL_TB)

        # The same stored scheme, but the viewer's last choice was a stage layout:
        # nothing was ever tiled, so nothing is restored.
        settings.player_layout = layouts.LAYOUT_STACKED
        other, problem = player_open.open_recording(
            sidecar, layout=layouts.LAYOUT_SEPARATE, settings=settings
        )
        self.assertIsNotNone(other, problem)
        self.addCleanup(_close, other)
        self.assertEqual(other.tiling(), "")

    def test_a_cancel_opens_nothing_and_complains_about_nothing(self) -> None:
        """Отмена is not a failure, and must not be reported as one."""
        self.addCleanup(
            setattr, player_open, "choose_layout", player_open.choose_layout
        )
        player_open.choose_layout = lambda recording, settings=None: None
        opened, problem = player_open.open_recording(self.two_screen_set())
        self.assertIsNone(opened)
        self.assertEqual(problem, "", "a cancel was reported as a problem")
        self.assertNoWindowAppeared()

    def test_run_exits_zero_on_a_cancel(self) -> None:
        """Exit 2 would tell a script the recording was broken, which it is not."""
        # `run` puts the stored language in force - it is its own process - so this is
        # the one test here that changes the language of every test after it. Put back.
        self.addCleanup(i18n.set_language, i18n.language())
        self.addCleanup(
            setattr, player_open, "open_recording", player_open.open_recording
        )
        player_open.open_recording = lambda *a, **k: (None, "")
        complaints: list[str] = []
        self.addCleanup(setattr, player_open, "_complain", player_open._complain)
        player_open._complain = complaints.append
        self.assertEqual(player_open.run([str(self.two_screen_set())]), 0)
        self.assertEqual(complaints, [])

    # -- §2.3: remembering -------------------------------------------------

    def saved_settings(self) -> list[config.Settings]:
        """Capture every `save_settings` call instead of writing the real file."""
        written: list[config.Settings] = []
        self.addCleanup(setattr, config, "save_settings", config.save_settings)
        config.save_settings = lambda settings, path=None: (
            written.append(settings) or ""
        )
        return written

    def answer_with(self, layout: str, remember: bool) -> None:
        self.addCleanup(
            setattr, player_open, "choose_layout", player_open.choose_layout
        )
        player_open.choose_layout = (
            lambda recording, settings=None: (layout, remember)
        )

    def test_a_ticked_box_stores_the_layout_and_the_displays_it_was_chosen_on(
        self,
    ) -> None:
        written = self.saved_settings()
        self.answer_with(layouts.LAYOUT_STACKED, True)
        settings = config.Settings()
        opened, problem = player_open.open_recording(
            self.two_screen_set(), settings=settings
        )
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(settings.player_layout, layouts.LAYOUT_STACKED)
        self.assertEqual(
            settings.player_display_signature, layouts.current_signature()
        )
        self.assertTrue(written, "the ticked choice was never written to disk")
        self.assertIs(written[-1], settings)

    def test_an_unticked_box_writes_nothing_at_all(self) -> None:
        """The checkbox is the only thing a viewer is shown about any of this."""
        written = self.saved_settings()
        self.answer_with(layouts.LAYOUT_STACKED, False)
        settings = config.Settings()
        opened, problem = player_open.open_recording(
            self.two_screen_set(), settings=settings
        )
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(settings.player_layout, "")
        self.assertEqual(
            opened.findChildren(player_open._LayoutMemory), [],
            "nothing should be watching the layout when the box was not ticked",
        )
        self.assertEqual(written, [])

    def test_a_change_the_viewer_makes_afterwards_is_stored_too(self) -> None:
        """§2.3 remembers the layout's own state, not only which layout it is."""
        self.saved_settings()
        self.answer_with(layouts.LAYOUT_SIDE_BY_SIDE, True)
        settings = config.Settings()
        opened, problem = player_open.open_recording(
            self.two_screen_set(), settings=settings
        )
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        opened.swap()
        opened.set_layout(layouts.LAYOUT_STACKED)
        self.assertTrue(settings.player_swapped)
        self.assertEqual(settings.player_layout, layouts.LAYOUT_STACKED)

    def test_a_remembered_split_is_restored_only_on_the_same_desk(self) -> None:
        """§2.3: a fraction travels, but only back onto the displays it was set on."""
        recording, note = player_open.load(self.two_screen_set())
        self.assertIsNotNone(recording, note)
        assert recording is not None
        settings = config.Settings()
        settings.player_layout = layouts.LAYOUT_SIDE_BY_SIDE
        settings.player_split = 0.7
        settings.player_display_signature = layouts.current_signature()
        here = player_open._stage_arguments(
            recording, layouts.LAYOUT_SIDE_BY_SIDE, settings
        )
        self.assertEqual(here["split"], 0.7)

        settings.player_display_signature = "1024x768+0+0@1.0"
        elsewhere = player_open._stage_arguments(
            recording, layouts.LAYOUT_SIDE_BY_SIDE, settings
        )
        self.assertNotIn("split", elsewhere)
        self.assertEqual(elsewhere["layout"], layouts.LAYOUT_SIDE_BY_SIDE)

    def test_the_write_is_deferred_so_a_drag_is_one_write(self) -> None:
        """`split_changed` fires on every mouse move of a splitter drag."""
        written = self.saved_settings()
        settings = config.Settings()
        memory = player_open._LayoutMemory(settings)
        self.addCleanup(memory.deleteLater)
        for fraction in (0.51, 0.52, 0.53, 0.54):
            memory.set(player_split=fraction)
        self.assertEqual(written, [], "a drag was written on every step of it")
        self.assertEqual(settings.player_split, 0.54)
        memory.flush()
        self.assertEqual(len(written), 1)

    def test_a_field_this_build_has_no_name_for_is_said_not_set(self) -> None:
        """A `Settings` grown an attribute is written and then read back by nobody."""
        self.saved_settings()
        said: list[str] = []
        self.addCleanup(setattr, player_open, "_say", player_open._say)
        player_open._say = said.append
        settings = config.Settings()
        memory = player_open._LayoutMemory(settings)
        self.addCleanup(memory.deleteLater)
        memory.set(player_nonsense=1)
        self.assertFalse(hasattr(settings, "player_nonsense"))
        self.assertTrue(any("player_nonsense" in line for line in said), said)

    # -- the headless half -------------------------------------------------

    def test_load_reads_the_real_size_of_a_plain_file(self) -> None:
        lonely = self.copy_clip(2, "lonely.mp4")
        recording, note = player_open.load(lonely)
        self.assertIsNotNone(recording, note)
        assert recording is not None  # for the type checker
        self.assertEqual(note, "")
        self.assertEqual(len(recording.screens), 1)
        self.assertEqual(recording.screens[0].size, (160, 90))
        self.assertEqual(recording.audio_file, lonely)

    def test_load_of_a_set_keeps_both_screens(self) -> None:
        sidecar = self.two_screen_set()
        recording, note = player_open.load(sidecar)
        self.assertIsNotNone(recording, note)
        assert recording is not None
        self.assertEqual(note, "")
        self.assertEqual([s.monitor for s in recording.screens], [0, 1])
        self.assertEqual(recording.audio_file.name, "take_screen0.mp4")

    def test_most_recent_prefers_the_set_over_its_own_files(self) -> None:
        sidecar = self.two_screen_set()
        self.assertEqual(player_open.most_recent(self.folder), sidecar)

    def test_most_recent_finds_a_lone_video(self) -> None:
        lonely = self.copy_clip(2, "lonely.mp4")
        self.assertEqual(player_open.most_recent(self.folder), lonely)

    def test_most_recent_of_nothing_is_none(self) -> None:
        self.assertIsNone(player_open.most_recent(self.folder))
        self.assertIsNone(player_open.most_recent(self.folder / "not_a_folder"))

    def test_the_window_layer_is_found(self) -> None:
        factory, problem = player_open.window_factory()
        self.assertIsNotNone(factory, problem)
        self.assertEqual(problem, "")

    def test_a_pinned_factory_is_used_and_its_failure_is_reported(self) -> None:
        def explode(clock: engine.SyncEngine) -> object:
            raise RuntimeError("no screens today")

        player_open.use_window_factory(explode)
        self.addCleanup(player_open.use_window_factory, None)
        lonely = self.copy_clip(2, "lonely.mp4")
        opened, problem = player_open.open_recording(lonely)
        self.assertIsNone(opened)
        self.assertIn("no screens today", problem)
        self.assertNoWindowAppeared()


class LanguageTest(unittest.TestCase):
    """Are the sentences this module shows in a box translated?

    They are the only thing a viewer who double-clicked the wrong file ever sees:
    under the shipped launcher there is no console, so `_complain` and `_show_note`
    put them in a `QMessageBox`. They shipped as f-strings, which can never be
    catalogue keys, and the box came up in English whatever the setting said.

    No clips and no ffmpeg here: every case is a path that will not open, so this
    class does not need `PlayerOpenTest`'s skip.
    """

    def setUp(self) -> None:
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)
        self.folder = Path(tempfile.mkdtemp(prefix="screenrec_lang_"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def _refusals(self) -> dict[str, str]:
        """One sentence per way of asking for something that will not open."""
        not_a_video = self.folder / "notes.txt"
        not_a_video.write_text("nothing to play here", encoding="utf-8")
        return {
            "no path": player_open.open_recording(None)[1],
            "wrong kind": player_open.load(not_a_video)[1],
            "no file": player_open.load(self.folder / "gone.mp4")[1],
            "empty folder": player_open.load(self.folder)[1],
        }

    def test_every_refusal_is_translated(self) -> None:
        """THE DEFECT: the box said "notes.txt is not something the player can open"."""
        i18n.set_language(i18n.LANG_EN)
        english = self._refusals()
        i18n.set_language(i18n.LANG_RU)
        russian = self._refusals()
        untranslated = sorted(
            case for case, text in russian.items() if text == english[case]
        )
        self.assertEqual(
            untranslated, [],
            "these sentences read the same in Russian as in English, so they never "
            "went through i18n._()",
        )

    def test_the_refusal_keeps_the_name_and_the_suffixes_it_names(self) -> None:
        """A template with its fields left unfilled is worse than English."""
        i18n.set_language(i18n.LANG_RU)
        not_a_video = self.folder / "notes.txt"
        not_a_video.write_text("x", encoding="utf-8")
        _recording, problem = player_open.load(not_a_video)
        self.assertIn("notes.txt", problem)
        self.assertIn(multi.SIDECAR_SUFFIX, problem)
        self.assertIn(".mp4", problem)
        self.assertNotIn("{", problem)

    def test_english_is_unchanged(self) -> None:
        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(
            player_open.open_recording(None)[1], "No recording was named."
        )

    def test_the_usage_text_follows_the_language_and_keeps_the_command(self) -> None:
        """It was a module constant, which is translated once, at import.

        That is the bug this test exists for: with a constant, the first language the
        process happens to start in is the only one `--help` ever speaks.
        """
        i18n.set_language(i18n.LANG_EN)
        english = player_open.usage()
        i18n.set_language(i18n.LANG_RU)
        russian = player_open.usage()
        self.assertNotEqual(russian, english)
        for text in (english, russian):
            self.assertIn("python -m screenrec.player [path]", text)
            self.assertIn(multi.SIDECAR_SUFFIX, text)
        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(player_open.usage(), english)


class OwnFilesTest(RecordingFolder):
    """§1 of `docs/PLAYER_SOURCES.md`: files nobody paired for us.

    Two cameras are two files with no index, no shared clock and no agreement about
    when either of them started. Everything here is about building a playable set out
    of that - and about the regression that matters most, which is that a
    recorder-made set still opens exactly as it did before any of this existed.
    """

    def other_folder(self) -> Path:
        """A second folder, because two cameras are not guaranteed to share one."""
        second = Path(tempfile.mkdtemp(prefix="screenrec_camera_"))
        self.addCleanup(shutil.rmtree, second, ignore_errors=True)
        return second

    def two_cameras(self) -> tuple[Path, Path]:
        """One clip here and one somewhere else entirely, as a shoot really arrives."""
        here = self.copy_clip(0, "camera_a.mp4")
        there = self.other_folder() / "camera_b.mp4"
        shutil.copyfile(_clips[1], there)
        return here, there

    # -- building a recording out of loose files ---------------------------

    def test_two_unrelated_videos_become_two_screens(self) -> None:
        first, second = self.two_cameras()
        recording, note = player_open.recording_from_files([first, second])
        self.assertIsNotNone(recording, note)
        assert recording is not None
        self.assertEqual(note, "")
        self.assertEqual([s.file for s in recording.screens], [first, second])
        self.assertEqual(recording.audio_file, first, "§1.2: the first by default")

    def test_the_invented_desktop_runs_left_to_right(self) -> None:
        """Nothing recorded these together, so the arrangement has to be made up.

        What `layouts` then scores is this rectangle, so a second screen stacked on
        top of the first - or sharing its origin - would score as an overlap that
        never happened.
        """
        first, second = self.two_cameras()
        recording, _note = player_open.recording_from_files([first, second])
        assert recording is not None
        left, right = recording.screens
        self.assertEqual(left.desktop[:2], (0, 0))
        self.assertEqual(right.desktop[0], left.desktop[2])
        self.assertEqual(right.desktop[1], 0)
        self.assertEqual([s.monitor for s in recording.screens], [0, 1])
        self.assertTrue(left.primary)
        self.assertFalse(right.primary)

    def test_a_missing_file_is_named_and_the_rest_still_open(self) -> None:
        first, _second = self.two_cameras()
        gone = self.folder / "camera_c.mp4"
        recording, note = player_open.recording_from_files([first, gone])
        self.assertIsNotNone(recording, note)
        assert recording is not None
        self.assertEqual(len(recording.screens), 1)
        self.assertIn("camera_c.mp4", note)

    def test_a_file_of_the_wrong_kind_is_named_and_left_out(self) -> None:
        first, _second = self.two_cameras()
        notes = self.folder / "shotlist.txt"
        notes.write_text("clap on 3", encoding="utf-8")
        recording, note = player_open.recording_from_files([first, notes])
        assert recording is not None
        self.assertEqual(len(recording.screens), 1)
        self.assertIn("shotlist.txt", note)

    def test_nothing_openable_is_a_sentence_and_not_a_crash(self) -> None:
        recording, note = player_open.recording_from_files(
            [self.folder / "a.mp4", self.folder / "b.mp4"]
        )
        self.assertIsNone(recording)
        self.assertIn("a.mp4", note)
        self.assertIn("b.mp4", note)
        self.assertNotIn("{", note)

    def test_junk_in_the_list_is_a_sentence(self) -> None:
        """A drop carries whatever the window manager had; `None` must not raise."""
        first, _second = self.two_cameras()
        recording, note = player_open.recording_from_files([first, None])
        assert recording is not None
        self.assertEqual(len(recording.screens), 1)
        self.assertTrue(note)

    def test_the_audio_source_defaults_to_the_first_and_can_be_moved(self) -> None:
        """§1.2: the choice is the viewer's, not the file order's."""
        first, second = self.two_cameras()
        chosen, _note = player_open.recording_from_files(
            [first, second], audio_index=1
        )
        assert chosen is not None
        self.assertEqual(chosen.audio_file, second)
        self.assertEqual(chosen.master_index(), 1)

        # Out of range is read as the first stream rather than raising: the index
        # counts the files that opened, and one of them may have been left out.
        far, _note = player_open.recording_from_files([first, second], audio_index=7)
        assert far is not None
        self.assertEqual(far.audio_file, first)

    def test_ffprobe_missing_falls_back_with_a_note(self) -> None:
        """A player that will not open a file because ffprobe is gone is absurd."""
        first, second = self.two_cameras()
        self.addCleanup(setattr, paths, "ffprobe_exe", paths.ffprobe_exe)

        def refuse(override=None):
            raise paths.BinaryNotFound("ffprobe.exe was not found (tried: nowhere)")

        paths.ffprobe_exe = refuse
        recording, note = player_open.recording_from_files([first, second])
        self.assertIsNotNone(recording, note)
        assert recording is not None
        self.assertEqual(len(recording.screens), 2)
        self.assertEqual(recording.screens[0].size, player_open.FALLBACK_SIZE)
        self.assertIn("ffprobe", note)
        # The made-up desktop still runs left to right, at the fallback width.
        self.assertEqual(
            recording.screens[1].desktop[0], player_open.FALLBACK_SIZE[0]
        )

    # -- the door: one path, or several ------------------------------------

    def test_two_files_given_together_open_as_a_pair(self) -> None:
        first, second = self.two_cameras()
        opened, problem = player_open.open_recording(
            [first, second], layout=layouts.LAYOUT_SIDE_BY_SIDE
        )
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(len(_views(opened)), 2)

    def test_absolute_paths_as_text_open(self) -> None:
        """What a file dialog and a drop both hand over is a string, not a Path."""
        first, second = self.two_cameras()
        opened, problem = player_open.open_recording(
            [str(first.resolve()), str(second.resolve())],
            layout=layouts.LAYOUT_SIDE_BY_SIDE,
        )
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(len(_views(opened)), 2)

    def test_a_list_of_one_behaves_exactly_like_one_path(self) -> None:
        """A list of one is not a pair: an index and a folder still mean what they did.

        Both are the shapes a drop and a file dialog really produce, and both used to
        reach `load` directly.
        """
        sidecar = self.two_screen_set()
        for given in (sidecar, [sidecar], self.folder, [self.folder]):
            with self.subTest(given=given):
                opened, problem = player_open.open_recording(
                    given, layout=layouts.LAYOUT_SIDE_BY_SIDE
                )
                self.assertIsNotNone(opened, problem)
                self.addCleanup(_close, opened)
                self.assertEqual(len(_views(opened)), 2)

    def test_a_plain_video_in_a_list_still_opens_as_one_stream(self) -> None:
        lonely = self.copy_clip(2, "lonely.mp4")
        opened, problem = player_open.open_recording([lonely])
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(len(_views(opened)), 1)

    def test_an_empty_list_is_a_sentence(self) -> None:
        opened, problem = player_open.open_recording([])
        self.assertIsNone(opened)
        self.assertTrue(problem)
        self.assertNoWindowAppeared()

    def test_a_pair_where_one_file_is_gone_opens_the_other_and_says_which(
        self,
    ) -> None:
        first, _second = self.two_cameras()
        opened, problem = player_open.open_recording(
            [first, self.folder / "camera_c.mp4"]
        )
        self.assertIsNotNone(opened, problem)
        self.addCleanup(_close, opened)
        self.assertEqual(len(_views(opened)), 1)
        self.assertIn("camera_c.mp4", problem)

    # -- the regression that matters most ----------------------------------

    def test_a_recorder_made_set_is_untouched_by_any_of_this(self) -> None:
        """The sidecar's own geometry, audio choice and note, as they always were."""
        sidecar = self.two_screen_set()
        recording, note = player_open.load(sidecar)
        self.assertIsNotNone(recording, note)
        assert recording is not None
        self.assertEqual(note, "")
        self.assertEqual(
            [s.desktop for s in recording.screens],
            [(0, 0, 2560, 1440), (2560, 0, 1440, 2560)],
        )
        self.assertEqual([s.rotation for s in recording.screens], [1, 2])
        self.assertEqual(recording.audio_file.name, "take_screen0.mp4")
        self.assertEqual(recording.recorded, "2026-11-04T01:30:00")

    def test_the_newest_recording_is_still_the_set_and_not_one_of_its_files(
        self,
    ) -> None:
        """`most_recent` is now the first row of `scan`; it must still answer this."""
        sidecar = self.two_screen_set()
        self.assertEqual(player_open.most_recent(self.folder), sidecar)

    def test_a_loose_video_beside_a_set_is_a_recording_of_its_own(self) -> None:
        self.two_screen_set()
        lonely = self.copy_clip(2, "lonely.mp4")
        found = {candidate.path for candidate in player_open.scan(self.folder)}
        self.assertIn(lonely, found)
        self.assertNotIn(self.folder / "take_screen0.mp4", found)

    def test_an_index_naming_a_file_elsewhere_hides_nothing_here(self) -> None:
        """§1.3 lets `"file"` be absolute, so a basename alone must not claim one."""
        elsewhere = self.other_folder() / "take_screen0.mp4"
        shutil.copyfile(_clips[0], elsewhere)
        mine = self.copy_clip(1, "take_screen0.mp4")
        self.write_sidecar(
            f"elsewhere{multi.SIDECAR_SUFFIX}",
            {
                "version": 2,
                "screens": [{"file": str(elsewhere), "desktop": [0, 0, 160, 90]}],
            },
        )
        found = {candidate.path for candidate in player_open.scan(self.folder)}
        self.assertIn(mine, found, "a video here was hidden by a name used elsewhere")


class FramerateTest(RecordingFolder):
    """§2.1: the frame nudge moves a frame, so the rate has to be the file's own.

    `engine.DEFAULT_FRAMERATE` is 60 because that is what *this recorder* writes. A
    camera is not this recorder, and a pair the viewer put together by hand has no
    sidecar to name a rate - so until this was read off the file, «+1 кадр» on a 25 fps
    clip moved 17 ms (two thirds of a frame) and the transport's tooltip said 60 fps
    over footage that was never shot at 60.
    """

    def clip_at(self, name: str, rate: int) -> Path:
        target = self.folder / name
        problem = _make_clip(target, rate)
        if problem:
            self.skipTest(f"no test clip at {rate} fps: {problem}")
        return target

    def test_a_pair_takes_the_frame_rate_of_the_file_carrying_the_sound(self) -> None:
        """Two cameras, two rates, and one of them is the clock's."""
        slow = self.clip_at("cam_25.mp4", 25)
        fast = self.clip_at("cam_30.mp4", 30)

        first, note = player_open.recording_from_files([slow, fast])
        self.assertIsNotNone(first, note)
        assert first is not None
        self.assertAlmostEqual(first.framerate, 25.0, places=3)

        # §1.2 moves the sound, and the clock goes with it.
        second, note = player_open.recording_from_files(
            [slow, fast], audio_index=1
        )
        assert second is not None
        self.assertAlmostEqual(second.framerate, 30.0, places=3)

    def test_a_lone_video_is_stepped_at_its_own_rate(self) -> None:
        slow = self.clip_at("lonely_25.mp4", 25)
        recording, note = player_open.load(slow)
        self.assertIsNotNone(recording, note)
        assert recording is not None
        self.assertAlmostEqual(recording.framerate, 25.0, places=3)
        self.assertNotEqual(recording.framerate, engine.DEFAULT_FRAMERATE)

    def test_the_rate_is_read_in_the_same_run_as_the_size(self) -> None:
        """One ffprobe call answers both; neither may come back as the other."""
        slow = self.clip_at("sized_25.mp4", 25)
        probe, note = player_open._probe_video(slow)
        self.assertEqual(note, "")
        self.assertEqual((probe.width, probe.height), (160, 90))
        self.assertAlmostEqual(probe.framerate, 25.0, places=3)

    def test_an_unreadable_rate_falls_back_and_says_so(self) -> None:
        """No ffprobe means no rate either, and a stand-in nobody is told about is
        exactly how a tooltip comes to state a frame rate no camera has."""
        first, second = self.clip_at("a.mp4", 25), self.clip_at("b.mp4", 25)
        self.addCleanup(setattr, paths, "ffprobe_exe", paths.ffprobe_exe)

        def refuse(override=None):
            raise paths.BinaryNotFound("ffprobe.exe was not found (tried: nowhere)")

        paths.ffprobe_exe = refuse
        recording, note = player_open.recording_from_files([first, second])
        assert recording is not None
        self.assertEqual(recording.framerate, engine.DEFAULT_FRAMERATE)
        self.assertIn("ffprobe", note)

    def test_a_rate_is_read_as_the_rational_ffprobe_writes(self) -> None:
        """29.97 arrives as `30000/1001`; `float()` alone would refuse it."""
        self.assertAlmostEqual(player_open._as_rate("30000/1001"), 29.97, places=2)
        self.assertEqual(player_open._as_rate("25"), 25.0)
        self.assertEqual(player_open._as_rate("25/1"), 25.0)

    def test_a_rate_that_is_not_one_is_refused(self) -> None:
        """`0/0` is a container naming no rate and 1200 fps is a ceiling, not a rate."""
        for given in ("0/0", "", None, "n/a", "1200/1", "0.5"):
            with self.subTest(given=given):
                self.assertEqual(player_open._as_rate(given), 0.0)

    def test_the_fields_are_read_by_name_and_not_by_position(self) -> None:
        """ffprobe prints its own order, so a rate must not land where a height was."""
        fields = player_open._probe_fields(
            "r_frame_rate=25/1\nwidth=160\nheight=90\navg_frame_rate=25/1\n"
        )
        self.assertEqual(fields["width"], "160")
        self.assertEqual(fields["r_frame_rate"], "25/1")

    def test_a_recorder_made_set_still_steps_at_the_recorder_s_rate(self) -> None:
        """The regression that matters: an index naming no rate is 60, as always."""
        sidecar = self.two_screen_set()
        recording, note = player_open.load(sidecar)
        self.assertIsNotNone(recording, note)
        assert recording is not None
        self.assertEqual(recording.framerate, engine.DEFAULT_FRAMERATE)


class SaveSetTest(RecordingFolder):
    """§1.3: two files, an audio choice and an offset are work worth keeping.

    `multi.write_set` is being added in the same round of work as this, so what is
    pinned here is the half this module owns - which files go in, where the index
    lands, and that every way of failing is a sentence rather than a traceback. The
    last test rides the real writer once it exists and skips until then.
    """

    def use_writer(self, writer) -> None:
        """Stand `writer` in for `multi.write_set`, however that module is today."""
        had = getattr(multi, "write_set", None)
        if had is None:
            self.addCleanup(lambda: multi.__dict__.pop("write_set", None))
        else:
            self.addCleanup(setattr, multi, "write_set", had)
        multi.write_set = writer

    def two_cameras(self) -> tuple[Path, Path]:
        return self.copy_clip(0, "camera_a.mp4"), self.copy_clip(1, "camera_b.mp4")

    def test_the_index_lands_beside_the_first_file_with_what_it_was_given(
        self,
    ) -> None:
        seen: list[dict] = []

        def writer(path, files, *, audio=None, offsets=None, recorded=None):
            seen.append(
                {
                    "path": path, "files": list(files), "audio": audio,
                    "offsets": list(offsets or []), "recorded": recorded,
                }
            )
            Path(path).write_text("{}", encoding="utf-8")
            return ""

        self.use_writer(writer)
        first, second = self.two_cameras()
        where, note = player_open.save_set(
            [first, second], audio_index=1, offsets=[0, -1480]
        )
        self.assertIsNotNone(where, note)
        assert where is not None
        self.assertEqual(where, self.folder / f"camera_a{multi.SIDECAR_SUFFIX}")
        self.assertEqual(note, "")
        self.assertEqual(seen[0]["files"], [first, second])
        self.assertEqual(seen[0]["audio"], second, "§1.2's choice must reach the file")
        self.assertEqual(seen[0]["offsets"], [0, -1480])

    def test_a_writer_that_spells_its_arguments_differently_still_gets_them(
        self,
    ) -> None:
        """The writer is another hand's work; a rename must not be a TypeError."""
        seen: list[dict] = []

        def writer(path, files, audio_index=0, offsets_ms=None):
            seen.append({"audio_index": audio_index, "offsets": list(offsets_ms or [])})
            return ""

        self.use_writer(writer)
        first, second = self.two_cameras()
        where, note = player_open.save_set(
            [first, second], audio_index=1, offsets=[250, 0]
        )
        self.assertIsNotNone(where, note)
        self.assertEqual(seen[0], {"audio_index": 1, "offsets": [250, 0]})

    def test_a_writer_that_takes_only_kwargs_is_still_told_everything(self) -> None:
        """`**kwargs` declares no names, and passing nothing would drop the offsets."""
        seen: list[dict] = []

        def writer(path, files, **rest):
            seen.append(dict(rest))
            return ""

        self.use_writer(writer)
        first, second = self.two_cameras()
        where, note = player_open.save_set(
            [first, second], audio_index=1, offsets=[0, -1480]
        )
        self.assertIsNotNone(where, note)
        self.assertEqual(seen[0].get("audio"), second)
        self.assertEqual(seen[0].get("offsets"), [0, -1480])
        self.assertTrue(seen[0].get("recorded"))

    def test_an_index_that_is_already_there_is_stepped_over_not_through(self) -> None:
        """A recorder-made index carries the only copy of its desktop rectangles."""
        self.use_writer(lambda path, files, **rest: "")
        first, second = self.two_cameras()
        taken = self.folder / f"camera_a{multi.SIDECAR_SUFFIX}"
        taken.write_text("the recorder's own", encoding="utf-8")
        where, note = player_open.save_set([first, second])
        self.assertIsNotNone(where, note)
        assert where is not None
        self.assertNotEqual(where, taken)
        self.assertEqual(taken.read_text(encoding="utf-8"), "the recorder's own")

    def test_a_file_that_has_gone_is_named_and_its_offset_goes_with_it(self) -> None:
        seen: list[list[int]] = []

        def writer(path, files, offsets=None, **rest):
            seen.append(list(offsets or []))
            return ""

        self.use_writer(writer)
        first, second = self.two_cameras()
        where, note = player_open.save_set(
            [first, self.folder / "camera_x.mp4", second], offsets=[0, 99, -1480]
        )
        self.assertIsNotNone(where, note)
        self.assertIn("camera_x.mp4", note)
        self.assertEqual(
            seen[0], [0, -1480], "the dropped file's offset shifted the others"
        )

    def test_nothing_to_save_is_a_sentence(self) -> None:
        self.use_writer(lambda path, files, **rest: "")
        where, note = player_open.save_set([self.folder / "gone.mp4"])
        self.assertIsNone(where)
        self.assertIn("gone.mp4", note)
        self.assertNotIn("{", note)

    def test_a_folder_that_is_not_there_is_a_sentence(self) -> None:
        self.use_writer(lambda path, files, **rest: "")
        first, _second = self.two_cameras()
        where, note = player_open.save_set(
            [first], target=self.folder / "nowhere" / f"x{multi.SIDECAR_SUFFIX}"
        )
        self.assertIsNone(where)
        self.assertIn("nowhere", note)
        self.assertNotIn("{", note)

    def test_a_writer_that_raises_is_a_sentence(self) -> None:
        def writer(path, files, **rest):
            raise OSError(13, "Permission denied")

        self.use_writer(writer)
        first, _second = self.two_cameras()
        where, note = player_open.save_set([first])
        self.assertIsNone(where)
        self.assertIn("Permission denied", note)

    def test_a_writer_that_complains_is_passed_through(self) -> None:
        self.use_writer(lambda path, files, **rest: "The disk is full.")
        first, _second = self.two_cameras()
        where, note = player_open.save_set([first])
        self.assertIsNone(where)
        self.assertIn("The disk is full.", note)

    def test_the_set_is_dated_by_the_footage_not_by_the_moment_save_was_pressed(
        self,
    ) -> None:
        """§1.3's index carries the shoot's date; `write_set` writes `now()`.

        The library reads that date and sorts on it, so a pair saved out of last
        month's footage used to appear above the take it was made from, dated today.
        """
        if not callable(getattr(multi, "write_set", None)):
            self.skipTest("multi.write_set is not written yet")
        first, second = self.two_cameras()
        weeks_ago = time.time() - 21 * 24 * 3600
        for one in (first, second):
            os.utime(one, (weeks_ago, weeks_ago))
        shot = player_open._recorded_text(first)

        where, note = player_open.save_set([first, second], offsets=[0, -1480])
        self.assertIsNotNone(where, note)
        assert where is not None
        self.assertEqual(note, "")
        written = json.loads(where.read_text(encoding="utf-8"))
        self.assertEqual(written["recorded"], shot)

        # Everything else the writer put there is still there, untouched.
        self.assertEqual(written["audio"], "camera_a.mp4")
        self.assertEqual([s["offset_ms"] for s in written["screens"]], [0, -1480])

    def test_a_writer_that_takes_the_date_itself_is_not_written_over(self) -> None:
        """The day `write_set` grows a `recorded` parameter, nothing touches the file."""
        seen: list[object] = []

        def writer(path, files, *, audio=None, offsets=None, recorded=None):
            seen.append(recorded)
            Path(path).write_text(
                json.dumps({"recorded": "the writer's own"}), encoding="utf-8"
            )
            return ""

        self.use_writer(writer)
        first, second = self.two_cameras()
        where, note = player_open.save_set([first, second])
        self.assertIsNotNone(where, note)
        assert where is not None
        self.assertEqual(note, "")
        self.assertTrue(seen[0], "the date was not offered to a writer that takes it")
        written = json.loads(where.read_text(encoding="utf-8"))
        self.assertEqual(written["recorded"], "the writer's own")

    def test_an_index_that_cannot_be_re_read_costs_the_date_and_not_the_set(
        self,
    ) -> None:
        """Dating is a correction to somebody else's file, never a reason to fail.

        The writer here is today's `multi.write_set` shape - it names its arguments and
        `recorded` is not among them - so the date is put in afterwards, and this is
        what happens when the file it wrote is not JSON at all.
        """
        def writer(path, files, audio_index=0, offsets=None):
            Path(path).write_text("not json at all", encoding="utf-8")
            return ""

        self.use_writer(writer)
        first, second = self.two_cameras()
        where, note = player_open.save_set([first, second])
        self.assertIsNotNone(where, note)
        self.assertTrue(note)
        self.assertNotIn("{", note)

    def test_a_saved_set_reopens_as_the_pair(self) -> None:
        """The round trip §1.3 exists for. Skipped until `multi.write_set` lands."""
        if not callable(getattr(multi, "write_set", None)):
            self.skipTest("multi.write_set is not written yet")
        first, second = self.two_cameras()
        where, note = player_open.save_set(
            [first, second], audio_index=1, offsets=[0, -1480]
        )
        self.assertIsNotNone(where, note)
        assert where is not None
        recording, trouble = player_open.load(where)
        self.assertIsNotNone(recording, trouble)
        assert recording is not None
        self.assertEqual([s.file for s in recording.screens], [first, second])
        self.assertEqual(recording.audio_file, second)

    # -- what the round trip used to lose ----------------------------------

    def portrait_pair(self) -> tuple[Path, Path]:
        """A landscape camera and a portrait one, both at 25 fps.

        The shapes of the real pair in `scratch/twofinal` - 2560x1440 beside a rotated
        1440x2560 - at a size a test can afford, and at a rate that is not
        `engine.DEFAULT_FRAMERATE`, because 60 would hide the second half of the defect.
        """
        wide, tall = self.folder / "камера_А.mp4", self.folder / "камера_Б.mp4"
        for target, size in ((wide, "160x90"), (tall, "90x160")):
            problem = _make_clip(target, rate=25, size=size)
            if problem:
                self.skipTest(f"no test clip: {problem}")
        return wide, tall

    def test_a_saved_set_keeps_the_frame_size_and_the_frame_rate(self) -> None:
        """THE DEFECT: a portrait camera came back landscape, and 25 fps came back 60.

        Measured before the fix on the real two-screen recording in
        `scratch/twofinal`: saved from (2560, 1440) + (1440, 2560), reopened as
        (1920, 1080) + (1920, 1080), which is `engine.NOMINAL_DESKTOP` standing in for
        a size nobody wrote. On a 25 fps pair made with the bundled ffmpeg the rate
        came back 60.0 and the frame step moved 17 ms instead of 40.
        """
        if not callable(getattr(multi, "write_set", None)):
            self.skipTest("multi.write_set is not written yet")
        wide, tall = self.portrait_pair()

        opened, note = player_open.recording_from_files([wide, tall])
        assert opened is not None
        self.assertEqual([s.size for s in opened.screens], [(160, 90), (90, 160)], note)

        where, note = player_open.save_set([wide, tall], offsets=[0, -1480])
        self.assertIsNotNone(where, note)
        assert where is not None

        back, trouble = player_open.load(where)
        self.assertIsNotNone(back, trouble)
        assert back is not None
        self.assertEqual(
            [s.size for s in back.screens], [(160, 90), (90, 160)],
            "the saved set forgot its own shape",
        )
        self.assertAlmostEqual(back.framerate, 25.0, places=3)
        self.assertEqual(max(1, round(1000.0 / back.framerate)), 40)

    def test_the_sizes_the_caller_measured_are_believed_and_nothing_is_probed(
        self,
    ) -> None:
        """The window layer has already probed every file; one measurement is enough."""
        seen: list[dict] = []

        def writer(path, files, *, sizes=None, framerate=None, **rest):
            seen.append({"sizes": list(sizes or []), "framerate": framerate})
            return ""

        self.use_writer(writer)
        self.addCleanup(setattr, paths, "ffprobe_exe", paths.ffprobe_exe)

        def refuse(override=None):
            raise paths.BinaryNotFound("nothing here should ask for ffprobe")

        paths.ffprobe_exe = refuse
        first, second = self.two_cameras()
        where, note = player_open.save_set(
            [first, second], sizes=[(2560, 1440), (1440, 2560)], framerate=25.0
        )
        self.assertIsNotNone(where, note)
        self.assertEqual(seen[0]["sizes"], [(2560, 1440), (1440, 2560)])
        self.assertEqual(seen[0]["framerate"], 25.0)

    def test_a_file_nobody_could_measure_is_written_with_no_size_at_all(self) -> None:
        """A stand-in written into an index is read as a measurement for ever after."""
        seen: list[dict] = []

        def writer(path, files, *, sizes=None, framerate=None, **rest):
            seen.append({"sizes": list(sizes or []), "framerate": framerate})
            return ""

        self.use_writer(writer)
        self.addCleanup(setattr, paths, "ffprobe_exe", paths.ffprobe_exe)

        def refuse(override=None):
            raise paths.BinaryNotFound("ffprobe.exe was not found (tried: nowhere)")

        paths.ffprobe_exe = refuse
        first, second = self.two_cameras()
        where, note = player_open.save_set([first, second])
        self.assertIsNotNone(where, note)
        self.assertEqual(seen[0]["sizes"], [])
        self.assertIsNone(seen[0]["framerate"])

    def test_a_size_that_is_not_one_is_measured_rather_than_passed_on(self) -> None:
        """`(0, 1080)` is not a frame size, so the file is probed after all."""
        seen: list[dict] = []

        def writer(path, files, *, sizes=None, framerate=None, **rest):
            seen.append({"sizes": list(sizes or [])})
            return ""

        self.use_writer(writer)
        first, second = self.two_cameras()
        where, note = player_open.save_set(
            [first, second], sizes=[(0, 1080), "nonsense"]
        )
        self.assertIsNotNone(where, note)
        self.assertEqual(seen[0]["sizes"], [(160, 90), (160, 90)])

    def test_a_target_that_is_already_there_is_stepped_over_not_through(self) -> None:
        """THE DEFECT: `target=` was taken as given and written straight through.

        A caller pointing at a folder of recordings could land on a two-screen take's
        own index, and `write_set` would replace its desktop rectangles with a pair
        that has none - from a button that promised to *save* something.
        """
        def writer(path, files, **rest):
            Path(path).write_text("the new set", encoding="utf-8")
            return ""

        self.use_writer(writer)
        first, second = self.two_cameras()
        theirs = self.folder / f"чужой{multi.SIDECAR_SUFFIX}"
        theirs.write_text("somebody else's index", encoding="utf-8")

        where, note = player_open.save_set([first, second], target=theirs)
        self.assertIsNotNone(where, note)
        assert where is not None
        self.assertNotEqual(where, theirs)
        self.assertEqual(where.name, f"чужой-2{multi.SIDECAR_SUFFIX}")
        self.assertEqual(
            theirs.read_text(encoding="utf-8"), "somebody else's index"
        )

    def test_a_target_nobody_has_taken_is_written_exactly_there(self) -> None:
        self.use_writer(lambda path, files, **rest: "")
        first, second = self.two_cameras()
        wanted = self.folder / f"набор{multi.SIDECAR_SUFFIX}"
        where, note = player_open.save_set([first, second], target=wanted)
        self.assertEqual(where, wanted, note)

    # -- and the other half of it: a name that is ours ---------------------

    def test_pressing_save_twice_on_one_pair_leaves_one_index(self) -> None:
        """THE REGRESSION: every taken name was stepped over, this set's own too.

        Measured with the real writer on copies of the real pair in
        `scratch/twofinal`: three presses of Save left
        `..._screen0.screenrec.json`, `..._screen0-2.screenrec.json` and
        `..._screen0-3.screenrec.json`, `library.find_entries` listed the one
        recording as three rows, and `most_recent` pointed at the third. Save is not
        Save As; nudging an offset and pressing it again is not a second set.

        The real `multi.write_set` on purpose - the index on disk has to be a real
        index for `written_by_the_player_for` to recognise it as this set's own.
        """
        first, second = self.two_cameras()
        one, note = player_open.save_set([first, second], offsets=[0, 0])
        self.assertIsNotNone(one, note)
        assert one is not None
        two, note = player_open.save_set([first, second], offsets=[0, -1480])
        self.assertEqual(two, one, note)
        three, note = player_open.save_set([first, second], offsets=[0, -1480])
        self.assertEqual(three, one, note)
        self.assertEqual(
            sorted(p.name for p in self.folder.glob(f"*{multi.SIDECAR_SUFFIX}")),
            [f"camera_a{multi.SIDECAR_SUFFIX}"],
            "Save wrote a second index for one pair",
        )
        body = json.loads(one.read_text(encoding="utf-8"))
        self.assertEqual(
            [s.get("offset_ms") for s in body.get("screens", [])],
            [0, -1480],
            "the later Save landed somewhere else",
        )

    def test_a_recorder_made_index_of_these_very_files_is_stepped_over(self) -> None:
        """Same two files, and still not ours: it holds the only copy of the geometry.

        The file list alone cannot decide this. A viewer who pairs the two `.mp4`s of
        a recorder-made take by hand is saving a set over exactly the files that
        take's own index names, and `write_set` writes no `desktop` - so writing
        through would trade a recording's rectangles for a pair that has none.
        """
        first, second = self.two_cameras()
        theirs = self.folder / f"camera_a{multi.SIDECAR_SUFFIX}"
        theirs.write_text(
            json.dumps(
                {
                    "version": 2,
                    "recorded": "2026-11-04T13:01:13",
                    "audio": first.name,
                    "screens": [
                        {
                            "file": first.name, "monitor": 0,
                            "desktop": [0, 0, 2560, 1440], "size": [2560, 1440],
                            "rotation": 1, "primary": True,
                        },
                        {
                            "file": second.name, "monitor": 1,
                            "desktop": [2560, 0, 1440, 2560], "size": [1440, 2560],
                            "rotation": 2, "primary": False,
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        before = theirs.read_text(encoding="utf-8")
        where, note = player_open.save_set([first, second])
        self.assertIsNotNone(where, note)
        assert where is not None
        self.assertEqual(where.name, f"camera_a-2{multi.SIDECAR_SUFFIX}")
        self.assertEqual(theirs.read_text(encoding="utf-8"), before)

        # And the step settles there rather than climbing: the second press finds the
        # plain name still theirs and `-2` now its own.
        again, note = player_open.save_set([first, second])
        self.assertEqual(again, where, note)
        self.assertEqual(theirs.read_text(encoding="utf-8"), before)

    def test_an_index_that_names_another_pair_is_stepped_over(self) -> None:
        """Ours by format and somebody else's by content: a different second camera."""
        first, second = self.two_cameras()
        third = self.copy_clip(0, "camera_c.mp4")
        taken, note = player_open.save_set([first, third], offsets=[0, 700])
        self.assertIsNotNone(taken, note)
        assert taken is not None
        self.assertEqual(taken.name, f"camera_a{multi.SIDECAR_SUFFIX}")
        before = taken.read_text(encoding="utf-8")

        where, note = player_open.save_set([first, second], offsets=[0, -1480])
        self.assertIsNotNone(where, note)
        assert where is not None
        self.assertEqual(where.name, f"camera_a-2{multi.SIDECAR_SUFFIX}")
        self.assertEqual(
            taken.read_text(encoding="utf-8"), before, "the first pair was written over"
        )

    def test_a_target_naming_this_set_s_own_index_is_written_where_it_was_told(
        self,
    ) -> None:
        """A Save As that has already asked about an overwrite is not second-guessed."""
        first, second = self.two_cameras()
        wanted = self.folder / f"набор{multi.SIDECAR_SUFFIX}"
        one, note = player_open.save_set([first, second], target=wanted)
        self.assertEqual(one, wanted, note)
        two, note = player_open.save_set(
            [first, second], target=wanted, offsets=[0, 250]
        )
        self.assertEqual(two, wanted, note)
        body = json.loads(wanted.read_text(encoding="utf-8"))
        self.assertEqual(
            [s.get("offset_ms") for s in body.get("screens", [])], [0, 250]
        )


class UnreachableFolderTest(RecordingFolder):
    """§10 of the night's audit: a folder that is there and will not answer.

    `pathlib` swallows a "not found" and re-raises everything else, so a share that has
    simply gone is quiet while one that is reachable and refusing - `[WinError 5]
    Access is denied`, `[WinError 64] The specified network name is no longer
    available` - came straight out of `Path.exists()`. Measured on this machine, and
    the table is in `open._answered`.

    The share cannot be made to drop to order, so `Path.stat` is made to fail the way
    Windows fails: every one of these functions says in its first line that it never
    raises, and every one of them did.
    """

    def unreachable(self, folder: Path) -> None:
        """Make everything under `folder` answer the way a dropped share does."""
        real = Path.stat
        self.addCleanup(setattr, Path, "stat", real)

        def refuse(self, *rest, **more):
            if str(folder) in str(self):
                raise OSError(
                    32, "The specified network name is no longer available", None, 64
                )
            return real(self, *rest, **more)

        Path.stat = refuse

    def test_nothing_raises_out_of_the_file_layer(self) -> None:
        share = self.folder / "сетевая"
        share.mkdir()
        film = share / "камера_А.mp4"
        film.write_bytes(b"not really an mp4")
        index = share / f"набор{multi.SIDECAR_SUFFIX}"
        index.write_text(
            '{"version": 2, "screens": [{"file": "камера_А.mp4"}]}', encoding="utf-8"
        )
        self.unreachable(share)

        for name, call in (
            ("load a folder", lambda: player_open.load(share)),
            ("load a file", lambda: player_open.load(film)),
            ("scan", lambda: player_open.scan(share)),
            ("most_recent", lambda: player_open.most_recent(share)),
            ("recording_from_files", lambda: player_open.recording_from_files([film])),
            ("save_set", lambda: player_open.save_set([film])),
            ("read_sidecar", lambda: multi.read_sidecar(index)),
            ("find_sets", lambda: multi.find_sets(share)),
        ):
            with self.subTest(call=name):
                try:
                    call()
                except Exception as exc:  # noqa: BLE001 - that is the whole test
                    self.fail(f"{name} raised {type(exc).__name__}: {exc}")

    def test_the_refusal_is_a_sentence_with_the_system_s_own_words_in_it(self) -> None:
        share = self.folder / "сетевая"
        share.mkdir()
        film = share / "камера_А.mp4"
        film.write_bytes(b"not really an mp4")
        self.unreachable(share)

        _recording, problem = player_open.load(film)
        self.assertIn("камера_А.mp4", problem)
        self.assertIn("WinError 64", problem)
        self.assertNotIn("{", problem)

    def test_a_file_that_cannot_be_checked_still_goes_into_the_set(self) -> None:
        """Dropping it would save a pair as one camera, quietly."""
        seen: list[list[Path]] = []

        def writer(path, files, **rest):
            seen.append(list(files))
            return ""

        had = getattr(multi, "write_set", None)
        self.addCleanup(setattr, multi, "write_set", had)
        multi.write_set = writer

        share = self.folder / "сетевая"
        share.mkdir()
        here = self.copy_clip(0, "камера_А.mp4")
        there = share / "камера_Б.mp4"
        there.write_bytes(b"not really an mp4")
        self.unreachable(share)

        where, note = player_open.save_set([here, there], offsets=[0, -1480])
        self.assertIsNotNone(where, note)
        self.assertEqual(seen[0], [here, there])
        self.assertIn("камера_Б.mp4", note)


class ProbeLanguageTest(RecordingFolder):
    """What ffprobe could not say, said in the language in force.

    These are f-strings no catalogue could ever hold a key for, and they land in the
    same box as every other sentence here: a viewer who opens a file whose rate cannot
    be read is told so in Russian now.
    """

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(i18n.set_language, i18n.language())

    def notes(self, path: Path) -> str:
        return player_open._probe_video(path)[1]

    def test_a_file_ffprobe_cannot_read_is_reported_in_both_languages(self) -> None:
        notes = self.folder / "заметки.txt"
        notes.write_text("not a video at all", encoding="utf-8")

        i18n.set_language(i18n.LANG_EN)
        english = self.notes(notes)
        i18n.set_language(i18n.LANG_RU)
        russian = self.notes(notes)

        self.assertTrue(english, "ffprobe read a text file as a video")
        self.assertNotEqual(russian, english)
        self.assertIn("заметки.txt", russian)
        self.assertNotIn("{", russian)

    def test_a_missing_ffprobe_is_reported_in_both_languages(self) -> None:
        self.addCleanup(setattr, paths, "ffprobe_exe", paths.ffprobe_exe)

        def refuse(override=None):
            raise paths.BinaryNotFound("ffprobe.exe was not found (tried: nowhere)")

        paths.ffprobe_exe = refuse
        film = self.folder / "камера_А.mp4"
        film.write_bytes(b"not really an mp4")

        i18n.set_language(i18n.LANG_EN)
        english = self.notes(film)
        i18n.set_language(i18n.LANG_RU)
        russian = self.notes(film)
        self.assertNotEqual(russian, english)
        self.assertIn("ffprobe", russian, "the program's name is not a word")
        self.assertNotIn("{", russian)


class NothingToOpenTest(unittest.TestCase):
    """The player has to appear even when there is nothing in the folder.

    Reported on 2026-11-05: the old recordings were deleted and "the player would not
    start". Reproduced: `run([])` looked in the output folder, found nothing, printed
    "В …\\ScreenRec нет записей." and returned 2 - and the shipped launcher uses
    `pythonw`, which has no console, so clicking the shortcut did nothing visible at
    all. Its Open menu, its library and drag-and-drop all work with no recording
    loaded, so refusing to appear was never right.
    """

    def setUp(self) -> None:
        if QtWidgets.QApplication.instance() is None:
            QtWidgets.QApplication([])
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_an_empty_player_can_be_built(self) -> None:
        windows, problem = player_open.empty_player(self.folder)
        self.addCleanup(lambda: windows and windows.close_all())
        self.assertIsNotNone(windows, problem)
        self.assertFalse(problem, problem)

    def test_it_carries_the_controls_that_work_without_a_recording(self) -> None:
        windows, _problem = player_open.empty_player(self.folder)
        self.addCleanup(lambda: windows and windows.close_all())
        transport = getattr(windows, "transport", None)
        self.assertIsNotNone(transport, "an empty player still needs its transport")
        self.assertTrue(hasattr(transport, "open_button"))

    def test_an_empty_recording_is_not_reported_as_a_failure(self) -> None:
        """"Not one file of this recording could be opened" is for files that failed.

        With no files there is nothing that failed, and saying so would put a
        complaint in front of somebody who has not chosen anything yet.
        """
        windows, _problem = player_open.empty_player(self.folder)
        self.addCleanup(lambda: windows and windows.close_all())
        clock = getattr(windows, "engine", None) or getattr(windows, "_engine", None)
        said = []
        for holder in (clock, getattr(clock, "engine", None)):
            problems = getattr(holder, "startup_problems", None)
            if callable(problems):
                said.extend(problems() or [])
        self.assertEqual(said, [], f"an empty player complained: {said}")


if __name__ == "__main__":
    unittest.main()
