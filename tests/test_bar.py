"""Checks for `screenrec.ui.bar`.

The bar is the application, so the things that would be noticed first are the things
checked here:

* it builds and paints in every state it has - idle, recording, recording while frames
  are being dropped, and the compact strip it becomes when it is in the shot;
* the **width actually changes** when the layout switches. Two layouts in one widget is
  the design, and a switch that forgets to resize is the failure that design invites;
* every signal arrives, with the right argument, from the interaction that should fire
  it - including the two zones of a toggle button, which are one widget and so cannot be
  told apart by connecting to two buttons;
* `set_mic_level` moves the meter, and the meter's one timer stops itself when the level
  has been zero for a while. A repaint 20 times a second next to a running screen
  capture is not free;
* the edge snapping maths, against monitor rectangles written out here rather than read
  from the machine.

No GPU, no recording, no visible window: the offscreen platform plugin is set before
PySide6 is imported.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# The app package lives under B:\ScreenRec\app, which is not the repository root.
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import i18n, winapi  # noqa: E402
from screenrec.ui import bar as bar_module  # noqa: E402
from screenrec.ui import theme  # noqa: E402


#: Two monitors with the geometry of the machine in docs/CONTRACT.md, written out so the
#: snapping tests do not depend on what is plugged in while they run.
LANDSCAPE = winapi.Monitor(
    index=0, device_name="\\\\.\\DISPLAY2", x=0, y=0, width=2560, height=1440,
    rotation=0, adapter="test",
)
PORTRAIT = winapi.Monitor(
    index=1, device_name="\\\\.\\DISPLAY1", x=2560, y=0, width=1440, height=2560,
    rotation=2, adapter="test",
)
MONITORS = [LANDSCAPE, PORTRAIT]


def _app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    created = QtWidgets.QApplication([])
    theme.apply(created)
    return created


class _Catch:
    """Record everything a signal emits, arguments included."""

    def __init__(self, signal: QtCore.SignalInstance) -> None:
        self.calls: list[tuple] = []
        signal.connect(self._hit)

    def _hit(self, *args: object) -> None:
        self.calls.append(args)

    @property
    def count(self) -> int:
        return len(self.calls)


def _mouse(
    kind: QtCore.QEvent.Type,
    local: QtCore.QPointF,
    globalpos: QtCore.QPointF,
    buttons: QtCore.Qt.MouseButton = QtCore.Qt.MouseButton.LeftButton,
) -> QtGui.QMouseEvent:
    return QtGui.QMouseEvent(
        kind,
        local,
        globalpos,
        QtCore.Qt.MouseButton.LeftButton,
        buttons,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )


def _click(widget: QtWidgets.QWidget, x: float, y: float) -> None:
    """A press and a release at the same point, as a real click arrives."""
    local = QtCore.QPointF(x, y)
    world = QtCore.QPointF(widget.mapToGlobal(local.toPoint()))
    QtWidgets.QApplication.sendEvent(
        widget, _mouse(QtCore.QEvent.Type.MouseButtonPress, local, world)
    )
    QtWidgets.QApplication.sendEvent(
        widget,
        _mouse(
            QtCore.QEvent.Type.MouseButtonRelease,
            local,
            world,
            QtCore.Qt.MouseButton.NoButton,
        ),
    )


def _furnished() -> bar_module.Bar:
    """A bar with every setter called, as the application would leave it."""
    widget = bar_module.Bar()
    widget.set_source("monitor", "Screen 1 · 2560×1440")
    widget.set_toggles(mic=True, system=False, clicks=True)
    widget.set_quick("60 fps", "MP4 · H.264")
    widget.set_footer(r"C:\Users\User\Videos\ScreenRec", "", "Ctrl+Alt+R")
    return widget


# ------------------------------------------------------------------ pure helpers


class TestHelpers(unittest.TestCase):
    def test_elapsed_format(self) -> None:
        self.assertEqual(bar_module.format_elapsed(0), "00:00")
        self.assertEqual(bar_module.format_elapsed(9.7), "00:09")
        self.assertEqual(bar_module.format_elapsed(75), "01:15")
        self.assertEqual(bar_module.format_elapsed(3599), "59:59")
        self.assertEqual(bar_module.format_elapsed(3600), "1:00:00")
        self.assertEqual(bar_module.format_elapsed(-5), "00:00")

    def test_shorten_path(self) -> None:
        self.assertEqual(
            bar_module.shorten_path(r"C:\Users\User\Videos\ScreenRec"),
            "…\\Videos\\ScreenRec",
        )
        self.assertEqual(bar_module.shorten_path(r"C:\Videos"), "C:\\Videos")
        self.assertEqual(bar_module.shorten_path(""), "")

    def test_snap_pulls_to_the_near_edge(self) -> None:
        width, height = 400, 90
        # Left edge: 30 is 12 px from the 18 px margin, inside the 28 px snap.
        self.assertEqual(
            bar_module.snap_to_edges(30, 700, width, height, MONITORS), (18, 700)
        )
        # 60 is 42 px away, outside it.
        self.assertEqual(
            bar_module.snap_to_edges(60, 700, width, height, MONITORS), (60, 700)
        )
        # Bottom right corner of the landscape monitor: both axes snap at once.
        right = 2560 - width - 18
        bottom = 1440 - height - 18
        self.assertEqual(
            bar_module.snap_to_edges(right + 10, bottom - 9, width, height, MONITORS),
            (right, bottom),
        )
        # The portrait monitor starts at x=2560, and its own edges are the ones used.
        self.assertEqual(
            bar_module.snap_to_edges(2580, 40, width, height, MONITORS),
            (2578, 18),
        )

    def test_snap_leaves_a_position_off_every_screen_alone(self) -> None:
        self.assertEqual(
            bar_module.snap_to_edges(-5000, -5000, 400, 90, MONITORS), (-5000, -5000)
        )

    def test_clamp_keeps_the_bar_on_a_screen(self) -> None:
        # Centre at (2500, 1425), still on the landscape monitor, but the bar hangs off
        # its right and bottom edges.
        self.assertEqual(
            bar_module.clamp_to_monitors(2300, 1380, 400, 90, MONITORS),
            (2160, 1350),
        )
        # Centre at (2600, 1445) is on the portrait monitor, so that is the one it is
        # clamped to - the bar follows the screen its middle is on, not its corner.
        self.assertEqual(
            bar_module.clamp_to_monitors(2400, 1400, 400, 90, MONITORS),
            (2560, 1400),
        )
        # Centre on no screen at all: parked bottom right of the first monitor.
        self.assertEqual(
            bar_module.clamp_to_monitors(-9000, -9000, 400, 90, MONITORS),
            (2560 - 400 - 18, 1440 - 90 - 18),
        )


# ------------------------------------------------------------------------ states


class TestStates(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        # The checks here quote the English wording, so the language is pinned rather
        # than inherited from whatever ran before; `TestLanguage` covers the Russian.
        i18n.set_language(i18n.LANG_EN)
        self.bar = _furnished()

    def tearDown(self) -> None:
        self.bar.deleteLater()

    def _grab(self, tag: str) -> QtGui.QPixmap:
        shot = self.bar.grab()
        self.assertFalse(shot.isNull(), f"{tag}: grab() produced nothing")
        self.assertEqual(
            shot.size(), self.bar.size(), f"{tag}: grab() is not the widget's size"
        )
        return shot

    def test_every_state_builds_and_paints(self) -> None:
        self._grab("idle")
        self.bar.set_recording(True)
        self.bar.set_progress(75.0, 128.0, 60.0)
        self._grab("recording")
        self.bar.set_progress(81.0, 140.0, 44.0, dropped=37)
        self.assertEqual(self.bar.detail.text(), "37 dropped")
        self._grab("recording, dropping frames")
        self.bar.set_recording(True, in_shot=True)
        self._grab("recording, in shot")
        self.bar.set_recording(False)
        self._grab("idle again")

    def test_width_changes_between_idle_and_recording(self) -> None:
        idle = self.bar.width()
        self.bar.set_recording(True)
        recording = self.bar.width()
        self.assertNotEqual(idle, recording)
        self.assertLess(recording, idle, "the recording strip should be the narrower one")
        self.bar.set_recording(True, in_shot=True)
        self.assertLess(self.bar.width(), recording, "in shot should be narrower again")
        self.bar.set_recording(False)
        self.assertEqual(self.bar.width(), idle, "going back to idle left a stale size")

    def test_footer_only_exists_when_idle(self) -> None:
        self.assertTrue(self.bar._footer.isVisibleTo(self.bar))
        self.bar.set_recording(True)
        self.assertFalse(self.bar._footer.isVisibleTo(self.bar))
        self.assertFalse(self.bar._idle.isVisibleTo(self.bar))
        self.bar.set_recording(False)
        self.assertTrue(self.bar._footer.isVisibleTo(self.bar))

    def test_in_shot_fades_and_drops_the_extras(self) -> None:
        self.bar.set_recording(True, in_shot=True)
        self.assertAlmostEqual(self.bar.windowOpacity(), 0.55, places=2)
        self.assertFalse(self.bar.detail.isVisibleTo(self.bar))
        self.assertFalse(self.bar.discard_button.isVisibleTo(self.bar))
        self.bar.set_recording(True, in_shot=False)
        self.assertAlmostEqual(self.bar.windowOpacity(), 1.0, places=2)
        self.assertTrue(self.bar.detail.isVisibleTo(self.bar))

    def test_setters_reach_the_widgets(self) -> None:
        self.bar.set_source("window", "Visual Studio Code")
        self.assertEqual(self.bar.source_chip.label(), "Visual Studio Code")
        self.assertEqual(self.bar.source_chip.kind, "window")
        self.bar.set_quick("30 fps", "MKV · HEVC")
        self.assertEqual(self.bar.fps_pill.text(), "30 fps")
        self.assertEqual(self.bar.format_pill.text(), "MKV · HEVC")
        self.bar.set_toggles(mic=False, system=True, clicks=False)
        self.assertFalse(self.bar.mic_button.is_on())
        self.assertEqual(self.bar.mic_button.kind, "microphone_off")
        self.assertTrue(self.bar.system_button.is_on())
        self.assertEqual(self.bar.system_button.kind, "speaker")
        self.bar.set_recording(True)
        self.bar.set_progress(3661.0, 2048.0, 59.0)
        self.assertEqual(self.bar.clock.text(), "1:01:01")
        self.assertEqual(self.bar.detail.text(), "2048 MB · 59 fps")

    def test_unknown_source_icon_is_reported_not_silently_blank(self) -> None:
        with mock.patch("builtins.print") as printed:
            self.bar.set_source("teapot", "Something")
        self.assertTrue(printed.called, "an unknown icon name must say so")
        self.assertEqual(self.bar.source_chip.kind, "monitor")

    def test_last_file_button_is_disabled_until_there_is_one(self) -> None:
        self.assertFalse(self.bar.last_file_button.isEnabled())
        self.bar.set_footer(r"C:\Videos", r"C:\Videos\clip.mp4", "F9")
        self.assertTrue(self.bar.last_file_button.isEnabled())
        self.assertEqual(self.bar.keycap.text(), "F9")

    def test_keycap_is_measured_not_guessed(self) -> None:
        cap = self.bar.keycap
        cap.set_text("Ctrl+Alt+R")
        metrics = QtGui.QFontMetrics(cap._font())
        self.assertEqual(
            cap.sizeHint().width(),
            metrics.horizontalAdvance("Ctrl+Alt+R") + 2 * bar_module.Keycap.PAD_X,
        )
        cap.set_text("F9")
        self.assertLess(cap.sizeHint().width(), 60)

    def test_the_chevron_zone_is_drawn_not_just_hit_tested(self) -> None:
        # A zone the user cannot see is a zone the user will not use, so the chevron
        # has to leave ink in the right-hand part of the button.
        button = self.bar.mic_button
        image = button.grab().toImage()
        split = int(button.split_x())
        background = image.pixel(button.width() - 2, 2)
        inked = sum(
            1
            for x in range(split, button.width())
            for y in range(button.height())
            if image.pixel(x, y) != background
        )
        self.assertGreater(inked, 10, "nothing was drawn in the chevron zone")

    def test_pause_is_offered_and_shows_that_the_clock_stopped(self) -> None:
        """Pause was thought impossible and is not.

        ffmpeg has no pause command, which was read for a while as the product being
        unable to pause. A paused recording is simply more than one segment, joined with
        -c copy at the end - measured at 0.03 s for three segments, into one file whose
        duration is the sum. So the button exists, and the clock must visibly stop:
        a paused recording mistaken for a running one is how the end of a take is lost.
        """
        bar = bar_module.Bar()
        self.addCleanup(bar.deleteLater)
        bar.set_recording(True)
        bar.set_progress(94.0, 128.0, 59.8, 0)

        fired: list[int] = []
        bar.pause_requested.connect(lambda: fired.append(1))
        bar.pause_button.click()
        self.assertEqual([1], fired, "the pause button did not ask for a pause")

        bar.set_paused(True)
        self.assertTrue(bar.pause_button.is_on())
        self.assertEqual(bar.pause_button.toolTip(), "Carry on recording")
        self.assertTrue(getattr(bar.clock, "_dim", False), "a paused clock must dim")
        bar.grab()

        bar.set_paused(False)
        self.assertFalse(getattr(bar.clock, "_dim", False))
        bar.grab()

    def test_nothing_in_the_module_takes_focus(self) -> None:
        # Parsed rather than grepped: the module docstring names these three calls in
        # order to say it does not make them, and a grep cannot tell the two apart.
        tree = ast.parse(Path(bar_module.__file__).read_text(encoding="utf-8"))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        for forbidden in ("activateWindow", "raise_", "setFocus"):
            self.assertNotIn(
                forbidden, called, f"{forbidden}() would pull focus off the user's app"
            )


# ---------------------------------------------------------------------- language


class TestLanguage(unittest.TestCase):
    """The bar in Russian, and the switch happening while it is on screen.

    The language is process-wide state, so every test here puts it back: a test that
    left Russian behind would fail the next module to quote an English sentence.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        i18n.set_language(i18n.LANG_EN)
        self.addCleanup(i18n.set_language, i18n.LANG_EN)
        self.bar = _furnished()
        self.addCleanup(self.bar.deleteLater)

    def test_the_switch_reaches_a_bar_that_is_already_built(self) -> None:
        english = self.bar.record_button.toolTip()
        self.assertEqual(english, "Start recording")
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(self.bar.record_button.toolTip(), "Начать запись")
        self.assertEqual(self.bar.settings_button.toolTip(), "Настройки")
        self.assertEqual(self.bar.quit_button.toolTip(), "Выйти из ScreenRec")
        self.assertEqual(self.bar.last_file_button.text(), "Последний файл")
        self.assertEqual(self.bar.last_file_button.toolTip(), "Записей пока нет")
        self.assertEqual(self.bar.stop_button.toolTip(), "Остановить запись")
        i18n.set_language(i18n.LANG_EN)
        self.assertEqual(self.bar.record_button.toolTip(), english)

    def test_the_detail_line_is_rewritten_in_the_new_language(self) -> None:
        self.bar.set_recording(True)
        self.bar.set_progress(75.0, 128.0, 60.0)
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(self.bar.detail.text(), "128 МБ · 60 к/с")

    def test_dropped_frames_take_the_russian_plural(self) -> None:
        """1, 2 and 5 are three different words, and 11 is not what 21 is."""
        self.bar.set_recording(True)
        i18n.set_language(i18n.LANG_RU)
        for dropped, expected in (
            (1, "пропущено 1 кадр"),
            (3, "пропущено 3 кадра"),
            (11, "пропущено 11 кадров"),
            (21, "пропущено 21 кадр"),
        ):
            with self.subTest(dropped=dropped):
                self.bar.set_progress(10.0, 1.0, 60.0, dropped=dropped)
                self.assertEqual(self.bar.detail.text(), expected)

    def test_the_countdown_tooltip_follows_the_language(self) -> None:
        self.bar.set_countdown(3)
        self.assertEqual(
            self.bar.record_button.toolTip(), "Starting in 3… click to cancel"
        )
        i18n.set_language(i18n.LANG_RU)
        self.assertEqual(
            self.bar.record_button.toolTip(),
            "Старт через 3… нажмите, чтобы отменить",
        )

    def test_a_russian_bar_still_paints_and_fits(self) -> None:
        i18n.set_language(i18n.LANG_RU)
        self.assertFalse(self.bar.grab().isNull())
        self.bar.set_recording(True)
        self.bar.set_progress(75.0, 128.0, 60.0)
        self.assertFalse(self.bar.grab().isNull())
        self.assertEqual(self.bar.grab().size(), self.bar.size())


# ----------------------------------------------------------------------- signals


class TestSignals(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.bar = _furnished()

    def tearDown(self) -> None:
        self.bar.deleteLater()

    def test_plain_buttons(self) -> None:
        cases = [
            (self.bar.record_button, self.bar.record_requested),
            (self.bar.settings_button, self.bar.settings_clicked),
            (self.bar.source_chip, self.bar.source_clicked),
            (self.bar.fps_pill, self.bar.fps_clicked),
            (self.bar.format_pill, self.bar.format_clicked),
            (self.bar.folder_button, self.bar.folder_clicked),
        ]
        for button, signal in cases:
            with self.subTest(button=button.toolTip() or type(button).__name__):
                catcher = _Catch(signal)
                button.click()
                self.assertEqual(catcher.count, 1)

    def test_recording_buttons(self) -> None:
        self.bar.set_recording(True)
        stop = _Catch(self.bar.stop_requested)
        discard = _Catch(self.bar.discard_requested)
        folder = _Catch(self.bar.folder_clicked)
        self.bar.stop_button.click()
        self.bar.discard_button.click()
        self.bar.folder_button_rec.click()
        self.assertEqual((stop.count, discard.count, folder.count), (1, 1, 1))

    def test_last_file_needs_a_file(self) -> None:
        catcher = _Catch(self.bar.last_file_clicked)
        self.bar.last_file_button.click()
        self.assertEqual(catcher.count, 0, "a disabled button must not fire")
        self.bar.set_footer(r"C:\Videos", r"C:\Videos\clip.mp4", "F9")
        self.bar.last_file_button.click()
        self.assertEqual(catcher.count, 1)

    def test_toggles_carry_their_new_value(self) -> None:
        self.bar.set_toggles(mic=False, system=False, clicks=False)
        for button, signal in (
            (self.bar.mic_button, self.bar.mic_toggled),
            (self.bar.system_button, self.bar.system_toggled),
            (self.bar.clicks_button, self.bar.clicks_toggled),
        ):
            with self.subTest(button=button.toolTip()):
                catcher = _Catch(signal)
                button.click()
                self.assertEqual(catcher.calls, [(True,)])
                button.click()
                self.assertEqual(catcher.calls, [(True,), (False,)])

    def test_the_left_zone_toggles_and_the_right_zone_opens_the_menu(self) -> None:
        button = self.bar.mic_button
        self.bar.set_toggles(mic=False, system=False, clicks=False)
        toggled = _Catch(self.bar.mic_toggled)
        menu = _Catch(self.bar.mic_menu_requested)

        middle = button.height() / 2
        left_zone = button.split_x() * 0.5
        right_zone = button.split_x() + (button.width() - button.split_x()) * 0.5
        self.assertEqual(button.zone_at(QtCore.QPointF(left_zone, middle)), "toggle")
        self.assertEqual(button.zone_at(QtCore.QPointF(right_zone, middle)), "menu")

        _click(button, left_zone, middle)
        self.assertEqual(toggled.calls, [(True,)])
        self.assertEqual(menu.count, 0)
        self.assertTrue(button.is_on())

        _click(button, right_zone, middle)
        self.assertEqual(menu.count, 1, "the chevron zone must open the menu")
        self.assertEqual(
            toggled.calls, [(True,)], "the chevron zone must not toggle as well"
        )
        self.assertTrue(button.is_on(), "the chevron zone changed the state")

    def test_system_and_pointer_chevrons(self) -> None:
        system = _Catch(self.bar.system_menu_requested)
        settings = _Catch(self.bar.settings_clicked)
        for button in (self.bar.system_button, self.bar.clicks_button):
            right = button.split_x() + (button.width() - button.split_x()) * 0.5
            _click(button, right, button.height() / 2)
        self.assertEqual(system.count, 1)
        # The pointer toggle has no device list; its chevron opens the settings panel.
        self.assertEqual(settings.count, 1)

    def test_right_click_menu_builds_in_both_states(self) -> None:
        # exec() would spin an event loop, so the menu is built the same way the handler
        # builds it and then inspected; what is proven here is that it builds at all.
        for recording in (False, True):
            with self.subTest(recording=recording):
                self.bar.set_recording(recording)
                menu = QtWidgets.QMenu(self.bar)
                menu.setStyleSheet(theme.style_sheet())
                self.assertTrue(menu.styleSheet())
                menu.deleteLater()


# ------------------------------------------------------------------------- meter


class TestMeter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.bar = _furnished()
        self.meter = self.bar.meter

    def tearDown(self) -> None:
        self.bar.deleteLater()

    def test_level_lights_bars_in_proportion(self) -> None:
        for level, lit in ((0.0, 0), (0.2, 1), (0.5, 3), (1.0, 5)):
            with self.subTest(level=level):
                self.meter.set_mic_level(level)
                self.assertEqual(self.meter.lit_bars(), lit)

    def test_out_of_range_levels_are_clamped_not_refused(self) -> None:
        self.meter.set_mic_level(4.0)
        self.assertEqual(self.meter.level(), 1.0)
        self.meter.set_mic_level(-1.0)
        self.assertLessEqual(self.meter.level(), 1.0)
        self.assertGreaterEqual(self.meter.level(), 0.0)

    def test_the_fall_is_gradual_and_the_timer_stops_itself(self) -> None:
        self.meter.set_mic_level(1.0)
        self.assertTrue(self.meter.timer_running(), "a live level must drive the timer")
        self.assertEqual(self.meter.level(), 1.0)

        self.meter.set_mic_level(0.0)
        self.assertEqual(
            self.meter.level(), 1.0, "the fall is a decay, not an instant drop"
        )
        # The timer is driven by hand so the check is deterministic rather than a sleep.
        self.meter._tick()
        self.assertAlmostEqual(self.meter.level(), 1.0 - bar_module.METER_DECAY, places=5)

        ticks = 0
        while self.meter.timer_running() and ticks < 200:
            self.meter._tick()
            ticks += 1
        self.assertFalse(self.meter.timer_running(), "the decay timer never stopped")
        self.assertEqual(self.meter.level(), 0.0)
        # Decay to zero, then the quiet count: both have to have happened.
        self.assertGreaterEqual(ticks, bar_module.METER_IDLE_TICKS)

        self.meter.set_mic_level(0.4)
        self.assertTrue(self.meter.timer_running(), "sound must start the timer again")

    def test_the_painted_bars_change_with_the_level(self) -> None:
        # The accessor could be right while the paint ignores it, so the pixels are
        # compared: quiet and loud must not look the same.
        self.meter.set_mic_level(0.0)
        quiet = self.meter.grab().toImage()
        self.meter.set_mic_level(1.0)
        loud = self.meter.grab().toImage()
        self.assertNotEqual(quiet, loud, "the meter painted the same thing at 0 and 1")

    def test_a_grab_with_a_live_level(self) -> None:
        self.meter.set_mic_level(0.7)
        shot = self.bar.grab()
        self.assertFalse(shot.isNull())
        self.bar.set_toggles(mic=False, system=False, clicks=False)
        self.assertFalse(self.bar.grab().isNull())


# -------------------------------------------------------------------- placement


class TestPlacement(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.bar = _furnished()

    def tearDown(self) -> None:
        self.bar.deleteLater()

    def test_place_clamps_to_the_monitor(self) -> None:
        self.bar.place(2400, 1400, MONITORS)
        expected = bar_module.clamp_to_monitors(
            2400, 1400, self.bar.width(), self.bar.height(), MONITORS
        )
        self.assertEqual((self.bar.x(), self.bar.y()), expected)

    def test_place_default_is_bottom_right_of_the_first_monitor(self) -> None:
        self.bar.place_default(MONITORS)
        self.assertEqual(
            self.bar.x(), 2560 - self.bar.width() - bar_module.MARGIN
        )
        self.assertEqual(
            self.bar.y(), 1440 - self.bar.height() - bar_module.MARGIN * 3
        )

    def test_place_default_with_no_monitors_says_so(self) -> None:
        with mock.patch("builtins.print") as printed:
            self.bar.place_default([])
        self.assertTrue(printed.called)
        self.assertEqual((self.bar.x(), self.bar.y()), (80, 80))

    def test_move_off_steps_onto_the_other_screen(self) -> None:
        self.bar.place(200, 200, MONITORS)
        self.assertTrue(self.bar.move_off(0, MONITORS))
        self.assertGreaterEqual(self.bar.x(), PORTRAIT.x)
        # Already clear of the recorded screen: nothing moves.
        where = (self.bar.x(), self.bar.y())
        self.assertTrue(self.bar.move_off(0, MONITORS))
        self.assertEqual((self.bar.x(), self.bar.y()), where)

    def test_move_off_reports_when_there_is_nowhere_to_go(self) -> None:
        self.bar.place(200, 200, [LANDSCAPE])
        self.assertFalse(self.bar.move_off(0, [LANDSCAPE]))


# ------------------------------------------------------------------------ drag


class TestDrag(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.bar = _furnished()
        self.bar.place(400, 400, MONITORS)
        # mouseMoveEvent asks the machine for its monitors; the fake pair keeps the
        # snapping deterministic and keeps DXGI out of the test.
        patcher = mock.patch.object(
            bar_module.winapi, "list_monitors", return_value=MONITORS
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.bar.deleteLater()

    def _drag(self, to_x: int, to_y: int) -> _Catch:
        catcher = _Catch(self.bar.moved)
        start_local = QtCore.QPointF(6, 6)
        start_world = QtCore.QPointF(self.bar.x() + 6, self.bar.y() + 6)
        QtWidgets.QApplication.sendEvent(
            self.bar,
            _mouse(QtCore.QEvent.Type.MouseButtonPress, start_local, start_world),
        )
        end_world = QtCore.QPointF(to_x + 6, to_y + 6)
        QtWidgets.QApplication.sendEvent(
            self.bar,
            _mouse(QtCore.QEvent.Type.MouseMove, start_local, end_world),
        )
        QtWidgets.QApplication.sendEvent(
            self.bar,
            _mouse(
                QtCore.QEvent.Type.MouseButtonRelease,
                start_local,
                end_world,
                QtCore.Qt.MouseButton.NoButton,
            ),
        )
        return catcher

    def test_a_drag_moves_and_reports(self) -> None:
        catcher = self._drag(900, 600)
        self.assertEqual((self.bar.x(), self.bar.y()), (900, 600))
        self.assertEqual(catcher.calls, [(900, 600)])

    def test_a_drag_near_an_edge_snaps(self) -> None:
        catcher = self._drag(24, 600)
        self.assertEqual(self.bar.x(), bar_module.MARGIN)
        self.assertEqual(catcher.calls, [(bar_module.MARGIN, 600)])

    def test_a_press_that_did_not_move_is_not_a_drag(self) -> None:
        catcher = _Catch(self.bar.moved)
        where = (self.bar.x(), self.bar.y())
        local = QtCore.QPointF(6, 6)
        world = QtCore.QPointF(self.bar.x() + 6, self.bar.y() + 6)
        QtWidgets.QApplication.sendEvent(
            self.bar, _mouse(QtCore.QEvent.Type.MouseButtonPress, local, world)
        )
        QtWidgets.QApplication.sendEvent(
            self.bar,
            _mouse(
                QtCore.QEvent.Type.MouseButtonRelease,
                local,
                world,
                QtCore.Qt.MouseButton.NoButton,
            ),
        )
        self.assertEqual(catcher.count, 0)
        self.assertEqual((self.bar.x(), self.bar.y()), where)

    def test_a_one_pixel_wobble_is_still_a_click(self) -> None:
        catcher = self._drag(self.bar.x() + 1, self.bar.y())
        self.assertEqual(
            catcher.count, 0, f"a {bar_module.DRAG_SLOP} px slop should absorb this"
        )


if __name__ == "__main__":
    unittest.main()


class TestTheCameraButton(unittest.TestCase):
    """It is on both rows, because it is the one toggle that means something in the
    middle of a take."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.bar = _furnished()

    def tearDown(self) -> None:
        self.bar.deleteLater()

    def test_both_rows_have_one(self) -> None:
        self.assertIsNotNone(self.bar.camera_button)
        self.assertIsNotNone(self.bar.camera_button_rec)

    def test_either_one_reports_the_change(self) -> None:
        for button in (self.bar.camera_button, self.bar.camera_button_rec):
            with self.subTest(button=button):
                self.bar.set_camera(False)
                catcher = _Catch(self.bar.camera_toggled)
                button.click()
                self.assertEqual(catcher.count, 1)
                self.assertEqual(catcher.calls, [(True,)])

    def test_the_two_rows_never_disagree(self) -> None:
        """Switching it off while recording must not leave the idle row claiming it is
        on - the user would come back to a bar that lies about the setting."""
        self.bar.set_camera(False)
        self.bar.camera_button_rec.click()
        self.assertTrue(self.bar.camera_button.is_on())
        self.bar.camera_button.click()
        self.assertFalse(self.bar.camera_button_rec.is_on())

    def test_the_icon_says_which_state_it_is_in(self) -> None:
        self.bar.set_camera(True)
        self.assertEqual(self.bar.camera_button.kind, "camera")
        self.bar.set_camera(False)
        self.assertEqual(self.bar.camera_button.kind, "camera_off")

    def test_set_toggles_carries_it(self) -> None:
        self.bar.set_toggles(False, False, False, True)
        self.assertTrue(self.bar.camera_button.is_on())
        self.bar.set_toggles(False, False, False, False)
        self.assertFalse(self.bar.camera_button.is_on())

    def test_the_chevron_asks_for_the_device_list(self) -> None:
        for button in (self.bar.camera_button, self.bar.camera_button_rec):
            with self.subTest(button=button):
                catcher = _Catch(self.bar.camera_menu_requested)
                button.menu_requested.emit()
                self.assertEqual(catcher.count, 1)

