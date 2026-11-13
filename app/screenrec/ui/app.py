"""The application: everything that is not a widget and not the engine.

The bar, the popovers and the overlays each know how to look after themselves. This owns
the things that cross between them - which source is chosen, whether a recording is
running, what the hotkeys mean, when the disk is about to fill - and the lifetime of the
recording itself.

There is no main window. The bar is the application; a settings panel and a source
picker open from it and close again. That is the whole navigation, and it is deliberate:
recording is an action, and the old four-card settings form made you read twelve controls
to press one button.

**Threads.** Progress arrives on ffmpeg's stdout reader thread, the hotkey on its
listener thread, clicks on the mouse-hook thread, audio levels on their own threads.
Not one of them touches a widget: each hands over through a Qt signal, and the slots
below are the only code that paints.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import audio as audio_mod
from .. import capabilities
from .. import endpoints as endpoints_mod
from .. import config as config_mod
from .. import encoders as encoders_mod
from .. import errorlog
from .. import levels as levels_mod
from .. import multi as multi_mod
from .. import paths, presets as presets_mod, recorder, shellops, storage
from .. import takes as takes_mod
from .. import winapi
from .. import i18n
from ..i18n import _, pick
from . import surfaces, theme
from .bar import Bar
from .captureframe import CaptureFrame
from .countdown import Countdown
from .tray import SingleInstance, Tray
from .settings_panel import SettingsPanel
from .sources import SourcePicker

TICK_MS = 200
LEVEL_MS = 50
#: How long the system-sound track may be pure silence before the bar says so, in
#: seconds. Long enough that a pause in a conversation cannot trigger it, short enough
#: that the answer arrives while the take can still be restarted - the failure this is
#: for cost a whole call on 2026-11-13, and was only reported once it had ended.
SILENT_SYSTEM_SECONDS = 25.0
#: How often the playback endpoints are asked where the sound is going, in
#: milliseconds. A sweep of the default's name and every endpoint's peak was measured
#: at 1.6 ms on 2026-11-13, so this is not about cost - it is about not moving a
#: recording's capture device on a momentary answer.
ENDPOINT_MS = 2000
#: How long one queued status line holds the bar before the next takes its turn.
#:
#: Not a measurement - a chosen reading pause. What *was* measured, on 2026-11-05 with
#: a spy on `_say` while `_tick` handled an ffmpeg that had died, is what it replaces:
#: the five lines that function says went by in 0.74 ms in all, "Recording stopped on
#: its own." held the bar for 0.29 ms and the line that gave the real reason for 0.17
#: ms, and what was left on screen afterwards was "Saved ... - 12 MB". See `_say_each`.
STATUS_DWELL_MS = 2200


def _screens_word(count: int) -> str:
    """The right form of "screen" for `count`. The picker's words, not a second set."""
    return i18n.plural(
        count,
        pick("plural.source.screen.one", "screen"),
        pick("plural.source.screen.few", "screens"),
        pick("plural.source.screen.many", "screens"),
    )


def _recordings_word(count: int) -> str:
    """The right form of "recording" for `count`."""
    return i18n.plural(
        count,
        pick("plural.recording.one", "recording"),
        pick("plural.recording.few", "recordings"),
        pick("plural.recording.many", "recordings"),
    )


def _minutes_word(count: int) -> str:
    """The right form of "minute" for `count`."""
    return i18n.plural(
        count,
        pick("plural.minute.one", "minute"),
        pick("plural.minute.few", "minutes"),
        pick("plural.minute.many", "minutes"),
    )


def _settings_word(count: int) -> str:
    """The right form of "setting" for `count`."""
    return i18n.plural(
        count,
        pick("plural.setting.one", "setting"),
        pick("plural.setting.few", "settings"),
        pick("plural.setting.many", "settings"),
    )


def _hotkey_name(name: str) -> str:
    """The user's word for one of the three hotkeys.

    Written out rather than looked up in a dict because the catalogue checker reads
    the source: a `pick()` whose key is a variable is a key nothing can verify.
    """
    if name == "record":
        return pick("hotkey.record", "record")
    if name == "pause":
        return pick("hotkey.pause", "pause")
    return pick("hotkey.annotate", "annotate")


class Application(QtCore.QObject):
    """Owns the recording and everything that has to agree about it."""

    _progress_ready = QtCore.Signal(object)
    _hotkey_record = QtCore.Signal()
    _hotkey_pause = QtCore.Signal()
    _hotkey_annotate = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.settings, settings_problem = config_mod.load_settings()
        # Before the first widget is built: a window reads the catalogue in its
        # constructor, so a language applied afterwards would leave it English.
        config_mod.apply_language(self.settings)
        self.monitors: list[winapi.Monitor] = []
        self.mics: list[audio_mod.MicDevice] = []
        self.loopbacks: list[audio_mod.LoopbackDevice] = []
        self.encoders: list[capabilities.EncoderResult] = []

        self.session: recorder.RecordingSession | None = None
        self.multi: multi_mod.MultiSession | None = None
        self.last_set: Path | None = None
        self._player = None
        self.last_file: Path | None = None
        self.countdown_left = 0
        self._pending_start = False
        self._space_warned = False
        #: The encoder probe runs once, from the event loop - see `_probe_encoders`.
        self._probing = False
        self._probed = False
        #: The last reason `_note_encoder` said out loud, so one take does not say
        #: the same sentence for every segment it writes. It is a fact about this
        #: machine - which encoder families ran at all - so it is said once a run and
        #: cleared only when the probe has new facts.
        self._encoder_note = ""
        #: The same, for `_note_small_frame`, and cleared at the top of every take in
        #: `_really_start`. That one is a fact about the rectangle the user chose, not
        #: about the machine, and the next take can choose a different one.
        self._frame_note = ""
        #: That same sentence, kept so the start can put it back on the bar after its
        #: own line has run over it - see `_flush_encoder_note`.
        self._pending_note: Callable[[], str] | None = None
        #: Quit was asked for while the probe held the thread - see `quit`.
        self._quit_wanted = False
        #: The line on the bar, kept as what produced it - see `_say`.
        self._status: tuple[str | Callable[[], str], bool] | None = None
        #: Lines waiting their turn on that one line - see `_say_each`. It has to exist
        #: before `_check_ffmpeg`, which is the first thing in here that can say
        #: anything at all.
        self._status_queue: list[tuple[str | Callable[[], str], bool]] = []
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._next_status)
        #: How many recordings this run of the program has started, and the tag built
        #: from it - see `_loopback_pipe`.
        self._takes_started = 0
        self._take_tag = ""
        #: Milliseconds of "stop after N minutes" still owed when the pause began -
        #: see `_hold_auto_stop`.
        self._auto_stop_left = 0
        #: Said once per take; see `_watch_system_sound`.
        self._silence_warned = False
        #: The playback endpoint being captured and metered, by its Windows name.
        #: Empty until something has been metered, so the first sweep after that
        #: cannot announce a change that nobody made.
        self._capturing_name = ""
        #: The endpoint last pointed at by `_advise_on_silence`, so one wrong choice
        #: is reported once and not every two seconds.
        self._advised_name = ""
        #: Asks Windows which device is playing. None where COM is not available, and
        #: then every one of these features is simply absent - see `endpoints.py`.
        #: **Before `_start_meters`**, which reads it: it was built at the top of a
        #: take instead, and the program died on its first start-up.
        self.endpoints = (
            endpoints_mod.Endpoints() if endpoints_mod.available() else None
        )
        if self.endpoints is not None and self.endpoints.problem:
            print(f"app: {self.endpoints.problem} Playback devices will not be followed.")
            self.endpoints = None

        self.bar = Bar()
        self.picker: SourcePicker | None = None
        self.panel: SettingsPanel | None = None
        self.frame = CaptureFrame()
        #: One outline per screen in the recorded set; `self.frame` is the first of them
        #: and the only one a single-screen recording ever needs.
        self.frames: list[CaptureFrame] = [self.frame]
        self.countdown = Countdown()
        self.tray = Tray(self)
        self.highlight = None
        #: The camera overlay while a recording runs, or None. Declared beside the
        #: click highlight because it has the same life: created at start, gone at stop.
        self.webcam = None
        self.hotkeys: dict[str, object] = {}
        self.meters = levels_mod.LevelPair()
        self.annotations = None

        self._ffmpeg = ""
        self._check_ffmpeg()
        self._load_devices()
        self._refresh_bar()
        self._wire()
        self._start_hotkeys()

        if settings_problem:
            self._say(settings_problem, warn=True)
        self._offer_recovery()
        self._place_bar()
        self.bar.show()
        self._start_meters()

        problem = self.tray.show()
        if problem:
            self._say(problem, warn=True)

        # Last, and from the event loop rather than from here: the probe is seconds
        # long on a machine it has not seen before, and it used to run above, before
        # the bar was shown. See `_probe_encoders` for the measurement.
        QtCore.QTimer.singleShot(0, self._ask_where_to_save)
        QtCore.QTimer.singleShot(0, self._probe_encoders)

    def _ask_where_to_save(self) -> None:
        """On the first run only: where should recordings go?

        Asked from the event loop, after the bar is up, for the same reason the
        encoder probe is: a modal dialog raised from the constructor would be the
        first and only thing on the screen, with no window behind it to say what had
        opened.

        Asked **once**. `settings.output_dir_asked` is set whatever the answer is,
        Cancel included - the request was "запомнить выбор и больше не спрашивать",
        and a question that comes back every launch is what that is about. Afterwards
        the folder lives where every other setting does, in the settings panel.
        """
        if self.settings.output_dir_asked:
            return
        self.settings.output_dir_asked = True

        # The installer's wizard asks this question, so an installed copy must not ask
        # it again - and it cannot write the answer into this user's settings itself,
        # because an elevated installer is the administrator. So it leaves the answer
        # in the registry and the first run here adopts it.
        proposed = paths.proposed_output_dir()
        if proposed:
            self.settings.output_dir = proposed
            problem = config_mod.save_settings(self.settings)
            if problem:
                self._say(problem, warn=True)
                return
            self._say(
                lambda: _("Recordings go to {folder}. Change it in the settings.").format(
                    folder=self.settings.resolved_output_dir()
                )
            )
            return

        start = str(self.settings.resolved_output_dir())
        chosen = ""
        try:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                None,
                _("Where should ScreenRec keep your recordings?"),
                start,
            )
        except Exception as exc:  # noqa: BLE001 - a dialog is not worth a failed start
            self._say(f"The folder could not be asked for: {exc}", warn=True)
        if chosen:
            self.settings.output_dir = chosen
        problem = config_mod.save_settings(self.settings)
        if problem:
            self._say(problem, warn=True)
            return
        self._say(
            lambda: _("Recordings go to {folder}. Change it in the settings.").format(
                folder=self.settings.resolved_output_dir()
            )
        )

    # ------------------------------------------------------------- start-up

    def _check_ffmpeg(self) -> None:
        try:
            self._ffmpeg = str(paths.ffmpeg_exe(self.settings.ffmpeg_dir or None))
        except paths.BinaryNotFound as exc:
            self._ffmpeg = ""
            self._say(str(exc), warn=True)

    def _load_devices(self) -> None:
        winapi.enable_dpi_awareness()
        try:
            self.monitors = winapi.list_monitors()
        except Exception as exc:
            self.monitors = []
            self._say(
                lambda: _("Monitors could not be listed: {reason}").format(
                    reason=exc
                ),
                warn=True,
            )
        self.mics = audio_mod.list_microphones(self._ffmpeg) if self._ffmpeg else []
        self.loopbacks = audio_mod.list_loopback_devices()

    def _probe_encoders(self) -> None:
        """Ask the machine what it can really encode with, not what ffmpeg lists.

        Measured here: this ffmpeg lists seven encoders - every AV1, every Intel QSV and
        every AMD one - that this GPU refuses. Choosing from the list would hand the user
        a recording that fails after they pressed record.

        **It is slow**, because every candidate now encodes two frames through the real
        `ddagrab` pipeline rather than through a convenient one. Measured on this machine
        on 2026-11-04 with an empty cache: 5.87 s in all - 0.50 s to list the encoders
        and ask whether there is a desktop to duplicate, then twelve candidates at 355
        to 605 ms each. A cached answer costs one `ffmpeg -version` instead. Until
        2026-11-04 this ran in the constructor, before `self.bar.show()`, so the first
        launch on a machine with no cached answer was six seconds of nothing at all: no
        window, no tray icon, nothing to say the program had started.

        It stays on the UI thread on purpose. Nothing can record until it has finished,
        so a worker thread would buy no concurrency that anybody can use and would need
        a lock around `self.encoders`; `_probe_step` pumps the event queue between
        candidates instead, which is what keeps the bar painting and its buttons alive.
        """
        if self._probing or self._probed or not self._ffmpeg:
            return
        before = self._status
        self._probing = True
        try:
            self.encoders = capabilities.probe(self._ffmpeg, on_step=self._probe_step)
        finally:
            self._probing = False
        self._probed = True
        # New facts about this machine: whatever was said about the last choice was said
        # without them, so let the next plan speak again.
        self._encoder_note = ""
        if self.panel is not None:
            # The panel may have been opened and closed again while this ran; it keeps
            # its rows, so it has to be told the moment there is an answer.
            self.panel.set_encoders(self.encoders)
        if self._quit_wanted:
            # Asked for during the probe and held back there, because `QApplication`
            # would have eaten it. Outside `_probe_step`'s nested loop it works.
            self._quit_wanted = False
            QtWidgets.QApplication.quit()
            return
        if not capabilities.working(self.encoders):
            # Not `rejected and not working`, which is what stood here until
            # 2026-11-05: `rejected` counts only the names ffmpeg *listed*, so a build
            # that lists nothing this program can use - `listed_encoders` also returns
            # an empty set on any OSError or SubprocessError - produced an empty
            # rejected list, no warning at all, and a status line restored as if the
            # machine were healthy. Every recording then runs `libx264`, which on that
            # machine is just as absent.
            if capabilities.rejected(self.encoders):
                self._say(
                    lambda: _(
                        "No encoder on this machine could encode a test frame; "
                        "recording will fail until that is sorted out."
                    ),
                    warn=True,
                )
            else:
                self._say(
                    lambda: _(
                        "ffmpeg named no encoder this program can use. It may be the "
                        "wrong build, or it may not have run at all; recording will "
                        "fail until that is sorted out."
                    ),
                    warn=True,
                )
            return
        # Put back whatever the line held before the progress ran over it - a settings
        # problem, a recovered take - instead of leaving the last candidate's name up.
        self._say(*(before or ("", False)))

    def _probe_step(self, name: str, index: int, total: int) -> None:
        """One line per candidate, and a repaint, while the probe runs.

        `capabilities.probe` calls this before each attempt; nothing passed it until
        now. Pumping the queue here is the whole reason the probe may sit on the UI
        thread: without it the bar is on screen and frozen for as long as the probe
        takes, which is worse than honest, because it looks like a hung program.

        **It is a nested event loop**, and that is not free. Every slot this program
        has runs inside it: `start_recording` refuses while `_probing` is set,
        `open_settings` hands the panel nothing until `_probed` is, and `quit` holds
        the quit until the probe lets go - `QApplication.quit()` called in here exits
        this loop and not `exec()`. A slot added later has to decide the same thing.

        The encoder's name is not translated - `hevc_nvenc` is ffmpeg's word, not ours.
        """
        self._say(
            lambda: _(
                "Checking what this machine can encode: {name} ({done} of {total})"
            ).format(name=name, done=index + 1, total=total)
        )
        QtWidgets.QApplication.processEvents()

    def _offer_recovery(self) -> None:
        """A session that died leaves playable segments. Offer them, do not bin them."""
        folder = self.settings.resolved_output_dir()
        found = takes_mod.orphans(folder)
        if not found:
            return
        groups = takes_mod.group_orphans(found)
        count = len(groups)
        answer = QtWidgets.QMessageBox.question(
            None,
            _("Unfinished recording"),
            _(
                "{count} {word} did not finish - the segments are still in {folder}."
                "\n\nAssemble them into playable files now?"
            ).format(count=count, word=_recordings_word(count), folder=folder),
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        recovered, failed = 0, []
        for segments in groups.values():
            path, problem = takes_mod.recover(self._ffmpeg, segments)
            if path is not None:
                recovered += 1
                self.last_file = path
            elif problem:
                failed.append(problem)
        if recovered:
            self._say(
                lambda: _("Recovered {count} {word}.").format(
                    count=recovered, word=_recordings_word(recovered)
                )
            )
        for problem in failed:
            self._say(problem, warn=True)

    # ----------------------------------------------------------------- wire

    def _wire(self) -> None:
        bar = self.bar
        bar.record_requested.connect(self.start_recording)
        bar.stop_requested.connect(self.stop_recording)
        bar.pause_requested.connect(self.toggle_pause)
        bar.discard_requested.connect(self.discard_recording)
        bar.source_clicked.connect(self.open_source_picker)
        bar.settings_clicked.connect(self.open_settings)
        bar.quit_requested.connect(self.quit)
        bar.mic_toggled.connect(self._mic_toggled)
        bar.system_toggled.connect(self._system_toggled)
        bar.clicks_toggled.connect(self._clicks_toggled)
        bar.camera_toggled.connect(self._on_camera_toggled)
        bar.camera_menu_requested.connect(lambda: self._device_menu("camera"))
        bar.mic_menu_requested.connect(lambda: self._device_menu("mic"))
        bar.system_menu_requested.connect(lambda: self._device_menu("system"))
        bar.fps_clicked.connect(self._fps_menu)
        bar.format_clicked.connect(self._format_menu)
        bar.folder_clicked.connect(self._open_folder)
        bar.last_file_clicked.connect(self.watch_last)
        bar.moved.connect(self._bar_moved)

        self.tray.record_requested.connect(self.start_recording)
        self.tray.stop_requested.connect(self.stop_recording)
        self.tray.pause_requested.connect(self.toggle_pause)
        self.tray.show_requested.connect(self._show_bar)
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.folder_requested.connect(self._open_folder)
        self.tray.watch_requested.connect(self.watch_last)
        self.tray.quit_requested.connect(self.quit)
        self.countdown.finished.connect(self._really_start)

        self._progress_ready.connect(self._on_progress)
        self._hotkey_record.connect(self.toggle_recording)
        self._hotkey_pause.connect(self.toggle_pause)
        self._hotkey_annotate.connect(self.toggle_annotations)
        i18n.notifier.changed.connect(self._retranslate)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)

        self.level_timer = QtCore.QTimer(self)
        self.level_timer.setInterval(LEVEL_MS)
        self.level_timer.timeout.connect(self._tick_levels)

        self.endpoint_timer = QtCore.QTimer(self)
        self.endpoint_timer.setInterval(ENDPOINT_MS)
        self.endpoint_timer.timeout.connect(self._tick_endpoints)

        self.countdown_timer = QtCore.QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._countdown_tick)

        self.auto_stop_timer = QtCore.QTimer(self)
        self.auto_stop_timer.setSingleShot(True)
        self.auto_stop_timer.timeout.connect(self._auto_stop)

    def _stop_hotkeys(self) -> None:
        """Hand every registered combination back to Windows.

        Its own method because the settings window needs it: a shortcut registered
        with `RegisterHotKey` never reaches any window, so a box waiting for a key
        press would read nothing and the recording would start instead.
        """
        for name, listener in list(self.hotkeys.items()):
            try:
                listener.stop()
            except Exception as exc:
                self._say(
                    lambda n=name, e=exc: _(
                        "The {name} hotkey would not stop: {reason}"
                    ).format(name=_hotkey_name(n), reason=e),
                    warn=True,
                )
        self.hotkeys = {}

    def _on_hotkey_capture(self, armed: bool) -> None:
        """The settings window is reading a key press, or has finished."""
        if armed:
            self._stop_hotkeys()
        else:
            self._start_hotkeys()

    def _start_hotkeys(self) -> None:
        from .. import hotkeys as hotkeys_mod

        wanted = {
            "record": (self.settings.hotkey or "Ctrl+Alt+R", self._hotkey_record.emit),
            "pause": (self.settings.hotkey_pause, self._hotkey_pause.emit),
            "annotate": (self.settings.hotkey_annotate, self._hotkey_annotate.emit),
        }
        self._stop_hotkeys()

        for name, (text, slot) in wanted.items():
            if not text:
                continue
            try:
                listener = hotkeys_mod.HotkeyListener(slot)
                problem = listener.start(text)
            except Exception as exc:
                self._say(
                    lambda n=name, e=exc: _(
                        "The {name} hotkey was not registered: {reason}"
                    ).format(name=_hotkey_name(n), reason=e),
                    warn=True,
                )
                continue
            if problem:
                self._say(problem, warn=True)
                continue
            self.hotkeys[name] = listener

    # --------------------------------------------------------------- source

    def _source_label(self) -> tuple[str, str]:
        kind = self.settings.source_kind
        if kind == config_mod.SOURCE_ALL_SCREENS:
            count = len(self.monitors)
            # The picker's own wording for the same thing, by the same key: two
            # spellings of "All 3 screens" on one screen is how a language drifts.
            return "monitor", _("All {count} screens").format(
                count=count, word=_screens_word(count)
            )
        if kind == config_mod.SOURCE_REGION:
            # `is_a_rectangle`, not `len(...) == 4`: four integers that enclose nothing
            # are not a region, and showing "Region 0×0" on the chip told the user they
            # had one. See `config.is_a_rectangle` for what a file like that did next.
            if config_mod.is_a_rectangle(self.settings.region):
                x, y, width, height = self.settings.region
                return "region", _("Region {width}×{height}").format(
                    width=width, height=height
                )
            return "region", _("Choose a region")
        if kind == config_mod.SOURCE_WINDOW:
            return "window", self.settings.window_title or _("Choose a window")
        monitor = self._chosen_monitor()
        if monitor is None:
            return "monitor", _("No screen")
        icon = "monitor_rotated" if monitor.rotated else "monitor"
        screen = pick("source.screen_number", "Screen {number}").format(
            number=monitor.index + 1
        )
        return icon, f"{screen} — {monitor.width}×{monitor.height}"

    def _chosen_monitor(self) -> winapi.Monitor | None:
        for monitor in self.monitors:
            if monitor.index == self.settings.monitor_index:
                return monitor
        return self.monitors[0] if self.monitors else None

    def open_source_picker(self) -> None:
        if self.picker is None:
            self.picker = SourcePicker(self.settings)
            self.picker.chosen.connect(self._source_chosen)
            self.picker.cancelled.connect(lambda: self.bar.set_source(*self._source_label()))
        self.picker.refresh()
        self.picker.popup_near(self.bar)

    def _source_chosen(self, kind: str, payload: object) -> None:
        self.settings.source_kind = kind
        if kind == config_mod.SOURCE_MONITOR and payload is not None:
            self.settings.monitor_index = int(payload)
        elif kind == config_mod.SOURCE_REGION and payload:
            chosen = list(payload)  # type: ignore[arg-type]
            # The selector cannot emit a degenerate rectangle today, and the settings
            # file is checked on the way in; this is the third way in and is checked
            # by the same rule, so no path can store one.
            if config_mod.is_a_rectangle(chosen):
                self.settings.region = chosen
        elif kind == config_mod.SOURCE_WINDOW and payload is not None:
            self.settings.window_hwnd = int(payload)
            info = next(
                (w for w in winapi.list_windows() if w.hwnd == int(payload)), None
            )
            self.settings.window_title = info.title if info else ""
        self._refresh_bar()
        self._save()

    # -------------------------------------------------------------- the bar

    def _refresh_bar(self) -> None:
        self.bar.set_source(*self._source_label())
        self.bar.set_toggles(
            self.settings.mic_enabled,
            self.settings.system_audio_enabled,
            self.settings.click_highlight,
            self.settings.webcam_enabled,
        )
        profile = self.settings.encoding()
        self.bar.set_quick(
            _("{rate} fps").format(rate=self.settings.framerate),
            profile.label.split(" — ")[-1],
        )
        self.bar.set_footer(
            str(self.settings.resolved_output_dir()),
            self.last_file.name if self.last_file else "",
            self.settings.hotkey or "Ctrl+Alt+R",
        )

    def _place_bar(self) -> None:
        if len(self.settings.island_pos) == 2:
            self.bar.place(self.settings.island_pos[0], self.settings.island_pos[1])
        else:
            self.bar.place_default(self.monitors)

    def _bar_moved(self, x: int, y: int) -> None:
        self.settings.island_pos = [x, y]
        self._save()

    def _say(self, text: str | Callable[[], str], warn: bool = False) -> None:
        """One line under the bar.

        `text` is normally a callable that produces the sentence - a lambda round the
        `_("...")` that words it. The callable, not the sentence, is what is kept, so
        `_retranslate` can run it again and the line already on screen follows a
        language switch instead of staying in yesterday's language under a bar that
        moved on. A plain string - a problem another module worded on its way here -
        is shown as it is and stays as it was.

        It still replaces whatever is on the bar at once, and deliberately: a line
        said on its own is an answer to something the user just did. Several lines
        said in one breath are the case `_say_each` exists for.
        """
        self._status = (text, warn)
        self.bar.set_status(text() if callable(text) else text, warn)

    def _say_now_then_queue(
        self, lines: list[tuple[str | Callable[[], str], bool]]
    ) -> None:
        """Say the first of these at once, and give the rest their turn after it.

        `_say_each` makes every line wait `STATUS_DWELL_MS`, the first one included,
        so that a caller can put its own line up first and have it read. A stop that
        saved no file has no line of its own - the reason *is* the answer to the
        button - and queueing all of it would leave the bar empty for 2.2 s, which is
        the silence the queue exists to end.
        """
        if not lines:
            return
        text, warn = lines[0]
        self._say(text, warn)
        self._say_each(lines[1:])

    def _say_each(
        self, lines: list[tuple[str | Callable[[], str], bool]]
    ) -> None:
        """Several lines said in one breath, each given its own turn on the bar.

        `Bar.set_status` - which is not this module's to change - replaces the label
        with no dwell and no queue, so a run of `_say` calls leaves only the last one
        on screen. Measured on 2026-11-05 with a spy on `_say`, driving `_tick` with a
        session whose ffmpeg had died: those five lines went by in 0.74 ms in all,
        "Recording stopped on its own." held the bar for 0.29 ms and the line that
        gave the real reason for 0.17 ms, and what was left on screen afterwards was
        "Saved take_001.mp4 - 12 MB". The whole diagnosis was written and none of it
        was readable.

        **Every line here waits its turn, the first one included.** That is what lets
        a caller say its own line with `_say` first - the file names, the fact that a
        recording has stopped - and have it read before the detail arrives, instead of
        being wiped by it a few microseconds later. The queue then runs on a
        `STATUS_DWELL_MS` beat and the last line stays, so a failure's reason is what
        is left on the bar.
        """
        if not lines:
            return
        self._status_queue.extend(lines)
        if not self._status_timer.isActive():
            self._status_timer.start(STATUS_DWELL_MS)

    def _next_status(self) -> None:
        """One line's turn is over; show the next and keep the queue moving."""
        if not self._status_queue:
            return
        text, warn = self._status_queue.pop(0)
        self._say(text, warn)
        if self._status_queue:
            self._status_timer.start(STATUS_DWELL_MS)

    def _clear_status_queue(self) -> None:
        """Drop whatever is still waiting. A new recording's own line is not a queue
        member - it is the thing the user just asked for and must show at once."""
        self._status_queue.clear()
        self._status_timer.stop()

    def _retranslate(self, code: str = "") -> None:
        """Re-word everything this controller - not the widgets - decided.

        Connected to `i18n.notifier.changed`. The bar retranslates its own fixed text;
        the source chip, the quick pills, the footer and the status line are worded
        here, so they are rebuilt here.
        """
        self._refresh_bar()
        if self._status is None:
            return
        text, warn = self._status
        if callable(text):
            self.bar.set_status(text(), warn)

    def _save(self) -> None:
        problem = config_mod.save_settings(self.settings)
        if problem:
            self._say(problem, warn=True)

    # ------------------------------------------------------------- toggles

    def _mic_toggled(self, on: bool) -> None:
        self.settings.mic_enabled = on
        if on and not self.settings.mic_device and self.mics:
            self.settings.mic_device = self.mics[0].ffmpeg_id
            self.settings.mic_label = self.mics[0].name
        self._save()
        self._start_meters()

    def _system_toggled(self, on: bool) -> None:
        self.settings.system_audio_enabled = on
        self._save()
        self._start_meters()

    def _clicks_toggled(self, on: bool) -> None:
        self.settings.click_highlight = on
        self._save()

    def _device_menu(self, which: str) -> None:
        menu = QtWidgets.QMenu()
        menu.setStyleSheet(theme.style_sheet())
        if which == "mic":
            if not self.mics:
                menu.addAction(_("No microphone found")).setEnabled(False)
            for mic in self.mics:
                action = menu.addAction(mic.name)
                action.setCheckable(True)
                action.setChecked(mic.ffmpeg_id == self.settings.mic_device)
                action.triggered.connect(
                    lambda _checked, m=mic: self._choose_mic(m)
                )
        elif which == "camera":
            found = self._cameras()
            if not found:
                menu.addAction(_("No camera was found on this machine.")).setEnabled(
                    False
                )
            else:
                automatic = menu.addAction(_("Choose for me"))
                automatic.setCheckable(True)
                automatic.setChecked(not self.settings.webcam_device)
                automatic.triggered.connect(lambda: self._choose_camera(""))
            for name in found:
                action = menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(name == self.settings.webcam_device)
                action.triggered.connect(
                    lambda _checked, n=name: self._choose_camera(n)
                )
            menu.addSeparator()
            settings_action = menu.addAction(_("Camera settings"))
            settings_action.triggered.connect(
                lambda: self._open_camera_settings()
            )
        else:
            # The settings panel's own words for this device, by the same keys - and
            # the same reason for naming the default: see `_default_loopback_label`.
            default = menu.addAction(self._default_loopback_label())
            default.setCheckable(True)
            # Ticked by name, like everything else here: the saved index can point
            # at another device after a renumbering, and a menu that ticks the wrong
            # line is worse than one that ticks none. See `audio.index_for_label`.
            chosen = self._chosen_loopback_index()
            default.setChecked(chosen < 0)
            default.triggered.connect(lambda: self._choose_loopback(-1))
            for device in self.loopbacks:
                action = menu.addAction(device.label)
                action.setCheckable(True)
                action.setChecked(device.index == chosen)
                action.triggered.connect(
                    lambda _checked, d=device: self._choose_loopback(d.index)
                )
        menu.exec(QtGui.QCursor.pos())

    def _on_camera_toggled(self, on: bool) -> None:
        """The camera button on either row.

        **This one works in the middle of a take, and the other toggles do not.** The
        camera is a window on the desktop, so taking it away stops it reaching the file
        from that frame on and the recording carries on untouched; the microphone and
        the system sound are ffmpeg inputs, fixed when it starts. The reason somebody
        wants this is that the picture sometimes sits on top of the thing being
        demonstrated.

        Off is always obeyed. On, during a take, is attempted at once and may fail -
        `_start_webcam` says why and leaves the button where the setting now is, so
        "on" and "not showing" never look the same as "on and showing".
        """
        wanted = bool(on)
        self.settings.webcam_enabled = wanted
        self._save()
        if self._is_recording():
            if wanted:
                self._start_webcam()
                if self.webcam is None:
                    # It did not open; `_start_webcam` has already said why.
                    self.settings.webcam_enabled = False
                    self._save()
            else:
                self._stop_webcam()
        self._refresh_bar()

    def _cameras(self) -> list[str]:
        """The cameras Windows reports, by name, or an empty list.

        Asked only when the menu is opened: `webcam` pulls in QtMultimedia and opening
        the device is not free, so a program that has never shown the camera must not
        pay for the list at start-up.
        """
        try:
            from .. import webcam as webcam_mod

            return [device.description() for device in webcam_mod.cameras()]
        except Exception as exc:  # noqa: BLE001 - somebody else's hardware
            self._say(
                lambda: _("The camera list could not be read: {reason}").format(
                    reason=exc
                ),
                warn=True,
            )
            return []

    def _choose_camera(self, description: str) -> None:
        """Pick a camera from the bar's menu, and switch to it mid-take if need be."""
        self.settings.webcam_device = description
        # A mode chosen for a different camera means nothing on this one.
        self.settings.webcam_resolution = ""
        self.settings.webcam_enabled = True
        self._save()
        if self._is_recording():
            self._stop_webcam()
            self._start_webcam()
            if self.webcam is None:
                self.settings.webcam_enabled = False
                self._save()
        self._refresh_bar()

    def _choose_mic(self, mic: audio_mod.MicDevice) -> None:
        self.settings.mic_device = mic.ffmpeg_id
        self.settings.mic_label = mic.name
        self.settings.mic_enabled = True
        self._save()
        self._refresh_bar()
        self._start_meters()

    def _choose_loopback(self, index: int) -> None:
        self.settings.system_audio_device = index
        # The name beside the number, because the number is not what identifies an
        # endpoint - see `audio.index_for_label`. "" for the default, which is not a
        # device at all but "whatever Windows is using", and must stay that way.
        self.settings.system_audio_label = audio_mod.label_for_index(
            index, self.loopbacks
        )
        self.settings.system_audio_enabled = True
        self._save()
        self._refresh_bar()
        self._start_meters()

    def _open_camera_settings(self) -> None:
        """The settings window, already on the Camera page."""
        self.open_settings()
        if self.panel is not None:
            self.panel.show_page("camera")

    def _fps_menu(self) -> None:
        menu = QtWidgets.QMenu()
        menu.setStyleSheet(theme.style_sheet())
        for rate in (24, 30, 48, 60, 120):
            action = menu.addAction(_("{rate} fps").format(rate=rate))
            action.setCheckable(True)
            action.setChecked(rate == self.settings.framerate)
            action.triggered.connect(lambda _c, r=rate: self._set_framerate(r))
        menu.exec(QtGui.QCursor.pos())

    def _set_framerate(self, rate: int) -> None:
        self.settings.framerate = rate
        self._save()
        self._refresh_bar()

    def _format_menu(self) -> None:
        menu = QtWidgets.QMenu()
        menu.setStyleSheet(theme.style_sheet())
        for key, profile in config_mod.PROFILES.items():
            action = menu.addAction(_(profile.label))
            action.setCheckable(True)
            action.setChecked(key == self.settings.profile)
            action.triggered.connect(lambda _c, k=key: self._set_profile(k))
        menu.addSeparator()
        for preset in presets_mod.BUILTIN:
            action = menu.addAction(_(preset.name))
            action.triggered.connect(lambda _c, p=preset: self._apply_preset(p))
        menu.exec(QtGui.QCursor.pos())

    def _set_profile(self, key: str) -> None:
        self.settings.profile = key
        self._save()
        self._refresh_bar()

    def _apply_preset(self, preset: presets_mod.Preset) -> None:
        changed = preset.apply_to(self.settings)
        self.settings.preset = preset.key
        self._save()
        self._refresh_bar()
        self._start_meters()
        if self.panel is not None:
            self.panel.refresh_from_settings()
        touched = len(changed)
        self._say(
            lambda: _("{name}: {count} {word} changed.").format(
                name=_(preset.name),
                count=touched,
                word=_settings_word(touched),
            )
        )

    # ------------------------------------------------------------ settings

    def open_settings(self) -> None:
        if self.panel is None:
            self.panel = SettingsPanel(self.settings)
            self.panel.changed.connect(self._settings_changed)
            self.panel.closed.connect(lambda: None)
            self.panel.hotkey_capture.connect(self._on_hotkey_capture)
        self.panel.refresh_devices(self.mics, self.loopbacks)
        width, height = self._capture_size()
        self.panel.set_capture_size(width, height)
        # Without this line the panel never learns anything about this machine, so it
        # shows no path sentence, no Details expander, and - the one that matters - it
        # goes on offering 4:4:4 on a machine whose every working encoder refuses it.
        # `_probe_encoders` calls it too, for a panel that was open before the answer
        # arrived; both are needed, because either can happen first.
        #
        # Only once there *is* an answer. `_probe_step` pumps the event queue, so the
        # gear is live while the probe runs, and `self.encoders` is empty until
        # `capabilities.probe` returns - and an empty list is not "nothing known yet"
        # to the panel, it is "the probe ran and nothing works", which would put "No
        # hardware encoder works on this machine" in front of somebody whose machine
        # is still being asked.
        if self._probed:
            self.panel.set_encoders(self.encoders)
        self.panel.set_recording(self._is_recording())
        self.panel.popup_near(self.bar)

    def _settings_changed(self) -> None:
        self._save()
        self._refresh_bar()
        self._start_hotkeys()
        self._start_meters()

    def _capture_size(self) -> tuple[int, int]:
        plan_source = self._resolve_source()
        if plan_source is None:
            monitor = self._chosen_monitor()
            if monitor is None:
                return 1920, 1080
            return monitor.width, monitor.height
        return plan_source.output_size()

    # --------------------------------------------------------------- meters

    def _start_meters(self) -> None:
        notes = self.meters.start(
            mic_dshow_name=(
                self.settings.mic_label if self.settings.mic_enabled else ""
            ),
            loopback_index=(
                self._loopback_index() if self.settings.system_audio_enabled else -1
            ),
        )
        for note in notes:
            self._say(note, warn=True)
        self._capturing_name = self._loopback_name()
        if self.settings.mic_enabled or self.settings.system_audio_enabled:
            self.level_timer.start()
        else:
            self.level_timer.stop()
            self.bar.set_mic_level(0.0)
            self.bar.set_system_level(0.0)
        if self.settings.system_audio_enabled and self.endpoints is not None:
            self.endpoint_timer.start()
        else:
            self.endpoint_timer.stop()

    def _default_loopback_label(self) -> str:
        """"Default playback device", and which one of them it is at the moment."""
        plain = _("Default playback device")
        try:
            index = audio_mod.default_loopback_index()
        except Exception:  # noqa: BLE001 - somebody else's audio stack
            return plain
        for device in self.loopbacks:
            if device.index == index:
                return _("Default playback device ({name})").format(name=device.label)
        return plain

    def _loopback_index(self) -> int:
        """The endpoint to capture: the chosen one, or whatever Windows is using.

        The default is resolved by **name** through `endpoints`, and only falls back to
        PortAudio's own idea of the default. The two can disagree, and PortAudio's
        answer costs 17 ms and a start-stop of PortAudio underneath a running capture -
        measured on 2026-11-13 against 1.3 ms for the name.
        """
        chosen = self._chosen_loopback_index()
        if chosen >= 0:
            return chosen
        if self.endpoints is not None:
            index = self._loopback_index_named(self.endpoints.default_name())
            if index >= 0:
                return index
        return audio_mod.default_loopback_index()

    def _chosen_loopback_index(self) -> int:
        """The endpoint the user picked, by name, or -1 for "follow Windows".

        The name wins over the saved index whenever there is one: PortAudio renumbers
        its devices whenever the set of them changes, so the index alone is a promise
        it cannot keep. A settings file written before the name existed has only the
        index, and that is still honoured - but a name that no longer matches anything
        means the device has gone, and -1 sends the caller to the current default
        rather than to whatever now happens to sit at that number.
        """
        settings = self.settings
        label = getattr(settings, "system_audio_label", "")
        if label:
            return audio_mod.index_for_label(label, self.loopbacks)
        if settings.system_audio_device >= 0:
            # An older settings file. Give it a name now, so the next renumbering
            # cannot take the device away again.
            name = audio_mod.label_for_index(
                settings.system_audio_device, self.loopbacks
            )
            if name:
                settings.system_audio_label = name
            return settings.system_audio_device
        return -1

    def _loopback_index_named(self, name: str) -> int:
        """The PortAudio index of the endpoint Windows calls `name`, or -1.

        Matched against the **live** device list. PortAudio enumerates once, when it is
        initialised, and the recording and the meters hold it open for their whole
        lives - so a device that appeared after that is not in the list and cannot be
        captured until the next time PortAudio is started. That is a real limit and the
        caller is told about it by the -1 rather than being given a wrong index.
        """
        if not name:
            return -1
        try:
            for device in audio_mod.list_loopback_devices():
                if device.label == name:
                    return device.index
        except Exception:  # noqa: BLE001 - somebody else's audio stack
            return -1
        return -1

    def _loopback_name(self) -> str:
        """The Windows name of the endpoint currently being captured, or ""."""
        chosen = self._chosen_loopback_index()
        index = chosen if chosen >= 0 else self._loopback_index()
        return audio_mod.label_for_index(index)

    # ------------------------------------------------- where the sound goes

    def _tick_endpoints(self) -> None:
        """Every `ENDPOINT_MS`: is the sound still going where we are recording?"""
        if self.endpoints is None or not self.settings.system_audio_enabled:
            return
        # "Follow Windows" is the absence of a chosen device, and a chosen device
        # that has gone is the same thing again - there is nothing left to be loyal to.
        if self._chosen_loopback_index() < 0:
            self._follow_default()
        else:
            self._advise_on_silence()

    def _follow_default(self) -> None:
        """Move the capture to whatever Windows is now using for playback.

        Only when the user asked for "the default" - a named device is a decision and
        is never moved underneath them. The recording is not interrupted:
        `LoopbackFeeder.switch_to` reopens the capture stream and leaves the pipe
        alone, and the silence padding covers the gap.
        """
        wanted = self.endpoints.default_name()
        if not wanted or wanted == self._capturing_name:
            return
        index = self._loopback_index_named(wanted)
        if index < 0:
            # It is a device PortAudio has not enumerated - plugged in after it
            # started. Nothing can be captured from it until the next launch, and
            # saying so beats recording the old one in silence.
            self.endpoints.refresh()
            self._say(
                lambda name=wanted: _(
                    "Sound moved to “{name}”, which this recording cannot follow. "
                    "Restart ScreenRec to record it."
                ).format(name=name),
                warn=True,
            )
            self._capturing_name = wanted
            return
        was = self._capturing_name
        self._capturing_name = wanted
        feeder = getattr(getattr(self.session, "recorder", None), "feeder", None)
        moved = feeder.switch_to(index) if feeder is not None else False
        self._start_meters()
        # `_start_meters` re-reads the name; keep the one just decided on.
        self._capturing_name = wanted
        if was and moved:
            self._say(
                lambda name=wanted: _(
                    "Sound moved to “{name}”; the recording moved with it."
                ).format(name=name)
            )

    def _advise_on_silence(self) -> None:
        """A named device is silent while another one is playing. Say so, once.

        Said only while recording. Outside a take the two meters on the bar show the
        same thing without a sentence, and a bar that comments on every sound the
        machine makes is a bar nobody reads.
        """
        if not self._is_recording():
            return
        chosen = self._loopback_name()
        peaks = self.endpoints.peaks()
        if not chosen or peaks.get(chosen, 0.0) >= endpoints_mod.AUDIBLE_PEAK:
            self._advised_name = ""
            return
        loud, _peak = self.endpoints.loudest(ignoring=chosen)
        if not loud or loud == self._advised_name:
            return
        self._advised_name = loud
        self._say(
            lambda where=loud, here=chosen: _(
                "The sound is going to “{where}”, but “{here}” is being recorded."
            ).format(where=where, here=here),
            warn=True,
        )

    def _tick_levels(self) -> None:
        self.bar.set_mic_level(self.meters.mic_level())
        self.bar.set_system_level(self.meters.system_level())

    # ------------------------------------------------------------ recording

    def _resolve_source(self) -> recorder.VideoSource | None:
        if not self.monitors:
            return None
        kind = self.settings.source_kind
        if kind == config_mod.SOURCE_REGION:
            # The guard that stops `recorder.surface_crop` raising out of every path
            # that resolves a source - the gear and the record button both come
            # through here. See `config.is_a_rectangle`.
            if not config_mod.is_a_rectangle(self.settings.region):
                return None
            x, y, width, height = self.settings.region
            monitor = winapi.monitor_at(self.monitors, x, y)
            if monitor is None:
                return None
            return recorder.VideoSource(
                monitor=monitor, crop=(x, y, width, height), name="region"
            )
        if kind == config_mod.SOURCE_WINDOW:
            hwnd = getattr(self.settings, "window_hwnd", 0)
            if not hwnd or not winapi.window_is_alive(int(hwnd)):
                return None
            rect = winapi.window_rect(int(hwnd))
            if rect is None:
                return None
            x, y, width, height = rect
            monitor = winapi.monitor_at(
                self.monitors, x + width // 2, y + height // 2
            )
            if monitor is None:
                return None
            return recorder.VideoSource(
                monitor=monitor, crop=(x, y, width, height), name="window"
            )
        monitor = self._chosen_monitor()
        if monitor is None:
            return None
        return recorder.VideoSource(monitor=monitor, name=monitor.device_name)

    def _resolve_sources(self) -> list[recorder.VideoSource]:
        """Every screen this recording covers.

        One for the ordinary modes; one per monitor when "every screen" is chosen. The
        list is in monitor order so the first one - which carries the sound - is
        predictable.
        """
        if self.settings.source_kind != config_mod.SOURCE_ALL_SCREENS:
            single = self._resolve_source()
            return [single] if single is not None else []
        return [
            recorder.VideoSource(monitor=monitor, name=monitor.device_name)
            for monitor in sorted(self.monitors, key=lambda m: m.index)
        ]

    def _encoder_for(
        self,
        profile: config_mod.Profile,
        source: recorder.VideoSource | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        """Which ffmpeg encoder this profile records with here, and its extra arguments.

        `source` is the screen or rectangle about to be recorded, and it is given so
        that a frame below the chosen encoder's floor can be moved to the processor -
        see `_encoder_for_frame`. Without it the answer is the profile's alone, which
        is what the settings panel and the size estimate want.

        The decision itself is `capabilities.choose`, and all of it: the profile names a
        codec, the probe says which families this machine really ran, and §2 of
        `docs/ENCODERS.md` ranks them by how far the frame has to travel. What stood
        here until 2026-11-04 was an override that read `capabilities.best()` and
        applied it only when `pix_fmt == "yuv420p"`, which had two consequences and both
        were real: the profile literally called "No GPU - x264" was recorded on NVENC,
        and the 4:4:4 profile kept an encoder that cannot do 4:4:4.

        A manual choice stays the interface's business. `encoder_choice` is anything but
        "auto" only because somebody named an encoder, and a named encoder is used as
        named; `choose` has no override parameter and should not grow one.

        The profile's `extra` is dropped when the chosen encoder does not encode the
        profile's codec, **on the manual path as well as the automatic one**. Measured
        on the bundled build, 2026-11-04: `hevc444` carries `-profile:v rext`, and
        `libx264 -profile:v rext` - which is exactly what that profile is given on a
        machine with no NVENC, and exactly what `encoder_choice="libx264"` asked for -
        refuses to open the encoder at all (`x264 [error]: invalid profile: rext`, exit
        127). Arguments written for one codec are not carried into another, however the
        codec was arrived at. Until 2026-11-05 the manual branch returned
        `profile.extra` untouched and rebuilt that failure by hand.

        A name nobody has measured is not a choice, it is a typo: `encoders.codec_args`
        raises `UnknownEncoder` on it at record time, which reaches the user as a raw
        English exception on the bar. `SettingsPanel._chosen_encoder` has always fallen
        back to the probe for an unrecognised name; this does the same, so the panel and
        the recorder describe one recording rather than two.
        """
        chosen = self._named_encoder()
        if chosen:
            extra = (
                profile.extra
                if encoders_mod.codec_of(chosen) == profile.codec else ()
            )
            return self._encoder_for_frame(chosen, extra, profile, source)
        name, reason = capabilities.choose(self.encoders, profile)
        self._note_encoder(name, reason, profile)
        extra = profile.extra if encoders_mod.codec_of(name) == profile.codec else ()
        return self._encoder_for_frame(name, extra, profile, source)

    def _encoder_for_frame(
        self,
        name: str,
        extra: tuple[str, ...],
        profile: config_mod.Profile,
        source: recorder.VideoSource | None,
    ) -> tuple[str, tuple[str, ...]]:
        """Move a frame the chosen encoder will not take onto the processor.

        **The defect this closes.** A rectangle small enough to be refused could be
        dragged out, saved and recorded with nothing said: measured on this machine on
        2026-11-05 through the real `ddagrab -> encoder` pipeline, `h264_nvenc` refuses
        anything under 146 wide or 50 high and `hevc_nvenc` under 130 by 34, exiting
        non-zero with `Nothing was written into output file`. `config.MIN_FRAME` holds
        the measurement and how it was taken.

        **Why the processor and not a refusal at the moment of dragging.** The floor
        belongs to the encoder, and the only encoders anybody on this project can
        measure are NVIDIA's: `hevc_mf` and `h264_mf` return NVIDIA's own numbers here
        because Media Foundation dispatches to `NVIDIA HEVC Encoder MFT`
        (docs/ENCODERS.md §3), and there is no AMD or Intel hardware on this desk at
        all. A selector that refused a rectangle on the strength of 146x50 would be
        refusing it on every machine on the strength of one graphics card - the exact
        mistake `docs/ENCODERS.md` §1 is written against. The cost of going the other
        way is measured and small: `libx264` encoded every shape down to 2x2, and a
        144x48 rectangle for 6 s cost ffmpeg 0.55 s of processor - 8.6% of one core,
        against 95.1% for a whole 2560x1440 screen on the same encoder and the same
        near-static desktop. So the rectangle records, on the one encoder measured to
        take any size, and the bar says so.

        **It applies to a hand-named `encoder_choice` as well.** "A named encoder is
        used as named" is still the rule for every choice the machine can honour; a
        name that physically will not take this frame is not a choice that can be
        honoured, and the alternative is a recording that fails after the record
        button with the user never told why.

        Said once per take, not once per segment - see `_note_small_frame`, which owns
        the guard that makes that true.
        """
        if source is None:
            return name, extra
        try:
            width, height = source.output_size()
        except ValueError:
            # Not a rectangle at all. `_resolve_source` refuses that one first; this
            # is here so that planning can never be what raises.
            return name, extra
        floor_width, floor_height = config_mod.min_frame(name)
        if width >= floor_width and height >= floor_height:
            return name, extra

        # `("hevc", "cpu")` has no name at all - `libx265` is not a candidate - so
        # this is deliberately asked for H.264 and not for the profile's codec.
        processor = encoders_mod.name_for("h264", "cpu")
        usable = {result.name for result in capabilities.working(self.encoders)}
        if not processor or (self.encoders and processor not in usable):
            # Nothing to move it to. Say so rather than let ffmpeg fail in English
            # after the user has pressed record.
            self._note_small_frame(
                width, height, name,
                lambda: _(
                    "{width}×{height} is smaller than {encoder} will encode, and no "
                    "encoder here can take it. Choose a bigger region."
                ).format(width=width, height=height, encoder=name),
            )
            return name, extra

        self._note_small_frame(
            width, height, name,
            lambda: _(
                "{width}×{height} is smaller than {encoder} will encode — "
                "recording it on the processor instead."
            ).format(width=width, height=height, encoder=name),
        )
        kept = (
            profile.extra
            if encoders_mod.codec_of(processor) == profile.codec else ()
        )
        return processor, kept

    def _note_small_frame(
        self, width: int, height: int, name: str, message: Callable[[], str]
    ) -> None:
        """Say it, and hold it so the start's own line does not run over it.

        Its own guard, not `_note_encoder`'s. The two sentences answer different
        questions and are worth repeating at different rates: "nvenc, amf, qsv and mf
        did not work here" is a fact about the machine and does not change between
        takes, while "this rectangle is below the floor" is a fact about the rectangle
        the user just dragged. `_really_start` clears this one and not that one.
        """
        reason = f"{name}: frame {width}x{height} is below its floor"
        if reason == self._frame_note:
            return
        self._frame_note = reason
        self._pending_note = message
        self._say(message, warn=True)

    def _named_encoder(self) -> str:
        """The encoder somebody wrote into the settings file, or "" for "let it choose".

        `Settings.encoder_choice` is the JSON-only escape hatch it is documented as.
        "auto" and "" both mean the probe decides, and so does anything
        `encoders.family_of` does not recognise - the same rule, spelt the same way, as
        `SettingsPanel._chosen_encoder`.
        """
        wanted = getattr(self.settings, "encoder_choice", "auto")
        if not isinstance(wanted, str) or wanted in ("", "auto"):
            return ""
        return wanted if encoders_mod.family_of(wanted) else ""

    def _estimate(self) -> storage.Estimate:
        """What a minute of the current settings costs here, measured where it can be.

        `config.estimate_mb_per_minute`, which stood here until 2026-11-05, is the
        `config.PROFILES` table, and that table was measured on this machine's NVENC.
        On a machine recording through Media Foundation `docs/ENCODERS.md` §3 puts the
        real figure at up to 4.9x it, so the free-space check was reading somebody
        else's number. `storage.size_estimate` returns this machine's own figure once it
        has one and falls back to the same table until then, so nothing is worse off.

        It reads `sizes.json` on every call, and `_watch_space` calls it five times a
        second while a recording runs. Measured here on 2026-11-05, 2000 calls each:
        12.9 us when the file does not exist, 67.9 us when it holds twelve samples,
        against 0.2 us for `config.estimate_mb_per_minute`. At five calls a second that
        is a third of a millisecond a second at worst, beside the free-space query
        `storage.check` was already making on the same tick.
        """
        settings = self.settings
        width, height = self._capture_size()
        return storage.size_estimate(
            settings.profile, settings.quality_level, width, height,
            settings.framerate,
            encoder=(self._named_encoder() or self._chosen_encoder_name()),
        )

    def _chosen_encoder_name(self) -> str:
        """`capabilities.choose`'s answer with nothing said about it.

        `_encoder_for` is the planning path and announces a surprising answer on the
        bar. The size estimate asks the same question several times a second while a
        recording runs, and must not.
        """
        if not self.encoders:
            return ""
        name, _reason = capabilities.choose(self.encoders, self.settings.encoding())
        return name

    def _note_encoder(
        self, name: str, reason: str, profile: config_mod.Profile
    ) -> None:
        """Say out loud when the encoder is not the one the profile promised.

        Not on every take - only when the answer is a surprise. On this machine the
        answer for the default profile is `hevc_nvenc`, which is what its label says it
        is, and announcing it before every recording is noise. Two answers are worth a line, because a user would otherwise
        find out from the file: the recording is in a different codec from the one the
        profile's label names, or a profile that never asked for the processor has ended
        up on it. H.265 through AMF instead of NVENC records exactly what the profile
        says it records, so it stays quiet.

        `capabilities.choose`'s reason is English and is assembled at run time out of
        family names, so it cannot be a catalogue key; the sentence around it is
        translated and carries the reason inside it as the detail. The name is stripped
        off the front of it, because the sentence already says the name.
        """
        surprising = encoders_mod.codec_of(name) != profile.codec or (
            encoders_mod.family_of(name) == "cpu" and not profile.software_only
        )
        if not surprising or reason == self._encoder_note:
            return
        self._encoder_note = reason
        head = f"{name}: "
        detail = reason[len(head):] if reason.startswith(head) else reason

        def message() -> str:
            return _("Recording with {encoder} — {reason}").format(
                encoder=name, reason=detail
            )

        # Said now - planning happens before the start has anything to say - and kept,
        # because the start says its own line one statement later and `Bar.set_status`
        # has no queue and no dwell. Until 2026-11-05 this sentence was on screen for
        # the length of one function call on every path that could produce it.
        self._pending_note = message
        self._say(message, warn=True)

    def _flush_encoder_note(self) -> None:
        """Put the encoder's sentence back on the bar, after the start's own line.

        The start's line names the file, which the bar shows again when the recording
        finishes. The encoder's line is said once and is the only place a user is told
        their recording is not in the codec the format's label promised, so it is the
        one that stays.
        """
        note, self._pending_note = self._pending_note, None
        if note is not None:
            self._say(note, warn=True)

    def _loopback_pipe(self, segment: Path) -> str:
        r"""The named pipe this segment's system sound travels down.

        A name of this recording's own, and of this segment's within it. Until
        2026-11-05 the tag was the tail of the segment's stem - `segment.stem[-6:]`
        here and `[-8:]` on the all-screens path - and every take's first segment is
        `....part00`, so the tag was a constant. Reproduced on 2026-11-05 by planning
        two takes five minutes apart in one process and printing the name: both asked
        for `\\.\pipe\screenrec_22980_part00`, and both all-screens plans for
        `\\.\pipe\screenrec_22980_0.part00`. Two recordings in one session collide
        there by design, and the pipe belongs to whichever of them created it first.

        The segment's part number stays in the name, because within one take the
        segments are consecutive and a name of their own is what keeps a resumed
        segment off the pipe the paused one left behind.
        """
        stem = segment.stem
        part = stem.rsplit(".part", 1)[-1] if ".part" in stem else stem
        return recorder.pipe_name(f"{self._take_tag}_{part}")

    def _plan_for_source(
        self, source: recorder.VideoSource, segment: Path, with_audio: bool
    ) -> recorder.RecordingPlan | None:
        """One screen's plan. Only one screen in a take carries the sound."""
        settings = self.settings
        profile = settings.encoding()
        encoder, extra = self._encoder_for(profile, source)
        mic = settings.mic_device if (with_audio and settings.mic_enabled) else ""
        loopback = (
            self._loopback_pipe(segment)
            if (with_audio and settings.system_audio_enabled) else ""
        )
        return recorder.RecordingPlan(
            sources=[source],
            outputs=[segment],
            encoder=encoder,
            pix_fmt=profile.pix_fmt,
            encoder_extra=extra,
            quality=settings.cq(),
            quality_level=settings.quality_level,
            framerate=settings.framerate,
            max_height=settings.max_height,
            draw_mouse=settings.draw_mouse,
            layout=recorder.LAYOUT_SINGLE,
            mic_device=mic,
            loopback_pipe=loopback,
            audio_mode=settings.audio_mode,
            audio_offset_ms=settings.audio_offset_ms,
        )

    def _plan_for(self, segment: Path) -> recorder.RecordingPlan | None:
        source = self._resolve_source()
        if source is None:
            return None
        settings = self.settings
        profile = settings.encoding()
        encoder, extra = self._encoder_for(profile, source)
        return recorder.RecordingPlan(
            sources=[source],
            outputs=[segment],
            encoder=encoder,
            pix_fmt=profile.pix_fmt,
            encoder_extra=extra,
            quality=settings.cq(),
            quality_level=settings.quality_level,
            framerate=settings.framerate,
            max_height=settings.max_height,
            draw_mouse=settings.draw_mouse,
            layout=recorder.LAYOUT_SINGLE,
            mic_device=settings.mic_device if settings.mic_enabled else "",
            loopback_pipe=(
                self._loopback_pipe(segment)
                if settings.system_audio_enabled else ""
            ),
            audio_mode=settings.audio_mode,
            audio_offset_ms=settings.audio_offset_ms,
        )

    def toggle_recording(self) -> None:
        if self.multi is not None and self.multi.active:
            self.stop_recording()
            return
        if self.countdown_timer.isActive() or self.countdown.running:
            self.countdown_timer.stop()
            self.countdown.cancel()
            self._pending_start = False
            self.bar.set_countdown(0)
            self.bar.set_recording(False)
            self._say(lambda: _("Countdown cancelled."))
            return
        if self.session is not None and self.session.active:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if not self._ffmpeg:
            self._check_ffmpeg()
            if not self._ffmpeg:
                return
        if self._probing:
            # `_probe_step` pumps the event queue, so this button is live while the
            # probe runs. Planning a recording now would plan it from half an answer.
            self._say(
                lambda: _(
                    "Still checking what this machine can encode - one moment."
                ),
                warn=True,
            )
            return
        if not self._probed:
            # Pressed before the event loop got to the probe, or ffmpeg only turned up
            # at this click. A pause here beats a recording planned from no answer at
            # all, which `capabilities.choose` would have to answer with libx264.
            self._probe_encoders()
        if self._resolve_source() is None:
            self._say(lambda: _("Choose something to record first."), warn=True)
            self.open_source_picker()
            return

        space = storage.check(
            self.settings.resolved_output_dir(), self._estimate().mb_per_minute
        )
        warning = storage.warning_for(space)
        if warning:
            self._say(warning, warn=True)

        if self.settings.countdown > 0:
            self.countdown_left = self.settings.countdown
            self._pending_start = True
            self.bar.set_countdown(self.countdown_left)
            source = self._resolve_source()
            # On the screen that is about to be recorded, and excluded from the capture
            # so it is not in the file. A countdown in a tooltip is not a countdown.
            self.countdown.start(
                self.countdown_left, source.monitor if source else None
            )
            self.countdown_timer.start()
            return
        self._really_start()

    def _countdown_tick(self) -> None:
        """Keep the bar's own label in step; the ring runs on its own timer."""
        self.countdown_left -= 1
        self.bar.set_countdown(max(0, self.countdown_left))
        if self.countdown_left <= 0:
            self.countdown_timer.stop()

    def _hide_from_capture(self) -> None:
        """Take the program's own windows out of every screen capture, for the take.

        They come back in `_hide_overlays`, which every path that ends a take goes
        through. **Called once a take has really started, never before it.** It used to
        run at the top of `_really_start`, before the session existed, and nothing put
        it back when the start failed. On 2026-11-13 a start refused by a dead sound
        device left the bar invisible to every capture on the machine for the rest of
        the session: the user could see the error it was showing but could not
        photograph it, and had to take a picture of the screen with a phone to show
        anyone. Every later take fixed it by accident, and every later failure made it
        worse.
        """
        surfaces.set_recording(True)
        if self.panel is not None:
            # The gear is on the bar, so the window can be open when a take begins.
            self.panel.set_recording(True)

    def _clear_the_way(self) -> bool:
        """Whether a new take may begin. Says why when it may not.

        The old guard was `if self.session is not None and self.session.active:
        return`, and it read both sessions' `active` as proof that a take is running.
        Today that is sound - `RecordingSession.active` is `recording or paused` by
        definition, and `MultiSession.active` is the same over its screens - so a
        session that only *claims* to be running cannot occur, and the last branch
        here is reached by nothing in this build.

        It is written anyway, because the cost of being wrong is not symmetrical. A
        guard that returns in silence turns any future mistake about what `active`
        means into a record button that does nothing and says nothing, which is the
        single hardest symptom to diagnose in this program - it took most of a day on
        2026-11-13 to rule out, and the actual cause was elsewhere entirely (see
        `Recorder.start`). Running and paused takes are still left alone in silence,
        because the ring and its tick really do both arrive; anything else is said.
        """
        session = self.session or self.multi
        if session is None:
            return True
        if session.recording or session.paused:
            return False  # a real take; the ring and the tick both arriving is normal
        if not session.active:
            return True  # finished and not cleared up; nothing to say about it
        self.session = None
        self.multi = None
        surfaces.set_recording(False)
        if self.panel is not None:
            self.panel.set_recording(False)
        self._say(
            lambda: _("The last recording did not end cleanly. Starting a new one."),
            warn=True,
        )
        return True

    def _say_dropped_audio(self, session) -> None:
        """Say, as the take begins, that it is going out without system sound.

        `Recorder.start` drops the system-sound track rather than refusing to record
        when its device will not open. That is the right trade - a recording missing
        one track beats no recording - but it has to be said at once, while the user
        can still stop and fix it, and not only when the file is saved.
        """
        reason = getattr(session, "dropped_audio", "")
        if not reason:
            return
        code = getattr(session, "dropped_reason", "")
        self._say(lambda: self._audio_reason(code, reason), warn=True)

    @staticmethod
    def _audio_reason(code: str, english: str) -> str:
        """A translated sentence for one of `audio.REASON_*`, or the English one.

        `audio.py` and `levels.py` are deliberately Qt-free and so cannot reach
        `i18n._()`; their sentences arrive here in English and are turned round here.
        That hole is how a Russian bar came to read `No system-sound device available:
        [Errno -9996] Invalid device info` on 2026-11-13. A reason with no sentence
        below falls through to the English, which is worse than a translation and much
        better than nothing.
        """
        sentences = {
            audio_mod.REASON_NO_DEVICE: lambda: _(
                "Windows is not offering a playback device that can be recorded, so "
                "this recording has no system sound - the picture and the microphone "
                "are not affected. A playback device has usually just gone away, a "
                "headset switching off for instance. Choose one in Settings, Sound."
            ),
            audio_mod.REASON_NO_PYAUDIO: lambda: _(
                "System sound cannot be captured: the sound library is missing from "
                "this copy of ScreenRec. The picture and the microphone are not "
                "affected."
            ),
            audio_mod.REASON_FFMPEG_TIMEOUT: lambda: _(
                "System sound did not start in time, so this recording has no system "
                "sound - the picture and the microphone are not affected. Start the "
                "recording again, or switch system sound off."
            ),
            audio_mod.REASON_PIPE_REFUSED: lambda: _(
                "The channel ScreenRec uses for system sound is held by something "
                "else, so this recording has no system sound. Close any other copy of "
                "ScreenRec; if there is none, restart ScreenRec."
            ),
            audio_mod.REASON_PIPE_FAILED: lambda: _(
                "The channel for system sound could not be opened, so this recording "
                "has no system sound. Restart ScreenRec, or record with system sound "
                "switched off."
            ),
            audio_mod.REASON_FELL_BACK: lambda: _(
                "The playback device chosen for system sound was not available, so "
                "the current default one was recorded instead."
            ),
            audio_mod.REASON_CAPTURE_FAILED: lambda: _(
                "Capturing system sound failed, so this recording has no system "
                "sound - the picture and the microphone are not affected."
            ),
        }
        make = sentences.get(code)
        return make() if make is not None else english

    def _really_start(self) -> None:
        if not self._clear_the_way():
            return
        self._pending_start = False
        self.countdown.cancel()
        self.bar.set_countdown(0)
        self._space_warned = False
        self._silence_warned = False
        # A take gets its own chance to report a misrouted device: the one before it
        # may have been told about a different one.
        self._advised_name = ""
        self._pending_note = None
        # One take, one small-frame sentence. Until 2026-11-05 `_note_small_frame`
        # shared `_note_encoder`'s guard, which is cleared only in `__init__` and after
        # the probe, so it spanned the whole run of the program: measured here with the
        # same 100x60 region recorded twice in one process, take one said "100x60 is
        # smaller than hevc_nvenc will encode - recording it on the processor instead."
        # and take two said nothing at all, while being just as much on the processor.
        self._frame_note = ""
        self._silence_warned = False
        self._auto_stop_left = 0
        # Whatever was still waiting its turn belongs to the take that ended. The line
        # the user just asked for shows now, not behind a queue.
        self._clear_status_queue()
        when = datetime.now()
        final = recorder.output_path(self.settings, when)
        # One tag per recording, for `_loopback_pipe`. The clock alone is not enough:
        # two takes can start inside the same second, and the counter settles it.
        self._takes_started += 1
        self._take_tag = f"{when:%H%M%S}_{self._takes_started}"

        if self.settings.source_kind == config_mod.SOURCE_ALL_SCREENS:
            self._start_all_screens(final)
            return

        self.session = recorder.RecordingSession(self._ffmpeg, final, self._plan_for)
        problem = self.session.start(
            on_progress=self._progress_ready.emit,
            loopback_device=(
                self._loopback_index() if self.settings.system_audio_enabled else -1
            ),
        )
        if problem:
            self.session = None
            self._say(problem, warn=True)
            self.bar.set_recording(False)
            return

        self._hide_from_capture()
        self._show_overlays()
        self.bar.set_recording(True)
        self.tray.set_recording(True)
        self._say(lambda: _("Recording to {name}").format(name=final.name))
        self._say_dropped_audio(self.session)
        self._flush_encoder_note()
        self.timer.start()
        if self.settings.auto_stop_minutes > 0:
            self.auto_stop_timer.start(self.settings.auto_stop_minutes * 60_000)

    def _start_all_screens(self, final: Path) -> None:
        """Every monitor, one ffmpeg process each.

        One process with several ddagrab inputs looked like the tidier answer and was
        measured twice: it costs frames (53.4 and 59.2 fps against 59.8 for one screen)
        AND landed the two files 50 ms apart, while a process per screen held 59.7 and
        59.1 and landed them 17 ms apart - one frame. So: one each.

        **The 17 ms is no longer what this does.** `Recorder.start` waits
        `recorder.START_GRACE` - 0.35 s on a *successful* start, so that an ffmpeg
        which dies immediately is caught before the bar says "recording" - and the
        loop below pays it once per screen, before the next one is launched.
        Re-measured on 2026-11-05 by timestamping the `ddagrab` `Popen` calls through
        this very method, on this desk's two monitors: the two processes go up **359.7
        ms apart** as shipped and 4.0 ms apart with `START_GRACE` set to 0, and this
        method takes 0.72 s against 0.01 s. The sidecar tells the player the screens
        are simultaneous, so that gap is 21 frames at 60 fps - worse than the single
        process rejected above. The grace lives in `recorder.py` and is not this
        module's to move, but the number here is this module's to keep true.
        """
        self._pending_note = None
        sources = self._resolve_sources()
        if len(sources) < 2:
            self._say(lambda: _("There is only one screen to record."), warn=True)
            self.settings.source_kind = config_mod.SOURCE_MONITOR
            self._refresh_bar()
            return

        session = multi_mod.MultiSession(self._ffmpeg, final, self._plan_for_source)
        for source in sources:
            session.add(source)
        problem = session.start(
            on_progress=self._progress_ready.emit,
            loopback_device=(
                self._loopback_index() if self.settings.system_audio_enabled else -1
            ),
        )
        if problem:
            self._say(problem, warn=True)
            self.bar.set_recording(False)
            return
        self.multi = session
        self._hide_from_capture()
        self._show_overlays()
        self.bar.set_recording(True)
        self.tray.set_recording(True)
        self._say_dropped_audio(session)
        count = len(sources)
        self._say(
            lambda: _("Recording {count} {word} to {pattern}").format(
                count=count,
                word=_screens_word(count),
                pattern=f"{final.stem}_screen*.mp4",
            )
        )
        self._flush_encoder_note()
        # Last, and queued: each of these is a screen that did not start the way it
        # was asked to, and said straight through `_say` they would have run over one
        # another and then over the line above. See `_say_each`.
        self._say_each([(note, True) for note in session.notes])
        self.timer.start()
        if self.settings.auto_stop_minutes > 0:
            self.auto_stop_timer.start(self.settings.auto_stop_minutes * 60_000)

    def _stop_all_screens(self) -> None:
        session, self.multi = self.multi, None
        if session is None:
            return
        paths_written, problem = session.stop()
        self.bar.set_recording(False)
        self.tray.set_recording(False)
        # Said straight through `_say` until 2026-11-05, and `Bar.set_status` has no
        # queue: measured on this path with two screens saved and screen 1's ffmpeg
        # dead, "screen 1: ffmpeg exited with code 1" was replaced by "Saved 2 screens
        # - 4 MB..." in the same breath and the reason was never readable.
        reasons: list[tuple[str | Callable[[], str], bool]] = (
            [(problem, True)] if problem else []
        )
        if not paths_written:
            self._say_now_then_queue(reasons)
            return
        self.last_file = paths_written[0]
        self.last_set = session.sidecar_path()
        self._refresh_bar()
        total = sum(p.stat().st_size for p in paths_written if p.exists()) / 1e6
        count = len(paths_written)
        self._say(
            lambda: _(
                "Saved {count} {word} \u2014 {size} MB. "
                "Click the file name below to watch them together."
            ).format(count=count, word=_screens_word(count), size=f"{total:.0f}")
        )
        action_problem = shellops.run_action(
            self.settings.after_recording, paths_written[0]
        )
        if action_problem:
            reasons.append((action_problem, True))
        # Last, and queued: the file names are the answer to the button and show now;
        # each reason gets its turn after them, and the last one stays on the bar.
        self._say_each(reasons)

    def toggle_pause(self) -> None:
        """Pause the take that is running, or carry on with the one that is paused.

        **What is painted comes from `take.recording`, not from the take's own
        `paused` flag and not from what was asked of it.** Measured on 2026-11-05
        against the real classes: after a refused `MultiSession.resume` - which a
        settings change during an all-screens pause now produces, because every
        screen's `_begin_segment` refuses - `multi.paused` is `False` while
        `multi.recording` is `False` too. The flag is cleared over the refusal it has
        just collected and only `recording` still tells the truth. Reading the flag
        is what left the clock running, the bar saying "recording" and the tray
        agreeing with it over a take that was still paused on disk; and the next
        press read the same flag, called `pause()` and got "Nothing is recording.",
        so the button alternated between those two and could never resume the take.

        The other half is a pause that reports a problem. `RecordingSession.pause`
        sets its flag *before* it returns the problem - measured: `pause()` on a
        session whose ffmpeg stop answers "ffmpeg exited with code 1" leaves
        `paused=True recording=False` - so the branch that returned early on a
        problem left the clock ticking over a take that really had stopped.
        """
        take = self.multi if self.multi is not None and self.multi.active else None
        if take is None:
            if self.session is None or not self.session.active:
                return
            take = self.session
        was_recording = take.recording
        problem = take.pause() if was_recording else take.resume()
        self._show_pause_state(take.recording, was_recording, problem)

    def _show_pause_state(
        self, recording: bool, was_recording: bool, problem: str
    ) -> None:
        """Make the clock, the bar and the tray agree with the take.

        Nothing moves when the take did not. A second `_hold_auto_stop` would read
        `remainingTime()` on a timer that is already stopped, which answers -1, and
        throw away the minutes the first pause banked - so a refused resume would
        cost the take the rest of its "stop after N minutes".
        """
        if recording == was_recording:
            if problem:
                self._say(problem, warn=True)
            return
        if recording:
            self.timer.start()
            self._resume_auto_stop()
        else:
            self.timer.stop()
            self._hold_auto_stop()
        self.bar.set_paused(not recording)
        self.tray.set_recording(True, paused=not recording)
        if problem:
            self._say(problem, warn=True)
        elif recording:
            self._say(lambda: _("Recording again."))
        else:
            # Until 2026-11-05 the all-screens branch had no sentence at all: pausing
            # one screen said "Paused - the clock has stopped." and pausing every
            # screen said nothing, on the same button.
            self._say(lambda: _("Paused - the clock has stopped."))

    def _auto_stop(self) -> None:
        """"Stop after N minutes" has come round.

        `self.session` is one of the two ways a recording runs. Until 2026-11-05 it
        was the only one this asked about, while `_start_all_screens` started the very
        same timer - so on an all-screens take the timer fired and this did nothing at
        all. Reproduced on 2026-11-05 with a live `multi` and no `session`: the timeout
        reached here and `stop_recording` was called 0 times.
        """
        if not self._is_recording():
            return
        minutes = self.settings.auto_stop_minutes
        self._say(
            lambda: _("Stopped automatically after {count} {word}.").format(
                count=minutes, word=_minutes_word(minutes)
            )
        )
        self.stop_recording()

    def _is_recording(self) -> bool:
        """Either kind of take, running. One screen or all of them is still a take."""
        return (self.session is not None and self.session.active) or (
            self.multi is not None and self.multi.active
        )

    def _hold_auto_stop(self) -> None:
        """Stop the auto-stop clock for the pause, keeping what is still owed.

        "Stop after 30 minutes" means thirty minutes of recording, not thirty minutes
        of wall clock. Until 2026-11-05 `toggle_pause` did not mention this timer at
        all - the word `auto_stop_timer` does not appear in it - so a take paused for
        ten minutes of a thirty-minute limit stopped with twenty minutes of video.
        """
        left = self.auto_stop_timer.remainingTime()
        self.auto_stop_timer.stop()
        # QTimer.remainingTime() answers -1 when the timer is not running, which is
        # every take started with "Stop after" switched off.
        self._auto_stop_left = left if left > 0 else 0

    def _resume_auto_stop(self) -> None:
        """Start the clock again on what was owed when the pause began."""
        left, self._auto_stop_left = self._auto_stop_left, 0
        if left > 0:
            self.auto_stop_timer.start(left)

    def stop_recording(self) -> None:
        self.timer.stop()
        self.auto_stop_timer.stop()
        self._auto_stop_left = 0
        self._hide_overlays()
        if self.multi is not None:
            self._stop_all_screens()
            return
        session, self.session = self.session, None
        if session is None:
            self.bar.set_recording(False)
            return

        path, problem = session.stop()
        self.bar.set_recording(False)
        self.tray.set_recording(False)
        # The diagnosis of a take that went wrong, and both lines were said straight
        # through `_say`, which has no queue. Measured on this path on 2026-11-05:
        # "No sound at all arrived from “Headphones”.", then "ffmpeg exited with
        # code 1", then "Saved take.mp4 — 0 MB" - all three in 1.17 ms, and what was
        # left on the bar was the "Saved" line. `feeder_note` is `audio.py`'s own
        # finding that there is no sound in the recording at all, and it held the bar
        # for microseconds. See `_say_each`.
        note = getattr(session, "feeder_note", "")
        if note:
            # Said in the user's language where `audio.py` gave a reason code for it;
            # that module is Qt-free and cannot translate its own sentences.
            note = self._audio_reason(getattr(session, "feeder_reason", ""), note)
        reasons: list[tuple[str | Callable[[], str], bool]] = [
            (line, True) for line in (note, problem) if line
        ]
        if path is None:
            self._say_now_then_queue(reasons)
            return

        self.last_file = path
        self._refresh_bar()
        size = path.stat().st_size / 1e6 if path.exists() else 0
        self._say(
            lambda: _("Saved {name} — {size} MB").format(
                name=path.name, size=f"{size:.0f}"
            )
        )
        action_problem = shellops.run_action(self.settings.after_recording, path)
        if action_problem:
            reasons.append((action_problem, True))
        # Last, and queued: the file name is the answer to the button and shows now;
        # each reason gets its turn after it, and the last one stays on the bar.
        self._say_each(reasons)

    def discard_recording(self) -> None:
        if self.multi is not None:
            answer = QtWidgets.QMessageBox.question(
                None, _("Throw it away?"),
                _("Delete this recording and every screen in it?"),
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            self.timer.stop()
            self.auto_stop_timer.stop()
            self._hide_overlays()
            session, self.multi = self.multi, None
            problem = session.discard()
            self.bar.set_recording(False)
            self.tray.set_recording(False)
            self._say(
                problem or (lambda: _("Recording discarded.")), warn=bool(problem)
            )
            return
        if self.session is None:
            return
        answer = QtWidgets.QMessageBox.question(
            None, _("Throw it away?"),
            _("Delete this recording and everything in it?"),
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.timer.stop()
        self.auto_stop_timer.stop()
        self._hide_overlays()
        session, self.session = self.session, None
        problem = session.discard()
        self.bar.set_recording(False)
        self.tray.set_recording(False)
        self._say(problem or (lambda: _("Recording discarded.")), warn=bool(problem))

    def _tick(self) -> None:
        if self.multi is not None:
            progress = self.multi.progress
            self.bar.set_progress(
                progress.seconds, progress.size_bytes / 1e6,
                progress.worst_fps, progress.dropped,
            )
            self.tray.set_elapsed(progress.seconds, progress.size_bytes / 1e6)
            if not self.multi.recording and not self.multi.paused:
                self._say(lambda: _("Recording stopped on its own."), warn=True)
                self.stop_recording()
            self._watch_space()
            return
        session = self.session
        if session is None:
            return
        if not session.recording and not session.paused:
            # One breath, five lines, and `Bar.set_status` has no queue - so the
            # sentence and the reason under it were both written and neither could be
            # read. `_say_each` gives each its turn. ffmpeg's own words are framed as
            # ffmpeg's, because they are English on a Russian bar and the user should
            # know whose English they are.
            tail = [
                line.strip()
                for line in session.log_text().strip().splitlines()[-3:]
                if line.strip()
            ]
            self._say(lambda: _("Recording stopped on its own."), warn=True)
            self._say_each(
                [
                    (lambda text=line: _("ffmpeg: {line}").format(line=text), True)
                    for line in tail
                ]
            )
            # Says "Saved ..." on its way out, which is why the reason is queued and
            # not said: a queued line outlives the one said over the top of it.
            self.stop_recording()
            return
        progress = session.progress
        self.bar.set_progress(
            progress.seconds,
            progress.size_bytes / 1e6,
            progress.fps,
            progress.dropped,
        )
        self.tray.set_elapsed(progress.seconds, progress.size_bytes / 1e6)
        self._watch_space()
        self._watch_system_sound(session)

    def _watch_system_sound(self, session) -> None:
        """Say, once, if the system-sound track has been silence for long enough.

        The feeder's own counters, not the level meter: these are what is actually
        going into the file, and a second opinion from a second device is a second
        thing to keep in step. `audible_frames` is zero both when the endpoint produces
        nothing and when it produces digital silence - which is what recording the
        wrong playback device sounds like, and is the case that was missed.
        """
        if self._silence_warned or not self.settings.system_audio_enabled:
            return
        feeder = getattr(getattr(session, "recorder", None), "feeder", None)
        if feeder is None or feeder.error:
            return  # a failure of its own; `_feeder_note` reports that one
        if feeder.seconds_written < SILENT_SYSTEM_SECONDS or feeder.audible_frames:
            return
        self._silence_warned = True
        self._say(
            lambda: _(
                "No system sound has arrived yet. Check which playback device is "
                "being recorded - the recording is otherwise fine."
            ),
            warn=True,
        )

    def _watch_space(self) -> None:
        if self._space_warned:
            return
        space = storage.check(
            self.settings.resolved_output_dir(), self._estimate().mb_per_minute
        )
        if space.critical:
            self._space_warned = True
            self._say(storage.warning_for(space), warn=True)

    def _on_progress(self, _progress: object) -> None:
        # The reader thread hands the object over here; _tick reads the aggregate.
        pass

    # -------------------------------------------------------------- overlays

    def _show_overlays(self) -> None:
        if self.settings.capture_frame:
            self._show_frames()
        if self.settings.click_highlight:
            self._start_highlight()
        if self.settings.webcam_enabled:
            self._start_webcam()

    def _show_frames(self) -> None:
        """Outline everything being recorded - one frame per screen in the set.

        A whole screen used to get no frame at all, because an outline inside the
        captured rectangle would be in the file. That is no longer so: `CaptureFrame`
        hides its own window from the capture and draws along the screen's edge, so the
        thing the user asked for - seeing in the window itself that it is being written -
        now works for one screen and for all of them.
        """
        if self.settings.source_kind == config_mod.SOURCE_ALL_SCREENS:
            targets = [(monitor, None) for monitor in self.monitors]
        else:
            source = self._resolve_source()
            targets = [] if source is None else [(source.monitor, source.crop)]
        if not targets:
            return

        while len(self.frames) < len(targets):
            self.frames.append(CaptureFrame())
        refused = ""
        for frame, (monitor, crop) in zip(self.frames, targets):
            if not frame.show_for(monitor, crop):
                # Only the first refusal is said out loud: four screens refusing for the
                # same reason would be four identical notices, which is noise.
                refused = refused or frame.problem
        for spare in self.frames[len(targets):]:
            spare.hide_frame()
        if refused:
            self._say(refused)

    def _hide_overlays(self) -> None:
        for frame in self.frames:
            try:
                frame.hide_frame()
            except Exception as exc:
                self._say(
                    lambda e=exc: _(
                        "The capture frame would not close: {reason}"
                    ).format(reason=e),
                    warn=True,
                )
        self._stop_highlight()
        self._stop_webcam()
        # The take is over, so the program's own windows can be photographed again
        # and the settings' picture and sound rows go live. Here rather than in
        # `stop_recording`, because every path that ends a take comes through this one -
        # stop, discard, and both of their all-screens halves.
        surfaces.set_recording(False)
        if self.panel is not None:
            self.panel.set_recording(False)

    def _start_webcam(self) -> None:
        """Put the camera in the corner of whatever is about to be recorded.

        A camera that will not open never stops a recording: the reason goes on the
        bar and the screen is recorded without it. Losing the take because a USB
        camera was unplugged would be the worse failure by far.
        """
        from .. import webcam as webcam_mod

        area = self._webcam_area()
        try:
            self.webcam = webcam_mod.WebcamOverlay()
            self.webcam.set_placement(
                self.settings.webcam_corner,
                self.settings.webcam_size,
                self.settings.webcam_shape,
            )
            if area is not None:
                self.webcam.set_area(area)
            problem = self.webcam.start(
                self.settings.webcam_device, self.settings.webcam_resolution
            )
        except Exception as exc:
            self.webcam = None
            self._say(
                lambda: _("The camera is off: {reason}").format(reason=exc), warn=True
            )
            return
        if problem:
            self.webcam = None
            self._say(
                lambda: _("The camera is off: {reason}").format(reason=problem),
                warn=True,
            )

    def _webcam_area(self) -> QtCore.QRect | None:
        """The rectangle the camera should sit in a corner of.

        Whatever is being recorded: the dragged-out region, the window, the monitor -
        or, for every screen at once, the first of them, because the camera is one
        picture and it has to land on one of the files.
        """
        if self.settings.source_kind == config_mod.SOURCE_ALL_SCREENS:
            monitors = self.monitors or []
            if not monitors:
                return None
            first = monitors[0]
            return QtCore.QRect(first.x, first.y, first.width, first.height)
        source = self._resolve_source()
        if source is None:
            return None
        if source.crop:
            x, y, width, height = source.crop
            return QtCore.QRect(x, y, width, height)
        monitor = source.monitor
        return QtCore.QRect(monitor.x, monitor.y, monitor.width, monitor.height)

    def _stop_webcam(self) -> None:
        if self.webcam is None:
            return
        try:
            self.webcam.stop()
            self.webcam.deleteLater()
        except Exception as exc:
            self._say(
                lambda: _("The camera would not shut down: {reason}").format(reason=exc),
                warn=True,
            )
        self.webcam = None

    def _start_highlight(self) -> None:
        from .. import overlay

        try:
            self.highlight = overlay.ClickHighlight()
            problem = self.highlight.start()
        except Exception as exc:
            self.highlight = None
            self._say(
                lambda: _("Click highlighting is off: {reason}").format(reason=exc),
                warn=True,
            )
            return
        if problem:
            self.highlight = None
            self._say(
                lambda: _("Click highlighting is off: {reason}").format(reason=problem),
                warn=True,
            )

    def _stop_highlight(self) -> None:
        if self.highlight is None:
            return
        try:
            self.highlight.stop()
            self.highlight.deleteLater()
        except Exception as exc:
            self._say(
                lambda: _(
                    "The click overlay would not shut down: {reason}"
                ).format(reason=exc),
                warn=True,
            )
        self.highlight = None

    # ----------------------------------------------------------- annotations

    def toggle_annotations(self) -> None:
        controller = self._annotations()
        if controller is None:
            return
        controller.toggle_mode()

    def _annotations(self):
        if self.annotations is not None:
            return self.annotations
        try:
            from .annotations import AnnotationController
        except ImportError as exc:
            self._say(
                lambda: _("The drawing tools are unavailable: {reason}").format(
                    reason=exc
                ),
                warn=True,
            )
            return None
        try:
            self.annotations = AnnotationController(self.settings, self._say)
            # The canvas raises itself when it enters draw mode; the bar and the
            # toolbar have to come back above it or they end up underneath.
            self.annotations.canvas.raised.connect(self._restack)
            # And again after every press on the canvas: activating it raises it, which
            # buried the toolbar after the first stroke.
            self.annotations.restack_needed.connect(self._raise_bar)
        except Exception as exc:
            self._say(
                lambda: _("The drawing tools could not start: {reason}").format(
                    reason=exc
                ),
                warn=True,
            )
            return None
        return self.annotations

    def _show_bar(self) -> None:
        """Bring the bar back where the user can see it, wherever it wandered to."""
        if not self.bar.isVisible():
            self.bar.show()
        monitors = winapi.list_monitors()
        here = winapi.monitor_at(
            monitors,
            self.bar.x() + self.bar.width() // 2,
            self.bar.y() + self.bar.height() // 2,
        )
        if here is None:
            self.bar.place_default(monitors)
        self.bar.raise_()

    def quit(self) -> None:
        """The way out. There was none at all in the first version.

        **A quit asked for while the probe runs is held until the probe ends.**
        `_probe_step` pumps the queue with `QApplication.processEvents()`, so this slot
        runs inside a nested event loop, and `QApplication.quit()` exits *that* loop -
        the outer `exec()` never returns and the program hangs with its bar still up.
        Measured here on 2026-11-05, offscreen, with a slot that pumps the queue
        between 0.25 s steps while a timer calls `quit()`: the quit arrived at 0.250 s,
        the slot ended at 1.001 s, and `exec()` was still running 2.0 s after the quit.
        The same harness with the quit held back and repeated after the slot left
        `exec()` at 1.001 s. The window this is reachable in is the cold probe, which
        `_probe_encoders` measures at 5.87 s; `SingleInstance` then makes a relaunch
        wake the stuck copy rather than start a fresh one.
        """
        if self._probing:
            self._quit_wanted = True
            self._say(
                lambda: _("Closing as soon as the encoder check finishes."), warn=True
            )
            return
        # Either kind of take. Until 2026-11-05 this asked only about `self.session`,
        # so quitting in the middle of an all-screens recording asked nothing and threw
        # away however many screens were being written - reproduced on 2026-11-05 with
        # a live `multi`: no question box at all, straight to `QApplication.quit()`.
        if self._is_recording():
            answer = QtWidgets.QMessageBox.question(
                None,
                _("Still recording"),
                _("A recording is running. Stop it and quit?"),
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        QtWidgets.QApplication.quit()

    def _raise_bar(self) -> None:
        """Keep the bar above the drawing canvas without giving it focus."""
        if self.bar.isVisible():
            self.bar.raise_()

    def _restack(self) -> None:
        """Put the chrome back above the canvas, without giving any of it focus."""
        controller = self.annotations
        if controller is not None and controller.toolbar.isVisible():
            controller.toolbar.raise_()
            if controller.textbox.isVisible():
                controller.textbox.raise_()
        if self.bar.isVisible():
            self.bar.raise_()

    def watch_last(self) -> None:
        """Open the most recent recording in the player.

        A multi-screen set is the case this exists for - two files that mean nothing
        apart - but a single recording opens in the same player, because a viewer who
        asks to watch the last thing should not have to care which kind it was.
        """
        from ..player.open import open_recording, most_recent

        folder = self.settings.resolved_output_dir()
        target = self.last_set or self.last_file or most_recent(folder)
        if target is None:
            self._say(lambda: _("There is nothing recorded in that folder yet."))
            return
        windows, problem = open_recording(target)
        if windows is None:
            self._say(
                problem or (lambda: _("That recording could not be opened.")),
                warn=True,
            )
            return
        # Held on the controller: a player whose only reference is a local goes away
        # the moment this function returns.
        self._player = windows
        # open_recording builds the windows and leaves showing them to the caller, so
        # a player opened from here would otherwise exist and be invisible.
        show = getattr(windows, "show", None)
        if callable(show):
            try:
                show()
            except Exception as exc:
                self._say(
                    lambda: _("The player would not open: {reason}").format(
                        reason=exc
                    ),
                    warn=True,
                )
                self._player = None
                return
        if problem:
            self._say(problem, warn=True)

    # -------------------------------------------------------------- actions

    def _open_folder(self) -> None:
        problem = shellops.open_folder(self.settings.resolved_output_dir())
        if problem:
            self._say(problem, warn=True)

    def _reveal_last(self) -> None:
        if self.last_file is None:
            self._say(lambda: _("Nothing recorded yet."))
            return
        problem = shellops.reveal(self.last_file)
        if problem:
            self._say(problem, warn=True)

    # --------------------------------------------------------------- finish

    def shutdown(self) -> None:
        if (self.session is not None and self.session.active) or (
            self.multi is not None and self.multi.active
        ):
            self.stop_recording()
        self.countdown.cancel()
        self.tray.hide()
        self.level_timer.stop()
        self.meters.stop()
        self._hide_overlays()
        if self.annotations is not None:
            try:
                self.annotations.shutdown()
            except Exception as exc:
                self._say(
                    lambda: _(
                        "The drawing tools would not close: {reason}"
                    ).format(reason=exc),
                    warn=True,
                )
        for name, listener in self.hotkeys.items():
            try:
                listener.stop()
            except Exception:
                # Shutting down; a hotkey that will not unregister dies with the process.
                pass
        self.hotkeys = {}
        self._save()


def run() -> int:
    # First of all, before anything can throw: this build has no console, and Qt hands
    # an exception raised in a slot to `sys.excepthook` rather than letting it out, so
    # without this a failure leaves the program running and silent. See `errorlog`.
    errorlog.install()
    winapi.enable_dpi_awareness()

    # One copy only. Without this a second launch starts a rival that fights the first
    # for the hotkeys and the output folder - which is exactly what happened, because
    # there was no way to tell the first one was running and no way to close it.
    guard = SingleInstance()
    if not guard.acquire():
        guard.wake_the_other_one()
        print("ScreenRec is already running; asked that copy to show itself.")
        return 0

    qt_app = QtWidgets.QApplication(sys.argv)
    qt_app.setApplicationName("ScreenRec")
    qt_app.setQuitOnLastWindowClosed(False)
    theme.apply(qt_app)

    controller = Application()
    qt_app.aboutToQuit.connect(controller.shutdown)
    qt_app.aboutToQuit.connect(guard.release)
    try:
        return qt_app.exec()
    finally:
        guard.release()
