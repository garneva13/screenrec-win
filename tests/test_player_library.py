"""Tests for ``player/library.py`` - what is in the recordings folder, as rows.

§1 of ``docs/PLAYER_SOURCES.md`` asks for "every `.screenrec.json` set and every loose
video, newest first, with its name, date, length and size". Every one of those words is
a decision that can be got wrong silently - a two-screen take listed as two videos, a
set called "take.screenrec", the newest recording second - so they are pinned here
rather than noticed by somebody scrolling a list.

One clip is made with the bundled ffmpeg, because a duration cannot be faked: the point
of `duration_ms` is that it reads a real container. Everything else is structure, and
structure is checked with files that only need to exist.

No widget is built anywhere in this file. That is the module's own claim - it returns
data - and a test that needed a screen to check a row would disprove it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from screenrec import multi, paths  # noqa: E402
from screenrec.player import library, open as player_open  # noqa: E402

CREATE_NO_WINDOW = 0x08000000

_media_dir: Path | None = None
_clip: Path | None = None
_ffmpeg_problem = ""


def _make_clip(target: Path) -> str:
    """One second of ``testsrc`` at 160x90. Returns a problem, or ''.

    The same fixture as `test_player_open`'s, and deliberately not imported from it:
    ``unittest discover -s tests`` imports these modules flat, so a cross-module import
    between two test files is an ImportError in the one run that matters.
    """
    try:
        ffmpeg = paths.ffmpeg_exe()
    except paths.BinaryNotFound as exc:
        return str(exc)
    command = [
        str(ffmpeg),
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi",
        "-i", "testsrc=size=160x90:rate=30:duration=1",
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
    global _media_dir, _clip, _ffmpeg_problem
    _media_dir = Path(tempfile.mkdtemp(prefix="screenrec_library_"))
    made = _media_dir / "one_second.mp4"
    _ffmpeg_problem = _make_clip(made)
    if not _ffmpeg_problem:
        _clip = made


def tearDownModule() -> None:
    if _media_dir is not None:
        shutil.rmtree(_media_dir, ignore_errors=True)


class LibraryTest(unittest.TestCase):
    """The listing itself: what is in it, in which order, and what each row says."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="screenrec_lib_case_"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    # -- building a folder to look at --------------------------------------

    def video(self, name: str, *, size: int = 2048, when: float | None = None) -> Path:
        """A file that is a video as far as the listing is concerned."""
        path = self.folder / name
        path.write_bytes(b"\0" * size)
        if when is not None:
            os.utime(path, (when, when))
        return path

    def index(self, stem: str, files: list[Path], *, recorded: str = "") -> Path:
        document = {
            "version": 1,
            "recorded": recorded or "2026-11-04T01:30:00",
            "audio": files[0].name,
            "screens": [
                {
                    "file": path.name,
                    "monitor": number,
                    "desktop": [number * 1920, 0, 1920, 1080],
                    "size": [1920, 1080],
                }
                for number, path in enumerate(files)
            ],
        }
        path = self.folder / f"{stem}{multi.SIDECAR_SUFFIX}"
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return path

    def names(self, entries: list[library.Entry]) -> list[str]:
        return [entry.name for entry in entries]

    # -- what is in the list -----------------------------------------------

    def test_a_folder_with_nothing_in_it_is_an_empty_list(self) -> None:
        self.assertEqual(library.find_entries(self.folder, durations=False), [])

    def test_a_folder_that_is_not_there_is_an_empty_list(self) -> None:
        """Never raises: the output folder may be on a drive that is not plugged in."""
        self.assertEqual(
            library.find_entries(self.folder / "nowhere", durations=False), []
        )

    def test_a_set_is_one_row_and_its_screens_are_not_rows_of_their_own(self) -> None:
        """The whole reason the player exists: two files that mean nothing apart."""
        first = self.video("take_screen0.mp4")
        second = self.video("take_screen1.mp4")
        self.index("take", [first, second])
        entries = library.find_entries(self.folder, durations=False)
        self.assertEqual(self.names(entries), ["take"])
        self.assertTrue(entries[0].is_set)
        self.assertEqual(entries[0].screens, 2)
        self.assertEqual(entries[0].files, (first, second))

    def test_a_loose_video_is_a_recording_in_its_own_right(self) -> None:
        lonely = self.video("Screen 2026-11-04.mp4")
        entries = library.find_entries(self.folder, durations=False)
        self.assertEqual(self.names(entries), ["Screen 2026-11-04"])
        self.assertFalse(entries[0].is_set)
        self.assertEqual(entries[0].screens, 1)
        self.assertEqual(entries[0].path, lonely)

    def test_a_file_that_is_not_a_video_is_not_a_recording(self) -> None:
        (self.folder / "notes.txt").write_text("not a video", encoding="utf-8")
        self.assertEqual(library.find_entries(self.folder, durations=False), [])

    def test_an_index_nobody_can_read_is_not_offered(self) -> None:
        """One bad file must not be the top of the list, or nothing below it opens."""
        (self.folder / f"bad{multi.SIDECAR_SUFFIX}").write_text(
            "{ truncated", encoding="utf-8"
        )
        self.video("good.mp4")
        self.assertEqual(
            self.names(library.find_entries(self.folder, durations=False)), ["good"]
        )

    def test_the_newest_is_first_and_agrees_with_most_recent(self) -> None:
        """A shortcut that opens "the newest" and this list must name the same file."""
        self.video("old.mp4", when=1_700_000_000)
        newest = self.video("new.mp4", when=1_800_000_000)
        entries = library.find_entries(self.folder, durations=False)
        self.assertEqual(self.names(entries), ["new", "old"])
        self.assertEqual(entries[0].path, newest)
        self.assertEqual(entries[0].path, player_open.most_recent(self.folder))

    # -- what a row says ----------------------------------------------------

    def test_a_sets_name_has_neither_suffix_on_it(self) -> None:
        """`Path.stem` leaves "take.screenrec", which is a format, not a name."""
        first = self.video("take_screen0.mp4")
        self.index("Meeting 2026-11-04", [first])
        entries = library.find_entries(self.folder, durations=False)
        self.assertEqual(self.names(entries), ["Meeting 2026-11-04"])

    def test_a_sets_size_is_all_of_its_files_together(self) -> None:
        first = self.video("take_screen0.mp4", size=1000)
        second = self.video("take_screen1.mp4", size=2000)
        self.index("take", [first, second])
        entries = library.find_entries(self.folder, durations=False)
        self.assertEqual(entries[0].size_bytes, 3000)

    def test_a_file_named_by_an_index_but_gone_counts_as_nothing(self) -> None:
        first = self.video("take_screen0.mp4", size=1000)
        self.index("take", [first, self.folder / "take_screen1.mp4"])
        entries = library.find_entries(self.folder, durations=False)
        self.assertEqual(entries[0].size_bytes, 1000)
        self.assertEqual(entries[0].screens, 2, "the row still describes the take")

    def test_the_date_comes_from_the_index_when_it_names_one(self) -> None:
        first = self.video("take_screen0.mp4")
        self.index("take", [first], recorded="2026-10-31T09:15:00")
        entries = library.find_entries(self.folder, durations=False)
        self.assertEqual(entries[0].recorded, "2026-10-31T09:15:00")

    def test_a_loose_video_is_dated_by_the_file_itself(self) -> None:
        """Its mtime is the nearest thing a video with no index has to a date."""
        self.video("lonely.mp4", when=1_800_000_000)
        entries = library.find_entries(self.folder, durations=False)
        self.assertTrue(entries[0].recorded, "a row with no date at all")
        self.assertIn("T", entries[0].recorded)

    # -- the length ---------------------------------------------------------

    def test_a_real_clip_is_measured(self) -> None:
        if _ffmpeg_problem or _clip is None:
            self.skipTest(f"no test clip: {_ffmpeg_problem}")
        shutil.copyfile(_clip, self.folder / "one_second.mp4")
        entries = library.find_entries(self.folder)
        self.assertEqual(len(entries), 1)
        self.assertAlmostEqual(entries[0].duration_ms, 1000, delta=200)

    def test_a_set_is_measured_by_its_first_file(self) -> None:
        if _ffmpeg_problem or _clip is None:
            self.skipTest(f"no test clip: {_ffmpeg_problem}")
        first = self.folder / "take_screen0.mp4"
        shutil.copyfile(_clip, first)
        self.index("take", [first])
        entries = library.find_entries(self.folder)
        self.assertAlmostEqual(entries[0].duration_ms, 1000, delta=200)

    def test_asking_for_no_durations_runs_no_ffprobe_at_all(self) -> None:
        """A window that has to appear now can fill the lengths in afterwards."""
        self.video("lonely.mp4")
        asked: list[str] = []
        self.addCleanup(setattr, paths, "ffprobe_exe", paths.ffprobe_exe)
        paths.ffprobe_exe = lambda override=None: asked.append("asked")  # type: ignore
        entries = library.find_entries(self.folder, durations=False)
        self.assertEqual(entries[0].duration_ms, 0)
        self.assertEqual(asked, [])

    def test_a_length_that_cannot_be_read_is_zero_rather_than_a_crash(self) -> None:
        """Zero means "not known": a file still being written lands here too."""
        self.assertEqual(library.duration_ms(self.video("empty.mp4", size=10)), 0)
        self.assertEqual(library.duration_ms(self.folder / "gone.mp4"), 0)

    def test_no_ffprobe_is_zero_rather_than_a_crash(self) -> None:
        self.addCleanup(setattr, paths, "ffprobe_exe", paths.ffprobe_exe)

        def refuse(override=None):
            raise paths.BinaryNotFound("ffprobe.exe was not found (tried: nowhere)")

        paths.ffprobe_exe = refuse
        self.video("lonely.mp4")
        entries = library.find_entries(self.folder)
        self.assertEqual(entries[0].duration_ms, 0)
        self.assertEqual(entries[0].size_bytes, 2048, "the cheap half still works")

    def test_a_length_that_is_not_a_finite_number_is_zero(self) -> None:
        """THE DEFECT: `int(float("inf") * 1000)` is an OverflowError.

        Measured in the interpreter: `int(float("inf") * 1000)` and
        `int(float("1e400") * 1000)` both raise "cannot convert float infinity to
        integer". `float()` takes either spelling out of a header as happily as it
        takes 12.5, and the exception came out of a function documented to answer 0
        when a length cannot be read, and then out of `find_entries`, which says it
        never raises. ffprobe's own output is stood in for here - the point is what
        this module does with a line it is given, not what ffprobe prints.
        """
        self.addCleanup(setattr, subprocess, "run", subprocess.run)

        class _Result:
            def __init__(self, text: str) -> None:
                self.stdout = text
                self.stderr = ""
                self.returncode = 0

        printed = ""
        subprocess.run = lambda *rest, **more: _Result(printed)  # type: ignore
        film = self.video("странная.mp4")
        for printed in ("inf\n", "1e400\n", "-inf\n", "nan\n", "N/A\n", "\n"):
            with self.subTest(printed=printed.strip()):
                self.assertEqual(library.duration_ms(film), 0)
        printed = "12.5\n"
        self.assertEqual(library.duration_ms(film), 12500)

    def test_a_folder_that_will_not_answer_is_an_empty_list(self) -> None:
        """§10: `pathlib` only swallows a "not found", and the listing promises more.

        `[WinError 64] The specified network name is no longer available` comes out of
        `Path.is_file()` and `Path.stat()` rather than being read as False - measured,
        with the table in `open._answered` - so a share that drops while the library is
        being built used to raise out of `find_entries`, which says it never raises.
        """
        self.video("good.mp4")
        self.index("take", [self.video("take_screen0.mp4")])
        real = Path.stat
        self.addCleanup(setattr, Path, "stat", real)
        folder = self.folder

        def refuse(self, *rest, **more):
            if str(folder) in str(self):
                raise OSError(
                    32, "The specified network name is no longer available", None, 64
                )
            return real(self, *rest, **more)

        Path.stat = refuse
        self.assertEqual(library.find_entries(self.folder), [])


class SavedTwiceTest(unittest.TestCase):
    """What the viewer sees after pressing Save twice on one pair.

    The library is where the regression showed: `open.save_set` stepped over every
    taken name, its own included, so the second press wrote `camera_a-2...` and the
    third `camera_a-3...`, and one recording stood in the list three times with
    `most_recent` pointing at the newest copy of it. The rows are this module's
    answer, so the proof belongs here as well as beside the saver.

    `sizes` and `framerate` are handed over so that no ffprobe is run: these files are
    a few bytes of zeroes and what is being counted is rows, not pictures.
    """

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="screenrec_lib_saved_"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.first = self.folder / "camera_a.mp4"
        self.second = self.folder / "camera_b.mp4"
        for path in (self.first, self.second):
            path.write_bytes(b"\0" * 2048)

    def save(self, offsets: list[int]) -> Path:
        where, note = player_open.save_set(
            [self.first, self.second],
            offsets=offsets,
            sizes=[(1920, 1080), (1920, 1080)],
            framerate=60.0,
        )
        self.assertIsNotNone(where, note)
        assert where is not None
        return where

    def test_one_pair_saved_three_times_is_one_row(self) -> None:
        first_save = self.save([0, 0])
        self.save([0, -1480])
        self.save([0, -1480])
        rows = library.find_entries(self.folder, durations=False)
        self.assertEqual([row.name for row in rows], ["camera_a"])
        self.assertEqual(rows[0].screens, 2)
        self.assertEqual(player_open.most_recent(self.folder), first_save)


if __name__ == "__main__":
    unittest.main()
