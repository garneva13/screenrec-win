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


class FakeCamera:
    """What `webcam.cameras()` returns, as far as the panel is concerned."""

    def __init__(self, description: str) -> None:
        self._description = description

    def description(self) -> str:
        return self._description


CAMERAS = (FakeCamera("HD Pro Webcam C922"), FakeCamera("Integrated Camera"))


class PanelTestCase(unittest.TestCase):
    """Shared fixture: a themed application and a panel over a real `Settings`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = _app()
        theme.apply(cls.qt_app)

    def setUp(self) -> None:
        self._fake_cameras()
        self.settings = config_mod.Settings()
        self.panel = settings_panel.SettingsPanel(self.settings)
        self.panel.refresh_devices(MICS, LOOPBACKS)
        self.fired = 0
        self.panel.changed.connect(self._count)

    def tearDown(self) -> None:
        self.panel.deleteLater()
        self.qt_app.processEvents()
        # `processEvents` does **not** run deferred deletions - Qt only delivers
        # DeferredDelete when an event loop level exits, and there is no loop here. So
        # without this every window ever built in this file stays alive, listening to
        # `i18n.notifier`, and each language switch retranslates all of them: measured
        # on 2026-11-08, the module went from 46 s to over 900 s as the count grew.
        self.qt_app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def _fake_cameras(self) -> None:
        """Two cameras that are not on this machine, for the length of one test.

        Enumerating the real ones opens the device: measured at up to 40 s for one
        test on 2026-11-08, and the list differs from machine to machine, so a test
        that asks is both slow and not a test.
        """
        from screenrec import webcam as webcam_mod

        patches = (
            mock.patch.object(webcam_mod, "cameras", lambda: list(CAMERAS)),
            mock.patch.object(webcam_mod, "camera_named", lambda _name: None),
            mock.patch.object(webcam_mod, "resolutions_of", lambda _device: []),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _count(self) -> None:
        self.fired += 1

    def _choose(self, combo, value) -> None:
        """Pick a value from a list by what it means, not by where it happens to sit."""
        index = combo.findData(value)
        self.assertGreaterEqual(index, 0, f"{value!r} is not on the list")
        combo.setCurrentIndex(index)

    def _estimate(self) -> str:
        """Both lines of the estimate box, which is one sentence in two weights."""
        return f"{self.panel.estimate_label.text()} {self.panel.estimate_note.text()}"


class TheCameraRows(PanelTestCase):
    """The round trip for the camera section, and the one rule that is not obvious:
    every camera control is dead while the switch is off."""

    def test_they_come_up_showing_the_settings(self) -> None:
        self.assertEqual(self.panel.webcam_switch.isChecked(),
                         self.settings.webcam_enabled)
        self.assertEqual(self.panel.webcam_corner_combo.currentData(),
                         self.settings.webcam_corner)
        self.assertEqual(self.panel.webcam_size_combo.currentData(),
                         self.settings.webcam_size)
        self.assertEqual(self.panel.webcam_shape_combo.currentData(),
                         self.settings.webcam_shape)
        self.assertEqual(self.panel.webcam_res_combo.currentData(),
                         self.settings.webcam_resolution)
        self.assertEqual(self.fired, 0)

    def test_moving_one_writes_it_back_and_says_so(self) -> None:
        for combo, field, value in (
            (self.panel.webcam_corner_combo, "webcam_corner", "top_left"),
            (self.panel.webcam_size_combo, "webcam_size", "large"),
            (self.panel.webcam_shape_combo, "webcam_shape", "circle"),
        ):
            with self.subTest(field=field):
                before = self.fired
                combo.setCurrentIndex(combo.findData(value))
                self.assertEqual(getattr(self.settings, field), value)
                self.assertGreater(self.fired, before)

    def test_the_size_setting_takes_a_size_not_a_resolution(self) -> None:
        """`max_height` scales the recording; `webcam_size` is the little window.
        They sit three rows apart and have been confused before."""
        self.panel.webcam_size_combo.setCurrentIndex(
            self.panel.webcam_size_combo.findData("large")
        )
        self.assertEqual(self.settings.webcam_size, "large")
        self.assertEqual(self.settings.max_height, 0)

    def test_automatic_is_the_first_resolution_and_is_an_empty_setting(self) -> None:
        self.assertEqual(self.panel.webcam_res_combo.itemData(0), "")

    def test_the_whole_section_is_dead_while_the_switch_is_off(self) -> None:
        self.panel.webcam_switch.setChecked(False)
        self.qt_app.processEvents()
        for control in (self.panel.webcam_combo, self.panel.webcam_corner_combo,
                        self.panel.webcam_size_combo, self.panel.webcam_shape_combo,
                        self.panel.webcam_res_combo):
            self.assertFalse(control.isEnabled())
        self.panel.webcam_switch.setChecked(True)
        self.qt_app.processEvents()
        for control in (self.panel.webcam_combo, self.panel.webcam_corner_combo,
                        self.panel.webcam_size_combo, self.panel.webcam_shape_combo,
                        self.panel.webcam_res_combo):
            self.assertTrue(control.isEnabled())

    def test_a_saved_shape_the_build_does_not_know_falls_back(self) -> None:
        settings = config_mod.Settings(webcam_shape="trapezoid")
        panel = settings_panel.SettingsPanel(settings)
        self.assertEqual(panel.webcam_shape_combo.currentData(),
                         config_mod.DEFAULT_WEBCAM_SHAPE)
        panel.deleteLater()

class TestBuildsAndShowsTheSettings(PanelTestCase):
    def test_controls_show_the_settings_values(self) -> None:
        settings = self.settings
        self.assertEqual(self.panel.fps_combo.currentData(), settings.framerate)
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
        self.assertEqual(self.panel.countdown_combo.currentData(), settings.countdown)
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
        self.assertEqual(panel.fps_combo.currentData(), 30)
        self.assertEqual(panel.profile_combo.currentData(), "hevc444")
        self.assertEqual(panel.quality_combo.currentData(), "best")
        self.assertEqual(panel.mic_combo.currentText(), "Line In (Realtek)")
        self.assertEqual(panel.system_combo.currentData(), 9)
        self.assertEqual(panel.countdown_combo.currentData(), 3)
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
        self._choose(self.panel.fps_combo, 30)
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
        self._choose(self.panel.countdown_combo, 5)
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
            self._choose(self.panel.fps_combo, 24)
            self.panel.cursor_switch.click()
            self.panel.hotkey_edit.arm()
            QTest.keyClick(
                self.panel.hotkey_edit, QtCore.Qt.Key.Key_F9,
                QtCore.Qt.KeyboardModifier.ControlModifier
                | QtCore.Qt.KeyboardModifier.ShiftModifier,
            )
        finally:
            config_mod.save_settings = original  # type: ignore[assignment]
        self.assertEqual(calls, [])
        self.assertEqual(self.settings.hotkey, "Ctrl+Shift+F9")


class TestHotkey(PanelTestCase):
    """The boxes read a key press. Several things have to be true of that.

    Each one is a way the old typed-in box went wrong: a shortcut that fires the thing
    you are editing, a half-finished combination stored as if it were whole, two
    commands quietly sharing one combination, and no way out of the box except to type
    something valid.
    """

    def _press(self, field, key, modifiers) -> None:
        field.arm()
        QTest.keyClick(field, key, modifiers)

    def test_a_press_is_read_and_stored_in_the_canonical_spelling(self) -> None:
        self._press(
            self.panel.hotkey_edit, QtCore.Qt.Key.Key_F9,
            QtCore.Qt.KeyboardModifier.ControlModifier
            | QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertEqual(self.settings.hotkey, "Ctrl+Shift+F9")
        self.assertEqual(self.panel.hotkey_edit.text(), "Ctrl+Shift+F9")
        self.assertEqual(self.fired, 1)
        self.assertFalse(self.panel.hotkey_problem.isVisibleTo(self.panel))

    def test_the_global_hotkeys_are_stood_down_while_it_waits(self) -> None:
        """Without this the box could not read the shortcut it is editing: Windows
        takes a registered combination and no window ever sees it."""
        heard: list[bool] = []
        self.panel.hotkey_capture.connect(heard.append)
        self._press(
            self.panel.hotkey_edit, QtCore.Qt.Key.Key_K,
            QtCore.Qt.KeyboardModifier.ControlModifier
            | QtCore.Qt.KeyboardModifier.AltModifier,
        )
        self.assertEqual(heard, [True, False])

    def test_escape_leaves_the_shortcut_as_it_was(self) -> None:
        before = self.settings.hotkey
        field = self.panel.hotkey_edit
        field.arm()
        self.assertTrue(field.is_armed())
        QTest.keyClick(field, QtCore.Qt.Key.Key_Escape)
        self.assertFalse(field.is_armed())
        self.assertEqual(field.text(), before)
        self.assertEqual(self.settings.hotkey, before)
        self.assertEqual(self.fired, 0)

    def test_a_modifier_on_its_own_is_not_a_shortcut(self) -> None:
        before = self.settings.hotkey
        field = self.panel.hotkey_edit
        field.arm()
        QTest.keyClick(field, QtCore.Qt.Key.Key_Control)
        self.assertTrue(field.is_armed(), "still waiting for the key it belongs to")
        self.assertEqual(self.settings.hotkey, before)

    def test_a_bare_key_is_not_a_shortcut_either(self) -> None:
        """One that fires while somebody is typing is worse than none at all."""
        before = self.settings.hotkey
        field = self.panel.hotkey_edit
        field.arm()
        QTest.keyClick(field, QtCore.Qt.Key.Key_F9)
        self.assertTrue(field.is_armed())
        self.assertEqual(self.settings.hotkey, before)
        self.assertEqual(self.fired, 0)

    def test_a_combination_another_command_has_is_refused_in_words(self) -> None:
        self.settings.hotkey_pause = "Ctrl+Alt+K"
        self.panel.refresh_from_settings()
        self.fired = 0
        before = self.settings.hotkey
        self._press(
            self.panel.hotkey_edit, QtCore.Qt.Key.Key_K,
            QtCore.Qt.KeyboardModifier.ControlModifier
            | QtCore.Qt.KeyboardModifier.AltModifier,
        )
        self.assertEqual(self.settings.hotkey, before)
        self.assertEqual(self.panel.hotkey_edit.text(), before)
        self.panel.show_page("control")
        self.assertTrue(self.panel.hotkey_problem.isVisibleTo(self.panel))
        message = self.panel.hotkey_problem.text()
        self.assertIn("Ctrl+Alt+K", message)
        self.assertIn("pause", message)
        self.assertEqual(self.fired, 0)

    def test_the_other_two_boxes_write_their_own_settings(self) -> None:
        for field, attribute, key in (
            (self.panel.hotkey_pause_edit, "hotkey_pause", QtCore.Qt.Key.Key_F7),
            (self.panel.hotkey_draw_edit, "hotkey_annotate", QtCore.Qt.Key.Key_F8),
        ):
            with self.subTest(attribute=attribute):
                self._press(
                    field, key, QtCore.Qt.KeyboardModifier.ControlModifier
                    | QtCore.Qt.KeyboardModifier.AltModifier
                )
                expected = "Ctrl+Alt+" + QtGui.QKeySequence(key).toString()
                self.assertEqual(getattr(self.settings, attribute), expected)

    def test_hiding_the_window_disarms_a_box(self) -> None:
        """A box left waiting would hold the global hotkeys down for ever."""
        heard: list[bool] = []
        self.panel.hotkey_capture.connect(heard.append)
        self.panel.show()
        self.panel.hotkey_edit.arm()
        self.panel.hide()
        self.assertFalse(self.panel.hotkey_edit.is_armed())
        self.assertEqual(heard, [True, False])


class TestEstimate(PanelTestCase):
    def test_it_starts_at_the_default_capture_size_and_says_it_is_an_estimate(
        self,
    ) -> None:
        text = self._estimate()
        self.assertEqual(self.panel.capture_size(), settings_panel.DEFAULT_CAPTURE)
        self.assertIn("on a busy screen", text)
        self.assertIn("1920 × 1080", text)
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

        self._choose(self.panel.fps_combo, 30)
        after_fps = self.panel.estimate_label.text()
        self.assertNotEqual(after_quality, after_fps)

        self.panel.set_capture_size(2560, 1440)
        after_size = self.panel.estimate_label.text()
        self.assertNotEqual(after_fps, after_size)
        self.assertIn("2560 × 1440", after_size)

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
            # It really wraps at the width it has rather than being elided: the height
            # it needs at that width is the height it was given, and it is more than
            # one line.
            needed = note.heightForWidth(note.width())
            self.assertGreaterEqual(note.height(), needed)
            self.assertGreater(needed, note.fontMetrics().height(),
                               "a one-line answer means it did not wrap")
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
            "title": self._caption("Settings"),
            "quality": self._caption("Quality"),
            "format": self.panel.profile_combo.currentText(),
            "off": self.panel.mic_combo.itemText(0),
            "tracks": self.panel.tracks_note.text(),
            "estimate": self._estimate(),
        }
        for value in before.values():
            self.assertTrue(value, "nothing to compare against")

        self.panel.language_combo.setCurrentIndex(
            self.panel.language_combo.findData(i18n.LANG_RU)
        )

        self.assertEqual(self._caption("Settings"), "Настройки")
        self.assertEqual(self._caption("Quality"), "Качество")
        self.assertEqual(self.panel.mic_combo.itemText(0), "Выкл.")
        self.assertIn("МБ в минуту", self._estimate())
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
        self.settings.hotkey_pause = "Ctrl+Alt+K"
        self.panel.refresh_from_settings()
        self.panel.hotkey_edit.arm()
        QTest.keyClick(
            self.panel.hotkey_edit, QtCore.Qt.Key.Key_K,
            QtCore.Qt.KeyboardModifier.ControlModifier
            | QtCore.Qt.KeyboardModifier.AltModifier,
        )
        self.assertTrue(self.panel.hotkey_problem.text())
        i18n.set_language(i18n.LANG_RU)
        said = self.panel.hotkey_problem.text()
        self.panel.show_page("control")
        self.assertTrue(self.panel.hotkey_problem.isVisibleTo(self.panel))
        self.assertIn("уже занято", said)
        self.assertIn("Ctrl+Alt+K", said)

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
        self.assertEqual(seen["framerate"], self.settings.framerate)

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
        text = self._estimate()
        self.assertIn("31", text)
        self.assertIn("Measured on this machine", text)
        self.assertIn("hevc_nvenc", text)
        self.assertNotIn("busy screen", text)

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
        text = self._estimate()
        self.assertIn("98", text)
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
        text = self._estimate()
        self.assertIn("Измерено на этой машине", text)
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


class TheSidebar(PanelTestCase):
    """Five pages, one of them showing, and the keyboard able to reach them."""

    def test_it_opens_on_recording(self) -> None:
        self.assertEqual(self.panel.current_page(), "recording")
        self.assertTrue(self.panel._nav[0][0].isChecked())

    def test_every_page_in_PAGES_can_be_shown(self) -> None:
        for key, _icon, _word in settings_panel.PAGES:
            with self.subTest(page=key):
                self.panel.show_page(key)
                self.assertEqual(self.panel.current_page(), key)

    def test_pressing_a_button_shows_its_page(self) -> None:
        for index, (button, _word) in enumerate(self.panel._nav):
            with self.subTest(index=index):
                button.click()
                self.assertEqual(
                    self.panel.current_page(), settings_panel.PAGES[index][0]
                )

    def test_showing_a_page_checks_its_button_and_only_its_button(self) -> None:
        self.panel.show_page("camera")
        checked = [b.isChecked() for b, _w in self.panel._nav]
        self.assertEqual(checked, [False, False, True, False, False])

    def test_an_unknown_page_changes_nothing(self) -> None:
        self.panel.show_page("sound")
        self.panel.show_page("nonsense")
        self.assertEqual(self.panel.current_page(), "sound")

    def test_only_the_page_scrolls(self) -> None:
        """The sidebar and the footer stay put; a long page is the one that moves."""
        for index in range(self.panel.stack.count()):
            widget = self.panel.stack.widget(index)
            self.assertIsInstance(widget, QtWidgets.QScrollArea)
            self.assertTrue(widget.widgetResizable())

    def test_every_setting_from_the_old_column_is_still_reachable(self) -> None:
        """The redesign moved controls; it was not allowed to lose any."""
        controls = (
            "preset_combo", "fps_combo", "size_combo", "profile_combo",
            "quality_combo", "folder_edit", "browse_button",
            "mic_combo", "system_combo", "tracks_combo",
            "webcam_switch", "webcam_combo", "webcam_corner_combo",
            "webcam_size_combo", "webcam_shape_combo", "webcam_res_combo",
            "cursor_switch", "clicks_switch", "frame_switch", "bar_switch",
            "countdown_combo", "autostop_combo", "after_combo",
            "hotkey_edit", "hotkey_pause_edit", "hotkey_draw_edit",
            "language_combo",
        )
        for name in controls:
            with self.subTest(control=name):
                widget = getattr(self.panel, name, None)
                self.assertIsNotNone(widget, f"{name} is gone")
                # And it is on one of the pages, not left parentless in the air.
                self.assertIsNotNone(widget.parent())


class TheCameraPage(PanelTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.panel.show_page("camera")

    def test_the_dependent_settings_are_hidden_while_the_camera_is_off(self) -> None:
        self.settings.webcam_enabled = False
        self.panel.refresh_from_settings()
        self.assertFalse(self.panel.webcam_body.isVisibleTo(self.panel))
        self.settings.webcam_enabled = True
        self.panel.refresh_from_settings()
        self.assertTrue(self.panel.webcam_body.isVisibleTo(self.panel))

    def test_their_values_survive_being_hidden(self) -> None:
        self.settings.webcam_enabled = True
        self.settings.webcam_corner = "top_left"
        self.settings.webcam_shape = "circle"
        self.panel.refresh_from_settings()
        self.panel.webcam_switch.click()  # off
        self.panel.webcam_switch.click()  # on again
        self.assertEqual(self.settings.webcam_corner, "top_left")
        self.assertEqual(self.settings.webcam_shape, "circle")
        self.assertEqual(self.panel.webcam_corner_combo.currentData(), "top_left")

    def test_the_diagram_follows_the_lists(self) -> None:
        self.settings.webcam_enabled = True
        self.panel.refresh_from_settings()
        self._choose(self.panel.webcam_corner_combo, "top_right")
        self._choose(self.panel.webcam_shape_combo, "circle")
        self._choose(self.panel.webcam_size_combo, "large")
        self.assertEqual(
            self.panel.camera_view.placement(), ("top_right", "large", "circle")
        )

    def test_clicking_the_diagram_writes_the_setting(self) -> None:
        self.settings.webcam_enabled = True
        self.panel.refresh_from_settings()
        self.fired = 0
        self.panel.camera_view.cornerPicked.emit("top_left")
        self.assertEqual(self.settings.webcam_corner, "top_left")
        self.assertEqual(self.panel.webcam_corner_combo.currentData(), "top_left")
        self.assertEqual(self.fired, 1)


class WhileATakeIsRunning(PanelTestCase):
    """Everything about the picture and the sound was read when the take started.

    Leaving those controls live would let somebody change the frame rate mid-take and
    wonder why nothing happened, so they are disabled and the footer says why.
    """

    def test_the_footer_says_something_different(self) -> None:
        before = self.panel.status_label.text()
        self.panel.set_recording(True)
        after = self.panel.status_label.text()
        self.assertNotEqual(before, after)
        self.assertIn("next one", after)
        self.panel.set_recording(False)
        self.assertEqual(self.panel.status_label.text(), before)

    def test_the_picture_and_sound_controls_are_locked(self) -> None:
        self.panel.set_recording(True)
        for name in ("fps_combo", "size_combo", "profile_combo", "quality_combo",
                     "preset_combo", "webcam_switch", "mic_switch", "system_switch",
                     "countdown_combo"):
            with self.subTest(control=name):
                self.assertFalse(getattr(self.panel, name).isEnabled())

    def test_what_really_does_apply_at_once_stays_live(self) -> None:
        """The language, the shortcuts, the bar and the folder are not read at the
        start of a take, so locking them would be a lie about why."""
        self.panel.set_recording(True)
        for name in ("language_combo", "hotkey_edit", "hotkey_pause_edit",
                     "hotkey_draw_edit", "bar_switch", "browse_button",
                     "folder_edit", "after_combo", "autostop_combo"):
            with self.subTest(control=name):
                self.assertTrue(getattr(self.panel, name).isEnabled())

    def test_it_all_comes_back(self) -> None:
        self.panel.set_recording(True)
        self.panel.set_recording(False)
        self.assertTrue(self.panel.fps_combo.isEnabled())
        self.assertTrue(self.panel.profile_combo.isEnabled())

    def test_saying_it_twice_changes_nothing(self) -> None:
        self.panel.set_recording(True)
        self.assertTrue(self.panel.recording())
        self.panel.set_recording(True)
        self.assertTrue(self.panel.recording())


class EverythingOnItCanBeSaidInRussian(PanelTestCase):
    """`check_translations.py` cannot see these.

    Every caption on this window reaches `_()` through a variable - `_(title)`,
    `_(word)`, `_(info)` - which the static scan reports as "not checkable". So the
    window is asked directly instead: it knows every English source it registered.
    """

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(i18n.set_language, i18n.LANG_EN)

    def test_no_caption_falls_back_to_english(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        missing = [
            source for _widget, source, _upper in self.panel._captions
            if source.strip() and i18n._(source) == source
        ]
        self.assertEqual(missing, [])

    def test_no_sidebar_word_falls_back(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        missing = [word for _b, word in self.panel._nav if i18n._(word) == word]
        self.assertEqual(missing, [])

    def test_no_explanation_falls_back(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        missing = [
            source for _widget, source in self.panel._tips
            if i18n._(source) == source
        ]
        self.assertEqual(missing, [])

    def test_the_sidebar_really_changes_language(self) -> None:
        english = [button.text() for button, _w in self.panel._nav]
        i18n.set_language(i18n.LANG_RU)
        russian = [button.text() for button, _w in self.panel._nav]
        self.assertNotEqual(english, russian)
        self.assertEqual(russian[0], "Запись")


class TheFolderField(PanelTestCase):
    """A folder that cannot be written to is not a setting - it is a recording that
    will fail once ffmpeg has already started."""

    def test_a_folder_that_works_has_nothing_to_say(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.panel._folder_problem(folder), "")

    def test_one_that_is_not_there_says_why(self) -> None:
        problem = self.panel._folder_problem(str(Path("B:/") / "no-such-folder-here"))
        self.assertTrue(problem)
        self.assertIn("cannot be written", problem)

    def test_the_probe_file_does_not_stay_behind(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            self.panel._folder_problem(folder)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_a_refused_folder_leaves_the_setting_alone(self) -> None:
        before = self.settings.output_dir
        with mock.patch.object(
            QtWidgets.QFileDialog, "getExistingDirectory",
            staticmethod(lambda *a, **k: "B:/no-such-folder-here"),
        ):
            self.panel._browse()
        self.assertEqual(self.settings.output_dir, before)
        self.assertTrue(self.panel.folder_problem.isVisibleTo(self.panel))
        self.assertEqual(self.fired, 0)

    def test_a_good_one_is_taken_and_announced(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(
                QtWidgets.QFileDialog, "getExistingDirectory",
                staticmethod(lambda *a, **k: folder),
            ):
                self.panel._browse()
            self.assertEqual(self.settings.output_dir, folder)
            self.assertEqual(self.panel.folder_edit.text(), folder)
            self.assertFalse(self.panel.folder_problem.isVisibleTo(self.panel))
            self.assertEqual(self.fired, 1)


class TheLinksToTheProject(PanelTestCase):
    """Two words on the General page. The only ways they can be wrong are the address
    behind them, the keyboard not reaching them, and silence when nothing opens."""

    def setUp(self) -> None:
        super().setUp()
        self.panel.show_page("general")

    def test_they_point_at_the_project(self) -> None:
        self.assertEqual(self.panel.page_button.toolTip(), settings_panel.PROJECT_PAGE)
        self.assertEqual(
            self.panel.source_button.toolTip(), settings_panel.PROJECT_SOURCE
        )
        self.assertTrue(settings_panel.PROJECT_PAGE.startswith("https://"))
        self.assertTrue(settings_panel.PROJECT_SOURCE.startswith("https://"))

    def test_pressing_one_hands_the_address_to_windows(self) -> None:
        opened: list[str] = []
        with mock.patch.object(
            QtGui.QDesktopServices, "openUrl",
            staticmethod(lambda url: opened.append(url.toString()) or True),
        ):
            self.panel.page_button.click()
            self.panel.source_button.click()
        self.assertEqual(
            opened, [settings_panel.PROJECT_PAGE, settings_panel.PROJECT_SOURCE]
        )
        self.assertFalse(self.panel.link_problem.isVisibleTo(self.panel))

    def test_a_link_that_does_not_open_says_so(self) -> None:
        """Silence after a click is the one outcome a link may not have."""
        with mock.patch.object(
            QtGui.QDesktopServices, "openUrl", staticmethod(lambda _url: False)
        ):
            self.panel.page_button.click()
        self.assertTrue(self.panel.link_problem.isVisibleTo(self.panel))
        self.assertIn(settings_panel.PROJECT_PAGE, self.panel.link_problem.text())

    def test_opening_one_changes_no_setting(self) -> None:
        with mock.patch.object(
            QtGui.QDesktopServices, "openUrl", staticmethod(lambda _url: True)
        ):
            self.panel.page_button.click()
        self.assertEqual(self.fired, 0)

    def test_the_keyboard_reaches_them(self) -> None:
        for button in (self.panel.page_button, self.panel.source_button):
            with self.subTest(button=button.text()):
                self.assertNotEqual(
                    button.focusPolicy(), QtCore.Qt.FocusPolicy.NoFocus
                )

    def test_the_words_follow_the_language(self) -> None:
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(self.panel.page_button.text(), "Страница программы")
        self.assertEqual(self.panel.source_button.text(), "Исходный код")
        # The address itself is not translated.
        self.assertEqual(self.panel.page_button.toolTip(), settings_panel.PROJECT_PAGE)

    def test_the_version_is_the_one_the_package_reports(self) -> None:
        from screenrec import __version__

        self.assertIn(__version__, self.panel.version_label.text())


class TheDefaultPlaybackDeviceIsNamed(PanelTestCase):
    """"Default playback device" is a promise the list cannot keep on its own.

    This machine has two endpoints whose names differ by one word, and a call playing
    to the wrong one records as silence with nothing on screen to say so. Naming the
    one it resolves to turns the promise into something a person can check.
    """

    DEVICES = (
        audio_mod.LoopbackDevice(index=24, name="Headphones - wire (Temple) [Loopback]",
                                 channels=2, rate=48000),
        audio_mod.LoopbackDevice(index=25, name="Headphones - wifi (Temple) [Loopback]",
                                 channels=2, rate=48000),
    )

    def _with_default(self, index: int):
        return mock.patch.object(
            audio_mod, "default_loopback_index", staticmethod(lambda: index)
        )

    def test_the_row_says_which_device_it_means(self) -> None:
        with self._with_default(25):
            self.panel.refresh_devices([], self.DEVICES)
        text = self.panel.system_combo.itemText(1)
        self.assertEqual(self.panel.system_combo.itemData(1), -1)
        self.assertIn("Headphones - wifi (Temple)", text)

    def test_the_loopback_suffix_is_not_shown(self) -> None:
        with self._with_default(24):
            self.panel.refresh_devices([], self.DEVICES)
        for index in range(self.panel.system_combo.count()):
            with self.subTest(row=index):
                self.assertNotIn("[Loopback]", self.panel.system_combo.itemText(index))

    def test_a_default_that_is_not_in_the_list_still_reads(self) -> None:
        """An endpoint can disappear between the two calls; the row must not go blank."""
        with self._with_default(99):
            self.panel.refresh_devices([], self.DEVICES)
        self.assertTrue(self.panel.system_combo.itemText(1).strip())
        self.assertEqual(self.panel.system_combo.itemData(1), -1)

    def test_an_audio_stack_that_raises_does_not_take_the_window_down(self) -> None:
        def boom():
            raise OSError("no audio service")

        with mock.patch.object(audio_mod, "default_loopback_index",
                               staticmethod(boom)):
            self.panel.refresh_devices([], self.DEVICES)
        self.assertTrue(self.panel.system_combo.itemText(1).strip())

