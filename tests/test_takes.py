"""The join that turns a take's segments into the one file the user asked for.

`takes.measure` is covered in `tests/test_storage.py`, which owns the cost history.
This file is about `finish` itself, and it exists because of two defects found on
2026-11-05, both of which lost the user data while reporting success:

1. **Both audio tracks are not both in the finished file.** A recording made with the
   microphone and the system sound as separate tracks wrote a segment holding
   ``[video, audio "Microphone", audio "System"]`` and produced an MP4 holding
   ``[video, audio]``. ffmpeg's default stream mapping keeps the best *one* stream of
   each kind; nothing said so and the exit code was 0. Fixed with ``-map 0``.
2. **A take whose settings changed while it was paused was joined anyway.** Measured:
   recording 2 s of H.265, pausing, switching the profile to H.264 and recording 2 s
   more gave a 4.10 s file in which 122 of 244 frames decoded, 353 decode errors
   ("Invalid NAL unit size (1677729708 > 31606)"), and the user was told nothing at
   all. `shape_problem` is the guard and carries the full measurement.

The real-ffmpeg class at the end synthesises its segments with ``lavfi`` - no screen
capture, no GPU, 0.06 s per segment measured on this build - and skips when there is
no bundled ffmpeg, the same arrangement `tests/test_encoders.py` uses.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from screenrec import i18n, paths, takes

CREATE_NO_WINDOW = 0x08000000


def _ffmpeg() -> str:
    try:
        return str(paths.ffmpeg_exe())
    except (paths.BinaryNotFound, OSError):
        return ""


class _Run:
    """What `subprocess.run` returned, as much of it as `finish` reads."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


class _Recorded:
    """Stands in for `subprocess.run`, remembering the argument lists it was given."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self._result = _Run(returncode, stderr)

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        return self._result


class JoinArgumentsTest(unittest.TestCase):
    """What `finish` asks ffmpeg to do, without asking it."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="screenrec-takes-"))
        self.addCleanup(self._clean)
        self.runs = _Recorded()
        self._real_run = takes.subprocess.run
        takes.subprocess.run = self.runs
        self.addCleanup(setattr, takes.subprocess, "run", self._real_run)
        # No probe, so `shape_problem` cannot speak and does not try to.
        self._real_shape = takes.segment_shape
        takes.segment_shape = lambda ffprobe, path: None
        self.addCleanup(setattr, takes, "segment_shape", self._real_shape)

    def _clean(self) -> None:
        for path in self.folder.glob("*"):
            path.unlink(missing_ok=True)
        self.folder.rmdir()

    def take(self, count: int) -> takes.Take:
        take = takes.Take(final=self.folder / "take.mp4")
        for _index in range(count):
            segment = take.next_segment()
            segment.write_bytes(b"not really Matroska, but it is on the disk")
            take.add(segment)
        # `finish` checks the output exists before it believes ffmpeg; the fake run
        # writes nothing, so it is put there instead.
        take.final.write_bytes(b"joined")
        return take

    def test_the_single_segment_remux_carries_every_stream(self) -> None:
        """Without `-map 0` a two-track recording arrives with one track."""
        takes.finish("ffmpeg.exe", self.take(1), keep_segments=True)
        args = self.runs.calls[0]
        self.assertIn("-map", args)
        self.assertEqual(args[args.index("-map") + 1], "0")

    def test_the_concat_carries_every_stream_too(self) -> None:
        takes.finish("ffmpeg.exe", self.take(3), keep_segments=True)
        args = self.runs.calls[0]
        self.assertIn("-f", args)
        self.assertIn("concat", args)
        self.assertEqual(args[args.index("-map") + 1], "0")

    def test_the_mapping_comes_before_the_output_file(self) -> None:
        """ffmpeg is position-sensitive: an option after the file is not an option."""
        takes.finish("ffmpeg.exe", self.take(2), keep_segments=True)
        args = self.runs.calls[0]
        self.assertLess(args.index("-map"), args.index(str(self.folder / "take.mp4")))

    def test_nothing_recorded_is_still_the_first_answer(self) -> None:
        take = takes.Take(final=self.folder / "take.mp4")
        path, problem = takes.finish("ffmpeg.exe", take)
        self.assertIsNone(path)
        self.assertIn("Nothing was recorded", problem)
        self.assertEqual(self.runs.calls, [])


class ShapeProblemTest(unittest.TestCase):
    """`shape_problem` on shapes handed to it directly."""

    VIDEO = (("video", "hevc", 640, 480, "yuv420p", 0),)
    H264 = (("video", "h264", 640, 480, "yuv420p", 0),)
    BIGGER = (("video", "hevc", 800, 600, "yuv420p", 0),)
    DEEPER = (("video", "hevc", 640, 480, "yuv444p", 0),)
    STEREO = ("audio", "aac", 0, 0, "", 2)
    MONO = ("audio", "aac", 0, 0, "", 1)
    TWO_TRACKS = VIDEO + (STEREO, STEREO)
    ONE_TRACK = VIDEO + (STEREO,)
    ONE_MONO_TRACK = VIDEO + (MONO,)

    def answer(self, *shapes):
        """Make `segment_shape` give these answers, one per segment, in order."""
        table = {Path(f"part{index:02d}.mkv"): shape
                 for index, shape in enumerate(shapes)}
        self._real = takes.segment_shape
        takes.segment_shape = lambda ffprobe, path: table[path]
        self.addCleanup(setattr, takes, "segment_shape", self._real)
        return list(table)

    def test_one_segment_is_never_a_disagreement(self) -> None:
        segments = self.answer(self.VIDEO)
        self.assertEqual(takes.shape_problem("ffmpeg.exe", segments), "")

    def test_segments_that_match_are_joined(self) -> None:
        segments = self.answer(self.VIDEO, self.VIDEO, self.VIDEO)
        self.assertEqual(takes.shape_problem("ffmpeg.exe", segments), "")

    def test_a_changed_codec_is_refused(self) -> None:
        """Measured: 46 of 90 frames, 125 decode errors, exit code 0."""
        segments = self.answer(self.VIDEO, self.H264)
        problem = takes.shape_problem("ffmpeg.exe", segments)
        self.assertIn("hevc", problem)
        self.assertIn("h264", problem)

    def test_a_changed_frame_size_is_refused(self) -> None:
        """Measured: every frame arrives, the header still says 640x480, 3 errors."""
        segments = self.answer(self.VIDEO, self.BIGGER)
        self.assertIn("800x600", takes.shape_problem("ffmpeg.exe", segments))

    def test_a_changed_pixel_format_is_refused(self) -> None:
        segments = self.answer(self.VIDEO, self.DEEPER)
        self.assertIn("yuv444p", takes.shape_problem("ffmpeg.exe", segments))

    def test_a_lost_audio_track_is_refused(self) -> None:
        """Measured: ffmpeg exits 0 and the take simply carries one track."""
        segments = self.answer(self.TWO_TRACKS, self.ONE_TRACK)
        self.assertNotEqual(takes.shape_problem("ffmpeg.exe", segments), "")

    def test_the_first_segment_is_the_contract_not_the_neighbour(self) -> None:
        """Segment 3 is held to segment 1, not to segment 2."""
        segments = self.answer(self.VIDEO, self.VIDEO, self.H264)
        problem = takes.shape_problem("ffmpeg.exe", segments)
        self.assertIn("part00.mkv", problem)
        self.assertIn("part02.mkv", problem)

    def test_a_segment_that_cannot_be_read_is_not_a_disagreement(self) -> None:
        """No ffprobe, or a probe that failed, must not block a join."""
        self._real = takes.segment_shape
        takes.segment_shape = lambda ffprobe, path: None
        self.addCleanup(setattr, takes, "segment_shape", self._real)
        self.assertEqual(
            takes.shape_problem("ffmpeg.exe", [Path("a.mkv"), Path("b.mkv")]), ""
        )

    def test_a_microphone_that_went_stereo_to_mono_is_refused(self) -> None:
        """Measured on this build: it joins, exits 0, decodes with 0 errors - and the
        finished file says 2 channels for a half that holds 1. Width and height are 0
        on an audio stream, so the five fields this guard had could not see it."""
        segments = self.answer(self.ONE_TRACK, self.ONE_MONO_TRACK)
        problem = takes.shape_problem("ffmpeg.exe", segments)
        self.assertIn("2ch", problem)
        self.assertIn("1ch", problem)


class UnreadableSegmentTest(unittest.TestCase):
    """The segment the concat demuxer drops while exiting 0. See `unreadable_problem`."""

    SHAPE = (("video", "hevc", 640, 480, "yuv420p", 0),)

    def test_a_segment_that_did_not_probe_is_named(self) -> None:
        segments = [Path("a.mkv"), Path("b.mkv"), Path("c.mkv")]
        problem = takes.unreadable_problem(segments, [self.SHAPE, None, self.SHAPE])
        self.assertEqual(problem, "b.mkv")

    def test_every_lost_segment_is_named(self) -> None:
        segments = [Path("a.mkv"), Path("b.mkv"), Path("c.mkv")]
        problem = takes.unreadable_problem(segments, [self.SHAPE, None, None])
        self.assertEqual(problem, "b.mkv, c.mkv")

    def test_no_probe_at_all_is_not_a_refusal(self) -> None:
        """With no ffprobe every shape is None, and a join must not be blocked by a
        check that could not be made - the rule `segment_shape` states."""
        segments = [Path("a.mkv"), Path("b.mkv")]
        self.assertEqual(takes.unreadable_problem(segments, [None, None]), "")

    def test_segments_that_all_probe_are_not_a_refusal(self) -> None:
        segments = [Path("a.mkv"), Path("b.mkv")]
        self.assertEqual(
            takes.unreadable_problem(segments, [self.SHAPE, self.SHAPE]), ""
        )


class RealJoinTest(unittest.TestCase):
    """The whole of `finish` against the bundled ffmpeg.

    The segments are made with ``lavfi`` - a test pattern and two sine waves - so
    nothing here captures a screen or touches the GPU. Measured on this build:
    0.062 s to write one segment.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.ffmpeg = _ffmpeg()
        if not cls.ffmpeg:
            raise unittest.SkipTest("the bundled ffmpeg is not in this checkout")

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="screenrec-join-")
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)

    def segment(self, path: Path, *, tracks: int = 2, codec: str = "libx264",
                size: str = "160x120") -> Path:
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=10"]
        for index in range(tracks):
            args += ["-f", "lavfi", "-i", f"sine=frequency={440 * (index + 1)}"]
        args += ["-map", "0:v"]
        for index in range(tracks):
            args += ["-map", f"{index + 1}:a"]
        args += ["-t", "0.5", "-c:v", codec, "-c:a", "aac"]
        args += takes.SEGMENT_FLAGS + ["-y", str(path)]
        subprocess.run(args, capture_output=True, check=True,
                       creationflags=CREATE_NO_WINDOW)
        return path

    def streams(self, path: Path) -> list[tuple[str, str]]:
        probe = subprocess.run(
            [takes.ffprobe_beside(self.ffmpeg), "-v", "error", "-show_entries",
             "stream=codec_type,codec_name", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        return [(s["codec_type"], s["codec_name"])
                for s in json.loads(probe.stdout)["streams"]]

    def take(self, *specs) -> takes.Take:
        take = takes.Take(final=self.folder / "take.mp4")
        for spec in specs:
            take.add(self.segment(take.next_segment(), **spec))
        return take

    def test_one_segment_with_two_tracks_keeps_both(self) -> None:
        """Without `-map 0` this arrives as [video, audio] - measured."""
        take = self.take({"tracks": 2})
        path, problem = takes.finish(self.ffmpeg, take)
        self.assertEqual(problem, "")
        self.assertEqual(self.streams(path),
                         [("video", "h264"), ("audio", "aac"), ("audio", "aac")])

    def test_several_segments_with_two_tracks_keep_both(self) -> None:
        take = self.take({"tracks": 2}, {"tracks": 2})
        path, problem = takes.finish(self.ffmpeg, take)
        self.assertEqual(problem, "")
        self.assertEqual(len([s for s in self.streams(path) if s[0] == "audio"]), 2)

    def test_a_take_whose_codec_changed_is_not_reported_as_saved(self) -> None:
        take = self.take({"codec": "libx264"}, {"codec": "libx265"})
        path, problem = takes.finish(self.ffmpeg, take)
        self.assertIsNone(path)
        self.assertIn("settings changed", problem)
        self.assertFalse(take.final.is_file())

    def test_a_refused_join_leaves_every_segment_on_the_disk(self) -> None:
        """The segments hold everything recorded and each one plays on its own."""
        take = self.take({"size": "160x120"}, {"size": "320x240"})
        takes.finish(self.ffmpeg, take)
        self.assertEqual(len(take.written()), 2)

    def test_a_take_that_lost_a_track_half_way_is_refused(self) -> None:
        take = self.take({"tracks": 2}, {"tracks": 1})
        path, problem = takes.finish(self.ffmpeg, take)
        self.assertIsNone(path)
        self.assertNotEqual(problem, "")

    def test_a_take_that_only_changed_frame_rate_is_still_joined(self) -> None:
        """A frame rate that changed over a pause keeps every frame, so it is joined.

        This synthesises 10 -> 30 fps, which measured 60 frames and the right
        duration on this build. It used to quote "30 -> 60 fps, 138 frames and no
        decode errors", which is neither what it does nor what that join does: see
        `takes.shape_problem` for the dts lines a re-mux of one reports.
        """
        take = takes.Take(final=self.folder / "take.mp4")
        first = take.next_segment()
        take.add(self.segment(first, tracks=0))
        second = take.next_segment()
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc2=size=160x120:rate=30", "-t", "0.5",
                "-c:v", "libx264"] + takes.SEGMENT_FLAGS + ["-y", str(second)]
        subprocess.run(args, capture_output=True, check=True,
                       creationflags=CREATE_NO_WINDOW)
        take.add(second)
        path, problem = takes.finish(self.ffmpeg, take)
        self.assertEqual(problem, "")
        self.assertIsNotNone(path)

    def test_recover_refuses_the_same_join_finish_refuses(self) -> None:
        """An orphan group is rebuilt through `finish`, so it inherits the guard."""
        take = self.take({"codec": "libx264"}, {"codec": "libx265"})
        path, problem = takes.recover(self.ffmpeg, take.written())
        self.assertIsNone(path)
        self.assertIn("settings changed", problem)

    def test_segment_shape_reads_what_is_really_in_the_file(self) -> None:
        segment = self.segment(self.folder / "one.mkv", tracks=1)
        shape = takes.segment_shape(takes.ffprobe_beside(self.ffmpeg), segment)
        self.assertEqual(shape[0], ("video", "h264", 160, 120, "yuv420p", 0))
        self.assertEqual(shape[1][0], "audio")
        # The channel count the guard needs. lavfi's `sine` is mono, so this is 1.
        self.assertEqual(shape[1][5], 1)

    def test_segment_shape_on_something_that_is_not_a_video(self) -> None:
        rubbish = self.folder / "rubbish.mkv"
        rubbish.write_bytes(b"this is not Matroska")
        self.assertIsNone(
            takes.segment_shape(takes.ffprobe_beside(self.ffmpeg), rubbish)
        )


class CorruptSegmentTest(unittest.TestCase):
    """A segment the concat demuxer cannot open, against the bundled ffmpeg.

    Measured on this build on 2026-11-05, four 3.0 s segments with the second one's
    EBML header overwritten with zeroes: ffmpeg **exits 0**, says "Impossible to open
    '...part01.mkv'" and "Error during demuxing" on stderr, writes a 3.0 s file for
    the 9.0 s that went in - and `finish` then deleted all four segments and told the
    user the recording was saved. The same through `recover`, which is the path a
    crashed session is offered on.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.ffmpeg = _ffmpeg()
        if not cls.ffmpeg:
            raise unittest.SkipTest("the bundled ffmpeg is not in this checkout")

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="screenrec-corrupt-")
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)

    def segment(self, path: Path, seconds: float = 1.0) -> Path:
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc2=size=160x120:rate=10", "-t", str(seconds),
                "-c:v", "libx264"] + takes.SEGMENT_FLAGS + ["-y", str(path)]
        subprocess.run(args, capture_output=True, check=True,
                       creationflags=CREATE_NO_WINDOW)
        return path

    def smash(self, path: Path) -> None:
        """Overwrite the EBML header, which is what an unreadable segment looks like."""
        data = bytearray(path.read_bytes())
        data[0:8] = b"\x00" * 8
        path.write_bytes(bytes(data))

    def take(self, count: int = 3, damaged: int = 1) -> takes.Take:
        take = takes.Take(final=self.folder / "take.mp4")
        for _index in range(count):
            take.add(self.segment(take.next_segment()))
        self.smash(take.segments[damaged])
        return take

    def seconds(self, path: Path) -> float:
        return takes.probe_seconds(takes.ffprobe_beside(self.ffmpeg), path)

    def test_a_take_with_an_unreadable_segment_is_not_reported_as_saved(self) -> None:
        take = self.take()
        path, problem = takes.finish(self.ffmpeg, take)
        self.assertIsNone(path)
        self.assertIn("take.part01.mkv", problem)

    def test_nothing_is_deleted_when_the_join_is_refused(self) -> None:
        """The whole point: three segments went in and three are still there."""
        take = self.take()
        takes.finish(self.ffmpeg, take)
        self.assertEqual(len(take.segments), 3)
        self.assertTrue(all(p.is_file() for p in take.segments))
        self.assertFalse(take.final.is_file())

    def test_the_refusal_says_where_the_segments_are(self) -> None:
        take = self.take()
        _path, problem = takes.finish(self.ffmpeg, take)
        self.assertIn(str(self.folder), problem)

    def test_recover_refuses_it_too(self) -> None:
        """The crash path: orphans go through `finish`, so they inherit the guard."""
        take = self.take()
        path, problem = takes.recover(self.ffmpeg, take.written())
        self.assertIsNone(path)
        self.assertIn("take.part01.mkv", problem)

    def test_a_truncated_segment_is_still_joined(self) -> None:
        """What crash safety is for. Measured: a segment cut in half still probes,
        still joins, and what reached the disk is kept - only an unreadable *header*
        is dropped in silence, so only that is refused."""
        take = takes.Take(final=self.folder / "take.mp4")
        for _index in range(2):
            take.add(self.segment(take.next_segment()))
        half = take.segments[1].read_bytes()
        take.segments[1].write_bytes(half[: len(half) // 2])
        path, problem = takes.finish(self.ffmpeg, take)
        self.assertEqual(problem, "")
        self.assertIsNotNone(path)
        self.assertGreater(self.seconds(take.final), 1.0)

    def test_the_refusal_is_russian_on_a_russian_bar(self) -> None:
        """`finish` writes straight onto the bar through `_stop_single`, and it was
        English end to end - a whole paragraph with a Windows path in it."""
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language(i18n.LANG_RU)
        take = self.take()
        _path, problem = takes.finish(self.ffmpeg, take)
        self.assertIn("Часть записи не читается", problem)
        self.assertNotIn("could not be read", problem)
        self.assertIn("take.part01.mkv", problem)  # file names stay file names

    def test_a_single_unreadable_segment_still_fails_its_remux(self) -> None:
        """One segment is a remux, and ffmpeg exits non-zero on that one - measured
        ("Error opening input files"), so the guard is not needed below two."""
        take = takes.Take(final=self.folder / "take.mp4")
        take.add(self.segment(take.next_segment()))
        self.smash(take.segments[0])
        path, problem = takes.finish(self.ffmpeg, take)
        self.assertIsNone(path)
        self.assertNotEqual(problem, "")
        self.assertTrue(take.segments[0].is_file())


if __name__ == "__main__":
    unittest.main()
