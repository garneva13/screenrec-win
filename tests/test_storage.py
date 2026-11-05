"""Free space, and what a minute of recording really costs on *this* machine.

The second half is the point of this file. `config.PROFILES[...].mb_per_minute` is
measured, but it was measured on an RTX 3070 Ti and on x264; `docs/ENCODERS.md` section 3
measured `hevc_mf` writing 4.9x that on a busy desktop. So the estimate line has to stop
quoting somebody else's number as soon as this machine has one of its own, and these
tests pin the two things that makes dangerous:

* a measurement must never be taken from a recording that cannot support one - too
  short, empty, or with a duration that belongs to a different file;
* the figure must come back out at the size and frame rate it is asked about, not at the
  one it happened to be taken at.

Every store is a temporary file: nothing here may touch the real `%APPDATA%\\ScreenRec`.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from screenrec import config, paths, storage, takes


class StoreTest(unittest.TestCase):
    """A temporary sizes.json for every test, so none of them see the real one."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="screenrec-sizes-")
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.store = self.root / "sizes.json"

    def remember(self, **kwargs) -> str:
        """One finished recording, with workable defaults for whatever is not the point."""
        fields = dict(
            encoder="hevc_nvenc",
            quality="balanced",
            pix_fmt="yuv420p",
            size_bytes=10_000_000,
            seconds=60.0,
            width=1280,
            height=720,
            framerate=60,
            store=self.store,
        )
        fields.update(kwargs)
        return storage.remember_recording(**fields)


# --------------------------------------------------------------- free space


class SpaceTest(unittest.TestCase):
    def test_a_folder_that_does_not_exist_yet_measures_its_volume(self) -> None:
        """A first run names a folder nobody has created. That is not an error."""
        with tempfile.TemporaryDirectory() as raw:
            deep = Path(raw) / "not" / "made" / "yet"
            space = storage.check(deep, 10.0)
        self.assertTrue(space.known, space.problem)
        self.assertGreater(space.free_bytes, 0)

    def test_an_unknown_cost_per_minute_is_an_unknown_number_of_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            space = storage.check(Path(raw), 0.0)
        self.assertEqual(space.minutes_left, float("inf"))
        self.assertFalse(space.low)


# ------------------------------------------------- the model, and its one copy


class CaptureFactorTest(unittest.TestCase):
    def test_it_is_the_same_model_config_uses(self) -> None:
        """`capture_factor` is a copy of `config.estimate_mb_per_minute`'s arithmetic.

        It is a copy on purpose - `config.py` has no business knowing about a history
        file - and this is what stops the copy drifting.
        """
        base = config.PROFILES["hevc420"].mb_per_minute["balanced"]
        for width, height in ((1280, 720), (2560, 1440), (3840, 2160), (800, 600)):
            for framerate in (24, 30, 60, 120):
                with self.subTest(size=(width, height), fps=framerate):
                    self.assertAlmostEqual(
                        config.estimate_mb_per_minute(
                            "hevc420", "balanced", width, height, framerate
                        ),
                        base * storage.capture_factor(width, height, framerate),
                        places=9,
                    )

    def test_the_reference_capture_costs_exactly_the_table_figure(self) -> None:
        self.assertEqual(storage.capture_factor(1280, 720, 60), 1.0)

    def test_a_nonsense_capture_cannot_make_the_factor_zero(self) -> None:
        """Guarding a division: a zero factor would turn every stored figure into inf."""
        self.assertGreater(storage.capture_factor(0, 0, 0), 0.0)


# ----------------------------------------------------- storing a measurement


class RememberTest(StoreTest):
    def test_a_finished_recording_is_stored_as_what_it_cost(self) -> None:
        self.assertEqual(self.remember(size_bytes=24_500_000, seconds=60.0), "")
        samples = storage.load_samples(self.store)
        self.assertEqual(list(samples), ["hevc_nvenc|balanced|yuv420p"])
        sample = samples["hevc_nvenc|balanced|yuv420p"][0]
        self.assertAlmostEqual(sample.mb_per_minute, 24.5, places=6)
        self.assertAlmostEqual(sample.base_mb_per_minute, 24.5, places=6)
        self.assertGreater(sample.at, 0.0)

    def test_a_bigger_capture_is_scaled_back_to_the_reference(self) -> None:
        """2560x1440 is four times 1280x720, so the base figure is a quarter of it."""
        self.remember(size_bytes=40_000_000, seconds=60.0, width=2560, height=1440)
        sample = storage.load_samples(self.store)["hevc_nvenc|balanced|yuv420p"][0]
        self.assertAlmostEqual(sample.mb_per_minute, 40.0, places=6)
        self.assertAlmostEqual(sample.base_mb_per_minute, 10.0, places=6)

    def test_it_survives_a_round_trip_through_the_file(self) -> None:
        self.remember()
        self.assertTrue(self.store.is_file())
        written = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(written["version"], storage.SIZES_VERSION)
        reread = storage.load_samples(self.store)["hevc_nvenc|balanced|yuv420p"][0]
        self.assertIsInstance(reread, storage.Sample)
        self.assertEqual(reread.encoder, "hevc_nvenc")

    def test_only_the_last_few_recordings_are_kept(self) -> None:
        for index in range(storage.KEEP_SAMPLES + 3):
            self.remember(size_bytes=(index + 1) * 1_000_000)
        samples = storage.load_samples(self.store)["hevc_nvenc|balanced|yuv420p"]
        self.assertEqual(len(samples), storage.KEEP_SAMPLES)
        # The newest survive, not the oldest.
        self.assertAlmostEqual(samples[-1].mb_per_minute, 8.0, places=6)

    def test_a_store_that_cannot_be_written_says_so(self) -> None:
        blocker = self.root / "blocker"
        blocker.write_text("not a folder", encoding="utf-8")
        problem = self.remember(store=blocker / "sizes.json")
        self.assertIn("could not be saved", problem)


class KeyTest(StoreTest):
    def test_two_encoders_are_two_different_answers(self) -> None:
        self.remember(encoder="hevc_nvenc", size_bytes=24_500_000)
        self.remember(encoder="hevc_mf", size_bytes=120_000_000)
        samples = storage.load_samples(self.store)
        self.assertEqual(len(samples), 2)

    def test_two_quality_levels_are_two_different_answers(self) -> None:
        self.remember(quality="balanced")
        self.remember(quality="best")
        self.assertEqual(len(storage.load_samples(self.store)), 2)

    def test_the_pixel_format_is_part_of_the_key(self) -> None:
        """hevc420 and hevc444 are one encoder name at one quality and two costs.

        `config.PROFILES` measured them at 24.5 and 19.0 MB/min at `balanced`, both
        through `hevc_nvenc`. One key for the two would average a difference that was
        measured rather than guessed.
        """
        self.remember(pix_fmt="yuv420p")
        self.remember(pix_fmt="yuv444p")
        self.assertEqual(
            sorted(storage.load_samples(self.store)),
            ["hevc_nvenc|balanced|yuv420p", "hevc_nvenc|balanced|yuv444p"],
        )


class GuardTest(StoreTest):
    """The four ways a finished recording is not worth measuring."""

    def assertNotStored(self, problem: str) -> None:
        self.assertNotEqual(problem, "")
        self.assertEqual(storage.load_samples(self.store), {})

    def test_a_zero_byte_file_is_not_a_measurement(self) -> None:
        self.assertNotStored(self.remember(size_bytes=0))

    def test_a_negative_size_is_not_a_measurement(self) -> None:
        self.assertNotStored(self.remember(size_bytes=-1))

    def test_a_recording_of_no_length_is_not_a_measurement(self) -> None:
        self.assertNotStored(self.remember(seconds=0.0))

    def test_a_recording_shorter_than_the_floor_is_refused(self) -> None:
        problem = self.remember(seconds=storage.MIN_MEASURED_SECONDS - 0.5)
        self.assertNotStored(problem)
        self.assertIn("too short", problem)

    def test_a_recording_just_over_the_floor_is_kept(self) -> None:
        self.assertEqual(self.remember(seconds=storage.MIN_MEASURED_SECONDS + 0.1), "")

    def test_an_unknown_capture_size_is_refused(self) -> None:
        """Without the geometry the figure cannot be compared with anything at all."""
        self.assertNotStored(self.remember(width=0, height=0))

    def test_an_unknown_frame_rate_is_refused(self) -> None:
        self.assertNotStored(self.remember(framerate=0))

    def test_a_take_with_no_encoder_name_has_nothing_to_be_keyed_on(self) -> None:
        self.assertNotStored(self.remember(encoder=""))

    def test_a_take_with_no_quality_level_has_nothing_to_be_keyed_on(self) -> None:
        self.assertNotStored(self.remember(quality=""))

    def test_a_clock_that_counted_a_pause_is_not_this_file(self) -> None:
        """The paused case: 20 s recorded, 80 s of wall clock, one 20 s file."""
        problem = self.remember(seconds=20.0, claimed_seconds=80.0)
        self.assertNotStored(problem)
        self.assertIn("not the same recording", problem)

    def test_a_clock_a_frame_out_is_still_this_file(self) -> None:
        """Measured: ffmpeg's out_time and the file agreed to 0.0003 s on clean stops.

        The tolerance is far wider than that on purpose, so ordinary rounding never
        throws a real measurement away.
        """
        self.assertEqual(self.remember(seconds=20.0, claimed_seconds=20.02), "")

    def test_the_tolerance_grows_with_the_recording(self) -> None:
        """5% of an hour is three minutes; 5% of ten seconds is less than the slack."""
        self.assertEqual(self.remember(seconds=3600.0, claimed_seconds=3700.0), "")
        self.assertNotEqual(self.remember(seconds=10.0, claimed_seconds=11.0), "")


class SaveTest(StoreTest):
    """The history must not be able to destroy itself on the way to disk."""

    @staticmethod
    def torn(self: Path, data: str, encoding: str | None = None, **rest) -> int:
        """Stand in for a write that dies after the file has been opened.

        `Path.write_text` truncates the moment it opens, so this is what a full disk, a
        power cut or a killed process does to whichever file is being written: it exists,
        it is empty, and the exception arrives afterwards. Reproduced against the plain
        `write_text` this replaced: `sizes.json` came back zero bytes long and a
        measurement that was already stored was gone.
        """
        with open(self, "w", encoding="utf-8"):
            pass
        raise OSError(28, "No space left on device")

    def test_a_write_that_dies_part_way_keeps_the_previous_history(self) -> None:
        self.assertEqual(self.remember(size_bytes=120_000_000), "")
        before = storage.load_samples(self.store)

        with mock.patch.object(Path, "write_text", self.torn):
            problem = self.remember(size_bytes=130_000_000)

        self.assertIn("could not be saved", problem)
        self.assertEqual(storage.load_samples(self.store), before)

    def test_a_write_that_dies_part_way_leaves_no_half_written_file_about(self) -> None:
        """Whatever was being written is cleaned up; %APPDATA% keeps no rubbish."""
        self.assertEqual(self.remember(), "")
        with mock.patch.object(Path, "write_text", self.torn):
            self.remember()
        self.assertEqual([p.name for p in self.root.iterdir()], ["sizes.json"])

    def test_a_good_save_leaves_only_the_history_file(self) -> None:
        self.assertEqual(self.remember(), "")
        self.assertEqual(self.remember(size_bytes=20_000_000), "")
        self.assertEqual([p.name for p in self.root.iterdir()], ["sizes.json"])


class LoadTest(StoreTest):
    def test_a_missing_file_is_an_empty_history(self) -> None:
        self.assertEqual(storage.load_samples(self.root / "nothing.json"), {})

    def test_a_corrupt_file_is_an_empty_history_rather_than_a_crash(self) -> None:
        self.store.write_text("{not json at all", encoding="utf-8")
        self.assertEqual(storage.load_samples(self.store), {})

    def test_a_file_from_another_version_is_dropped(self) -> None:
        self.remember()
        raw = json.loads(self.store.read_text(encoding="utf-8"))
        raw["version"] = storage.SIZES_VERSION + 1
        self.store.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(storage.load_samples(self.store), {})

    def test_a_row_of_an_unknown_shape_is_skipped_not_fatal(self) -> None:
        self.remember()
        raw = json.loads(self.store.read_text(encoding="utf-8"))
        rows = raw["samples"]["hevc_nvenc|balanced|yuv420p"]
        rows.append({"encoder": "hevc_mf"})  # missing everything else
        rows.append("not a row at all")
        self.store.write_text(json.dumps(raw), encoding="utf-8")
        samples = storage.load_samples(self.store)
        self.assertEqual(len(samples["hevc_nvenc|balanced|yuv420p"]), 1)

    def hand_edit(self, **fields) -> dict[str, list[storage.Sample]]:
        """Rewrite the one stored row's fields the way a text editor would."""
        self.remember()
        raw = json.loads(self.store.read_text(encoding="utf-8"))
        raw["samples"]["hevc_nvenc|balanced|yuv420p"][0].update(fields)
        self.store.write_text(json.dumps(raw), encoding="utf-8")
        return storage.load_samples(self.store)

    def test_a_figure_that_is_not_a_number_is_dropped(self) -> None:
        """The file sits in %APPDATA% where anybody can open it.

        Left untyped this reaches `statistics.median` and takes the settings panel down
        with a TypeError.
        """
        self.assertEqual(self.hand_edit(base_mb_per_minute="lots"), {})

    def test_a_figure_of_nan_is_dropped(self) -> None:
        """`json.loads` accepts a bare NaN, and NaN propagates through a median."""
        self.remember()
        raw = self.store.read_text(encoding="utf-8")
        self.store.write_text(
            raw.replace('"base_mb_per_minute": 24.5', '"base_mb_per_minute": NaN')
            .replace('"base_mb_per_minute": 10.0', '"base_mb_per_minute": NaN'),
            encoding="utf-8",
        )
        self.assertEqual(storage.load_samples(self.store), {})

    def test_a_figure_of_zero_is_dropped(self) -> None:
        self.assertEqual(self.hand_edit(base_mb_per_minute=0.0), {})

    def test_a_number_written_as_text_is_still_read(self) -> None:
        """Converting rather than rejecting: "24.5" is a number somebody retyped."""
        table = self.hand_edit(base_mb_per_minute="24.5", width="1280")
        sample = table["hevc_nvenc|balanced|yuv420p"][0]
        self.assertEqual(sample.base_mb_per_minute, 24.5)
        self.assertEqual(sample.width, 1280)

    def test_an_estimate_survives_a_history_of_rubbish(self) -> None:
        self.hand_edit(base_mb_per_minute=None)
        answer = storage.size_estimate(
            "hevc420", "balanced", 1280, 720, 60,
            encoder="hevc_nvenc", store=self.store,
        )
        self.assertFalse(answer.measured)
        self.assertEqual(
            answer.mb_per_minute,
            config.estimate_mb_per_minute("hevc420", "balanced", 1280, 720, 60),
        )

    def test_forgetting_leaves_no_history(self) -> None:
        self.remember()
        self.assertEqual(storage.forget_measurements(self.store), "")
        self.assertEqual(storage.load_samples(self.store), {})

    def test_forgetting_what_is_not_there_is_not_a_failure(self) -> None:
        self.assertEqual(storage.forget_measurements(self.root / "nothing.json"), "")


# ------------------------------------------------------- reading it back out


class EstimateTest(StoreTest):
    def table(self, **kwargs) -> float:
        fields = dict(
            profile_key="hevc420", quality_level="balanced",
            width=1280, height=720, framerate=60,
        )
        fields.update(kwargs)
        return config.estimate_mb_per_minute(**fields)

    def estimate(self, **kwargs) -> storage.Estimate:
        fields = dict(
            profile_key="hevc420", quality_level="balanced",
            width=1280, height=720, framerate=60,
            encoder="hevc_nvenc", store=self.store,
        )
        fields.update(kwargs)
        return storage.size_estimate(**fields)

    def test_with_no_history_it_is_the_table_and_says_so(self) -> None:
        answer = self.estimate()
        self.assertFalse(answer.measured)
        self.assertEqual(answer.mb_per_minute, self.table())
        self.assertEqual(answer.samples, 0)
        self.assertEqual(answer.at, 0.0)

    def test_without_an_encoder_name_there_is_nothing_to_look_up(self) -> None:
        """The panel may not know yet which encoder will run. Then it gets the table."""
        self.remember()
        answer = self.estimate(encoder="")
        self.assertFalse(answer.measured)
        self.assertEqual(answer.mb_per_minute, self.table())

    def test_one_finished_recording_replaces_the_table(self) -> None:
        self.remember(size_bytes=120_000_000, seconds=60.0)
        answer = self.estimate()
        self.assertTrue(answer.measured)
        self.assertEqual(answer.encoder, "hevc_nvenc")
        self.assertEqual(answer.samples, 1)
        self.assertAlmostEqual(answer.mb_per_minute, 120.0, places=6)
        self.assertNotAlmostEqual(answer.mb_per_minute, self.table(), places=3)

    def test_the_media_foundation_case_the_whole_thing_exists_for(self) -> None:
        """A machine whose MF path writes 4.9x the NVENC table must say 4.9x.

        The ratio is `docs/ENCODERS.md` section 3's measurement on a busy desktop;
        `hevc_mf` is the encoder an AMD or Intel machine is most likely to land on, and
        on such a machine the table is quoting an RTX 3070 Ti.
        """
        measured = self.table() * 4.9
        self.remember(
            encoder="hevc_mf",
            size_bytes=int(measured * 1e6),
            seconds=60.0,
        )
        answer = self.estimate(encoder="hevc_mf")
        self.assertTrue(answer.measured)
        self.assertAlmostEqual(answer.mb_per_minute, measured, places=3)

    def test_several_recordings_report_their_median(self) -> None:
        """One recording of a still screen and one of a game are both honest.

        `docs/ENCODERS.md` section 3 measured 71.5 against 146.1 MB/min for identical
        settings on different content, so the middle of a handful is the answer, not the
        latest and not the mean.
        """
        for megabytes in (10, 200, 30, 20, 40):
            self.remember(size_bytes=megabytes * 1_000_000, seconds=60.0)
        answer = self.estimate()
        self.assertEqual(answer.samples, 5)
        self.assertAlmostEqual(answer.mb_per_minute, 30.0, places=6)

    def test_a_figure_measured_at_720p_is_re_scaled_to_the_capture_being_asked_about(
        self,
    ) -> None:
        self.remember(size_bytes=20_000_000, seconds=60.0, width=1280, height=720)
        answer = self.estimate(width=2560, height=1440)
        self.assertTrue(answer.measured)
        self.assertAlmostEqual(answer.mb_per_minute, 80.0, places=6)

    def test_a_figure_measured_at_1440p_is_re_scaled_down_to_720p(self) -> None:
        self.remember(size_bytes=80_000_000, seconds=60.0, width=2560, height=1440)
        answer = self.estimate(width=1280, height=720)
        self.assertAlmostEqual(answer.mb_per_minute, 20.0, places=6)

    def test_a_different_frame_rate_scales_too(self) -> None:
        self.remember(size_bytes=60_000_000, seconds=60.0, framerate=60)
        self.assertAlmostEqual(
            self.estimate(framerate=30).mb_per_minute, 30.0, places=6
        )

    def test_a_measurement_of_another_encoder_is_not_borrowed(self) -> None:
        self.remember(encoder="libx264", size_bytes=200_000_000, seconds=60.0)
        answer = self.estimate(encoder="hevc_nvenc")
        self.assertFalse(answer.measured)
        self.assertEqual(answer.mb_per_minute, self.table())

    def test_a_measurement_of_another_quality_level_is_not_borrowed(self) -> None:
        self.remember(quality="best", size_bytes=200_000_000, seconds=60.0)
        self.assertFalse(self.estimate(quality_level="balanced").measured)

    def test_the_444_profile_does_not_borrow_the_420_measurement(self) -> None:
        """Same encoder name, same quality level, measurably different cost."""
        self.remember(pix_fmt="yuv420p", size_bytes=200_000_000, seconds=60.0)
        answer = self.estimate(profile_key="hevc444")
        self.assertFalse(answer.measured)
        self.assertEqual(answer.mb_per_minute, self.table(profile_key="hevc444"))

    def test_the_444_profile_uses_its_own_measurement(self) -> None:
        self.remember(pix_fmt="yuv444p", size_bytes=19_000_000, seconds=60.0)
        answer = self.estimate(profile_key="hevc444")
        self.assertTrue(answer.measured)
        self.assertAlmostEqual(answer.mb_per_minute, 19.0, places=6)

    def test_an_unknown_profile_key_still_answers(self) -> None:
        """`config.estimate_mb_per_minute` falls back to the default profile; so do we,
        and without claiming the fallback was measured."""
        answer = self.estimate(profile_key="no-such-profile")
        self.assertFalse(answer.measured)
        self.assertEqual(answer.mb_per_minute, self.table(profile_key="hevc420"))

    def test_every_shipped_profile_can_be_asked(self) -> None:
        for key in config.PROFILES:
            for level in config.QUALITY_LEVELS:
                with self.subTest(profile=key, quality=level):
                    answer = self.estimate(profile_key=key, quality_level=level)
                    self.assertGreater(answer.mb_per_minute, 0.0)


# ------------------------------------------------------- the join does the storing


class FfprobeLookupTest(unittest.TestCase):
    def test_the_ffprobe_beside_the_ffmpeg_wins(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / "ffmpeg.exe").write_bytes(b"MZ")
            (folder / "ffprobe.exe").write_bytes(b"MZ")
            found = takes.ffprobe_beside(str(folder / "ffmpeg.exe"))
        self.assertEqual(Path(found).parent, folder)

    def test_with_no_sibling_it_falls_back_to_the_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            lonely = Path(raw) / "ffmpeg.exe"
            lonely.write_bytes(b"MZ")
            found = takes.ffprobe_beside(str(lonely))
        self.assertEqual(Path(found).name, "ffprobe.exe")
        self.assertNotEqual(Path(found).parent, lonely.parent)

    def test_an_unreadable_file_is_no_duration_rather_than_an_exception(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "gone.mp4"
            self.assertEqual(takes.probe_seconds("ffprobe-that-is-not-here", missing),
                             0.0)


class MeasureTest(StoreTest):
    """`takes.measure` on a file that exists, with the probe answering for it."""

    def setUp(self) -> None:
        super().setUp()
        self.file = self.root / "take.mp4"
        self.file.write_bytes(b"x" * 20_000_000)
        self._probe = takes.probe_seconds
        self.addCleanup(setattr, takes, "probe_seconds", self._probe)
        takes.probe_seconds = lambda ffprobe, path: self.duration
        self.duration = 60.0

    def take(self, **kwargs) -> takes.Take:
        fields = dict(
            encoder="hevc_nvenc", quality="balanced", pix_fmt="yuv420p",
            width=1280, height=720, framerate=60,
        )
        fields.update(kwargs)
        return takes.Take(final=self.file, **fields)

    def ffmpeg(self) -> str:
        """A path whose sibling ffprobe exists, so the lookup gets that far."""
        (self.root / "ffmpeg.exe").write_bytes(b"MZ")
        (self.root / "ffprobe.exe").write_bytes(b"MZ")
        return str(self.root / "ffmpeg.exe")

    def test_the_finished_file_is_measured_from_its_own_size_and_duration(self) -> None:
        self.assertEqual(
            takes.measure(self.ffmpeg(), self.take(), self.file, self.store), ""
        )
        sample = storage.load_samples(self.store)["hevc_nvenc|balanced|yuv420p"][0]
        self.assertAlmostEqual(sample.mb_per_minute, 20.0, places=3)

    def test_a_take_that_does_not_name_its_encoder_is_not_measured(self) -> None:
        """`recover` builds exactly this take out of a crashed session's orphans."""
        problem = takes.measure(
            self.ffmpeg(), self.take(encoder=""), self.file, self.store
        )
        self.assertIn("does not say which encoder", problem)
        self.assertEqual(storage.load_samples(self.store), {})

    def test_a_duration_that_cannot_be_read_is_not_guessed_at(self) -> None:
        self.duration = 0.0
        problem = takes.measure(self.ffmpeg(), self.take(), self.file, self.store)
        self.assertIn("duration could not be read", problem)
        self.assertEqual(storage.load_samples(self.store), {})

    def test_the_recorders_own_clock_is_cross_checked_not_used(self) -> None:
        """The take says 300 s; the file is 60 s long. That is a paused take's clock."""
        problem = takes.measure(
            self.ffmpeg(), self.take(seconds=300.0), self.file, self.store
        )
        self.assertIn("not the same recording", problem)
        self.assertEqual(storage.load_samples(self.store), {})

    def test_a_missing_file_is_reported_not_raised(self) -> None:
        gone = self.root / "gone.mp4"
        problem = takes.measure(self.ffmpeg(), self.take(), gone, self.store)
        self.assertNotEqual(problem, "")
        self.assertEqual(storage.load_samples(self.store), {})


@unittest.skipUnless(
    (paths.TOOLS_DIR / "ffmpeg.exe").is_file()
    and (paths.TOOLS_DIR / "ffprobe.exe").is_file(),
    "the bundled ffmpeg/ffprobe are not there",
)
class FinishMeasuresTest(StoreTest):
    """End to end on the real binaries: segments in, one file out, one measurement.

    Deterministic `testsrc2` rather than a screen capture, at 320x240, so the test is
    about the join and the storing and costs a fraction of a second. It is longer than
    `MIN_MEASURED_SECONDS` because a shorter one would be refused, which is the point of
    `GuardTest`.
    """

    def setUp(self) -> None:
        super().setUp()
        self.ffmpeg = str(paths.ffmpeg_exe())

    def segment(self, name: str, seconds: float) -> Path:
        out = self.root / name
        subprocess.run(
            [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
             "-t", f"{seconds}", "-c:v", "libx264", "-preset", "ultrafast",
             "-crf", "30", "-pix_fmt", "yuv420p", *takes.SEGMENT_FLAGS, str(out)],
            capture_output=True, timeout=120,
            creationflags=takes.CREATE_NO_WINDOW,
        )
        self.assertTrue(out.is_file(), f"ffmpeg wrote no {name}")
        return out

    def test_a_joined_take_leaves_a_measurement_behind(self) -> None:
        take = takes.Take(
            final=self.root / "joined.mp4",
            encoder="libx264", quality="balanced", pix_fmt="yuv420p",
            width=320, height=240, framerate=30,
        )
        take.add(self.segment("t.part00.mkv", 2.0))
        take.add(self.segment("t.part01.mkv", 3.0))
        take.seconds = 5.0  # what the recorder's clock would have said

        path, problem = takes.finish(self.ffmpeg, take, size_store=self.store)

        self.assertEqual(problem, "")
        self.assertIsNotNone(path)
        self.assertEqual(take.measurement_problem, "")
        samples = storage.load_samples(self.store)["libx264|balanced|yuv420p"]
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0].seconds, 5.0, delta=0.2)
        self.assertGreater(samples[0].mb_per_minute, 0.0)

    def test_a_take_too_short_to_measure_still_joins(self) -> None:
        """The measurement is never allowed to be the reason a recording fails."""
        take = takes.Take(
            final=self.root / "short.mp4",
            encoder="libx264", quality="balanced", pix_fmt="yuv420p",
            width=320, height=240, framerate=30,
        )
        take.add(self.segment("s.part00.mkv", 1.0))

        path, problem = takes.finish(self.ffmpeg, take, size_store=self.store)

        self.assertEqual(problem, "")
        self.assertTrue(path and path.is_file())
        self.assertIn("too short", take.measurement_problem)
        self.assertEqual(storage.load_samples(self.store), {})

    def test_an_anonymous_take_joins_exactly_as_it_did_before(self) -> None:
        """Nothing asked for a measurement, so nothing is stored and nothing changes."""
        take = takes.Take(final=self.root / "plain.mp4")
        take.add(self.segment("p.part00.mkv", 2.0))

        path, problem = takes.finish(self.ffmpeg, take, size_store=self.store)

        self.assertEqual(problem, "")
        self.assertTrue(path and path.is_file())
        self.assertFalse(self.store.exists())


if __name__ == "__main__":
    unittest.main()
