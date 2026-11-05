"""Checks for `screenrec.multi`: the index both halves of the player read.

Nothing here records anything. `MultiSession` is built by hand with a stub session, so
what is checked is the one thing this module decides on its own - what goes into the
`.screenrec.json` and what comes back out of it.

The check that matters most is `test_a_recorder_set_is_what_it_was_but_version_2`.
`docs/PLAYER_SOURCES.md` adds a version 2 index with per-screen offsets and absolute
paths, and the recorder is finished: its own output had to gain the new version number
and nothing else. That test holds the whole document, byte for byte, so a field added to
the recorder's sidecar by accident cannot pass unnoticed.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from screenrec import multi, recorder  # noqa: E402
from screenrec.winapi import Monitor  # noqa: E402


def monitor(index: int = 0, **changes) -> Monitor:
    fields = {
        "index": index,
        "device_name": rf"\\.\DISPLAY{index + 1}",
        "x": index * 2560,
        "y": 0,
        "width": 2560,
        "height": 1440,
        "rotation": 0,
        "adapter": "Test adapter",
    }
    fields.update(changes)
    return Monitor(**fields)


class _StubSession:
    """Stands in for a `RecordingSession` that has already finished.

    `write_sidecar` asks a take for nothing but "did you run"; a take whose session is
    None is one that failed to start and is left out of the index.
    """


class SidecarWritingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _session(self, screens: int = 2) -> multi.MultiSession:
        session = multi.MultiSession(
            "ffmpeg.exe", self.folder / "Take.mp4", lambda *a, **k: None
        )
        for index in range(screens):
            take = session.add(recorder.VideoSource(monitor=monitor(index)))
            take.session = _StubSession()
        session.started_at = datetime(2026, 11, 4, 18, 20, 0)
        return session

    def test_a_recorder_set_is_what_it_was_but_version_2(self) -> None:
        session = self._session()
        written = [take.final for take in session.takes]
        self.assertEqual(session.write_sidecar(written), "")

        text = session.sidecar_path().read_text(encoding="utf-8")
        expected = json.dumps(
            {
                "version": 2,
                "recorded": "2026-11-04T18:20:00",
                "audio": "Take_screen0.mp4",
                "screens": [
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
                        "desktop": [2560, 0, 2560, 1440],
                        "size": [2560, 1440],
                        "rotation": 0,
                        "primary": False,
                    },
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        # Byte for byte, keys and order included. The only thing the version 2 work was
        # allowed to change in the recorder's own output is the number on the first line.
        self.assertEqual(text, expected)

    def test_the_recorder_writes_no_offsets_at_all(self) -> None:
        # §1.3: "The recorder never writes `offset_ms`, because its own screens start
        # together and a field that is always 0 is a field that rots." The two processes
        # were measured 17 ms apart, which is one frame and nothing to correct.
        session = self._session()
        session.write_sidecar([take.final for take in session.takes])
        document = json.loads(session.sidecar_path().read_text(encoding="utf-8"))
        for screen in document["screens"]:
            self.assertNotIn("offset_ms", screen)

    def test_a_screen_that_never_ran_is_left_out(self) -> None:
        session = self._session()
        session.takes[1].session = None
        self.assertEqual(session.write_sidecar([session.takes[0].final]), "")
        document = json.loads(session.sidecar_path().read_text(encoding="utf-8"))
        self.assertEqual([s["file"] for s in document["screens"]], ["Take_screen0.mp4"])


class ResolveFileTests(unittest.TestCase):
    """The one rule about where a `"file"` entry points, in the one place it lives."""

    def test_a_bare_name_is_beside_the_index(self) -> None:
        self.assertEqual(
            multi.resolve_file(Path("B:/shoot"), "camera_a.mp4"),
            Path("B:/shoot/camera_a.mp4"),
        )

    def test_an_absolute_path_is_used_as_it_stands(self) -> None:
        self.assertEqual(
            multi.resolve_file(Path("B:/shoot"), "D:/other/camera_b.mp4"),
            Path("D:/other/camera_b.mp4"),
        )

    def test_a_subfolder_is_still_relative_to_the_index(self) -> None:
        self.assertEqual(
            multi.resolve_file(Path("B:/shoot"), "raw/camera_b.mp4"),
            Path("B:/shoot/raw/camera_b.mp4"),
        )


class WriteSetTests(unittest.TestCase):
    """The player's "Save this set": two files, an audio choice and two offsets."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)
        self._elsewhere = tempfile.TemporaryDirectory()
        self.elsewhere = Path(self._elsewhere.name)

    def tearDown(self) -> None:
        self._temp.cleanup()
        self._elsewhere.cleanup()

    def _file(self, folder: Path, name: str) -> Path:
        path = folder / name
        path.write_bytes(b"not really an mp4")
        return path

    def test_a_pair_in_one_folder_is_written_with_bare_names(self) -> None:
        first = self._file(self.folder, "camera_a.mp4")
        second = self._file(self.folder, "camera_b.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"

        self.assertEqual(multi.write_set(target, [first, second], 0, [0, -1480]), "")
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], 2)
        self.assertEqual(document["audio"], "camera_a.mp4")
        self.assertEqual(
            [s["file"] for s in document["screens"]], ["camera_a.mp4", "camera_b.mp4"]
        )
        self.assertEqual([s["offset_ms"] for s in document["screens"]], [0, -1480])

    def test_a_file_from_another_folder_is_written_absolute(self) -> None:
        first = self._file(self.folder, "camera_a.mp4")
        second = self._file(self.elsewhere, "camera_b.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"

        multi.write_set(target, [first, second], 1, [0, 250])
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["screens"][0]["file"], "camera_a.mp4")
        self.assertEqual(Path(document["screens"][1]["file"]), second)
        # The audio choice is written in the same spelling as the screen it names, so
        # the loader pairs them by string and cannot miss.
        self.assertEqual(document["audio"], document["screens"][1]["file"])

    def test_the_file_it_writes_reads_back_with_no_complaint(self) -> None:
        first = self._file(self.folder, "camera_a.mp4")
        second = self._file(self.elsewhere, "camera_b.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, second], 0, [0, -1480])

        raw, problem = multi.read_sidecar(target)
        self.assertEqual(problem, "")  # the absolute path is found, not reported missing
        self.assertEqual(len(raw["screens"]), 2)

    def test_a_file_that_has_moved_away_is_named_in_full(self) -> None:
        first = self._file(self.folder, "camera_a.mp4")
        second = self.elsewhere / "camera_b.mp4"  # never created
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, second], 0, [0, 0])

        _raw, problem = multi.read_sidecar(target)
        self.assertIn(str(second), problem)

    def test_offsets_may_be_left_out(self) -> None:
        first = self._file(self.folder, "camera_a.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first])
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["screens"][0]["offset_ms"], 0)

    def test_a_short_offset_list_is_padded_not_misaligned(self) -> None:
        first = self._file(self.folder, "camera_a.mp4")
        second = self._file(self.folder, "camera_b.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, second], 0, [700])
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual([s["offset_ms"] for s in document["screens"]], [700, 0])

    def test_the_frame_sizes_are_written_when_the_caller_measured_them(self) -> None:
        """§1.3's own example index carries a `"size"` on every entry, and it must.

        `engine._screen_from_entry` falls back to a nominal 1920x1080 for an entry that
        names neither a desktop rectangle nor a size, and everything downstream reads
        `Screen.size`: the layout scores, the reason line under each card, the drawn
        thumbnails and the window rectangles. A saved pair that forgot it was a 2560
        landscape beside a 1440 portrait came back as two 16:9 windows.
        """
        first = self._file(self.folder, "camera_a.mp4")
        second = self._file(self.folder, "camera_b.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"

        multi.write_set(
            target, [first, second], 0, [0, -1480], [(2560, 1440), (1440, 2560)]
        )
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(
            [s["size"] for s in document["screens"]], [[2560, 1440], [1440, 2560]]
        )
        # Still no desktop rectangle: where these files were shot is the thing that
        # really is unknown, and inventing one is the lie this writer refuses.
        self.assertNotIn("desktop", document["screens"][0])

    def test_a_size_nobody_measured_is_left_out_rather_than_invented(self) -> None:
        first = self._file(self.folder, "camera_a.mp4")
        second = self._file(self.folder, "camera_b.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"

        # A pair that is not a frame size, and a list one short because the second file
        # could not be probed. Neither may become a measurement.
        multi.write_set(target, [first, second], 0, None, [(0, 1080)])
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertNotIn("size", document["screens"][0])
        self.assertNotIn("size", document["screens"][1])

    def test_a_saved_size_survives_the_round_trip_into_the_player(self) -> None:
        from screenrec.player import engine as player_engine

        first = self._file(self.folder, "camera_a.mp4")
        second = self._file(self.folder, "camera_b.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(
            target, [first, second], 0, [0, 0], [(2560, 1440), (1440, 2560)]
        )

        recording, problem = player_engine.Recording.load(target)
        assert recording is not None
        self.assertEqual(problem, "")
        self.assertEqual(
            [s.size for s in recording.screens], [(2560, 1440), (1440, 2560)]
        )

    def test_an_audio_index_that_is_not_a_file_falls_back_to_the_first(self) -> None:
        first = self._file(self.folder, "camera_a.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first], 7, [0])
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["audio"], "camera_a.mp4")

    def test_nothing_to_save_is_said_rather_than_written(self) -> None:
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        self.assertTrue(multi.write_set(target, []))
        self.assertFalse(target.exists())

    def test_a_folder_that_is_not_there_is_reported_not_raised(self) -> None:
        first = self._file(self.folder, "camera_a.mp4")
        target = self.folder / "no_such_folder" / f"shoot{multi.SIDECAR_SUFFIX}"
        problem = multi.write_set(target, [first], 0, [0])
        self.assertTrue(problem)

    def test_nothing_to_save_is_said_in_the_language_in_force(self) -> None:
        """It is read in a message box beside the player's Save button."""
        from screenrec import i18n

        self.addCleanup(i18n.set_language, i18n.language())
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        i18n.set_language(i18n.LANG_EN)
        english = multi.write_set(target, [])
        i18n.set_language(i18n.LANG_RU)
        russian = multi.write_set(target, [])
        self.assertTrue(english and russian)
        self.assertNotEqual(russian, english)
        self.assertNotIn("{", russian)

    # -- the frame rate, which used to be written nowhere at all -----------

    def test_the_frame_rate_is_written_and_comes_back(self) -> None:
        """A 25 fps pair reopened at 60, and the frame step moved 17 ms, not 40.

        Measured before the fix on a pair made with the bundled ffmpeg at
        `testsrc=rate=25`: `save_set` then `load` gave `recording.framerate` 60.0, and
        `SyncEngine`'s step is `max(1, round(1000 / framerate))` - 17 ms against the
        40 ms a frame of that footage really is.
        """
        from screenrec.player import engine as player_engine

        first = self._file(self.folder, "camera_a.mp4")
        second = self._file(self.folder, "camera_b.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"

        self.assertEqual(
            multi.write_set(target, [first, second], 0, [0, 0], None, 25.0), ""
        )
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["framerate"], 25.0)

        recording, problem = player_engine.Recording.load(target)
        assert recording is not None
        self.assertEqual(problem, "")
        self.assertEqual(recording.framerate, 25.0)
        self.assertEqual(max(1, round(1000.0 / recording.framerate)), 40)

    def test_a_rate_nobody_measured_is_left_out_rather_than_invented(self) -> None:
        """Left out reads as "unknown"; a number reads as a measurement, for ever."""
        first = self._file(self.folder, "camera_a.mp4")
        for rate in (None, 0, -25.0, float("nan"), float("inf"), "25 fps", True):
            with self.subTest(rate=rate):
                target = self.folder / f"r{rate}{multi.SIDECAR_SUFFIX}"
                self.assertEqual(
                    multi.write_set(target, [first], 0, [0], None, rate), ""
                )
                document = json.loads(target.read_text(encoding="utf-8"))
                self.assertNotIn("framerate", document)

    # -- a relative path, and what it means once it is in the file ---------

    def test_a_relative_file_is_written_so_the_index_can_find_it_again(self) -> None:
        """THE DEFECT: `_index_spelling` took a relative path at face value.

        `resolve_file` resolves anything that is not absolute **beside the index**, so a
        path relative to the working directory went in meaning one file and came out
        meaning another. Measured before the fix with the working directory on the
        footage and the index one folder along: `вложенная\\камера_Б.mp4` was written
        verbatim and `read_sidecar` reported it missing.
        """
        import os

        shoot = self.folder / "съёмка"
        shoot.mkdir()
        (shoot / "камера_А.mp4").write_bytes(b"not really an mp4")
        output = self.folder / "вывод"
        output.mkdir()
        target = output / f"набор{multi.SIDECAR_SUFFIX}"

        here = Path.cwd()
        self.addCleanup(os.chdir, here)
        os.chdir(shoot)
        self.assertEqual(multi.write_set(target, [Path("камера_А.mp4")]), "")
        os.chdir(here)

        document = json.loads(target.read_text(encoding="utf-8"))
        written = document["screens"][0]["file"]
        self.assertTrue(
            Path(written).is_absolute(),
            f"{written!r} is read beside the index, which is not where the file is",
        )
        _raw, problem = multi.read_sidecar(target)
        self.assertEqual(problem, "")
        self.assertEqual(
            multi.resolve_file(target.parent, written), shoot / "камера_А.mp4"
        )

    def test_a_file_in_the_working_directory_is_still_a_bare_name(self) -> None:
        """The other half: relative, and really beside the index, stays short."""
        import os

        shoot = self.folder / "съёмка"
        shoot.mkdir()
        (shoot / "камера_А.mp4").write_bytes(b"not really an mp4")
        target = shoot / f"набор{multi.SIDECAR_SUFFIX}"

        here = Path.cwd()
        self.addCleanup(os.chdir, here)
        os.chdir(shoot)
        self.assertEqual(multi.write_set(target, [Path("камера_А.mp4")]), "")
        os.chdir(here)

        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["screens"][0]["file"], "камера_А.mp4")


class SavedSetOwnershipTest(unittest.TestCase):
    """Whose index is this? - the question `open.save_set` asks before it overwrites.

    Two documents wear `SIDECAR_SUFFIX`. A take's own index carries the only copy of
    where its screens were; a set the player saved carries nothing a second Save would
    not write again. Stepping over both left one pair listed in the library three
    times after three presses of Save; writing through both would hand a recording's
    desktop rectangles to a writer that writes none. So both halves are asked here.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)
        self._elsewhere = tempfile.TemporaryDirectory()
        self.elsewhere = Path(self._elsewhere.name)

    def tearDown(self) -> None:
        self._temp.cleanup()
        self._elsewhere.cleanup()

    def _file(self, folder: Path, name: str) -> Path:
        path = folder / name
        path.write_bytes(b"not really an mp4")
        return path

    def _pair(self) -> tuple[Path, Path]:
        return (
            self._file(self.folder, "camera_a.mp4"),
            self._file(self.folder, "camera_b.mp4"),
        )

    def test_what_write_set_wrote_is_this_set_s_own(self) -> None:
        first, second = self._pair()
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        self.assertEqual(multi.write_set(target, [first, second], 0, [0, -1480]), "")
        self.assertTrue(multi.written_by_the_player_for(target, [first, second]))

    def test_a_file_from_another_folder_is_matched_through_resolve_file(self) -> None:
        """One is a bare name beside the index and the other an absolute path."""
        first = self._file(self.folder, "camera_a.mp4")
        second = self._file(self.elsewhere, "camera_b.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, second], 0, [0, 250])
        self.assertTrue(multi.written_by_the_player_for(target, [first, second]))

    def test_a_recorder_made_index_of_the_same_files_is_not_ours(self) -> None:
        """`desktop` is the only copy of where those screens were. Same files, no."""
        first, second = self._pair()
        target = self.folder / f"take{multi.SIDECAR_SUFFIX}"
        target.write_text(
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
                }
            ),
            encoding="utf-8",
        )
        self.assertFalse(multi.written_by_the_player_for(target, [first, second]))

    def test_the_recorder_s_real_sidecar_is_not_ours_either(self) -> None:
        """Built by `MultiSession`, not by hand, so the shape is the real one."""
        session = multi.MultiSession(
            "ffmpeg.exe", self.folder / "Take.mp4", lambda *a, **k: None
        )
        for index in (0, 1):
            take = session.add(recorder.VideoSource(monitor=monitor(index)))
            take.session = _StubSession()
            take.final.write_bytes(b"not really an mp4")
        written = [t.final for t in session.takes]
        self.assertEqual(session.write_sidecar(written), "")
        self.assertFalse(
            multi.written_by_the_player_for(session.sidecar_path(), written)
        )

    def test_an_index_carrying_both_an_offset_and_a_desktop_is_not_ours(self) -> None:
        """Neither of today's two writers wrote this, so it is nobody's to overwrite.

        `offset_ms` alone tells the recorder's index from the player's, and it is what
        the two tests above really turn on: remove the `RECORDER_ONLY_KEYS` half and
        both still pass, because `write_sidecar` writes no offsets. This document is
        the case that half exists for - a hand-edited or merged index that carries an
        offset *and* the rectangles - and writing it through would still cost somebody
        the only record of where those screens were.
        """
        first, second = self._pair()
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, second], 0, [0, -1480])
        document = json.loads(target.read_text(encoding="utf-8"))
        document["screens"][0]["desktop"] = [0, 0, 2560, 1440]
        target.write_text(json.dumps(document), encoding="utf-8")
        self.assertFalse(multi.written_by_the_player_for(target, [first, second]))

    def test_an_index_of_another_pair_is_not_ours(self) -> None:
        first, second = self._pair()
        third = self._file(self.folder, "camera_c.mp4")
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, third], 0, [0, 700])
        self.assertFalse(multi.written_by_the_player_for(target, [first, second]))

    def test_the_same_files_in_the_other_order_are_another_set(self) -> None:
        """Which screen is stream 0 decides the offsets and which carries the sound."""
        first, second = self._pair()
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, second], 0, [0, -1480])
        self.assertFalse(multi.written_by_the_player_for(target, [second, first]))

    def test_a_set_of_a_different_length_is_not_ours(self) -> None:
        first, second = self._pair()
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, second], 0, [0, -1480])
        self.assertFalse(multi.written_by_the_player_for(target, [first]))

    def test_an_entry_with_no_offset_is_not_ours(self) -> None:
        """§1.3: the recorder never writes `offset_ms`, and `write_set` always does."""
        first, second = self._pair()
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, second], 0, [0, -1480])
        document = json.loads(target.read_text(encoding="utf-8"))
        document["screens"][1].pop("offset_ms")
        target.write_text(json.dumps(document), encoding="utf-8")
        self.assertFalse(multi.written_by_the_player_for(target, [first, second]))

    def test_nothing_readable_there_is_not_ours_and_does_not_raise(self) -> None:
        """A Save button is the caller: every one of these is a quiet False."""
        first, second = self._pair()
        missing = self.folder / f"gone{multi.SIDECAR_SUFFIX}"
        self.assertFalse(multi.written_by_the_player_for(missing, [first, second]))

        junk = self.folder / f"junk{multi.SIDECAR_SUFFIX}"
        junk.write_text("the recorder's own", encoding="utf-8")
        self.assertFalse(multi.written_by_the_player_for(junk, [first, second]))

        a_list = self.folder / f"list{multi.SIDECAR_SUFFIX}"
        a_list.write_text('["camera_a.mp4"]', encoding="utf-8")
        self.assertFalse(multi.written_by_the_player_for(a_list, [first, second]))

        no_screens = self.folder / f"empty{multi.SIDECAR_SUFFIX}"
        no_screens.write_text('{"version": 2}', encoding="utf-8")
        self.assertFalse(multi.written_by_the_player_for(no_screens, [first, second]))

        strings = self.folder / f"strings{multi.SIDECAR_SUFFIX}"
        strings.write_text('{"screens": ["camera_a.mp4", 2]}', encoding="utf-8")
        self.assertFalse(multi.written_by_the_player_for(strings, [first, second]))

        folder_not_file = self.folder / f"folder{multi.SIDECAR_SUFFIX}"
        folder_not_file.mkdir()
        self.assertFalse(
            multi.written_by_the_player_for(folder_not_file, [first, second])
        )

        not_utf8 = self.folder / f"bytes{multi.SIDECAR_SUFFIX}"
        not_utf8.write_bytes(b"\xff\xfe{")
        self.assertFalse(multi.written_by_the_player_for(not_utf8, [first, second]))

    def test_no_files_is_not_ours(self) -> None:
        """Nothing to save means nothing to recognise; `save_set` refuses earlier."""
        first, second = self._pair()
        target = self.folder / f"shoot{multi.SIDECAR_SUFFIX}"
        multi.write_set(target, [first, second], 0, [0, 0])
        self.assertFalse(multi.written_by_the_player_for(target, []))


class ReadSidecarTests(unittest.TestCase):
    """What `read_sidecar` does with an index somebody has been editing by hand."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def index(self, screens: object) -> Path:
        target = self.folder / f"рваный{multi.SIDECAR_SUFFIX}"
        target.write_text(
            json.dumps({"version": 2, "screens": screens}, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

    def test_an_entry_that_is_not_a_screen_is_skipped_not_raised(self) -> None:
        """THE DEFECT: `AttributeError: 'str' object has no attribute 'get'`.

        Out of a function whose first line says it never raises. Reproduced with
        `{"screens": ["камера_А.mp4"]}`, which is what a truncated index looks like
        once somebody has repaired the brackets by hand.
        """
        real = self.folder / "камера_А.mp4"
        real.write_bytes(b"not really an mp4")
        target = self.index([{"file": real.name, "monitor": 0}, "камера_Б.mp4", 7])

        raw, problem = multi.read_sidecar(target)
        self.assertEqual(problem, "")  # the entry that is a screen is there
        self.assertEqual(len(raw["screens"]), 3, "the document is handed over as it is")

    def test_an_index_where_nothing_is_a_screen_lists_no_screens(self) -> None:
        """Refused outright: it would otherwise reach the library as a dead row."""
        target = self.index(["камера_А.mp4", "камера_Б.mp4"])
        raw, problem = multi.read_sidecar(target)
        self.assertEqual(raw, {})
        self.assertIn(target.name, problem)
        self.assertNotIn("{", problem)

    def test_a_missing_file_is_still_reported_beside_the_junk(self) -> None:
        target = self.index([{"file": "камера_А.mp4"}, "junk"])
        raw, problem = multi.read_sidecar(target)
        self.assertTrue(raw)
        self.assertIn("камера_А.mp4", problem)


class FindSetsTests(unittest.TestCase):
    """The listing two "never raises" functions are built on."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_a_folder_that_is_not_there_is_an_empty_list(self) -> None:
        self.assertEqual(multi.find_sets(self.folder / "нет такой"), [])

    def test_an_unreachable_network_folder_is_an_empty_list(self) -> None:
        """Measured: `is_dir()` on a host that does not exist answers False in 2.5 s.

        It does not raise, so this is what a vanished share looks like from here on
        this machine. The test below covers the case where it does raise instead.
        """
        self.assertEqual(multi.find_sets(Path("//нет-такого-хоста/общая/записи")), [])

    def test_a_folder_that_stops_answering_mid_walk_is_an_empty_list(self) -> None:
        """THE DEFECT: the walk came out of `find_sets` as an exception.

        Windows raises `OSError [WinError 53] The network path was not found` from
        `stat` and from the walk itself when a share goes while it is being read -
        measured from `Path.iterdir()` and `Path.stat()` on `//no-such-host/share`,
        which raise exactly that. The share cannot be made to go away to order, so the
        filesystem is made to fail the way it does: `open.scan` and
        `library.find_entries` both promise a viewer they never raise, and both are
        this function's callers.
        """
        vanished = OSError(53, "The network path was not found")

        def gone(self, *rest, **more):
            raise vanished

        self.addCleanup(setattr, Path, "glob", Path.glob)
        Path.glob = gone
        self.assertEqual(multi.find_sets(self.folder), [])

    def test_a_file_that_goes_between_the_glob_and_the_stat_is_not_lost(self) -> None:
        """The race the old comment in `open.scan` described, made to happen.

        The listing checks `is_file()` and then sorts on `st_mtime`, so a file that
        disappears between the two took the whole folder's listing down with it. Here
        the first `stat` of each file answers and the second does not, which is that
        race exactly - and it is also how a share behaves the moment it drops.
        """
        import os

        old = self.folder / f"старый{multi.SIDECAR_SUFFIX}"
        new = self.folder / f"новый{multi.SIDECAR_SUFFIX}"
        for one in (old, new):
            one.write_text("{}", encoding="utf-8")
        os.utime(old, (1_700_000_000, 1_700_000_000))
        os.utime(new, (1_800_000_000, 1_800_000_000))

        real_stat = Path.stat
        asked: dict[str, int] = {}

        def refuse(self, *rest, **more):
            if self.name == old.name:
                asked[self.name] = asked.get(self.name, 0) + 1
                if asked[self.name] > 1:
                    raise FileNotFoundError(
                        2, "The system cannot find the file specified", None, 2
                    )
            return real_stat(self, *rest, **more)

        self.addCleanup(setattr, Path, "stat", real_stat)
        Path.stat = refuse
        self.assertEqual(multi.find_sets(self.folder), [new, old])


if __name__ == "__main__":
    unittest.main()
