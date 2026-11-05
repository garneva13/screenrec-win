"""The controller's half of the language switch, and the engine's.

Everything the user reads that is *not* a widget's own label is worded in
``screenrec/ui/app.py`` or returned as a ``problem`` string by the engine modules below
it - ``storage``, ``recorder``, ``multi``, ``ui/captureframe``. The widgets were taught
to retranslate first and this half was left behind, which is a particular kind of broken:
a fully Russian bar with an English sentence under it. These tests are that half.

``Application`` itself is not built here. Its constructor registers global hotkeys, puts
an icon in the system tray, starts audio meters and opens a window on every monitor -
none of which belongs in a unit test. What it *decides* is reachable without it: the
methods that word the status line and the source chip only touch ``self.bar`` and
``self.settings``, so they are exercised on a real ``Bar`` with those two fields set.
The one thing that cannot be reached that way - the order of calls inside ``__init__`` -
is checked by reading the source, which is what the shipped bug needed:
``apply_language`` existed, was documented as "called once, right after
load_settings", and had no caller.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import unittest
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from screenrec import config, i18n, multi, recorder, storage, winapi  # noqa: E402
from screenrec.player import open as player_open  # noqa: E402
from screenrec.ui import app as app_module  # noqa: E402
from screenrec.ui import captureframe as captureframe_module  # noqa: E402
from screenrec.ui import theme  # noqa: E402
from screenrec.ui.bar import Bar  # noqa: E402

LANDSCAPE = winapi.Monitor(
    index=0, device_name="\\\\.\\DISPLAY2", x=0, y=0, width=2560, height=1440,
    rotation=0, adapter="test",
)
PORTRAIT = winapi.Monitor(
    index=1, device_name="\\\\.\\DISPLAY1", x=2560, y=0, width=1440, height=2560,
    rotation=2, adapter="test",
)


class _StillRunning:
    """Stands in for a live ffmpeg: `Recorder.running` only asks it to poll."""

    @staticmethod
    def poll() -> None:
        return None


def _qt_app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    created = QtWidgets.QApplication([])
    theme.apply(created)
    return created


class LanguageTestCase(unittest.TestCase):
    """Restores the language afterwards: it is process-wide state, like a setting."""

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(i18n.set_language, i18n.language())


# --------------------------------------------------------------------- start-up


class StartUpLanguageTest(unittest.TestCase):
    """The stored choice has to be in force before the first window is built.

    Read from the source rather than run, because running it means a tray icon and
    three global hotkeys. The failure this guards against is not a wrong call but a
    missing one, and a missing call is exactly what source text shows.
    """

    def test_the_controller_applies_the_language_before_it_builds_anything(self) -> None:
        source = inspect.getsource(app_module.Application.__init__)
        self.assertIn(
            "apply_language", source,
            "the stored language is never put in force; the whole interface opens in "
            "English whatever settings.json says",
        )
        loaded = source.index("load_settings")
        applied = source.index("apply_language")
        self.assertLess(loaded, applied, "the settings have to be read first")
        built = source.index("Bar(")
        self.assertLess(
            applied, built,
            "a widget reads the catalogue in its constructor, so the language has to "
            "be in force before the first one is built",
        )

    def test_the_player_applies_it_too(self) -> None:
        """The player is a second process and reads the same settings file."""
        source = inspect.getsource(player_open.run)
        self.assertIn(
            "apply_language", source,
            "ScreenRecPlayer.cmd has no language control of its own, so without this "
            "the player can never be anything but English",
        )

    def test_apply_language_really_puts_the_stored_code_in_force(self) -> None:
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language(i18n.LANG_EN)
        config.apply_language(config.Settings(language=i18n.LANG_RU))
        self.assertEqual(i18n.language(), i18n.LANG_RU)


# ------------------------------------------------------------------ the status


class _Controller:
    """An `Application` with only the two fields its wording touches.

    `_say`, `_retranslate`, `_refresh_bar` and `_source_label` are taken from the real
    class, so a change to any of them is tested; everything the constructor would also
    have started is simply not here.
    """

    _say = app_module.Application._say
    _retranslate = app_module.Application._retranslate
    _refresh_bar = app_module.Application._refresh_bar
    _source_label = app_module.Application._source_label
    _chosen_monitor = app_module.Application._chosen_monitor

    def __init__(self, bar: Bar, settings: config.Settings) -> None:
        self.bar = bar
        self.settings = settings
        self.monitors = [LANDSCAPE, PORTRAIT]
        self.last_file = None
        self._status = None


class StatusLineTest(LanguageTestCase):
    """The line under the bar, which is the controller's and not the bar's."""

    def setUp(self) -> None:
        super().setUp()
        _qt_app()
        self.bar = Bar()
        self.addCleanup(self.bar.deleteLater)
        self.controller = _Controller(self.bar, config.Settings())

    def test_a_status_line_is_shown_in_the_language_in_force(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.controller._say(lambda: app_module._("Recording again."))
        shown = self.bar.status_label.text()
        self.assertNotEqual(
            shown, "Recording again.",
            "the status line is the sentence the user reads most often and it was "
            "going straight to the bar in English",
        )
        self.assertEqual(shown, i18n.CATALOGUE["Recording again."][i18n.LANG_RU])

    def test_the_line_already_on_screen_follows_a_language_change(self) -> None:
        i18n.set_language(i18n.LANG_EN)
        self.controller._say(lambda: app_module._("Recording again."))
        self.assertEqual(self.bar.status_label.text(), "Recording again.")
        i18n.set_language(i18n.LANG_RU)
        self.controller._retranslate()
        self.assertEqual(
            self.bar.status_label.text(),
            i18n.CATALOGUE["Recording again."][i18n.LANG_RU],
        )

    def test_a_filled_in_sentence_is_rebuilt_not_re_shown(self) -> None:
        """A line with a number in it has to be built again, not cached as text."""
        i18n.set_language(i18n.LANG_EN)
        self.controller._say(
            lambda: app_module._("Saved {name} — {size} MB").format(
                name="take_01.mp4", size="48"
            )
        )
        self.assertIn("take_01.mp4", self.bar.status_label.text())
        i18n.set_language(i18n.LANG_RU)
        self.controller._retranslate()
        shown = self.bar.status_label.text()
        self.assertIn("take_01.mp4", shown)
        self.assertIn("48", shown)
        self.assertNotIn("MB", shown)

    def test_a_problem_worded_elsewhere_is_shown_as_it_arrived(self) -> None:
        """`_say` also takes a plain string - a problem another module worded."""
        self.controller._say("ffmpeg said something odd", warn=True)
        self.assertEqual(self.bar.status_label.text(), "ffmpeg said something odd")
        i18n.set_language(i18n.LANG_RU)
        self.controller._retranslate()
        self.assertEqual(self.bar.status_label.text(), "ffmpeg said something odd")


class SourceChipTest(LanguageTestCase):
    """The biggest label on the bar, built entirely by the controller."""

    def setUp(self) -> None:
        super().setUp()
        _qt_app()
        self.bar = Bar()
        self.addCleanup(self.bar.deleteLater)
        self.settings = config.Settings()
        self.controller = _Controller(self.bar, self.settings)

    def _label(self) -> str:
        return self.controller._source_label()[1]

    def test_every_shape_of_the_chip_is_translated(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        shapes = {
            config.SOURCE_ALL_SCREENS: "screens",
            config.SOURCE_MONITOR: "Screen",
            config.SOURCE_REGION: "Region",
            config.SOURCE_WINDOW: "Choose",
        }
        for kind, english in shapes.items():
            with self.subTest(source=kind):
                self.settings.source_kind = kind
                self.settings.region = []
                self.settings.window_title = ""
                self.assertNotIn(english, self._label())

    def test_a_region_keeps_its_numbers(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.settings.source_kind = config.SOURCE_REGION
        self.settings.region = [0, 0, 800, 600]
        label = self._label()
        self.assertIn("800", label)
        self.assertIn("600", label)
        self.assertNotIn("Region", label)

    def test_the_chip_follows_a_language_change(self) -> None:
        self.settings.source_kind = config.SOURCE_ALL_SCREENS
        i18n.set_language(i18n.LANG_EN)
        self.controller._refresh_bar()
        english = self.bar.source_chip.label()
        i18n.set_language(i18n.LANG_RU)
        self.controller._retranslate()
        self.assertNotEqual(self.bar.source_chip.label(), english)

    def test_the_chip_borrows_the_picker_s_words_rather_than_its_own(self) -> None:
        """Two spellings of "All 3 screens" in one session is how a language drifts."""
        i18n.set_language(i18n.LANG_RU)
        self.settings.source_kind = config.SOURCE_ALL_SCREENS
        expected = app_module._("All {count} screens").format(
            count=2, word=app_module._screens_word(2)
        )
        self.assertEqual(self._label(), expected)


class FrameRateUnitTest(unittest.TestCase):
    """One unit for frames a second, in all three places that write it.

    The settings panel said "60 к/с", the pill beside it said "60 fps" and the strip
    shown while recording said "128 МБ · 60 fps" - three places, two units,
    one of them half-converted inside a single string. The unit itself is
    not pinned here, only that the other two agree with whichever one the
    panel's spin box uses.
    """

    def test_the_bar_and_the_panel_write_the_same_unit(self) -> None:
        unit = i18n.CATALOGUE[" fps"][i18n.LANG_RU].strip()
        for key in ("{rate} fps", "{size} MB · {fps} fps"):
            with self.subTest(key=key):
                russian = i18n.CATALOGUE[key][i18n.LANG_RU]
                # The placeholders carry English names; only words count.
                words = re.sub(r"{[^}]*}", "", russian)
                self.assertIn(unit, words)
                self.assertNotIn("fps", words)


# ------------------------------------------------------------------- the engine


class EngineMessageTest(LanguageTestCase):
    """The `problem` strings. They are not logs: `_say` puts them on the bar."""

    def setUp(self) -> None:
        super().setUp()
        i18n.set_language(i18n.LANG_RU)

    def test_the_disk_warning_is_translated_whole(self) -> None:
        space = storage.Space(free_bytes=4_000_000_000, total_bytes=1, minutes_left=2.0)
        warning = storage.warning_for(space)
        self.assertNotIn("GB left", warning)
        self.assertNotIn("of recording", warning)

    def test_every_shape_of_the_duration_phrase_is_translated(self) -> None:
        for minutes in (float("inf"), 60 * 25, 0.5, 90.0, 120.0, 12.0):
            with self.subTest(minutes=minutes):
                self.assertNotIn("recording", storage.duration(minutes))

    def test_free_space_reads_as_one_sentence(self) -> None:
        space = storage.Space(free_bytes=4_000_000_000, total_bytes=1, minutes_left=12.0)
        self.assertNotIn("free", space.human())

    def test_the_recorder_refuses_in_the_user_s_language(self) -> None:
        engine = recorder.Recorder("ffmpeg.exe")
        # `running` only asks this to poll; nothing was ever started.
        engine.process = _StillRunning()
        self.assertNotIn("already running", engine.start(None))

    def test_the_multi_screen_index_complains_in_the_user_s_language(self) -> None:
        missing = Path(__file__).resolve().parent / "no-such-recording.screenrec.json"
        _document, problem = multi.read_sidecar(missing)
        self.assertTrue(problem)
        self.assertNotIn("could not be read", problem)

    def test_the_capture_frame_refuses_in_the_user_s_language(self) -> None:
        _qt_app()
        frame = captureframe_module.CaptureFrame()
        self.addCleanup(frame.deleteLater)
        self.assertFalse(frame.show_for(None, None))
        self.assertNotIn("No monitor was given", frame.problem)

    def test_english_is_still_the_source_text(self) -> None:
        """The fallback that makes an untranslated string a correct sentence."""
        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(
            storage.duration(float("inf")), "an unknown amount of recording"
        )
        space = storage.Space(free_bytes=4_000_000_000, total_bytes=1, minutes_left=2.0)
        self.assertEqual(
            storage.warning_for(space),
            "Only 4.0 GB left — about 2 min of recording. "
            "The recording will stop when the disk fills.",
        )


if __name__ == "__main__":
    unittest.main()
