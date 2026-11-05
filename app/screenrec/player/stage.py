"""Both recorded screens in one window, under one transport: §3 and §4 of the contract.

`window.PlayerWindows` is the *window* half of `docs/PLAYER_LAYOUTS.md` - one borderless
window per screen plus a floating bar, which is `separate` and `one_per_monitor`. This
module is the other half: the three **stage** layouts, `side_by_side`, `stacked` and
`focus`, in one host window with one timeline. It exists because the question the whole
feature answers is "a viewer with one monitor has two pictures and one screen to put
them on", and two free-floating windows is only one of the answers to it.

Five decisions worth stating, because each had an alternative and each avoids a failure
with a name.

**The transport is docked, not floating.** A floating bar is a second window: one more
thing to place on a desk, to clamp into a real screen and to keep above the pictures.
Docked under the stage *area* it is part of the one window the viewer already has, and
"the controls are under the pictures, always" stops being a rule anybody has to re-apply
on a resize. `window.TransportBar` is the floating one and it stays where it belongs -
the separate-windows half, which has no single window to dock anything to.

**Nothing here ever touches a player.** Play, pause, seek, speed and frame step go to the
engine through `window._Engine`, which is the resolver the window half already uses, so
there is one place that knows what the engine is called. Changing the layout, swapping
the two streams and moving the splitter are *widget* operations and a
`VideoPane.set_stream`, which is a disconnect and a connect on a sink. §6 requires the
position to be unchanged across every one of those, and the only way to be sure of that
is for the code path not to contain an engine call at all - which is what
`tests/test_player_stage.py` asserts by counting the fake engine's seeks.

**Two pane widgets, both connected all the time.** `focus` shows one and hides the
other; the question is whether the hidden one should be *disconnected* from its sink.
It should not. `VideoPane.set_stream(index, title, None)` is the only way to disconnect
one from outside, and that call is how a host says "this pane has nothing to show": it
writes `setup_problem`, says so on stderr and leaves the pane announcing that it will
stay black. A layout switch is not a fault, and it would raise that complaint twice on
every switch. So the hidden pane keeps its connection, and the cost is one handle copy
per frame into a hidden `QVideoWidget` - a `QVideoFrame` is implicitly shared, so it is a
handle and not a picture, the same cost the visible pane already pays. That is reasoned,
not measured: no two-screen recording was played while this was written.

**Which stream is where is one list, and the labels follow it.** `_order[0]` is the index
of the stream in the main pane; a swap reverses the list and re-points the sinks. §3:
"the name follows the *stream*, not the position. After a swap, Экран 1 is still Экран 1,
now on the right." `VideoPane.set_stream` takes the title with the index for exactly that
reason, so the two cannot come apart.

**The window is clamped into one real screen before it is shown, and again if that
screen is unplugged.** `window.transport_spot` carries the account of why: a window
placed from the bounding box of several screens lands in the empty corner between two
monitors, 760x52 at 1620,2480, on no monitor at all, with every control present and
none of them reachable. `place_on_screen` picks the screen that already shows most of
the window and clamps into that one's `availableGeometry`, with `layouts.clamp_into`
doing the arithmetic rather than a second copy of it here. It runs from `setVisible`
and not from `show`, because `QWidget::show` is not virtual and a host calling
`setVisible(True)` walked straight past the override; and `rescue_window` runs it again
on `screenRemoved`, which is the half §6 asks for that this module simply did not have
- `PlayerWindows` moved its windows off a monitor that had gone while the stage, which
carries the only transport those two streams have, stayed where no screen was left.

**The one place a clock is rebuilt, and why it is here.** `docs/PLAYER_SOURCES.md` §1.1:
a `QMediaPlayer` cannot grow a second file, so «Добавить второй экран» has to build a
new `SyncEngine` over both files. Everything else in this player is forbidden from
doing that, and the rule is not weakened by this: a layout change, a swap, an offset
and the audio control all still go nowhere near it. What makes a rebuild honest here is
that the viewer asked for a different *set of files*, which is the only thing a clock
cannot be talked out of, and that the position, the paused state, the speed, the volume
and the offsets are all carried across it - `_adopt_clock` is one function so that none
of them can be forgotten in one branch and remembered in another. Opening a different
recording from the menu, the library or a drop is the same operation with the position
reset, for the same reason.

**The sound and the picture are two questions.** §1.2 is emphatic that swapping which
screen is on the left must not move the sound and that the audio control must not move
the picture, so `swap` does not mention audio and `set_audio_source` does not mention
`_order`. The engine owns "exactly one audio output at any moment" - it gives one to
the stream it is told to and none to the others - and this module only asks it to move
that output. An engine that cannot (`_Engine.can_choose_audio` is False) is not lied
about: the chip is not there at all, which is `PlayerWindows`' answer to the same
question and better than a control that does nothing.

**An offset is not a seek.** §2.2's panel calls `engine.set_offset` and nothing else,
because the viewer is sliding one video against the other and not scrubbing. The
timeline position is read before and after in `tests/test_player_stage.py`, and the
strong form is asserted as well: no `seek` reaches the engine at all.

**Both of `docs/PLAYER_SOURCES.md`'s controls are `window`'s, not copies of them.**
`SyncPanel`, the file-drop helpers, the offset formatter and every word on both chips
come from the module the floating transport is in. This window is the *docked*
transport of the same player, and a second «−100 мс» button with its own limit and its
own Russian is how the two of them come to disagree about what the viewer just pressed.
What is written here is the wiring: which clock, which streams, and what the chrome
does when the clock is replaced under it.

**What is not verified here.** No media was decoded and no real `QMediaPlayer` ran:
every check is against a fake engine, or - for §1.1's rebuild - against a real
`SyncEngine` over stub players, under the offscreen platform plugin. So the frame
bridge through both panes, the dimming veil over a live video surface, a real file
dialog, a real drag from Explorer and the behaviour of a `QSplitter` dragged with a
real pointer over a real picture are written and exercised, and none of them is
*claimed* until somebody has watched them.
"""
from __future__ import annotations

import math
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .. import config, i18n, multi
from ..i18n import _, pick
from ..ui import surfaces, theme
from . import engine as clock_module
from . import layouts, library
from . import open as sources
from .panes import VideoPane
from .window import (
    ARRANGE_MARGIN,
    JUMP_MS,
    LIBRARY_ROWS,
    NUDGE_MS,
    POLL_MS,
    Notice,
    PlayerButton,
    Scrubber,
    SpeedChip,
    SyncPanel,
    _Engine,
    accept_file_drag,
    dropped_files,
    format_clock,
    format_offset,
    recording_files,
    recording_framerate,
    rescue_into,
    screen_areas,
    screen_titles,
)
from .window import _report as _window_report

#: The three layouts this window can be in, in the order the menu draws them. The same
#: three as `layouts.STAGE_LAYOUTS`, which is a frozenset and therefore has no order; a
#: menu does, and a menu whose items move between runs is a menu nobody learns.
STAGE_ORDER: tuple[str, ...] = (
    layouts.LAYOUT_SIDE_BY_SIDE,
    layouts.LAYOUT_STACKED,
    layouts.LAYOUT_FOCUS,
)

#: The splitter fraction may not leave this range, which is §2.3's own
#: `player_split: float # the splitter's fraction, 0.15..0.85`. `layouts.MIN_PANE` is
#: the pixel rule and the splitter enforces it by itself; this is the *stored* rule, so
#: that a settings file holding 0.0 cannot open a stage with one pane in it.
SPLIT_MIN = 0.15
SPLIT_MAX = 0.85

#: The space round the controls, and between the two rows of chrome.
CHROME_MARGIN = 10
CHROME_GAP = 6

#: Header, transport and footer together. A floor, not a measurement: the rows size
#: themselves from their controls, and Qt takes the larger of this and what they ask
#: for, so being a little low here costs nothing and being high would clip a stage.
CHROME_HEIGHT = 140

#: A stage smaller than this cannot hold two panes side by side at `layouts.MIN_PANE`
#: plus the chrome, so it is the window's own floor. Qt raises it further by itself
#: when the splitter's children ask for more - `stacked` needs two pane heights.
MIN_STAGE_WIDTH = 2 * layouts.MIN_PANE + 2 * CHROME_MARGIN
MIN_STAGE_HEIGHT = layouts.MIN_PANE + CHROME_HEIGHT

#: How much of a screen a stage opens on when nobody has given it a size.
OPENING_SHARE = 0.78

#: How long §1.1's carried position waits for the rebuilt clock to read its files
#: before it is given up. Generous, because the wait costs nothing and the files are
#: the viewer's own - a long one on a slow disk is the case this is patient for - and
#: finite, because a file that never opens never reports a length, and a carry held
#: for ever is a seek that can still land on a viewer minutes later. `PlayerStage.
#: _carry_waits` spends it on the poll timer.
CARRY_PATIENCE_MS = 10_000

#: The glyphs this module draws that `window.PLAYER_GLYPHS` and `ui.icons.KINDS` do not
#: have. Both of those belong to finished modules this feature may not edit.
STAGE_GLYPHS: tuple[str, ...] = ("fullscreen", "fullscreen_exit")

#: §1's and §2.2's numbers, and §2.2's panel itself, are `window`'s. They are not
#: copied here for the reason every control on the transport is imported rather than
#: drawn again: the floating bar and this docked one are two views of one feature, and
#: a second «−100 мс» button with its own limit is how the two of them come to disagree
#: about what the viewer just pressed.

_GRID = 24.0


def _report(message: str) -> None:
    """Say something on a failure path, on a stream that is actually there.

    Routed through `window._report` for the reason `panes._report` is: that function
    already knows the player ships as `pythonw.exe -m screenrec.player`, where
    `sys.stdout` is None and CPython's `print` silently returns. One place knowing that
    is the point; three places knowing it is how one of them stops knowing it.

    This is the developer's half. Anything a *viewer* has to know goes on the notice
    strip through `PlayerStage.say`.
    """
    _window_report(f"player.stage: {message}")


# ------------------------------------------------------- the two injectable seams


def build_clock(recording: Any, parent: QtCore.QObject | None) -> Any:
    """Build the clock for §1.1's rebuild. The only one this module ever makes.

    A module-level function rather than one line inside the method, for the reason
    `open.use_window_factory` is one: it is the seam a test replaces to run a **real**
    `SyncEngine` - the real offset arithmetic, the real position - over stub players
    instead of over media this machine would have to decode. Nothing in the shipped
    program replaces it, and nothing here builds a player: `SyncEngine` owns those,
    which is what `tests/test_player_stage.py` reads off this module's parse tree.
    """
    return clock_module.SyncEngine(recording, parent)


def ask_for_files(
    parent: QtWidgets.QWidget | None,
    title: str,
    *,
    multiple: bool = False,
    folder: str = "",
    videos_only: bool = False,
) -> list[str]:
    """A file dialog over everything the player opens. Empty when it was cancelled.

    Module-level for `build_clock`'s reason and one more: a modal dialog cannot be
    opened under the offscreen platform plugin without stopping the test that opened
    it, so every check of what the menu *does* with a chosen file replaces this.

    The filter is `PlayerWindows._file_filter`'s, through the same two catalogue keys,
    so both halves of §1 offer the same words over the same suffixes: an index **and**
    a video when a recording is being opened, videos only when a second camera is
    being added to one - there is nothing to add a `.screenrec.json` to.
    """
    patterns = " ".join(f"*{suffix}" for suffix in sources.VIDEO_SUFFIXES)
    if videos_only:
        chooses = pick("windows.filter.video", "Video ({patterns})").format(
            patterns=patterns
        )
    else:
        chooses = pick(
            "windows.filter.any", "Recordings (*{index} {patterns})"
        ).format(index=multi.SIDECAR_SUFFIX, patterns=patterns)
    if multiple:
        chosen, _picked = QtWidgets.QFileDialog.getOpenFileNames(
            parent, title, folder, chooses
        )
        return [str(name) for name in chosen if name]
    chosen_one, _picked = QtWidgets.QFileDialog.getOpenFileName(
        parent, title, folder, chooses
    )
    return [str(chosen_one)] if chosen_one else []


# ---------------------------------------------------------------------- glyphs


def draw_stage_glyph(
    painter: QtGui.QPainter,
    kind: str,
    rect: QtCore.QRectF | QtCore.QRect,
    colour: QtGui.QColor | str,
    weight: float = 1.9,
) -> bool:
    """Stroke one of `STAGE_GLYPHS` inside `rect`. False for a name it does not know.

    Never raises for an unknown name, for the reason `window.draw_glyph` does not: an
    exception escaping a live `QPainter` takes the interpreter down with it.

    The same 24x24 grid, round caps and joins as the player's own six glyphs, so the
    full-screen button sits beside the play button without looking borrowed.
    """
    box = QtCore.QRectF(rect)
    if box.isEmpty() or kind not in STAGE_GLYPHS:
        return False
    ink = QtGui.QColor(colour)
    scale = min(box.width(), box.height()) / _GRID

    painter.save()
    try:
        painter.translate(
            box.x() + (box.width() - _GRID * scale) / 2,
            box.y() + (box.height() - _GRID * scale) / 2,
        )
        painter.scale(scale, scale)
        pen = QtGui.QPen(ink, weight)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        if kind == "fullscreen":
            corners = (
                ((4.0, 9.0), (4.0, 4.0), (9.0, 4.0)),
                ((15.0, 4.0), (20.0, 4.0), (20.0, 9.0)),
                ((20.0, 15.0), (20.0, 20.0), (15.0, 20.0)),
                ((9.0, 20.0), (4.0, 20.0), (4.0, 15.0)),
            )
        else:
            corners = (
                ((9.0, 4.0), (9.0, 9.0), (4.0, 9.0)),
                ((15.0, 4.0), (15.0, 9.0), (20.0, 9.0)),
                ((20.0, 15.0), (15.0, 15.0), (15.0, 20.0)),
                ((4.0, 15.0), (9.0, 15.0), (9.0, 20.0)),
            )
        for bracket in corners:
            painter.drawPolyline(
                QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in bracket])
            )
        return True
    finally:
        painter.restore()


class StageButton(PlayerButton):
    """A `PlayerButton` that can also draw `STAGE_GLYPHS`.

    Written out rather than delegated, for the reason `PlayerButton` itself gives for
    doing the same to `surfaces.IconButton`: the base class resolves the glyph at the
    end of its own `_paint` and there is no seam there to replace. The resting, hover
    and pressed colours below are therefore the same expressions as in
    `PlayerButton._paint` **deliberately** - if those change, this has to change with
    them, which is why both are written in terms of `theme.COLOURS` and neither
    hard-codes a value.
    """

    def _paint(self, painter: QtGui.QPainter) -> None:
        if self.kind not in STAGE_GLYPHS:
            super()._paint(painter)
            return
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        box = QtCore.QRectF(0, 0, self.width(), self.height())
        hovered = self.underMouse() and self.isEnabled()

        background = None
        if self.isDown():
            background = colours["line"]
        elif self.is_on() or hovered:
            background = colours["hover"]
        if background is not None:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(background))
            painter.drawRoundedRect(box, theme.RADIUS["small"], theme.RADIUS["small"])

        if not self.isEnabled():
            ink = colours["disabled"]
        elif self.is_on():
            ink = colours["accent"]
        elif hovered:
            ink = colours["text"]
        else:
            ink = colours["text_dim"]

        inset = (self.width() - surfaces.ICON) / 2
        draw_stage_glyph(
            painter,
            self.kind,
            QtCore.QRectF(inset, inset, surfaces.ICON, surfaces.ICON),
            ink,
        )


# ------------------------------------------------------------- the 1 | 2 switch


class StreamSwitch(surfaces.Segmented):
    """`focus`'s two-button switch, reachable from the keyboard as §3 requires.

    `surfaces.Segmented` paints exactly this control and answers the mouse; what it has
    no notion of is focus, because every other segmented control in this application
    sits in a panel where Tab reaches the field beside it. §3 names the keyboard twice
    for this one - "`Tab` moves to it, `Space` still plays" - so it takes focus and
    answers the arrows and the digits.

    **`Space` is deliberately not handled.** A focused `QAbstractButton` answers Space
    by pressing itself, which is exactly the trap §3 is warning about: the viewer tabs
    to the switch, presses Space to pause, and changes which screen they are looking at
    instead. This is a plain `QWidget`, so Space is left to propagate to the stage.
    """

    def __init__(
        self,
        options: list[tuple[str, str]],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(options, parent)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def select(self, key: str) -> None:
        """Move to `key` and say so, as a click on it would.

        `set_current` is the silent setter - the stage uses it to follow a swap that
        came from somewhere else. This one is for the keyboard, where the press *is*
        the request.
        """
        if key == self.current:
            return
        self.set_current(key)
        # `set_current` refuses a key that is not one of the options, so this is the
        # test for "it took", not a tautology: a bad key must not announce a change.
        if key == self.current:
            self.changed.emit(key)

    def _step(self, by: int) -> None:
        keys = [key for key, _label in self.options]
        if not keys:
            return
        here = keys.index(self.current) if self.current in keys else 0
        self.select(keys[(here + by) % len(keys)])

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if key in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Up):
            self._step(-1)
            event.accept()
            return
        if key in (QtCore.Qt.Key.Key_Right, QtCore.Qt.Key.Key_Down):
            self._step(1)
            event.accept()
            return
        digits = {QtCore.Qt.Key.Key_1: 0, QtCore.Qt.Key.Key_2: 1}
        if key in digits and digits[key] < len(self.options):
            self.select(self.options[digits[key]][0])
            event.accept()
            return
        # Everything else - Space above all - belongs to the stage.
        event.ignore()
        super().keyPressEvent(event)


# ----------------------------------------------------------------- the splitter


class _Handle(QtWidgets.QSplitterHandle):
    """The bar between two panes, which answers a double-click.

    §3 asks for "a double-click to return to equal halves". `QSplitterHandle` has no
    signal for one, and an event filter installed on `splitter.handle(1)` would have to
    be re-installed every time the orientation changed - a rule in two places, one of
    which is a line somebody will delete. A handle that says so itself cannot drift.
    """

    double_clicked = QtCore.Signal()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _Splitter(QtWidgets.QSplitter):
    """A splitter whose handles report a double-click, as one signal for the stage.

    It also reports its **own** resize, which the stage needs and the host window's
    `resizeEvent` cannot give it: a child's geometry is settled after the parent's
    event has been handled, so a fraction applied from the window's resize is applied
    to the splitter's previous width. At the stage's first appearance that previous
    width is 100 px, both panes are below `layouts.MIN_PANE`, and `setSizes` clamps
    them to equal minimums - which is how a remembered 0.7 opened as an even split and
    then overwrote itself the first time the viewer touched the handle.
    """

    equalise_requested = QtCore.Signal()
    resized = QtCore.Signal()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.resized.emit()

    def createHandle(self) -> QtWidgets.QSplitterHandle:
        handle = _Handle(self.orientation(), self)
        handle.double_clicked.connect(self.equalise_requested)
        return handle


# --------------------------------------------------------------- the stage area


class _StageArea(QtWidgets.QWidget):
    """Where the pictures live: the splitter, and nothing but the splitter.

    A widget of its own so that the pictures are one thing the chrome is laid out
    around. The transport, the notice strip and the two rows of buttons are siblings of
    this, never children of it, so no control can be drawn inside the picture area and
    no picture can push one out of the window.
    """


# --------------------------------------------------------------------- the stage


def streams_from(recording: Any, count: int | None = None) -> list[layouts.Stream]:
    """The recording's screens as `layouts.Stream`s: index, name, native pixel size.

    The names come from `window.screen_titles`, which is the one place that invents
    "Screen 2" and the one place that hands a recording's own names back untranslated.
    The sizes come from each screen's `size`, falling back to its desktop rectangle,
    because that is what `engine._screen_from_entry` already does when the sidecar
    names no frame size. A size is never required - a pane letterboxes whatever the
    decoder hands it - but knowing it saves waiting for the first frame to find out.
    """
    screens = getattr(recording, "screens", None)
    if not isinstance(screens, (list, tuple)):
        return []
    total = len(screens) if count is None else min(count, len(screens))
    titles = screen_titles(recording, total)
    out: list[layouts.Stream] = []
    for index in range(total):
        entry = screens[index]
        size = getattr(entry, "size", None)
        if not (isinstance(size, (list, tuple)) and len(size) == 2):
            desktop = getattr(entry, "desktop", None)
            size = (
                (desktop[2], desktop[3])
                if isinstance(desktop, (list, tuple)) and len(desktop) == 4
                else (0, 0)
            )
        out.append(
            layouts.Stream(
                index=index,
                title=titles[index],
                size=(int(size[0]), int(size[1])),
            )
        )
    return out


class PlayerStage(QtWidgets.QWidget):
    """One window: both pictures, the transport under them, and the layout switch.

    The three stage layouts of §3. The host builds it, restores the remembered
    geometry through the constructor, and listens to the signals below to write the
    viewer's choices back to `config.Settings`.

    Exactly **two** streams. The stage is the answer to "two pictures, one screen", the
    labels §3 names are «Экран 1» and «Экран 2», and `swapped(bool)` can only describe
    two things - a recording of three screens has no "swapped" state at all. A set with
    more streams than that opens on its first two and says which were left out on the
    notice strip, because showing two of three silently would be the worst of the three
    possible behaviours.
    """

    #: The viewer picked a different stage layout. The id, never the title.
    layout_changed = QtCore.Signal(str)
    #: Which stream is the main one: False for the recording's own order.
    swapped = QtCore.Signal(bool)
    #: The splitter's fraction, first pane over the whole, held to `SPLIT_MIN..MAX`.
    split_changed = QtCore.Signal(float)
    #: The window was closed, however. The host stops the engine; the stage does not,
    #: because the engine may well outlive one layout - §6 forbids a layout change
    #: restarting anything, and `separate` is a layout like any other.
    closed = QtCore.Signal()
    #: A different set of files is playing: §1's Open, the library, a drop, or §1.1's
    #: second video. The argument is the clock now in force, which is **not** the one
    #: the host built. A host that tied anything to the old engine - `open._build_
    #: windows` ties its deletion to `closed` - should re-tie it here; one that does
    #: not is still safe, because every engine this stage builds it also deletes.
    clock_changed = QtCore.Signal(object)
    #: Which stream the sound comes from, or -1 for off (§1.2).
    audio_source_changed = QtCore.Signal(int)
    #: A stream was slid against the other: its index and its new offset (§2.2).
    offset_changed = QtCore.Signal(int, int)

    def __init__(
        self,
        engine: Any,
        *,
        streams: Sequence[layouts.Stream] | None = None,
        layout: str = layouts.LAYOUT_SIDE_BY_SIDE,
        swapped: bool = False,
        split: float = 0.5,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self._engine = _Engine(engine)
        self._emitted_closed = False

        #: True when this object invented the screen names, so `retranslate` may say
        #: them again in the other language. A host that passed its own `streams` in
        #: owns those words and they are left exactly as they arrived - the same rule
        #: `PlayerWindows.retranslate` follows for a view renamed from outside.
        self._own_titles = streams is None
        found = list(streams) if streams is not None else streams_from(
            self._engine.recording
        )
        self._streams: list[layouts.Stream] = found[:2]
        # Both of these go through `_Engine.complain` rather than `_report`, so that
        # they reach the notice strip as well as the console. A viewer watching two of
        # three recorded screens has to be told which one is not on the stage; finding
        # that out by counting windows is the complaint this whole player already had
        # once. `complain` keeps them until `_connect_engine` has a strip to drain
        # them onto, which is after this constructor has built one.
        #
        # Which is exactly why both go through `_()`. They are the viewer's sentences,
        # not the developer's, and as f-strings they were the only two things this
        # window says that stayed English in a Russian interface - the three-screen one
        # half-English, because the stream names inside it are translated and the
        # sentence round them was not. The counted noun goes through `_screen_count`:
        # the second of these is reached with one screen, where «1 экрана» is wrong.
        if len(found) > 2:
            self._engine.complain(
                _(
                    "This recording has {screens} and this window shows two. "
                    "Not on it: {names}. Separate windows show them all."
                ).format(
                    screens=_screen_count(len(found)),
                    names=", ".join(stream.title for stream in found[2:]),
                )
            )
        if len(self._streams) < 2:
            self._engine.complain(
                _(
                    "This window shows two screens and this recording has "
                    "{screens}; the second pane will stay black."
                ).format(screens=_screen_count(len(self._streams)))
            )

        self._layout = layout if layout in STAGE_ORDER else STAGE_ORDER[0]
        if layout not in STAGE_ORDER:
            _report(
                f"{layout!r} is not a stage layout ({', '.join(STAGE_ORDER)}); "
                f"opening in {self._layout!r}."
            )
        #: `_order[0]` is the index of the stream in the main pane. A swap reverses it;
        #: the panes are then re-pointed and the labels go with them (§3).
        #:
        #: Built from the streams that were actually found, and holding their own
        #: indices - not `[0, 1]` or `[1, 0]` from the `swapped` argument alone. That
        #: version made `swap`'s `len(self._order) < 2` guard unreachable, so a stage
        #: over the one-screen recording the complaint above says this window handles
        #: swapped anyway: `_stream_at(0)` came back None, the main pane was skipped,
        #: position 1 resolved to the only stream there was, and the second pane lit
        #: up with a copy of it while `swapped(True)` went to the host to be stored.
        #: Carrying the real indices also fixes a host that passes `streams=` with a
        #: gap in them - screens 0 and 2 of a three-screen recording - which used to
        #: give the second pane sink 1, a stream the stage does not believe it shows.
        self._order: list[int] = [stream.index for stream in self._streams]
        if swapped:
            self._order.reverse()
        self._split = _held_split(split)
        self._scrubbing = False
        self._volume = 1.0
        self._muted = self._engine.muted
        #: The clock, as the transport last wrote it. Set before `_build`, because
        #: `set_position` reads `_duration` to clamp the scrubber and the first call
        #: happens while the window is still being assembled.
        self._position = 0
        self._duration = 0
        #: The remembered splitter fraction is applied once the splitter actually has
        #: a size. Before the window is shown it has none, and `setSizes([0, 0])` is
        #: silently ignored - which is how a restored 0.7 opened as an even split.
        self._split_applied = False

        #: Every (signal, slot) pair this stage connected to the current clock, so
        #: that §1.1's rebuild can take them all off again. Without the list the old
        #: engine would go on writing the transport's clock from its last tick, and
        #: the two would fight over the scrubber until the dead one was collected.
        self._clock_links: list[tuple[Any, Any]] = []
        #: The engines this stage built itself, and must therefore delete itself.
        #: The one it was handed belongs to the host (`open._free_clock_with` ties
        #: that one's deletion to `closed`), so it is retired and never deleted here.
        self._built_clocks: list[Any] = []
        self._retired_clocks: list[Any] = []
        #: §2.1's waiting streams, by stream index, and the ones currently wearing the
        #: badge. Two sets rather than one, because the badge lives on a *pane* and a
        #: swap moves streams between panes - the second set is how `_refresh_waiting`
        #: knows which pane to give its picture back to.
        self._waiting: set[int] = set()
        self._waiting_shown: set[int] = set()
        #: §1.1: the position a rebuilt clock is to be put back to, and whether it was
        #: running - kept until the new clock has read its file. See `_carry_over`.
        self._carry: tuple[int, bool] | None = None
        #: The instant after which waiting for that clock stops being patience and
        #: becomes a seek that is never going to land. `time.monotonic`, not the wall
        #: clock: a daylight-saving change must not be able to move it. `_carry_waits`.
        self._carry_deadline = 0.0

        self.setWindowTitle(_("ScreenRec player"))
        self.setMinimumSize(MIN_STAGE_WIDTH, MIN_STAGE_HEIGHT)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        # §1: "Drag and drop onto any player window. One file opens it; two files at
        # once open them as a pair." The window takes the drop, not a pane: a gesture
        # that works over the picture and not over the transport is a gesture people
        # learn is unreliable.
        self.setAcceptDrops(True)
        self.setAutoFillBackground(True)
        ground = self.palette()
        ground.setColor(
            QtGui.QPalette.ColorRole.Window, QtGui.QColor(theme.COLOURS["panel"])
        )
        self.setPalette(ground)

        self._build()
        self._apply_streams()
        self._lay_out()
        self._connect_engine()
        # After `_connect_engine`, because both of `docs/PLAYER_SOURCES.md`'s controls
        # are built out of what the clock can do and what it is holding: the frame
        # length for the nudges, the offsets a saved set opens with, and which stream
        # is heard. Before the first `transport_refresh`, so the window is never drawn
        # once with a sync panel that thinks the recording runs at the default rate.
        self._refresh_sources()
        self._start_polling()
        self._watch_the_displays()

        self.transport_refresh()
        i18n.notifier.changed.connect(self.retranslate)

    # -- building ----------------------------------------------------------

    def _build(self) -> None:
        """Header, stage area, notice strip, transport, footer - top to bottom."""
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header(), 0)

        self.area = _StageArea(self)
        body = QtWidgets.QVBoxLayout(self.area)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.splitter = _Splitter(QtCore.Qt.Orientation.Horizontal, self.area)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.equalise_requested.connect(lambda: self.equalise())
        self.splitter.splitterMoved.connect(self._splitter_dragged)
        self.splitter.resized.connect(self._apply_remembered_split)
        body.addWidget(self.splitter, 1)

        sinks = self._engine.sinks
        self.main = VideoPane(0, "", self._sink_for(0, sinks), parent=self.splitter)
        self.side = VideoPane(0, "", self._sink_for(1, sinks), parent=self.splitter)
        self.splitter.addWidget(self.main)
        self.splitter.addWidget(self.side)

        # Every `clicked` below is wrapped in a lambda rather than connected straight
        # through. `QAbstractButton.clicked` carries a `checked` bool, and a slot with
        # an optional argument swallows it silently: `swap(announce=True)` connected
        # directly becomes `swap(False)` from a button press, and the stage then
        # changes the picture without ever telling the host to save it.
        self.main.clicked.connect(lambda: self._engine.toggle())
        self.side.clicked.connect(lambda: self._engine.toggle())

        outer.addWidget(self.area, 1)

        self.notice = Notice(self)
        self.notice.hide()
        outer.addWidget(self.notice, 0)
        self._notice_timer = QtCore.QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(9000)
        self._notice_timer.timeout.connect(self.clear_notice)

        outer.addWidget(self._build_transport(), 0)

        # Under the transport and over the footer: §2.2's panel is about the two
        # streams rather than about the playhead, so it sits below the controls that
        # move the playhead and never between them and the pictures.
        # `window.SyncPanel`, not a second one written here. It is the control the
        # floating transport already carries, it emits an absolute offset rather than
        # a delta, and it owns the clamp and the frame length - so the two transports
        # cannot come to disagree about what "+1 кадр" moved.
        self.sync_panel = SyncPanel(self)
        self.sync_panel.hide()
        self.sync_panel.offset_requested.connect(self.set_offset)
        outer.addWidget(self.sync_panel, 0)

        outer.addWidget(self._build_footer(), 0)

    def _build_header(self) -> QtWidgets.QWidget:
        header = QtWidgets.QWidget(self)
        row = QtWidgets.QHBoxLayout(header)
        row.setContentsMargins(CHROME_MARGIN, CHROME_MARGIN, CHROME_MARGIN, 0)
        row.setSpacing(CHROME_GAP)

        row.addWidget(self._build_open_chip(header))

        self.layout_chip = surfaces.Chip("monitor", layouts.title(self._layout))
        self.layout_chip.setToolTip(_("Arrange (L)"))
        self.layout_menu = QtWidgets.QMenu(self)
        self._layout_actions: dict[str, QtGui.QAction] = {}
        group = QtGui.QActionGroup(self)
        group.setExclusive(True)
        for name in STAGE_ORDER:
            action = QtGui.QAction(layouts.title(name), self)
            action.setCheckable(True)
            action.setChecked(name == self._layout)
            action.triggered.connect(
                lambda _checked=False, which=name: self.set_layout(which)
            )
            group.addAction(action)
            self.layout_menu.addAction(action)
            self._layout_actions[name] = action
        self.layout_chip.clicked.connect(lambda: self._open_layout_menu())
        row.addWidget(self.layout_chip)

        self.swap_button = QtWidgets.QPushButton(_("Swap the screens"), header)
        self.swap_button.setObjectName("ghost")
        self.swap_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.swap_button.setToolTip(_("Swap the screens (S)"))
        self.swap_button.clicked.connect(lambda: self.swap())
        row.addWidget(self.swap_button)

        self.even_button = QtWidgets.QPushButton(_("Even them up"), header)
        self.even_button.setObjectName("ghost")
        self.even_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.even_button.setToolTip(_("Give both screens the same room"))
        self.even_button.clicked.connect(lambda: self.equalise())
        row.addWidget(self.even_button)

        self.switch = StreamSwitch([("0", "1"), ("1", "2")], header)
        # §6's table gives `1` and `2` to the stage layouts, and this switch is what
        # they move; the separate-windows half has no main stream to choose, so the
        # two keys belong here and are named here.
        self.switch.setToolTip(_("Which screen fills the window (1, 2)"))
        self.switch.changed.connect(self._switch_picked)
        row.addWidget(self.switch)

        row.addStretch(1)
        # The right-hand group is the other two questions of this feature: what you
        # hear, and whether the two pictures are the same instant. They are kept apart
        # from the arrangement controls on the left because §1.2 is precisely about
        # the two not being one question - a viewer reaching for "swap" and finding
        # the sound had moved with it is the failure that paragraph exists to stop.
        row.addWidget(self._build_sound_chip(header))
        row.addWidget(self._build_sync_chip(header))
        return header

    # -- §1: the three ways in ---------------------------------------------

    def _build_open_chip(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """The Open chip and the menu under it: §1's dialog, library and two actions.

        The menu is filled when it is about to be shown and not when it is built. Its
        contents depend on what is in the recordings folder *now* and on whether this
        set has one stream or two - §1.1 offers «Добавить второй экран» only to a lone
        video and §1.3 offers «Сохранить набор» only to a pair - and a menu assembled
        once at start-up would be answering a question nobody had asked yet.
        """
        self.open_chip = surfaces.Chip("folder", _("Open"))
        self.open_chip.setToolTip(
            pick("windows.open.button", "Open a recording (Ctrl+O)")
        )

        self.open_menu = QtWidgets.QMenu(self)
        # A disabled row with the reason on it teaches more than a row that is not
        # there - the layout chooser's own rule - and a disabled row shows nothing at
        # all without this.
        self.open_menu.setToolTipsVisible(True)
        self.library_menu = QtWidgets.QMenu(pick("stage.library", "Library"), self)
        self.library_menu.setToolTipsVisible(True)

        self.open_action = QtGui.QAction(pick("windows.open.dialog", "Open…"), self)
        self.open_action.setShortcut(QtGui.QKeySequence("Ctrl+O"))
        self.open_action.setShortcutContext(
            QtCore.Qt.ShortcutContext.WindowShortcut
        )
        self.open_action.triggered.connect(lambda _checked=False: self.open_dialog())
        # On the window as well as in the menu: a shortcut that only works once the
        # popup it lives in has been opened is not a shortcut. `WindowShortcut` so it
        # answers while the focus is in the sync panel's number field too.
        self.addAction(self.open_action)

        self.second_action = QtGui.QAction(
            pick("windows.open.second", "Add a second video…"), self
        )
        self.second_action.triggered.connect(
            lambda _checked=False: self.add_second_video()
        )
        self.save_action = QtGui.QAction(
            pick("windows.open.save", "Save this set"), self
        )
        self.save_action.triggered.connect(
            lambda _checked=False: self.save_this_set()
        )

        self.open_menu.aboutToShow.connect(self._fill_open_menu)
        self.open_chip.clicked.connect(
            lambda: self._drop_menu(self.open_chip, self.open_menu)
        )
        return self.open_chip

    def _fill_open_menu(self) -> None:
        """§1's menu, as it stands this second."""
        self.open_menu.clear()
        self.open_menu.addAction(self.open_action)
        self._fill_library_menu()
        self.open_menu.addMenu(self.library_menu)
        self.open_menu.addSeparator()
        if len(self._streams) > 1:
            self.open_menu.addAction(self.save_action)
        else:
            self.open_menu.addAction(self.second_action)

    def _fill_library_menu(self) -> None:
        """§1's library: what is in the recorder's output folder, newest first.

        `library.find_entries` is the one scanner - `open.scan` underneath it - so this
        list and "the newest recording" a shortcut opens can never disagree.

        **Without the durations, and capped at `LIBRARY_ROWS`.** §1 asks a row to carry
        a length and a length costs an `ffprobe` per file - a process per row, on the
        thread that is drawing this menu, while the picture behind it waits. A menu is
        built on a click and has to appear on that click, so a row carries the name,
        the date and the size, which are a `stat` each. That is
        `PlayerWindows.open_menu`'s decision and this is the same menu; a library
        *window* with lengths in it would be a different feature and is not here.

        `QMenu.clear()` deletes the actions it owns, which is every row below: they
        are parented to this menu and nothing else holds them. The four actions the
        stage owns are parented to the stage, which is why the Open menu can be
        cleared on every show without them being destroyed under it.
        """
        self.library_menu.clear()
        folder = self._library_folder()
        entries: list[Any] = []
        if folder is not None:
            try:
                entries = library.find_entries(folder, durations=False)
            except OSError as exc:
                # `find_entries` is documented never to raise. This is the belt beside
                # those braces: a folder on a drive that has gone must leave the menu
                # usable rather than take the window down from inside a popup.
                _report(f"the library in {folder} could not be read ({exc!r}).")
        if not entries:
            empty = self.library_menu.addAction(_("Nothing recorded yet"))
            empty.setEnabled(False)
            if folder is not None:
                empty.setToolTip(str(folder))
            return
        for entry in entries[:LIBRARY_ROWS]:
            action = self.library_menu.addAction(_library_row(entry))
            action.setToolTip(str(entry.path))
            action.triggered.connect(
                lambda _checked=False, where=entry.path: self.open_paths([where])
            )

    def _library_folder(self) -> Path | None:
        """Where the recorder writes, as the settings file says. None when it cannot.

        Read on every show rather than kept: the player and the recorder are two
        processes sharing one settings file, and a folder changed in one of them has
        to reach the other without the player being restarted.
        """
        settings, trouble = config.load_settings()
        if trouble:
            # Not said to the viewer: the defaults are a usable folder and this is a
            # menu, not a recording. The console gets it because an unreadable
            # settings file is also why a layout did not come back.
            _report(f"the settings would not read ({trouble}); using the defaults.")
        resolver = getattr(settings, "resolved_output_dir", None)
        if not callable(resolver):
            _report("the settings have no output folder; the library is empty.")
            return None
        try:
            return Path(resolver())
        except (OSError, TypeError, ValueError) as exc:
            _report(f"the output folder could not be resolved ({exc!r}).")
            return None

    def open_dialog(self) -> None:
        """§1's «Открыть…»: a file dialog, and whatever it names is opened.

        More than one file may be chosen, which §1 promises only of a drop. It is the
        same code and the same meaning - two files are a pair - and a viewer who has
        two cameras in a folder should not have to drag them to say so.
        """
        chosen = ask_for_files(
            self,
            pick("windows.open.dialog.title", "Open a recording"),
            multiple=True,
            folder=self._dialog_folder(),
        )
        if chosen:
            self.open_paths(chosen)

    def add_second_video(self) -> None:
        """§1.1's «Добавить второй экран…»: pick a file, play it beside this one."""
        if len(self._streams) > 1:
            self.say(
                pick(
                    "stage.second.already",
                    "This recording already has two screens.",
                )
            )
            return
        chosen = ask_for_files(
            self,
            pick("windows.open.second.title", "Add a second video"),
            folder=self._dialog_folder(),
            videos_only=True,
        )
        if chosen:
            self.add_video(chosen[0])

    def add_video(self, path: str | Path) -> bool:
        """§1.1: play `path` beside what is open. The position is carried across.

        The one rebuild. `QMediaPlayer` cannot be given a second file, so the clock is
        built again over both - and because the viewer asked for this by name, and not
        because a layout changed, the position and the paused state are carried over
        it. Losing somebody's place when they add the second camera is the failure
        they would notice before anything else on this window.
        """
        files = recording_files(self._engine.recording)
        if not files:
            self.say(
                pick(
                    "windows.open.second.unknown",
                    "The file behind this picture is not known, so a second one "
                    "cannot be added to it.",
                )
            )
            return False
        recording, note = sources.recording_from_files(
            [*files, Path(str(path))], audio_index=max(0, self._engine.audio_index)
        )
        if recording is None:
            self.say(note or _("{path} could not be opened.").format(path=path))
            return False
        if note:
            self.say(note)
        return self._adopt_clock(
            recording,
            position=self._engine.position,
            playing=self._engine.playing,
            offsets=self._offsets(),
        )

    def open_paths(self, paths: Sequence[str | Path]) -> bool:
        """Open a different recording in this window: §1's dialog, library and drop.

        One path goes through `open.load`, so an index opens as the set it describes,
        a folder opens its newest recording and a lone video opens as one stream -
        exactly as the command line already behaves. Two or more are §1's two cameras
        and are paired by `open.recording_from_files`.

        The layout is **not** asked about again. The viewer is looking at a window they
        have already arranged; re-asking on every Open would make the library unusable,
        and §1.1 sets the precedent for a set the viewer assembled themselves ("the
        chooser is not re-shown"). A recording with more streams than this window shows
        still says so on the notice strip, as it does when the host opens one.
        """
        wanted = [Path(str(item)) for item in paths if str(item).strip()]
        if not wanted:
            self.say(_("No recording was named."))
            return False
        if len(wanted) == 1:
            recording, note = sources.load(wanted[0])
        else:
            recording, note = sources.recording_from_files(wanted)
        if recording is None:
            self.say(
                note or _("{path} could not be opened.").format(path=wanted[0])
            )
            return False
        if note:
            self.say(note)
        return self._adopt_clock(recording)

    def save_this_set(self) -> bool:
        """§1.3's «Сохранить набор»: the pair, the audio choice and the offsets.

        Beside the first file and never over an index that is already there -
        `open.save_set` owns both of those rules and the format itself. What is here is
        the sentence afterwards, because a viewer who presses Save and is told nothing
        has no way to know whether anything was written or where.
        """
        files = recording_files(self._engine.recording)
        if len(files) < 2:
            self.say(
                pick(
                    "windows.save.alone",
                    "There is one video open, so there is no set to save.",
                )
            )
            return False
        audio = self._engine.audio_index
        where, trouble = sources.save_set(
            files,
            audio_index=audio if 0 <= audio < len(files) else 0,
            offsets=self._offsets(),
        )
        if where is None:
            self.say(trouble or pick("windows.save.failed", "Nothing was saved."))
            return False
        if trouble:
            self.say(trouble)
        self.say(
            pick("windows.save.done", "Saved as {name}").format(name=where.name)
        )
        return True

    def _dialog_folder(self) -> str:
        """Where a file dialog opens: beside what is playing, else the library."""
        files = recording_files(self._engine.recording)
        if files:
            return str(files[0].parent)
        folder = self._library_folder()
        return str(folder) if folder is not None else ""

    def _offsets(self) -> list[int]:
        """§2.1's offsets, one per **file** of the recording. Zeroes for an old clock.

        Per file and not per pane, which is the difference that matters twice: the
        list is indexed by stream index everywhere it is read, and §1.3 writes it into
        the index beside `files`, where one short would silently shift every offset
        onto the wrong camera. A stage over a three-screen recording shows two of them
        and still saves three.
        """
        screens = getattr(self._engine.recording, "screens", None)
        counted = len(screens) if isinstance(screens, (list, tuple)) else 0
        return self._engine.offsets(max(counted, len(self._streams)))

    # -- §1.1: the one rebuild ---------------------------------------------

    def _adopt_clock(
        self,
        recording: Any,
        *,
        position: int = 0,
        playing: bool = False,
        offsets: Sequence[int] = (),
    ) -> bool:
        """Play `recording` in this window, on a clock built here. True when it took.

        The only place in the player where a clock is replaced, and everything that
        makes that honest is in this one function rather than spread over the three
        callers: the old engine is disconnected before the new one is listened to, the
        speed, volume, mute and offsets are carried, the position is restored last and
        the paused state after it. A second copy of this logic is how the two of them
        would come to carry different halves of the viewer's state.

        The failure path leaves the window exactly as it was and says so out loud. The
        files are the viewer's own and anything at all can be in them, so building a
        clock over them is the one call here that is wrapped broadly.
        """
        speed = self._engine.speed
        try:
            clock = build_clock(recording, self)
        except Exception as exc:  # noqa: BLE001 - the files are the viewer's
            _report(f"a clock for {recording!r} could not be built: {exc!r}")
            self.say(
                pick(
                    "windows.open.failed", "{path} could not be opened: {detail}"
                ).format(
                    path=getattr(recording, "folder", ""),
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            return False

        was, was_resolved = self.engine, self._engine
        self._disconnect_clock()
        self.engine = clock
        self._engine = _Engine(clock)
        self._built_clocks.append(clock)
        self._retire(was, was_resolved)

        # The names are this module's again: they are generated from the new
        # recording, and a host's own `streams=` described the set that has just been
        # replaced.
        self._own_titles = True
        found = streams_from(clock.recording)
        self._streams = found[:2]
        self._order = [stream.index for stream in self._streams]
        self._waiting.clear()
        self._waiting_shown.clear()
        if len(found) > 2:
            self.say(
                _(
                    "This recording has {screens} and this window shows two. "
                    "Not on it: {names}. Separate windows show them all."
                ).format(
                    screens=_screen_count(len(found)),
                    names=", ".join(stream.title for stream in found[2:]),
                )
            )

        for index, value in enumerate(offsets):
            # Before the position is restored, so that each stream is placed where the
            # viewer had already lined it up rather than at the timeline's own instant.
            if index < len(self._streams) and int(value):
                self._engine.set_offset(index, int(value))

        self._apply_streams()
        self._lay_out()
        self._connect_clock()
        self._engine.set_muted(self._muted)
        self._engine.set_volume(self._volume)
        self._engine.set_speed(speed)
        self._carry_over(int(position), bool(playing))

        self._refresh_sources()
        self.transport_refresh()
        self._refresh_waiting()
        self.clock_changed.emit(clock)
        return True

    def _carry_over(self, position: int, playing: bool) -> None:
        """§1.1: put the new clock where the old one was, in the state it was in.

        Asked for more than once, and that is not belt and braces.
        `QMediaPlayer.setSource` is asynchronous: a `setPosition` issued before the
        file has been read is dropped, and the viewer would be thrown back to 00:00 by
        the very action they took to keep watching. So the position is asked for now,
        and asked for again the moment the new clock knows how long its files are -
        which is the moment it has read them.

        **That moment is noticed on this window's own timer, not only on the clock's
        duration signal.** `SyncEngine` emits a duration from `_announce_duration`,
        which runs from its tick - and its tick runs only while something is playing.
        After this rebuild the player is usually *paused*, because pausing on the
        clapperboard and then reaching for «Добавить второй экран» is the ordinary way
        this feature is used. Hanging the retry on that signal alone left `_carry`
        unclaimed for ever and the viewer at 00:00, with their place reappearing as a
        jump a second after they pressed play. `_carry_waits` is the retry that works
        in the state the carry exists for; `_carry_landed` stays because while
        something *is* playing the signal is the earlier of the two.

        A clock that already knows its length has read its files, so the attempt made
        here stuck and there is nothing to retry - `_carry` is dropped at once rather
        than left for a retry to re-seek a viewer who has since scrubbed.

        `PlayerWindows._carry_over` carries the same two values across the same
        rebuild for the separate-windows half; the mechanism is written twice because
        the two halves replace their clocks differently, and the *reason* is one.
        """
        self._carry = (max(0, int(position)), bool(playing))
        self._carry_deadline = monotonic() + CARRY_PATIENCE_MS / 1000.0
        self._apply_carry()
        if self._engine.duration > 0:
            self._carry = None

    def _apply_carry(self) -> None:
        if self._carry is None:
            return
        position, playing = self._carry
        if position > 0:
            self._engine.seek(position)
        if playing and not self._engine.playing:
            # `_Engine` publishes no `play`, and it does not need one: `toggle` is in
            # its `_REQUIRED` list, so every engine the player accepts has it, and a
            # clock that is already running must not be toggled into a pause.
            self._engine.toggle()

    def _carry_landed(self, _duration: int = 0) -> None:
        """The new clock knows its length, so the position it is given will stick."""
        if self._carry is None:
            return
        self._apply_carry()
        self._carry = None

    def _carry_waits(self) -> None:
        """Ask the new clock, on every poll, whether it has read its files yet.

        The paused half of `_carry_over`. A length is the proof - it is the first
        thing a `QMediaPlayer` can only answer once it has opened the file - and this
        window's poll timer runs whether or not anything is playing, which the
        engine's own tick does not.

        Bounded, because a file that never opens has no length to report and a carry
        left armed is a seek that could land on the viewer minutes later. After
        `CARRY_PATIENCE_MS` the place is given up and said out loud, rather than being
        held for an instant that is not coming.
        """
        if self._carry is None:
            return
        if self._engine.duration > 0:
            self._carry_landed()
            return
        if monotonic() >= self._carry_deadline:
            position, _playing = self._carry
            self._carry = None
            _report(
                f"the new clock never reported a length, so {position} ms was not "
                "restored; the files may not have opened."
            )

    def _retire(self, clock: Any, resolved: _Engine) -> None:
        """Let go of an engine this window has finished with, as safely as it can.

        **Released, not merely stopped.** `_Engine.release` carries the measurement:
        a stopped `QMediaPlayer` still holds its MP4 open, and the recorder that
        opened this player may be asked to write over that file within the minute. A
        retired clock that keeps its files is the same bug one window later.

        A clock this stage built is then deleted: nothing else knows it exists. The
        clock the **host** handed in is kept, because the host tied its deletion to
        this window's `closed` signal (`open._free_clock_with`) and deleting it now
        would leave that connection pointing at an object that is gone - a crash
        inside somebody else's teardown, which is the worst place to put one.
        """
        if clock is None:
            return
        resolved.listener = None
        resolved.close()
        resolved.release()
        if clock in self._built_clocks:
            self._built_clocks.remove(clock)
            delete = getattr(clock, "deleteLater", None)
            if callable(delete):
                delete()
            return
        self._retired_clocks.append(clock)

    def _build_transport(self) -> QtWidgets.QWidget:
        """The docked row: play, step, clock, scrubber, volume, speed, full screen.

        Not `window.TransportBar`: that class *is* a window - a `FloatingSurface` with
        frameless, stays-on-top and drag-to-move - and §4 wants this one docked. What
        is shared is every control on it, imported from `window` rather than written
        again, so the two transports cannot drift apart glyph by glyph.
        """
        bar = QtWidgets.QWidget(self)
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(CHROME_MARGIN, CHROME_GAP, CHROME_MARGIN, 0)
        row.setSpacing(CHROME_GAP)

        self.play_button = StageButton("play", _("Play (Space)"))
        self.play_button.clicked.connect(lambda: self._engine.toggle())
        row.addWidget(self.play_button)

        self.back_button = StageButton("step_back", _("One frame back (,)"))
        self.back_button.clicked.connect(lambda _checked=False: self._engine.step(-1))
        row.addWidget(self.back_button)

        self.forward_button = StageButton("step_forward", _("One frame on (.)"))
        self.forward_button.clicked.connect(lambda _checked=False: self._engine.step(1))
        row.addWidget(self.forward_button)

        self.clock = surfaces.label("00:00 / 00:00", "text_soft")
        clock_font = QtGui.QFont("Consolas")
        clock_font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        clock_font.setPointSizeF(9.5)
        self.clock.setFont(clock_font)
        self.clock.setMinimumWidth(104)
        self.clock.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.clock)

        self.scrubber = Scrubber(bar)
        self.scrubber.setRange(0, 0)
        # No focus on any control, so Space and the arrows reach `keyPressEvent`
        # instead of being answered by a slider or pressed into a button.
        self.scrubber.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.scrubber.setToolTip(_("Scrub"))
        self.scrubber.sliderPressed.connect(self._scrub_began)
        self.scrubber.sliderMoved.connect(self._scrub_moved)
        self.scrubber.sliderReleased.connect(self._scrub_ended)
        row.addWidget(self.scrubber, 1)

        self.volume_button = StageButton("volume")
        self._write_volume_tooltip()
        self.volume_button.clicked.connect(lambda: self.toggle_mute())
        row.addWidget(self.volume_button)

        self.speed_chip = SpeedChip(bar)
        self.speed_chip.changed.connect(self._engine.set_speed)
        row.addWidget(self.speed_chip)

        self.full_button = StageButton("fullscreen", _("Full screen"))
        self.full_button.clicked.connect(lambda: self.toggle_full_screen())
        row.addWidget(self.full_button)
        return bar

    def _build_footer(self) -> QtWidgets.QWidget:
        foot = QtWidgets.QWidget(self)
        row = QtWidgets.QHBoxLayout(foot)
        row.setContentsMargins(CHROME_MARGIN + 2, 2, CHROME_MARGIN, CHROME_GAP)
        row.setSpacing(0)
        self.footer = surfaces.label("", "text_faint", 9.0)
        self._write_footer()
        row.addWidget(self.footer)
        row.addStretch(1)
        return foot

    # -- §1.2: which file the sound comes from -----------------------------

    def _build_sound_chip(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """The sound chip and its menu: screen 1, screen 2, or no sound at all.

        A chip with a menu rather than a three-cell switch, because the chip *states*
        the answer where a switch makes the viewer work out which cell is lit. §1.2
        wants the choice visible, not merely available.

        The same control as `TransportBar`'s, down to the catalogue keys: this is the
        docked transport and that is the floating one, and a viewer who uses both must
        not find the sound named two different ways.

        **Mute is not this control.** The volume button silences what is playing; this
        says which file is playing at all, and -1 is the engine's own answer for none
        of them. Two states that sound alike from the speakers and are not: one is
        undone by turning the volume up and the other is not.
        """
        self.sound_chip = surfaces.Chip("speaker", "")
        self.sound_chip.clicked.connect(lambda: self._open_sound_menu())
        self._write_sound_chip()
        return self.sound_chip

    def _open_sound_menu(self) -> None:
        """§1.2's answers: each stream by name, and no sound.

        Built on the press and never kept, as `TransportBar._open_audio_menu` is: the
        stream names can be renamed from outside and the language can change under an
        open player, and a menu built once at start-up is the one place neither
        reaches.
        """
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(theme.style_sheet())
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        here = self._engine.audio_index
        for stream in self._streams:
            action = menu.addAction(stream.title)
            action.setCheckable(True)
            action.setChecked(stream.index == here)
            # `_checked` absorbs `triggered(bool)`; without it PySide hands `checked`
            # to the first parameter and every row would ask for stream 0 or 1.
            action.triggered.connect(
                lambda _checked=False, which=stream.index:
                self.set_audio_source(which)
            )
        menu.addSeparator()
        silence = menu.addAction(pick("windows.audio.off", "No sound"))
        silence.setCheckable(True)
        silence.setChecked(not 0 <= here < len(self._streams))
        silence.triggered.connect(lambda _checked=False: self.set_audio_source(-1))
        self.sound_chip.set_open(True)
        menu.aboutToHide.connect(lambda: self.sound_chip.set_open(False))
        menu.popup(
            self.sound_chip.mapToGlobal(
                QtCore.QPoint(0, self.sound_chip.height() + 2)
            )
        )

    def audio_source(self) -> int:
        """Which stream is heard, as the clock says. -1 when none of them is."""
        return self._engine.audio_index

    def set_audio_source(self, index: int) -> None:
        """Take the sound from stream `index`, or from none of them for -1. §1.2.

        **Nothing here moves the picture.** `_order` is neither read nor written, so
        choosing the other screen's sound leaves both panes exactly where they are -
        and `swap` returns the favour by never mentioning the sound. They are two
        questions and they get two controls, which is the whole of §1.2.

        The chip is written from the engine afterwards rather than from the number
        that was asked for, so a clock that refused the request goes on naming the
        stream that is actually being heard.
        """
        if not self._engine.can_choose_audio():
            # Never reached from the menu, which is not shown for a clock that cannot
            # do this; reachable from a host calling it. Said rather than dropped.
            self.say(
                pick(
                    "stage.audio.fixed",
                    "This recording's sound cannot be moved while it is open.",
                )
            )
            return
        self._engine.set_audio_source(int(index))
        self._write_sound_chip()
        self.audio_source_changed.emit(self._engine.audio_index)

    def _stream_title(self, index: int) -> str:
        """What this stage calls stream `index`, or a name for one it does not show."""
        for stream in self._streams:
            if stream.index == index:
                return stream.title
        return _("Screen {number}").format(number=index + 1)

    def _write_sound_chip(self) -> None:
        """The chip's label and its sentence, in one place. `TransportBar`'s words.

        Hidden for a recording with one stream and for a clock that cannot move the
        sound: with one file there is nothing to choose between, and a control that
        cannot do what it says is worse than a control that is not there. The volume
        button is on the transport in both cases, so the sound can still be turned
        off - which is a different question, as `_build_sound_chip` says.
        """
        self.sound_chip.setVisible(
            len(self._streams) > 1 and self._engine.can_choose_audio()
        )
        here = self._engine.audio_index
        silent = not 0 <= here < len(self._streams)
        self.sound_chip.set_value(
            "speaker_off" if silent else "speaker",
            pick("windows.audio.off", "No sound") if silent
            else self._stream_title(here),
        )
        self.sound_chip.setToolTip(
            pick("windows.audio.hint.off", "Nothing is being heard - click to choose")
            if silent else
            pick(
                "windows.audio.hint", "The sound comes from {name} - click to change"
            ).format(name=self._stream_title(here))
        )

    # -- §2.2: lining the two up -------------------------------------------

    def _build_sync_chip(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """The chip that opens §2.2's panel, and carries its value while it is shut.

        The panel is a whole row of controls and most viewers never need it: the
        recorder's own screens start together and their offset is 0 for ever. So it
        folds away - and the chip keeps the number on screen, because §2.2 is explicit
        that "an offset that is silently non-zero is a recording that looks broken for
        a reason nobody can see".
        """
        self.sync_chip = surfaces.Chip("refresh", "")
        self.sync_chip.clicked.connect(
            lambda: self.show_sync_panel(not self.sync_panel.isVisible())
        )
        self._write_sync_chip()
        return self.sync_chip

    def show_sync_panel(self, show: bool = True) -> None:
        """Open or fold §2.2's panel.

        Refused for anything but two streams, and for a clock with no `set_offset`:
        the panel's picker is a two-cell switch, a third stream would be a control
        that cannot address it, and a panel that moves nothing teaches the viewer that
        the player is broken.
        """
        wanted = bool(show) and self._can_sync()
        self.sync_panel.setVisible(wanted)
        self.sync_chip.set_open(wanted)

    def _can_sync(self) -> bool:
        """Two streams, and a clock that can slide one against the other."""
        return len(self._streams) == 2 and self._engine.can_sync()

    def sync_index(self) -> int:
        """The stream §2.2's controls move; the other is the reference."""
        return self.sync_panel.moving()

    def offset_of(self, index: int) -> int:
        """§2.1's offset for one stream, as the clock holds it. 0 when it has none."""
        offsets = self._offsets()
        return offsets[index] if 0 <= index < len(offsets) else 0

    def set_offset(self, index: int, milliseconds: int) -> None:
        """Slide one stream against the other. §2.2: the timeline must not move.

        The whole of the operation is `engine.set_offset`, which is documented to
        reseek that one stream and to pin the timeline across it. **Nothing here
        seeks**, and the test for that is not that the position happens to be the same
        afterwards but that no `seek` reached the clock at all.

        Nothing is written back into the panel here either: the engine answers with
        `offsets_changed`, and taking the number from there means a value it clamped
        or refused is the value the viewer ends up reading. Writing the asked-for
        number into the field instead would let the control and the recording disagree
        over a sign convention, which is the one disagreement nobody can debug.
        """
        if not self._engine.can_sync():
            self.say(
                pick(
                    "stage.sync.cannot",
                    "This player's clock cannot line two videos up.",
                )
            )
            return
        if index not in [stream.index for stream in self._streams]:
            _report(f"stream {index!r} is not on this stage; nothing was moved.")
            return
        self._engine.set_offset(int(index), int(milliseconds))
        self.offset_changed.emit(int(index), self.offset_of(int(index)))

    def nudge_offset(self, *, frames: int = 0, milliseconds: int = 0) -> None:
        """§2.2's four buttons and four keys, through the panel's own arithmetic.

        The panel owns the clamp and the frame length, so the keyboard and the buttons
        are one code path rather than two that have to agree about both.
        """
        self.sync_panel.nudge(frames=frames, milliseconds=milliseconds)

    def reset_offset(self) -> None:
        """§2.2's Reset: the picked stream back in step with the reference."""
        self.sync_panel.reset()

    def _offsets_moved(self) -> None:
        """The clock says an offset has changed, whoever changed it."""
        self.sync_panel.set_offsets(self._offsets())
        self._write_sync_chip()
        self._write_footer()
        self._refresh_waiting()

    def _refresh_sources(self) -> None:
        """Hand the panel and the chips the set that is playing now.

        Called at start-up and after §1.1's rebuild, where everything has changed at
        once: different streams, different names, different offsets, a different file
        carrying the sound. `recording_framerate` rather than a constant, because §2.1
        is explicit that the frame step uses the reference stream's own rate.
        """
        self.sync_panel.set_streams(
            [stream.title for stream in self._streams],
            self._offsets(),
            recording_framerate(self._engine.recording),
        )
        self.sync_chip.setVisible(self._can_sync())
        if not self._can_sync():
            self.sync_panel.setVisible(False)
            self.sync_chip.set_open(False)
        elif any(self._offsets()):
            # A set saved with an offset opens with the panel already showing it: the
            # number explains the picture, and a viewer who has to find a control
            # before they can be told why their two cameras are 1.5 s apart will read
            # it as a broken recording first.
            self.show_sync_panel(True)
        self._write_sync_chip()
        self._write_sound_chip()

    def _write_sync_chip(self) -> None:
        """The chip says the offset when there is one, and its own name when not.

        `TransportBar`'s two sentences for its sync button, through the same keys, for
        the reason the sound chip shares its: one feature, two transports, one wording.
        """
        moved = [value for value in self._offsets() if value]
        if moved:
            self.sync_chip.set_value("refresh", format_offset(moved[0]))
            self.sync_chip.setToolTip(
                pick(
                    "windows.sync.button.moved",
                    "Line the screens up by hand - one is {offset} out",
                ).format(offset=format_offset(moved[0]))
            )
            return
        self.sync_chip.set_value(
            "refresh", pick("windows.sync.in.step", "both are in step")
        )
        self.sync_chip.setToolTip(
            pick("windows.sync.button", "Line the screens up by hand")
        )

    # -- §2.1: a stream the timeline has not reached yet --------------------

    def _stream_waiting(self, index: int, waiting: bool) -> None:
        """One stream has nothing to show yet, or has started. §2.1's black and badge."""
        index = int(index)
        if waiting:
            self._waiting.add(index)
        else:
            self._waiting.discard(index)
        self._refresh_waiting()

    def _refresh_waiting(self) -> None:
        """Hold black with a badge for every waiting stream, and only for those.

        §2.1: "Showing a frozen first frame as if it were live is the thing to avoid."
        The engine parks a waiting player on its first frame, so the picture itself has
        to go - the pane's own ground is black, and hiding the video widget is what
        "black" means here without a second kind of overlay being invented.

        The badge is the ended badge with other words, because that is exactly what §6
        built: one quiet line along the bottom of a pane that is not showing live
        video. What is not shared is *when it is cleared* - `set_busy` clears every
        ended badge on a seek and the engine re-announces nothing that has not changed,
        so the waiting ones are put back here afterwards.
        """
        for pane in self._panes():
            stream = self._stream_of(pane)
            if stream is None:
                continue
            if stream.index in self._waiting:
                pane.set_ended(
                    True,
                    pick(
                        "windows.waiting", "{name} — this recording starts later"
                    ).format(name=stream.title),
                )
                pane.video.setVisible(False)
                self._waiting_shown.add(stream.index)
            elif stream.index in self._waiting_shown or pane.video.isHidden():
                # Either this stream's wait is over, or a swap has handed this pane a
                # stream that is not waiting while the pane itself is still dark.
                #
                # `isHidden`, not `not isVisible`: a child of a window that has not
                # been shown yet reports `isVisible() == False` whatever was asked of
                # it, so the second test would fire on every stage at start-up and
                # clear a badge §6 had just put up.
                pane.video.setVisible(True)
                pane.set_ended(
                    bool(_ask(self.engine, "stream_has_ended", False, stream.index))
                )
                self._waiting_shown.discard(stream.index)

    # -- the engine --------------------------------------------------------

    def _drop_menu(self, chip: surfaces.Chip, menu: QtWidgets.QMenu) -> None:
        """Drop `menu` under `chip`, and let the chip show that it is open.

        One function for all three chips, so a fourth cannot open its menu somewhere
        else. `exec` blocks until the menu closes, which is why the chip is put back
        in a `finally`: a menu dismissed with Escape must not leave the chip lit.
        """
        chip.set_open(True)
        try:
            menu.exec(chip.mapToGlobal(QtCore.QPoint(0, chip.height() + 2)))
        finally:
            chip.set_open(False)

    def _sink_for(self, position: int, sinks: Sequence[Any]) -> Any:
        """The sink of the stream currently in `position` of `_order`, or None."""
        if position >= len(self._order):
            return None
        index = self._order[position]
        return sinks[index] if 0 <= index < len(sinks) else None

    def _connect_engine(self) -> None:
        """Everything the stage listens to on the clock it has now.

        `window._Engine` resolves the transport's own calls and signals; the four
        states that are not in its table - §6's `seeking_changed` and `stream_ended`,
        and §2's `stream_waiting` and `offsets_changed` - are resolved here instead,
        because `_Engine._SIGNALS` is a table in a module this feature may not edit.
        An engine without them is not an error: a single-stream recording never dims,
        never badges and has nothing to line up, which is §6's own answer for it.

        **Every connection is recorded.** §1.1's rebuild has to take them all off
        again, and an engine still writing this window's clock after it has been
        replaced would fight the new one for the scrubber on every tick.
        """
        self._engine.listener = self.say
        for note in self._engine.drain_notes():
            self.say(note)
        for pane in self._panes():
            # A pane with no stream at all has already been accounted for, by the
            # constructor's own sentence about a recording with fewer than two
            # screens. Its complaint would be the same news with the worse wording -
            # the pane has no name to put in it, so it reads ": no source of frames".
            if pane.setup_problem and self._stream_of(pane) is not None:
                self.say(pane.setup_problem)

        for key, slot in (
            ("position", self.set_position),
            ("duration", self.set_duration),
            ("playing", self.set_playing),
            ("problem", self._engine_said),
            ("finished", self._engine_finished),
            # §1.1's second attempt at the carried position. On the duration because
            # that is the first thing a `QMediaPlayer` can only say once it has read
            # the file - see `_carry_over`. A no-op whenever nothing is being carried,
            # which is every clock but a rebuilt one.
            ("duration", self._carry_landed),
        ):
            self._listen(self._engine.signal(key), slot)

        self._listen(_optional_signal(self.engine, "seeking_changed"), self.set_busy)
        self._listen(_optional_signal(self.engine, "stream_ended"), self._stream_ended)
        self._listen(
            _optional_signal(self.engine, "stream_waiting"), self._stream_waiting
        )
        self._listen(
            _optional_signal(self.engine, "offsets_changed"), self._offsets_moved
        )

        # The dimming veil, the ended badges and §2.1's waiting streams are *states*,
        # not only news: a stage built in the middle of a seek, after the shorter
        # stream ran out, or over a set whose second camera starts later would
        # otherwise never hear about any of them.
        self.set_busy(bool(_ask(self.engine, "seeking", False)))
        for stream in self._streams:
            if _ask(self.engine, "stream_has_ended", False, stream.index):
                self._stream_ended(stream.index)
            if _ask(self.engine, "stream_is_waiting", False, stream.index):
                self._waiting.add(stream.index)
        self._refresh_waiting()

    #: The rebuild calls this under its own name; the constructor keeps the old one.
    #: Two names for one method rather than a rename, because `_connect_engine` is
    #: what this window has always called it and a rename for its own sake is a diff
    #: nobody can read.
    _connect_clock = _connect_engine

    def _listen(self, signal: Any, slot: Any) -> None:
        """Connect, and remember the pair so `_disconnect_clock` can undo it."""
        if signal is None:
            return
        try:
            signal.connect(slot)
        except (RuntimeError, TypeError) as exc:
            _report(f"{slot!r} could not be connected to {signal!r}: {exc!r}")
            return
        self._clock_links.append((signal, slot))

    def _disconnect_clock(self) -> None:
        """Stop listening to the clock in force. §1.1's rebuild, and nothing else."""
        self._engine.listener = None
        for signal, slot in self._clock_links:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError) as exc:
                # PySide raises rather than returning False when the connection has
                # already gone. Said out loud, because the other reading - "the old
                # clock is still driving this transport" - is the bug this list exists
                # to prevent and it would otherwise be invisible.
                _report(f"a clock connection would not come off ({exc!r}).")
        self._clock_links.clear()

    def _start_polling(self) -> None:
        """The one timer this window owns, started once and never rebuilt.

        Runs whatever the engine publishes, for `PlayerWindows`' two reasons: it is
        the only clock for an engine with no position signal, and `SyncEngine` emits a
        duration only from its own tick, which runs only while something is playing -
        so without this the stage would open reading "00:00 / 00:00" with a dead
        scrubber until the viewer pressed play.

        That same engine limitation is why §1.1's carried position is retried here
        too: a rebuild made while paused never gets a duration signal, so the retry
        that hangs off one cannot be the only one. `_carry_waits` runs first of the
        two slots, so the refresh in the same tick draws the position that has just
        been restored rather than the 00:00 it is about to replace.

        Out of `_connect_engine` because that one runs again on §1.1's rebuild, and a
        second timer on the same window would refresh the transport twice as often for
        the rest of the session - and only the last of them would ever be stopped.
        """
        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._carry_waits)
        self._poll.timeout.connect(self.transport_refresh)
        self._poll.start()

    # -- the language ------------------------------------------------------

    def retranslate(self, _language: str = "") -> None:
        """Say everything this window says, again, in the language now in force.

        The screen *names* are said again only when this object invented them. A host
        that handed its own `streams` in owns those words - they may be what the
        recording calls those screens - and rewriting them here would replace the
        viewer's own word with "Screen 2".

        The notice strip is deliberately not retranslated, for `TransportBar`'s reason:
        what is on it is a finished sentence from the engine, and a sentence cannot be
        translated back into the language it was not written in.
        """
        self.setWindowTitle(_("ScreenRec player"))
        self.open_chip.set_value("folder", _("Open"))
        self.open_chip.setToolTip(
            pick("windows.open.button", "Open a recording (Ctrl+O)")
        )
        self.open_action.setText(pick("windows.open.dialog", "Open…"))
        self.second_action.setText(
            pick("windows.open.second", "Add a second video…")
        )
        self.save_action.setText(pick("windows.open.save", "Save this set"))
        self.library_menu.setTitle(pick("stage.library", "Library"))
        self.layout_chip.set_value("monitor", layouts.title(self._layout))
        self.layout_chip.setToolTip(_("Arrange (L)"))
        for name, action in self._layout_actions.items():
            action.setText(layouts.title(name))
        self.swap_button.setText(_("Swap the screens"))
        self.swap_button.setToolTip(_("Swap the screens (S)"))
        self.even_button.setText(_("Even them up"))
        self.even_button.setToolTip(_("Give both screens the same room"))
        self.switch.setToolTip(_("Which screen fills the window (1, 2)"))
        self.scrubber.setToolTip(_("Scrub"))
        self.back_button.setToolTip(_("One frame back (,)"))
        self.forward_button.setToolTip(_("One frame on (.)"))
        self.play_button.setToolTip(self._play_tooltip())
        self.full_button.setToolTip(self._full_tooltip())
        self._write_volume_tooltip()
        self._write_footer()
        if self._own_titles:
            self._rename_streams()
        # After the renaming, because both chips and the panel's summary put a
        # stream's name in a sentence. `SyncPanel.retranslate` is connected to the
        # notifier itself and says its own words; the stream names *in* it are this
        # window's, and `_refresh_sources` is where they are handed over again.
        self._refresh_sources()
        # §2.1's badge carries a stream's name and its own sentence, and a pane only
        # re-words a badge it wrote itself - so the waiting ones are said again here
        # or they stay in the language the stream started waiting in.
        self._waiting_shown.clear()
        self._refresh_waiting()

    def _rename_streams(self) -> None:
        """Re-read the generated screen names and put them back on the panes."""
        titles = screen_titles(self._engine.recording, len(self._streams))
        if len(titles) != len(self._streams):
            return
        self._streams = [
            layouts.Stream(index=stream.index, title=title, size=stream.size)
            for stream, title in zip(self._streams, titles)
        ]
        for position, pane in ((0, self.main), (1, self.side)):
            stream = self._stream_at(position)
            if stream is not None and pane.title() != stream.title:
                pane.set_label(stream.title)

    def _play_tooltip(self) -> str:
        """What the play button says in the state it is in.

        Read off the glyph rather than from a flag of its own: `set_playing` is the
        only writer of both, so a second copy of the state could only ever disagree
        with the picture the viewer is looking at.
        """
        if self.play_button.kind == "pause":
            return _("Pause (Space)")
        return _("Play (Space)")

    def _full_tooltip(self) -> str:
        if self.isFullScreen():
            return _("Leave full screen")
        return _("Full screen")

    def _write_volume_tooltip(self) -> None:
        """The volume button's sentence, with the level in it.

        Two whole templates rather than a word glued to a tail, for the reason
        `TransportBar._write_volume_tooltip` gives: Russian does not put the verb and
        the rest of the sentence together the way English does.
        """
        percent = round(self._volume * 100)
        template = (
            _("Unmute - volume {percent}%, scroll here to change it")
            if self._muted
            else _("Mute - volume {percent}%, scroll here to change it")
        )
        self.volume_button.setToolTip(template.format(percent=percent))

    def _write_footer(self) -> None:
        """The mock-up's footer line: how many streams, and that they share one clock.

        Two translated fragments joined by a middle dot rather than one template with
        two holes, because both fragments already exist in the `player` block - the
        separate-windows transport says the same two things - and a key lives in one
        block only. The dot is punctuation, not grammar, so nothing a translator needs
        to move is trapped on the Python side of the join.

        The count goes through `_stream_count` rather than through the one template,
        because «{count} видеопотока» is the *few* form: right for the two streams a
        stage normally has, wrong for the one the constructor says this window also
        opens on, and wrong again from five up.
        """
        streams = _stream_count(len(self._streams))
        # And whether they really are in step. A footer saying the two files play
        # together while the chip beside it says +1200 ms is the window
        # contradicting itself about the one thing the sync panel exists to do.
        state = (
            _("Lined up by hand") if any(self._offsets())
            else _("Playing in step")
        )
        self.footer.setText(f"{streams} · {state}")

    # -- saying something to the viewer ------------------------------------

    def say(self, text: str) -> None:
        """One sentence on the strip above the transport, for a few seconds.

        A second message replaces the first rather than queueing: these arrive from the
        engine when a file misbehaves, and the one on screen now is the one that still
        matters.
        """
        message = " ".join(str(text).split())
        if not message:
            return
        self.notice.set_text(message)
        self.notice.show()
        self._notice_timer.start()

    def clear_notice(self) -> None:
        """Take the strip away. Safe to call when nothing is showing."""
        self._notice_timer.stop()
        if self.notice.isHidden():
            return
        self.notice.hide()
        self.notice.set_text("")

    def _engine_said(self, text: str) -> None:
        _report(f"the engine says: {text}")
        self.say(text)

    def _engine_finished(self) -> None:
        self.set_playing(False)
        self.set_position(self._engine.position)

    # -- which stream is where (§3) ----------------------------------------

    def streams(self) -> list[layouts.Stream]:
        return list(self._streams)

    def _stream_at(self, position: int) -> layouts.Stream | None:
        """The stream in `position` of the order - 0 is the main pane."""
        if position >= len(self._order):
            return None
        index = self._order[position]
        for stream in self._streams:
            if stream.index == index:
                return stream
        return None

    def _panes(self) -> tuple[VideoPane, ...]:
        return (self.main, self.side)

    def _stream_of(self, pane: VideoPane) -> layouts.Stream | None:
        """The stream this pane was given, or None when the recording had none for it.

        The main pane is position 0 of the order and the other is position 1, so this
        is the position-to-stream question asked from the widget's end.
        """
        return self._stream_at(0 if pane is self.main else 1)

    def _panes_for(self, index: int) -> list[VideoPane]:
        """Every pane currently showing stream `index`, visible or not."""
        return [pane for pane in self._panes() if pane.stream_index() == index]

    def main_stream(self) -> int:
        """The index of the stream filling the stage."""
        return self._order[0] if self._order else 0

    def _natural_order(self) -> list[int]:
        """The stream indices in the order the recording itself gives them."""
        return [stream.index for stream in self._streams]

    def is_swapped(self) -> bool:
        """True when the main pane holds the *second* stream of the recording.

        Read against the recording's own order rather than against the number 0: a
        host may pass `streams=` holding screens 2 and 5 of a longer recording, and
        `_order[0] != 0` would call that stage swapped before anyone had touched it.
        """
        return self._order != self._natural_order()

    def set_main_stream(self, index: int, *, announce: bool = True) -> None:
        """Make stream `index` the main one. A no-op when it already is.

        This is `1` and `2` from §7, and it is the same operation as `swap` for two
        streams - which is the only number of streams a stage has.
        """
        if index not in self._order:
            _report(f"stream {index!r} is not on this stage; the request was dropped.")
            return
        if self._order[0] == index:
            return
        self.swap(announce=announce)

    def set_swapped(self, swapped: bool, *, announce: bool = True) -> None:
        """Restore a remembered `player_swapped`. A no-op when it already is."""
        if bool(swapped) == self.is_swapped():
            return
        self.swap(announce=announce)

    def swap(self, *, announce: bool = True) -> None:
        """Exchange the two streams between the panes. §6: nothing is sought.

        `announce` is keyword-only so that a signal carrying a stray positional - a
        button's `checked` bool - raises here instead of quietly turning the
        announcement off and leaving the host's saved layout out of step with the
        window the viewer is looking at.

        The engine is not called at all - not even asked where it is - so there is no
        path from here to a seek, a reload or a restart. The labels travel with the
        streams, so «Экран 1» is still «Экран 1» on the other side.
        """
        if len(self._order) < 2:
            # Reachable now that `_order` is built from the streams that were found.
            # Said out loud rather than returned in silence: a viewer pressing S on a
            # one-screen recording gets nothing, and the console is where the reason
            # for nothing belongs. The swap button is disabled for the same case, so
            # this is the keyboard's path and a host calling `swap()` directly.
            _report("this stage has one stream; there is nothing to swap it with.")
            return
        self._order.reverse()
        self._apply_streams()
        self.switch.set_current(str(self.main_stream()))
        if announce:
            self.swapped.emit(self.is_swapped())

    def _apply_streams(self) -> None:
        """Point each pane at the stream its position now holds.

        `VideoPane.set_stream` is a disconnect and a connect, and it deliberately
        forgets the stream's shape and clears the ended badge - so the shape is given
        back here from the sidecar, and the badge is asked for again from the engine.
        Without the first, a pane waits for a frame to learn the shape it is already
        being told; without the second, a recording whose shorter stream has already
        run out loses its badge on every swap.
        """
        sinks = self._engine.sinks
        for position, pane in ((0, self.main), (1, self.side)):
            stream = self._stream_at(position)
            if stream is None:
                continue
            pane.set_stream(stream.index, stream.title, self._sink_for(position, sinks))
            if stream.size[0] > 0 and stream.size[1] > 0:
                pane.set_native_size(stream.size)
            if _ask(self.engine, "stream_has_ended", False, stream.index):
                pane.set_ended(True)
        # §2.1's badge is about the *stream*, so it travels with it: without this a
        # swap would leave the black pane where the waiting stream used to be and put
        # its live picture under a badge saying it has not started.
        self._refresh_waiting()

    def _switch_picked(self, key: str) -> None:
        try:
            index = int(key)
        except ValueError:
            _report(f"the focus switch offered {key!r}, which is not a stream index.")
            return
        self.set_main_stream(index)

    # -- the layout (§3) ---------------------------------------------------

    def layout_id(self) -> str:
        return self._layout

    def set_layout(self, layout: str, *, announce: bool = True) -> None:
        """Show the same two streams a different way. §6: nothing is sought.

        Every branch below moves widgets and re-cuts rectangles. None of them calls
        the engine, which is how "changing the layout must not seek, reload or restart
        anything" is kept - not by being careful, but by there being nothing there.
        """
        if layout not in STAGE_ORDER:
            _report(
                f"{layout!r} is not a stage layout ({', '.join(STAGE_ORDER)}); "
                "the request was dropped."
            )
            return
        if layout == self._layout:
            return
        self._layout = layout
        self._lay_out()
        if announce:
            self.layout_changed.emit(layout)

    def cycle_layout(self) -> None:
        """`L` from §7: the next of the three, wrapping round."""
        here = STAGE_ORDER.index(self._layout)
        self.set_layout(STAGE_ORDER[(here + 1) % len(STAGE_ORDER)])

    def _lay_out(self) -> None:
        """Put the widgets where the current layout wants them."""
        split = self._layout in (layouts.LAYOUT_SIDE_BY_SIDE, layouts.LAYOUT_STACKED)
        upright = self._layout == layouts.LAYOUT_STACKED

        self.splitter.setOrientation(
            QtCore.Qt.Orientation.Vertical if upright
            else QtCore.Qt.Orientation.Horizontal
        )
        # The minimum is set on the axis the splitter divides and released on the
        # other, so a tall narrow stage can still show two stacked panes. Setting both
        # at once would give the window a 320x320 floor it has no need of.
        for pane in (self.main, self.side):
            pane.setMinimumWidth(0 if upright else layouts.MIN_PANE)
            pane.setMinimumHeight(layouts.MIN_PANE if upright else 0)

        self.side.setVisible(split)
        self.even_button.setVisible(split)
        # A stage over a one-screen recording has nothing to swap and nothing to
        # choose between. The guards in `swap` and `set_main_stream` are what make
        # that state safe; a live control that does nothing is what makes it rude.
        both = len(self._streams) > 1
        self.swap_button.setEnabled(both)
        self.switch.setVisible(self._layout == layouts.LAYOUT_FOCUS and both)
        self.switch.set_current(str(self.main_stream()))
        # Both of `docs/PLAYER_SOURCES.md`'s controls are for a pair: one video has
        # nothing to line up and nothing to choose the sound between. §1's regression
        # that matters most is the one-video recording, so they go away rather than
        # sitting there greyed.
        self.sync_chip.setVisible(self._can_sync())
        self.sound_chip.setVisible(
            len(self._streams) > 1 and self._engine.can_choose_audio()
        )

        self.layout_chip.set_value("monitor", layouts.title(self._layout))
        action = self._layout_actions.get(self._layout)
        if action is not None:
            action.setChecked(True)
        if split:
            self.set_split(self._split, announce=False)

    def _open_layout_menu(self) -> None:
        """Drop the menu under the chip, and let the chip show that it is open."""
        self._drop_menu(self.layout_chip, self.layout_menu)

    # -- the splitter ------------------------------------------------------

    def split(self) -> float:
        """The fraction of the stage the first pane has, held to `SPLIT_MIN..MAX`."""
        return self._split

    def set_split(self, fraction: float, *, announce: bool = True) -> None:
        """Give the first pane `fraction` of the splitter. Clamped before it is stored.

        The clamp is §2.3's own 0.15..0.85, applied to the *stored* number rather than
        read back out of the sizes Qt settled on: `layouts.MIN_PANE` can push a legal
        0.15 up on a narrow stage, and storing what the pixels came to would write a
        number back to the settings file that depends on the window's width rather
        than on anything the viewer chose.
        """
        held = _held_split(fraction)
        self._split = held
        total = self._splitter_extent()
        if total > 0:
            first = int(round(total * held))
            self.splitter.setSizes([first, max(0, total - first)])
        if announce:
            self.split_changed.emit(held)

    def _apply_remembered_split(self) -> None:
        """Put the restored fraction on the splitter, once it is big enough to hold it.

        Waiting for `2 * layouts.MIN_PANE` rather than for any size at all: below that
        both panes are at their minimum whatever fraction is asked for, `setSizes`
        hands back two equal halves, and marking the fraction "applied" there would
        throw the viewer's remembered 0.7 away for the rest of the session.
        """
        if self._split_applied:
            return
        if self._splitter_extent() < 2 * layouts.MIN_PANE:
            return
        self._split_applied = True
        self.set_split(self._split, announce=False)

    def _splitter_extent(self) -> int:
        """The splitter's size along the axis it divides."""
        if self.splitter.orientation() == QtCore.Qt.Orientation.Vertical:
            return self.splitter.height()
        return self.splitter.width()

    def equalise(self) -> None:
        """§3's «Выровнять», and the double-click on the handle: two equal halves."""
        self.set_split(0.5)

    def _splitter_dragged(self, _position: int, _index: int) -> None:
        sizes = self.splitter.sizes()
        total = sum(sizes)
        if len(sizes) < 2 or total <= 0:
            return
        held = _held_split(sizes[0] / total)
        if held == self._split:
            return
        self._split = held
        self.split_changed.emit(held)

    # -- the transport -----------------------------------------------------

    def set_position(self, milliseconds: int) -> None:
        """Where the playhead is, as the engine says. Ignored during a drag.

        `_position` belongs to the drag while the handle is down, and that is the
        whole of the fix for a second seek on release. `_scrub_ended` compares the
        released value against `_position` to decide whether the drag has already
        asked for this millisecond; the 100 ms poll calls this method throughout the
        drag with the master player's *actual* position, which `SyncEngine`'s own
        docstring measures landing 33-67 ms away from the number it was given. So the
        comparison never held and every release sent a redundant `seek` - pause every
        player, move it, start it again, with §6's veil re-armed - for nothing. The
        clock is left alone too: it is drawn from `_position`, which the drag is
        writing, and following the player back would make the numbers fight.
        """
        if self._scrubbing:
            return
        self._position = max(0, int(milliseconds))
        blocked = self.scrubber.blockSignals(True)
        try:
            self.scrubber.setValue(min(self._position, self._duration))
        finally:
            self.scrubber.blockSignals(blocked)
        self._write_clock()

    def set_duration(self, milliseconds: int) -> None:
        self._duration = max(0, int(milliseconds))
        self.scrubber.setRange(0, self._duration)
        self.scrubber.setPageStep(5000)
        self.scrubber.setSingleStep(1000)
        self._write_clock()

    def set_playing(self, playing: bool) -> None:
        self.play_button.set_kind("pause" if playing else "play")
        self.play_button.setToolTip(self._play_tooltip())

    def set_speed(self, speed: float) -> None:
        self.speed_chip.set_value(speed)

    def transport_refresh(self) -> None:
        """Read the clock and show it. The only thing the poll timer does."""
        self.set_position(self._engine.position)
        self.set_duration(self._engine.duration)
        self.set_playing(self._engine.playing)
        self.set_speed(self._engine.speed)

    def _write_clock(self) -> None:
        self.clock.setText(
            f"{format_clock(self._position)} / {format_clock(self._duration)}"
        )

    def _scrub_began(self) -> None:
        self._scrubbing = True

    def _scrub_moved(self, value: int) -> None:
        self._position = int(value)
        self._write_clock()
        self._engine.seek(int(value))

    def _scrub_ended(self) -> None:
        self._scrubbing = False
        value = int(self.scrubber.value())
        # The release is the backstop for a press that moved the handle without a
        # `sliderMoved` - not a second seek to the place the drag already asked for.
        # `SyncEngine.seek` pauses every player, moves it and starts it again, so a
        # repeat at the same millisecond is a visible hitch and buys nothing.
        if value != self._position:
            self._position = value
            self._engine.seek(value)

    def toggle_mute(self) -> None:
        """Silence what is playing, or let it be heard again.

        Untouched by §1.2, and that is the point: this is the volume at nought, and
        the sound chip is which *file* is heard. A viewer who mutes still has an audio
        source and gets it back by pressing this again; one who chooses «Звук
        выключен» has asked for no source at all. See `_build_sound_chip`.
        """
        self._muted = not self._muted
        self._engine.set_muted(self._muted)
        self.volume_button.set_kind("volume_off" if self._muted else "volume")
        self._write_volume_tooltip()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        """The wheel is the volume, anywhere on the stage.

        `Scrubber` hands the wheel back up on purpose - a slider that answers it moves
        its own handle without asking anyone to seek - so a scroll over the timeline
        arrives here like every other.
        """
        steps = event.angleDelta().y() / 120.0
        if not steps:
            event.ignore()
            return
        self._volume = max(0.0, min(1.0, self._volume + steps * 0.05))
        self._engine.set_volume(self._volume)
        self._write_volume_tooltip()
        event.accept()

    # -- the two states of §6 ----------------------------------------------

    def set_busy(self, busy: bool) -> None:
        """Dim every pane while a seek is in flight, and clear §6's ended badges.

        Every pane, including the one `focus` has hidden: a pane that was dark while
        hidden and lit when it came back would show the old moment for the length of a
        layout switch, which is the exact thing the state exists to prevent.

        **This is the only place the ended badge is cleared**, which is the sentence
        `PlayerWindows._seeking_changed` carries and the reason it is the same slot
        doing the same two jobs. `SyncEngine.seek` clears its own per-stream ended
        flags, and `stream_ended` carries only an index - there is no signal that ever
        says a stream has *un*-ended. So without this a viewer who scrubs back from
        the end of the longer stream keeps «Экран 2 — запись закончилась» over a
        picture that is playing again, for the rest of the session. A swap used to
        repair it by accident, because `_apply_streams` asks the engine again, which
        is why it read as working.
        """
        busy = bool(busy)
        for pane in self._panes():
            pane.set_busy(busy)
            if busy:
                pane.set_ended(False)
        # §2.1's badge is not §6's and must survive a seek: a stream whose content
        # starts after the timeline's zero is still waiting at the other end of the
        # scrub, and the engine says `stream_waiting` only when it *changes*.
        self._refresh_waiting()

    def _stream_ended(self, index: int) -> None:
        """One screen ran out while the other carries on - §6's badge."""
        for pane in self._panes_for(int(index)):
            pane.set_ended(True)

    # -- full screen -------------------------------------------------------

    def toggle_full_screen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self.full_button.set_on(self.isFullScreen())
        self.full_button.setToolTip(self._full_tooltip())

    # -- the keyboard (§7) -------------------------------------------------

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if not self.handle_key(event):
            super().keyPressEvent(event)

    def handle_key(self, event: QtGui.QKeyEvent) -> bool:
        """§7's table. True when the key was used.

        **The letters are matched on the text, not only on the key code.** `Key_S` is
        what a Latin layout reports; on ЙЦУКЕН the same physical key reports `ы`, and a
        shortcut that works only while the viewer is typing English is a shortcut that
        stops working in the middle of a sentence. The digits and the arrows are the
        same code on both layouts and need no such help.

        **What the two halves of the player share, and what they do not.**
        `PlayerWindows.handle_key` now answers `S` and `L` as well, with the same
        spellings and the same ЙЦУКЕН pairs, so every key §6 of
        `docs/PLAYER_LAYOUTS.md` writes for the whole player means one thing wherever
        the focus is. Two keys are this window's alone and are named on the controls
        they move rather than left to be discovered: `1` and `2`, which §6 itself
        marks "(stage layouts)" and which the stream switch's tooltip carries, and
        `F11`, which is not in §6's table at all - the separate-windows half has no
        main stream to choose and no bordered window to go full screen out of.
        """
        key = event.key()
        letter = event.text().lower()
        # §2.2's keyboard is read first, because it is the plain arrows with a
        # modifier on them: Shift+Left is not a skip of 5 s that happens to have Shift
        # held, and reading it second would make the two shortcuts fight.
        if self._sync_key(event, key):
            return True
        if key == QtCore.Qt.Key.Key_Space:
            self._engine.toggle()
        elif key == QtCore.Qt.Key.Key_Left:
            self._engine.skip(-JUMP_MS)
        elif key == QtCore.Qt.Key.Key_Right:
            self._engine.skip(JUMP_MS)
        elif key in (QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Less):
            self._engine.step(-1)
        elif key in (QtCore.Qt.Key.Key_Period, QtCore.Qt.Key.Key_Greater):
            self._engine.step(1)
        elif key == QtCore.Qt.Key.Key_Home:
            self._engine.seek(0)
        elif key == QtCore.Qt.Key.Key_End:
            self._engine.seek(self._engine.duration)
        elif key == QtCore.Qt.Key.Key_1:
            self.set_main_stream(0)
        elif key == QtCore.Qt.Key.Key_2:
            self.set_main_stream(1)
        elif key == QtCore.Qt.Key.Key_F11:
            self.toggle_full_screen()
        elif letter in ("s", "ы"):
            self.swap()
        elif letter in ("l", "д"):
            self.cycle_layout()
        elif key == QtCore.Qt.Key.Key_Escape:
            # Esc leaves full screen first and closes the player only from a window
            # that is already ordinary. §7 gives Esc one job, but a viewer in full
            # screen with no title bar has no other way out, and closing the whole
            # player instead of the full-screen state would lose their place.
            if self.isFullScreen():
                self.toggle_full_screen()
            else:
                self.close()
        else:
            return False
        event.accept()
        return True

    def _sync_key(self, event: QtGui.QKeyEvent, key: int) -> bool:
        """§2.2's `Shift+←/→` and `Alt+←/→`. True when the key was one of them.

        Answers False - and leaves the key to mean what it always did - whenever
        there is nothing to slide: one stream, three of them, or an engine with no
        `set_offset`. The alternative was tried and is worse: swallowing the modified
        arrow made Shift+Right do *nothing* on a stage showing one video while the
        same key on the floating transport, on the same recording, still skipped a
        second, because `PlayerWindows._sync_key` falls through. One key with two
        meanings, chosen by which half of the player happens to have focus, is a
        worse outcome than a skip a viewer did not quite ask for - and the skip is
        what Shift+arrow did here before §2.2 existed. The two halves now answer
        this the same way, and the single video has one less key that does nothing.
        """
        if key not in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
            return False
        modifiers = event.modifiers()
        shift = bool(modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & QtCore.Qt.KeyboardModifier.AltModifier)
        if not (shift or alt):
            return False
        if not self._can_sync():
            # Left for the plain arrow below, which is the whole of the agreement
            # with `PlayerWindows._sync_key`. Nothing is reported: a modifier on a
            # recording that has no second stream is not a fault, and a line of
            # stderr per arrow press is how a log stops being read.
            return False
        way = -1 if key == QtCore.Qt.Key.Key_Left else 1
        if shift:
            self.nudge_offset(frames=way)
        else:
            self.nudge_offset(milliseconds=way * NUDGE_MS)
        event.accept()
        return True

    # -- §1: a file dropped on the window ----------------------------------

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        """Take a drag carrying local files, and nothing else.

        §1: "Drag and drop onto any player window." `window.accept_file_drag` is the
        same three lines `ScreenView` and `TransportBar` each answer this with, so a
        drag that is taken over one window is taken over all of them - and one that is
        refused shows the no-entry cursor rather than being accepted and then dropped.
        """
        accept_file_drag(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        """Qt asks again on every move; the answer is the one `dragEnterEvent` gave.

        Without this the drop is refused at the moment of release on some window
        managers, which reads as a gesture that works everywhere except where it
        matters.
        """
        accept_file_drag(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        """One file opens it; two open as a pair; more are the first two and a word."""
        paths = dropped_files(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        if len(paths) > 2:
            # Three cameras are not a case this window has: it shows two pictures.
            # Said rather than silently truncated, because a viewer who dropped three
            # files and got two would be left guessing which two.
            self.say(
                pick(
                    "stage.drop.two",
                    "Two videos at a time: opening {first} and {second}.",
                ).format(first=Path(paths[0]).name, second=Path(paths[1]).name)
            )
            paths = paths[:2]
        self.open_paths(paths)

    # -- showing, and where (§6's last paragraph) --------------------------

    def place_on_screen(
        self, areas: Sequence[QtCore.QRect] | None = None
    ) -> QtCore.QRect:
        """Clamp this window into one real screen's available area, and return it.

        `window.transport_spot` is the pattern and carries the account of the failure:
        a window placed from the bounding box of several screens lands in the empty
        corner between two monitors, with every control present and none of them
        reachable. So one screen is chosen - the one already showing most of the
        window, or the first when none does - and the window is fitted into that one
        with `layouts.clamp_into`, which is the module that owns "all of it inside",
        shrinking included.

        A stage nobody has sized or moved yet is opened at `OPENING_SHARE` of the host
        and centred on it. `WA_Resized` and `WA_Moved` are Qt's own record of whether
        anyone asked for a geometry, so "never placed" is read from the widget rather
        than from a flag of this module's that a host could forget to set: an unshown
        `QWidget` reports a default 640x480 at 0,0, and taking that for the viewer's
        remembered geometry would open every stage small in the top-left corner.

        `areas` is injectable so this can be checked against a desk this machine does
        not have; left out, it is every screen's `availableGeometry`.
        """
        where = list(areas) if areas is not None else screen_areas()
        if not where:
            _report("no screen reported an available area; the stage is left as it is.")
            return QtCore.QRect(self.geometry())

        sized = self.testAttribute(QtCore.Qt.WidgetAttribute.WA_Resized)
        moved = self.testAttribute(QtCore.Qt.WidgetAttribute.WA_Moved)
        wanted = QtCore.QRect(self.geometry())

        host = where[0]
        if moved:
            best = -1
            for area in where:
                overlap = area.intersected(wanted)
                covered = overlap.width() * overlap.height()
                if covered > best:
                    best, host = covered, area

        if not sized:
            wanted.setSize(
                QtCore.QSize(
                    max(MIN_STAGE_WIDTH, int(host.width() * OPENING_SHARE)),
                    max(MIN_STAGE_HEIGHT, int(host.height() * OPENING_SHARE)),
                )
            )
        if not moved:
            wanted.moveCenter(host.center())

        room = host.adjusted(
            ARRANGE_MARGIN, ARRANGE_MARGIN, -ARRANGE_MARGIN, -ARRANGE_MARGIN
        )
        # A screen too small for the margin would otherwise give an empty rectangle and
        # a window of no size; being on screen beats the gap.
        if room.width() < MIN_STAGE_WIDTH or room.height() < MIN_STAGE_HEIGHT:
            room = QtCore.QRect(host)
        box = layouts.clamp_into(wanted, room)
        self.setGeometry(box)
        return box

    def setVisible(self, visible: bool) -> None:
        """Clamp into a real screen on the way up, whatever asked for it.

        The clamp lived in a `show()` override, which is a door with a wall beside
        it: `QWidget::show` is not virtual, so a host calling `setVisible(True)`, or
        Qt showing this window from inside some other call, walked straight past the
        override and the window appeared wherever its remembered geometry put it -
        which on an unplugged monitor is nowhere. `setVisible` **is** virtual, and
        every path up goes through it.

        Two states are left alone. A window already visible has been placed, and
        re-placing it would undo a move the viewer made; a window going to full
        screen has had its state set before this is called, and clamping it into the
        available area would cut the taskbar's strip out of the middle of the picture.
        """
        if visible and not self.isVisible() and not self.isFullScreen():
            self.place_on_screen()
        super().setVisible(visible)

    def show(self) -> None:
        """Show the window and give it the keyboard. `setVisible` does the placing."""
        super().show()
        self.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    # -- §6: a monitor unplugged while watching ----------------------------

    def _watch_the_displays(self) -> None:
        """Listen for a monitor being unplugged, as `PlayerWindows` already does.

        §6: "On `screenRemoved`, any window on the lost screen is moved to the primary
        one and a notice says so. No window may ever end up where it cannot be
        reached." `place_on_screen` ran only on the way up, so after the stage was
        shown nothing ever re-checked where it was - and this is the window carrying
        the only transport those two streams have, so it is the worst one to lose.

        `screenRemoved` is the only one of Qt's three display signals that can strand
        a window: `screenAdded` gives more room and `primaryScreenChanged` moves
        nothing. It is connected on the application object rather than on a screen,
        because the screen it is about is the one that has gone.
        """
        application = QtGui.QGuiApplication.instance()
        if application is None or not hasattr(application, "screenRemoved"):
            _report("no application object to ask about the displays.")
            return
        application.screenRemoved.connect(self._screen_removed)

    def _screen_removed(self, screen: Any = None) -> None:
        """Qt says a monitor has gone. Make sure this window did not go with it."""
        name = getattr(screen, "name", None)
        _report(f"a screen was removed ({name() if callable(name) else name!r}).")
        self.rescue_window()

    def rescue_window(self, areas: Sequence[QtCore.QRect] | None = None) -> bool:
        """Move the stage onto a screen that is still there. True when it had to.

        False and silent is the ordinary case, and it has to be: a desk of two
        monitors where the *other* one was unplugged strands nothing, and a notice
        for that would be the player talking about itself. `window.rescue_into` does
        the arithmetic - the same function `PlayerWindows.rescue_windows` calls - so
        which screen, and shrunk to fit it, is decided in one place for both halves
        of the feature rather than in two that can drift.

        `areas` is injectable for `place_on_screen`'s reason: this machine has one
        monitor and nobody can unplug it, so the only way to exercise this is to hand
        it a desk it does not have.
        """
        where = list(areas) if areas is not None else screen_areas()
        if not where:
            _report("no screen reported an available area; the stage is left alone.")
            return False
        box = QtCore.QRect(self.geometry())
        rescued = rescue_into(box, where)
        if rescued == box:
            return False
        self.setGeometry(rescued)
        self.say(_("A monitor is gone — this window moved to one that is still there."))
        return True

    # -- closing -----------------------------------------------------------

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Say so exactly once, whatever closed it.

        The engine is **not** stopped here. The host owns it, the stage is one way of
        looking at it, and §6 forbids a change of layout restarting anything - which
        includes the change from a stage layout to `separate`.

        The engines this window **built** are a different matter: nothing else knows
        they exist, so nothing else will ever free them, and a `QMediaPlayer` that is
        merely stopped still holds its file open - on Windows that is a recording the
        viewer cannot move or delete until the process ends. `deleteLater` rather than
        a plain drop, for `open._free_clock_with`'s measured reason: the players have
        to outlive the call that is closing them.
        """
        self._poll.stop()
        self._notice_timer.stop()
        self._engine.listener = None
        for clock in list(self._built_clocks):
            if clock is self.engine:
                # The one still playing: released here, because nothing else will.
                # `_retire` has already done this to the others.
                self._engine.close()
                self._engine.release()
            delete = getattr(clock, "deleteLater", None)
            if callable(delete):
                delete()
        self._built_clocks.clear()
        if not self._emitted_closed:
            self._emitted_closed = True
            self.closed.emit()
        super().closeEvent(event)


# ------------------------------------------------------------------- helpers


def _held_split(fraction: float) -> float:
    """A splitter fraction held to §2.3's own 0.15..0.85.

    A value that is not a finite number falls back to half rather than being clamped:
    `min(max(nan, lo), hi)` is `nan`, because both comparisons against it are false,
    and `json.loads` accepts a bare `NaN` token - so a hand-edited settings file is
    enough to produce one, and it would walk straight into `int(round(total * nan))`.
    """
    try:
        value = float(fraction)
    except (TypeError, ValueError):
        _report(f"{fraction!r} is not a splitter fraction; using 0.5.")
        return 0.5
    if not math.isfinite(value):
        _report(f"{fraction!r} is not a usable splitter fraction; using 0.5.")
        return 0.5
    return min(max(value, SPLIT_MIN), SPLIT_MAX)


def _library_row(entry: Any) -> str:
    """One library row: what it is called, when it was made, and how big it is.

    `PlayerWindows._library_text`'s two keys, so the same recording reads the same in
    both halves of §1 - and so the Russian for a row lives in one place. The date is
    the first ten characters of the ISO stamp, which is the day: a menu row is read
    sideways, and the minutes are in the file dialog for anyone who needs them.
    """
    recorded = str(getattr(entry, "recorded", "") or "")[:10]
    megabytes = int(getattr(entry, "size_bytes", 0) or 0) / (1024 * 1024)
    screens = int(getattr(entry, "screens", 1) or 1)
    name = str(getattr(entry, "name", ""))
    if screens > 1:
        return pick(
            "windows.library.row.set",
            "{name} — {date}, {screens} screens, {size} MB",
        ).format(name=name, date=recorded, screens=screens, size=f"{megabytes:.0f}")
    return pick("windows.library.row", "{name} — {date}, {size} MB").format(
        name=name, date=recorded, size=f"{megabytes:.0f}"
    )


def _screen_count(count: int) -> str:
    """"3 screens" / «3 экрана», in the form the number actually takes.

    Russian has three forms for a counted noun and `i18n.plural` is the one place
    that knows the rule, including the 11-14 exception that catches every naive
    attempt. The constructor's two notices are reached with 1 and with anything from
    3 up, so a single «{count} экрана» would be wrong at both ends of their range.
    """
    return i18n.plural(
        count,
        pick("stage.screens.one", "{count} screen"),
        pick("stage.screens.few", "{count} screens"),
        pick("stage.screens.many", "{count} screens"),
    ).format(count=count)


def _stream_count(count: int) -> str:
    """"2 video streams" / «2 видеопотока», in the form the number actually takes.

    The *few* form is `translations/player.py`'s own `"{count} video streams"`, whose
    Russian is «{count} видеопотока». It is taken rather than spelled again because a
    key lives in exactly one block, and that block's wording is already right for the
    two streams a stage normally has; what this block adds is the two forms a stage
    with one stream, or a hypothetical five, needs and that key cannot carry.
    """
    return i18n.plural(
        count,
        pick("stage.streams.one", "{count} video stream"),
        _("{count} video streams"),
        pick("stage.streams.many", "{count} video streams"),
    ).format(count=count)


def _optional_signal(engine: Any, name: str) -> Any | None:
    """The engine's signal called `name`, or None when it publishes no such thing.

    The two §6 signals are resolved here rather than through `_Engine.signal`, whose
    table lives in `window.py` - a module this feature may not edit. An engine without
    them is not a fault: §6's own answer for a single-stream recording is that neither
    state exists for it.
    """
    found = getattr(engine, name, None)
    if found is None or not hasattr(found, "connect"):
        return None
    return found


def _ask(engine: Any, name: str, default: Any, *args: Any) -> Any:
    """Call `engine.name(*args)` if it has one, else return `default`.

    The same tolerance `engine._call` has, and for the same reason: the stage must run
    against the real `SyncEngine`, against a host's own wrapper and against the fake in
    the tests, and a method that is not there is not a failure path - the state it
    would have reported simply does not exist on that engine.
    """
    method = getattr(engine, name, None)
    if not callable(method):
        return default
    try:
        return method(*args)
    except (TypeError, ValueError, RuntimeError) as exc:
        _report(f"engine.{name}{args!r} raised {exc!r}; using {default!r}.")
        return default
