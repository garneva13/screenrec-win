"""The settings panel: everything you change once a month, behind the gear.

The bar carries the decisions a recording needs *now* - what to capture, which devices
are live, record. Everything else lives here, on one floating surface 360 px wide, in
labelled sections. It is a panel and not a window: nothing modal, nothing that takes
focus, nothing that needs a window manager.

Two rules shape the layout:

* **The control column is a fixed width.** A row is a label on the left and a control on
  the right, and the control column never changes size, so choosing "Sharp text - H.265
  4:4:4" after "No GPU - x264" does not make the whole panel jump.
* **The title row is the handle.** A frameless window has no title bar, so pressing
  "Recording settings" and dragging moves the panel - `surfaces.SurfaceDrag`, the same
  press / 2 px slop / snap-to-edge / clamp the bar is moved by. The close button in
  that row still closes. Where it was dropped is remembered **for the session**, and
  `popup_near` reopens it there instead of under the gear: the panel is read and
  edited, so putting it somewhere is a decision, and `config.Settings` is finished and
  not this module's to extend with a field for it.
* **Nothing is truncated that the user needs to read.** The chosen profile's ``note`` is
  where the 4:4:4 "Windows' own player does not decode this" warning lives, so it word
  wraps and the panel grows to fit it rather than eliding it away.

The panel writes straight into the `config.Settings` object it was handed and emits
`changed`. **It never saves the file** - the application owns that, and one writer for
one file is the only way the atomic write in `config.save_settings` stays meaningful.

**What the two device combo boxes show is what the next recording will use.** The
settings do not always pin a device down - the bar's microphone toggle sets
`mic_enabled` without naming one - so the combo resolves it, and `refresh_devices` then
writes that resolution back into the `Settings` object, once, with one `changed`.
Without it the panel says "USB Microphone" while `recorder.RecordingPlan.has_mic`
(``bool(mic_device)``) is false and the file comes back silent. A device that *is* named
but absent is never substituted: both combos keep it, labelled "(not found)", exactly as
`audio.LoopbackFeeder` and the dshow input will find it, so the panel and the failure
agree. And nothing is corrected before `refresh_devices` has been called at all: an
empty list nobody has filled in is not evidence that the machine has no microphone.

**This one surface takes focus, and that is deliberate.** The house rule in
`docs/UI_CONTRACT.md` - nothing activates, because clicking Record must not pull the user
out of what they were about to demonstrate - is written for the bar and the popovers,
which are all mouse. The panel is the only surface with a hotkey box and two spin boxes,
and a window carrying `WS_EX_NOACTIVATE` is never activated by a click, so every
keystroke would go to the application behind it. So the panel opts out of
`surfaces._no_activate` (see `_styled` in `__init__`) and activates itself in
`popup_near`, the way `overlay.RegionSelector` does. `Qt.Tool` still keeps it out of
Alt+Tab; only the no-activate bit is given up.

**The panel says which encoding path is in force, and only once it has been told.**
`set_encoders` hands it `capabilities.probe`'s rows; until then the line under the
format picker and the "Details" expander are not shown at all. A sentence claiming the
graphics card is doing the work, written before anybody asked the machine, is the exact
class of confident guess `docs/ENCODERS.md` exists to stop. The line names the class of
path and the codec and never the vendor - the vendor belongs in the expander, which is
where somebody looks when something is wrong. The line describes the format's own frame
path: a rotated screen and the all-screens layout force the download for reasons the
panel cannot see from here, so it is `recorder` that knows about those, not this.

The capture-frame switch reaches the settings object through ``CAPTURE_FRAME_ATTR``
rather than naming the field: the switch is older than the field, and going through
`getattr` keeps the panel working against a `Settings` object that has not got it.

**The language row changes the language of the whole program, not of this panel.** It
calls `i18n.set_language`, which tells every window through `i18n.notifier`; this panel
listens to that notifier like any other window and rebuilds its own text in
`retranslate`, so a switch made with the panel open changes the panel under the pointer.
Every string on it therefore has to be re-readable: nothing translated may be built once
and forgotten, which is why `_build` registers its captions and the combo boxes are
filled from the `_*_items` methods that `retranslate` calls again. The row writes
`config.Settings.language` and emits `changed` like every other control - the
application still owns the file, for the reason above.
"""
from __future__ import annotations

from typing import Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .. import audio as audio_mod
from .. import capabilities as capabilities_mod
from .. import config as config_mod
from .. import encoders as encoders_mod
from .. import i18n
from .. import presets as presets_mod
from .. import shellops
from .. import storage as storage_mod
from .. import hotkeys as hotkeys_mod
from .. import winapi
from ..i18n import _
from . import bar as bar_geometry
from . import surfaces, theme

#: The panel's width. The bar is small; a panel wider than this stops reading as a
#: popover and starts reading as a window.
WIDTH = 360

#: Every row's control is exactly this wide, so a value changing cannot move the layout.
#: Measured, not guessed: with `theme.style_sheet()` applied, a combo box holding all four
#: `config.PROFILES` labels asks for 199 px, so at 200 the format picker shows its longest
#: label ("Most compatible - H.264") whole. That leaves 120 px for the row label, and the
#: widest of those ("Show the cursor") is 94 px.
CONTROL_WIDTH = 200

#: What the estimate assumes before the application tells us the real capture size.
DEFAULT_CAPTURE = (1920, 1080)

#: The capture-frame setting has no field in `config.Settings`; see the module docstring.
CAPTURE_FRAME_ATTR = "capture_frame"
CAPTURE_FRAME_DEFAULT = True

#: Sentinel used in the device combo boxes for "do not record this source at all".
_OFF = "__off__"

#: How a codec is written for a reader. `capabilities` keeps the same table privately;
#: this one is the panel's, because the panel shows the codec and never the encoder name
#: on its one visible line - `hevc_mf` tells a user nothing, "H.265" tells them what
#: their file will be.
CODEC_LABELS: dict[str, str] = {"hevc": "H.265", "h264": "H.264", "av1": "AV1"}

# The measurement behind the sentences `_path_sentence` can say. `ffmpeg -benchmark`,
# one 2560x1440 screen at 60 fps, 600 frames, `hevc_nvenc` in every row except the last
# so that the frame path is the only thing that differs. The first pair of figures is
# the 2026-11-04 run; the second is the same four commands re-run on 2026-11-05, once
# with the desktop left as it was and once with `tools/screen_content.py` scrolling:
#
#                                                 2026-11-04   static   scrolling
#     ddagrab -> hevc_nvenc                            5.8%       3%         4%
#     ddagrab -> hwdownload,...,format=yuv420p       154.5%      67%        63%
#     ddagrab -> hwdownload,...,format=yuv444p       147.8%      45%        79%
#     the same download into libx264 -preset veryfast  n/a      136%       152%
#
# **The download rows disagree by a factor of three and nobody here has explained it.**
# A third run writing a real mp4 instead of `-f null -` came in lower still, at 46%.
# The earlier figure was reproduced independently once (146% and 145%), so it is not a
# typo; it is simply not what this machine does today, on 20 logical cores. So the
# sentence quotes the span rather than a middle: half a core to one and a half. A
# single number in front of a user would be a promise that three of five runs break.
#
# The processor row gets no number at all, and the reason given here until 2026-11-05 -
# "`libx264 -preset veryfast` on the same screen cost 71.5 s of CPU in 10.25 s - seven
# cores" - was not reproducible. Measured twice above at 136% and 152% of one core, and
# once independently at 154%; `docs/ENCODERS.md` has 173% on scrolling text. All four
# are between one and two cores and none is seven. The row still gets no number,
# because that figure is content, resolution and machine - but the reason is now the
# spread of four real runs, not a number nobody can reach.

#: How tall the probe table may grow before it scrolls instead.
#:
#: Re-measured on 2026-11-05, because the figures written here on 2026-11-04 did not
#: reproduce. Offscreen platform, default fonts, a default `config.Settings` - so the
#: standard profile, no 4:4:4 warning - and this machine's own twelve probe rows:
#:
#:     never told about any encoder            1117 px
#:     told, expander closed                   1215 px
#:     expander open at DETAILS_HEIGHT = 150   1367 px
#:     expander open with the cap lifted       1505 px
#:
#: Only the 1215 px reproduced; 1409 and 1746 did not, and the second of them overstated
#: the case by 241 px. The case survives anyway: 1505 px is taller than the 1440 px
#: screen it was measured for, the last rows cannot be read at all, and `popup_near` can
#: only keep the top of the panel on screen. At 150 px about five rows are visible.
DETAILS_HEIGHT = 150


class Switch(QtWidgets.QAbstractButton):
    """A real toggle switch: a painted pill with a knob that travels across it.

    A check box says "this is a form". A switch says "this is on or off right now", which
    is what every one of these settings is. It is a `QAbstractButton` with
    ``setCheckable(True)``, so `toggled` arrives for free and the keyboard works the way
    Qt users expect - Space toggles it, Tab reaches it.

    The travel is animated over `DURATION_MS` by one `QVariantAnimation` held on the
    instance. Holding it matters: an animation that is only referenced by the call that
    started it is collected mid-flight and the knob stops halfway.
    """

    WIDTH = 38
    HEIGHT = 22
    KNOB = 16
    DURATION_MS = 120

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

        #: 0.0 = knob fully left (off), 1.0 = knob fully right (on).
        self._travel = 1.0 if self.isChecked() else 0.0
        self._animation = QtCore.QVariantAnimation(self)
        self._animation.setDuration(self.DURATION_MS)
        self._animation.setEasingCurve(QtCore.QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._on_travel)
        self.toggled.connect(self._on_toggled)

    # ------------------------------------------------------------- animation

    def _on_toggled(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(float(self._travel))
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _on_travel(self, value: object) -> None:
        try:
            self._travel = max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # QVariantAnimation interpolates floats here; anything else is a programming
            # error, and snapping to the settled state beats painting a broken knob.
            self._travel = 1.0 if self.isChecked() else 0.0
        self.update()

    def set_checked_now(self, checked: bool) -> None:
        """Set the state with no animation - for loading settings into the panel."""
        self._animation.stop()
        was_blocked = self.blockSignals(True)
        try:
            self.setChecked(checked)
        finally:
            self.blockSignals(was_blocked)
        self._travel = 1.0 if checked else 0.0
        self.update()

    def travel(self) -> float:
        """Where the knob is, 0..1. Exposed so a test can watch the animation."""
        return self._travel

    # ---------------------------------------------------------------- paint

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.WIDTH, self.HEIGHT)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        enabled = self.isEnabled()
        hovered = self.underMouse() and enabled
        radius = self.height() / 2.0
        track = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        if not enabled:
            # Dim: the track keeps its shape so the state is still readable, but neither
            # the accent nor the hairline is at full strength.
            fill = colours["line"] if self.isChecked() else colours["panel"]
            edge = colours["line"]
            knob_ink = colours["disabled"]
        elif self.isChecked():
            fill = colours["accent_hover"] if hovered else colours["accent"]
            edge = fill
            knob_ink = colours["text"]
        else:
            fill = colours["hover"] if hovered else colours["line"]
            edge = colours["line_strong"] if hovered else colours["line"]
            knob_ink = colours["text_soft"]

        painter.setPen(QtGui.QPen(QtGui.QColor(edge), 1))
        painter.setBrush(QtGui.QColor(fill))
        painter.drawRoundedRect(track, radius, radius)

        margin = (self.height() - self.KNOB) / 2.0
        span = self.width() - self.KNOB - 2 * margin
        centre = QtCore.QPointF(
            margin + self.KNOB / 2.0 + span * self._travel, self.height() / 2.0
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(knob_ink))
        painter.drawEllipse(centre, self.KNOB / 2.0, self.KNOB / 2.0)

        if self.hasFocus() and enabled:
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["accent_light"]), 1))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                track.adjusted(-1.5, -1.5, 1.5, 1.5), radius + 1.5, radius + 1.5
            )

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


def _heading(text: str) -> QtWidgets.QLabel:
    """A section heading: small, wide-tracked, upper case, in `text_dim`."""
    widget = surfaces.label(text.upper(), "text_dim", 8.0)
    font = QtGui.QFont(widget.font())
    font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 0.9)
    font.setBold(True)
    widget.setFont(font)
    return widget


class SettingsPanel(surfaces.FloatingSurface):
    """The gear's panel. Writes into the `Settings` it was given; the caller saves."""

    changed = QtCore.Signal()
    closed = QtCore.Signal()

    def __init__(
        self,
        settings: config_mod.Settings,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(radius=theme.RADIUS["surface"], parent=parent)
        #: `FloatingSurface.showEvent` calls `surfaces._no_activate` unless this is
        #: already true. Setting it here is how this one surface keeps the keyboard:
        #: see the module docstring. The flag is not re-read anywhere else.
        self._styled = True
        self.settings = settings
        self.mics: list[audio_mod.MicDevice] = []
        self.loopbacks: list[audio_mod.LoopbackDevice] = []
        #: False until `refresh_devices` has been called: an empty list before that
        #: means "nobody has enumerated yet", which is not the same as "none exist".
        self._devices_known = False
        self._capture_size = DEFAULT_CAPTURE
        #: What `capabilities.probe` found, and whether anybody has said. Empty and
        #: False are different answers: "no encoder works here" is a finding, and
        #: "nobody has asked yet" is not, so the panel claims nothing until
        #: `set_encoders` is called.
        self._encoders: list[capabilities_mod.EncoderResult] = []
        self._encoders_known = False
        #: True while settings are being pushed into the widgets, so the handlers that
        #: write back do nothing and `changed` does not fire for a value nobody changed.
        self._loading = False
        #: Dragging the title row moves the panel; see `mousePressEvent`.
        self._drag = surfaces.SurfaceDrag(self)
        #: Where the user last dropped it, in desktop pixels, or None if never dragged.
        #: The panel is not a popover - it is read and edited, and it is opened from a
        #: bar the user may deliberately have put it clear of - so once it has been
        #: moved by hand, `popup_near` stops second-guessing that and reopens it where
        #: it was left. For the session only: it is where a window is, not a setting,
        #: and `config.Settings` holds no window geometry at all.
        self._dropped_at: tuple[int, int] | None = None
        #: Every caption on the panel, as (widget, its English source, upper-case it),
        #: so `retranslate` can say the same thing again in the other language.
        self._captions: list[tuple[QtWidgets.QLabel, str, bool]] = []
        #: The hotkey complaint being shown, as (English template, its values), or None.
        #: Kept rather than just painted so that a language switch can say it again
        #: instead of swallowing a message the user has not read yet.
        self._hotkey_message: tuple[str, dict[str, str]] | None = None

        self.setFixedWidth(WIDTH)
        self._build()
        self._wire()
        # One path fills the notes, the tool tips and the lists, at start-up and at
        # every switch, so there is no second copy of any string to fall behind.
        self.retranslate()
        self.refresh_from_settings()
        i18n.notifier.changed.connect(self._on_language_switched)

    # ---------------------------------------------------------------- build

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 14)
        outer.setSpacing(2)

        outer.addWidget(self._build_header())
        outer.addSpacing(6)

        outer.addWidget(self._section("Language"))
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.setFixedWidth(CONTROL_WIDTH)
        self._fill(self.language_combo, self._language_items())
        outer.addWidget(self._row("Language", self.language_combo))
        self.language_note = self._note("")
        outer.addWidget(self.language_note)
        outer.addSpacing(10)

        outer.addWidget(self._section("Preset"))
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.setFixedWidth(CONTROL_WIDTH)
        self._fill(self.preset_combo, self._preset_items())
        outer.addWidget(self._row("Use", self.preset_combo))
        self.preset_note = self._note("")
        outer.addWidget(self.preset_note)
        outer.addSpacing(10)

        outer.addWidget(self._section("Video"))
        self.fps_spin = QtWidgets.QSpinBox()
        self.fps_spin.setRange(5, 240)
        self.fps_spin.setSuffix(_(" fps"))
        self.fps_spin.setLocale(QtCore.QLocale.c())
        self.fps_spin.setKeyboardTracking(False)
        self.fps_spin.setFixedWidth(CONTROL_WIDTH)
        outer.addWidget(self._row("Frame rate", self.fps_spin))

        self.size_combo = self._combo()
        self._fill(self.size_combo, self._size_items())
        outer.addWidget(self._row("Size", self.size_combo))

        #: What the chosen size will really do, which depends on the screen: on a
        #: 1080p monitor "1080p" changes nothing, and saying so is better than leaving
        #: somebody to wonder why the file is the same size.
        self.size_note = self._note("")
        outer.addWidget(self.size_note)

        self.profile_combo = self._combo()
        self._fill(self.profile_combo, self._profile_items())
        outer.addWidget(self._row("Format", self.profile_combo))

        self.profile_note = self._note("")
        outer.addWidget(self.profile_note)

        #: Why the chosen format cannot be recorded on this machine, in the idiom of
        #: `Profile.note`. Shown in the error colour, because unlike a note it is the
        #: reason a format in the list above is greyed out.
        self.profile_problem = self._note("")
        self.profile_problem.setStyleSheet(
            f"color: {theme.COLOURS['error_text']}; background: transparent;"
        )
        self.profile_problem.setVisible(False)
        outer.addWidget(self.profile_problem)

        #: The one line that says which encoding path a recording will take.
        self.encoding_note = self._note("")
        self.encoding_note.setVisible(False)
        outer.addWidget(self.encoding_note)

        self.details_button = QtWidgets.QPushButton("")
        self.details_button.setCheckable(True)
        self.details_button.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        # Quiet on purpose: this is a disclosure, not an action. The panel's own
        # `hotkey_problem` sets its colour the same way, for the same reason - one
        # widget's exception does not belong in `theme.style_sheet()`.
        self.details_button.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; padding: 2px 0;"
            f" text-align: left; color: {theme.COLOURS['accent_light']}; }}"
            f"QPushButton:hover {{ color: {theme.COLOURS['text']}; }}"
        )
        self.details_button.setVisible(False)
        outer.addWidget(self.details_button)

        #: Twelve candidates, several of them with a wrapped sentence of ffmpeg's
        #: underneath, is more than the panel can grow by - see `DETAILS_HEIGHT`. So the
        #: rows live in `details_rows` and the box around them scrolls.
        self.details_rows = QtWidgets.QWidget()
        self.details_rows.setObjectName("formRow")
        self._details_layout = QtWidgets.QVBoxLayout(self.details_rows)
        self._details_layout.setContentsMargins(0, 0, 0, 0)
        self._details_layout.setSpacing(1)

        self.details_box = QtWidgets.QScrollArea()
        self.details_box.setObjectName("formRow")
        self.details_box.setWidget(self.details_rows)
        self.details_box.setWidgetResizable(True)
        self.details_box.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.details_box.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.details_box.setMaximumHeight(DETAILS_HEIGHT)
        self.details_box.setVisible(False)
        outer.addWidget(self.details_box)

        self.quality_combo = self._combo()
        self._fill(self.quality_combo, self._quality_items())
        outer.addWidget(self._row("Quality", self.quality_combo))

        self.estimate_label = self._note("")
        outer.addWidget(self.estimate_label)
        outer.addSpacing(10)

        outer.addWidget(self._section("Audio"))
        self.mic_combo = self._combo()
        outer.addWidget(self._row("Microphone", self.mic_combo))
        self.system_combo = self._combo()
        outer.addWidget(self._row("System sound", self.system_combo))
        self.tracks_combo = self._combo()
        self._fill(self.tracks_combo, self._tracks_items())
        self.tracks_row = self._row("Tracks", self.tracks_combo)
        outer.addWidget(self.tracks_row)
        self.tracks_note = self._note(
            _("Two tracks need both the microphone and the system sound.")
        )
        outer.addWidget(self.tracks_note)
        outer.addSpacing(10)

        outer.addWidget(self._section("Camera"))
        self.webcam_switch = Switch()
        self.webcam_switch.setToolTip(
            _("The camera is drawn on the screen itself, so it reaches the recording "
              "the way the click rings do - and so you can see it too while you record.")
        )
        outer.addWidget(self._row("Camera in the corner", self.webcam_switch))

        self.webcam_combo = self._combo()
        outer.addWidget(self._row("Which camera", self.webcam_combo))

        self.webcam_corner_combo = self._combo()
        self._fill(self.webcam_corner_combo, self._corner_items())
        outer.addWidget(self._row("Corner", self.webcam_corner_combo))

        self.webcam_size_combo = self._combo()
        self._fill(self.webcam_size_combo, self._webcam_size_items())
        outer.addWidget(self._row("Camera size", self.webcam_size_combo))

        self.webcam_shape_combo = self._combo()
        self._fill(self.webcam_shape_combo, self._webcam_shape_items())
        outer.addWidget(self._row("Camera shape", self.webcam_shape_combo))

        self.webcam_res_combo = self._combo()
        outer.addWidget(self._row("Camera resolution", self.webcam_res_combo))

        #: Empty, or why there is no camera to choose from.
        self.webcam_note = self._note("")
        self.webcam_note.setVisible(False)
        outer.addWidget(self.webcam_note)

        outer.addWidget(self._section("More"))
        self.cursor_switch = Switch()
        outer.addWidget(self._row("Show the cursor", self.cursor_switch))
        self.clicks_switch = Switch()
        outer.addWidget(self._row("Highlight clicks", self.clicks_switch))
        self.frame_switch = Switch()
        self.frame_switch.setToolTip(
            _("A thin red outline round what is being recorded, on every screen in "
              "the set. It stays on your screen and never reaches the file.")
        )
        outer.addWidget(self._row("Capture frame", self.frame_switch))
        self.bar_switch = Switch()
        outer.addWidget(self._row("Floating bar", self.bar_switch))
        self.countdown_spin = QtWidgets.QSpinBox()
        self.countdown_spin.setRange(0, 10)
        self.countdown_spin.setSuffix(_(" s"))
        self.countdown_spin.setSpecialValueText(_("None"))
        self.countdown_spin.setLocale(QtCore.QLocale.c())
        self.countdown_spin.setKeyboardTracking(False)
        self.countdown_spin.setFixedWidth(CONTROL_WIDTH)
        outer.addWidget(self._row("Countdown", self.countdown_spin))

        self.autostop_spin = QtWidgets.QSpinBox()
        self.autostop_spin.setRange(0, 600)
        self.autostop_spin.setSuffix(_(" min"))
        self.autostop_spin.setSpecialValueText(_("Never"))
        self.autostop_spin.setLocale(QtCore.QLocale.c())
        self.autostop_spin.setKeyboardTracking(False)
        self.autostop_spin.setFixedWidth(CONTROL_WIDTH)
        self.autostop_spin.setToolTip(
            _("Stop on its own after this long - for a process you start and walk "
              "away from")
        )
        outer.addWidget(self._row("Stop after", self.autostop_spin))

        self.after_combo = QtWidgets.QComboBox()
        self.after_combo.setFixedWidth(CONTROL_WIDTH)
        self._fill(self.after_combo, self._after_items())
        outer.addWidget(self._row("When it ends", self.after_combo))
        outer.addSpacing(10)

        outer.addWidget(self._section("Output"))
        folder_row = QtWidgets.QWidget()
        folder_row.setObjectName("formRow")
        folder_layout = QtWidgets.QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 2, 0, 2)
        folder_layout.setSpacing(6)
        self.folder_edit = QtWidgets.QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setMinimumWidth(10)
        self.browse_button = surfaces.IconButton(
            "folder", _("Choose where recordings are saved"), size=28
        )
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(self.browse_button)
        outer.addWidget(folder_row)
        outer.addSpacing(10)

        outer.addWidget(self._section("Hotkey"))
        self.hotkey_edit = QtWidgets.QLineEdit()
        self.hotkey_edit.setPlaceholderText("Ctrl+Alt+R")
        self.hotkey_edit.setFixedWidth(CONTROL_WIDTH)
        outer.addWidget(self._row("Start / stop", self.hotkey_edit))

        self.hotkey_pause_edit = QtWidgets.QLineEdit()
        self.hotkey_pause_edit.setPlaceholderText("Ctrl+Alt+P")
        self.hotkey_pause_edit.setFixedWidth(CONTROL_WIDTH)
        outer.addWidget(self._row("Pause / carry on", self.hotkey_pause_edit))

        self.hotkey_draw_edit = QtWidgets.QLineEdit()
        self.hotkey_draw_edit.setPlaceholderText("Ctrl+Alt+D")
        self.hotkey_draw_edit.setFixedWidth(CONTROL_WIDTH)
        self.hotkey_draw_edit.setToolTip(
            _("Switches between drawing on the screen and working with the application")
        )
        outer.addWidget(self._row("Draw / work", self.hotkey_draw_edit))
        self.hotkey_problem = self._note("")
        self.hotkey_problem.setStyleSheet(
            f"color: {theme.COLOURS['error_text']}; background: transparent;"
        )
        self.hotkey_problem.setVisible(False)
        outer.addWidget(self.hotkey_problem)

    def _build_header(self) -> QtWidgets.QWidget:
        """The title row, as a widget rather than a bare layout.

        It is a widget because the panel is dragged by it and `_in_header` needs a
        rectangle to measure; `formRow` keeps it transparent, so it looks the same as
        the layout it replaced.
        """
        holder = QtWidgets.QWidget()
        holder.setObjectName("formRow")
        header = QtWidgets.QHBoxLayout(holder)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = surfaces.label(_("Recording settings"), "text", 10.5)
        font = QtGui.QFont(title.font())
        font.setWeight(QtGui.QFont.Weight.DemiBold)
        title.setFont(font)
        self._captions.append((title, "Recording settings", False))
        header.addWidget(title)
        header.addStretch(1)
        self.close_button = surfaces.IconButton("close", _("Close"), size=26)
        header.addWidget(self.close_button)
        self.header = holder
        return holder

    def _section(self, text: str) -> QtWidgets.QLabel:
        """A section heading, registered so `retranslate` can rewrite it."""
        widget = _heading(_(text))
        self._captions.append((widget, text, True))
        return widget

    def _language_items(self) -> list[tuple[str, object]]:
        """Each language in its own name, in `i18n.LANGUAGES` order.

        Not translated: a list of languages written in a language the reader cannot
        read helps nobody, which is what `i18n.NATIVE_NAMES` is for.
        """
        return [(i18n.NATIVE_NAMES[code], code) for code in i18n.LANGUAGES]

    def _preset_items(self) -> list[tuple[str, object]]:
        items: list[tuple[str, object]] = [(_("Custom"), "")]
        items += [(_(preset.name), preset.key) for preset in presets_mod.BUILTIN]
        return items

    def _profile_items(self) -> list[tuple[str, object]]:
        return [
            (_(profile.label), key) for key, profile in config_mod.PROFILES.items()
        ]

    def _quality_items(self) -> list[tuple[str, object]]:
        return [
            (_(config_mod.QUALITY_LABELS[level]), level)
            for level in config_mod.QUALITY_LEVELS
        ]

    def _tracks_items(self) -> list[tuple[str, object]]:
        return [
            (_("Mixed into one"), config_mod.AUDIO_MIX),
            (_("Two separate"), config_mod.AUDIO_SEPARATE),
        ]

    def _after_items(self) -> list[tuple[str, object]]:
        return [(_(label), key) for key, label in shellops.ACTIONS]

    def _fill(
        self, combo: QtWidgets.QComboBox, items: list[tuple[str, object]]
    ) -> None:
        """Put `items` in the combo, keeping whatever was chosen before.

        Used to build the fixed lists and to say them again in the other language. The
        signals are blocked throughout: refilling a list is not the user choosing
        something, and a handler firing here would write a value nobody touched.
        """
        chosen = combo.currentData()
        was_blocked = combo.blockSignals(True)
        try:
            combo.clear()
            for text, data in items:
                combo.addItem(text, data)
            index = combo.findData(chosen) if chosen is not None else -1
            combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(was_blocked)

    def _combo(self) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.setFixedWidth(CONTROL_WIDTH)
        # Without this a single long device name decides the panel's width.
        combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(8)
        view = combo.view()
        if view is not None:
            view.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)
        return combo

    def _note(self, text: str) -> QtWidgets.QLabel:
        """A full-width, word-wrapping line of explanation under a control."""
        widget = surfaces.label(text, "text_dim", 8.5)
        widget.setWordWrap(True)
        widget.setContentsMargins(0, 1, 0, 3)
        return widget

    def _row(self, text: str, control: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """A label on the left, `control` right-aligned in a fixed-width column."""
        holder = QtWidgets.QWidget()
        holder.setObjectName("formRow")
        layout = QtWidgets.QHBoxLayout(holder)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(8)
        caption = surfaces.label(_(text), "text_soft", 9.5)
        self._captions.append((caption, text, False))
        layout.addWidget(caption, 1)
        column = QtWidgets.QWidget()
        column.setObjectName("formRow")
        column.setFixedWidth(CONTROL_WIDTH)
        column_layout = QtWidgets.QHBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(0)
        column_layout.addStretch(1)
        column_layout.addWidget(control)
        layout.addWidget(column)
        return holder

    def _wire(self) -> None:
        self.close_button.clicked.connect(self._close)
        self.language_combo.currentIndexChanged.connect(self._on_language)
        self.fps_spin.valueChanged.connect(self._on_framerate)
        self.size_combo.currentIndexChanged.connect(self._on_size)
        self.webcam_switch.toggled.connect(self._on_webcam_enabled)
        self.webcam_combo.currentIndexChanged.connect(self._on_webcam_device)
        self.webcam_corner_combo.currentIndexChanged.connect(self._on_webcam_corner)
        self.webcam_size_combo.currentIndexChanged.connect(self._on_webcam_size)
        self.webcam_shape_combo.currentIndexChanged.connect(self._on_webcam_shape)
        self.webcam_res_combo.currentIndexChanged.connect(self._on_webcam_resolution)
        self.profile_combo.currentIndexChanged.connect(self._on_profile)
        self.quality_combo.currentIndexChanged.connect(self._on_quality)
        self.mic_combo.currentIndexChanged.connect(self._on_mic)
        self.system_combo.currentIndexChanged.connect(self._on_system)
        self.tracks_combo.currentIndexChanged.connect(self._on_tracks)
        self.cursor_switch.toggled.connect(self._on_cursor)
        self.clicks_switch.toggled.connect(self._on_clicks)
        self.frame_switch.toggled.connect(self._on_frame)
        self.bar_switch.toggled.connect(self._on_bar)
        self.countdown_spin.valueChanged.connect(self._on_countdown)
        self.browse_button.clicked.connect(self._browse)
        self.hotkey_edit.editingFinished.connect(self._on_hotkey)
        self.hotkey_pause_edit.editingFinished.connect(self._on_hotkey_pause)
        self.hotkey_draw_edit.editingFinished.connect(self._on_hotkey_draw)
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        self.autostop_spin.valueChanged.connect(self._on_autostop)
        self.after_combo.currentIndexChanged.connect(self._on_after)
        self.details_button.toggled.connect(self._on_details)

    # ------------------------------------------------------------- language

    def _on_language(self, _index: int) -> None:
        """Switch the whole program's language and remember the choice.

        `set_language` emits `i18n.notifier.changed`, which arrives back here as
        `_on_language_switched` and rebuilds this panel's own text, so the panel the
        user is looking at changes with the click rather than at the next opening.
        """
        if self._loading:
            return
        code = self.language_combo.currentData()
        if not isinstance(code, str) or code not in i18n.LANGUAGES:
            return
        in_force = i18n.set_language(code)
        if self.settings.language == in_force:
            return
        self.settings.language = in_force
        self._write()  # the application hears `changed` and saves the file

    def _on_language_switched(self, _code: str) -> None:
        """`i18n.notifier.changed` arrived - from this panel or from anywhere else."""
        try:
            self.retranslate()
        except RuntimeError:
            # The panel's C++ side has been deleted while the notifier still held the
            # connection (a panel dropped without `deleteLater` having run yet). There
            # is nothing left to retranslate, and this must not take the window down.
            pass

    def retranslate(self) -> None:
        """Say everything on the panel again in the language now in force.

        Every string the user reads is rebuilt here: the captions registered by `_row`
        and `_section`, the fixed combo boxes, the two device lists, the notes, the
        tool tips and whatever complaint the hotkey boxes are showing. `_loading` is
        held throughout so that nothing of this counts as the user changing a setting.
        """
        was_loading = self._loading
        self._loading = True
        try:
            for widget, source, upper in self._captions:
                text = _(source)
                widget.setText(text.upper() if upper else text)
            self._retranslate_fixed()
            self._fill(self.language_combo, self._language_items())
            # The switch may have come from another window, so the row shows the
            # language that is actually in force rather than the one it last showed.
            index = self.language_combo.findData(i18n.language())
            self.language_combo.setCurrentIndex(max(0, index))
            self._fill(self.preset_combo, self._preset_items())
            self._fill(self.profile_combo, self._profile_items())
            self._fill(self.quality_combo, self._quality_items())
            self._fill(self.tracks_combo, self._tracks_items())
            self._fill(self.after_combo, self._after_items())
            self._fill_mic_combo()
            self._fill_system_combo()
            self._refresh_preset()
            self._show_hotkey_message()
        finally:
            self._loading = was_loading
        # Recomputes the estimate line and the profile note, and refits the panel: the
        # same sentence is a different height in another language.
        self._update_enabled()

    def _retranslate_fixed(self) -> None:
        """The strings that are not a caption and not a list item."""
        self.close_button.setToolTip(_("Close"))
        self.browse_button.setToolTip(_("Choose where recordings are saved"))
        self.fps_spin.setSuffix(_(" fps"))
        self.countdown_spin.setSuffix(_(" s"))
        self.countdown_spin.setSpecialValueText(_("None"))
        self.autostop_spin.setSuffix(_(" min"))
        self.autostop_spin.setSpecialValueText(_("Never"))
        self.autostop_spin.setToolTip(
            _("Stop on its own after this long - for a process you start and walk "
              "away from")
        )
        self.frame_switch.setToolTip(
            _("A thin red outline round what is being recorded, on every screen in "
              "the set. It stays on your screen and never reaches the file.")
        )
        self.hotkey_draw_edit.setToolTip(
            _("Switches between drawing on the screen and working with the application")
        )
        self.tracks_note.setText(
            _("Two tracks need both the microphone and the system sound.")
        )
        self._set_details_text()
        self._fill_details()
        self.language_note.setText(
            _("Everything changes at once. The player is a separate program and "
              "takes the new language the next time it opens.")
        )

    # ------------------------------------------------------------- devices

    def refresh_devices(
        self,
        mics: Sequence[audio_mod.MicDevice],
        loopbacks: Sequence[audio_mod.LoopbackDevice],
    ) -> None:
        """Rebuild the two device lists, keeping what the settings asked for.

        Enumeration is the caller's job - it owns the ffmpeg path and does it off the
        first paint. The panel only shows what it is handed.
        """
        self.mics = list(mics)
        self.loopbacks = list(loopbacks)
        self._devices_known = True
        corrected = False  # bound before the try, so a raise cannot shadow the failure
        self._loading = True
        try:
            self._fill_mic_combo()
            self._fill_system_combo()
            corrected = self._adopt_shown_devices()
        finally:
            self._loading = False
        self._update_enabled()
        if corrected:
            self._write()

    def _fill_mic_combo(self) -> None:
        self.mic_combo.clear()
        self.mic_combo.addItem(_("Off"), _OFF)
        for mic in self.mics:
            self.mic_combo.addItem(mic.name, mic.ffmpeg_id)
        wanted = self.settings.mic_device
        if self.settings.mic_enabled and wanted:
            index = self.mic_combo.findData(wanted)
            if index < 0:
                # The remembered device is not plugged in. Keep it on the list rather
                # than silently choosing another one, and say so.
                name = self.settings.mic_label or wanted
                self.mic_combo.addItem(_("{name} (not found)").format(name=name), wanted)
                index = self.mic_combo.count() - 1
            self.mic_combo.setCurrentIndex(index)
        elif self.settings.mic_enabled and self.mics:
            self.mic_combo.setCurrentIndex(1)
        else:
            self.mic_combo.setCurrentIndex(0)
        if not self.mics:
            self.mic_combo.setToolTip(_("No microphone was found on this machine."))
        else:
            self.mic_combo.setToolTip(self.mic_combo.currentText())

    def _fill_system_combo(self) -> None:
        self.system_combo.clear()
        self.system_combo.addItem(_("Off"), _OFF)
        self.system_combo.addItem(_("Default playback device"), -1)
        for device in self.loopbacks:
            self.system_combo.addItem(device.name, device.index)
        if self.settings.system_audio_enabled:
            wanted = self.settings.system_audio_device
            index = self.system_combo.findData(wanted)
            if index < 0:
                # The remembered endpoint is not here. Say so rather than quietly
                # selecting the default: `audio.LoopbackFeeder` will fail on this index,
                # and a panel that claims the default device hides the reason. Same
                # treatment as a missing microphone, for the same reason.
                self.system_combo.addItem(
                    _("Device {index} (not found)").format(index=wanted), wanted
                )
                index = self.system_combo.count() - 1
            self.system_combo.setCurrentIndex(index)
        else:
            self.system_combo.setCurrentIndex(0)
        self.system_combo.setToolTip(self.system_combo.currentText())

    def _adopt_shown_devices(self) -> bool:
        """Make the `Settings` object agree with what the two combo boxes now show.

        The fills above resolve a selection the settings did not fully pin down: the
        first microphone when one is asked for but none is named, "Off" when a source is
        on and the machine has nothing to offer. Those are decisions, and until they are
        written back the panel and `recorder.RecordingPlan` disagree -
        `has_mic` is ``bool(mic_device)``, so a panel showing a microphone over an empty
        `mic_device` records silence.

        It corrects nothing until `refresh_devices` has been called at least once. An
        empty device list before that means "not asked yet", not "none on this machine",
        and turning the microphone off - or adopting a fallback - on the strength of a
        list nobody has filled in would throw away what the user chose last session.

        Returns whether anything was written, so the caller emits `changed` once.
        """
        if not self._devices_known:
            return False
        settings = self.settings
        wrote = False

        data = self.mic_combo.currentData()
        if data == _OFF or data is None:
            if settings.mic_enabled:
                # Asked for a microphone, and there is none on this machine to offer.
                settings.mic_enabled = False
                wrote = True
        else:
            device = str(data)
            if not settings.mic_enabled:
                settings.mic_enabled = True
                wrote = True
            if settings.mic_device != device:
                settings.mic_device = device
                wrote = True
            name = next(
                (mic.name for mic in self.mics if mic.ffmpeg_id == device), None
            )
            # Only a device that is really present names itself. The "(not found)" entry
            # keeps the remembered label instead of rewriting it with the suffix.
            if name is not None and settings.mic_label != name:
                settings.mic_label = name
                wrote = True

        data = self.system_combo.currentData()
        if data == _OFF or data is None:
            if settings.system_audio_enabled:
                settings.system_audio_enabled = False
                wrote = True
        else:
            index = int(data)
            if not settings.system_audio_enabled:
                settings.system_audio_enabled = True
                wrote = True
            if settings.system_audio_device != index:
                settings.system_audio_device = index
                wrote = True

        return wrote

    # ------------------------------------------------------------- settings

    def refresh_from_settings(self) -> None:
        """Push the `Settings` object into every control.

        Nothing is written back except a device the settings left unresolved: see
        `_adopt_shown_devices`.
        """
        corrected = False  # bound before the try, so a raise cannot shadow the failure
        self._loading = True
        try:
            settings = self.settings
            # An empty `language` is "never chosen", so the row shows the language the
            # program is actually speaking rather than preselecting English.
            index = self.language_combo.findData(
                settings.language or i18n.language()
            )
            self.language_combo.setCurrentIndex(max(0, index))
            self.fps_spin.setValue(settings.framerate)
            index = self.size_combo.findData(settings.max_height)
            self.size_combo.setCurrentIndex(max(0, index))
            self.webcam_switch.setChecked(bool(settings.webcam_enabled))
            self._fill_cameras()
            index = self.webcam_combo.findData(settings.webcam_device)
            self.webcam_combo.setCurrentIndex(max(0, index))
            index = self.webcam_corner_combo.findData(settings.webcam_corner)
            self.webcam_corner_combo.setCurrentIndex(max(0, index))
            index = self.webcam_size_combo.findData(settings.webcam_size)
            self.webcam_size_combo.setCurrentIndex(max(0, index))
            index = self.webcam_shape_combo.findData(settings.webcam_shape)
            self.webcam_shape_combo.setCurrentIndex(max(0, index))
            self._fill_resolutions()
            index = self.webcam_res_combo.findData(settings.webcam_resolution)
            self.webcam_res_combo.setCurrentIndex(max(0, index))
            index = self.profile_combo.findData(settings.profile)
            self.profile_combo.setCurrentIndex(max(0, index))
            index = self.quality_combo.findData(settings.quality_level)
            self.quality_combo.setCurrentIndex(max(0, index))
            self._fill_mic_combo()
            self._fill_system_combo()
            corrected = self._adopt_shown_devices()
            index = self.tracks_combo.findData(settings.audio_mode)
            self.tracks_combo.setCurrentIndex(max(0, index))
            self.cursor_switch.set_checked_now(settings.draw_mouse)
            self.clicks_switch.set_checked_now(settings.click_highlight)
            self.frame_switch.set_checked_now(
                bool(getattr(settings, CAPTURE_FRAME_ATTR, CAPTURE_FRAME_DEFAULT))
            )
            self.bar_switch.set_checked_now(settings.show_island)
            self.countdown_spin.setValue(settings.countdown)
            self.folder_edit.setText(str(settings.resolved_output_dir()))
            self.folder_edit.setToolTip(self.folder_edit.text())
            self.hotkey_edit.setText(settings.hotkey)
            self.hotkey_pause_edit.setText(getattr(settings, "hotkey_pause", ""))
            self.hotkey_draw_edit.setText(getattr(settings, "hotkey_annotate", ""))
            self.autostop_spin.setValue(getattr(settings, "auto_stop_minutes", 0))
            index = self.after_combo.findData(getattr(settings, "after_recording", ""))
            self.after_combo.setCurrentIndex(max(0, index))
            self._refresh_preset()
            self._say_hotkey("")
        finally:
            self._loading = False
        self._update_enabled()
        if corrected:
            # Pushing the settings in is not a change, but resolving a device that the
            # settings did not name is one, and the caller has to hear about it.
            self._write()

    def set_capture_size(self, width: int, height: int) -> None:
        """Tell the panel what is actually being captured, for the size estimate.

        The panel does not know the source - that is the bar's business - so until it is
        told it estimates for `DEFAULT_CAPTURE` and the line says it is an estimate.
        """
        size = (max(1, int(width)), max(1, int(height)))
        if size != self._capture_size:
            self._capture_size = size
            # The size note says what the chosen height does to *this* screen, so it
            # is wrong the moment the screen changes.
            self._write_size_note()
            self._update_estimate()

    def capture_size(self) -> tuple[int, int]:
        return self._capture_size

    def _write(self) -> None:
        """One place to announce a write, so no handler can forget to.

        The preset label is recomputed here too: it is derived from the settings,
        so any change by hand drops it back to Custom rather than leaving a
        preset name on a set of values that is no longer that preset.
        """
        if not self._loading:
            was = self._loading
            self._loading = True
            try:
                self._refresh_preset()
            finally:
                self._loading = was
        self.changed.emit()

    # -------------------------------------------------------------- handlers

    def _refresh_preset(self) -> None:
        """Show which preset the settings currently are, or Custom.

        Derived from the settings rather than remembered, so changing one control by
        hand drops the label back to Custom instead of lying about what is set.
        """
        current = presets_mod.current(self.settings)
        key = current.key if current is not None else ""
        index = self.preset_combo.findData(key)
        self.preset_combo.setCurrentIndex(max(0, index))
        self.preset_note.setText(_(current.note) if current is not None else "")
        self.preset_note.setVisible(bool(current is not None and current.note))

    def _on_preset(self, _index: int) -> None:
        if self._loading:
            return
        key = self.preset_combo.currentData()
        if not key:
            return
        preset = next((p for p in presets_mod.BUILTIN if p.key == key), None)
        if preset is None:
            return
        preset.apply_to(self.settings)
        self.settings.preset = preset.key
        self.refresh_from_settings()
        self._write()

    def _on_autostop(self, value: int) -> None:
        if self._loading:
            return
        self.settings.auto_stop_minutes = int(value)
        self._write()

    def _on_after(self, _index: int) -> None:
        if self._loading:
            return
        self.settings.after_recording = self.after_combo.currentData() or ""
        self._write()

    def _on_hotkey_pause(self) -> None:
        self._set_hotkey_field(
            self.hotkey_pause_edit, "hotkey_pause", "Ctrl+Alt+P"
        )

    def _on_hotkey_draw(self) -> None:
        self._set_hotkey_field(
            self.hotkey_draw_edit, "hotkey_annotate", "Ctrl+Alt+D"
        )

    def _set_hotkey_field(self, field, attribute: str, placeholder: str) -> None:
        """Validate and store one hotkey, refusing bad text in words.

        An empty field is allowed and means the shortcut is off; anything else has to
        parse, or the setting is left alone and the old text put back - silently
        accepting "Ctrl+" would give a shortcut that never fires and no reason why.
        """
        if self._loading:
            return
        text = field.text().strip()
        if not text:
            setattr(self.settings, attribute, "")
            self._say_hotkey("")
            self._write()
            return
        parsed = hotkeys_mod.parse_hotkey(text)
        if parsed is None:
            self._say_hotkey(
                "\u201c{text}\u201d is not a shortcut this understands. "
                "Try something like {placeholder}.",
                text=text,
                placeholder=placeholder,
            )
            field.setText(getattr(self.settings, attribute, "") or "")
            return
        setattr(self.settings, attribute, hotkeys_mod.format_hotkey(*parsed))
        field.setText(getattr(self.settings, attribute))
        self._say_hotkey("")
        self._write()

    def _size_items(self) -> list[tuple[str, object]]:
        """The sizes, largest first, with the screen's own size at the top."""
        items: list[tuple[str, object]] = [(_("As on screen"), 0)]
        for height in config_mod.MAX_HEIGHTS:
            if height:
                items.append((_("{height}p").format(height=height), height))
        return items

    def _on_size(self, _index: int) -> None:
        value = self.size_combo.currentData()
        self.settings.max_height = int(value) if value else 0
        self._write_size_note()
        self.changed.emit()

    def _write_size_note(self) -> None:
        """One line on what this choice does to the screen that is about to be captured.

        Three different things to say, and the middle one is the reason this note
        exists: a size at or above the screen's own is not an error and not a smaller
        file either, it simply does nothing.
        """
        height = int(self.settings.max_height or 0)
        if not height:
            self.size_note.setText("")
            self.size_note.setVisible(False)
            return
        self.size_note.setVisible(True)
        screen = int(self._capture_size[1] or 0)
        if screen and screen <= height:
            self.size_note.setText(
                _("This screen is {screen}p, so nothing is scaled and nothing is saved.")
                .format(screen=screen)
            )
            return
        self.size_note.setText(
            _("Scaled down while recording. That costs a copy through system memory, "
              "and the frame rate held when it was measured.")
        )

    def _corner_items(self) -> list[tuple[str, object]]:
        return [
            (_("Top left"), "top_left"),
            (_("Top right"), "top_right"),
            (_("Bottom left"), "bottom_left"),
            (_("Bottom right"), "bottom_right"),
        ]

    def _webcam_size_items(self) -> list[tuple[str, object]]:
        return [(_("Small"), "small"), (_("Medium"), "medium"), (_("Large"), "large")]

    def _fill_cameras(self) -> None:
        """List the cameras Windows reports, or say there are none.

        The import is late on purpose: `webcam` pulls in QtMultimedia, and a settings
        window that has never been opened should not pay for a camera stack.
        """
        try:
            from .. import webcam as webcam_mod

            found = webcam_mod.cameras()
        except Exception as exc:  # noqa: BLE001 - somebody else's hardware
            found = []
            self.webcam_note.setText(
                _("The camera list could not be read: {reason}").format(reason=exc)
            )
            self.webcam_note.setVisible(True)
        else:
            self.webcam_note.setVisible(not found)
            if not found:
                self.webcam_note.setText(_("No camera was found on this machine."))
        items: list[tuple[str, object]] = [(_("First one found"), "")]
        for device in found:
            items.append((device.description(), device.description()))
        self._fill(self.webcam_combo, items)

    def _on_webcam_enabled(self, on: bool) -> None:
        self.settings.webcam_enabled = bool(on)
        self._update_enabled()
        self.changed.emit()

    def _webcam_shape_items(self) -> list[tuple[str, object]]:
        return [
            (_("Rounded rectangle"), "rounded"),
            (_("Circle"), "circle"),
            (_("Rectangle"), "rectangle"),
        ]

    def _fill_resolutions(self) -> None:
        """The chosen camera's own modes, with the frame rate each can really do.

        The frame rate is on the label on purpose. This camera's two largest modes run
        at 2 fps - measured - so a list of bare resolutions would invite somebody to
        pick the biggest and end up with a slide show in the corner of their lesson.
        """
        items: list[tuple[str, object]] = [(_("Choose for me"), "")]
        try:
            from .. import webcam as webcam_mod

            device = webcam_mod.camera_named(self.settings.webcam_device)
            modes = webcam_mod.resolutions_of(device) if device is not None else []
        except Exception:  # noqa: BLE001 - somebody else's hardware
            modes = []
        for width, height, rate in modes:
            items.append((
                _("{width}×{height} · {fps} fps").format(
                    width=width, height=height, fps=int(round(rate))
                ),
                f"{width}x{height}",
            ))
        self._fill(self.webcam_res_combo, items)

    def _on_webcam_device(self, _index: int) -> None:
        value = self.webcam_combo.currentData()
        self.settings.webcam_device = str(value or "")
        # A different camera has different modes, and the one that was chosen may not
        # exist on it.
        was = self.settings.webcam_resolution
        self._fill_resolutions()
        index = self.webcam_res_combo.findData(was)
        if index < 0:
            self.settings.webcam_resolution = ""
            index = 0
        self.webcam_res_combo.setCurrentIndex(index)
        self.changed.emit()

    def _on_webcam_shape(self, _index: int) -> None:
        value = self.webcam_shape_combo.currentData()
        if value:
            self.settings.webcam_shape = str(value)
            self.changed.emit()

    def _on_webcam_resolution(self, _index: int) -> None:
        value = self.webcam_res_combo.currentData()
        self.settings.webcam_resolution = str(value or "")
        self.changed.emit()

    def _on_webcam_corner(self, _index: int) -> None:
        value = self.webcam_corner_combo.currentData()
        if value:
            self.settings.webcam_corner = str(value)
            self.changed.emit()

    def _on_webcam_size(self, _index: int) -> None:
        value = self.webcam_size_combo.currentData()
        if value:
            self.settings.webcam_size = str(value)
            self.changed.emit()

    def _on_framerate(self, value: int) -> None:
        if self._loading:
            return
        self.settings.framerate = int(value)
        self._update_estimate()
        self._write()

    def _on_profile(self, _index: int) -> None:
        if self._loading:
            return
        key = self.profile_combo.currentData()
        self.settings.profile = key or config_mod.DEFAULT_PROFILE
        self._update_estimate()
        self._write()

    def _on_quality(self, _index: int) -> None:
        if self._loading:
            return
        level = self.quality_combo.currentData()
        self.settings.quality_level = level or config_mod.DEFAULT_QUALITY
        self._update_estimate()
        self._write()

    def _on_mic(self, _index: int) -> None:
        if self._loading:
            return
        data = self.mic_combo.currentData()
        if data == _OFF or data is None:
            self.settings.mic_enabled = False
        else:
            self.settings.mic_enabled = True
            self.settings.mic_device = str(data)
            self.settings.mic_label = self.mic_combo.currentText()
        self.mic_combo.setToolTip(self.mic_combo.currentText())
        self._update_enabled()
        self._write()

    def _on_system(self, _index: int) -> None:
        if self._loading:
            return
        data = self.system_combo.currentData()
        if data == _OFF or data is None:
            self.settings.system_audio_enabled = False
        else:
            self.settings.system_audio_enabled = True
            self.settings.system_audio_device = int(data)
        self.system_combo.setToolTip(self.system_combo.currentText())
        self._update_enabled()
        self._write()

    def _on_tracks(self, _index: int) -> None:
        if self._loading:
            return
        mode = self.tracks_combo.currentData()
        self.settings.audio_mode = mode or config_mod.AUDIO_MIX
        self._write()

    def _on_cursor(self, checked: bool) -> None:
        if self._loading:
            return
        self.settings.draw_mouse = bool(checked)
        self._write()

    def _on_clicks(self, checked: bool) -> None:
        if self._loading:
            return
        self.settings.click_highlight = bool(checked)
        self._write()

    def _on_frame(self, checked: bool) -> None:
        if self._loading:
            return
        # See the module docstring: `config.Settings` has no field for this yet, so it
        # lives on the object and is not written to the JSON file.
        setattr(self.settings, CAPTURE_FRAME_ATTR, bool(checked))
        self._write()

    def _on_bar(self, checked: bool) -> None:
        if self._loading:
            return
        self.settings.show_island = bool(checked)
        self._write()

    def _on_countdown(self, value: int) -> None:
        if self._loading:
            return
        self.settings.countdown = int(value)
        self._write()

    def _browse(self) -> None:
        start = self.folder_edit.text() or str(self.settings.resolved_output_dir())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, _("Where should recordings go?"), start
        )
        if not chosen:
            return  # the user cancelled; nothing to report and nothing to change
        self.settings.output_dir = chosen
        self.folder_edit.setText(chosen)
        self.folder_edit.setToolTip(chosen)
        self._write()

    def _on_hotkey(self) -> None:
        """Refuse an unusable combination in words, and leave the setting alone."""
        if self._loading:
            return
        text = self.hotkey_edit.text().strip()
        parsed = hotkeys_mod.parse_hotkey(text)
        if parsed is None:
            # Two templates rather than one with "an empty box" substituted into it:
            # the piece that would be substituted is itself a translated phrase, and a
            # stored message has to survive a language switch whole.
            if text:
                self._say_hotkey(
                    "{shown} is not a hotkey. Use a modifier and a key, like "
                    "Ctrl+Alt+R - Ctrl, Alt, Shift or Win plus a letter, a digit or "
                    "F1-F24. Still using {hotkey}.",
                    shown=text,
                    hotkey=self.settings.hotkey,
                )
            else:
                self._say_hotkey(
                    "An empty box is not a hotkey. Use a modifier and a key, like "
                    "Ctrl+Alt+R - Ctrl, Alt, Shift or Win plus a letter, a digit or "
                    "F1-F24. Still using {hotkey}.",
                    hotkey=self.settings.hotkey,
                )
            return
        canonical = hotkeys_mod.format_hotkey(*parsed) or text
        self._say_hotkey("")
        if canonical != self.hotkey_edit.text():
            was_blocked = self.hotkey_edit.blockSignals(True)
            try:
                self.hotkey_edit.setText(canonical)
            finally:
                self.hotkey_edit.blockSignals(was_blocked)
        if canonical == self.settings.hotkey:
            return  # nothing changed; do not wake the caller up to re-register it
        self.settings.hotkey = canonical
        self._write()

    def _say_hotkey(self, template: str, **values: str) -> None:
        """Show a complaint about a hotkey box, or clear it with an empty template.

        The English template and its values are kept rather than only the finished
        sentence, so `retranslate` can say the same thing again instead of swallowing
        a message the user has not read yet.
        """
        self._hotkey_message = (template, dict(values)) if template else None
        self._show_hotkey_message()

    def _show_hotkey_message(self) -> None:
        """Paint whatever complaint is current, in the language now in force."""
        if self._hotkey_message is None:
            self.hotkey_problem.setText("")
            self.hotkey_problem.setVisible(False)
        else:
            template, values = self._hotkey_message
            self.hotkey_problem.setText(_(template).format(**values))
            self.hotkey_problem.setVisible(True)
        self._fit()

    def _close(self) -> None:
        self.hide()
        self.closed.emit()

    # ----------------------------------------------------------- derived UI

    def _update_enabled(self) -> None:
        both = self.settings.mic_enabled and self.settings.system_audio_enabled
        self.tracks_combo.setEnabled(both)
        self.tracks_note.setVisible(not both)
        # Which camera, which corner and how big mean nothing with the camera off, and
        # a live control under a switch that is off is an invitation to wonder why it
        # does nothing.
        on = bool(self.settings.webcam_enabled)
        for control in (self.webcam_combo, self.webcam_corner_combo,
                        self.webcam_size_combo, self.webcam_shape_combo,
                        self.webcam_res_combo):
            control.setEnabled(on)
        self._update_estimate()

    def _update_estimate(self) -> None:
        """Say what a minute costs, and admit that it is an estimate.

        The per-minute figures in `config.PROFILES` were measured on a *busy* screen -
        scrolling coloured text - and a real desktop recording came in far under them, so
        the wording has to carry that rather than quote one number as a promise.

        And the table is this machine's NVENC. `storage.size_estimate` is asked instead
        of `config.estimate_mb_per_minute`, because on a machine recording through Media
        Foundation `docs/ENCODERS.md` §3 puts the real cost at up to 4.9x the table -
        somebody else's number, shown as if it were theirs. `size_estimate` answers with
        this machine's own recordings once there are any and falls back to the same
        table until then, so the figure never gets worse; when it is a real measurement
        the sentence says so, because "estimated" and "measured here" are not the same
        claim and the user is entitled to know which they are reading.

        The encoder is passed only once the probe has answered: before that there is
        nothing to look a measurement up by, and `_chosen_encoder` would answer for an
        empty machine - `libx264` - which is a key to somebody else's samples.
        """
        profile_key = self.profile_combo.currentData() or config_mod.DEFAULT_PROFILE
        level = self.quality_combo.currentData() or config_mod.DEFAULT_QUALITY
        width, height = self._capture_size
        profile = config_mod.PROFILES.get(profile_key)
        encoder = (
            self._chosen_encoder(profile)
            if (profile is not None and self._encoders_known) else ""
        )
        estimate = storage_mod.size_estimate(
            profile_key, level, width, height, self.fps_spin.value(),
            encoder=encoder,
        )
        rate = estimate.mb_per_minute
        gigabytes = rate * 60 / 1000
        if estimate.measured:
            self.estimate_label.setText(
                _("About {rate} MB a minute at {width}×{height} ({gigabytes} GB an "
                  "hour) - measured on this machine, from what {encoder} has already "
                  "written.").format(
                    rate=f"{rate:.0f}",
                    width=width,
                    height=height,
                    gigabytes=f"{gigabytes:.1f}",
                    encoder=estimate.encoder,
                )
            )
        else:
            self.estimate_label.setText(
                _("Estimated {rate} MB a minute at {width}×{height} ({gigabytes} GB an "
                  "hour) on a busy screen - much less on a still one.").format(
                    rate=f"{rate:.0f}",
                    width=width,
                    height=height,
                    gigabytes=f"{gigabytes:.1f}",
                )
            )
        self.profile_note.setText(_(profile.note) if profile else "")
        self._refresh_encoding()
        self._fit()

    def _fit(self) -> None:
        """Grow or shrink to the content. The 4:4:4 note must never be truncated.

        `sizeHint().height()` is the wrong figure here and it is wrong in both
        directions. A `QLabel` with `wordWrap` reports a size hint for the width it
        would *like*, not for the 360 px it is going to get, so at the 4:4:4 note it
        asked for 796 px where the wrapped content needs 838 and the warning was clipped;
        with wider system fonts it overshoots instead and leaves dead space under the
        hotkey row. `totalHeightForWidth` is the only figure that asks the layout how
        tall it is *at this width*, margins included.
        """
        layout = self.layout()
        if layout is None:
            return
        width = self.width() or WIDTH
        if layout.hasHeightForWidth():
            wanted = layout.totalHeightForWidth(width)
        else:
            # No wrapping child in the tree - nothing to ask, so the hint is the answer.
            wanted = layout.sizeHint().height()
        wanted = max(wanted, layout.totalMinimumSize().height())
        if wanted != self.height():
            self.setFixedHeight(wanted)

    # -------------------------------------------------------- which encoder

    def set_encoders(
        self, results: Sequence[capabilities_mod.EncoderResult]
    ) -> None:
        """Hand the panel what the encoder probe found on this machine.

        The application calls this with `Application.encoders`, next to
        `set_capture_size`. Before it is called the panel shows nothing about encoding
        and offers every format: see the module docstring for why silence beats a guess.

        Handing it an empty list *is* an answer - "the probe ran and nothing works" -
        and is not the same as never calling it.
        """
        self._encoders = list(results)
        self._encoders_known = True
        self._fill_details()
        self._update_enabled()

    def encoders(self) -> list[capabilities_mod.EncoderResult]:
        """What the panel was told, so a caller need not keep a second copy."""
        return list(self._encoders)

    def _chosen_encoder(self, profile: config_mod.Profile) -> str:
        """The ffmpeg encoder this machine would really use for `profile`.

        `capabilities.choose` is the one answer, and the panel asks it rather than
        working anything out of its own: a second opinion here is a second chooser to
        keep in step with the recorder.

        `Settings.encoder_choice` stays the JSON-only escape hatch it is documented as -
        there is no control for it on this panel, deliberately - but if somebody has
        written a real encoder name into the file then that is what will be recorded
        with, and this line has to say so instead of describing a choice nobody made.
        Anything else in that field, "auto" and "" included, means "let the probe
        decide".
        """
        wanted = getattr(self.settings, "encoder_choice", "auto")
        if isinstance(wanted, str) and encoders_mod.family_of(wanted):
            return wanted
        name, _reason = capabilities_mod.choose(self._encoders, profile)
        return name

    def _path_sentence(self, profile: config_mod.Profile) -> str:
        """Which class of encoding is in force, in words, naming the codec.

        Three classes, and the processor is three sentences rather than one, because
        "no hardware encoder works on this machine" is false in two of the three cases
        it used to be said in:

        * a `software_only` profile is on the processor because it was asked to be;
        * a 4:4:4 profile on an AMD, Intel or Media-Foundation machine is on the
          processor because none of those encoders takes 4:4:4 - reproduced here on
          2026-11-05 with the probe rows `hevc_mf, h264_mf, libx264`, where the panel
          offered the format, chose `libx264` for it, and told the user their hardware
          encoder does not work while Media Foundation was working perfectly well;
        * and only when nothing on the machine encodes at all is the old sentence true.
        """
        name = self._chosen_encoder(profile)
        family = encoders_mod.family_of(name)
        codec = CODEC_LABELS.get(encoders_mod.codec_of(name), name)
        if family == "cpu":
            if profile.software_only:
                return _(
                    "This format asks for the processor, so {codec} is encoded there "
                    "whatever the graphics card can do - processor time the program "
                    "you are recording may need."
                ).format(codec=codec)
            if self._hardware_works():
                return _(
                    "This machine's hardware encoders cannot record this format, so "
                    "{codec} is encoded by the processor - processor time the program "
                    "you are recording may need."
                ).format(codec=codec)
            return _(
                "No hardware encoder works on this machine, so {codec} is encoded by "
                "the processor - processor time the program you are recording may "
                "need."
            ).format(codec=codec)
        chain = encoders_mod.frame_path(name, pix_fmt=profile.pix_fmt)
        if "hwdownload" in chain:
            return _(
                "Recorded by the graphics card in {codec}, but every frame is copied "
                "through system memory on the way - between half a processor core and "
                "one and a half at 2560×1440 and 60 fps."
            ).format(codec=codec)
        return _(
            "Recorded by the graphics card in {codec}. The frame never leaves it, and "
            "ffmpeg costs about a twentieth of a processor core."
        ).format(codec=codec)

    def _hardware_works(self) -> bool:
        """Whether anything on this machine encodes without the processor.

        The probe's own rows, not a vendor name: a family counts when one of its
        encoders really encoded a frame here.
        """
        return any(
            encoders_mod.family_of(result.name) not in ("", "cpu")
            for result in capabilities_mod.working(self._encoders)
        )

    def _unavailable_reason(self, profile: config_mod.Profile) -> str:
        """Why this machine cannot record `profile` at all, or "" when it can.

        Only 4:4:4 can be refused today, and it is refused on a measurement rather than
        on a vendor name: `encoders.supports_444` is what this build's own
        `ffmpeg -h encoder=...` lists, confirmed against the real ddagrab pipeline.
        Offering it anyway is worse than hiding it, because `encoders.frame_path`
        rewrites `yuv444p` to `nv12` for the Media Foundation family - the command runs,
        the file plays, and three quarters of the colour samples the format was chosen
        for are gone.

        Nothing is refused while the probe says nothing works at all: that is a machine
        in trouble, the application already says so on the bar, and greying out every
        format on top of it helps nobody.
        """
        if not self._encoders_known:
            return ""
        usable = capabilities_mod.working(self._encoders)
        if not usable:
            return ""
        if profile.pix_fmt == "yuv444p" and not any(
            encoders_mod.supports_444(result.name) for result in usable
        ):
            return _(
                "No encoder on this machine takes 4:4:4, so this format cannot be "
                "recorded here."
            )
        return ""

    def _apply_profile_availability(self) -> None:
        """Grey out the formats this machine cannot encode, with the reason on hover.

        The reason is also shown under the picker whenever the greyed-out one is the
        chosen format - a settings file written on a machine with NVENC and opened on
        one without it comes up exactly like that, and a disabled row with no
        explanation is the worst of both.
        """
        model = self.profile_combo.model()
        if not isinstance(model, QtGui.QStandardItemModel):
            # A view this panel did not build. Nothing to disable, and guessing at
            # another model's flags is how a list stops being selectable at all.
            return
        for index in range(self.profile_combo.count()):
            key = self.profile_combo.itemData(index)
            profile = config_mod.PROFILES.get(key) if isinstance(key, str) else None
            item = model.item(index)
            if profile is None or item is None:
                continue
            reason = self._unavailable_reason(profile)
            item.setEnabled(not reason)
            self.profile_combo.setItemData(
                index, reason, QtCore.Qt.ItemDataRole.ToolTipRole
            )

    def _refresh_encoding(self) -> None:
        """Say which path is in force, or say nothing - never guess."""
        profile_key = self.profile_combo.currentData() or config_mod.DEFAULT_PROFILE
        profile = config_mod.PROFILES.get(profile_key)
        if profile is None:
            profile = config_mod.PROFILES[config_mod.DEFAULT_PROFILE]
        self._apply_profile_availability()
        problem = self._unavailable_reason(profile)
        self.profile_problem.setText(problem)
        self.profile_problem.setVisible(bool(problem))
        self.details_button.setVisible(self._encoders_known)
        self.details_box.setVisible(
            self._encoders_known and self.details_button.isChecked()
        )
        if not self._encoders_known or problem:
            # Nothing has been probed, or the chosen format will not be recorded at
            # all. Either way there is no path to describe.
            self.encoding_note.setText("")
            self.encoding_note.setVisible(False)
            return
        self.encoding_note.setText(self._path_sentence(profile))
        self.encoding_note.setVisible(True)

    def _set_details_text(self) -> None:
        """The expander's own caption. The triangle is not translated: it is a shape."""
        arrow = "\u25be" if self.details_button.isChecked() else "\u25b8"
        self.details_button.setText(f"{arrow} {_('Details')}")

    def _fill_details(self) -> None:
        """The probe table as it stands: name, vendor, and the refusal when there is one.

        Every candidate, including the ones this ffmpeg was not built with, because the
        question somebody opens this for is "why is it not using my card" and an absent
        row answers nothing.

        `EncoderResult.reason` is ffmpeg's own sentence and is shown untranslated. It is
        not a phrase this program wrote, it is evidence, and translating evidence makes
        it unsearchable.
        """
        while self._details_layout.count():
            item = self._details_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for result in self._encoders:
            if result.works:
                text = _("{name} · {vendor} — works").format(
                    name=result.name, vendor=result.vendor
                )
                row = self._note(text)
                row.setStyleSheet(
                    f"color: {theme.COLOURS['ok']}; background: transparent;"
                )
            else:
                text = _("{name} · {vendor} — did not work: {reason}").format(
                    name=result.name,
                    vendor=result.vendor,
                    reason=result.reason or _("no reason given"),
                )
                row = self._note(text)
            self._details_layout.addWidget(row)

    def _on_details(self, shown: bool) -> None:
        self._set_details_text()
        self.details_box.setVisible(bool(shown) and self._encoders_known)
        self._fit()

    # -------------------------------------------------------------- dragging

    def _in_header(self, point: QtCore.QPoint) -> bool:
        """Whether `point` (in panel coordinates) is on the title row.

        The band reaches the top of the panel, so the 12 px margin above the title is
        draggable too - that is where the pointer lands when someone grabs the top of
        the window.
        """
        return point.y() <= self.header.geometry().bottom()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        point = event.position().toPoint()
        # The close button is in this row and must still close. It accepts its own
        # press, so it never reaches here, but `control_at` says so rather than
        # relying on that - and it covers whatever else the header grows.
        if not self._in_header(point) or surfaces.control_at(self, point) is not None:
            event.ignore()
            return
        self._drag.begin(event.globalPosition().toPoint())
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._drag.active():
            event.ignore()
            return
        self._drag.to(event.globalPosition().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self._drag.end():
            # Only a real drag is remembered. A press that did not travel is a click
            # on the title, which means nothing and must not pin the panel in place.
            self._dropped_at = (self.x(), self.y())
        event.accept()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        # Hidden mid-drag the panel never sees the release, and a half-finished drag
        # would move it on the first mouse move after it came back.
        self._drag.cancel()
        super().hideEvent(event)

    def dropped_at(self) -> tuple[int, int] | None:
        """Where the user dragged the panel to this session, or None."""
        return self._dropped_at

    # -------------------------------------------------------------- placing

    def popup_near(self, widget: QtWidgets.QWidget) -> None:
        """Show below `widget` if the panel fits there, otherwise above; always on screen.

        Unlike every other surface in this interface, the panel activates itself: the
        hotkey box and the two spin boxes are useless without the keyboard. See the
        module docstring for why that is an exception and not a slip.

        Once the panel has been dragged somewhere by hand, that is where it reopens -
        moving it back under the gear would throw away what the user just did. It is
        still clamped, because the screen it was dropped on may have been unplugged
        since.
        """
        self._fit()
        if self._dropped_at is not None:
            self._show_at(*self._clamped(*self._dropped_at))
            return
        anchor = widget.mapToGlobal(QtCore.QPoint(0, 0))
        anchor_rect = QtCore.QRect(anchor, widget.size())
        screen = QtGui.QGuiApplication.screenAt(anchor_rect.center())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        area = (
            screen.availableGeometry()
            if screen is not None
            else QtCore.QRect(0, 0, 1920, 1080)
        )

        gap = 8
        width, height = self.width(), self.height()
        below = anchor_rect.bottom() + gap
        above = anchor_rect.top() - gap - height
        if below + height <= area.bottom():
            y = below
        elif above >= area.top():
            y = above
        else:
            y = max(area.top(), area.bottom() - height)

        x = anchor_rect.center().x() - width // 2
        x = max(area.left(), min(x, area.right() - width))
        self._show_at(x, y)

    def _show_at(self, x: int, y: int) -> None:
        """Move there and show, activating as `popup_near` documents."""
        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self.activateWindow()

    def _clamped(self, x: int, y: int) -> tuple[int, int]:
        """A remembered position, pulled back onto a screen that still exists.

        `ui.bar`'s clamp against `winapi.list_monitors()`, so a panel dropped on a
        monitor that has since been unplugged comes back somewhere it can be reached.
        Only the remembered position goes through this: the anchored placement above
        is already measured against the `QScreen` the gear is on, including the task
        bar, and re-clamping it against the raw monitor rectangle would undo that.
        """
        try:
            monitors = winapi.list_monitors()
        except Exception as exc:  # DXGI fails on a remote session or a driver reload
            print(f"settings_panel: list_monitors() failed ({exc!r}); not clamping.")
            return int(x), int(y)
        if not monitors:
            return int(x), int(y)
        return bar_geometry.clamp_to_monitors(
            int(x), int(y), self.width(), self.height(), monitors
        )
