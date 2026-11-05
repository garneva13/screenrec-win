"""Checks for `screenrec.ui.settings_panel`.

Everything here runs under the offscreen platform plugin: no GPU, no recording, no
visible window. What is worth proving automatically, because each of these fails
quietly otherwise:

* the round trip - every control comes up showing the setting's value, and moving a
  control writes that value back into the `Settings` object and says so with `changed`;
* the panel never saves: the caller owns the file;
* an invalid hotkey is refused **in the panel** and leaves the setting alone;
* the estimate line really recomputes when the format, the quality, the frame rate or
  the capture size changes;
* the `Switch` paints differently in its three states, and its animation settles. A
  painted widget that looks identical on and off is a bug a `grab()`-only test misses.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from screenrec import audio as audio_mod  # noqa: E402
from screenrec import capabilities  # noqa: E402
from screenrec import config as config_mod  # noqa: E402
from screenrec import encoders as encoders_mod  # noqa: E402
from screenrec import i18n  # noqa: E402
from screenrec import storage as storage_mod  # noqa: E402
from screenrec.ui import settings_panel, theme  # noqa: E402


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if isinstance(existing, QtWidgets.QApplication):
        return existing
    return QtWidgets.QApplication(sys.argv[:1])


MICS = [
    audio_mod.MicDevice(name="Microphone (USB)", alt_name="@device_cm_{usbmic}"),
    audio_mod.MicDevice(name="Line In (Realtek)", alt_name="@device_cm_{realtek}"),
]
LOOPBACKS = [
    audio_mod.LoopbackDevice(index=7, name="Speakers (Realtek) [Loopback]",
                             channels=2, rate=48000),
    audio_mod.LoopbackDevice(index=9, name="DELL U2718Q [Loopback]",
                             channels=2, rate=48000),
]


class PanelTestCase(unittest.TestCase):
    """Shared fixture: a themed application and a panel over a real `Settings`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = _app()
        theme.apply(cls.qt_app)

    def setUp(self) -> None:
        self.settings = config_mod.Settings()
        self.panel = settings_panel.SettingsPanel(self.settings)
        self.panel.refresh_devices(MICS, LOOPBACKS)
        self.fired = 0
        self.panel.changed.connect(self._count)

    def tearDown(self) -> None:
        self.panel.deleteLater()
        self.qt_app.processEvents()

    def _count(self) -> None:
        self.fired += 1


class TestBuildsAndShowsTheSettings(PanelTestCase):
    def test_controls_show_the_settings_values(self) -> None:
        settings = self.settings
        self.assertEqual(self.panel.fps_spin.value(), settings.framerate)
        self.assertEqual(self.panel.profile_combo.currentData(), settings.profile)
        self.assertEqual(
            self.panel.profile_combo.currentText(),
            config_mod.PROFILES[settings.profile].label,
        )
        self.assertEqual(self.panel.quality_combo.currentData(), settings.quality_level)
        self.assertEqual(self.panel.tracks_combo.currentData(), settings.audio_mode)
        self.assertEqual(self.panel.cursor_switch.isChecked(), settings.draw_mouse)
        self.assertEqual(self.panel.clicks_switch.isChecked(), settings.click_highlight)
        self.assertEqual(self.panel.bar_switch.isChecked(), settings.show_island)
        self.assertEqual(self.panel.countdown_spin.value(), settings.countdown)
        self.assertEqual(
            self.panel.folder_edit.text(), str(settings.resolved_output_dir())
        )
        self.assertEqual(self.panel.hotkey_edit.text(), settings.hotkey)
        # Both audio sources default to off, so both combos sit on "Off".
        self.assertEqual(self.panel.mic_combo.currentText(), "Off")
        self.assertEqual(self.panel.system_combo.currentText(), "Off")
        self.assertEqual(self.fired, 0)

    def test_building_the_panel_fires_nothing(self) -> None:
        settings = config_mod.Settings(framerate=30, profile="hevc444",
                                       quality_level="best", mic_enabled=True,
                                       mic_device="@device_cm_{realtek}",
                                       mic_label="Line In (Realtek)",
                                       system_audio_enabled=True,
                                       system_audio_device=9, countdown=3,
                                       draw_mouse=False, show_island=False)
        panel = settings_panel.SettingsPanel(settings)
        fired: list[int] = []
        panel.changed.connect(lambda: fired.append(1))
        panel.refresh_devices(MICS, LOOPBACKS)
        self.assertEqual(fired, [])
        self.assertEqual(panel.fps_spin.value(), 30)
        self.assertEqual(panel.profile_combo.currentData(), "hevc444")
        self.assertEqual(panel.quality_combo.currentData(), "best")
        self.assertEqual(panel.mic_combo.currentText(), "Line In (Realtek)")
        self.assertEqual(panel.system_combo.currentData(), 9)
        self.assertEqual(panel.countdown_spin.value(), 3)
        self.assertFalse(panel.cursor_switch.isChecked())
        self.assertFalse(panel.bar_switch.isChecked())
        panel.deleteLater()

    def test_a_missing_remembered_microphone_is_kept_and_named(self) -> None:
        settings = config_mod.Settings(mic_enabled=True, mic_device="@gone",
                                       mic_label="USB mic")
        panel = settings_panel.SettingsPanel(settings)
        panel.refresh_devices(MICS, LOOPBACKS)
        self.assertEqual(panel.mic_combo.currentData(), "@gone")
        self.assertIn("not found", panel.mic_combo.currentText())
        self.assertEqual(settings.mic_device, "@gone")
        panel.deleteLater()

    def test_every_state_grabs(self) -> None:
        for mic, system in ((False, False), (True, False), (True, True)):
            self.settings.mic_enabled = mic
            self.settings.system_audio_enabled = system
            self.panel.refresh_from_settings()
            self.panel.refresh_devices(MICS, LOOPBACKS)
            image = self.panel.grab()
            self.assertFalse(image.isNull())
            self.assertEqual(image.width(), settings_panel.WIDTH)


class TestDevicesAgreeWithTheSettings(PanelTestCase):
    """What the two combo boxes show is what the next recording will use.

    `recorder.RecordingPlan.has_mic` is `bool(mic_device)`, so a panel that shows a
    microphone over an empty `mic_device` promises sound and records silence.
    """

    @staticmethod
    def _panel_for(settings: config_mod.Settings) -> tuple[object, list[int]]:
        panel = settings_panel.SettingsPanel(settings)
        fired: list[int] = []
        panel.changed.connect(lambda: fired.append(1))
        return panel, fired

    def test_a_microphone_that_is_on_but_unnamed_is_resolved_and_written_back(
        self,
    ) -> None:
        settings = config_mod.Settings(mic_enabled=True, mic_device="")
        panel, fired = self._panel_for(settings)
        panel.refresh_devices(MICS, LOOPBACKS)
        self.assertEqual(panel.mic_combo.currentText(), "Microphone (USB)")
        self.assertEqual(settings.mic_device, "@device_cm_{usbmic}")
        self.assertEqual(settings.mic_label, "Microphone (USB)")
        self.assertTrue(settings.mic_enabled)
        self.assertEqual(fired, [1])
        panel.deleteLater()

    def test_a_microphone_that_is_on_with_no_microphones_is_turned_off(self) -> None:
        settings = config_mod.Settings(mic_enabled=True, mic_device="")
        panel, fired = self._panel_for(settings)
        panel.refresh_devices([], LOOPBACKS)
        self.assertEqual(panel.mic_combo.currentText(), "Off")
        self.assertFalse(settings.mic_enabled)
        self.assertEqual(fired, [1])
        panel.deleteLater()

    def test_an_unplugged_loopback_is_named_not_substituted(self) -> None:
        settings = config_mod.Settings(system_audio_enabled=True,
                                       system_audio_device=42)
        panel, fired = self._panel_for(settings)
        panel.refresh_devices(MICS, LOOPBACKS)
        self.assertEqual(panel.system_combo.currentData(), 42)
        self.assertIn("not found", panel.system_combo.currentText())
        # The remembered endpoint survives: it comes back when it is plugged in.
        self.assertEqual(settings.system_audio_device, 42)
        self.assertEqual(fired, [])
        panel.deleteLater()

    def test_nothing_is_corrected_before_the_devices_are_known(self) -> None:
        """An empty list nobody filled in is not evidence that there are no devices."""
        settings = config_mod.Settings(mic_enabled=True, mic_device="",
                                       system_audio_enabled=True,
                                       system_audio_device=9)
        panel, fired = self._panel_for(settings)
        panel.refresh_from_settings()
        self.assertTrue(settings.mic_enabled)
        self.assertEqual(settings.mic_device, "")
        self.assertEqual(settings.system_audio_device, 9)
        self.assertEqual(fired, [])
        # And once they are known, the remembered loopback is found again.
        panel.refresh_devices(MICS, LOOPBACKS)
        self.assertEqual(panel.system_combo.currentData(), 9)
        self.assertEqual(settings.system_audio_device, 9)
        panel.deleteLater()


class TestWritesBack(PanelTestCase):
    def test_frame_rate(self) -> None:
        self.panel.fps_spin.setValue(30)
        self.assertEqual(self.settings.framerate, 30)
        self.assertEqual(self.fired, 1)

    def test_format(self) -> None:
        index = self.panel.profile_combo.findData("hevc444")
        self.panel.profile_combo.setCurrentIndex(index)
        self.assertEqual(self.settings.profile, "hevc444")
        self.assertEqual(self.fired, 1)
        self.assertEqual(
            self.panel.profile_note.text(), config_mod.PROFILES["hevc444"].note
        )
        self.assertIn("does not decode 4:4:4", self.panel.profile_note.text())

    def test_quality(self) -> None:
        index = self.panel.quality_combo.findData("best")
        self.panel.quality_combo.setCurrentIndex(index)
        self.assertEqual(self.settings.quality_level, "best")
        self.assertEqual(self.fired, 1)

    def test_microphone_on_and_off(self) -> None:
        index = self.panel.mic_combo.findData("@device_cm_{usbmic}")
        self.panel.mic_combo.setCurrentIndex(index)
        self.assertTrue(self.settings.mic_enabled)
        self.assertEqual(self.settings.mic_device, "@device_cm_{usbmic}")
        self.assertEqual(self.settings.mic_label, "Microphone (USB)")
        self.assertEqual(self.fired, 1)

        self.panel.mic_combo.setCurrentIndex(0)
        self.assertFalse(self.settings.mic_enabled)
        self.assertEqual(self.fired, 2)
        # Turning it off must not forget which device it was.
        self.assertEqual(self.settings.mic_device, "@device_cm_{usbmic}")

    def test_system_sound(self) -> None:
        index = self.panel.system_combo.findData(7)
        self.panel.system_combo.setCurrentIndex(index)
        self.assertTrue(self.settings.system_audio_enabled)
        self.assertEqual(self.settings.system_audio_device, 7)
        self.assertEqual(self.fired, 1)

    def test_tracks_only_when_both_sources_are_on(self) -> None:
        self.assertFalse(self.panel.tracks_combo.isEnabled())
        self.panel.mic_combo.setCurrentIndex(
            self.panel.mic_combo.findData("@device_cm_{usbmic}")
        )
        self.assertFalse(self.panel.tracks_combo.isEnabled())
        self.panel.system_combo.setCurrentIndex(self.panel.system_combo.findData(7))
        self.assertTrue(self.panel.tracks_combo.isEnabled())

        index = self.panel.tracks_combo.findData(config_mod.AUDIO_SEPARATE)
        self.panel.tracks_combo.setCurrentIndex(index)
        self.assertEqual(self.settings.audio_mode, config_mod.AUDIO_SEPARATE)
        self.assertEqual(self.fired, 3)

    def test_switches(self) -> None:
        cases = (
            (self.panel.cursor_switch, "draw_mouse"),
            (self.panel.clicks_switch, "click_highlight"),
            (self.panel.bar_switch, "show_island"),
        )
        for switch, field in cases:
            with self.subTest(field=field):
                before = getattr(self.settings, field)
                fired_before = self.fired
                switch.click()
                self.assertEqual(getattr(self.settings, field), not before)
                self.assertEqual(self.fired, fired_before + 1)

    def test_capture_frame_switch_writes_the_documented_attribute(self) -> None:
        before = getattr(
            self.settings,
            settings_panel.CAPTURE_FRAME_ATTR,
            settings_panel.CAPTURE_FRAME_DEFAULT,
        )
        self.panel.frame_switch.click()
        self.assertEqual(
            getattr(self.settings, settings_panel.CAPTURE_FRAME_ATTR), not before
        )
        self.assertEqual(self.fired, 1)

    def test_countdown(self) -> None:
        self.panel.countdown_spin.setValue(5)
        self.assertEqual(self.settings.countdown, 5)
        self.assertEqual(self.fired, 1)

    def test_space_toggles_a_switch_from_the_keyboard(self) -> None:
        switch = self.panel.cursor_switch
        before = self.settings.draw_mouse
        switch.setFocus()
        QTest.keyClick(switch, QtCore.Qt.Key.Key_Space)
        self.assertEqual(self.settings.draw_mouse, not before)
        self.assertEqual(self.fired, 1)

    def test_the_panel_never_saves_the_file(self) -> None:
        """`config.save_settings` is the caller's job; the panel must not call it."""
        calls: list[object] = []
        original = config_mod.save_settings
        config_mod.save_settings = lambda *a, **k: calls.append(a) or ""  # type: ignore
        try:
            self.panel.fps_spin.setValue(24)
            self.panel.cursor_switch.click()
            self.panel.hotkey_edit.setText("Ctrl+Shift+F9")
            self.panel.hotkey_edit.editingFinished.emit()
        finally:
            config_mod.save_settings = original  # type: ignore[assignment]
        self.assertEqual(calls, [])
        self.assertEqual(self.settings.hotkey, "Ctrl+Shift+F9")


class TestHotkey(PanelTestCase):
    def test_invalid_text_is_refused_in_words_and_changes_nothing(self) -> None:
        before = self.settings.hotkey
        self.panel.hotkey_edit.setText("Banana")
        self.panel.hotkey_edit.editingFinished.emit()
        self.assertEqual(self.settings.hotkey, before)
        self.assertEqual(self.fired, 0)
        self.assertTrue(self.panel.hotkey_problem.isVisibleTo(self.panel))
        message = self.panel.hotkey_problem.text()
        self.assertIn("Banana", message)
        self.assertIn("Ctrl+Alt+R", message)
        self.assertIn(before, message)

    def test_an_empty_box_is_refused_too(self) -> None:
        before = self.settings.hotkey
        self.panel.hotkey_edit.setText("   ")
        self.panel.hotkey_edit.editingFinished.emit()
        self.assertEqual(self.settings.hotkey, before)
        self.assertEqual(self.fired, 0)
        self.assertIn("empty", self.panel.hotkey_problem.text())

    def test_a_valid_hotkey_is_accepted_and_canonicalised(self) -> None:
        self.panel.hotkey_edit.setText("  ctrl + shift + f9 ")
        self.panel.hotkey_edit.editingFinished.emit()
        self.assertEqual(self.settings.hotkey, "Ctrl+Shift+F9")
        self.assertEqual(self.panel.hotkey_edit.text(), "Ctrl+Shift+F9")
        self.assertEqual(self.fired, 1)
        self.assertFalse(self.panel.hotkey_problem.isVisibleTo(self.panel))

    def test_a_refusal_is_cleared_by_a_good_one(self) -> None:
        self.panel.hotkey_edit.setText("Ctrl+")
        self.panel.hotkey_edit.editingFinished.emit()
        self.assertTrue(self.panel.hotkey_problem.text())
        self.panel.hotkey_edit.setText("Ctrl+Alt+K")
        self.panel.hotkey_edit.editingFinished.emit()
        self.assertEqual(self.panel.hotkey_problem.text(), "")
        self.assertEqual(self.settings.hotkey, "Ctrl+Alt+K")


class TestEstimate(PanelTestCase):
    def test_it_starts_at_the_default_capture_size_and_says_it_is_an_estimate(
        self,
    ) -> None:
        text = self.panel.estimate_label.text()
        self.assertEqual(self.panel.capture_size(), settings_panel.DEFAULT_CAPTURE)
        self.assertIn("Estimated", text)
        self.assertIn("1920×1080", text)
        self.assertIn("MB a minute", text)

    def test_it_changes_with_the_format(self) -> None:
        before = self.panel.estimate_label.text()
        self.panel.profile_combo.setCurrentIndex(
            self.panel.profile_combo.findData("h264")
        )
        after = self.panel.estimate_label.text()
        self.assertNotEqual(before, after)

    def test_it_changes_with_quality_frame_rate_and_capture_size(self) -> None:
        start = self.panel.estimate_label.text()
        self.panel.quality_combo.setCurrentIndex(
            self.panel.quality_combo.findData("best")
        )
        after_quality = self.panel.estimate_label.text()
        self.assertNotEqual(start, after_quality)

        self.panel.fps_spin.setValue(30)
        after_fps = self.panel.estimate_label.text()
        self.assertNotEqual(after_quality, after_fps)

        self.panel.set_capture_size(2560, 1440)
        after_size = self.panel.estimate_label.text()
        self.assertNotEqual(after_fps, after_size)
        self.assertIn("2560×1440", after_size)

    def test_the_four_four_four_warning_is_never_truncated(self) -> None:
        self.panel.profile_combo.setCurrentIndex(
            self.panel.profile_combo.findData("hevc444")
        )
        note = self.panel.profile_note
        self.assertEqual(note.text(), config_mod.PROFILES["hevc444"].note)
        self.assertTrue(note.wordWrap())
        # The note has to be laid out before it is measured: unshown it is still at its
        # default 640 px, where the warning fits on two lines and the check proves
        # nothing about the 328 px it really gets.
        self.panel.show()
        self.qt_app.processEvents()
        try:
            self.assertLess(note.width(), settings_panel.WIDTH)
            needed = note.heightForWidth(note.width())
            self.assertGreaterEqual(note.height(), needed)
            # The whole note fits inside the panel, which grew to make room for it.
            # `totalHeightForWidth` and not `sizeHint`: the hint is for the width the
            # wrapping label wants, which is not the width it has.
            self.assertGreaterEqual(
                self.panel.height(),
                self.panel.layout().totalHeightForWidth(self.panel.width()),
            )
        finally:
            self.panel.hide()


class TestSwitch(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = _app()
        theme.apply(cls.qt_app)

    def test_three_states_grab_and_differ(self) -> None:
        switch = settings_panel.Switch()
        switch.resize(switch.sizeHint())

        switch.set_checked_now(False)
        off = switch.grab().toImage()
        switch.set_checked_now(True)
        on = switch.grab().toImage()
        switch.setEnabled(False)
        disabled = switch.grab().toImage()

        for name, image in (("off", off), ("on", on), ("disabled", disabled)):
            with self.subTest(state=name):
                self.assertFalse(image.isNull())
                self.assertEqual(image.width(), settings_panel.Switch.WIDTH)
        self.assertNotEqual(on, off)
        self.assertNotEqual(on, disabled)

        # The pill really is the accent colour when it is on, and is not when it is off.
        accent = QtGui.QColor(theme.COLOURS["accent"]).rgb()
        left = QtCore.QPoint(5, switch.height() // 2)
        self.assertEqual(on.pixel(left), accent)
        self.assertNotEqual(off.pixel(left), accent)
        switch.deleteLater()

    def test_the_knob_travels_and_settles(self) -> None:
        switch = settings_panel.Switch()
        switch.resize(switch.sizeHint())
        self.assertEqual(switch.travel(), 0.0)
        switch.click()
        self.assertTrue(switch.isChecked())
        deadline = QtCore.QElapsedTimer()
        deadline.start()
        while switch.travel() < 1.0 and deadline.elapsed() < 2000:
            self.qt_app.processEvents(
                QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20
            )
        self.assertEqual(switch.travel(), 1.0)
        switch.deleteLater()

    def test_set_checked_now_is_silent(self) -> None:
        switch = settings_panel.Switch()
        seen: list[bool] = []
        switch.toggled.connect(seen.append)
        switch.set_checked_now(True)
        self.assertTrue(switch.isChecked())
        self.assertEqual(seen, [])
        self.assertEqual(switch.travel(), 1.0)
        switch.deleteLater()


class TestPlacement(PanelTestCase):
    def test_popup_near_keeps_the_panel_on_screen(self) -> None:
        anchor = QtWidgets.QWidget()
        anchor.resize(120, 32)
        anchor.move(10, 10)
        anchor.show()
        self.panel.popup_near(anchor)
        area = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        geometry = self.panel.geometry()
        self.assertGreaterEqual(geometry.left(), area.left())
        self.assertLessEqual(geometry.right(), area.right())
        self.assertGreaterEqual(geometry.top(), area.top())
        self.assertTrue(self.panel.isVisible())
        self.panel.hide()
        anchor.deleteLater()

    def test_close_hides_and_reports(self) -> None:
        seen: list[int] = []
        self.panel.closed.connect(lambda: seen.append(1))
        self.panel.show()
        self.panel.close_button.click()
        self.assertEqual(seen, [1])
        self.assertFalse(self.panel.isVisible())


class TestTheLanguageRow(PanelTestCase):
    """Choosing a language, and the panel saying everything again afterwards."""

    def setUp(self) -> None:
        super().setUp()
        # One language is in force for the whole process; English has to be put back
        # or every test file that runs after this one reads Russian.
        self.addCleanup(i18n.set_language, i18n.LANG_EN)

    def test_it_offers_every_language_in_its_own_name(self) -> None:
        combo = self.panel.language_combo
        self.assertEqual(
            [combo.itemData(i) for i in range(combo.count())], list(i18n.LANGUAGES)
        )
        self.assertEqual(
            [combo.itemText(i) for i in range(combo.count())],
            [i18n.NATIVE_NAMES[code] for code in i18n.LANGUAGES],
        )

    def test_it_shows_the_language_in_force_when_none_is_stored(self) -> None:
        self.assertEqual(self.settings.language, "")
        self.assertEqual(self.panel.language_combo.currentData(), i18n.language())

    def test_choosing_one_switches_stores_and_asks_for_a_save(self) -> None:
        index = self.panel.language_combo.findData(i18n.LANG_RU)
        self.panel.language_combo.setCurrentIndex(index)
        self.assertEqual(i18n.language(), i18n.LANG_RU)
        self.assertEqual(self.settings.language, i18n.LANG_RU)
        self.assertEqual(self.fired, 1, "the application has to hear it to save it")

    def test_choosing_one_emits_the_notifier(self) -> None:
        heard: list[str] = []
        i18n.notifier.changed.connect(heard.append)
        self.addCleanup(i18n.notifier.changed.disconnect, heard.append)
        self.panel.language_combo.setCurrentIndex(
            self.panel.language_combo.findData(i18n.LANG_RU)
        )
        self.assertEqual(heard, [i18n.LANG_RU])

    def test_the_text_really_changes(self) -> None:
        """Not that `retranslate` was called - that the widgets say something else."""
        before = {
            "title": self._caption("Recording settings"),
            "quality": self._caption("Quality"),
            "format": self.panel.profile_combo.currentText(),
            "off": self.panel.mic_combo.itemText(0),
            "tracks": self.panel.tracks_note.text(),
            "estimate": self.panel.estimate_label.text(),
        }
        for value in before.values():
            self.assertTrue(value, "nothing to compare against")

        self.panel.language_combo.setCurrentIndex(
            self.panel.language_combo.findData(i18n.LANG_RU)
        )

        self.assertEqual(self._caption("Recording settings"), "Настройки записи")
        self.assertEqual(self._caption("Quality"), "Качество")
        self.assertEqual(self.panel.mic_combo.itemText(0), "Выкл.")
        self.assertIn("МБ в минуту", self.panel.estimate_label.text())
        self.assertIn("Для двух дорожек", self.panel.tracks_note.text())
        self.assertNotEqual(self.panel.profile_combo.currentText(), before["format"])

    def test_switching_keeps_every_choice(self) -> None:
        """Refilling the lists in another language must not change a setting."""
        self.settings.quality_level = "best"
        self.settings.profile = "x264"
        self.settings.audio_mode = config_mod.AUDIO_SEPARATE
        self.panel.refresh_from_settings()
        self.fired = 0

        i18n.set_language(i18n.LANG_RU)

        self.assertEqual(self.panel.profile_combo.currentData(), "x264")
        self.assertEqual(self.panel.quality_combo.currentData(), "best")
        self.assertEqual(
            self.panel.tracks_combo.currentData(), config_mod.AUDIO_SEPARATE
        )
        self.assertEqual(self.settings.profile, "x264")
        self.assertEqual(self.settings.quality_level, "best")
        self.assertEqual(self.fired, 0, "saying it again is not the user changing it")

    def test_a_switch_from_elsewhere_reaches_the_panel(self) -> None:
        """Another window's language row must change this one too."""
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(self._caption("Language"), "Язык")
        self.assertEqual(self.panel.language_combo.currentData(), i18n.LANG_RU)

    def test_a_hotkey_complaint_is_said_again_not_swallowed(self) -> None:
        self.panel.hotkey_edit.setText("Ctrl+")
        self.panel.hotkey_edit.editingFinished.emit()
        self.assertTrue(self.panel.hotkey_problem.text())
        i18n.set_language(i18n.LANG_RU)
        said = self.panel.hotkey_problem.text()
        self.assertTrue(self.panel.hotkey_problem.isVisibleTo(self.panel))
        self.assertIn("не горячая клавиша", said)
        self.assertIn("Ctrl+", said)

    def test_the_note_says_what_changes_at_once(self) -> None:
        self.assertTrue(self.panel.language_note.text())
        i18n.set_language(i18n.LANG_RU)
        self.assertIn("сразу", self.panel.language_note.text())

    def _caption(self, source: str) -> str:
        """What the panel is currently showing for the English caption `source`."""
        for widget, text, upper in self.panel._captions:
            if text == source:
                return widget.text().capitalize() if upper else widget.text()
        self.fail(f"no caption registered for {source!r}")
        return ""


def _result(name: str, works: bool, reason: str = "") -> capabilities.EncoderResult:
    """One probe row, with the codec and vendor `capabilities.CANDIDATES` gives it.

    Built from the real table rather than by hand, so a test machine that is invented
    here cannot claim a vendor the program does not know about.
    """
    for candidate, codec, vendor in capabilities.CANDIDATES:
        if candidate == name:
            return capabilities.EncoderResult(
                name=name, codec=codec, vendor=vendor, listed=True, works=works,
                reason=reason, millis=120,
            )
    raise AssertionError(f"{name} is not one of capabilities.CANDIDATES")


def _machine(*working: str) -> list[capabilities.EncoderResult]:
    """A whole probe answer: every candidate, with only `working` passing.

    Every row, not only the interesting ones, because that is what the application
    hands the panel and because the expander is supposed to show all of them.
    """
    rows: list[capabilities.EncoderResult] = []
    for name, _codec, _vendor in capabilities.CANDIDATES:
        rows.append(
            _result(name, name in working,
                    "" if name in working else "Nothing was written into output file")
        )
    return rows


#: This machine, as the probe really answers on it (RTX 3070 Ti, 2026-11-04): both
#: NVENC paths, both Media Foundation paths and libx264 work; every AMD, Intel and AV1
#: candidate is refused.
THIS_MACHINE = ("hevc_nvenc", "h264_nvenc", "hevc_mf", "h264_mf", "libx264")


class TestWhichEncoderIsInForce(PanelTestCase):
    """The one line under the format picker, and what it refuses to claim.

    AMD and Intel rows in these tests are invented: this machine has an RTX 3070 Ti and
    no AMD or Intel graphics, so the only thing that can be tested about those families
    is that the panel reads `capabilities` and `encoders` instead of deciding anything
    itself. Both of those modules carry their own measurements.
    """

    def test_it_says_nothing_until_it_has_been_told(self) -> None:
        """A sentence written before anybody asked the machine is the bug, not the gap."""
        self.assertEqual(self.panel.encoding_note.text(), "")
        self.assertFalse(self.panel.encoding_note.isVisibleTo(self.panel))
        self.assertFalse(self.panel.details_button.isVisibleTo(self.panel))
        self.assertFalse(self.panel.profile_problem.isVisibleTo(self.panel))

    def test_an_empty_probe_is_an_answer_and_not_a_silence(self) -> None:
        self.panel.set_encoders([])
        self.assertTrue(self.panel.details_button.isVisibleTo(self.panel))
        self.assertIn("processor", self.panel.encoding_note.text())

    def test_set_encoders_is_not_the_user_changing_anything(self) -> None:
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        self.assertEqual(self.fired, 0)

    def test_hardware_that_keeps_the_frame_on_the_card(self) -> None:
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        text = self.panel.encoding_note.text()
        self.assertTrue(self.panel.encoding_note.isVisibleTo(self.panel))
        self.assertIn("graphics card", text)
        self.assertIn("never leaves", text)
        self.assertIn("H.265", text)

    def test_the_line_names_the_codec_and_never_the_vendor(self) -> None:
        """The vendor belongs in the expander, which is where somebody looks when
        something is wrong. On the line it only invites "but I have an NVIDIA card"."""
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        for vendor in ("NVIDIA", "AMD", "Intel", "nvenc", "hevc_nvenc"):
            with self.subTest(vendor=vendor):
                self.assertNotIn(vendor, self.panel.encoding_note.text())

    def test_hardware_through_system_memory_says_what_it_costs(self) -> None:
        """4:4:4 on NVENC really does go through `hwdownload` - measured, not assumed."""
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        self.panel.profile_combo.setCurrentIndex(
            self.panel.profile_combo.findData("hevc444")
        )
        text = self.panel.encoding_note.text()
        self.assertIn("system memory", text)
        # A span, not a middle. Five runs of the same command on this machine spread
        # from 46% of a core to 154.5%; `ui/settings_panel.py` records all of them.
        self.assertIn("half a processor core", text)
        self.assertIn("one and a half", text)
        self.assertIn(
            "hwdownload",
            encoders_mod.frame_path("hevc_nvenc", pix_fmt="yuv444p"),
            "the sentence is only true while this is the path",
        )

    def test_no_hardware_at_all_says_the_processor_is_doing_it(self) -> None:
        self.panel.set_encoders(_machine("libx264"))
        text = self.panel.encoding_note.text()
        self.assertIn("No hardware encoder works", text)
        self.assertIn("processor", text)
        # The format asked for H.265 and there is no H.265 encoder on the processor,
        # so the line says what will really be written rather than what was asked for.
        self.assertIn("H.264", text)

    def test_the_software_format_is_not_called_a_missing_card(self) -> None:
        """A format named "No GPU - x264" on a machine with a working card is a
        choice, not a defect."""
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        self.panel.profile_combo.setCurrentIndex(
            self.panel.profile_combo.findData("x264")
        )
        text = self.panel.encoding_note.text()
        self.assertIn("This format asks for the processor", text)
        self.assertNotIn("No hardware encoder works", text)

    def test_a_hand_written_encoder_choice_is_what_the_line_describes(self) -> None:
        """`Settings.encoder_choice` has no control, and still has to be told the truth
        about: a JSON file naming libx264 records on libx264."""
        self.settings.encoder_choice = "libx264"
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        self.assertIn("processor", self.panel.encoding_note.text())

        self.settings.encoder_choice = "auto"
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        self.assertIn("never leaves", self.panel.encoding_note.text())

    def test_a_nonsense_encoder_choice_falls_back_to_the_probe(self) -> None:
        self.settings.encoder_choice = "hevc_banana"
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        self.assertIn("never leaves", self.panel.encoding_note.text())

    def test_the_line_is_said_again_in_russian(self) -> None:
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        i18n.set_language(i18n.LANG_RU)
        text = self.panel.encoding_note.text()
        self.assertIn("видеокарта", text)
        self.assertIn("H.265", text, "a codec is not translated")


class TestTheProcessorIsNotAlwaysAMissingCard(PanelTestCase):
    """"No hardware encoder works on this machine" was said in three cases and is true
    in one of them.

    Reproduced on 2026-11-05 with a real panel and the probe rows
    `hevc_mf, h264_mf, libx264`, profile `hevc444`: Media Foundation works perfectly
    well, it simply does not take 4:4:4 - `encoders.YUV444_ENCODERS`, measured against
    this build's own `ffmpeg -h encoder=...` - so `choose` lands on `libx264`,
    `_unavailable_reason` stays empty because libx264 *does* keep 4:4:4, and the panel
    offered the format and told the user their graphics card was broken in one breath.

    The machines here are invented: this one has an RTX 3070 Ti and no AMD or Intel
    graphics, so what can be tested is which sentence the panel picks, not the silicon.
    """

    #: Windows' own path plus the processor. Nothing here takes 4:4:4 except libx264.
    MF_AND_CPU = ("hevc_mf", "h264_mf", "libx264")
    #: The same shape an AMD machine would have, by `docs/ENCODERS.md` §3.
    AMF_AND_CPU = ("hevc_amf", "h264_amf", "hevc_mf", "h264_mf", "libx264")

    def _sentence(self, working, profile_key) -> str:
        self.settings.profile = profile_key
        self.panel.refresh_from_settings()
        self.panel.set_encoders(_machine(*working))
        return self.panel.encoding_note.text()

    def test_hardware_that_cannot_do_this_format_is_not_called_broken(self) -> None:
        for working in (self.MF_AND_CPU, self.AMF_AND_CPU):
            with self.subTest(machine=working):
                text = self._sentence(working, "hevc444")
                self.assertNotIn(
                    "No hardware encoder works", text,
                    "it works; it just does not take 4:4:4",
                )
                self.assertIn("cannot record this format", text)
                self.assertIn("processor", text)

    def test_a_machine_with_nothing_at_all_still_gets_the_old_sentence(self) -> None:
        self.assertIn(
            "No hardware encoder works", self._sentence(("libx264",), "hevc420")
        )

    def test_a_software_profile_is_still_a_choice_and_not_a_defect(self) -> None:
        text = self._sentence(self.MF_AND_CPU, "x264")
        self.assertIn("This format asks for the processor", text)
        self.assertNotIn("No hardware encoder works", text)
        self.assertNotIn("cannot record this format", text)

    def test_every_processor_sentence_is_whole_english(self) -> None:
        """Both of the two that existed ended "- processor the program you are
        recording may need", which is missing a word."""
        seen = set()
        for working, key in ((self.MF_AND_CPU, "hevc444"), (("libx264",), "hevc420"),
                             (self.MF_AND_CPU, "x264")):
            with self.subTest(machine=working, profile=key):
                text = self._sentence(working, key)
                seen.add(text)
                self.assertIn("processor time the program you are recording", text)
                self.assertNotIn("- processor the program", text)
        self.assertEqual(len(seen), 3, "three cases, three sentences")

    def test_the_new_sentence_is_said_again_in_russian(self) -> None:
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        self._sentence(self.MF_AND_CPU, "hevc444")
        i18n.set_language(i18n.LANG_RU)
        text = self.panel.encoding_note.text()
        self.assertIn("не умеют записывать этот формат", text)
        self.assertIn("H.264", text, "a codec is not translated")

    def test_hardware_is_counted_from_the_probe_and_not_from_a_vendor_name(self) -> None:
        self.panel.set_encoders(_machine(*self.MF_AND_CPU))
        self.assertTrue(self.panel._hardware_works())
        self.panel.set_encoders(_machine("libx264"))
        self.assertFalse(self.panel._hardware_works())
        self.panel.set_encoders(_machine())
        self.assertFalse(self.panel._hardware_works())


class TestTheEstimateAsksWhatThisMachineMeasured(PanelTestCase):
    """The per-minute line is this machine's own figure once it has one.

    `config.PROFILES` was measured on this machine's NVENC; `docs/ENCODERS.md` §3 puts
    a Media Foundation recording at up to 4.9x it. Showing that table to an AMD or
    Intel user is showing them somebody else's number as if it were theirs.
    """

    def _patched(self, estimate):
        seen: dict = {}

        def fake(profile_key, level, width, height, framerate, encoder="", store=None):
            seen.update(profile_key=profile_key, level=level, width=width,
                        height=height, framerate=framerate, encoder=encoder)
            return estimate
        return seen, fake

    def test_the_figure_comes_from_storage_and_not_from_the_table(self) -> None:
        seen, fake = self._patched(storage_mod.Estimate(123.0, False))
        with mock.patch.object(settings_panel.storage_mod, "size_estimate", fake):
            self.panel.set_capture_size(2560, 1440)
        self.assertIn("123", self.panel.estimate_label.text())
        self.assertEqual(seen["profile_key"], self.settings.profile)
        self.assertEqual((seen["width"], seen["height"]), (2560, 1440))

    def test_it_is_asked_with_the_encoder_this_machine_would_use(self) -> None:
        seen, fake = self._patched(storage_mod.Estimate(10.0, False))
        with mock.patch.object(settings_panel.storage_mod, "size_estimate", fake):
            self.panel.set_encoders(_machine("libx264"))
        self.assertEqual(seen["encoder"], "libx264")

    def test_nothing_is_looked_up_before_the_probe_has_answered(self) -> None:
        """`choose` answers `libx264` for an empty machine, and looking a measurement
        up under that name would be reading another encoder's samples."""
        seen, fake = self._patched(storage_mod.Estimate(10.0, False))
        with mock.patch.object(settings_panel.storage_mod, "size_estimate", fake):
            self.panel.set_capture_size(3840, 2160)
        self.assertEqual(seen["encoder"], "")

    def test_a_measured_figure_says_that_it_is_measured(self) -> None:
        measured = storage_mod.Estimate(
            mb_per_minute=31.0, measured=True, encoder="hevc_nvenc", samples=3,
            at=1762300000.0,
        )
        _seen, fake = self._patched(measured)
        with mock.patch.object(settings_panel.storage_mod, "size_estimate", fake):
            self.panel.set_encoders(_machine(*THIS_MACHINE))
        text = self.panel.estimate_label.text()
        self.assertIn("31", text)
        self.assertIn("measured on this machine", text)
        self.assertIn("hevc_nvenc", text)
        self.assertNotIn("Estimated", text)

    def test_the_table_still_answers_until_then(self) -> None:
        """No store, no samples: the sentence is the one that says "estimated".

        The estimate is read through a stand-in rather than from the real store. Without
        that this test asks the machine it is running on whether anybody has ever
        recorded anything - and it failed the first time somebody did, which is not a
        thing a unit test may depend on.
        """
        nothing = storage_mod.Estimate(
            mb_per_minute=98.0, measured=False, encoder="hevc_nvenc", samples=0,
        )
        _seen, fake = self._patched(nothing)
        with mock.patch.object(settings_panel.storage_mod, "size_estimate", fake):
            self.panel.set_encoders(_machine(*THIS_MACHINE))
        text = self.panel.estimate_label.text()
        self.assertIn("Estimated", text)
        self.assertIn("on a busy screen", text)

    def test_the_measured_sentence_is_said_again_in_russian(self) -> None:
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        measured = storage_mod.Estimate(
            mb_per_minute=31.0, measured=True, encoder="hevc_nvenc", samples=3,
            at=1762300000.0,
        )
        _seen, fake = self._patched(measured)
        with mock.patch.object(settings_panel.storage_mod, "size_estimate", fake):
            self.panel.set_encoders(_machine(*THIS_MACHINE))
            i18n.set_language(i18n.LANG_RU)
        text = self.panel.estimate_label.text()
        self.assertIn("измерено на этом компьютере", text)
        self.assertIn("hevc_nvenc", text, "an ffmpeg name is not translated")


class TestTheProbeTable(PanelTestCase):
    """The "Details" expander: the only place the vendor is named, and the only place
    the probe's own answers are visible at all."""

    def setUp(self) -> None:
        super().setUp()
        self.panel.set_encoders(_machine(*THIS_MACHINE))

    def test_it_is_folded_away_until_it_is_asked_for(self) -> None:
        self.assertTrue(self.panel.details_button.isVisibleTo(self.panel))
        self.assertFalse(self.panel.details_box.isVisibleTo(self.panel))
        self.assertIn("Details", self.panel.details_button.text())

    def test_opening_it_shows_every_candidate_with_its_vendor(self) -> None:
        self.panel.details_button.setChecked(True)
        self.assertTrue(self.panel.details_box.isVisibleTo(self.panel))
        lines = self._rows()
        self.assertEqual(len(lines), len(capabilities.CANDIDATES))
        joined = "\n".join(lines)
        for name, _codec, vendor in capabilities.CANDIDATES:
            with self.subTest(name=name):
                self.assertIn(name, joined)
                self.assertIn(vendor, joined)

    def test_a_refusal_carries_ffmpeg_s_own_reason(self) -> None:
        self.panel.details_button.setChecked(True)
        failed = [line for line in self._rows() if "did not work" in line]
        self.assertTrue(failed)
        for line in failed:
            self.assertIn("Nothing was written into output file", line)

    def test_a_row_with_no_reason_still_says_something(self) -> None:
        self.panel.set_encoders([_result("hevc_amf", False, "")])
        self.panel.details_button.setChecked(True)
        self.assertIn("no reason given", self._rows()[0])

    def test_the_table_scrolls_rather_than_growing_past_the_screen(self) -> None:
        """Twelve candidates laid out inline took the panel to 1505 px on a 1440 px
        screen - re-measured 2026-11-05, and `popup_near` can only keep the top of it
        visible. The 1746 px written here on 2026-11-04 did not reproduce; see
        `settings_panel.DETAILS_HEIGHT` for all four heights."""
        self.panel.details_button.setChecked(True)
        self.panel.show()
        self.qt_app.processEvents()
        try:
            self.assertLessEqual(
                self.panel.details_box.height(), settings_panel.DETAILS_HEIGHT
            )
            self.assertGreater(
                self.panel.details_rows.sizeHint().height(),
                self.panel.details_box.height(),
                "nothing is being scrolled; the cap proves nothing",
            )
        finally:
            self.panel.hide()

    def test_it_closes_again(self) -> None:
        self.panel.details_button.setChecked(True)
        self.panel.details_button.setChecked(False)
        self.assertFalse(self.panel.details_box.isVisibleTo(self.panel))

    def test_it_is_said_again_in_russian(self) -> None:
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        self.panel.details_button.setChecked(True)
        i18n.set_language(i18n.LANG_RU)
        self.assertIn("Подробнее", self.panel.details_button.text())
        joined = "\n".join(self._rows())
        self.assertIn("работает", joined)
        # ffmpeg's own sentence is evidence, not prose, and stays as it was said.
        self.assertIn("Nothing was written into output file", joined)

    def test_opening_it_does_not_change_a_setting(self) -> None:
        self.panel.details_button.setChecked(True)
        self.assertEqual(self.fired, 0)

    def _rows(self) -> list[str]:
        layout = self.panel.details_rows.layout()
        rows: list[str] = []
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if isinstance(widget, QtWidgets.QLabel):
                rows.append(widget.text())
        return rows


class TestAFormatThisMachineCannotEncode(PanelTestCase):
    """4:4:4 is offered only while something here can really encode it.

    `encoders.frame_path` rewrites `yuv444p` to `nv12` for the Media Foundation family,
    so the alternative to refusing is a command that runs, a file that plays, and three
    quarters of the colour samples the format was chosen for quietly gone.
    """

    #: A machine where Media Foundation is the only thing that works - which is the
    #: shape of the answer `docs/ENCODERS.md` expects from an AMD or Intel machine.
    #: Invented: this one has an RTX 3070 Ti and no AMD or Intel graphics.
    NO_444 = ("hevc_mf", "h264_mf")

    def _index(self, key: str) -> int:
        return self.panel.profile_combo.findData(key)

    def _item(self, key: str):
        return self.panel.profile_combo.model().item(self._index(key))

    def test_every_format_is_offered_before_the_probe_has_spoken(self) -> None:
        for key in config_mod.PROFILES:
            with self.subTest(key=key):
                self.assertTrue(self._item(key).isEnabled())

    def test_it_stays_offered_where_something_can_encode_it(self) -> None:
        self.panel.set_encoders(_machine(*THIS_MACHINE))
        self.assertTrue(self._item("hevc444").isEnabled())
        self.assertEqual(self.panel.profile_problem.text(), "")

    def test_libx264_alone_is_enough_to_keep_it(self) -> None:
        """It is not an NVENC-only format: `encoders.YUV444_ENCODERS` was measured."""
        self.panel.set_encoders(_machine("libx264"))
        self.assertTrue(self._item("hevc444").isEnabled())

    def test_it_is_greyed_out_with_a_reason_when_nothing_can(self) -> None:
        self.panel.set_encoders(_machine(*self.NO_444))
        item = self._item("hevc444")
        self.assertFalse(item.isEnabled())
        tip = self.panel.profile_combo.itemData(
            self._index("hevc444"), QtCore.Qt.ItemDataRole.ToolTipRole
        )
        self.assertIn("4:4:4", tip)
        # And the formats that machine can encode are untouched.
        for key in ("hevc420", "h264", "x264"):
            with self.subTest(key=key):
                self.assertTrue(self._item(key).isEnabled())

    def test_a_settings_file_that_already_names_it_is_told_why(self) -> None:
        """Written on a machine with NVENC, opened on one without: the row is greyed
        out and still selected, so the reason has to be on the panel."""
        self.settings.profile = "hevc444"
        self.panel.refresh_from_settings()
        self.panel.set_encoders(_machine(*self.NO_444))
        self.assertEqual(self.panel.profile_combo.currentData(), "hevc444")
        self.assertTrue(self.panel.profile_problem.isVisibleTo(self.panel))
        self.assertIn("4:4:4", self.panel.profile_problem.text())
        # No path is described, because there is no recording to describe.
        self.assertFalse(self.panel.encoding_note.isVisibleTo(self.panel))

    def test_nothing_is_greyed_out_on_a_machine_where_nothing_works(self) -> None:
        """A machine in trouble is told so on the bar. Taking its formats away as well
        would only hide the one thing it might still manage."""
        self.panel.set_encoders(_machine())
        for key in config_mod.PROFILES:
            with self.subTest(key=key):
                self.assertTrue(self._item(key).isEnabled())

    def test_the_reason_is_said_again_in_russian(self) -> None:
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        self.settings.profile = "hevc444"
        self.panel.refresh_from_settings()
        self.panel.set_encoders(_machine(*self.NO_444))
        i18n.set_language(i18n.LANG_RU)
        self.assertIn("4:4:4", self.panel.profile_problem.text())
        self.assertIn("нельзя", self.panel.profile_problem.text())


if __name__ == "__main__":
    unittest.main()
