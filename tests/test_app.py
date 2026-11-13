"""The controller's two start-up decisions: when the probe runs, and what it decides.

Both of these were defects on 2026-11-04 and both were invisible to a green suite,
because nothing in `tests/` ever reached `ui/app.py`'s planning code at all:

* `_probe_encoders` ran inside `Application.__init__`, **before** `self.bar.show()`.
  Since the probe moved onto the real `ddagrab` pipeline it costs 5.87 s on this machine
  with an empty cache (measured 2026-11-04), so the first launch on somebody else's
  computer was six seconds with no window and no tray icon - nothing at all to say the
  program had started.
* `_plan_for` and `_plan_for_source` each carried a copy of an override that read
  `capabilities.best()` and applied it only when `pix_fmt == "yuv420p"`. That recorded
  the profile called "No GPU - x264" on NVENC, and left the 4:4:4 profile pointing at an
  encoder that cannot do 4:4:4.

`Application` itself is not built here, for the reason `tests/test_app_i18n.py` gives:
its constructor registers global hotkeys, puts an icon in the system tray, starts audio
meters and can open a modal dialog. What it *decides* is reachable without it - the
methods below only touch `self.bar`, `self.settings`, `self.encoders` and `self.monitors`
- so they are exercised on a real `Bar` with those fields set. The one thing that cannot
be reached that way is the **order of calls** inside `__init__`, which is the whole of
the first defect; that is read out of the source, exactly as the language test does.

No GPU is needed: every probe result here is written out by hand. The machines in
`_machine` are fakes, and two of them - AMD and Intel - are families nobody on this
project can test for real; see `encoders.YUV444_ENCODERS`.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Must be set before the first QGuiApplication exists, so before PySide6 is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from screenrec import capabilities, config, encoders, recorder, storage, winapi  # noqa: E402
from screenrec import audio as audio_module  # noqa: E402
from screenrec import multi as multi_module  # noqa: E402
from screenrec.ui import app as app_module  # noqa: E402
from screenrec.ui import surfaces  # noqa: E402
from screenrec.ui import theme  # noqa: E402
from screenrec.ui.bar import Bar  # noqa: E402

LANDSCAPE = winapi.Monitor(
    index=0, device_name="\\\\.\\DISPLAY2", x=0, y=0, width=2560, height=1440,
    rotation=0, adapter="test",
)


def _qt_app() -> QtWidgets.QApplication:
    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        return existing
    created = QtWidgets.QApplication([])
    theme.apply(created)
    return created


def _machine(*working: str) -> list[capabilities.EncoderResult]:
    """A probe answer: every candidate, with the named ones having encoded a frame.

    The shape a real probe returns - `listed` true throughout, because the bundled
    build lists all twelve names here and the interesting failures are the ones that
    list and then refuse.
    """
    return [
        capabilities.EncoderResult(
            name=name, codec=codec, vendor=vendor, listed=True,
            works=name in working,
            reason="" if name in working else "it did not encode a frame",
            millis=400,
        )
        for name, codec, vendor in capabilities.CANDIDATES
    ]


#: This desk: NVENC works, Media Foundation dispatches to the same card, no AV1 on
#: Ampere. Measured on 2026-11-04 by `capabilities.probe` and written out here.
THIS_MACHINE = _machine("hevc_nvenc", "h264_nvenc", "hevc_mf", "h264_mf", "libx264")
#: A machine whose own vendor family refuses but whose Windows path works. Not observed
#: anywhere - it is the case `hevc_mf` exists for, written as a fake.
ONLY_WINDOWS = _machine("hevc_mf", "h264_mf", "libx264")
#: No hardware at all: a virtual machine, a remote session, a card with no encoder.
ONLY_CPU = _machine("libx264")
#: The start-up warning's case: ffmpeg lists everything and the machine runs none of it.
NOTHING = _machine()


class _Controller:
    """An `Application` with only the fields the probe and the planner touch.

    Every method here is the real one, taken off the class, so a change to any of them
    is what these tests run. Everything else the constructor would have started - the
    tray, the hotkeys, the meters, the overlays - is simply absent.
    """

    _say = app_module.Application._say
    _say_each = app_module.Application._say_each
    _say_now_then_queue = app_module.Application._say_now_then_queue
    _next_status = app_module.Application._next_status
    _clear_status_queue = app_module.Application._clear_status_queue
    _loopback_pipe = app_module.Application._loopback_pipe
    _encoder_for_frame = app_module.Application._encoder_for_frame
    _note_small_frame = app_module.Application._note_small_frame
    _source_label = app_module.Application._source_label
    _source_chosen = app_module.Application._source_chosen
    _refresh_bar = app_module.Application._refresh_bar
    _resolve_sources = app_module.Application._resolve_sources
    _is_recording = app_module.Application._is_recording
    _auto_stop = app_module.Application._auto_stop
    _hold_auto_stop = app_module.Application._hold_auto_stop
    _resume_auto_stop = app_module.Application._resume_auto_stop
    toggle_pause = app_module.Application.toggle_pause
    _show_pause_state = app_module.Application._show_pause_state
    stop_recording = app_module.Application.stop_recording
    _stop_all_screens = app_module.Application._stop_all_screens
    _tick = app_module.Application._tick
    _probe_encoders = app_module.Application._probe_encoders
    _probe_step = app_module.Application._probe_step
    _encoder_for = app_module.Application._encoder_for
    _named_encoder = app_module.Application._named_encoder
    _chosen_encoder_name = app_module.Application._chosen_encoder_name
    _estimate = app_module.Application._estimate
    _note_encoder = app_module.Application._note_encoder
    _flush_encoder_note = app_module.Application._flush_encoder_note
    _plan_for = app_module.Application._plan_for
    _plan_for_source = app_module.Application._plan_for_source
    _resolve_source = app_module.Application._resolve_source
    _chosen_monitor = app_module.Application._chosen_monitor
    _capture_size = app_module.Application._capture_size
    start_recording = app_module.Application.start_recording
    _really_start = app_module.Application._really_start
    _start_all_screens = app_module.Application._start_all_screens
    open_settings = app_module.Application.open_settings
    # The settings window asks for the global hotkeys to be stood down while a
    # shortcut box is reading a key press, and `open_settings` connects to it.
    # The bar's camera button, which is the one toggle that works mid-take.
    # The take's own watch on the system-sound track; see `_watch_system_sound`.
    _watch_system_sound = app_module.Application._watch_system_sound
    # Following the playback device, and saying where the sound went instead.
    _tick_endpoints = app_module.Application._tick_endpoints
    _follow_default = app_module.Application._follow_default
    _advise_on_silence = app_module.Application._advise_on_silence
    _loopback_index_named = app_module.Application._loopback_index_named
    _loopback_name = app_module.Application._loopback_name
    _on_camera_toggled = app_module.Application._on_camera_toggled
    _choose_camera = app_module.Application._choose_camera
    _start_webcam = app_module.Application._start_webcam
    _stop_highlight = app_module.Application._stop_highlight
    _stop_webcam = app_module.Application._stop_webcam
    _webcam_area = app_module.Application._webcam_area
    _on_hotkey_capture = app_module.Application._on_hotkey_capture
    _start_hotkeys = app_module.Application._start_hotkeys
    _stop_hotkeys = app_module.Application._stop_hotkeys
    quit = app_module.Application.quit

    def __init__(self, bar: Bar, settings: config.Settings,
                 found: list[capabilities.EncoderResult] | None = None) -> None:
        self.bar = bar
        self.settings = settings
        self.monitors = [LANDSCAPE]
        self.encoders = found if found is not None else []
        self.last_file = None
        self._status = None
        self._ffmpeg = "ffmpeg.exe"
        self._probing = False
        self._probed = found is not None
        self._encoder_note = ""
        self._frame_note = ""
        self._pending_note = None
        self._quit_wanted = False
        self.picker_opened = 0
        # The panel is built lazily by `open_settings`, exactly as in `Application`.
        self.panel = None
        self.session = None
        self.multi = None
        self.mics: list = []
        self.loopbacks: list = []
        self._pending_start = False
        self._space_warned = False
        self._progress_ready = _Stub()
        self.countdown = _Stub()
        self.tray = _Stub()
        self.timer = _Stub()
        self.overlays_shown = 0
        self.overlays_hidden = 0
        self.saved = 0
        # Real `QTimer`s, not stubs: `_hold_auto_stop` reads `remainingTime()` and
        # `_say_each` asks `isActive()`, and a stub answering `None` to either would
        # be testing the stub. They have no parent because `_Controller` is not a
        # `QObject`; nothing here lets one fire, and `ControllerTestCase` stops them.
        self.auto_stop_timer = QtCore.QTimer()
        self.auto_stop_timer.setSingleShot(True)
        self.auto_stop_timer.timeout.connect(self._auto_stop)
        self._status_queue: list = []
        self._status_timer = QtCore.QTimer()
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._next_status)
        self._takes_started = 0
        self._take_tag = ""
        self._auto_stop_left = 0
        # The overlays `_hide_overlays` puts away. None of them is ever started here.
        self.webcam = None
        self.highlight = None
        self.frames: list = []
        # Where the sound is going, as `Application` tracks it.
        self._capturing_name = ""
        self._advised_name = ""
        self.endpoints = None
        self.meters_started = 0

    def open_source_picker(self) -> None:
        self.picker_opened += 1

    def _show_overlays(self) -> None:
        self.overlays_shown += 1

    def _hide_overlays(self) -> None:
        self.overlays_hidden += 1

    def _start_meters(self) -> None:
        """The real one opens WASAPI streams; nothing here may touch hardware."""
        self.meters_started += 1

    def _watch_space(self) -> None:
        pass

    def _loopback_index(self) -> int:
        """The real one asks WASAPI for the default playback device. Nothing here is
        about which device the sound comes from, and no test may need hardware."""
        return -1

    def _save(self) -> None:
        self.saved += 1

    def _settings_changed(self) -> None:
        pass

    def drain_status(self) -> list[str]:
        """Every line still waiting its turn, shown one after another.

        Stands in for `STATUS_DWELL_MS` of wall clock: `_next_status` is exactly what
        the timer calls, so this walks the queue the way the bar would.
        """
        shown = []
        while self._status_queue:
            self._next_status()
            shown.append(self.bar.status_label.text())
        return shown


class _Stub:
    """Swallows the calls `_really_start` makes on the timers, the tray and the ring."""

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class ControllerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        _qt_app()
        self.bar = Bar()
        self.addCleanup(self.bar.deleteLater)

    def controller(self, found=None, **settings) -> _Controller:
        made = _Controller(self.bar, config.Settings(**settings), found)
        # No test here waits for a timer, and one left running would fire into a
        # controller the next test has thrown away.
        self.addCleanup(made.auto_stop_timer.stop)
        self.addCleanup(made._status_timer.stop)
        return made


# ------------------------------------------------------------- when it runs


class StartUpOrderTest(unittest.TestCase):
    """The window has to be on screen before the probe starts, not after.

    Read out of the source: running `__init__` means a tray icon and three global
    hotkeys, and the failure is an order of calls, which is exactly what source text
    shows. This is the same argument - and the same technique - as
    `test_app_i18n.StartUpLanguageTest`.
    """

    def setUp(self) -> None:
        super().setUp()
        self.source = inspect.getsource(app_module.Application.__init__)
        #: The scheduling call itself, not the field comment that also names the probe.
        self.scheduled = self.source.index("singleShot(0, self._probe_encoders)")

    def test_the_bar_is_shown_before_the_probe_is_even_scheduled(self) -> None:
        shown = self.source.index("self.bar.show()")
        probe = self.scheduled
        self.assertLess(
            shown, probe,
            "the probe is seconds long on a machine it has not seen before; starting "
            "it first is a launch that shows nothing at all until it finishes",
        )

    def test_the_probe_is_scheduled_rather_than_called(self) -> None:
        self.assertNotIn(
            "self._probe_encoders()", self.source,
            "called straight from the constructor the probe blocks the constructor, "
            "which runs before QApplication.exec - so the bar is built, told to show "
            "itself, and never painted",
        )
        self.assertIn("singleShot(0, self._probe_encoders)", self.source)

    def test_the_tray_icon_is_up_before_the_probe_too(self) -> None:
        self.assertLess(
            self.source.index("self.tray.show()"), self.scheduled,
            "the tray icon is the only thing left when the bar is hidden, so it has "
            "to exist before anything slow runs",
        )

    def test_the_probe_reports_its_progress(self) -> None:
        """`capabilities.probe` has had an `on_step` hook all along and nothing used it."""
        source = inspect.getsource(app_module.Application._probe_encoders)
        self.assertIn("on_step=self._probe_step", source)

    def test_the_progress_hook_pumps_the_queue(self) -> None:
        """This is what buys the right to keep the probe on the UI thread."""
        source = inspect.getsource(app_module.Application._probe_step)
        self.assertIn(
            "processEvents", source,
            "without this the bar is on screen and frozen for the whole probe, which "
            "looks like a hung program rather than a slow one",
        )


class ProbeTest(ControllerTestCase):
    """What the probe does to the status line, and that it only runs once."""

    def _probe_returning(self, found, seen=None):
        """A stand-in for `capabilities.probe` that calls the progress hook like the
        real one does - before each candidate, with its index."""

        def fake(ffmpeg, use_cache=True, on_step=None):
            for index, row in enumerate(found):
                if on_step is not None:
                    on_step(row.name, index, len(found))
                if seen is not None:
                    seen.append(self.bar.status_label.text())
            return list(found)

        return fake

    def test_the_candidate_being_tried_is_named_on_the_bar(self) -> None:
        seen: list[str] = []
        controller = self.controller()
        with mock.patch.object(
            capabilities, "probe", self._probe_returning(THIS_MACHINE, seen)
        ):
            controller._probe_encoders()
        self.assertEqual(len(seen), len(THIS_MACHINE))
        self.assertIn("hevc_nvenc", seen[0])
        self.assertIn("1", seen[0])
        self.assertIn(str(len(THIS_MACHINE)), seen[0])

    def test_the_line_goes_back_to_what_it_said_before(self) -> None:
        """The progress is transient; a warning that was already there is not."""
        controller = self.controller()
        controller._say("the settings file could not be read", warn=True)
        with mock.patch.object(capabilities, "probe", self._probe_returning(THIS_MACHINE)):
            controller._probe_encoders()
        self.assertEqual(
            self.bar.status_label.text(), "the settings file could not be read",
            "a real problem was pushed off the bar by the last candidate's name",
        )

    def test_an_empty_line_is_left_empty(self) -> None:
        controller = self.controller()
        with mock.patch.object(capabilities, "probe", self._probe_returning(THIS_MACHINE)):
            controller._probe_encoders()
        self.assertEqual(self.bar.status_label.text(), "")

    def test_it_runs_once(self) -> None:
        controller = self.controller()
        calls = []

        def fake(ffmpeg, use_cache=True, on_step=None):
            calls.append(ffmpeg)
            return list(THIS_MACHINE)

        with mock.patch.object(capabilities, "probe", fake):
            controller._probe_encoders()
            controller._probe_encoders()
        self.assertEqual(len(calls), 1, "five seconds, twice, for the same answer")
        self.assertTrue(controller._probed)

    def test_nothing_is_probed_without_ffmpeg(self) -> None:
        controller = self.controller()
        controller._ffmpeg = ""
        with mock.patch.object(capabilities, "probe", self._probe_returning(THIS_MACHINE)):
            controller._probe_encoders()
        self.assertEqual(controller.encoders, [])
        self.assertFalse(
            controller._probed,
            "ffmpeg may turn up later - `start_recording` looks for it again - and the "
            "probe has to be allowed to run then",
        )

    def test_a_machine_where_nothing_works_still_opens(self) -> None:
        """The warning stays, and nothing raises: the program is up and usable."""
        controller = self.controller()
        with mock.patch.object(capabilities, "probe", self._probe_returning(NOTHING)):
            controller._probe_encoders()
        self.assertTrue(controller._probed)
        shown = self.bar.status_label.text()
        self.assertIn("test frame", shown)
        plan = controller._plan_for(Path("take_001.mp4"))
        self.assertIsNotNone(plan, "the planner must still answer, even hopelessly")
        self.assertEqual(
            plan.encoder, capabilities.LAST_RESORT,
            "recording will fail, and it fails on the one encoder every ffmpeg build "
            "has rather than on nothing at all",
        )


class RecordBeforeTheProbeTest(ControllerTestCase):
    """The record button is live while the probe runs, because the queue is pumped."""

    def test_pressing_record_mid_probe_is_refused_rather_than_planned(self) -> None:
        controller = self.controller()
        controller._probing = True
        controller.start_recording()
        self.assertIn("one moment", self.bar.status_label.text())
        self.assertEqual(
            controller.picker_opened, 0, "it must not get as far as planning anything"
        )

    def test_pressing_record_before_the_probe_ran_probes_first(self) -> None:
        controller = self.controller()
        controller.monitors = []  # so start_recording stops at "choose a source"
        calls = []

        def fake(ffmpeg, use_cache=True, on_step=None):
            calls.append(ffmpeg)
            return list(THIS_MACHINE)

        with mock.patch.object(capabilities, "probe", fake):
            controller.start_recording()
        self.assertEqual(
            len(calls), 1,
            "the hotkey can arrive before the event loop has run the probe, and a "
            "recording planned from no answer records on the processor",
        )


# ------------------------------------------------------------ what it decides


class PlanEncoderTest(ControllerTestCase):
    """`capabilities.choose` is the decision, and the plan is what it said."""

    def _plan(self, controller, name="take_001.mp4"):
        plan = controller._plan_for(Path(name))
        self.assertIsNotNone(plan)
        return plan

    def test_every_profile_on_every_machine_is_what_choose_said(self) -> None:
        for key, profile in config.PROFILES.items():
            for label, found in (
                ("this machine", THIS_MACHINE), ("windows only", ONLY_WINDOWS),
                ("cpu only", ONLY_CPU), ("nothing", NOTHING),
            ):
                with self.subTest(profile=key, machine=label):
                    controller = self.controller(found=found, profile=key)
                    expected, _reason = capabilities.choose(found, profile)
                    self.assertEqual(self._plan(controller).encoder, expected)

    def test_the_default_profile_records_on_nvenc_here(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self.assertEqual(self._plan(controller).encoder, "hevc_nvenc")

    def test_a_machine_with_no_vendor_family_records_through_windows(self) -> None:
        controller = self.controller(found=ONLY_WINDOWS)
        self.assertEqual(self._plan(controller).encoder, "hevc_mf")

    def test_the_x264_profile_is_no_longer_recorded_on_the_gpu(self) -> None:
        """The defect this replaced: "No GPU - x264" recorded on NVENC."""
        controller = self.controller(found=THIS_MACHINE, profile="x264")
        self.assertEqual(self._plan(controller).encoder, "libx264")

    def test_the_444_profile_keeps_444_on_a_machine_with_no_nvenc(self) -> None:
        controller = self.controller(found=ONLY_WINDOWS, profile="hevc444")
        plan = self._plan(controller)
        self.assertTrue(
            encoders.supports_444(plan.encoder),
            "Media Foundation lists yuv444p and will not encode it; the filter chain "
            "would quietly rewrite it to nv12 and the recording would lose the colour "
            "samples the profile exists for",
        )
        self.assertEqual(plan.pix_fmt, "yuv444p")

    def test_the_444_profiles_rext_argument_does_not_follow_it_to_x264(self) -> None:
        """Measured 2026-11-04 on the bundled build: `libx264 -profile:v rext` is
        `x264 [error]: invalid profile: rext` and exit 127 - the encoder never opens."""
        controller = self.controller(found=ONLY_CPU, profile="hevc444")
        plan = self._plan(controller)
        self.assertEqual(plan.encoder, "libx264")
        self.assertEqual(plan.encoder_extra, ())

    def test_the_444_profile_keeps_its_argument_where_it_belongs(self) -> None:
        controller = self.controller(found=THIS_MACHINE, profile="hevc444")
        plan = self._plan(controller)
        self.assertEqual(plan.encoder, "hevc_nvenc")
        self.assertEqual(plan.encoder_extra, config.PROFILES["hevc444"].extra)

    def test_a_named_encoder_is_used_as_named(self) -> None:
        """`encoder_choice` has no control in the interface yet; when it gets one, a
        choice made there is a choice, not a suggestion to the chooser."""
        controller = self.controller(found=THIS_MACHINE, encoder_choice="h264_nvenc")
        self.assertEqual(self._plan(controller).encoder, "h264_nvenc")

    def test_both_planners_agree(self) -> None:
        """The multi-screen planner carried its own copy of the override, and the two
        copies had already drifted apart by one `extra`."""
        for key in config.PROFILES:
            with self.subTest(profile=key):
                one = self.controller(found=ONLY_WINDOWS, profile=key)
                many = self.controller(found=ONLY_WINDOWS, profile=key)
                single = one._plan_for(Path("take_001.mp4"))
                source = many._resolve_source()
                per_screen = many._plan_for_source(
                    source, Path("take_001_screen1.mp4"), with_audio=True
                )
                self.assertEqual(single.encoder, per_screen.encoder)
                self.assertEqual(single.encoder_extra, per_screen.encoder_extra)

    def test_no_override_block_is_left_anywhere(self) -> None:
        for method in (app_module.Application._plan_for,
                       app_module.Application._plan_for_source):
            with self.subTest(method=method.__name__):
                source = inspect.getsource(method)
                self.assertNotIn("capabilities.best", source)
                self.assertNotIn("yuv420p", source)
                # `(profile` and not `(profile)`: since 2026-11-05 both planners hand
                # the source over too, so that a frame below the chosen encoder's
                # floor can be moved - see `SmallFrameTest`.
                self.assertIn("self._encoder_for(profile", source)


class EncoderNoticeTest(ControllerTestCase):
    """The reason reaches the user when the encoder is not the one the label promised."""

    def test_a_different_codec_is_said_out_loud(self) -> None:
        controller = self.controller(found=ONLY_CPU)  # an H.265 profile, an H.264 encoder
        controller._plan_for(Path("take_001.mp4"))
        shown = self.bar.status_label.text()
        self.assertIn("libx264", shown)
        self.assertIn(
            "did not work here", shown,
            "the reason `choose` gives is the half that says why, and it is the half "
            "worth reading",
        )

    def test_the_expected_answer_is_not_announced(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        controller._plan_for(Path("take_001.mp4"))
        self.assertEqual(
            self.bar.status_label.text(), "",
            "`hevc_nvenc` for the H.265 profile is what the label says; a line before "
            "every recording saying so is noise",
        )

    def test_the_software_profile_is_not_announced_either(self) -> None:
        """It asked for the processor. Being given it is not news."""
        controller = self.controller(found=THIS_MACHINE, profile="x264")
        controller._plan_for(Path("take_001.mp4"))
        self.assertEqual(self.bar.status_label.text(), "")

    def test_a_hardware_profile_that_lands_on_the_processor_is_said(self) -> None:
        controller = self.controller(found=ONLY_CPU, profile="h264")
        controller._plan_for(Path("take_001.mp4"))
        self.assertIn("libx264", self.bar.status_label.text())

    def test_it_is_said_once_per_take_not_once_per_segment(self) -> None:
        """`_plan_for` is the segment factory: pause and resume call it again."""
        controller = self.controller(found=ONLY_CPU)
        controller._plan_for(Path("take_001.mp4"))
        self.assertNotEqual(self.bar.status_label.text(), "")
        controller._say("recording to take_001.mp4")
        controller._plan_for(Path("take_002.mp4"))
        self.assertEqual(
            self.bar.status_label.text(), "recording to take_001.mp4",
            "the same sentence for every segment would push the recording's own line "
            "off the bar each time it rolled",
        )

    def test_a_fresh_probe_lets_it_speak_again(self) -> None:
        controller = self.controller(found=ONLY_CPU)
        controller._plan_for(Path("take_001.mp4"))
        controller._probed = False
        with mock.patch.object(
            capabilities, "probe",
            lambda ffmpeg, use_cache=True, on_step=None: list(ONLY_CPU),
        ):
            controller._probe_encoders()
        controller._say("")
        controller._plan_for(Path("take_002.mp4"))
        self.assertIn("libx264", self.bar.status_label.text())


class PanelIsToldTest(ControllerTestCase):
    """The settings panel learns about this machine through exactly one call.

    Until 2026-11-05 nothing in `app/` called `SettingsPanel.set_encoders` at all.
    `_encoders_known` therefore stayed False for the life of the process, which the
    panel handles correctly and quietly: no path sentence, no Details expander, and -
    the one that matters - every format still offered, so "Sharp text - H.265 4:4:4"
    was on offer on a machine whose every working encoder refuses 4:4:4.
    """

    #: Media Foundation and the processor: nothing here takes 4:4:4, and `libx264` is
    #: deliberately absent, because it does. Invented - this machine has an RTX 3070 Ti
    #: and no AMD or Intel graphics.
    NO_444 = _machine("hevc_mf", "h264_mf")

    def _open(self, controller):
        controller.open_settings()
        self.addCleanup(controller.panel.deleteLater)
        return controller.panel

    def test_opening_the_panel_hands_it_what_the_probe_found(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        panel = self._open(controller)
        self.assertEqual(
            [row.name for row in panel.encoders()],
            [row.name for row in THIS_MACHINE],
        )

    def test_a_panel_opened_before_the_answer_is_told_when_it_arrives(self) -> None:
        """The probe is scheduled from the event loop and the gear is live while it
        runs, so the panel can exist before there is anything to put in it."""
        controller = self.controller()
        panel = self._open(controller)
        self.assertEqual(panel.encoders(), [])
        with mock.patch.object(
            capabilities, "probe",
            lambda ffmpeg, use_cache=True, on_step=None: list(THIS_MACHINE),
        ):
            controller._probe_encoders()
        self.assertEqual(len(panel.encoders()), len(THIS_MACHINE))

    def test_a_panel_opened_mid_probe_is_told_nothing_yet(self) -> None:
        """An empty list is not "nothing known yet" to the panel - it is "the probe
        ran and nothing works", and the gear is live while the probe runs because
        `_probe_step` pumps the queue."""
        controller = self.controller()
        controller._probing = True
        panel = self._open(controller)
        self.assertFalse(panel._encoders_known)
        self.assertEqual(panel.encoding_note.text(), "")
        for key in config.PROFILES:
            index = panel.profile_combo.findData(key)
            with self.subTest(key=key):
                self.assertTrue(panel.profile_combo.model().item(index).isEnabled())

    def test_444_is_greyed_out_on_a_machine_that_cannot_encode_it(self) -> None:
        """The whole point of the wiring: this is what the panel does once told."""
        controller = self.controller(found=self.NO_444)
        panel = self._open(controller)
        index = panel.profile_combo.findData("hevc444")
        item = panel.profile_combo.model().item(index)
        self.assertFalse(
            item.isEnabled(),
            "offering 4:4:4 here is worse than hiding it: `encoders.frame_path` "
            "rewrites yuv444p to nv12 for the mf family, so the command runs, the "
            "file plays, and the colour samples the format exists for are gone",
        )

    def test_the_panel_says_which_path_is_in_force_once_it_is_open(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        panel = self._open(controller)
        self.assertTrue(panel.encoding_note.isVisibleTo(panel))
        self.assertIn("graphics card", panel.encoding_note.text())


class ProbeWarningTest(ControllerTestCase):
    """Which machines are told that recording will not work."""

    def _probe_returning(self, found):
        def fake(ffmpeg, use_cache=True, on_step=None):
            return list(found)
        return fake

    def test_a_machine_that_lists_everything_and_runs_none_is_warned(self) -> None:
        controller = self.controller()
        with mock.patch.object(capabilities, "probe", self._probe_returning(NOTHING)):
            controller._probe_encoders()
        self.assertIn("test frame", self.bar.status_label.text())

    def test_a_machine_whose_ffmpeg_lists_nothing_is_warned_too(self) -> None:
        """`rejected` counts only the names ffmpeg listed. `listed_encoders` returns an
        empty set on any `OSError` or `SubprocessError` and never looks at the return
        code, so a broken or wrong ffmpeg build produces this shape - and the old
        condition, `rejected and not working`, said nothing at all about it."""
        nothing_listed = [
            capabilities.EncoderResult(
                name=name, codec=codec, vendor=vendor, listed=False, works=False,
                reason="this ffmpeg was not built with it", millis=0,
            )
            for name, codec, vendor in capabilities.CANDIDATES
        ]
        controller = self.controller()
        controller._say("the settings file could not be read", warn=True)
        with mock.patch.object(
            capabilities, "probe", self._probe_returning(nothing_listed)
        ):
            controller._probe_encoders()
        shown = self.bar.status_label.text()
        self.assertIn("ffmpeg", shown)
        self.assertNotEqual(
            shown, "the settings file could not be read",
            "the line was restored as if the machine were healthy, and every "
            "recording then runs libx264, which on that machine is equally absent",
        )

    def test_a_machine_that_works_is_told_nothing(self) -> None:
        controller = self.controller()
        with mock.patch.object(
            capabilities, "probe", self._probe_returning(THIS_MACHINE)
        ):
            controller._probe_encoders()
        self.assertEqual(self.bar.status_label.text(), "")


class QuitDuringTheProbeTest(ControllerTestCase):
    """Quit clicked while the probe holds the thread must still quit.

    `_probe_step` pumps the queue with `QApplication.processEvents()`, so the click is
    delivered inside a nested event loop and `QApplication.quit()` exits *that* loop.
    Measured on 2026-11-05, offscreen, with a slot that pumps the queue between 0.25 s
    steps: the quit arrived at 0.250 s, the slot ended at 1.001 s, and `exec()` was
    still running 2.0 s after the quit. Held back and repeated after the slot, the same
    harness left `exec()` at 1.001 s.
    """

    def test_a_quit_asked_for_during_the_probe_is_not_made_there(self) -> None:
        controller = self.controller()
        controller._probing = True
        with mock.patch.object(QtWidgets.QApplication, "quit") as quit_call:
            controller.quit()
        quit_call.assert_not_called()
        self.assertTrue(controller._quit_wanted)
        self.assertIn("Closing", self.bar.status_label.text())

    def test_it_is_made_as_soon_as_the_probe_lets_go(self) -> None:
        controller = self.controller()

        def fake(ffmpeg, use_cache=True, on_step=None):
            controller.quit()  # the click, delivered by `_probe_step`'s processEvents
            return list(THIS_MACHINE)

        with mock.patch.object(QtWidgets.QApplication, "quit") as quit_call:
            with mock.patch.object(capabilities, "probe", fake):
                controller._probe_encoders()
        quit_call.assert_called_once_with()
        self.assertFalse(controller._quit_wanted)

    def test_a_quit_outside_the_probe_goes_straight_through(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        with mock.patch.object(QtWidgets.QApplication, "quit") as quit_call:
            controller.quit()
        quit_call.assert_called_once_with()


class ManualEncoderChoiceTest(ControllerTestCase):
    """`Settings.encoder_choice` is read the same way by the panel and the planner."""

    def test_a_named_encoder_does_not_carry_another_codec_s_argument(self) -> None:
        """Measured on the bundled build: `libx264 -crf 22 -pix_fmt yuv444p
        -profile:v rext` is `x264 [error]: invalid profile: rext` and exit 127. The
        automatic path stopped doing this on 2026-11-04; the manual one did not."""
        controller = self.controller(
            found=THIS_MACHINE, profile="hevc444", encoder_choice="libx264"
        )
        plan = controller._plan_for(Path("take_001.mp4"))
        self.assertEqual(plan.encoder, "libx264")
        self.assertEqual(plan.encoder_extra, ())

    def test_a_named_encoder_of_the_right_codec_keeps_it(self) -> None:
        controller = self.controller(
            found=THIS_MACHINE, profile="hevc444", encoder_choice="hevc_nvenc"
        )
        plan = controller._plan_for(Path("take_001.mp4"))
        self.assertEqual(plan.encoder_extra, config.PROFILES["hevc444"].extra)

    def test_a_name_nobody_has_measured_falls_back_to_the_probe(self) -> None:
        """`encoders.codec_args` raises `UnknownEncoder` on it at record time, which
        reaches the user as a raw English exception on the bar - and the panel has
        always described `choose`'s answer instead, so the two disagreed."""
        for wanted in ("nvenc", "NVENC", "hevc_nvenc ", "hevc_videotoolbox", ""):
            with self.subTest(wanted=wanted):
                controller = self.controller(
                    found=THIS_MACHINE, encoder_choice=wanted
                )
                expected, _reason = capabilities.choose(
                    THIS_MACHINE, controller.settings.encoding()
                )
                self.assertEqual(
                    controller._plan_for(Path("take_001.mp4")).encoder, expected
                )

    def test_the_panel_and_the_planner_read_the_field_the_same_way(self) -> None:
        for wanted in ("auto", "", "libx264", "h264_mf", "banana", "hevc_nvenc "):
            for key in config.PROFILES:
                with self.subTest(wanted=wanted, profile=key):
                    controller = self.controller(
                        found=THIS_MACHINE, profile=key, encoder_choice=wanted
                    )
                    panel = app_module.SettingsPanel(controller.settings)
                    self.addCleanup(panel.deleteLater)
                    panel.set_encoders(THIS_MACHINE)
                    profile = config.PROFILES[key]
                    self.assertEqual(
                        controller._plan_for(Path("take_001.mp4")).encoder,
                        panel._chosen_encoder(profile),
                    )


class SizeEstimateTest(ControllerTestCase):
    """The free-space check reads this machine's own figure where there is one.

    `config.estimate_mb_per_minute` is the `config.PROFILES` table, measured on this
    machine's NVENC. `docs/ENCODERS.md` §3 puts a Media Foundation recording at up to
    4.9x it, so on that machine the check was reading somebody else's number.
    """

    def test_the_estimate_is_asked_for_with_the_encoder_it_will_record_with(self) -> None:
        controller = self.controller(found=ONLY_CPU)
        seen: dict = {}

        def fake(profile_key, level, width, height, framerate, encoder="", store=None):
            seen.update(profile_key=profile_key, level=level, width=width,
                        height=height, framerate=framerate, encoder=encoder)
            return storage.Estimate(99.0, False)

        with mock.patch.object(storage, "size_estimate", fake):
            answer = controller._estimate()
        self.assertEqual(answer.mb_per_minute, 99.0)
        self.assertEqual(seen["encoder"], "libx264")
        self.assertEqual(seen["profile_key"], controller.settings.profile)
        self.assertEqual((seen["width"], seen["height"]), (2560, 1440))

    def test_a_hand_written_encoder_choice_is_what_it_asks_about(self) -> None:
        controller = self.controller(found=THIS_MACHINE, encoder_choice="h264_mf")
        seen: dict = {}

        def fake(profile_key, level, width, height, framerate, encoder="", store=None):
            seen["encoder"] = encoder
            return storage.Estimate(1.0, False)

        with mock.patch.object(storage, "size_estimate", fake):
            controller._estimate()
        self.assertEqual(seen["encoder"], "h264_mf")

    def test_asking_for_the_estimate_says_nothing_on_the_bar(self) -> None:
        """`_watch_space` asks several times a second while a recording runs."""
        controller = self.controller(found=ONLY_CPU)
        controller._say("")
        controller._estimate()
        self.assertEqual(self.bar.status_label.text(), "")

    def test_the_table_is_no_longer_read_directly(self) -> None:
        for method in (app_module.Application.start_recording,
                       app_module.Application._watch_space):
            with self.subTest(method=method.__name__):
                self.assertNotIn(
                    "estimate_mb_per_minute", inspect.getsource(method),
                    "`storage.size_estimate` falls back to that table itself, and "
                    "replaces it once this machine has recorded something",
                )


class EncoderNoteSurvivesTheStartTest(ControllerTestCase):
    """The start's own line used to run over the encoder's one statement later.

    `Bar.set_status` replaces the label with no dwell and no queue, and `_really_start`
    says "Recording to ..." immediately after `session.start()` has planned the take.
    So on every machine that lands on an encoder its format never asked for - the AMD
    and Intel machines nobody here owns among them - the one sentence that said so was
    on screen for the length of one function call.
    """

    class _FakeSession:
        active = True

        def __init__(self, ffmpeg, final, plan_for) -> None:
            self._plan_for = plan_for
            self.final = final

        def start(self, on_progress=None, loopback_device=-1) -> str:
            self._plan_for(Path("take_001.mp4"))
            return ""

    def _start(self, controller) -> None:
        with mock.patch.object(recorder, "RecordingSession", self._FakeSession):
            controller._really_start()

    def test_the_encoder_s_line_is_what_is_left_on_the_bar(self) -> None:
        controller = self.controller(found=ONLY_CPU, profile="h264")
        self._start(controller)
        shown = self.bar.status_label.text()
        self.assertIn("libx264", shown)
        self.assertIn("did not work here", shown)

    def test_an_unsurprising_answer_leaves_the_file_name_up(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self._start(controller)
        self.assertIn("Recording to", self.bar.status_label.text())

    def test_the_note_is_not_held_over_to_the_next_take(self) -> None:
        controller = self.controller(found=ONLY_CPU, profile="h264")
        self._start(controller)
        controller.session = None
        self._start(controller)
        self.assertIn(
            "Recording to", self.bar.status_label.text(),
            "the sentence is said once; holding it back and replaying it on a later "
            "take would be the same noise `_note_encoder` exists to avoid",
        )

    def test_both_starts_flush_it(self) -> None:
        for method in (app_module.Application._really_start,
                       app_module.Application._start_all_screens):
            with self.subTest(method=method.__name__):
                source = inspect.getsource(method)
                self.assertIn("self._flush_encoder_note()", source)
                self.assertLess(
                    source.index("self._say("),
                    source.index("self._flush_encoder_note()"),
                    "the flush has to come after the line that would run over it",
                )


class PipeNameTest(ControllerTestCase):
    r"""One named pipe per recording, not one per build of the program.

    `ui/app.py:_plan_for` built the system-sound pipe's name out of
    `segment.stem[-6:]`, and every take's first segment is `....part00`. Reproduced on
    2026-11-05 against the shipped code by planning two takes five minutes apart in one
    process: both asked for `\\.\pipe\screenrec_<pid>_part00`, and the all-screens
    planner's `[-8:]` gave `0.part00` for both. The handle leak on the other side of
    that pipe belongs elsewhere; the collision is here, and it is by design rather than
    by accident.
    """

    class _FakeSession:
        active = True

        def __init__(self, ffmpeg, final, plan_for) -> None:
            self._plan_for = plan_for
            self.final = final

        def start(self, on_progress=None, loopback_device=-1) -> str:
            return ""

    def _take(self, controller) -> str:
        """Start a recording and return the tag it will name its pipes with."""
        with mock.patch.object(recorder, "RecordingSession", self._FakeSession):
            controller._really_start()
        controller.session = None
        return controller._take_tag

    def _pipe(self, controller, name: str) -> str:
        plan = controller._plan_for(Path(name))
        self.assertIsNotNone(plan)
        return plan.loopback_pipe

    def test_two_takes_in_one_session_do_not_share_a_pipe(self) -> None:
        controller = self.controller(found=THIS_MACHINE, system_audio_enabled=True)
        self._take(controller)
        first = self._pipe(controller, "ScreenRec_2026-11-05_10-00-00.part00.mkv")
        self._take(controller)
        second = self._pipe(controller, "ScreenRec_2026-11-05_10-05-30.part00.mkv")
        self.assertNotEqual(
            first, second,
            "the first segment of every take is .part00, so a name built from it is "
            "the same name for every recording this program ever makes",
        )

    def test_two_takes_inside_one_second_do_not_share_one_either(self) -> None:
        """The clock alone is not enough - `_take_tag` carries a counter as well."""
        controller = self.controller(found=THIS_MACHINE, system_audio_enabled=True)
        fixed = datetime(2026, 11, 5, 10, 0, 0)
        with mock.patch.object(app_module, "datetime") as clock:
            clock.now.return_value = fixed
            first_tag = self._take(controller)
            second_tag = self._take(controller)
        self.assertNotEqual(first_tag, second_tag)

    def test_the_segments_of_one_take_still_differ(self) -> None:
        """Within a take the part number stays in the name, which is what it was for."""
        controller = self.controller(found=THIS_MACHINE, system_audio_enabled=True)
        self._take(controller)
        names = {
            self._pipe(controller, f"ScreenRec_2026-11-05_10-00-00.part{n:02d}.mkv")
            for n in range(3)
        }
        self.assertEqual(len(names), 3)

    def test_the_all_screens_planner_follows_the_same_rule(self) -> None:
        controller = self.controller(found=THIS_MACHINE, system_audio_enabled=True)
        source = controller._resolve_source()
        seen = []
        for name in ("ScreenRec_2026-11-05_10-00-00_screen0.part00.mkv",
                     "ScreenRec_2026-11-05_10-05-30_screen0.part00.mkv"):
            self._take(controller)
            plan = controller._plan_for_source(source, Path(name), with_audio=True)
            seen.append(plan.loopback_pipe)
        self.assertNotEqual(seen[0], seen[1])

    def test_no_pipe_at_all_without_system_sound(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self._take(controller)
        self.assertEqual(self._pipe(controller, "take.part00.mkv"), "")


class SmallFrameTest(ControllerTestCase):
    """A rectangle NVENC refuses is recorded, not failed.

    Measured on this machine on 2026-11-05 through the real `ddagrab -> encoder`
    pipeline, at the boundary and twice each: `h264_nvenc` and `h264_mf` encode 146x720
    and 720x50 and refuse 144x720 and 720x48; `hevc_nvenc` and `hevc_mf` encode 130x720
    and 720x34 and refuse 128x720 and 720x32; `libx264` encoded every shape tried, down
    to 2x2. Below the floor ffmpeg exits non-zero having written nothing, which is what
    a user met after pressing record on a rectangle the selector let them drag.
    """

    def _region(self, width: int, height: int, **settings) -> _Controller:
        found = settings.pop("found", THIS_MACHINE)
        return self.controller(
            found=found, source_kind=config.SOURCE_REGION,
            region=[100, 100, width, height], **settings
        )

    def test_the_floors_are_the_ones_that_were_measured(self) -> None:
        self.assertEqual(config.MIN_FRAME["h264_nvenc"], (146, 50))
        self.assertEqual(config.MIN_FRAME["hevc_nvenc"], (130, 34))
        self.assertEqual(config.MIN_FRAME["h264_mf"], (146, 50))
        self.assertEqual(config.MIN_FRAME["hevc_mf"], (130, 34))
        self.assertEqual(config.MIN_FRAME["libx264"], (2, 2))

    def test_an_unmeasured_encoder_gets_the_largest_floor_anybody_measured(self) -> None:
        """No AMD and no Intel graphics on this desk, so their floors are unknown -
        and ignorance has to degrade, not crash. See `docs/ENCODERS.md` §1."""
        self.assertEqual(config.min_frame("h264_amf"), config.UNMEASURED_MIN_FRAME)
        self.assertEqual(config.min_frame("hevc_qsv"), config.UNMEASURED_MIN_FRAME)
        self.assertEqual(
            config.UNMEASURED_MIN_FRAME, max(config.MIN_FRAME.values())
        )

    def test_a_rectangle_under_the_floor_is_recorded_on_the_processor(self) -> None:
        """120x90, which the audit found failing on both NVENC encoders."""
        controller = self._region(120, 90)
        plan = controller._plan_for(Path("take_001.mp4"))
        self.assertEqual(
            plan.encoder, "libx264",
            "hevc_nvenc refuses anything under 130x34; recording it there is a "
            "recording that fails after the user has pressed record",
        )

    def test_a_rectangle_on_the_floor_keeps_the_hardware_encoder(self) -> None:
        controller = self._region(130, 34)
        self.assertEqual(
            controller._plan_for(Path("take_001.mp4")).encoder, "hevc_nvenc"
        )

    def test_one_pixel_under_either_floor_is_enough(self) -> None:
        for width, height in ((128, 34), (130, 32)):
            with self.subTest(size=(width, height)):
                controller = self._region(width, height)
                self.assertEqual(
                    controller._plan_for(Path("take_001.mp4")).encoder, "libx264"
                )

    def test_the_h264_profile_has_its_own_higher_floor(self) -> None:
        """146x50 for h264_nvenc, against 130x34 for hevc_nvenc - both measured."""
        at_the_hevc_floor = self._region(130, 34, profile="h264")
        self.assertEqual(
            at_the_hevc_floor._plan_for(Path("t.mp4")).encoder, "libx264"
        )
        at_its_own = self._region(146, 50, profile="h264")
        self.assertEqual(
            at_its_own._plan_for(Path("t.mp4")).encoder, "h264_nvenc"
        )

    def test_a_whole_screen_is_left_alone(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self.assertEqual(
            controller._plan_for(Path("take_001.mp4")).encoder, "hevc_nvenc"
        )

    def test_the_user_is_told_which_encoder_it_went_to(self) -> None:
        controller = self._region(120, 90)
        controller._plan_for(Path("take_001.mp4"))
        shown = self.bar.status_label.text()
        self.assertIn("hevc_nvenc", shown)
        self.assertIn("processor", shown)

    def test_it_is_said_once_per_take_and_not_once_per_segment(self) -> None:
        controller = self._region(120, 90)
        controller._plan_for(Path("take_001.part00.mkv"))
        controller._say(lambda: "something else")
        controller._plan_for(Path("take_001.part01.mkv"))
        self.assertEqual(self.bar.status_label.text(), "something else")

    def test_the_next_take_is_told_again(self) -> None:
        """"Once per take" means the take after it hears it too.

        Reproduced 2026-11-05: `_note_small_frame` shared `_note_encoder`'s guard,
        which is cleared only in `__init__` and after the probe, so it held for the
        whole run of the program. With the same 100x60 region recorded twice in one
        process, take one said "100x60 is smaller than hevc_nvenc will encode -
        recording it on the processor instead." and take two said nothing at all,
        while being just as much on the processor.
        """
        controller = self._region(100, 60)
        with mock.patch.object(
            recorder, "RecordingSession", EncoderNoteSurvivesTheStartTest._FakeSession
        ):
            controller._really_start()
            first = self.bar.status_label.text()
            controller.session = None
            controller._really_start()
            second = self.bar.status_label.text()
        self.assertIn("processor", first)
        self.assertIn("processor", second)

    def test_it_survives_the_start_s_own_line(self) -> None:
        """The same `_pending_note` idiom the encoder notice uses, for the same reason."""
        controller = self._region(120, 90)
        controller._plan_for(Path("take_001.part00.mkv"))
        self.assertIsNotNone(controller._pending_note)
        controller._say(lambda: "Recording to take_001.mp4")
        controller._flush_encoder_note()
        self.assertIn("processor", self.bar.status_label.text())

    def test_a_machine_with_no_processor_encoder_is_told_so(self) -> None:
        """NVENC works and `libx264` does not - a build without it, or one that could
        not run it. There is nowhere to move the frame to, so the only thing left is
        to say so before the user presses record."""
        no_cpu = _machine("hevc_nvenc", "h264_nvenc")
        controller = self._region(120, 90, found=no_cpu)
        plan = controller._plan_for(Path("take_001.mp4"))
        self.assertIn("Choose a bigger region", self.bar.status_label.text())
        self.assertEqual(
            plan.encoder,
            capabilities.choose(no_cpu, controller.settings.encoding())[0],
            "there is nothing to move it to, so the answer is unchanged and the "
            "sentence is all this can do",
        )

    def test_the_all_screens_planner_asks_the_same_question(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        source = controller._resolve_source()
        plan = controller._plan_for_source(
            source, Path("take_screen0.part00.mkv"), with_audio=True
        )
        self.assertEqual(plan.encoder, "hevc_nvenc", "a whole screen is never small")

    def test_a_region_that_is_not_a_rectangle_does_not_raise_here(self) -> None:
        """`output_size()` raises on one; planning must not be what the user meets."""
        controller = self.controller(found=THIS_MACHINE)
        bad = recorder.VideoSource(monitor=LANDSCAPE, crop=(100, 100, 0, 0), name="r")
        name, _extra = controller._encoder_for(
            controller.settings.encoding(), bad
        )
        self.assertEqual(name, "hevc_nvenc")


class MalformedRegionTest(ControllerTestCase):
    """A `region` of four integers that enclose nothing used to kill two buttons.

    Reproduced on 2026-11-05 against the shipped code with
    `{"source_kind": "region", "region": [100, 100, 0, 0]}` in the settings file: it
    loaded with no complaint at all, and then both `open_settings` and
    `start_recording` raised `ValueError: The rectangle 100,100 0x0 does not overlap
    monitor 0` out of `recorder.surface_crop`. `ScreenRec.cmd` runs the program under
    `pythonw.exe`, so the traceback has no console to reach: the gear and the record
    button simply stopped doing anything.
    """

    BAD = ([100, 100, 0, 0], [100, 100, -400, -300], [100, 100, 400, 0])

    def test_the_gear_opens(self) -> None:
        for region in self.BAD:
            with self.subTest(region=region):
                controller = self.controller(
                    found=THIS_MACHINE, source_kind=config.SOURCE_REGION,
                    region=list(region),
                )
                controller.open_settings()  # must not raise

    def test_the_record_button_answers(self) -> None:
        for region in self.BAD:
            with self.subTest(region=region):
                controller = self.controller(
                    found=THIS_MACHINE, source_kind=config.SOURCE_REGION,
                    region=list(region),
                )
                controller.start_recording()  # must not raise
                self.assertEqual(controller.picker_opened, 1)
                self.assertIn(
                    "Choose something to record first.",
                    self.bar.status_label.text(),
                )

    def test_the_chip_does_not_claim_a_region(self) -> None:
        controller = self.controller(
            found=THIS_MACHINE, source_kind=config.SOURCE_REGION,
            region=[100, 100, 0, 0],
        )
        _icon, label = controller._source_label()
        self.assertEqual(
            label, "Choose a region",
            '"Region 0×0" told the user they had chosen something they had not',
        )

    def test_a_real_region_is_still_a_region(self) -> None:
        controller = self.controller(
            found=THIS_MACHINE, source_kind=config.SOURCE_REGION,
            region=[100, 100, 400, 300],
        )
        _icon, label = controller._source_label()
        self.assertEqual(label, "Region 400×300")
        self.assertIsNotNone(controller._resolve_source())

    def test_the_picker_cannot_store_one_either(self) -> None:
        controller = self.controller(
            found=THIS_MACHINE, source_kind=config.SOURCE_REGION,
            region=[100, 100, 400, 300],
        )
        controller._source_chosen(config.SOURCE_REGION, (10, 10, 0, 0))
        self.assertEqual(controller.settings.region, [100, 100, 400, 300])


class AutoStopTest(ControllerTestCase):
    """"Stop after N minutes" means N minutes of recording, on any number of screens."""

    class _FakeMulti:
        """A take that stops recording when it is paused, as both real ones do.

        `recording` was a class attribute that stayed `True` through a pause, which
        neither `RecordingSession.recording` (`self.recorder is not None and
        .running`) nor `MultiSession.recording` (any screen's session recording) can
        be: measured 2026-11-05 on a real `RecordingSession`, a `pause()` that
        reports "ffmpeg exited with code 1" still leaves `recording=False`.
        """

        active = True

        def __init__(self) -> None:
            self.paused = False
            self.recording = True
            self.notes: list = []

        def pause(self) -> str:
            self.paused = True
            self.recording = False
            return ""

        def resume(self) -> str:
            self.paused = False
            self.recording = True
            return ""

    class _FakeSession(_FakeMulti):
        """One screen. The controller asks it exactly the same four questions."""

    def _stopping(self, controller) -> list:
        stopped: list = []
        controller.stop_recording = lambda: stopped.append(True)
        return stopped

    def test_it_stops_a_recording_of_every_screen(self) -> None:
        """Reproduced 2026-11-05: `_auto_stop` asked only about `self.session`, while
        `_start_all_screens` starts the very same timer - so the timeout arrived and
        `stop_recording` was called 0 times."""
        controller = self.controller(found=THIS_MACHINE, auto_stop_minutes=1)
        controller.multi = self._FakeMulti()
        stopped = self._stopping(controller)
        controller._auto_stop()
        self.assertEqual(len(stopped), 1)

    def test_it_still_stops_a_single_screen_recording(self) -> None:
        controller = self.controller(found=THIS_MACHINE, auto_stop_minutes=1)
        controller.session = self._FakeSession()
        stopped = self._stopping(controller)
        controller._auto_stop()
        self.assertEqual(len(stopped), 1)

    def test_it_does_nothing_when_nothing_is_recording(self) -> None:
        controller = self.controller(found=THIS_MACHINE, auto_stop_minutes=1)
        stopped = self._stopping(controller)
        controller._auto_stop()
        self.assertEqual(stopped, [])

    def test_it_says_why_it_stopped(self) -> None:
        controller = self.controller(found=THIS_MACHINE, auto_stop_minutes=7)
        controller.multi = self._FakeMulti()
        self._stopping(controller)
        controller._auto_stop()
        self.assertIn("7", self.bar.status_label.text())

    def test_the_pause_stops_the_clock(self) -> None:
        """Reproduced 2026-11-05: the word `auto_stop_timer` did not appear in
        `toggle_pause`, so the limit counted the pause as recording."""
        for live in ("session", "multi"):
            with self.subTest(recording=live):
                controller = self.controller(
                    found=THIS_MACHINE, auto_stop_minutes=30
                )
                setattr(controller, live,
                        self._FakeMulti() if live == "multi" else self._FakeSession())
                controller.auto_stop_timer.start(30 * 60_000)
                controller.toggle_pause()
                self.assertFalse(controller.auto_stop_timer.isActive())
                self.assertGreater(controller._auto_stop_left, 0)

    def test_the_resume_gives_back_what_was_owed_and_not_the_whole_limit(self) -> None:
        controller = self.controller(found=THIS_MACHINE, auto_stop_minutes=30)
        controller.session = self._FakeSession()
        controller.auto_stop_timer.start(30 * 60_000)
        controller._auto_stop_left = 0
        # Ten of the thirty minutes already recorded, then a pause.
        controller.auto_stop_timer.start(20 * 60_000)
        controller.toggle_pause()
        owed = controller._auto_stop_left
        controller.toggle_pause()
        self.assertTrue(controller.auto_stop_timer.isActive())
        self.assertLessEqual(controller.auto_stop_timer.remainingTime(), owed)
        self.assertGreater(
            controller.auto_stop_timer.remainingTime(), 19 * 60_000,
            "the pause must not cost the recording any of its minutes",
        )
        self.assertLess(
            controller.auto_stop_timer.remainingTime(), 30 * 60_000,
            "nor hand it back the ten minutes it had already used",
        )

    def test_a_take_with_no_limit_is_not_given_one_by_a_pause(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        controller.session = self._FakeSession()
        controller.toggle_pause()
        controller.toggle_pause()
        self.assertFalse(controller.auto_stop_timer.isActive())


class PauseButtonTest(ControllerTestCase):
    """The clock, the bar and the tray must agree with the take, not with the press.

    Reproduced on 2026-11-05 against the real classes, which is where the two facts
    these tests pin come from:

    * `MultiSession.resume` sets `self._paused = False` over the problems it has just
      collected, so after a refused resume `multi.paused` is `False` and
      `multi.recording` is `False` - only the second is true. The all-screens branch
      of `toggle_pause` read the flag: it started the clock, put the owed auto-stop
      minutes back and told the bar and the tray "recording", over a take that was
      still paused on disk. That file is not this agent's to change; the controller's
      reading of it is.
    * `RecordingSession.pause` sets its flag *before* returning the problem - a
      `pause()` whose ffmpeg stop answers "ffmpeg exited with code 1" leaves
      `paused=True recording=False` - so the single-screen branch, which returned
      early on any problem, left the clock running over a take that had stopped.
    """

    class _Take:
        """Answers `recording` and `paused` the way the real two do."""

        active = True

        def __init__(self, recording: bool = True) -> None:
            self.recording = recording
            self.paused = not recording
            self.calls: list[str] = []
            self.refuse_resume = ""
            self.pause_problem = ""
            #: `MultiSession.resume` clears its flag even when it refused.
            self.clears_the_flag = False

        def pause(self) -> str:
            self.calls.append("pause")
            if not self.recording:
                return "Nothing is recording."
            # The flag goes up whether or not the stop had something to report.
            self.recording = False
            self.paused = True
            return self.pause_problem

        def resume(self) -> str:
            self.calls.append("resume")
            if self.refuse_resume:
                if self.clears_the_flag:
                    self.paused = False
                return self.refuse_resume
            self.recording = True
            self.paused = False
            return ""

    def _controller(self, **settings):
        controller = self.controller(found=THIS_MACHINE, **settings)
        controller.timer = _CountingTimer()
        controller.tray = _CountingTray()
        return controller

    def test_a_refused_resume_leaves_the_bar_and_the_clock_paused(self) -> None:
        for kind in ("multi", "session"):
            with self.subTest(take=kind):
                controller = self._controller(auto_stop_minutes=30)
                take = self._Take(recording=False)
                take.refuse_resume = "The recording could not be set up: ..."
                take.clears_the_flag = kind == "multi"
                setattr(controller, kind, take)
                controller._auto_stop_left = 20 * 60_000
                controller.toggle_pause()
                self.assertEqual(controller.timer.started, 0,
                                 "the clock must not run over a take that is paused")
                self.assertFalse(controller.auto_stop_timer.isActive())
                self.assertEqual(
                    controller._auto_stop_left, 20 * 60_000,
                    "a refused resume must not spend the minutes it was owed",
                )
                self.assertEqual(
                    controller.tray.states, [],
                    "nothing changed, so the tray has nothing to be told",
                )
                self.assertEqual(
                    self.bar.status_label.text(),
                    "The recording could not be set up: ...",
                )

    def test_the_button_keeps_trying_to_resume_a_take_that_refused(self) -> None:
        """The all-screens symptom: the flag said "not paused", so the next press
        called `pause()` and answered "Nothing is recording." - and the take could
        never be resumed again, however many times the button was pressed."""
        controller = self._controller()
        take = self._Take(recording=False)
        take.refuse_resume = "The recording could not be set up: ..."
        take.clears_the_flag = True
        controller.multi = take
        for _press in range(3):
            controller.toggle_pause()
        self.assertEqual(take.calls, ["resume", "resume", "resume"])
        self.assertNotIn("Nothing is recording.", self.bar.status_label.text())

    def test_a_pause_that_reports_a_problem_still_stops_the_clock(self) -> None:
        for kind in ("multi", "session"):
            with self.subTest(take=kind):
                controller = self._controller(auto_stop_minutes=30)
                take = self._Take(recording=True)
                take.pause_problem = "ffmpeg exited with code 1"
                setattr(controller, kind, take)
                controller.auto_stop_timer.start(20 * 60_000)
                controller.toggle_pause()
                self.assertEqual(controller.timer.stopped, 1)
                self.assertFalse(controller.auto_stop_timer.isActive())
                self.assertGreater(controller._auto_stop_left, 19 * 60_000)
                self.assertEqual(controller.tray.states, [True])
                self.assertEqual(
                    self.bar.status_label.text(), "ffmpeg exited with code 1"
                )

    def test_pausing_every_screen_says_so_just_like_one_screen(self) -> None:
        """Until 2026-11-05 the all-screens branch had no sentence at all: one screen
        said "Paused - the clock has stopped." and every screen said nothing."""
        lines = {}
        for kind in ("multi", "session"):
            controller = self._controller()
            setattr(controller, kind, self._Take(recording=True))
            controller.toggle_pause()
            paused = self.bar.status_label.text()
            controller.toggle_pause()
            lines[kind] = (paused, self.bar.status_label.text())
        self.assertEqual(lines["multi"], lines["session"])
        self.assertTrue(all(lines["multi"]), "both presses must say something")

    def test_the_bar_is_told_which_state_the_take_is_in(self) -> None:
        controller = self._controller()
        take = self._Take(recording=True)
        controller.multi = take
        controller.toggle_pause()
        self.assertTrue(self.bar._paused)
        controller.toggle_pause()
        self.assertFalse(self.bar._paused)


class _CountingTimer:
    """`self.timer` is a real `QTimer` in `Application`; these tests only need to know
    whether it was started or stopped, and when."""

    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self, *args) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


class _CountingTray:
    def __init__(self) -> None:
        self.states: list[bool] = []

    def set_recording(self, recording: bool, paused: bool = False) -> None:
        self.states.append(paused)

    def set_elapsed(self, *args) -> None:
        pass


class QuitDuringAnAllScreensRecordingTest(ControllerTestCase):
    """One confirmation box for a single screen and none for all of them.

    Reproduced 2026-11-05 with a live `multi` and no `session`: no question was asked
    at all, and `QApplication.quit()` was reached straight away, throwing away however
    many screens were being written.
    """

    class _FakeMulti:
        active = True

    def _quit(self, controller, answer):
        asked: list = []

        def question(*args, **kwargs):
            asked.append(args)
            return answer

        quits: list = []
        with mock.patch.object(QtWidgets.QMessageBox, "question", question):
            with mock.patch.object(
                QtWidgets.QApplication, "quit",
                staticmethod(lambda: quits.append(True)),
            ):
                controller.quit()
        return asked, quits

    def test_it_asks(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        controller.multi = self._FakeMulti()
        asked, _quits = self._quit(
            controller, QtWidgets.QMessageBox.StandardButton.Yes
        )
        self.assertEqual(len(asked), 1)

    def test_a_no_leaves_the_recording_running(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        controller.multi = self._FakeMulti()
        _asked, quits = self._quit(
            controller, QtWidgets.QMessageBox.StandardButton.No
        )
        self.assertEqual(quits, [])

    def test_a_yes_quits(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        controller.multi = self._FakeMulti()
        _asked, quits = self._quit(
            controller, QtWidgets.QMessageBox.StandardButton.Yes
        )
        self.assertEqual(len(quits), 1)

    def test_nothing_recording_still_asks_nothing(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        asked, quits = self._quit(
            controller, QtWidgets.QMessageBox.StandardButton.No
        )
        self.assertEqual(asked, [])
        self.assertEqual(len(quits), 1)


class StatusQueueTest(ControllerTestCase):
    """Five lines in one breath and one line to say them on.

    Measured on 2026-11-05 with a spy on `_say`, driving `_tick` with a session whose
    ffmpeg had died: the five lines went by in 0.74 ms in all, "Recording stopped on
    its own." held the bar for 0.29 ms and the line carrying the real reason for 0.17
    ms, and the bar was left saying "Saved take_001.mp4 - 12 MB".
    """

    LOG = (
        "frame=  600 fps= 59 q=20.0 size=    1024KiB\n"
        "[hevc_nvenc @ 000001] No capable devices found\n"
        "Error initializing output stream: Error while opening encoder\n"
        "Conversion failed!\n"
    )

    class _DeadSession:
        active = True
        recording = False
        paused = False
        progress = recorder.Progress(
            frames=0, fps=0.0, seconds=0.0, size_bytes=0, dropped=0
        )

        def __init__(self, log: str, path: Path) -> None:
            self._log = log
            self._path = path

        def log_text(self) -> str:
            return self._log

        def stop(self, timeout: float = 12.0):
            return self._path, ""

    def _dead(self, controller):
        folder = tempfile.TemporaryDirectory(prefix="screenrec-status-")
        self.addCleanup(folder.cleanup)
        written = Path(folder.name) / "take_001.mp4"
        written.write_bytes(b"0" * 1024)
        controller.settings.output_dir = folder.name
        controller.session = self._DeadSession(self.LOG, written)
        return written

    def test_the_reason_is_on_the_bar_long_enough_to_read(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self._dead(controller)
        controller._tick()
        self.assertTrue(
            controller._status_timer.isActive(),
            "with no queue the three lines of the reason were written in 0.45 ms",
        )
        self.assertEqual(controller._status_timer.interval(), app_module.STATUS_DWELL_MS)
        shown = controller.drain_status()
        self.assertEqual(len(shown), 3)
        self.assertIn("No capable devices found", shown[0])
        self.assertIn("Conversion failed!", shown[-1])

    def test_ffmpeg_s_english_is_framed_as_ffmpeg_s(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self._dead(controller)
        controller._tick()
        for line in controller.drain_status():
            self.assertTrue(line.startswith("ffmpeg: "), line)

    def test_the_sentence_that_says_it_stopped_comes_first_and_is_readable(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self._dead(controller)
        seen: list = []
        original = _Controller._say

        def spy(self, text, warn=False):
            seen.append(text() if callable(text) else text)
            original(self, text, warn)

        with mock.patch.object(_Controller, "_say", spy):
            controller._tick()
            controller.drain_status()
        self.assertEqual(seen[0], "Recording stopped on its own.")
        self.assertIn("Saved", seen[1])
        self.assertTrue(
            seen[2].startswith("ffmpeg: "),
            "the diagnosis is queued behind the two lines said at once, so each of "
            "the three is readable instead of being written in 0.45 ms",
        )

    def test_the_last_line_left_up_is_the_reason_not_the_file_name(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self._dead(controller)
        controller._tick()
        controller.drain_status()
        self.assertIn("Conversion failed!", self.bar.status_label.text())

    def test_a_blank_log_queues_nothing(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self._dead(controller)
        controller.session._log = "\n   \n"
        controller._tick()
        self.assertEqual(controller._status_queue, [])

    def test_a_new_take_drops_what_was_still_waiting(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        self._dead(controller)
        controller._tick()
        self.assertTrue(controller._status_queue)
        controller.session = None
        with mock.patch.object(
            recorder, "RecordingSession", EncoderNoteSurvivesTheStartTest._FakeSession
        ):
            controller._really_start()
        self.assertEqual(controller._status_queue, [])
        self.assertFalse(controller._status_timer.isActive())
        self.assertIn("Recording to", self.bar.status_label.text())

    def test_one_line_said_on_its_own_is_still_immediate(self) -> None:
        """`_say` is unchanged: a line answering something the user just did shows now."""
        controller = self.controller(found=THIS_MACHINE)
        controller._say(lambda: "first")
        controller._say(lambda: "second")
        self.assertEqual(self.bar.status_label.text(), "second")
        self.assertEqual(controller._status_queue, [])


class StopLinesTest(ControllerTestCase):
    """The stop path's own diagnosis, which "Saved ..." was written over.

    Reproduced on 2026-11-05 with a spy on `_say` and a real `Bar`, stopping a session
    that reported both kinds of trouble:

        _say #0: 'No sound at all arrived from "Headphones".'
        _say #1: 'ffmpeg exited with code 1'
        _say #2: 'Saved repro_take.mp4 - 0 MB'
        LEFT ON THE BAR: 'Saved repro_take.mp4 - 0 MB'   queue: []

    All three in 1.17 ms. `feeder_note` is `audio.py`'s own finding that nothing at
    all arrived from the chosen device - the failure `levels.py`'s docstring says has
    happened on this machine once already - and nothing in `tests/` so much as named
    it. The all-screens stop lost its reason the same way, measured on the same day
    with two files saved and screen 1's ffmpeg dead:

        _say #0: 'screen 1: ffmpeg exited with code 1'
        _say #1: 'Saved 2 screens - 4 MB. Click the file name below ...'
    """

    class _Session:
        active = True
        recording = False
        paused = False

        def __init__(self, path, problem: str = "", note: str = "") -> None:
            self._path = path
            self._problem = problem
            self.feeder_note = note

        def stop(self, timeout: float = 12.0):
            return self._path, self._problem

    class _Multi:
        active = True
        recording = False
        paused = False

        def __init__(self, written: list, problem: str = "") -> None:
            self._written = written
            self._problem = problem

        def stop(self):
            return self._written, self._problem

        def sidecar_path(self):
            return Path("take_001.screens.json")

    def _written(self, name: str = "take_001.mp4", size: int = 1024) -> Path:
        folder = getattr(self, "_folder", None)
        if folder is None:
            folder = tempfile.TemporaryDirectory(prefix="screenrec-stop-")
            self.addCleanup(folder.cleanup)
            self._folder = folder
        path = Path(folder.name) / name
        path.write_bytes(b"0" * size)
        return path

    def _controller(self):
        controller = self.controller(found=THIS_MACHINE)
        controller.timer = _Stub()
        controller.tray = _Stub()
        return controller

    def test_the_silent_track_is_said_and_can_be_read(self) -> None:
        controller = self._controller()
        controller.session = self._Session(
            self._written(), note='No sound at all arrived from "Headphones".'
        )
        controller.stop_recording()
        self.assertIn("Saved", self.bar.status_label.text())
        self.assertTrue(
            controller._status_timer.isActive(),
            "the note was said and overwritten in the same breath",
        )
        self.assertEqual(
            controller.drain_status(),
            ['No sound at all arrived from "Headphones".'],
        )

    def test_both_reasons_get_their_turn_and_the_last_one_stays(self) -> None:
        controller = self._controller()
        controller.session = self._Session(
            self._written(), problem="ffmpeg exited with code 1",
            note='No sound at all arrived from "Headphones".',
        )
        controller.stop_recording()
        shown = controller.drain_status()
        self.assertEqual(len(shown), 2)
        self.assertIn("No sound at all", shown[0])
        self.assertEqual(shown[1], "ffmpeg exited with code 1")
        self.assertEqual(self.bar.status_label.text(), "ffmpeg exited with code 1")

    def test_a_take_that_saved_nothing_shows_its_reason_at_once(self) -> None:
        """There is no "Saved ..." to read first, so the bar must not be empty for
        the 2.2 s the queue would otherwise hold the first line back."""
        controller = self._controller()
        controller.session = self._Session(
            None, problem="ffmpeg exited with code 1",
            note='No sound at all arrived from "Headphones".',
        )
        controller.stop_recording()
        self.assertIn("No sound at all", self.bar.status_label.text())
        self.assertEqual(
            controller.drain_status(), ["ffmpeg exited with code 1"]
        )

    def test_a_clean_stop_queues_nothing(self) -> None:
        controller = self._controller()
        controller.session = self._Session(self._written())
        controller.stop_recording()
        self.assertEqual(controller._status_queue, [])
        self.assertFalse(controller._status_timer.isActive())
        self.assertIn("Saved", self.bar.status_label.text())

    def test_the_all_screens_stop_keeps_its_reason_too(self) -> None:
        controller = self._controller()
        controller.multi = self._Multi(
            [self._written("take_001_screen0.mp4"),
             self._written("take_001_screen1.mp4")],
            problem="screen 1: ffmpeg exited with code 1",
        )
        controller.stop_recording()
        self.assertIn("Saved", self.bar.status_label.text())
        self.assertEqual(
            controller.drain_status(), ["screen 1: ffmpeg exited with code 1"]
        )

    def test_an_all_screens_stop_that_wrote_nothing_shows_its_reason_at_once(self) -> None:
        controller = self._controller()
        controller.multi = self._Multi([], problem="No screen could be recorded.")
        controller.stop_recording()
        self.assertEqual(
            self.bar.status_label.text(), "No screen could be recorded."
        )


class AllScreensNotesTest(ControllerTestCase):
    """The notes a half-started set produces are the reason, and ran over each other."""

    class _FakeMulti:
        active = True

        def __init__(self, ffmpeg, final, plan_for) -> None:
            self.notes = [
                "Screen 2 did not start: Nothing was written into output file",
                "Screen 3 did not start: No capable devices found",
            ]
            self.added: list = []

        def add(self, source) -> None:
            self.added.append(source)

        def start(self, on_progress=None, loopback_device=-1) -> str:
            return ""

    def _controller(self):
        controller = self.controller(
            found=THIS_MACHINE, source_kind=config.SOURCE_ALL_SCREENS
        )
        controller.monitors = [
            LANDSCAPE,
            winapi.Monitor(
                index=1, device_name="\\\\.\\DISPLAY1", x=2560, y=0, width=1440,
                height=2560, rotation=2, adapter="test",
            ),
        ]
        return controller

    def test_the_line_naming_the_files_is_not_run_over_by_them(self) -> None:
        controller = self._controller()
        with mock.patch.object(multi_module, "MultiSession", self._FakeMulti):
            controller._start_all_screens(Path("ScreenRec_2026-11-05_10-00-00.mp4"))
        self.assertIn("Recording 2 screens", self.bar.status_label.text())

    def test_each_note_then_gets_its_turn(self) -> None:
        controller = self._controller()
        with mock.patch.object(multi_module, "MultiSession", self._FakeMulti):
            controller._start_all_screens(Path("ScreenRec_2026-11-05_10-00-00.mp4"))
        shown = controller.drain_status()
        self.assertEqual(len(shown), 2)
        self.assertIn("Screen 2", shown[0])
        self.assertIn("Screen 3", shown[1])

    def test_a_clean_start_queues_nothing(self) -> None:
        controller = self._controller()

        class _Quiet(self._FakeMulti):
            def __init__(self, ffmpeg, final, plan_for) -> None:
                super().__init__(ffmpeg, final, plan_for)
                self.notes = []

        with mock.patch.object(multi_module, "MultiSession", _Quiet):
            controller._start_all_screens(Path("ScreenRec_2026-11-05_10-00-00.mp4"))
        self.assertEqual(controller._status_queue, [])
        self.assertIn("Recording 2 screens", self.bar.status_label.text())


class PlanShapeTest(ControllerTestCase):
    """The rest of the plan is unchanged - the encoder was the only thing moved."""

    def test_the_plan_still_carries_everything_the_recorder_needs(self) -> None:
        controller = self.controller(found=THIS_MACHINE, framerate=30)
        plan = controller._plan_for(Path("take_001.mp4"))
        self.assertIsInstance(plan, recorder.RecordingPlan)
        self.assertEqual(plan.framerate, 30)
        self.assertEqual(plan.layout, recorder.LAYOUT_SINGLE)
        self.assertEqual(plan.quality, controller.settings.cq())
        self.assertEqual(len(plan.sources), 1)

    def test_no_source_means_no_plan(self) -> None:
        controller = self.controller(found=THIS_MACHINE)
        controller.monitors = []
        self.assertIsNone(controller._plan_for(Path("take_001.mp4")))


class WhereToSaveTest(unittest.TestCase):
    """Asked once, on the first run, and then never again.

    Asked for on 2026-11-05. `output_dir` cannot carry this by itself: empty already
    means "the default place", so somebody who chose the default on purpose would be
    asked for ever. Hence `output_dir_asked`, set whatever the answer was - Cancel
    included, because a question that returns every launch is the complaint.
    """

    def setUp(self) -> None:
        if QtWidgets.QApplication.instance() is None:
            QtWidgets.QApplication([])
        self._temp = tempfile.TemporaryDirectory()
        self.folder = Path(self._temp.name)
        self.asked = []

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _answering(self, answer):
        def dialog(parent, title, start, *rest, **kwargs):
            self.asked.append(title)
            return answer
        return staticmethod(dialog)

    def _settings(self, **kwargs):
        made = config.Settings(**kwargs)
        return made

    def _ask(self, settings, answer, proposed=""):
        """Drive the question once.

        `proposed` stands in for what the installer left in the registry, and it is
        **always** patched, never read from the machine. Without that these tests pass
        or fail depending on whether ScreenRec happens to be installed on the computer
        running them - which it was, the first time this was written, and four of them
        went red for a reason that had nothing to do with the code.
        """
        controller = app_module.Application.__new__(app_module.Application)
        controller.settings = settings
        said = []
        controller._say = lambda message, warn=False: said.append(message)
        with mock.patch.object(config.paths, "config_dir", lambda: self.folder), \
             mock.patch.object(app_module.paths, "proposed_output_dir",
                               lambda: proposed), \
             mock.patch.object(QtWidgets.QFileDialog, "getExistingDirectory",
                               self._answering(answer)):
            controller._ask_where_to_save()
        return said

    def test_the_first_run_asks(self) -> None:
        settings = self._settings()
        self.assertFalse(settings.output_dir_asked)
        self._ask(settings, str(self.folder / "mine"))
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(settings.output_dir, str(self.folder / "mine"))
        self.assertTrue(settings.output_dir_asked)

    def test_it_never_asks_twice(self) -> None:
        settings = self._settings()
        self._ask(settings, str(self.folder / "mine"))
        self._ask(settings, str(self.folder / "other"))
        self.assertEqual(len(self.asked), 1, "it asked a second time")
        self.assertEqual(settings.output_dir, str(self.folder / "mine"))

    def test_cancelling_is_remembered_too(self) -> None:
        """Otherwise the question comes back every launch, which is the complaint."""
        settings = self._settings()
        self._ask(settings, "")
        self.assertTrue(settings.output_dir_asked)
        self.assertEqual(settings.output_dir, "", "a cancel must not invent a folder")
        self._ask(settings, str(self.folder / "late"))
        self.assertEqual(len(self.asked), 1)

    def test_an_installed_copy_takes_the_folder_the_wizard_asked_for(self) -> None:
        """The installer asks; the program must not ask the same thing again.

        And the installer cannot write the answer into this user's settings itself: an
        elevated setup runs as the administrator, so its `%APPDATA%` is somebody else's.
        It leaves the answer in the registry and the first run adopts it.
        """
        settings = self._settings()
        proposed = str(self.folder / "From The Installer")
        self._ask(settings, str(self.folder / "never asked"), proposed=proposed)
        self.assertEqual(self.asked, [], "it asked although the installer had")
        self.assertEqual(settings.output_dir, proposed)
        self.assertTrue(settings.output_dir_asked)

    def test_without_an_installer_it_still_asks(self) -> None:
        """Run from the source tree, or from a folder somebody unzipped: no registry,
        so the question belongs to the program again."""
        settings = self._settings()
        self._ask(settings, str(self.folder / "chosen"), proposed="")
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(settings.output_dir, str(self.folder / "chosen"))

    def test_a_settings_file_that_already_names_a_folder_is_still_asked_once(self) -> None:
        """An upgrade from a build that had no such field: the field is absent, so
        the question is owed once, and the existing folder is what the dialog opens on."""
        settings = self._settings(output_dir=str(self.folder / "already"))
        self._ask(settings, "")
        self.assertEqual(settings.output_dir, str(self.folder / "already"))
        self.assertTrue(settings.output_dir_asked)


if __name__ == "__main__":
    unittest.main()


class TheCameraCanBeTurnedOffMidTakeTest(ControllerTestCase):
    """The camera is the one thing on the bar that can be changed while recording.

    It is a window on the desktop, so taking it away stops it reaching the file from
    that frame on and ffmpeg never notices; the microphone and the system sound are
    ffmpeg inputs and are fixed when it starts. The reason anybody wants this is that
    the picture sometimes sits on top of the thing being demonstrated.
    """

    class _FakeSession:
        active = True

    def _recording(self, **settings):
        controller = self.controller(**settings)
        controller.session = self._FakeSession()
        return controller

    def test_turning_it_off_while_recording_takes_the_picture_away(self) -> None:
        controller = self._recording(webcam_enabled=True)
        stopped: list[int] = []
        with mock.patch.object(
            type(controller), "_stop_webcam", lambda self: stopped.append(1)
        ):
            controller._on_camera_toggled(False)
        self.assertEqual(stopped, [1])
        self.assertFalse(controller.settings.webcam_enabled)

    def test_turning_it_on_while_recording_puts_it_up_at_once(self) -> None:
        controller = self._recording(webcam_enabled=False)
        started: list[int] = []

        def fake_start(self) -> None:
            started.append(1)
            self.webcam = object()  # it opened

        with mock.patch.object(type(controller), "_start_webcam", fake_start):
            controller._on_camera_toggled(True)
        self.assertEqual(started, [1])
        self.assertTrue(controller.settings.webcam_enabled)

    def test_a_camera_that_will_not_open_puts_the_setting_back(self) -> None:
        """"On" and "not showing" must not look the same as "on and showing"."""
        controller = self._recording(webcam_enabled=False)

        def fake_start(self) -> None:
            self.webcam = None  # it did not open; `_start_webcam` has said why

        with mock.patch.object(type(controller), "_start_webcam", fake_start):
            controller._on_camera_toggled(True)
        self.assertFalse(controller.settings.webcam_enabled)
        self.assertFalse(self.bar.camera_button.is_on())

    def test_toggling_it_while_idle_only_writes_the_setting(self) -> None:
        """Turning the camera on outside a take must not light the webcam up."""
        controller = self.controller(webcam_enabled=False)
        controller.session = None
        touched: list[str] = []
        with mock.patch.object(
            type(controller), "_start_webcam", lambda self: touched.append("start")
        ), mock.patch.object(
            type(controller), "_stop_webcam", lambda self: touched.append("stop")
        ):
            controller._on_camera_toggled(True)
            controller._on_camera_toggled(False)
        self.assertEqual(touched, [])
        self.assertFalse(controller.settings.webcam_enabled)

    def test_the_bar_shows_what_the_setting_says(self) -> None:
        controller = self.controller(webcam_enabled=True)
        controller._refresh_bar()
        self.assertTrue(self.bar.camera_button.is_on())
        controller.settings.webcam_enabled = False
        controller._refresh_bar()
        self.assertFalse(self.bar.camera_button.is_on())

    def test_choosing_a_camera_turns_it_on_and_forgets_the_old_mode(self) -> None:
        """A mode picked for one camera means nothing on another."""
        controller = self.controller(
            webcam_enabled=False, webcam_device="Old", webcam_resolution="1920x1080"
        )
        controller.session = None
        controller._choose_camera("HD Pro Webcam C922")
        self.assertEqual(controller.settings.webcam_device, "HD Pro Webcam C922")
        self.assertEqual(controller.settings.webcam_resolution, "")
        self.assertTrue(controller.settings.webcam_enabled)

    def test_choosing_one_mid_take_restarts_the_picture(self) -> None:
        controller = self._recording(webcam_enabled=True)
        order: list[str] = []

        def fake_start(self) -> None:
            order.append("start")
            self.webcam = object()

        with mock.patch.object(
            type(controller), "_stop_webcam", lambda self: order.append("stop")
        ), mock.patch.object(type(controller), "_start_webcam", fake_start):
            controller._choose_camera("Integrated Camera")
        self.assertEqual(order, ["stop", "start"])


class TheChromeIsHiddenOnlyWhileRecordingTest(ControllerTestCase):
    """WDA_EXCLUDEFROMCAPTURE hides a window from *every* capture on the machine, the
    Snipping Tool included. Outside a take that protects nothing and only stops the
    user photographing their own program - which is how it was found, twice."""

    def tearDown(self) -> None:
        surfaces.set_recording(False)
        super().tearDown()

    def test_a_take_hides_it_and_the_end_of_one_gives_it_back(self) -> None:
        """`_Controller` counts `_hide_overlays` instead of running it, so the second
        half is driven through the real method - which is the one under test."""
        controller = self.controller()
        seen: list[bool] = []
        with mock.patch.object(
            surfaces, "set_recording", lambda value: seen.append(bool(value)) or 0
        ):
            controller.session = None
            with mock.patch.object(recorder, "RecordingSession", _NoSession):
                controller._really_start()
            self.assertEqual(seen, [True], "a take must hide the chrome")
            app_module.Application._hide_overlays(controller)
        self.assertEqual(seen, [True, False])

    def test_the_flag_is_off_while_nothing_is_recorded(self) -> None:
        surfaces.set_recording(False)
        self.assertFalse(surfaces.recording())
        surfaces.set_recording(True)
        self.assertTrue(surfaces.recording())


class _NoSession:
    """A session that starts and reports nothing, for the two lines around it."""

    active = True

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self, on_progress=None, loopback_device=-1) -> str:
        return ""


class SilentSystemSoundIsReportedDuringTheTakeTest(ControllerTestCase):
    """The end-of-take finding is right and too late.

    A forty-minute call is already lost by the time it is said, which is how this was
    found on 2026-11-13. The feeder's counters answer the same question while the take
    is running.
    """

    class _Feeder:
        def __init__(self, seconds: float, audible: int, error: str = "") -> None:
            self.frames_written = int(seconds * 48000)
            self.audible_frames = audible
            self.error = error

        @property
        def seconds_written(self) -> float:
            return self.frames_written / 48000.0

    class _Session:
        active = True

        def __init__(self, feeder) -> None:
            self.recorder = types.SimpleNamespace(feeder=feeder)

    def _controller_with(self, feeder, **settings):
        controller = self.controller(system_audio_enabled=True, **settings)
        controller._silence_warned = False
        return controller, self._Session(feeder)

    def _said(self) -> str:
        return self.bar.status_label.text()

    def test_a_long_silent_track_is_reported_once(self) -> None:
        controller, session = self._controller_with(self._Feeder(30.0, 0))
        controller._watch_system_sound(session)
        self.assertIn("playback device", self._said())
        self.assertTrue(controller._silence_warned)

        self.bar.set_status("")
        controller._watch_system_sound(session)
        self.assertEqual(self._said(), "", "it must not nag every tick")

    def test_a_short_silence_is_not_reported(self) -> None:
        """A pause in a conversation is not a broken recording."""
        controller, session = self._controller_with(self._Feeder(8.0, 0))
        controller._watch_system_sound(session)
        self.assertEqual(self._said(), "")
        self.assertFalse(controller._silence_warned)

    def test_a_track_with_sound_in_it_is_never_reported(self) -> None:
        controller, session = self._controller_with(self._Feeder(600.0, 48000))
        controller._watch_system_sound(session)
        self.assertEqual(self._said(), "")

    def test_nothing_is_said_when_system_sound_is_off(self) -> None:
        controller = self.controller(system_audio_enabled=False)
        controller._silence_warned = False
        controller._watch_system_sound(self._Session(self._Feeder(600.0, 0)))
        self.assertEqual(self._said(), "")

    def test_a_feeder_that_failed_is_left_to_its_own_report(self) -> None:
        """`_feeder_note` says that one, with the reason; two lines for one fault is
        one line too many."""
        controller, session = self._controller_with(
            self._Feeder(600.0, 0, error="the device vanished")
        )
        controller._watch_system_sound(session)
        self.assertEqual(self._said(), "")

    def test_a_take_with_no_feeder_at_all_is_quiet(self) -> None:
        controller = self.controller(system_audio_enabled=True)
        controller._silence_warned = False
        controller._watch_system_sound(types.SimpleNamespace(recorder=None))
        self.assertEqual(self._said(), "")

    def test_each_take_gets_its_own_warning(self) -> None:
        controller, session = self._controller_with(self._Feeder(30.0, 0))
        controller._watch_system_sound(session)
        self.assertTrue(controller._silence_warned)
        with mock.patch.object(recorder, "RecordingSession", _NoSession):
            controller.session = None
            controller._really_start()
        self.assertFalse(
            controller._silence_warned,
            "a take that starts clean must be able to report its own silence",
        )


WIRED = "Headphones - wire (Temple)"
WIFI = "Headphones - wifi (Temple)"
SPEAKERS = "Speakers (Realtek)"

class _Endpoints:
    """A stand-in for `endpoints.Endpoints` with the answers written down."""

    def __init__(self, default=WIRED, peaks=None) -> None:
        self.problem = ""
        self._default = default
        self._peaks = dict(peaks or {})
        self.refreshed = 0

    def default_name(self) -> str:
        return self._default

    def peaks(self) -> dict:
        return dict(self._peaks)

    def loudest(self, ignoring: str = "", floor: float = 0.02):
        best, value = "", 0.0
        for name, peak in self._peaks.items():
            if name == ignoring or peak < floor:
                continue
            if peak > value:
                best, value = name, peak
        return best, value

    def refresh(self) -> None:
        self.refreshed += 1

    def close(self) -> None:
        pass


def _loopbacks(*names):
    from screenrec.audio import LoopbackDevice

    return [
        LoopbackDevice(index=24 + n, name=f"{name} [Loopback]", channels=2, rate=48000)
        for n, name in enumerate(names)
    ]


class FollowingThePlaybackDeviceTest(ControllerTestCase):
    """"Default playback device" is a promise to follow Windows, and it was resolved
    once, when the recording started. A wireless headset running out of battery moves
    every sound on the machine to the speakers and left the recording capturing a
    device nobody was listening to."""

    class _Feeder:
        def __init__(self) -> None:
            self.device_index = 24
            self.switched_to = []

        def switch_to(self, index: int) -> bool:
            if index == self.device_index:
                return False
            self.device_index = index
            self.switched_to.append(index)
            return True

    class _Session:
        active = True

        def __init__(self, feeder) -> None:
            self.recorder = types.SimpleNamespace(feeder=feeder)

    def _ready(self, default=WIRED, chosen=-1, names=(WIRED, SPEAKERS)):
        controller = self.controller(
            system_audio_enabled=True, system_audio_device=chosen
        )
        controller.endpoints = _Endpoints(default=default)
        controller._capturing_name = WIRED
        patch = mock.patch.object(
            audio_module, "list_loopback_devices",
            staticmethod(lambda: _loopbacks(*names)),
        )
        patch.start()
        self.addCleanup(patch.stop)
        return controller

    def test_nothing_happens_while_the_device_stays_put(self) -> None:
        controller = self._ready(default=WIRED)
        feeder = self._Feeder()
        controller.session = self._Session(feeder)
        controller._tick_endpoints()
        self.assertEqual(feeder.switched_to, [])
        self.assertEqual(self.bar.status_label.text(), "")

    def test_the_capture_follows_the_default(self) -> None:
        controller = self._ready(default=SPEAKERS)
        feeder = self._Feeder()
        controller.session = self._Session(feeder)
        with mock.patch.object(type(controller), "_start_meters", lambda self: None):
            controller._tick_endpoints()
        self.assertEqual(feeder.switched_to, [25], "it must move to the speakers")
        self.assertEqual(controller._capturing_name, SPEAKERS)
        self.assertIn("moved", self.bar.status_label.text())

    def test_a_named_device_is_never_moved(self) -> None:
        """The user chose it. Second-guessing that is worse than recording silence."""
        controller = self._ready(default=SPEAKERS, chosen=24)
        feeder = self._Feeder()
        controller.session = self._Session(feeder)
        controller.endpoints._peaks = {WIRED: 0.0, SPEAKERS: 0.5}
        with mock.patch.object(type(controller), "_start_meters", lambda self: None):
            controller._tick_endpoints()
        self.assertEqual(feeder.switched_to, [])

    def test_a_device_portaudio_never_saw_is_reported_not_guessed(self) -> None:
        """PortAudio enumerates once; a device plugged in afterwards is not there,
        and a wrong index would record the wrong thing in silence."""
        controller = self._ready(default="A headset nobody enumerated")
        feeder = self._Feeder()
        controller.session = self._Session(feeder)
        with mock.patch.object(type(controller), "_start_meters", lambda self: None):
            controller._tick_endpoints()
        self.assertEqual(feeder.switched_to, [])
        self.assertIn("cannot follow", self.bar.status_label.text())
        self.assertEqual(controller.endpoints.refreshed, 1)

    def test_it_follows_with_no_recording_running_and_says_nothing(self) -> None:
        """The meter has to point at the right device too; a sentence about it when
        nothing is being recorded is noise."""
        controller = self._ready(default=SPEAKERS)
        controller.session = None
        started = []
        with mock.patch.object(
            type(controller), "_start_meters", lambda self: started.append(1)
        ):
            controller._tick_endpoints()
        self.assertEqual(controller._capturing_name, SPEAKERS)
        self.assertEqual(started, [1])
        self.assertEqual(self.bar.status_label.text(), "")

    def test_nothing_is_done_when_system_sound_is_off(self) -> None:
        controller = self.controller(system_audio_enabled=False)
        controller.endpoints = _Endpoints(default=SPEAKERS)
        controller._capturing_name = WIRED
        controller._tick_endpoints()
        self.assertEqual(controller._capturing_name, WIRED)

    def test_a_machine_without_com_simply_does_none_of_this(self) -> None:
        controller = self.controller(system_audio_enabled=True)
        controller.endpoints = None
        controller._tick_endpoints()  # must not raise
        self.assertEqual(self.bar.status_label.text(), "")


class SayingWhereTheSoundWentTest(ControllerTestCase):
    """A named device that is silent while another one is playing."""

    class _Session:
        active = True
        recorder = None

    def _ready(self, peaks):
        controller = self.controller(
            system_audio_enabled=True, system_audio_device=24
        )
        controller.endpoints = _Endpoints(default=WIRED, peaks=peaks)
        controller.session = self._Session()
        patch = mock.patch.object(
            audio_module, "list_loopback_devices",
            staticmethod(lambda: _loopbacks(WIRED, SPEAKERS)),
        )
        patch.start()
        self.addCleanup(patch.stop)
        return controller

    def test_it_names_both_devices(self) -> None:
        controller = self._ready({WIRED: 0.0, SPEAKERS: 0.4})
        controller._tick_endpoints()
        said = self.bar.status_label.text()
        self.assertIn(SPEAKERS, said)
        self.assertIn(WIRED, said)

    def test_it_is_said_once(self) -> None:
        controller = self._ready({WIRED: 0.0, SPEAKERS: 0.4})
        controller._tick_endpoints()
        self.bar.set_status("")
        controller._tick_endpoints()
        self.assertEqual(self.bar.status_label.text(), "")

    def test_a_chosen_device_that_is_playing_says_nothing(self) -> None:
        controller = self._ready({WIRED: 0.3, SPEAKERS: 0.4})
        controller._tick_endpoints()
        self.assertEqual(self.bar.status_label.text(), "")

    def test_a_quiet_machine_says_nothing(self) -> None:
        """Nothing playing anywhere is not a misrouted recording."""
        controller = self._ready({WIRED: 0.0, SPEAKERS: 0.0})
        controller._tick_endpoints()
        self.assertEqual(self.bar.status_label.text(), "")

    def test_residue_below_the_floor_is_not_sound(self) -> None:
        controller = self._ready({WIRED: 0.0, SPEAKERS: 0.0004})
        controller._tick_endpoints()
        self.assertEqual(self.bar.status_label.text(), "")

    def test_nothing_is_said_when_no_recording_is_running(self) -> None:
        """Outside a take the two meters show it without a sentence."""
        controller = self._ready({WIRED: 0.0, SPEAKERS: 0.4})
        controller.session = None
        controller._tick_endpoints()
        self.assertEqual(self.bar.status_label.text(), "")

