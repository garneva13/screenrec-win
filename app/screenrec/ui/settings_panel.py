"""The settings window: a sidebar, five pages, and one place that writes the settings.

It was one column 1375 px tall until 2026-11-08, which was taller than the screen it
had to open on and could not be photographed to show anybody. The settings themselves
did not change; the way they are reached did.

**The shape.** A title band, a sidebar of five pages, the chosen page, and a footer that
says what happens to a change and carries the one button that closes the window. The
sidebar and the footer never move. Only the page scrolls, and it scrolls rather than
being squeezed: at 150% and 200% Windows scaling a scroll bar is honest and a clipped
control is not.

**What is on which page** follows what somebody is looking for, not what the code does:

* *Recording* - the profile, the four video settings, what a minute costs, where files go;
* *Sound* - the microphone, the system sound, and whether they share one track;
* *Camera* - the corner picture, with a diagram of where it will sit;
* *Control* - what is on screen while you record, the timers, and the three shortcuts;
* *General* - the language.

Three rules shape the layout:

* **A field is a caption above a control**, and two of them sit side by side. The grid
  drops to one column below `settings_widgets.FieldGrid.NARROW`, because a German or
  Russian caption in half of 600 px is not the same width as an English one.
* **The control column no longer has a fixed width.** It had one when the window was a
  360 px popover and a wide combo box would have moved the layout; in a two-column grid
  each control simply fills its cell, and a long value elides inside it.
* **Nothing is truncated that the user needs to read.** The profile's ``note`` - where
  the 4:4:4 "Windows' own player does not decode this" warning lives - word wraps across
  the full width of the page.

**The title row is the handle.** A frameless window has no title bar, so pressing the
title and dragging moves the window - `surfaces.SurfaceDrag`, the same press / 2 px slop
/ snap-to-edge / clamp the bar is moved by. Where it was dropped is remembered **for the
session**, and `popup_near` reopens it there instead of under the gear.

The panel writes straight into the `config.Settings` object it was handed and emits
`changed`. **It never saves the file** - the application owns that, and one writer for
one file is the only way the atomic write in `config.save_settings` stays meaningful.
Everything is applied as it is touched; the footer says so, and says something different
while a recording is running, because then the video and audio settings are describing
the *next* take and the controls for them are disabled.

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
which are all mouse. This window is read with the keyboard: it has three shortcut boxes
and a sidebar that answers the arrow keys, and a window carrying `WS_EX_NOACTIVATE` is
never activated by a click, so every keystroke would go to the application behind it. So
it opts out of `surfaces._no_activate` (see `_styled` in `__init__`) and activates itself
in `popup_near`, the way `overlay.RegionSelector` does. `Qt.Tool` still keeps it out of
Alt+Tab; only the no-activate bit is given up.

**Editing a shortcut stands the global hotkeys down.** `settings_widgets.HotkeyEdit`
reads a real key press, and a combination registered through `RegisterHotKey` never
reaches any window - Windows takes it. Without suspending them, editing the
start/stop shortcut would start a recording and the box would read nothing at all. The
panel passes `hotkey_capture` up to the application, which stops the listeners while a
box is armed and starts them again when it is finished.

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

**The language row changes the language of the whole program, not of this window.** It
calls `i18n.set_language`, which tells every window through `i18n.notifier`; this panel
listens to that notifier like any other window and rebuilds its own text in
`retranslate`, so a switch made with the window open changes the window under the
pointer. Every string on it therefore has to be re-readable: nothing translated may be
built once and forgotten, which is why `_build` registers its captions and the combo
boxes are filled from the `_*_items` methods that `retranslate` calls again. The row
writes `config.Settings.language` and emits `changed` like every other control - the
application still owns the file, for the reason above.
"""
from __future__ import annotations

from pathlib import Path
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
from .settings_widgets import (
    CameraDiagram,
    FieldGrid,
    HotkeyEdit,
    InfoButton,
    NavButton,
    Switch,
    caption,
    hairline,
)

#: The window. 820x680 was the size the layout was drawn for, and 680 was 40 px short:
#: measured on 2026-11-08 with the longest translation of each page, Recording wants 580
#: and Camera 578 of the 562 a 680 px window leaves them. At 720 both fit and only
#: Control still scrolls - it wants 672, and the 110 px to fit that would have to come
#: out of the spacing between four switches and three shortcut boxes, which is worse
#: than a short scroll. The height is cut down again in `popup_near` on a screen that
#: cannot hold it.
WIDTH = 820
HEIGHT = 720

#: The sidebar. Wide enough for "Управление" at 150% scaling, which is the longest of
#: the five words in either language.
NAV_WIDTH = 176

#: What the estimate assumes before the application tells us the real capture size.
DEFAULT_CAPTURE = (1920, 1080)

#: The capture-frame setting reaches `Settings` through this name; see the module
#: docstring.
CAPTURE_FRAME_ATTR = "capture_frame"
CAPTURE_FRAME_DEFAULT = True

#: Sentinel used in the device combo boxes for "do not record this source at all".
_OFF = "__off__"

#: The pages, in sidebar order: the key, the icon, and the English word.
PAGES: tuple[tuple[str, str, str], ...] = (
    ("recording", "record", "Recording"),
    ("sound", "speaker", "Sound"),
    ("camera", "camera", "Camera"),
    ("control", "keyboard", "Control"),
    ("general", "settings", "General"),
)

#: Frame rates offered by name. A rate that is in the settings and not on this list is
#: added to it when the window opens, so a value set by hand in the JSON survives being
#: looked at - the list is a convenience, not a new limit.
FRAME_RATES: tuple[int, ...] = (24, 30, 48, 60, 90, 120, 144)

#: Countdowns and automatic stops, in the units they are stored in. Both used to be spin
#: boxes, which asked somebody to find "Never" by holding an arrow key down to zero.
COUNTDOWNS: tuple[int, ...] = (0, 3, 5, 10)
AUTO_STOPS: tuple[int, ...] = (0, 5, 10, 15, 30, 45, 60, 90, 120)

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

#: How tall the probe table may grow before it scrolls instead. Twelve candidates, most
#: of them with a wrapped sentence of ffmpeg's underneath, is more than one page should
#: grow by. At 150 px about five rows are visible.
DETAILS_HEIGHT = 150

#: The project's own two addresses. Here rather than in `config`, because they are
#: not settings - nobody edits them and nothing reads them back.
PROJECT_PAGE = "https://garneva13.github.io/screenrec-win/"
PROJECT_SOURCE = "https://github.com/garneva13/screenrec-win"

#: Only these are styled from here. Everything else comes from `theme.style_sheet`; this
#: is the window's own geometry - taller fields than the bar's popovers use, and the
#: armed state of a shortcut box, which no other window has.
_LOCAL_STYLE = """
#settingsPanel QComboBox, #settingsPanel QLineEdit {
    min-height: 22px;
    border-radius: 8px;
}
#settingsPanel QLineEdit[armed="true"] {
    border: 1px solid %(accent)s;
    background-color: %(hover)s;
}
#settingsPanel QPushButton#primary {
    background-color: %(accent)s;
    color: %(accent_text)s;
    border: 1px solid %(accent)s;
    border-radius: 8px;
    padding: 7px 26px;
    font-weight: 600;
}
#settingsPanel QPushButton#primary:hover { background-color: %(accent_hover)s; }
#settingsPanel QPushButton#primary:pressed { background-color: %(accent_press)s; }
#settingsPanel QPushButton#primary:focus { border: 1px solid %(text)s; }
#settingsPanel QPushButton#link {
    background: transparent;
    border: none;
    padding: 2px 0;
    text-align: left;
    color: %(accent_light)s;
}
#settingsPanel QPushButton#link:hover { color: %(text)s; }
#settingsPanel QPushButton#link:focus { text-decoration: underline; }
#settingsPanel QWidget#navPane { background-color: %(panel)s; }
"""


class SettingsPanel(surfaces.FloatingSurface):
    """The gear's window. Writes into the `Settings` it was given; the caller saves."""

    changed = QtCore.Signal()
    closed = QtCore.Signal()
    #: True while a shortcut box is waiting for a key: the application must stand the
    #: global hotkeys down, or Windows eats the press. See the module docstring.
    hotkey_capture = QtCore.Signal(bool)

    # This window is hidden from capture only while a take is running, like every
    # other surface - `surfaces.set_recording`. It was a special case of its own until
    # the bar turned out to have the same problem; the rule belongs in one place.

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
        self.setObjectName("settingsPanel")
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
        #: Whether a take is running. The video and audio settings then describe the
        #: *next* one, so they are disabled and the footer says why.
        self._recording = False
        #: Dragging the title row moves the window; see `mousePressEvent`.
        self._drag = surfaces.SurfaceDrag(self)
        #: Where the user last dropped it, in desktop pixels, or None if never dragged.
        #: For the session only: it is where a window is, not a setting, and
        #: `config.Settings` holds no window geometry at all.
        self._dropped_at: tuple[int, int] | None = None
        #: Every caption on the window, as (widget, its English source, upper-case it),
        #: so `retranslate` can say the same thing again in the other language.
        self._captions: list[tuple[QtWidgets.QLabel, str, bool]] = []
        #: The nav buttons, as (button, its English source).
        self._nav: list[tuple[NavButton, str]] = []
        #: Everything with a tool tip that a translation has to reach, as
        #: (widget, its English source).
        self._tips: list[tuple[QtWidgets.QWidget, str]] = []
        #: The hotkey complaint being shown, as (English template, its values), or None.
        #: Kept rather than just painted so that a language switch can say it again
        #: instead of swallowing a message the user has not read yet.
        self._hotkey_message: tuple[str, dict[str, str]] | None = None

        self.setStyleSheet(_LOCAL_STYLE % theme.COLOURS)
        # Not a fixed size: `popup_near` cuts it down to a screen that cannot hold
        # it, and the two-column grids fall back to one column when that happens.
        self.setMinimumSize(520, 360)
        self.resize(WIDTH, HEIGHT)
        self._build()
        self._wire()
        # One path fills the notes, the tool tips and the lists, at start-up and at
        # every switch, so there is no second copy of any string to fall behind.
        self.retranslate()
        self.refresh_from_settings()
        self.show_page("recording")
        i18n.notifier.changed.connect(self._on_language_switched)

    # ---------------------------------------------------------------- build

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        # One pixel all round, so the surface's own hairline border is not painted over.
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())
        outer.addWidget(hairline())

        body = QtWidgets.QWidget()
        body.setObjectName("formRow")
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_nav())
        divider = QtWidgets.QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background: {theme.COLOURS['line']};")
        body_layout.addWidget(divider)
        self.stack = QtWidgets.QStackedWidget()
        body_layout.addWidget(self.stack, 1)
        outer.addWidget(body, 1)

        outer.addWidget(hairline())
        outer.addWidget(self._build_footer())

        for key, _icon, _word in PAGES:
            self.stack.addWidget(self._scrolled(getattr(self, f"_page_{key}")()))

    def _build_header(self) -> QtWidgets.QWidget:
        """The title row, as a widget rather than a bare layout.

        It is a widget because the window is dragged by it and `_in_header` needs a
        rectangle to measure; `formRow` keeps it transparent.
        """
        holder = QtWidgets.QWidget()
        holder.setObjectName("formRow")
        header = QtWidgets.QHBoxLayout(holder)
        header.setContentsMargins(20, 14, 14, 14)
        header.setSpacing(6)
        title = surfaces.label(_("Settings"), "text", 12.0)
        font = QtGui.QFont(title.font())
        font.setWeight(QtGui.QFont.Weight.DemiBold)
        title.setFont(font)
        self._captions.append((title, "Settings", False))
        header.addWidget(title)
        header.addStretch(1)
        self.close_button = surfaces.IconButton("close", _("Close"), size=28)
        header.addWidget(self.close_button)
        self.header = holder
        return holder

    def _build_nav(self) -> QtWidgets.QWidget:
        pane = QtWidgets.QWidget()
        pane.setObjectName("navPane")
        pane.setFixedWidth(NAV_WIDTH)
        layout = QtWidgets.QVBoxLayout(pane)
        layout.setContentsMargins(10, 14, 10, 14)
        layout.setSpacing(4)
        for _key, icon, word in PAGES:
            button = NavButton(icon, _(word))
            self._nav.append((button, word))
            layout.addWidget(button)
        layout.addStretch(1)
        return pane

    def _build_footer(self) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        holder.setObjectName("formRow")
        layout = QtWidgets.QHBoxLayout(holder)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(12)
        #: What happens to a change. It is not a fixed sentence: while a recording is
        #: running the video and audio settings are describing the next one.
        self.status_label = surfaces.label("", "text_dim", 9.0)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label, 1)
        self.done_button = QtWidgets.QPushButton(_("Done"))
        self.done_button.setObjectName("primary")
        self.done_button.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.done_button.setDefault(True)
        layout.addWidget(self.done_button)
        return holder

    def _scrolled(self, page: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        """Wrap one page so that it, and only it, scrolls."""
        area = QtWidgets.QScrollArea()
        area.setObjectName("formRow")
        area.setWidget(page)
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        return area

    # --------------------------------------------------------- page scaffolding

    def _page(self, title: str, subtitle: str) -> tuple[QtWidgets.QWidget,
                                                        QtWidgets.QVBoxLayout]:
        page = QtWidgets.QWidget()
        page.setObjectName("formRow")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(0)
        heading = surfaces.label(_(title), "text", 14.0)
        font = QtGui.QFont(heading.font())
        font.setWeight(QtGui.QFont.Weight.DemiBold)
        heading.setFont(font)
        self._captions.append((heading, title, False))
        layout.addWidget(heading)
        if subtitle:
            under = surfaces.label(_(subtitle), "text_dim", 9.5)
            under.setWordWrap(True)
            self._captions.append((under, subtitle, False))
            layout.addWidget(under)
        layout.addSpacing(18)
        return page, layout

    def _group(self, layout: QtWidgets.QVBoxLayout, title: str) -> None:
        """A heading over a band of related fields."""
        label = surfaces.label(_(title), "text", 10.0)
        font = QtGui.QFont(label.font())
        font.setWeight(QtGui.QFont.Weight.DemiBold)
        label.setFont(font)
        self._captions.append((label, title, False))
        layout.addWidget(label)
        layout.addSpacing(10)

    def _field(
        self, text: str, control: QtWidgets.QWidget, info: str = ""
    ) -> QtWidgets.QWidget:
        """A caption above a control, with an optional (i) beside the caption."""
        holder = QtWidgets.QWidget()
        holder.setObjectName("formRow")
        layout = QtWidgets.QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QtWidgets.QWidget()
        top.setObjectName("formRow")
        top_layout = QtWidgets.QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)
        label = caption(_(text))
        self._captions.append((label, text, False))
        label.setBuddy(control)
        top_layout.addWidget(label)
        if info:
            button = InfoButton(_(info))
            self._tips.append((button, info))
            top_layout.addWidget(button)
        top_layout.addStretch(1)
        layout.addWidget(top)
        layout.addWidget(control)
        return holder

    def _half(self, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """One field at the width of a grid column, rather than the whole page.

        A combo box 600 px wide to hold the word "Medium" reads as a mistake, and the
        eye has to travel the width of the window to find the arrow.
        """
        holder = QtWidgets.QWidget()
        holder.setObjectName("formRow")
        layout = QtWidgets.QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(widget, 1)
        layout.addStretch(1)
        return holder

    def _row(self, text: str, control: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """A label on the left, a switch on the right - for the on/off settings."""
        holder = QtWidgets.QWidget()
        holder.setObjectName("formRow")
        layout = QtWidgets.QHBoxLayout(holder)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(12)
        label = surfaces.label(_(text), "text_soft", 9.8)
        label.setWordWrap(True)
        self._captions.append((label, text, False))
        label.setBuddy(control)
        layout.addWidget(label, 1)
        layout.addWidget(control)
        return holder

    def _note(self, text: str) -> QtWidgets.QLabel:
        """A full-width, word-wrapping line of explanation under a control."""
        widget = surfaces.label(text, "text_dim", 8.8)
        widget.setWordWrap(True)
        widget.setContentsMargins(0, 2, 0, 0)
        return widget

    def _problem(self, text: str = "") -> QtWidgets.QLabel:
        """A note in the error colour: the reason something cannot be done."""
        widget = self._note(text)
        widget.setStyleSheet(
            f"color: {theme.COLOURS['error_text']}; background: transparent;"
        )
        widget.setVisible(False)
        return widget

    # ------------------------------------------------------------ the pages

    def _page_recording(self) -> QtWidgets.QWidget:
        page, layout = self._page("Recording", "Video and where files are saved")

        self.preset_combo = self._combo()
        self._fill(self.preset_combo, self._preset_items())
        layout.addWidget(self._half(self._field("Profile", self.preset_combo)))
        self.preset_note = self._note("")
        layout.addWidget(self.preset_note)
        layout.addSpacing(20)

        self._group(layout, "Video settings")
        grid = FieldGrid()
        self.fps_combo = self._combo()
        grid.add(self._field("Frame rate", self.fps_combo))
        self.size_combo = self._combo()
        self._fill(self.size_combo, self._size_items())
        grid.add(self._field(
            "Resolution", self.size_combo,
            "The screen's own size, or scaled down while recording. A smaller file, "
            "and a copy through system memory to make it.",
        ))
        self.profile_combo = self._combo()
        self._fill(self.profile_combo, self._profile_items())
        grid.add(self._field(
            "Format", self.profile_combo,
            "The codec and what it keeps of the colour. H.265 is smaller, H.264 plays "
            "everywhere, and 4:4:4 keeps text sharp at the cost of size.",
        ))
        self.quality_combo = self._combo()
        self._fill(self.quality_combo, self._quality_items())
        grid.add(self._field(
            "Quality", self.quality_combo,
            "How hard the encoder tries. It changes the size of the file far more "
            "than it changes what you can see.",
        ))
        layout.addWidget(grid)

        #: What the chosen size will really do, which depends on the screen: on a
        #: 1080p monitor "1080p" changes nothing, and saying so is better than leaving
        #: somebody to wonder why the file is the same size.
        self.size_note = self._note("")
        layout.addWidget(self.size_note)
        self.profile_note = self._note("")
        layout.addWidget(self.profile_note)
        #: Why the chosen format cannot be recorded on this machine, in the idiom of
        #: `Profile.note`. In the error colour, because unlike a note it is the reason
        #: a format in the list above is greyed out.
        self.profile_problem = self._problem()
        layout.addWidget(self.profile_problem)
        layout.addSpacing(12)
        layout.addWidget(self._build_estimate())
        layout.addSpacing(6)

        #: The one line that says which encoding path a recording will take.
        self.encoding_note = self._note("")
        self.encoding_note.setVisible(False)
        layout.addWidget(self.encoding_note)

        self.details_button = QtWidgets.QPushButton("")
        self.details_button.setObjectName("link")
        self.details_button.setCheckable(True)
        self.details_button.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.details_button.setVisible(False)
        layout.addWidget(self.details_button)

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
        layout.addWidget(self.details_box)
        layout.addSpacing(22)

        self._group(layout, "Saving")
        folder_row = QtWidgets.QWidget()
        folder_row.setObjectName("formRow")
        folder_layout = QtWidgets.QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(8)
        self.folder_edit = QtWidgets.QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setMinimumWidth(10)
        self.folder_edit.setCursorPosition(0)
        self.browse_button = surfaces.IconButton(
            "folder", _("Choose where recordings are saved"), size=30
        )
        self._tips.append((self.browse_button, "Choose where recordings are saved"))
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(self.browse_button)
        layout.addWidget(self._field("Folder for recordings", folder_row))
        self.open_folder_button = QtWidgets.QPushButton(_("Open the folder"))
        self.open_folder_button.setObjectName("link")
        self.open_folder_button.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self._captions.append((self.open_folder_button, "Open the folder", False))
        layout.addWidget(self.open_folder_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        #: Why the folder cannot be used, beside the field rather than in a dialog.
        self.folder_problem = self._problem()
        layout.addWidget(self.folder_problem)
        layout.addStretch(1)
        return page

    def _build_estimate(self) -> QtWidgets.QWidget:
        """The size estimate, in a box of its own: a figure and what it is worth."""
        box = QtWidgets.QFrame()
        box.setObjectName("card")
        box.setStyleSheet(
            f"QFrame#card {{ background: {theme.COLOURS['panel']};"
            f" border: 1px solid {theme.COLOURS['line']}; border-radius: 8px; }}"
        )
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.estimate_label = surfaces.label("", "text", 10.0)
        self.estimate_label.setWordWrap(True)
        layout.addWidget(self.estimate_label)
        #: Whether the figure above is a measurement from this machine or a table, in
        #: so many words. "Estimated" and "measured here" are not the same claim.
        self.estimate_note = surfaces.label("", "text_dim", 8.8)
        self.estimate_note.setWordWrap(True)
        layout.addWidget(self.estimate_note)
        return box

    def _page_sound(self) -> QtWidgets.QWidget:
        page, layout = self._page("Sound", "What is recorded besides the picture")

        self._group(layout, "Microphone")
        self.mic_switch = Switch()
        layout.addWidget(self._row("Record the microphone", self.mic_switch))
        self.mic_combo = self._combo()
        # Full width, unlike most fields: a device name is long by nature, and the
        # half-width column cut "Default playback device (Headphones - wire (Temple))"
        # off at exactly the word that says which device it is.
        layout.addWidget(self._field("Device", self.mic_combo))
        self.mic_note = self._note("")
        self.mic_note.setVisible(False)
        layout.addWidget(self.mic_note)
        layout.addSpacing(22)

        self._group(layout, "System sound")
        self.system_switch = Switch()
        layout.addWidget(self._row("Record what the computer plays", self.system_switch))
        self.system_combo = self._combo()
        layout.addWidget(self._field("Source", self.system_combo))
        layout.addSpacing(22)

        self._group(layout, "Tracks")
        self.tracks_combo = self._combo()
        self._fill(self.tracks_combo, self._tracks_items())
        self.tracks_row = self._field("In the file", self.tracks_combo)
        layout.addWidget(self._half(self.tracks_row))
        self.tracks_note = self._note("")
        layout.addWidget(self.tracks_note)
        layout.addStretch(1)
        return page

    def _page_camera(self) -> QtWidgets.QWidget:
        page, layout = self._page("Camera", "A picture on top of the recording")

        self.webcam_switch = Switch()
        self.webcam_switch.setToolTip(
            _("The camera is drawn on the screen itself, so it reaches the recording "
              "the way the click rings do - and so you can see it too while you record.")
        )
        self._tips.append((
            self.webcam_switch,
            "The camera is drawn on the screen itself, so it reaches the recording "
            "the way the click rings do - and so you can see it too while you record.",
        ))
        layout.addWidget(self._row("Show the camera", self.webcam_switch))
        layout.addSpacing(8)

        #: Everything that only means something with the camera on. Hidden rather than
        #: only greyed out: six dead controls under an off switch is six questions.
        self.webcam_body = QtWidgets.QWidget()
        self.webcam_body.setObjectName("formRow")
        body = QtWidgets.QVBoxLayout(self.webcam_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.webcam_combo = self._combo()
        body.addWidget(self._half(self._field("Device", self.webcam_combo)))
        body.addSpacing(18)

        self._group(body, "Where it sits")
        self.camera_view = CameraDiagram()
        body.addWidget(self.camera_view)
        body.addSpacing(16)

        grid = FieldGrid()
        self.webcam_corner_combo = self._combo()
        self._fill(self.webcam_corner_combo, self._corner_items())
        grid.add(self._field("Position", self.webcam_corner_combo))
        self.webcam_size_combo = self._combo()
        self._fill(self.webcam_size_combo, self._webcam_size_items())
        grid.add(self._field("Camera size", self.webcam_size_combo))
        self.webcam_shape_combo = self._combo()
        self._fill(self.webcam_shape_combo, self._webcam_shape_items())
        grid.add(self._field("Camera shape", self.webcam_shape_combo))
        self.webcam_res_combo = self._combo()
        grid.add(self._field(
            "Camera resolution", self.webcam_res_combo,
            "The size the camera itself runs at. The picture is a few hundred pixels "
            "wide, so the biggest mode is waste - and on this camera it is also the "
            "slowest.",
        ))
        body.addWidget(grid)
        layout.addWidget(self.webcam_body)

        #: Empty, or why there is no camera to choose from.
        self.webcam_note = self._note("")
        self.webcam_note.setVisible(False)
        layout.addWidget(self.webcam_note)
        layout.addStretch(1)
        return page

    def _page_control(self) -> QtWidgets.QWidget:
        page, layout = self._page("Control", "What is on the screen, and what starts it")

        self._group(layout, "While recording")
        self.cursor_switch = Switch()
        layout.addWidget(self._row("Show the cursor", self.cursor_switch))
        self.clicks_switch = Switch()
        layout.addWidget(self._row("Highlight clicks", self.clicks_switch))
        self.frame_switch = Switch()
        self.frame_switch.setToolTip(
            _("A thin red outline round what is being recorded, on every screen in "
              "the set. It stays on your screen and never reaches the file.")
        )
        self._tips.append((
            self.frame_switch,
            "A thin red outline round what is being recorded, on every screen in "
            "the set. It stays on your screen and never reaches the file.",
        ))
        layout.addWidget(self._row("Capture frame", self.frame_switch))
        self.bar_switch = Switch()
        layout.addWidget(self._row("Floating bar", self.bar_switch))
        layout.addSpacing(22)

        self._group(layout, "Timers")
        grid = FieldGrid()
        self.countdown_combo = self._combo()
        self._fill(self.countdown_combo, self._countdown_items())
        grid.add(self._field("Countdown", self.countdown_combo))
        self.autostop_combo = self._combo()
        self._fill(self.autostop_combo, self._autostop_items())
        grid.add(self._field(
            "Stop after", self.autostop_combo,
            "Stop on its own after this long - for a process you start and walk "
            "away from",
        ))
        self.after_combo = self._combo()
        self._fill(self.after_combo, self._after_items())
        grid.add(self._field("When it ends", self.after_combo))
        layout.addWidget(grid)
        layout.addSpacing(22)

        self._group(layout, "Hotkeys")
        self.hotkey_edit = HotkeyEdit()
        self.hotkey_edit.setPlaceholderText("Ctrl+Alt+R")
        layout.addWidget(self._half(
            self._field("Start / stop recording", self.hotkey_edit)
        ))
        layout.addSpacing(10)
        self.hotkey_pause_edit = HotkeyEdit()
        self.hotkey_pause_edit.setPlaceholderText("Ctrl+Alt+P")
        layout.addWidget(self._half(
            self._field("Pause / carry on", self.hotkey_pause_edit)
        ))
        layout.addSpacing(10)
        self.hotkey_draw_edit = HotkeyEdit()
        self.hotkey_draw_edit.setPlaceholderText("Ctrl+Alt+D")
        layout.addWidget(self._half(self._field(
            "Draw / work", self.hotkey_draw_edit,
            "Switches between drawing on the screen and working with the application",
        )))
        self.hotkey_hint = self._note("")
        layout.addWidget(self.hotkey_hint)
        self.hotkey_problem = self._problem()
        layout.addWidget(self.hotkey_problem)
        layout.addStretch(1)
        return page

    def _page_general(self) -> QtWidgets.QWidget:
        page, layout = self._page("General", "The program itself")
        self.language_combo = self._combo()
        self._fill(self.language_combo, self._language_items())
        layout.addWidget(self._half(self._field("Language", self.language_combo)))
        self.language_note = self._note("")
        layout.addWidget(self.language_note)
        layout.addSpacing(26)

        self._group(layout, "About")
        # Which build this is. Not a feature and not filler: the executables carry no
        # version in their file properties, so without this line the only way to answer
        # "which one have I got" is to find the installer it came from.
        from .. import __version__

        self.version_label = surfaces.label(
            f"ScreenRec {__version__}", "text_soft", 9.5
        )
        layout.addWidget(self.version_label)
        layout.addSpacing(8)

        links = QtWidgets.QWidget()
        links.setObjectName("formRow")
        links_layout = QtWidgets.QHBoxLayout(links)
        links_layout.setContentsMargins(0, 0, 0, 0)
        links_layout.setSpacing(18)
        self.page_button = self._link_button("Project page", PROJECT_PAGE)
        self.source_button = self._link_button("Source code", PROJECT_SOURCE)
        links_layout.addWidget(self.page_button)
        links_layout.addWidget(self.source_button)
        links_layout.addStretch(1)
        layout.addWidget(links)

        #: Said only when Windows refuses to open a browser, which is the one thing a
        #: link here can do wrong.
        self.link_problem = self._problem()
        layout.addWidget(self.link_problem)
        layout.addStretch(1)
        return page

    def _link_button(self, text: str, url: str) -> QtWidgets.QPushButton:
        """A word that opens an address in whatever browser Windows uses.

        A button and not a rich-text `QLabel`: a label's link colour comes from the
        palette rather than from `theme`, it is not reachable with Tab, and it answers
        no key. This one is in the tab order and answers Space like any other button.
        """
        button = QtWidgets.QPushButton(_(text))
        button.setObjectName("link")
        button.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        button.setToolTip(url)
        button.setAccessibleDescription(url)
        self._captions.append((button, text, False))
        button.clicked.connect(lambda _checked=False, u=url: self._open_url(u))
        return button

    def _open_url(self, url: str) -> None:
        """Hand an address to the browser, and say so here if that fails.

        `QDesktopServices.openUrl` answers False rather than raising, and silence after
        a click is the one outcome a link must not have.
        """
        self.link_problem.setVisible(False)
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)):
            self.link_problem.setText(
                _("That link would not open: {url}").format(url=url)
            )
            self.link_problem.setVisible(True)

    # ------------------------------------------------------------------ lists

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

    def _size_items(self) -> list[tuple[str, object]]:
        """The sizes, largest first, with the screen's own size at the top."""
        items: list[tuple[str, object]] = [(_("As on screen"), 0)]
        for height in config_mod.MAX_HEIGHTS:
            if height:
                items.append((_("{height}p").format(height=height), height))
        return items

    def _fps_items(self) -> list[tuple[str, object]]:
        """The usual rates, plus whatever the settings actually hold.

        A rate set by hand in the JSON - 25 for PAL footage, say - must survive the
        window being opened, so it is added rather than rounded to a neighbour.
        """
        stored = int(self.settings.framerate or 0)
        rates = sorted(set(FRAME_RATES) | ({stored} if stored > 0 else set()))
        return [(_("{rate} fps").format(rate=rate), rate) for rate in rates]

    def _countdown_items(self) -> list[tuple[str, object]]:
        values = sorted(set(COUNTDOWNS) | {int(self.settings.countdown or 0)})
        items: list[tuple[str, object]] = []
        for value in values:
            items.append(
                (_("No countdown") if not value
                 else _("{seconds} s").format(seconds=value), value)
            )
        return items

    def _autostop_items(self) -> list[tuple[str, object]]:
        stored = int(getattr(self.settings, "auto_stop_minutes", 0) or 0)
        values = sorted(set(AUTO_STOPS) | {stored})
        items: list[tuple[str, object]] = []
        for value in values:
            items.append(
                (_("No limit") if not value
                 else _("{minutes} min").format(minutes=value), value)
            )
        return items

    def _corner_items(self) -> list[tuple[str, object]]:
        return [
            (_("Top left"), "top_left"),
            (_("Top right"), "top_right"),
            (_("Bottom left"), "bottom_left"),
            (_("Bottom right"), "bottom_right"),
        ]

    def _webcam_size_items(self) -> list[tuple[str, object]]:
        return [
            (_("Tiny"), "tiny"),
            (_("Small"), "small"),
            (_("Medium"), "medium"),
            (_("Large"), "large"),
        ]

    def _webcam_shape_items(self) -> list[tuple[str, object]]:
        return [
            (_("Rounded rectangle"), "rounded"),
            (_("Circle"), "circle"),
            (_("Rectangle"), "rectangle"),
        ]

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
        # Without this a single long device name decides the column's width.
        combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(8)
        combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        view = combo.view()
        if view is not None:
            view.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)
        return combo

    # ------------------------------------------------------------------ wiring

    def _wire(self) -> None:
        self.close_button.clicked.connect(self._close)
        self.done_button.clicked.connect(self._close)
        for index, (button, _word) in enumerate(self._nav):
            button.clicked.connect(lambda _checked, i=index: self._show_index(i))
        self.language_combo.currentIndexChanged.connect(self._on_language)
        self.fps_combo.currentIndexChanged.connect(self._on_framerate)
        self.size_combo.currentIndexChanged.connect(self._on_size)
        self.webcam_switch.toggled.connect(self._on_webcam_enabled)
        self.webcam_combo.currentIndexChanged.connect(self._on_webcam_device)
        self.webcam_corner_combo.currentIndexChanged.connect(self._on_webcam_corner)
        self.webcam_size_combo.currentIndexChanged.connect(self._on_webcam_size)
        self.webcam_shape_combo.currentIndexChanged.connect(self._on_webcam_shape)
        self.webcam_res_combo.currentIndexChanged.connect(self._on_webcam_resolution)
        self.camera_view.cornerPicked.connect(self._on_corner_picked)
        self.profile_combo.currentIndexChanged.connect(self._on_profile)
        self.quality_combo.currentIndexChanged.connect(self._on_quality)
        self.mic_switch.toggled.connect(self._on_mic_switch)
        self.system_switch.toggled.connect(self._on_system_switch)
        self.mic_combo.currentIndexChanged.connect(self._on_mic)
        self.system_combo.currentIndexChanged.connect(self._on_system)
        self.tracks_combo.currentIndexChanged.connect(self._on_tracks)
        self.cursor_switch.toggled.connect(self._on_cursor)
        self.clicks_switch.toggled.connect(self._on_clicks)
        self.frame_switch.toggled.connect(self._on_frame)
        self.bar_switch.toggled.connect(self._on_bar)
        self.countdown_combo.currentIndexChanged.connect(self._on_countdown)
        self.browse_button.clicked.connect(self._browse)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        self.autostop_combo.currentIndexChanged.connect(self._on_autostop)
        self.after_combo.currentIndexChanged.connect(self._on_after)
        self.details_button.toggled.connect(self._on_details)
        for field, attribute in (
            (self.hotkey_edit, "hotkey"),
            (self.hotkey_pause_edit, "hotkey_pause"),
            (self.hotkey_draw_edit, "hotkey_annotate"),
        ):
            field.captured.connect(
                lambda text, a=attribute, f=field: self._on_hotkey_captured(f, a, text)
            )
            field.capturing.connect(self._on_hotkey_capturing)

    # ------------------------------------------------------------------- pages

    def show_page(self, key: str) -> None:
        """Open one page by its key in `PAGES`. Unknown keys are ignored."""
        for index, (page_key, _icon, _word) in enumerate(PAGES):
            if page_key == key:
                self._show_index(index)
                return

    def current_page(self) -> str:
        return PAGES[self.stack.currentIndex()][0]

    def _show_index(self, index: int) -> None:
        index = max(0, min(index, self.stack.count() - 1))
        self.stack.setCurrentIndex(index)
        button = self._nav[index][0]
        if not button.isChecked():
            was_blocked = button.blockSignals(True)
            try:
                button.setChecked(True)
            finally:
                button.blockSignals(was_blocked)

    # ------------------------------------------------------------- language

    def _on_language(self, _index: int) -> None:
        """Switch the whole program's language and remember the choice.

        `set_language` emits `i18n.notifier.changed`, which arrives back here as
        `_on_language_switched` and rebuilds this window's own text, so the window the
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
        """`i18n.notifier.changed` arrived - from this window or from anywhere else."""
        try:
            self.retranslate()
        except RuntimeError:
            # The window's C++ side has been deleted while the notifier still held the
            # connection (one dropped without `deleteLater` having run yet). There is
            # nothing left to retranslate, and this must not take the program down.
            pass

    def retranslate(self) -> None:
        """Say everything on the window again in the language now in force.

        Every string the user reads is rebuilt here: the captions registered by `_field`,
        `_row`, `_group` and `_page`, the sidebar, the fixed combo boxes, the two device
        lists, the notes, the tool tips and whatever complaint the hotkey boxes are
        showing. `_loading` is held throughout so that nothing of this counts as the
        user changing a setting.
        """
        was_loading = self._loading
        self._loading = True
        try:
            for widget, source, upper in self._captions:
                text = _(source)
                widget.setText(text.upper() if upper else text)
            for button, source in self._nav:
                button.setText(_(source))
            for widget, source in self._tips:
                widget.setToolTip(_(source))
                if isinstance(widget, InfoButton):
                    widget.set_text(_(source))
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
            self._fill(self.size_combo, self._size_items())
            self._fill(self.fps_combo, self._fps_items())
            self._fill(self.countdown_combo, self._countdown_items())
            self._fill(self.autostop_combo, self._autostop_items())
            self._fill(self.webcam_corner_combo, self._corner_items())
            self._fill(self.webcam_size_combo, self._webcam_size_items())
            self._fill(self.webcam_shape_combo, self._webcam_shape_items())
            self._fill_mic_combo()
            self._fill_system_combo()
            self._refresh_preset()
            self._show_hotkey_message()
        finally:
            self._loading = was_loading
        self._update_enabled()

    def _retranslate_fixed(self) -> None:
        """The strings that are not a caption and not a list item."""
        self.close_button.setToolTip(_("Close"))
        self.done_button.setText(_("Done"))
        self.tracks_note.setText(
            _("Two tracks need both the microphone and the system sound.")
        )
        self.hotkey_hint.setText(
            _("Click a box and press the keys. Esc leaves it as it was.")
        )
        self._set_details_text()
        self._fill_details()
        self.language_note.setText(
            _("Everything changes at once. The player is a separate program and "
              "takes the new language the next time it opens.")
        )
        self._write_status()

    # ------------------------------------------------------------- devices

    def refresh_devices(
        self,
        mics: Sequence[audio_mod.MicDevice],
        loopbacks: Sequence[audio_mod.LoopbackDevice],
    ) -> None:
        """Rebuild the two device lists, keeping what the settings asked for.

        Enumeration is the caller's job - it owns the ffmpeg path and does it off the
        first paint. The window only shows what it is handed.
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
        if self._devices_known and not self.mics:
            self.mic_note.setText(_("No microphone was found on this machine."))
            self.mic_note.setVisible(True)
            self.mic_combo.setToolTip(_("No microphone was found on this machine."))
        else:
            self.mic_note.setVisible(False)
            self.mic_combo.setToolTip(self.mic_combo.currentText())

    def _default_loopback_label(self) -> str:
        """"Default playback device", and which one that is right now.

        The bare phrase is not enough to act on. This machine has two endpoints whose
        names differ by one word, and a call playing to the wrong one records as
        silence with nothing on screen to say so - which is how a whole call was lost
        on 2026-11-13. Naming it turns a promise into something checkable.
        """
        plain = _("Default playback device")
        try:
            index = audio_mod.default_loopback_index()
        except Exception:  # noqa: BLE001 - somebody else's audio stack
            return plain
        for device in self.loopbacks:
            if device.index == index:
                return _("Default playback device ({name})").format(
                    name=device.label
                )
        return plain

    def _fill_system_combo(self) -> None:
        self.system_combo.clear()
        self.system_combo.addItem(_("Off"), _OFF)
        self.system_combo.addItem(self._default_loopback_label(), -1)
        for device in self.loopbacks:
            self.system_combo.addItem(device.label, device.index)
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
            self._fill(self.fps_combo, self._fps_items())
            index = self.fps_combo.findData(int(settings.framerate))
            self.fps_combo.setCurrentIndex(max(0, index))
            index = self.size_combo.findData(settings.max_height)
            self.size_combo.setCurrentIndex(max(0, index))
            self.webcam_switch.set_checked_now(bool(settings.webcam_enabled))
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
            self.mic_switch.set_checked_now(bool(settings.mic_enabled))
            self.system_switch.set_checked_now(bool(settings.system_audio_enabled))
            index = self.tracks_combo.findData(settings.audio_mode)
            self.tracks_combo.setCurrentIndex(max(0, index))
            self.cursor_switch.set_checked_now(settings.draw_mouse)
            self.clicks_switch.set_checked_now(settings.click_highlight)
            self.frame_switch.set_checked_now(
                bool(getattr(settings, CAPTURE_FRAME_ATTR, CAPTURE_FRAME_DEFAULT))
            )
            self.bar_switch.set_checked_now(settings.show_island)
            self._fill(self.countdown_combo, self._countdown_items())
            index = self.countdown_combo.findData(int(settings.countdown))
            self.countdown_combo.setCurrentIndex(max(0, index))
            self.folder_edit.setText(str(settings.resolved_output_dir()))
            self.folder_edit.setToolTip(self.folder_edit.text())
            self.folder_edit.setCursorPosition(0)
            self.hotkey_edit.setText(settings.hotkey)
            self.hotkey_pause_edit.setText(getattr(settings, "hotkey_pause", ""))
            self.hotkey_draw_edit.setText(getattr(settings, "hotkey_annotate", ""))
            self._fill(self.autostop_combo, self._autostop_items())
            index = self.autostop_combo.findData(
                int(getattr(settings, "auto_stop_minutes", 0) or 0)
            )
            self.autostop_combo.setCurrentIndex(max(0, index))
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
        """Tell the window what is actually being captured, for the size estimate.

        The window does not know the source - that is the bar's business - so until it
        is told it estimates for `DEFAULT_CAPTURE` and the line says it is an estimate.
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

    def set_recording(self, recording: bool) -> None:
        """Tell the window whether a take is running.

        Everything about the picture and the sound is read when a recording starts, so
        while one is running those controls describe the *next* take. Rather than let
        somebody change the frame rate mid-take and wonder why nothing happened, they
        are disabled and the footer says why. The language, the shortcuts, the floating
        bar and the folder stay live, because those really do apply at once.
        """
        recording = bool(recording)
        if recording == self._recording:
            return
        self._recording = recording
        self._update_enabled()

    def recording(self) -> bool:
        return self._recording

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

    def _on_autostop(self, _index: int) -> None:
        if self._loading:
            return
        value = self.autostop_combo.currentData()
        self.settings.auto_stop_minutes = int(value or 0)
        self._write()

    def _on_after(self, _index: int) -> None:
        if self._loading:
            return
        self.settings.after_recording = self.after_combo.currentData() or ""
        self._write()

    # ------------------------------------------------------------- shortcuts

    def _on_hotkey_capturing(self, armed: bool) -> None:
        """A box has started or stopped waiting. Pass it on and say so."""
        self.hotkey_capture.emit(bool(armed))
        if armed:
            self._say_hotkey("")

    def _on_hotkey_captured(
        self, field: HotkeyEdit, attribute: str, text: str
    ) -> None:
        """Store a shortcut the user just pressed, unless another one has it.

        A clash inside this program is the only one that can be checked: whether some
        other application has already asked Windows for the combination is not knowable
        from here, and `hotkeys.HotkeyListener.start` reports that when it fails.
        """
        if self._loading:
            return
        parsed = hotkeys_mod.parse_hotkey(text)
        if parsed is None:
            field.setText(getattr(self.settings, attribute, "") or "")
            return
        canonical = hotkeys_mod.format_hotkey(*parsed)
        clash = self._clashing_name(attribute, canonical)
        if clash:
            self._say_hotkey(
                "{shortcut} is already {name}. The shortcut is unchanged.",
                shortcut=canonical,
                name=clash,
            )
            field.setText(getattr(self.settings, attribute, "") or "")
            return
        self._say_hotkey("")
        field.setText(canonical)
        if canonical == getattr(self.settings, attribute, ""):
            return  # nothing changed; do not wake the caller up to re-register it
        setattr(self.settings, attribute, canonical)
        self._write()

    def _clashing_name(self, attribute: str, canonical: str) -> str:
        """What else this program already uses `canonical` for, in words, or ""."""
        others = {
            "hotkey": "start and stop",
            "hotkey_pause": "pause and carry on",
            "hotkey_annotate": "draw and work",
        }
        for other, name in others.items():
            if other == attribute:
                continue
            if (getattr(self.settings, other, "") or "") == canonical:
                return _(name)
        return ""

    def _say_hotkey(self, template: str, **values: str) -> None:
        """Show a complaint about a shortcut, or clear it with an empty template.

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
            return
        template, values = self._hotkey_message
        self.hotkey_problem.setText(_(template).format(**values))
        self.hotkey_problem.setVisible(True)

    # ----------------------------------------------------------------- video

    def _on_size(self, _index: int) -> None:
        if self._loading:
            return
        value = self.size_combo.currentData()
        self.settings.max_height = int(value) if value else 0
        self._write_size_note()
        self._update_estimate()
        self._write()

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

    def _on_framerate(self, _index: int) -> None:
        if self._loading:
            return
        value = self.fps_combo.currentData()
        if not value:
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

    # ----------------------------------------------------------------- sound

    def _on_mic_switch(self, on: bool) -> None:
        """The switch picks a device or turns the list off; the list is still the model.

        A switch turned on with no device chosen takes the first real one, because
        "on" with nothing selected would record silence.
        """
        if self._loading:
            return
        if not on:
            self.mic_combo.setCurrentIndex(0)  # the "Off" row; `_on_mic` does the rest
            return
        if self.mic_combo.currentIndex() > 0:
            return
        if self.mic_combo.count() > 1:
            self.mic_combo.setCurrentIndex(1)
            return
        # Nothing to turn on. Put the switch back rather than claim a microphone.
        self.mic_switch.set_checked_now(False)
        self._update_enabled()

    def _on_system_switch(self, on: bool) -> None:
        if self._loading:
            return
        if not on:
            self.system_combo.setCurrentIndex(0)
            return
        if self.system_combo.currentIndex() > 0:
            return
        self.system_combo.setCurrentIndex(1)  # the default playback device

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
        self.mic_switch.set_checked_now(bool(self.settings.mic_enabled))
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
        self.system_switch.set_checked_now(bool(self.settings.system_audio_enabled))
        self._update_enabled()
        self._write()

    def _on_tracks(self, _index: int) -> None:
        if self._loading:
            return
        mode = self.tracks_combo.currentData()
        self.settings.audio_mode = mode or config_mod.AUDIO_MIX
        self._write()

    # ---------------------------------------------------------------- camera

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
        # "Choose for me" rather than "First one found": it says what the program will
        # do, and every other entry is a real device name beside it.
        items: list[tuple[str, object]] = [(_("Choose for me"), "")]
        for device in found:
            items.append((device.description(), device.description()))
        self._fill(self.webcam_combo, items)

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

    def _on_webcam_enabled(self, on: bool) -> None:
        if self._loading:
            return
        self.settings.webcam_enabled = bool(on)
        self._update_enabled()
        self.changed.emit()

    def _on_webcam_device(self, _index: int) -> None:
        if self._loading:
            return
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
        if self._loading:
            return
        value = self.webcam_shape_combo.currentData()
        if value:
            self.settings.webcam_shape = str(value)
            self._refresh_camera_view()
            self.changed.emit()

    def _on_webcam_resolution(self, _index: int) -> None:
        if self._loading:
            return
        value = self.webcam_res_combo.currentData()
        self.settings.webcam_resolution = str(value or "")
        self.changed.emit()

    def _on_webcam_corner(self, _index: int) -> None:
        if self._loading:
            return
        value = self.webcam_corner_combo.currentData()
        if value:
            self.settings.webcam_corner = str(value)
            self._refresh_camera_view()
            self.changed.emit()

    def _on_webcam_size(self, _index: int) -> None:
        if self._loading:
            return
        value = self.webcam_size_combo.currentData()
        if value:
            self.settings.webcam_size = str(value)
            self._refresh_camera_view()
            self.changed.emit()

    def _on_corner_picked(self, corner: str) -> None:
        """The diagram was clicked. The list is still where the value comes from."""
        index = self.webcam_corner_combo.findData(corner)
        if index >= 0:
            self.webcam_corner_combo.setCurrentIndex(index)

    def _refresh_camera_view(self) -> None:
        self.camera_view.set_placement(
            self.settings.webcam_corner,
            self.settings.webcam_size,
            self.settings.webcam_shape,
        )

    # --------------------------------------------------------------- control

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
        setattr(self.settings, CAPTURE_FRAME_ATTR, bool(checked))
        self._write()

    def _on_bar(self, checked: bool) -> None:
        if self._loading:
            return
        self.settings.show_island = bool(checked)
        self._write()

    def _on_countdown(self, _index: int) -> None:
        if self._loading:
            return
        value = self.countdown_combo.currentData()
        self.settings.countdown = int(value or 0)
        self._write()

    # ---------------------------------------------------------------- folder

    def _browse(self) -> None:
        start = self.folder_edit.text() or str(self.settings.resolved_output_dir())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, _("Where should recordings go?"), start
        )
        if not chosen:
            return  # the user cancelled; nothing to report and nothing to change
        problem = self._folder_problem(chosen)
        if problem:
            # Said here and the setting left alone. A folder that cannot be written to
            # is not a setting, it is a recording that will fail at the worst moment -
            # `recorder` finds out when ffmpeg has already started.
            self.folder_problem.setText(problem)
            self.folder_problem.setVisible(True)
            return
        self.settings.output_dir = chosen
        self.folder_edit.setText(chosen)
        self.folder_edit.setToolTip(chosen)
        self.folder_edit.setCursorPosition(0)
        self.folder_problem.setVisible(False)
        self._write()

    @staticmethod
    def _folder_problem(folder: str) -> str:
        """Why recordings cannot go there, or "" when they can.

        It writes a file and deletes it again, because that is the only question that
        matters and the only one Windows answers truthfully: `os.access(W_OK)` reports
        the read-only attribute and not the permissions, so it says yes to a folder
        the recording will be refused by.
        """
        probe = Path(folder) / ".screenrec-write-test"
        try:
            probe.write_bytes(b"")
        except OSError as exc:
            return _("Recordings cannot be written there: {reason}").format(
                reason=getattr(exc, "strerror", None) or exc
            )
        try:
            probe.unlink()
        except OSError:
            # Written but not removed: the recording will work, and a stray empty file
            # is not worth refusing the folder over.
            pass
        return ""

    def _open_folder(self) -> None:
        """Show the folder in Explorer, and say so here if Windows refuses.

        `shellops.open_folder` answers with a sentence rather than raising, and that
        sentence belongs beside the field - a message box for "the folder is gone" is
        more ceremony than the problem deserves.
        """
        problem = shellops.open_folder(self.settings.resolved_output_dir())
        self.folder_problem.setText(problem)
        self.folder_problem.setVisible(bool(problem))

    def _close(self) -> None:
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        """Escape closes the window - unless a shortcut box is using it to cancel."""
        if event.key() == QtCore.Qt.Key.Key_Escape:
            for field in (self.hotkey_edit, self.hotkey_pause_edit,
                          self.hotkey_draw_edit):
                if field.is_armed():
                    super().keyPressEvent(event)
                    return
            self._close()
            event.accept()
            return
        super().keyPressEvent(event)

    # ----------------------------------------------------------- derived UI

    def _update_enabled(self) -> None:
        """Everything that is on, off, shown or hidden because of something else."""
        both = self.settings.mic_enabled and self.settings.system_audio_enabled
        self.tracks_combo.setEnabled(both and not self._recording)
        self.tracks_note.setText(
            _("Two tracks need both the microphone and the system sound.")
            if not both else ""
        )
        self.tracks_note.setVisible(not both)
        self.mic_combo.setEnabled(self.settings.mic_enabled and not self._recording)
        self.system_combo.setEnabled(
            self.settings.system_audio_enabled and not self._recording
        )

        # Which camera, which corner and how big mean nothing with the camera off.
        on = bool(self.settings.webcam_enabled)
        self.webcam_body.setVisible(on)
        for control in (self.webcam_combo, self.webcam_corner_combo,
                        self.webcam_size_combo, self.webcam_shape_combo,
                        self.webcam_res_combo):
            control.setEnabled(on and not self._recording)
        self.camera_view.setEnabled(on and not self._recording)
        self._refresh_camera_view()

        # What a running take has already read and will not read again.
        for control in (self.fps_combo, self.size_combo, self.profile_combo,
                        self.quality_combo, self.preset_combo, self.webcam_switch,
                        self.mic_switch, self.system_switch, self.cursor_switch,
                        self.countdown_combo):
            control.setEnabled(not self._recording)
        self._write_status()
        self._update_estimate()

    def _write_status(self) -> None:
        """The footer's sentence. It is not always the same one."""
        if self._recording:
            self.status_label.setText(
                _("A recording is running. The picture and sound settings will be "
                  "used by the next one.")
            )
        else:
            self.status_label.setText(_("Changes are saved as you make them."))

    def _update_estimate(self) -> None:
        """Say what a minute costs, and admit which kind of figure it is.

        The per-minute figures in `config.PROFILES` were measured on a *busy* screen -
        scrolling coloured text - and a real desktop recording came in far under them, so
        the wording has to carry that rather than quote one number as a promise.

        And the table is this machine's NVENC. `storage.size_estimate` is asked instead
        of `config.estimate_mb_per_minute`, because on a machine recording through Media
        Foundation `docs/ENCODERS.md` §3 puts the real cost at up to 4.9x the table -
        somebody else's number, shown as if it were theirs. `size_estimate` answers with
        this machine's own recordings once there are any and falls back to the same
        table until then, so the figure never gets worse; when it is a real measurement
        the second line says so, because "estimated" and "measured here" are not the
        same claim and the user is entitled to know which they are reading.

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
        framerate = int(self.fps_combo.currentData() or self.settings.framerate or 60)
        estimate = storage_mod.size_estimate(
            profile_key, level, width, height, framerate, encoder=encoder,
        )
        rate = estimate.mb_per_minute
        gigabytes = rate * 60 / 1000
        self.estimate_label.setText(
            _("≈ {rate} MB a minute at {width} × {height}").format(
                rate=f"{rate:.0f}", width=width, height=height
            )
        )
        if estimate.measured:
            self.estimate_note.setText(
                _("{gigabytes} GB an hour. Measured on this machine, from what "
                  "{encoder} has already written.").format(
                    gigabytes=f"{gigabytes:.1f}", encoder=estimate.encoder
                )
            )
        else:
            self.estimate_note.setText(
                _("{gigabytes} GB an hour on a busy screen - much less on a still "
                  "one.").format(gigabytes=f"{gigabytes:.1f}")
            )
        self.profile_note.setText(_(profile.note) if profile else "")
        self._refresh_encoding()

    # -------------------------------------------------------- which encoder

    def set_encoders(
        self, results: Sequence[capabilities_mod.EncoderResult]
    ) -> None:
        """Hand the window what the encoder probe found on this machine.

        The application calls this with `Application.encoders`, next to
        `set_capture_size`. Before it is called the window shows nothing about encoding
        and offers every format: see the module docstring for why silence beats a guess.

        Handing it an empty list *is* an answer - "the probe ran and nothing works" -
        and is not the same as never calling it.
        """
        self._encoders = list(results)
        self._encoders_known = True
        self._fill_details()
        self._update_enabled()

    def encoders(self) -> list[capabilities_mod.EncoderResult]:
        """What the window was told, so a caller need not keep a second copy."""
        return list(self._encoders)

    def _chosen_encoder(self, profile: config_mod.Profile) -> str:
        """The ffmpeg encoder this machine would really use for `profile`.

        `capabilities.choose` is the one answer, and the window asks it rather than
        working anything out of its own: a second opinion here is a second chooser to
        keep in step with the recorder.

        `Settings.encoder_choice` stays the JSON-only escape hatch it is documented as -
        there is no control for it here, deliberately - but if somebody has written a
        real encoder name into the file then that is what will be recorded with, and
        this line has to say so instead of describing a choice nobody made. Anything
        else in that field, "auto" and "" included, means "let the probe decide".
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
            # A view this window did not build. Nothing to disable, and guessing at
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
        arrow = "▾" if self.details_button.isChecked() else "▸"
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

    # -------------------------------------------------------------- dragging

    def _in_header(self, point: QtCore.QPoint) -> bool:
        """Whether `point` (in window coordinates) is on the title row."""
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
            # on the title, which means nothing and must not pin the window in place.
            self._dropped_at = (self.x(), self.y())
        event.accept()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        # Hidden mid-drag the window never sees the release, and a half-finished drag
        # would move it on the first mouse move after it came back.
        self._drag.cancel()
        # A box left armed would hold the global hotkeys down for ever.
        for field in (self.hotkey_edit, self.hotkey_pause_edit, self.hotkey_draw_edit):
            field.disarm()
        super().hideEvent(event)

    def dropped_at(self) -> tuple[int, int] | None:
        """Where the user dragged the window to this session, or None."""
        return self._dropped_at

    # -------------------------------------------------------------- placing

    def popup_near(self, widget: QtWidgets.QWidget) -> None:
        """Show beside `widget`, wholly on a screen, and never taller than one.

        Unlike every other surface in this interface, this one activates itself: the
        sidebar and the shortcut boxes are useless without the keyboard. See the module
        docstring for why that is an exception and not a slip.

        Once it has been dragged somewhere by hand, that is where it reopens - moving it
        back under the gear would throw away what the user just did. It is still
        clamped, because the screen it was dropped on may have been unplugged since.
        """
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
        # A window bigger than the screen cannot be read at the edges and cannot be
        # moved far enough to fix it. The page scrolls instead, and `FieldGrid` drops
        # to one column if the width goes with it.
        self.resize(
            min(WIDTH, max(520, area.width() - 32)),
            min(HEIGHT, max(360, area.height() - 48)),
        )

        if self._dropped_at is not None:
            self._show_at(*self._clamped(*self._dropped_at))
            return

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

        `ui.bar`'s clamp against `winapi.list_monitors()`, so a window dropped on a
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
