"""Settings defaults, the JSON round trip, and the coercion guards.

`config._coerce` is the only thing standing between a hand-edited settings file and the
recorder, so the interesting tests here are all about what it *refuses*.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from dataclasses import asdict, fields
from pathlib import Path

from screenrec import config, encoders, paths

# `i18n` and `player.layouts` import Qt. Nothing here builds an application, but the
# platform plugin has to be chosen before one could exist anywhere in the run.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from screenrec import i18n  # noqa: E402
from screenrec.player import layouts  # noqa: E402


class SettingsDefaultsTest(unittest.TestCase):
    def test_documented_defaults(self) -> None:
        settings = config.Settings()
        self.assertEqual(settings.source_kind, config.SOURCE_MONITOR)
        self.assertEqual(settings.monitor_index, 0)
        self.assertEqual(settings.region, [])
        self.assertEqual(settings.window_title, "")
        self.assertEqual(settings.framerate, 60)
        self.assertEqual(settings.profile, config.DEFAULT_PROFILE)
        self.assertEqual(settings.quality_level, config.DEFAULT_QUALITY)
        self.assertTrue(settings.draw_mouse)
        self.assertTrue(settings.click_highlight)
        self.assertFalse(settings.mic_enabled)
        self.assertFalse(settings.system_audio_enabled)
        self.assertEqual(settings.system_audio_device, -1)
        self.assertEqual(settings.audio_mode, config.AUDIO_MIX)
        self.assertEqual(settings.output_dir, "")
        self.assertEqual(settings.hotkey, "Ctrl+Alt+R")
        self.assertEqual(settings.countdown, 0)

    def test_default_profile_is_a_known_profile(self) -> None:
        self.assertIn(config.Settings().profile, config.PROFILES)

    def test_region_default_is_not_shared_between_instances(self) -> None:
        first = config.Settings()
        second = config.Settings()
        first.region.append(1)
        self.assertEqual(second.region, [], "region default_factory is leaking state")

    def test_resolved_output_dir_falls_back_to_the_default(self) -> None:
        settings = config.Settings()
        self.assertEqual(settings.resolved_output_dir(), paths.default_output_dir())
        settings.output_dir = r"C:\somewhere\else"
        self.assertEqual(settings.resolved_output_dir(), Path(r"C:\somewhere\else"))

    def test_has_audio(self) -> None:
        settings = config.Settings()
        self.assertFalse(settings.has_audio())
        settings.mic_enabled = True
        self.assertTrue(settings.has_audio())
        settings.mic_enabled = False
        settings.system_audio_enabled = True
        self.assertTrue(settings.has_audio())


class _TempConfig(unittest.TestCase):
    """Base class giving each test an isolated settings file."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="screenrec-test-")
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "settings.json"

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def load_raw(self, raw: dict | list | str) -> config.Settings:
        """Write `raw` as JSON, load it, and assert it loaded without a complaint."""
        self.write(json.dumps(raw, ensure_ascii=False))
        settings, problem = config.load_settings(self.path)
        self.assertEqual(problem, "", "a well-formed JSON object must not report a problem")
        return settings


class MinimumFrameTest(unittest.TestCase):
    """The smallest frame each encoder takes, measured rather than looked up.

    Taken on this machine on 2026-11-05 through the real `ddagrab -> encoder` pipeline
    that `recorder.build_command` builds, at the boundary and twice each: 128x720 and
    720x32 fail on `hevc_nvenc` while 130x720 and 720x34 encode; 144x720 and 720x48
    fail on `h264_nvenc` while 146x720 and 720x50 encode; the two Media Foundation
    names give the same four answers, because Media Foundation dispatches to
    `NVIDIA HEVC Encoder MFT` here; and `libx264` encoded every shape tried, 2x2 and
    2x720 and 720x2 among them.
    """

    def test_the_table_is_what_was_measured(self) -> None:
        self.assertEqual(
            config.MIN_FRAME,
            {
                "hevc_nvenc": (130, 34),
                "h264_nvenc": (146, 50),
                "hevc_mf": (130, 34),
                "h264_mf": (146, 50),
                "libx264": (2, 2),
            },
        )

    def test_every_name_in_it_is_one_this_program_can_record_with(self) -> None:
        for name in config.MIN_FRAME:
            with self.subTest(encoder=name):
                self.assertIn(name, encoders.FAMILIES)

    def test_an_unmeasured_name_is_held_to_the_largest_floor_seen(self) -> None:
        """No AMD and no Intel graphics on this desk. Guessing low would hand those
        machines the failed recording the table exists to prevent; guessing high costs
        a postage stamp encoded on the processor."""
        self.assertEqual(config.UNMEASURED_MIN_FRAME, max(config.MIN_FRAME.values()))
        for name in ("h264_amf", "hevc_amf", "h264_qsv", "av1_nvenc", "nonsense"):
            with self.subTest(encoder=name):
                self.assertEqual(config.min_frame(name), config.UNMEASURED_MIN_FRAME)

    def test_a_measured_name_answers_with_its_own(self) -> None:
        self.assertEqual(config.min_frame("hevc_nvenc"), (130, 34))
        self.assertEqual(config.min_frame("libx264"), (2, 2))

    def test_the_processor_takes_anything_the_selector_can_produce(self) -> None:
        """`recorder.surface_crop` makes every dimension even and at least two, and
        `overlay.RegionSelector.MIN_DRAG` is 3 - so 2x2 is the smallest frame that can
        reach ffmpeg at all, and libx264 was measured encoding exactly that."""
        self.assertEqual(config.min_frame("libx264"), (2, 2))


class RoundTripTest(_TempConfig):
    def test_save_then_load_returns_the_same_values(self) -> None:
        original = config.Settings(
            source_kind=config.SOURCE_REGION,
            monitor_index=1,
            region=[10, 20, 640, 480],
            window_title="Блокнот",
            framerate=30,
            profile="x264",
            quality_level="best",
            draw_mouse=False,
            click_highlight=False,
            mic_enabled=True,
            mic_device="@device_cm_{33D9A762}\\wave_{113C1505}",
            mic_label="Микрофон (USB Microphone)",
            system_audio_enabled=True,
            system_audio_device=7,
            audio_mode=config.AUDIO_SEPARATE,
            output_dir=r"D:\Captures",
            filename_pattern="Rec_%H-%M-%S",
            hotkey="Ctrl+Shift+F9",
            ffmpeg_dir=r"C:\ffmpeg\bin",
            countdown=3,
        )
        self.assertEqual(config.save_settings(original, self.path), "")
        self.assertTrue(self.path.is_file())

        loaded, problem = config.load_settings(self.path)
        self.assertEqual(problem, "")
        self.assertEqual(asdict(loaded), asdict(original))

    def test_every_field_survives_the_round_trip(self) -> None:
        """A new field added to Settings must be written and read back, not dropped."""
        self.assertEqual(config.save_settings(config.Settings(), self.path), "")
        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(written), {f.name for f in fields(config.Settings)})

    def test_save_creates_missing_parent_directories(self) -> None:
        nested = Path(self._dir.name) / "a" / "b" / "settings.json"
        self.assertEqual(config.save_settings(config.Settings(), nested), "")
        self.assertTrue(nested.is_file())

    def test_save_leaves_no_temporary_file_behind(self) -> None:
        self.assertEqual(config.save_settings(config.Settings(), self.path), "")
        leftovers = [p.name for p in self.path.parent.iterdir() if p.name != self.path.name]
        self.assertEqual(leftovers, [], f"stray files after an atomic save: {leftovers}")

    def test_save_failure_is_reported_not_raised(self) -> None:
        # A directory where the file should be: the write must fail and say so.
        blocked = Path(self._dir.name) / "blocked.json"
        blocked.mkdir()
        problem = config.save_settings(config.Settings(), blocked)
        self.assertNotEqual(problem, "")
        self.assertIn("could not be saved", problem)


class LoadFallbackTest(_TempConfig):
    def test_missing_file_is_not_a_problem(self) -> None:
        settings, problem = config.load_settings(self.path)
        self.assertEqual(problem, "")
        self.assertEqual(asdict(settings), asdict(config.Settings()))

    def test_corrupt_file_falls_back_and_explains(self) -> None:
        self.write('{"framerate": 30, "encoder":')
        settings, problem = config.load_settings(self.path)
        self.assertEqual(asdict(settings), asdict(config.Settings()))
        self.assertTrue(problem, "a corrupt settings file must report a problem")
        self.assertIn("defaults", problem)

    def test_non_object_json_falls_back_and_explains(self) -> None:
        for payload in ("[1, 2, 3]", '"a string"', "42", "null"):
            with self.subTest(payload=payload):
                self.write(payload)
                settings, problem = config.load_settings(self.path)
                self.assertEqual(asdict(settings), asdict(config.Settings()))
                self.assertTrue(problem, f"{payload!r} must report a problem")
                self.assertIn("defaults", problem)

    def test_empty_file_falls_back_and_explains(self) -> None:
        self.write("")
        settings, problem = config.load_settings(self.path)
        self.assertEqual(asdict(settings), asdict(config.Settings()))
        self.assertTrue(problem)


class CoerceTest(_TempConfig):
    def test_unknown_keys_are_ignored(self) -> None:
        settings = self.load_raw({"framerate": 30, "nonsense": 5, "__class__": "evil"})
        self.assertEqual(settings.framerate, 30)
        self.assertFalse(hasattr(settings, "nonsense"))

    def test_framerate_is_clamped(self) -> None:
        for given, expected in ((1, 5), (0, 5), (-100, 5), (5, 5), (60, 60), (240, 240), (1000, 240)):
            with self.subTest(given=given):
                self.assertEqual(self.load_raw({"framerate": given}).framerate, expected)

    def test_an_unknown_quality_level_falls_back(self) -> None:
        for given in ("", "ultra", "BEST", "0", "nonsense"):
            with self.subTest(given=given):
                self.assertEqual(
                    self.load_raw({"quality_level": given}).quality_level,
                    config.DEFAULT_QUALITY,
                )

    def test_every_quality_level_survives_a_round_trip(self) -> None:
        for level in config.QUALITY_LEVELS:
            with self.subTest(level=level):
                self.assertEqual(
                    self.load_raw({"quality_level": level}).quality_level, level
                )

    def test_cq_is_defined_for_every_profile_and_level(self) -> None:
        """A missing pairing would silently fall back and encode at the wrong quality."""
        for key, profile in config.PROFILES.items():
            for level in config.QUALITY_LEVELS:
                with self.subTest(profile=key, level=level):
                    self.assertIn(level, profile.cq)
                    self.assertIn(level, profile.mb_per_minute)
        for key, profile in config.PROFILES.items():
            ordered = [profile.cq[level] for level in config.QUALITY_LEVELS]
            self.assertEqual(ordered, sorted(ordered),
                             f"{key}: cq must rise as the quality level drops")
            sizes = [profile.mb_per_minute[level] for level in config.QUALITY_LEVELS]
            self.assertEqual(sizes, sorted(sizes, reverse=True),
                             f"{key}: the estimate must fall as the quality level drops")

    def test_the_estimate_scales_with_pixels_and_frame_rate(self) -> None:
        base = config.estimate_mb_per_minute("hevc420", "balanced", 1280, 720, 60)
        self.assertAlmostEqual(
            config.estimate_mb_per_minute("hevc420", "balanced", 2560, 1440, 60),
            base * 4, places=3)
        self.assertAlmostEqual(
            config.estimate_mb_per_minute("hevc420", "balanced", 1280, 720, 30),
            base / 2, places=3)
        self.assertGreater(
            config.estimate_mb_per_minute("hevc420", "balanced", 1280, 720, 60), 0)

    def test_countdown_is_clamped(self) -> None:
        for given, expected in ((-1, 0), (0, 0), (3, 3), (10, 10), (60, 10)):
            with self.subTest(given=given):
                self.assertEqual(self.load_raw({"countdown": given}).countdown, expected)

    def test_unknown_profile_falls_back(self) -> None:
        self.assertEqual(self.load_raw({"profile": "av1_qsv"}).profile,
                         config.DEFAULT_PROFILE)
        self.assertEqual(self.load_raw({"profile": "x264"}).profile, "x264")

    def test_unknown_source_kind_falls_back(self) -> None:
        self.assertEqual(self.load_raw({"source_kind": "webcam"}).source_kind, config.SOURCE_MONITOR)
        self.assertEqual(self.load_raw({"source_kind": config.SOURCE_WINDOW}).source_kind,
                         config.SOURCE_WINDOW)

    def test_unknown_audio_mode_falls_back(self) -> None:
        self.assertEqual(self.load_raw({"audio_mode": "stereo"}).audio_mode, config.AUDIO_MIX)
        self.assertEqual(self.load_raw({"audio_mode": config.AUDIO_SEPARATE}).audio_mode,
                         config.AUDIO_SEPARATE)

    def test_a_bool_is_not_accepted_where_an_int_is_expected(self) -> None:
        """`isinstance(True, int)` is True in Python; the guard must not rely on it."""
        for key in ("framerate", "monitor_index", "system_audio_device", "countdown"):
            default = getattr(config.Settings(), key)
            for value in (True, False):
                with self.subTest(key=key, value=value):
                    got = getattr(self.load_raw({key: value}), key)
                    self.assertEqual(got, default, f"{key} accepted the bool {value}")
                    self.assertNotIsInstance(got, bool)

    def test_an_int_is_not_accepted_where_a_bool_is_expected(self) -> None:
        for key in ("draw_mouse", "click_highlight", "mic_enabled", "system_audio_enabled"):
            default = getattr(config.Settings(), key)
            with self.subTest(key=key):
                self.assertEqual(getattr(self.load_raw({key: 1}), key), default)
                self.assertEqual(getattr(self.load_raw({key: 0}), key), default)

    def test_wrong_types_are_dropped(self) -> None:
        settings = self.load_raw({
            "framerate": "30",
            "profile": 5,
            "monitor_index": 1.5,
            "draw_mouse": "yes",
            "region": "0,0,100,100",
        })
        defaults = config.Settings()
        self.assertEqual(settings.framerate, defaults.framerate)
        self.assertEqual(settings.profile, defaults.profile)
        self.assertEqual(settings.monitor_index, defaults.monitor_index)
        self.assertEqual(settings.draw_mouse, defaults.draw_mouse)
        self.assertEqual(settings.region, [])

    def test_region_of_the_wrong_length_is_dropped(self) -> None:
        for bad in ([], [1], [1, 2], [1, 2, 3], [1, 2, 3, 4, 5]):
            with self.subTest(region=bad):
                self.assertEqual(self.load_raw({"region": bad}).region, [])
        self.assertEqual(self.load_raw({"region": [10, 20, 640, 480]}).region, [10, 20, 640, 480])

    def test_region_with_non_integer_members_is_dropped(self) -> None:
        self.assertEqual(self.load_raw({"region": [0, 0, 640.5, 480]}).region, [])
        self.assertEqual(self.load_raw({"region": [0, 0, "640", 480]}).region, [])

    def test_a_region_that_encloses_nothing_is_dropped(self) -> None:
        """Four integers are not a rectangle, and the difference was two dead buttons.

        Reproduced on 2026-11-05 against the shipped code: a settings file holding
        `{"source_kind": "region", "region": [100, 100, 0, 0]}` loaded with
        `problem == ""`, and then `Application.open_settings` and
        `Application.start_recording` both raised `ValueError: The rectangle 100,100
        0x0 does not overlap monitor 0 (0,0 2560x1440)` out of
        `recorder.surface_crop`. The program runs under `pythonw.exe`, so the
        traceback had no console to reach and the two buttons simply stopped working.
        """
        for bad in ([100, 100, 0, 0], [100, 100, 400, 0], [100, 100, 0, 300],
                    [100, 100, -400, -300], [0, 0, -1, 10]):
            with self.subTest(region=bad):
                self.assertEqual(self.load_raw({"region": bad}).region, [])

    def test_a_region_off_every_screen_is_kept(self) -> None:
        """A rectangle is a rectangle wherever it is. The monitor it was on may be
        unplugged today and back tomorrow, and `ui/app.py` answers a source it cannot
        resolve with "Choose something to record first." - a sentence, not a crash."""
        self.assertEqual(
            self.load_raw({"region": [9000, 9000, 400, 300]}).region,
            [9000, 9000, 400, 300],
        )

    def test_is_a_rectangle_is_the_one_rule(self) -> None:
        self.assertTrue(config.is_a_rectangle([0, 0, 2, 2]))
        self.assertTrue(config.is_a_rectangle([-100, -100, 1, 1]))
        self.assertFalse(config.is_a_rectangle([0, 0, 2]))
        self.assertFalse(config.is_a_rectangle([0, 0, 2, 2, 2]))
        self.assertFalse(config.is_a_rectangle([0, 0, 0, 2]))
        self.assertFalse(config.is_a_rectangle([]))
        self.assertFalse(
            config.is_a_rectangle([0, 0, True, 2]),
            "a JSON `true` is an int to Python and is not a width",
        )


class LanguageTest(_TempConfig):
    """The interface language: stored here, put in force by `apply_language`."""

    def setUp(self) -> None:
        super().setUp()
        # `i18n` holds one language for the whole process; leaving Russian behind
        # would be read by every test that runs after this file.
        self.addCleanup(i18n.set_language, i18n.LANG_EN)

    def test_the_codes_are_the_ones_i18n_offers(self) -> None:
        """The copy in `config` exists to keep Qt out; it must not drift."""
        self.assertEqual(config.LANGUAGES, i18n.LANGUAGES)

    def test_nothing_is_chosen_by_default(self) -> None:
        self.assertEqual(config.Settings().language, "")

    def test_a_choice_round_trips(self) -> None:
        for code in config.LANGUAGES:
            with self.subTest(code=code):
                original = config.Settings(language=code)
                self.assertEqual(config.save_settings(original, self.path), "")
                loaded, problem = config.load_settings(self.path)
                self.assertEqual(problem, "")
                self.assertEqual(loaded.language, code)

    def test_a_language_this_build_has_not_got_falls_back(self) -> None:
        for given in ("klingon", "EN", "ru-RU", "de", " ru"):
            with self.subTest(given=given):
                self.assertEqual(self.load_raw({"language": given}).language, "")

    def test_apply_language_puts_the_stored_one_in_force(self) -> None:
        settings = config.Settings(language=i18n.LANG_RU)
        self.assertEqual(config.apply_language(settings), i18n.LANG_RU)
        self.assertEqual(i18n.language(), i18n.LANG_RU)

    def test_apply_language_asks_the_system_when_nothing_is_stored(self) -> None:
        self.assertEqual(
            config.apply_language(config.Settings()), i18n.default_language()
        )

    def test_apply_language_survives_a_code_nobody_has(self) -> None:
        """Set by hand past `_coerce` - `apply_language` must not leave it in force."""
        settings = config.Settings()
        settings.language = "klingon"
        in_force = config.apply_language(settings)
        self.assertEqual(in_force, i18n.default_language())
        self.assertIn(in_force, i18n.LANGUAGES)

    def test_applying_a_language_tells_the_windows(self) -> None:
        heard: list[str] = []
        i18n.notifier.changed.connect(heard.append)
        self.addCleanup(i18n.notifier.changed.disconnect, heard.append)
        config.apply_language(config.Settings(language=i18n.LANG_RU))
        self.assertEqual(heard, [i18n.LANG_RU])


class PlayerLayoutSettingsTest(_TempConfig):
    """The `player_*` fields of docs/PLAYER_LAYOUTS.md §2.3."""

    def test_the_documented_defaults(self) -> None:
        settings = config.Settings()
        self.assertEqual(settings.player_layout, "")  # "" = ask every time
        self.assertEqual(settings.player_display_signature, "")
        self.assertFalse(settings.player_swapped)
        self.assertEqual(settings.player_split, 0.5)
        self.assertEqual(settings.player_tiling, "equal_lr")

    def test_the_ids_are_the_player_s_own(self) -> None:
        """`config` copies them to stay free of Qt; the copy must not drift."""
        self.assertEqual(config.PLAYER_LAYOUTS, layouts.LAYOUTS)
        self.assertEqual(config.PLAYER_TILINGS, layouts.TILINGS)
        self.assertEqual(config.DEFAULT_PLAYER_TILING, layouts.TILING_EQUAL_LR)

    def test_a_remembered_layout_round_trips(self) -> None:
        original = config.Settings(
            player_layout="stacked",
            player_display_signature="2560x1440+0+0@1.0|1440x2560+2560+0@1.0",
            player_swapped=True,
            player_split=0.35,
            player_tiling="big_right",
        )
        self.assertEqual(config.save_settings(original, self.path), "")
        loaded, problem = config.load_settings(self.path)
        self.assertEqual(problem, "")
        self.assertEqual(asdict(loaded), asdict(original))

    def test_every_layout_and_tiling_is_accepted(self) -> None:
        for layout in config.PLAYER_LAYOUTS:
            with self.subTest(layout=layout):
                self.assertEqual(
                    self.load_raw({"player_layout": layout}).player_layout, layout
                )
        for tiling in config.PLAYER_TILINGS:
            with self.subTest(tiling=tiling):
                self.assertEqual(
                    self.load_raw({"player_tiling": tiling}).player_tiling, tiling
                )

    def test_a_layout_nobody_has_falls_back_to_asking(self) -> None:
        for given in ("mosaic", "PIP", 7, None):
            with self.subTest(given=given):
                self.assertEqual(
                    self.load_raw({"player_layout": given}).player_layout, ""
                )

    def test_a_nonsense_tiling_falls_back(self) -> None:
        self.assertEqual(
            self.load_raw({"player_tiling": "diagonal"}).player_tiling, "equal_lr"
        )
        self.assertEqual(self.load_raw({"player_tiling": ""}).player_tiling, "equal_lr")

    def test_the_split_is_clamped_to_its_range(self) -> None:
        low, high = config.PLAYER_SPLIT_RANGE
        for given, expected in ((0.0, low), (-3.0, low), (0.5, 0.5), (9.0, high)):
            with self.subTest(split=given):
                self.assertEqual(
                    self.load_raw({"player_split": given}).player_split, expected
                )

    def test_a_whole_number_is_read_as_a_fraction(self) -> None:
        """JSON writes 1 for 1.0, and `_coerce` must not drop it as the wrong type."""
        self.assertEqual(
            self.load_raw({"player_split": 1}).player_split,
            config.PLAYER_SPLIT_RANGE[1],
        )

    def test_nan_falls_back_instead_of_slipping_through_the_clamp(self) -> None:
        """`json.loads` accepts the literal NaN, and no comparison with it is true."""
        self.write('{"player_split": NaN}')
        settings, problem = config.load_settings(self.path)
        self.assertEqual(problem, "")
        self.assertFalse(math.isnan(settings.player_split))
        self.assertEqual(settings.player_split, config.DEFAULT_PLAYER_SPLIT)

    def test_a_key_this_build_has_dropped_is_ignored_not_fatal(self) -> None:
        """Every settings file on this machine still holds the removed pip keys.

        Picture in picture was taken out of the player, and with it
        `player_pip_corner` and `player_pip_fraction` - but the file they were written
        to is still on disk with both of them in it. Skipping a key nothing knows is
        what keeps the rest of that file: the output folder, the hotkeys and the
        language must not be lost over two numbers that no longer mean anything.
        """
        self.write(
            '{"player_pip_corner": "tl", "player_pip_fraction": 0.4, '
            '"player_split": 0.35, "language": "ru"}'
        )
        settings, problem = config.load_settings(self.path)
        self.assertEqual(problem, "")
        self.assertEqual(settings.player_split, 0.35)
        self.assertEqual(settings.language, "ru")
        self.assertFalse(hasattr(settings, "player_pip_corner"))

    def test_the_wrong_type_is_dropped(self) -> None:
        defaults = config.Settings()
        for value in ("0.4", True, None, [0.4]):
            with self.subTest(value=value):
                self.assertEqual(
                    self.load_raw({"player_split": value}).player_split,
                    defaults.player_split,
                )
        self.assertEqual(
            self.load_raw({"player_swapped": 1}).player_swapped, defaults.player_swapped
        )


class ProfileCodecTest(unittest.TestCase):
    """A profile names a codec, not an encoder - the change of 2026-11-04.

    `encoder="hevc_nvenc"` was false in the data on every machine without NVENC, and
    `ui/app.py` corrected it at record time with an override that fired only when
    `pix_fmt == "yuv420p"`. The two consequences were real: the profile called
    "No GPU - x264" was silently overridden onto hardware, and the 4:4:4 profile was
    offered on machines that cannot encode 4:4:4 at all. These pin the data so that the
    lie cannot come back into it.
    """

    def test_no_profile_names_an_encoder_or_claims_a_gpu(self) -> None:
        for key, profile in config.PROFILES.items():
            with self.subTest(profile=key):
                self.assertFalse(
                    hasattr(profile, "encoder"),
                    "a profile must name a codec; the encoder is chosen at record time",
                )
                self.assertFalse(
                    hasattr(profile, "gpu"),
                    "`gpu` was `not software_only` and promised what only the probe can",
                )

    def test_every_profile_names_a_codec_that_has_a_name_somewhere(self) -> None:
        for key, profile in config.PROFILES.items():
            with self.subTest(profile=key):
                self.assertIn(profile.codec, ("hevc", "h264"))
                named = [
                    encoders.name_for(profile.codec, family)
                    for family in ("nvenc", "amf", "qsv", "mf", "cpu")
                ]
                self.assertTrue(
                    [name for name in named if name],
                    f"{key}: no family has an encoder name for {profile.codec!r}",
                )

    def test_only_the_x264_profile_asks_for_the_processor(self) -> None:
        software = {key for key, p in config.PROFILES.items() if p.software_only}
        self.assertEqual(software, {"x264"})

    def test_a_software_only_profile_names_a_codec_the_processor_has(self) -> None:
        """`libx265` is not a candidate, so a software-only H.265 profile would ask for
        a name that does not exist and land on H.264 without meaning to."""
        for key, profile in config.PROFILES.items():
            if profile.software_only:
                with self.subTest(profile=key):
                    self.assertTrue(encoders.name_for(profile.codec, "cpu"))

    def test_a_444_profile_is_never_impossible(self) -> None:
        """The floor has to be able to do it, or a 4:4:4 profile would have no answer at
        all on a machine with no hardware encoder. Measured on 2026-11-04:
        `ffmpeg -h encoder=libx264` lists `yuv444p`, and two frames of the real
        `ddagrab` pipeline encoded at `yuv444p` through `libx264` exit 0."""
        if any(p.pix_fmt == "yuv444p" for p in config.PROFILES.values()):
            self.assertTrue(encoders.supports_444("libx264"))


class ProfileTranslationTest(unittest.TestCase):
    """Every profile label and note has Russian, which nothing else checks.

    `tools/check_translations.py` cannot see these: the settings panel calls
    `_(profile.note)` over the table, and the checker collects *literals* handed to
    `_()`. `tests/test_i18n_tables.py` closes the same hole for `presets.BUILTIN` and
    `shellops.ACTIONS` and does not cover `config.PROFILES`.

    It is here because of a sentence nobody could finish: the `x264` profile's note
    still reads "For a machine with no NVENC", which was true when a profile named
    `hevc_nvenc` and is narrower than the truth now that `capabilities.choose` picks out
    of five families. Three reviews asked for it and each half of the change sits in a
    different agent's file - the English in `config.py`, the Russian in
    `translations/settings.py` - so each declined. This makes the half-change fail
    instead of shipping quietly: rewording a note without its Russian turns this red.

    It pins no wording. Rewording the Russian keeps it green.
    """

    def setUp(self) -> None:
        previous = i18n.language()
        self.addCleanup(i18n.set_language, previous)

    def _texts(self) -> list[str]:
        texts = [p.label for p in config.PROFILES.values()]
        texts += [p.note for p in config.PROFILES.values() if p.note]
        self.assertTrue(texts, "config.PROFILES is empty")
        return texts

    def test_every_profile_label_and_note_has_russian(self) -> None:
        self.assertEqual(
            i18n.missing_translations(self._texts()), [],
            "a profile shows English in the Russian interface; add it to "
            "app/screenrec/translations/settings.py",
        )

    def test_the_panel_really_changes_language(self) -> None:
        """What the panel builds: `_(profile.note)` over the table, under Russian."""
        i18n.set_language(i18n.LANG_RU)
        for key, profile in config.PROFILES.items():
            with self.subTest(profile=key):
                self.assertNotEqual(i18n._(profile.label), profile.label)
                if profile.note:
                    self.assertNotEqual(i18n._(profile.note), profile.note)


if __name__ == "__main__":
    unittest.main()
