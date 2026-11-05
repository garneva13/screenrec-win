"""What the viewer sees: one borderless window per recorded screen, and one transport.

The feature this serves is the one the user asked for in his own words - two screens
watched the way they were used, "как запись разных звуковых дорожек", eyes moving from
one monitor to the other. So the windows are not two copies of a media player placed
anywhere; they are laid out the way the screens really sat, and they are deliberately
plain, because anything that reads as application chrome breaks the illusion that you
are looking at the recorded desk.

Three decisions worth stating, because each had an alternative:

**`QVideoWidget`, fed from the engine's sink, rather than painting frames here.**
The engine owns the `QMediaPlayer`s and their `QVideoSink`s - that is the whole point of
the split, since the engine is what keeps the files in step. A `ScreenView` therefore
does not take the player: it listens to the engine's sink and pushes each frame straight
into its own `QVideoWidget`'s sink. A `QVideoFrame` is implicitly shared, so this is a
handle copy and not a picture copy, and the widget keeps Qt's accelerated presentation
path. The alternative - `QVideoFrame.toImage()` into a `paintEvent` - converts every
frame on the CPU, which at 2560x1440x60 is not free, and it would also mean re-writing
the letterboxing Qt already does. Nothing here is a measurement: it is a choice made to
avoid a known cost, and the frame rate of the finished player is worth measuring once
there is one.

**Frameless, but moved and resized by hand.** A title bar on each view would put two
application windows side by side instead of two screens, so the window has no frame; the
cost is that moving and resizing become this module's job. `startSystemResize` is tried
first so the viewer gets the operating system's own edge snapping, and a manual resize
takes over when the platform refuses it (which is what the offscreen plugin does, and so
is what the tests exercise).

**The label fades.** A view has to say which screen it is, or a two-monitor recording is
a guessing game; a label that stays is in the picture forever. It fades out a couple of
seconds after the pointer stops moving and comes back the moment it moves again.

**Problems are said on the bar, not to a console there is none of.** The player runs
under `pythonw`, where `sys.stdout` is `None` and `print` returns silently, and from
inside the recorder, which has no console either. So anything the engine complains about
- a file this machine cannot decode, a screen that will not stay in step, a recording
whose sound would not open - appears as a strip under the transport's controls for a few
seconds (`Notice`), as well as going to `stderr` through `player.open._say`. A black
second window with no sentence anywhere is the one failure this player must not have.

Unlike the recorder's chrome, the transport is an ordinary window: it is **not** excluded
from screen capture (that is a recorder concern - there is nothing to keep out of a file
here) and it **may** take focus, because it has a keyboard interface and a window that
refuses focus cannot have one.

§5, and what it changed
-----------------------
`docs/PLAYER_LAYOUTS.md` §5 is this module grown up. Four decisions in it had an
alternative and each avoids a failure with a name:

**Every rectangle comes from `layouts`.** `tile_rects`, `best_pairing` and `clamp_into`
are pure functions with their own tests, and the drawn thumbnails in the arrange menu
are painted from `tile_rects` as well rather than from four hand-written shapes - a
menu picture that disagrees with where the windows land is a control that lies, and the
only way to make that impossible is for both to be the same arithmetic.

**Closing the second window hides it; closing the first stops everything.** Today any
view closed the set. That was one rule and it was easy to be sure of, which is why the
change is made carefully: "the main window means stop" has to stay true, so index 0
still closes everything and only the others hide. A hidden view is a window that is
still being fed frames, so playback carries on untouched, and the transport grows a
«Показать второй экран» entry - hiding is not closing (§4 says the same of the
thumbnail).

**Re-tiling never touches the engine.** Moving, hiding and re-arranging a window is
`setGeometry` and `show`, and nothing in this module calls `seek`, `play` or
`setSource` from any of those paths. §6 demands it and `tests/test_player_window.py`
reads the engine's position either side of every arrangement to prove it.

**A window is placed into one screen's `availableGeometry`, never into the union.**
`transport_spot` already carries the account of what happens otherwise: the bar landed
at 1620,2480 on an L-shaped desk, on no monitor at all. `host_screen` is that rule
pulled out so the arrange menu, the rescue after `screenRemoved` and the bar all use
the one implementation of it - and, since the review that found this module breaking
the rule it states, `arrange` as well: it scaled the recorded set into every screen's
area united and centred it there, which on the user's own desk left a frameless view
with a fifth of itself on any one monitor and nothing to drag it back by.
`preferred_area` answers the other half of §6's rule, "to the primary one":
`QGuiApplication.screens()[0]` is not documented to be the primary and was being used
as if it were.

**The arrangement in force is three states, not two.** `_tiling` is one of §5's four
schemes or `""` for the desktop one; `_one_per_monitor` is the third. They are kept
apart rather than folded into one field because `tiling_changed` promises `config` a
value it will accept and `""` is not one. `arrangement()` is the single reader, and
«Вернуть раскладку» and the menu's ticks both go through it - the entry used to
re-apply only the scheme, so after "one screen per monitor" it shrank both windows
onto one monitor, which is the one thing its name forbids.

`docs/PLAYER_SOURCES.md`, and what it changed
---------------------------------------------
The player was built for a set this recorder wrote. The user asked for two cameras
nothing paired, so this window layer grew four things, and each one is a control on the
floating bar because the bar is the only chrome this mode has:

**The Open menu** (§1) - a file dialog, the recordings folder as a list, «Добавить
второй экран…» and «Сохранить набор». The bar carries the button and `PlayerWindows`
builds the menu, exactly as the arrange button already works and for the same reason:
what may be in it depends on how many streams are open and what is in the output
folder, and neither is the bar's to know.

**Drag and drop** (§1) onto any view *or* onto the bar. Two files dropped together are
a pair, which is the two-camera case in one gesture; one file is one recording. The
window layer does not decide which - `open.open_recording` takes a list and already
knows - so every drop is forwarded whole.

**The audio source** (§1.2) - two cameras both recorded sound and one of them recorded
it better, so which stream is heard is a question with its own control. It is **not**
the swap: swapping the picture must not move the sound, and §5 of
`docs/PLAYER_LAYOUTS.md` is unchanged, so exactly one stream is ever heard.

**The Sync panel** (§2.2) - a second row under the controls, open only when there are
two streams, that slides one stream against the other a frame or 100 ms at a time. It
moves an **offset**, never the timeline: `tests/test_player_window.py` reads the
engine's position either side of every nudge, every typed value and every reset, as it
already does either side of every arrangement.

Two decisions in it are worth naming because each contradicts something that was true
yesterday:

* **Shift+arrow now moves the offset when there are two streams**, where it used to be
  a second spelling of skip. §2.2 of `docs/PLAYER_SOURCES.md` claims `Shift+←/→` for a
  frame of offset and `Alt+←/→` for 100 ms, and a key cannot mean two things in one
  window. On a **one-stream** recording there is no offset to move and Shift+arrow
  still skips, which is the behaviour that must not get worse.
* **Opening another recording replaces this player rather than growing a second one.**
  There is one clock (§5), and a set opened from inside this one is a new clock with
  new windows; this object builds it through `open.open_recording`, holds it while it
  lives, and only then closes itself. The host's reference is to *this* object, so the
  new player would be collected the moment the call returned if nothing here held it.

The engine's side of the contract
---------------------------------
`PlayerWindows` is given the engine and asks it for exactly this much. Names in
parentheses are accepted synonyms, resolved once at construction and reported if nothing
matches, so a mismatch is a line of output at start-up rather than an `AttributeError` in
the middle of a key press::

    engine.recording.layout_rects   (x, y, w, h) per screen, in desktop pixels
    engine.recording.titles         optional; else `screens`, else "Screen N"
    engine.sinks                    (video_sinks)   one QVideoSink per screen, in order
    engine.position_ms              (position)      int, milliseconds
    engine.duration_ms              (duration)      int, milliseconds
    engine.is_playing               (playing)       bool
    engine.is_muted                 (muted)         bool, optional
    engine.speed                    (rate)          float, optional
    engine.toggle_play()            (toggle)
    engine.step_frames(n)           (step)          n frames, signed
    engine.seek(ms)                 (set_position)  absolute
    engine.skip(ms)                 (nudge)         relative; falls back to seek
    engine.set_speed(x)
    engine.set_muted(on)            (set_mute)      optional
    engine.set_volume(v)            optional, 0..1
    engine.close()                  (close_all, stop)
    engine.release()                (dispose)   optional; if it is not there, the
                                                players parented to the engine are
                                                released one by one - see
                                                `_Engine.release`
    engine.position_changed(int)    (positionChanged)  optional; else polled
    engine.duration_changed(int)    (durationChanged)  optional
    engine.playing_changed(bool)    (playingChanged)   optional

`docs/PLAYER_SOURCES.md` adds six more, every one of them optional, so an engine
written before it opens exactly as it did - without a sync panel and without an audio
source control, rather than with controls that do nothing::

    engine.offsets()                list[int], milliseconds, one per stream
    engine.set_offset(i, ms)        §2.1; must not move the timeline position
    engine.offsets_changed()        no argument; the panel reads `offsets()` back
    engine.stream_is_waiting(i)     bool; this stream's content has not started yet
    engine.stream_waiting(i, bool)  the same, announced
    engine.audio_index              (audio_source)  which stream is heard, or -1
    engine.set_audio_source(i)      (set_audio_index)  §1.2; -1 for no sound
    engine.audio_changed(int)       (audio_source_changed)  optional
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from PySide6 import QtCore, QtGui, QtMultimedia, QtMultimediaWidgets, QtWidgets

from .. import i18n
from ..i18n import _, pick
from ..ui import surfaces, theme
from . import layouts

#: Clear space left round the arranged set, in pixels of the viewer's screen.
ARRANGE_MARGIN = 28
#: No view is placed smaller than this, however far the set has to be scaled down.
MIN_VIEW_WIDTH = 160
MIN_VIEW_HEIGHT = 100
#: How close to an edge the pointer has to be for a press to start a resize.
RESIZE_MARGIN = 7
#: A press that travels less than this is a click, not a drag. The bar's own number.
DRAG_SLOP = 2
#: The screen label stays for this long after the pointer stops, then fades.
LABEL_HOLD_MS = 2500
LABEL_FADE_MS = 30
LABEL_FADE_STEP = 0.07
#: Shift+Left / Shift+Right.
JUMP_MS = 1000
#: Only used when the engine has no position signal.
POLL_MS = 100
SPEEDS: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0)

TRANSPORT_HEIGHT = 52
#: The strip that appears under the controls when something has gone wrong, and how
#: long it stays. Long enough to read a sentence twice; short enough not to become
#: part of the furniture.
NOTICE_HEIGHT = 30
NOTICE_MS = 9000
#: §2.2's second row, shown only while the viewer has it open. The bar grows by this
#: much, upwards, so the controls stay under the pointer - the same rule the notice
#: strip follows and for the same reason.
SYNC_HEIGHT = 38
#: How wide the bar may get, and how narrow it may be. The ceiling is applied by
#: `PlayerWindows._place_transport`, which used to spell it as a bare 760 while this
#: constant sat at 900 and was read by nothing - two numbers for one decision, and the
#: one that was documented was the one that was wrong. The floor moves: a one-stream
#: recording has no audio-source chip and no sync button, so it keeps the narrow bar it
#: has always had (`TransportBar.set_sources_available`).
TRANSPORT_MAX_WIDTH = 760
TRANSPORT_MIN_WIDTH = 548
TRANSPORT_MIN_WIDTH_PAIR = 700
#: §2.2's coarse nudge. The fine one is a frame, which is read off the recording.
NUDGE_MS = 100
#: Whatever two cameras did, they did not start a day apart. The field is clamped so a
#: typed digit too many cannot push a stream past the end of the timeline for good -
#: and an hour is far more than any hand-held pair of clapperboards is ever out by.
OFFSET_LIMIT_MS = 3_600_000
#: How many recordings the Open menu lists. A menu is not the recordings folder; it is
#: the handful at the top of it, and «Открыть…» reaches the rest.
LIBRARY_ROWS = 12
#: Used for the frame nudge when the recording will not say what it was shot at.
FALLBACK_FRAMERATE = 30.0
#: How much of the bar §1.2's chip may take. A stream's name can be long and the
#: scrubber is what the rest of the row is for.
AUDIO_CHIP_MAX_WIDTH = 150
#: The scrubber pages by five seconds and steps by one, which is what a recording is
#: measured in. Qt's defaults are 10 and 1 - ten *milliseconds* of a 65-second file.
SCRUB_PAGE_MS = 5000
SCRUB_STEP_MS = 1000

#: How far a view's picture is dimmed while a seek is in flight (§6). The same number
#: as `panes.BUSY_DIM`, for the same state: one recording watched in separate windows
#: and the same recording watched on the stage must not dim to two different greys.
#: **`ScreenView.set_busy` / `set_ended` and `panes.VideoPane`'s pair must stay in
#: step** - they are two implementations of one contract, kept apart only because
#: `panes` imports *this* module and the dependency cannot run both ways.
BUSY_DIM = 140
#: Clear space between an overlay and a view's edge. `panes.PANE_INSET`'s twin.
VIEW_INSET = 10
#: The drawn scheme pictures in the arrange menu, in pixels.
TILE_THUMB_WIDTH = 36
TILE_THUMB_HEIGHT = 24
#: How much of a window has to be on a real screen for it to count as reachable after
#: a monitor has gone. Not "any overlap at all": a window with four pixels showing is
#: one nobody can grab by its body, and this module's windows have no title bar to
#: grab them by either.
RESCUE_MIN_VISIBLE = 0.25

#: What §7's `L` steps through in the separate-windows half: the desktop arrangement
#: this player opens with, written `""` as `PlayerWindows.arrangement()` writes it,
#: then §5's four schemes, then round again. One screen per monitor is deliberately
#: not in it - see `PlayerWindows.cycle_arrangement`.
ARRANGEMENT_CYCLE: tuple[str, ...] = ("",) + layouts.TILINGS


def _report(message: str) -> None:
    """Say something on a failure path, on a stream that is actually there.

    A bare `print` was not a report at all. The player ships as `ScreenRecPlayer.cmd`,
    which runs `pythonw.exe -m screenrec.player`, and under `pythonw` `sys.stdout` is
    `None` and CPython's `print` silently returns; opened from the recorder it is the
    same GUI process with no console either. `player.open._say` is the writer the rest
    of the player already uses - it falls back to `stderr` and survives a closed or
    detached stream - so this goes through it rather than growing a second one.

    It is imported inside the call because `player.open` imports *this* module (inside
    a function, to open a recording), and because this is a failure path and not a hot
    one; after the first call it is a dictionary lookup in `sys.modules`.

    This is the developer's half of a problem. Anything a **viewer** has to know is
    also put on the transport bar, where they are looking - see
    `PlayerWindows._show_notice`.
    """
    text = f"player.window: {message}"
    try:
        from .open import _say
    except Exception:  # a half-imported package: fall through to the stream below
        _say = None  # type: ignore[assignment]
    if _say is not None:
        _say(text)
        return
    stream = sys.stderr or sys.stdout
    if stream is None:
        return
    try:
        print(text, file=stream)
    except (OSError, ValueError):  # a closed or detached stream; nothing else to do
        pass


# --------------------------------------------------------------------- glyphs
#
# `ui.icons.KINDS` has no play, no frame-step, no volume and nothing for the arrange
# menu. `ui/icons.py` belongs to the finished recorder and is not this feature's to edit,
# and an unknown name makes `icons.draw` stroke a placeholder, so the player's seven
# glyphs are drawn here on the same 24x24 grid, with the same round caps and joins, so
# they sit beside the recorder's icons without looking borrowed.

_GRID = 24.0

PLAYER_GLYPHS: tuple[str, ...] = (
    "play",
    "pause",
    "step_back",
    "step_forward",
    "volume",
    "volume_off",
    "arrange",
    "sync",
)


def draw_glyph(
    painter: QtGui.QPainter,
    kind: str,
    rect: QtCore.QRectF | QtCore.QRect,
    colour: QtGui.QColor | str,
    weight: float = 1.9,
) -> bool:
    """Stroke one of `PLAYER_GLYPHS` inside `rect`. False for a name it does not know.

    Never raises for an unknown name, for the reason `icons.draw` does not: an exception
    escaping a live `QPainter` takes the interpreter down with it.
    """
    box = QtCore.QRectF(rect)
    if box.isEmpty() or kind not in PLAYER_GLYPHS:
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

        if kind == "play":
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            painter.drawPolygon(
                QtGui.QPolygonF(
                    [
                        QtCore.QPointF(8.0, 4.8),
                        QtCore.QPointF(19.0, 12.0),
                        QtCore.QPointF(8.0, 19.2),
                    ]
                )
            )
        elif kind == "pause":
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            painter.drawRoundedRect(QtCore.QRectF(7.5, 5.0, 3.4, 14.0), 1.4, 1.4)
            painter.drawRoundedRect(QtCore.QRectF(13.1, 5.0, 3.4, 14.0), 1.4, 1.4)
        elif kind in ("step_back", "step_forward"):
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            if kind == "step_back":
                wedge = [
                    QtCore.QPointF(17.0, 5.4),
                    QtCore.QPointF(17.0, 18.6),
                    QtCore.QPointF(8.2, 12.0),
                ]
                stop = QtCore.QRectF(5.6, 5.4, 2.4, 13.2)
            else:
                wedge = [
                    QtCore.QPointF(7.0, 5.4),
                    QtCore.QPointF(7.0, 18.6),
                    QtCore.QPointF(15.8, 12.0),
                ]
                stop = QtCore.QRectF(16.0, 5.4, 2.4, 13.2)
            painter.drawPolygon(QtGui.QPolygonF(wedge))
            painter.drawRoundedRect(stop, 1.1, 1.1)
        elif kind == "arrange":
            # Two panes side by side, outlined rather than filled: the button opens a
            # menu of arrangements, and a solid pair of blocks at 17 px reads as one
            # dark lozenge. The uneven split says "these are arrangements", which a
            # symmetrical pair would not.
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
            painter.drawRoundedRect(QtCore.QRectF(4.0, 6.0, 7.6, 12.0), 1.4, 1.4)
            painter.drawRoundedRect(QtCore.QRectF(13.0, 6.0, 7.0, 12.0), 1.4, 1.4)
        elif kind == "sync":
            # Two tracks, one ahead of the other, and the line the viewer is lining
            # them up on. Deliberately *not* two circular arrows: that is the refresh
            # icon this application already uses, and the sync panel reloads nothing.
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            painter.drawRoundedRect(QtCore.QRectF(3.0, 6.0, 12.0, 4.4), 1.6, 1.6)
            painter.drawRoundedRect(QtCore.QRectF(9.0, 13.6, 12.0, 4.4), 1.6, 1.6)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            marker = QtGui.QPen(ink, 1.2)
            marker.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            marker.setStyle(QtCore.Qt.PenStyle.DotLine)
            painter.setPen(marker)
            painter.drawLine(QtCore.QPointF(12.0, 3.4), QtCore.QPointF(12.0, 20.6))
        else:  # volume, volume_off - a speaker, with waves or with a cross
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            painter.drawPolygon(
                QtGui.QPolygonF(
                    [
                        QtCore.QPointF(4.0, 9.5),
                        QtCore.QPointF(7.5, 9.5),
                        QtCore.QPointF(11.5, 5.5),
                        QtCore.QPointF(11.5, 18.5),
                        QtCore.QPointF(7.5, 14.5),
                        QtCore.QPointF(4.0, 14.5),
                    ]
                )
            )
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
            if kind == "volume":
                painter.drawArc(QtCore.QRectF(11.0, 8.0, 5.0, 8.0), -70 * 16, 140 * 16)
                painter.drawArc(QtCore.QRectF(11.0, 5.2, 8.6, 13.6), -65 * 16, 130 * 16)
            else:
                painter.drawLine(QtCore.QPointF(15.0, 9.5), QtCore.QPointF(20.0, 14.5))
                painter.drawLine(QtCore.QPointF(20.0, 9.5), QtCore.QPointF(15.0, 14.5))
        return True
    finally:
        painter.restore()


def draw_tiling(
    painter: QtGui.QPainter, tiling: str, rect: QtCore.QRectF | QtCore.QRect
) -> bool:
    """Draw §5's scheme `tiling` as two blocks inside `rect`. False for an unknown id.

    **The picture is `layouts.tile_rects`, not a drawing of it.** The menu entry and
    the windows it places therefore cannot disagree: a thumbnail showing two equal
    halves beside an action that tiles two thirds and one third is a control that lies,
    and the only way to make that impossible is for both to be the same arithmetic. The
    cost is a `QRect` of integers for a 36x24 picture, where a tenth of a pixel is lost
    to rounding; the alternative is four hand-written shapes that drift the first time
    a scheme's proportions are changed.

    Never raises for a name it does not know, for `draw_glyph`'s reason: an exception
    escaping a live `QPainter` takes the interpreter down with it.
    """
    box = QtCore.QRectF(rect)
    if box.isEmpty() or tiling not in layouts.TILINGS:
        return False
    colours = theme.COLOURS
    # One pixel of air between the two blocks, taken off each. Without it the pair is
    # a single rectangle with a hairline in it at this size.
    inner = box.adjusted(0.5, 0.5, -0.5, -0.5)
    whole = QtCore.QRect(
        int(inner.x()), int(inner.y()), int(inner.width()), int(inner.height())
    )
    painter.save()
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtGui.QPen(QtGui.QColor(colours["line_strong"]), 1))
        for index, tile in enumerate(layouts.tile_rects(whole, tiling)):
            shrunk = QtCore.QRectF(tile).adjusted(0.5, 0.5, -0.5, -0.5)
            if shrunk.isEmpty():
                continue
            # The first block is the main screen, so it carries the accent: the menu
            # has to say which window stays where, not only how the area is split.
            painter.setBrush(
                QtGui.QColor(colours["accent"] if index == 0 else colours["hover"])
            )
            painter.drawRoundedRect(shrunk, 2.0, 2.0)
        return True
    finally:
        painter.restore()


def tiling_icon(tiling: str) -> QtGui.QIcon:
    """`draw_tiling` as a menu icon. An empty icon for a name this module does not know.

    A `QPixmap` and not a `QIcon` built from a painter at paint time, because a menu
    asks its actions for an icon while it is laying itself out and a painter opened
    inside that call would be a painter inside a paint event. The pixmap is filled with
    a transparent colour first: an uninitialised `QPixmap` holds whatever was in that
    memory, which on Windows is usually - not always - black.
    """
    pixmap = QtGui.QPixmap(TILE_THUMB_WIDTH, TILE_THUMB_HEIGHT)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    if tiling not in layouts.TILINGS:
        return QtGui.QIcon()
    painter = QtGui.QPainter(pixmap)
    try:
        draw_tiling(painter, tiling, QtCore.QRectF(pixmap.rect()))
    finally:
        painter.end()
    return QtGui.QIcon(pixmap)


class PlayerButton(surfaces.IconButton):
    """An `IconButton` that can also draw the player's own glyphs.

    `_paint` is written out rather than delegated because the base class resolves the
    glyph through `icons.draw` at the end of its own method, and there is no seam there
    to replace. The resting, hover and pressed colours below are therefore the same
    expressions as in `surfaces.IconButton._paint`, deliberately - if that changes, this
    has to change with it, which is why both are stated in terms of `theme.COLOURS` and
    neither hard-codes a value.
    """

    def _paint(self, painter: QtGui.QPainter) -> None:
        if self.kind not in PLAYER_GLYPHS:
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
        draw_glyph(
            painter,
            self.kind,
            QtCore.QRectF(inset, inset, surfaces.ICON, surfaces.ICON),
            ink,
        )


class Scrubber(QtWidgets.QSlider):
    """The position slider: the whole bar is the target, and the wheel is not.

    **The wheel is handed back to the bar.** A `QSlider` answers the wheel by moving its
    handle, and it does that through `valueChanged` rather than `sliderMoved` - so a
    scroll over the scrubber would walk the handle along without ever asking the engine
    to seek, which is a control that lies. The wheel belongs to the volume on this bar,
    so it is passed up instead.

    **A press anywhere jumps there.** Qt's own answer to a press on the groove is a
    *page step*, and a page step is the same lie in slower motion: the handle moves by
    `pageStep` milliseconds, nothing asks the engine to seek, and the next poll snaps the
    handle back. Measured here with the real style: a click at 75% of a 336 px bar over a
    65-second recording moved the playhead ten milliseconds and issued no seek, because
    `SH_Slider_AbsoluteSetButtons` is the middle button and `sliderPressed`,
    `sliderMoved` and `sliderReleased` are only emitted while the handle itself is held.
    On that bar the handle is twelve pixels wide, so the control a viewer aims at is 3.5%
    of the thing they can see.

    So a press outside the handle is taken over here: the slider is marked down (which
    emits `sliderPressed`), moved to the pressed position (which, being down, emits
    `sliderMoved`), and followed until the button comes up. A press **inside** the handle
    is left to Qt, because its own drag keeps the grab offset - taking that over too
    would make a handle grabbed by its edge jump by half a handle, which on a 65-second
    recording is a second of video nobody asked to skip.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(QtCore.Qt.Orientation.Horizontal, parent)
        #: True while this class, rather than QSlider, is driving the drag.
        self._absolute = False

    # -- geometry ----------------------------------------------------------

    def _option(self) -> QtWidgets.QStyleOptionSlider:
        option = QtWidgets.QStyleOptionSlider()
        initialise = getattr(self, "initStyleOption", None)
        if callable(initialise):
            initialise(option)
            return option
        # PySide does not always expose the protected initialiser; the handful of
        # fields `subControlRect` reads are filled in by hand rather than guessing at
        # the groove with raw widget coordinates.
        option.initFrom(self)
        option.orientation = self.orientation()
        option.minimum = self.minimum()
        option.maximum = self.maximum()
        option.sliderPosition = self.sliderPosition()
        option.sliderValue = self.value()
        option.singleStep = self.singleStep()
        option.pageStep = self.pageStep()
        option.upsideDown = self.invertedAppearance()
        return option

    def _handle_and_groove(self) -> tuple[QtCore.QRect, QtCore.QRect]:
        option = self._option()
        style = self.style()
        handle = style.subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_Slider,
            option,
            QtWidgets.QStyle.SubControl.SC_SliderHandle,
            self,
        )
        groove = style.subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_Slider,
            option,
            QtWidgets.QStyle.SubControl.SC_SliderGroove,
            self,
        )
        return handle, groove

    def value_at(self, x: int) -> int:
        """The value a press at `x` pixels across this widget means."""
        handle, groove = self._handle_and_groove()
        span = max(1, groove.width() - handle.width())
        travelled = int(x) - groove.x() - handle.width() // 2
        return int(
            QtWidgets.QStyle.sliderValueFromPosition(
                self.minimum(),
                self.maximum(),
                travelled,
                span,
                self.invertedAppearance(),
            )
        )

    # -- the mouse ---------------------------------------------------------

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if (
            event.button() != QtCore.Qt.MouseButton.LeftButton
            or self.maximum() <= self.minimum()
        ):
            super().mousePressEvent(event)
            return
        where = event.position().toPoint()
        handle, _groove = self._handle_and_groove()
        if handle.contains(where):
            self._absolute = False
            super().mousePressEvent(event)
            return
        self._absolute = True
        self.setSliderDown(True)  # emits sliderPressed: the bar stops following the clock
        self.setSliderPosition(self.value_at(where.x()))  # pressed, so emits sliderMoved
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._absolute:
            super().mouseMoveEvent(event)
            return
        self.setSliderPosition(self.value_at(event.position().toPoint().x()))
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._absolute:
            super().mouseReleaseEvent(event)
            return
        self._absolute = False
        self.setSliderDown(False)  # emits sliderReleased: the final seek is sent there
        event.accept()


class SpeedChip(QtWidgets.QAbstractButton):
    """The playback speed, as a value you click to cycle: 0.5x, 1x, 1.5x, 2x.

    Four options and a current value would be a `Segmented` anywhere else in this
    application, but a transport bar is already a long row of controls and a segmented
    control with four cells is wider than the scrubber deserves to be short. A chip that
    cycles costs at most three clicks to reach any speed and says what it is on.
    """

    changed = QtCore.Signal(float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 1.0
        self.setFixedSize(46, surfaces.BUTTON)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setToolTip(_("Playback speed - click to cycle"))
        self.clicked.connect(self._cycle)
        i18n.notifier.changed.connect(self.retranslate)

    def retranslate(self) -> None:
        """Re-read the one sentence this chip shows. See `i18n.notifier`.

        The value painted on the chip - `0.5x`, `1x` - is deliberately not here: it is
        a number and a multiplication sign, which read the same in both languages.
        """
        self.setToolTip(_("Playback speed - click to cycle"))

    def value(self) -> float:
        return self._value

    def set_value(self, speed: float) -> None:
        """Show `speed` without emitting. The nearest known speed is used."""
        nearest = min(SPEEDS, key=lambda option: abs(option - float(speed)))
        if nearest != self._value:
            self._value = nearest
            self.update()

    def _cycle(self) -> None:
        index = SPEEDS.index(self._value) if self._value in SPEEDS else 1
        self._value = SPEEDS[(index + 1) % len(SPEEDS)]
        self.update()
        self.changed.emit(self._value)

    def text_value(self) -> str:
        whole = f"{self._value:g}"
        return f"{whole}x"

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            colours = theme.COLOURS
            hovered = self.underMouse() and self.isEnabled()
            box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(colours["line_strong" if hovered else "line"]), 1
                )
            )
            painter.setBrush(
                QtGui.QColor(colours["hover"] if hovered else colours["bg"])
            )
            painter.drawRoundedRect(box, theme.RADIUS["chip"], theme.RADIUS["chip"])
            font = QtGui.QFont(self.font())
            font.setPointSizeF(9.0)
            painter.setFont(font)
            painter.setPen(
                QtGui.QColor(
                    colours["text"]
                    if (hovered or self._value != 1.0)
                    else colours["text_soft"]
                )
            )
            painter.drawText(
                QtCore.QRectF(0, 0, self.width(), self.height()),
                int(QtCore.Qt.AlignmentFlag.AlignCenter),
                self.text_value(),
            )
        finally:
            painter.end()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class ScreenLabel(QtWidgets.QWidget):
    """Which screen this is, in the corner of a view, able to fade out.

    It is a child widget rather than something the view paints, because the picture is a
    `QVideoWidget` child and would be drawn over anything the parent painted underneath
    it. It takes no mouse events, so it can sit over the picture without being in the way
    of a click or a drag.
    """

    def __init__(self, text: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._opacity = 1.0
        self.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._resize_to_text()

    # -- content

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self._resize_to_text()
        self.update()

    def opacity(self) -> float:
        return self._opacity

    def set_opacity(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._opacity) < 0.001:
            return
        self._opacity = value
        # Fully faded means gone, not an invisible widget still being composited.
        self.setVisible(value > 0.01)
        self.update()

    def _font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(9.5)
        return font

    def _resize_to_text(self) -> None:
        metrics = QtGui.QFontMetrics(self._font())
        self.resize(metrics.horizontalAdvance(self._text) + 22, 26)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            painter.setOpacity(self._opacity)
            colours = theme.COLOURS
            box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["line"]), 1))
            painter.setBrush(QtGui.QColor(colours["panel"]))
            painter.drawRoundedRect(box, theme.RADIUS["small"], theme.RADIUS["small"])
            painter.setFont(self._font())
            painter.setPen(QtGui.QColor(colours["text_soft"]))
            painter.drawText(
                QtCore.QRectF(0, 0, self.width(), self.height()),
                int(QtCore.Qt.AlignmentFlag.AlignCenter),
                self._text,
            )
        finally:
            painter.end()


class SeekVeil(QtWidgets.QWidget):
    """§6's loading state over one view: the picture dimmed, with one quiet word on it.

    A child widget rather than something the view paints, for `ScreenLabel`'s reason -
    the picture is a `QVideoWidget` child and is drawn over anything the parent painted
    underneath it. It takes no mouse events, so a seek in flight never swallows the
    click that plays or pauses.

    **It does not animate.** A spinner is a `QTimer` firing ten times a second for the
    200 ms §6 is talking about, and a timer created once and left running is the version
    of that which survives into the release.

    `panes._BusyVeil` is the same widget for the stage layouts. The two are not one
    class because `panes` imports this module and the dependency cannot run both ways;
    they must stay in step, and `BUSY_DIM` being published here and read there is the
    one number that cannot drift between them.
    """

    def __init__(self, text: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = str(text)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            veil = QtGui.QColor(0, 0, 0)
            veil.setAlpha(BUSY_DIM)
            painter.fillRect(self.rect(), veil)

            colours = theme.COLOURS
            font = QtGui.QFont(self.font())
            font.setPointSizeF(9.5)
            painter.setFont(font)
            metrics = QtGui.QFontMetricsF(font)
            width = metrics.horizontalAdvance(self._text) + 28.0
            chip = QtCore.QRectF(
                (self.width() - width) / 2.0,
                (self.height() - 28.0) / 2.0,
                width,
                28.0,
            )
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["line"]), 1))
            painter.setBrush(QtGui.QColor(colours["panel"]))
            painter.drawRoundedRect(chip, theme.RADIUS["small"], theme.RADIUS["small"])
            painter.setPen(QtGui.QColor(colours["text_soft"]))
            painter.drawText(
                chip, int(QtCore.Qt.AlignmentFlag.AlignCenter), self._text
            )
        finally:
            painter.end()


class ScreenView(QtWidgets.QWidget):
    """One screen's picture, in its own frameless window.

    Click anywhere to play or pause. Drag the body to move it, drag an edge to resize it.

    **Closing it no longer closes the set by itself.** It still says so with `closed`,
    and `PlayerWindows` decides: §5 gives the first window the meaning "stop" and makes
    closing any other one a way to put that screen away while the rest carries on. So
    this class reports the gesture and owns none of the policy, which is the reason
    `closed` carries no "and therefore" in its name.
    """

    #: A click that was a click and not a drag.
    clicked = QtCore.Signal()
    #: §1: files dropped on this view. A list, because two at once are a pair. The
    #: view opens nothing itself - what a drop means is `PlayerWindows`' policy, the
    #: way closing a window is - so this carries the gesture and no "and therefore".
    files_dropped = QtCore.Signal(list)
    #: The window was closed, by its own close box, by Alt+F4, or by the set shutting
    #: down. Added to the brief's signature because something has to carry "close the
    #: rest", and an event filter on a window the caller does not own is worse.
    closed = QtCore.Signal()

    def __init__(
        self,
        title: str,
        sink: QtMultimedia.QVideoSink | None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._source_sink = sink
        self._emitted_closed = False
        self._busy = False
        self._ended = False
        #: The badge's words when the host chose its own, kept rather than only
        #: painted: `set_label` rewords a *default* badge, and a sentence the host
        #: wrote must not be quietly replaced by this module's.
        self._ended_custom = ""
        #: §2.1's other end of the same story: this stream's content has not started
        #: yet, because the viewer has wound it forward past the timeline's zero.
        self._waiting = False
        self._waiting_custom = ""
        #: Why this view will show nothing, as a *kind* and not as a sentence: "",
        #: "no_sink" or "no_signal". Kept this way because a finished sentence cannot
        #: be translated back into the other language, and `retranslate` has to be
        #: able to produce the same complaint again after a switch.
        #: `panes.VideoPane` holds the same two in the same way.
        self._problem = ""
        #: Why this view will show nothing, if that is the case. Read by
        #: `PlayerWindows` once the transport exists, because a view is built before
        #: there is anywhere to put a sentence, and "this window is black and nobody
        #: said why" is the complaint this whole round of fixes is about.
        self.setup_problem = ""

        self.setWindowTitle(_("ScreenRec - {name}").format(name=title))
        self.setWindowFlags(
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.FramelessWindowHint
        )
        self.setMinimumSize(MIN_VIEW_WIDTH, MIN_VIEW_HEIGHT)
        self.setMouseTracking(True)
        # §1: "drag and drop onto any player window", and this is the window the
        # pointer is over while the viewer is looking at the picture.
        self.setAcceptDrops(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(True)
        ground = self.palette()
        ground.setColor(
            QtGui.QPalette.ColorRole.Window, QtGui.QColor("#000000")
        )
        self.setPalette(ground)

        self.video = QtMultimediaWidgets.QVideoWidget(self)
        self.video.setAspectRatioMode(QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        # The picture must not eat the press that moves or resizes the window.
        self.video.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.video)

        self.label = ScreenLabel(title, self)
        self.label.move(14, 12)
        self.label.raise_()
        # Free-positioned children and deliberately not in the layout: a layout would
        # give the picture a share of the window instead of all of it.
        self.veil = SeekVeil(_("Seeking…"), self)
        self.veil.hide()
        self.badge = ScreenLabel("", self)
        self.badge.hide()

        self._hold = QtCore.QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(LABEL_HOLD_MS)
        self._hold.timeout.connect(self._start_fade)
        self._fade = QtCore.QTimer(self)
        self._fade.setInterval(LABEL_FADE_MS)
        self._fade.timeout.connect(self._fade_step)

        self._drag_offset: QtCore.QPoint | None = None
        self._press_global: QtCore.QPoint | None = None
        self._travelled = False
        self._resize_edges: QtCore.Qt.Edge | None = None
        self._resize_geometry: QtCore.QRect | None = None

        self._connect_sink(sink)
        self.wake_label()
        self._place_overlays()
        i18n.notifier.changed.connect(self.retranslate)

    # -- text --------------------------------------------------------------

    def retranslate(self) -> None:
        """Rebuild the window title, the seek veil and the ended badge.

        Only the frame is translated. `_title` is the screen's label, handed in by the
        caller, and rewriting it here would replace the viewer's own word for that
        screen. The label on the picture is left alone for the same reason, and
        showing it again would make a language change flash it back onto a window the
        viewer has been watching in silence.

        The badge is re-read only when this module wrote it: a sentence the host chose
        cannot be translated back into the language it was not written in, which is the
        rule `panes.VideoPane.retranslate` follows for the same badge.

        `setup_problem` is rebuilt as well, but the host read it when the view was
        built and put it on the transport's notice strip; refreshing that strip is
        the host's business, not this one's.
        """
        self.setWindowTitle(_("ScreenRec - {name}").format(name=self._title))
        self.veil.set_text(_("Seeking…"))
        self._write_badge()
        self.setup_problem = self._problem_text()
        self._place_overlays()

    # -- the two states of §6 ---------------------------------------------
    #
    # `panes.VideoPane` publishes the same two, with the same names and the same
    # meanings, because one engine drives both and `PlayerWindows` and the stage hand
    # them the same signals. **The two must stay in step**: a change to what `set_busy`
    # or `set_ended` means here is a change to that module as well.

    def is_busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        """Dim the picture while a seek is in flight, and say so quietly.

        §6: "Showing frames from two different moments as if they were synchronised is
        worse than showing nothing for 200 ms." The veil is what "nothing" looks like
        without throwing the picture away.
        """
        busy = bool(busy)
        if busy == self._busy:
            return
        self._busy = busy
        self.veil.setVisible(busy)
        self._place_overlays()

    def has_ended(self) -> bool:
        return self._ended

    def set_ended(self, ended: bool, text: str = "") -> None:
        """Hold the last frame with a small badge - §6, the shorter of two streams.

        Nothing here stops anything: the stream has already run out, the engine knows
        it, and this window's job is to say so rather than to go black, because a black
        rectangle beside a playing one reads as a crash.
        """
        self._ended = bool(ended)
        self._ended_custom = " ".join(str(text).split())
        self._write_badge()

    def is_waiting(self) -> bool:
        return self._waiting

    def set_waiting(self, waiting: bool, text: str = "") -> None:
        """§2.1: this stream's content has not started yet. Black, with a badge.

        **Black, and not the first frame.** The ended badge holds the last frame
        because the stream really did show it; a stream the timeline has not reached
        has shown nothing, and §2.1 names the alternative by its failure - "showing a
        frozen first frame as if it were live is the thing to avoid". So the picture is
        cleared once on the way in and frames are dropped for as long as this lasts,
        rather than letting the player's own frame 0 sit there looking synchronised.

        An empty `QVideoFrame` is what "clear it" is in Qt: a widget whose sink is
        never written to keeps whatever it last showed, and `QVideoWidget` has no
        `clear()`.
        """
        waiting = bool(waiting)
        self._waiting_custom = " ".join(str(text).split())
        if waiting != self._waiting:
            self._waiting = waiting
            if waiting:
                self._blank()
        self._write_badge()

    def _blank(self) -> None:
        """Throw the picture away, so the window is black and not a held frame."""
        target = self.video.videoSink()
        if target is None:  # torn down between a signal and this call
            return
        try:
            target.setVideoFrame(QtMultimedia.QVideoFrame())
        except (RuntimeError, TypeError) as exc:
            # A sink whose C++ side has gone, or a Qt build that will not take an
            # empty frame. The badge below still says what is happening, which is the
            # half of this the viewer actually reads.
            _report(f"{self._title}: the picture could not be cleared ({exc}).")

    def _write_badge(self) -> None:
        """One badge, two states, one place that decides which of them it shows.

        Waiting wins over ended. They can both be on - a stream wound forward by two
        seconds that has also run out at the far end - and the one the viewer needs is
        the one that explains the black rectangle in front of them.
        """
        text = self._badge_text()
        if not text:
            self.badge.hide()
            return
        self.badge.set_text(text)
        self.badge.show()
        self._place_overlays()

    def _badge_text(self) -> str:
        if self._waiting:
            return self._waiting_custom or self._waiting_text()
        if self._ended:
            return self._ended_custom or self._ended_text()
        return ""

    def _ended_text(self) -> str:
        return _("{name} — the recording ends here").format(name=self._title)

    def _waiting_text(self) -> str:
        return pick(
            "windows.waiting", "{name} — this recording starts later"
        ).format(name=self._title)

    def _place_overlays(self) -> None:
        """Put the label, the badge and the veil where they belong, in that order.

        The stacking is done here and not with a `raise_()` beside each state change,
        because two states can be on at once - a seek made while the shorter stream is
        already finished - and the one that was switched on last would otherwise decide
        which is on top.
        """
        self.label.move(14, 12)
        self.label.raise_()
        self.badge.move(
            max(VIEW_INSET, (self.width() - self.badge.width()) // 2),
            max(VIEW_INSET, self.height() - self.badge.height() - VIEW_INSET),
        )
        if self.badge.isVisible():
            self.badge.raise_()
        self.veil.setGeometry(self.rect())
        if self._busy:
            self.veil.raise_()

    # -- the picture -------------------------------------------------------

    def _connect_sink(self, sink: QtMultimedia.QVideoSink | None) -> None:
        """Bridge the engine's sink into this window's own.

        The engine owns the player and the sink it renders to; this window owns a
        `QVideoWidget`. Handing each frame across keeps that split and costs a handle
        copy, because a `QVideoFrame` is implicitly shared.
        """
        if sink is None:
            self._set_problem("no_sink")
            _report(f"{self._title}: no video sink was given; the view stays black.")
            return
        changed = getattr(sink, "videoFrameChanged", None)
        if changed is None:
            self._set_problem("no_signal")
            _report(
                f"{self._title}: {type(sink).__name__} has no videoFrameChanged "
                "signal; the view stays black."
            )
            return
        changed.connect(self._on_frame)

    def _set_problem(self, kind: str) -> None:
        """Record *why* this view will stay black, and write the sentence for it."""
        self._problem = kind
        self.setup_problem = self._problem_text()

    def _problem_text(self) -> str:
        """`_problem` as the sentence the host puts on the notice strip.

        Built here rather than stored, so a language switch can ask for it again.
        `panes.VideoPane._problem_text` says the same two things about a pane; the
        wording differs by one word because a window is not a pane, and a sentence
        that calls a separate window "this pane" is one the viewer cannot act on.
        The signal's name is left untranslated inside it on purpose: it is the one
        word in the sentence that a search of the source has to find.
        """
        if self._problem == "no_sink":
            return _(
                "{name}: no source of frames — this window will stay black."
            ).format(name=self._title)
        if self._problem == "no_signal":
            return _(
                "{name}: the source of frames has no videoFrameChanged signal "
                "— this window will stay black."
            ).format(name=self._title)
        return ""

    def _on_frame(self, frame: QtMultimedia.QVideoFrame) -> None:
        # Qt pushes an **invalid** frame when a file runs out - measured on
        # 2026-11-05 against this project's own recording: the last thing each sink was
        # given was `isValid() == False`. Forwarding it is the same call `_blank()`
        # makes on purpose, so the window went black at the end of every recording
        # while §5 of `docs/PLAYER_LAYOUTS.md` promises it holds its last frame.
        # Dropping it keeps that frame; a window that is *meant* to be black is blanked
        # deliberately elsewhere, and that path is untouched.
        if not _worth_showing(frame):
            return
        if self._waiting:
            # §2.1. The engine holds a waiting stream at its own position 0, so what
            # arrives here is frame 0 over and over; forwarding it would paint the
            # frozen first frame this state exists to avoid.
            return
        target = self.video.videoSink()
        if target is None:  # the widget was torn down between frames
            return
        target.setVideoFrame(frame)

    # -- §1: a file dropped on this window ---------------------------------

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        accept_file_drag(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        accept_file_drag(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        """Hand the paths up. The bar has the same three; see `accept_file_drag`."""
        files = dropped_files(event.mimeData())
        if not files:
            event.ignore()
            return
        event.acceptProposedAction()
        self.files_dropped.emit(files)

    # -- the label ---------------------------------------------------------

    def title(self) -> str:
        """What this view is called now - the same word the label is showing.

        `PlayerWindows.retranslate` asks, so that it can leave a view alone when its
        name has not changed. `panes.VideoPane` publishes the same accessor.
        """
        return self._title

    def set_label(self, text: str) -> None:
        """Say which screen this is. Shows the label again and restarts its timer."""
        self._title = text
        self.setWindowTitle(_("ScreenRec - {name}").format(name=text))
        self.label.set_text(text)
        self._write_badge()
        self.wake_label()
        self._place_overlays()

    def wake_label(self) -> None:
        """Bring the label back to full and give it another `LABEL_HOLD_MS`."""
        self._fade.stop()
        self.label.setVisible(True)
        self.label.set_opacity(1.0)
        self._hold.start()

    def _start_fade(self) -> None:
        self._fade.start()

    def _fade_step(self) -> None:
        remaining = self.label.opacity() - LABEL_FADE_STEP
        self.label.set_opacity(remaining)
        if remaining <= 0.0:
            self._fade.stop()

    # -- moving and resizing ----------------------------------------------

    def _edges_at(self, position: QtCore.QPointF) -> QtCore.Qt.Edge:
        edges = QtCore.Qt.Edge(0)
        x, y = position.x(), position.y()
        if x <= RESIZE_MARGIN:
            edges |= QtCore.Qt.Edge.LeftEdge
        elif x >= self.width() - RESIZE_MARGIN:
            edges |= QtCore.Qt.Edge.RightEdge
        if y <= RESIZE_MARGIN:
            edges |= QtCore.Qt.Edge.TopEdge
        elif y >= self.height() - RESIZE_MARGIN:
            edges |= QtCore.Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges: QtCore.Qt.Edge) -> QtCore.Qt.CursorShape:
        left = bool(edges & QtCore.Qt.Edge.LeftEdge)
        right = bool(edges & QtCore.Qt.Edge.RightEdge)
        top = bool(edges & QtCore.Qt.Edge.TopEdge)
        bottom = bool(edges & QtCore.Qt.Edge.BottomEdge)
        if (left and top) or (right and bottom):
            return QtCore.Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return QtCore.Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return QtCore.Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return QtCore.Qt.CursorShape.SizeVerCursor
        return QtCore.Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._press_global = event.globalPosition().toPoint()
        self._travelled = False
        edges = self._edges_at(event.position())
        if edges:
            self._resize_edges = edges
            handle = self.windowHandle()
            started = False
            if handle is not None:
                try:
                    started = bool(handle.startSystemResize(edges))
                except (RuntimeError, TypeError) as exc:
                    # Not every platform plugin implements it; the manual path below is
                    # the fallback and is what the offscreen tests exercise.
                    _report(f"startSystemResize refused ({exc}); resizing by hand.")
            self._resize_geometry = None if started else QtCore.QRect(self.geometry())
            event.accept()
            return
        self._drag_offset = self._press_global - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self.wake_label()
        point = event.globalPosition().toPoint()
        if self._press_global is not None:
            if (point - self._press_global).manhattanLength() > DRAG_SLOP:
                self._travelled = True

        if self._resize_edges is not None and self._resize_geometry is not None:
            self._resize_to(point)
            event.accept()
            return
        if self._drag_offset is not None:
            self.move(point - self._drag_offset)
            event.accept()
            return
        self.setCursor(self._cursor_for(self._edges_at(event.position())))
        super().mouseMoveEvent(event)

    def _resize_to(self, point: QtCore.QPoint) -> None:
        start = self._resize_geometry
        press = self._press_global
        edges = self._resize_edges
        if start is None or press is None or edges is None:
            return
        delta = point - press
        box = QtCore.QRect(start)
        if edges & QtCore.Qt.Edge.LeftEdge:
            box.setLeft(min(box.left() + delta.x(), box.right() - MIN_VIEW_WIDTH))
        if edges & QtCore.Qt.Edge.RightEdge:
            box.setRight(max(box.right() + delta.x(), box.left() + MIN_VIEW_WIDTH))
        if edges & QtCore.Qt.Edge.TopEdge:
            box.setTop(min(box.top() + delta.y(), box.bottom() - MIN_VIEW_HEIGHT))
        if edges & QtCore.Qt.Edge.BottomEdge:
            box.setBottom(max(box.bottom() + delta.y(), box.top() + MIN_VIEW_HEIGHT))
        self.setGeometry(box)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        was_resize = self._resize_edges is not None
        travelled = self._travelled
        self._resize_edges = None
        self._resize_geometry = None
        self._drag_offset = None
        self._press_global = None
        self._travelled = False
        if not was_resize and not travelled:
            self.clicked.emit()
        event.accept()

    def enterEvent(self, event: QtCore.QEnterEvent) -> None:
        self.wake_label()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.unsetCursor()
        super().leaveEvent(event)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._place_overlays()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        """Arm `closed` again, because §5 made closing this window reversible.

        `_emitted_closed` is a once-only guard so that a window closed by its own box
        and then torn down by `close_all` does not announce it twice. With §5 the
        second view is *hidden* on close and shown again from the transport, and
        without this it could only ever be closed once - the second time, the view
        would vanish and nothing would offer it back.
        """
        self._emitted_closed = False
        super().showEvent(event)
        self._place_overlays()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._hold.stop()
        self._fade.stop()
        if not self._emitted_closed:
            self._emitted_closed = True
            self.closed.emit()
        super().closeEvent(event)


class Notice(QtWidgets.QWidget):
    """One line of bad news, under the transport's controls.

    The player had nowhere to say anything. Everything the engine complains about - a
    file this machine cannot decode, a screen that will not stay in step, a recording
    with no sound - went to a `print` under `pythonw`, which is nowhere, and the viewer
    got a black second window and no sentence. This is where those sentences go.

    It is a strip inside the transport's own surface rather than a second window or a
    dialogue, for the reason the recorder's bar puts its footer there: a floating
    message box over a video is a thing to dismiss, and a dialogue steals the keyboard
    from the player that owns Space and the arrow keys. The controls above it stay
    reachable the whole time, which a message that covered them would not.

    Long text is elided and kept whole in the tooltip - a transport bar is 760 pixels
    wide and some of the engine's sentences are not.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self.setFixedHeight(NOTICE_HEIGHT)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = " ".join(str(text).split())
        self.setToolTip(self._text)
        self.update()

    def _font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(9.0)
        return font

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            colours = theme.COLOURS
            # The hairline that separates it from the controls, as in the recorder's
            # own footer strip: a step in colour would be a second surface.
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["line"]), 1))
            painter.drawLine(
                QtCore.QPointF(10.0, 0.5),
                QtCore.QPointF(self.width() - 10.0, 0.5),
            )
            dot = QtCore.QRectF(12.0, self.height() / 2 - 3.0, 6.0, 6.0)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(colours["warn"]))
            painter.drawEllipse(dot)

            font = self._font()
            painter.setFont(font)
            painter.setPen(QtGui.QColor(colours["text_soft"]))
            left = 26.0
            available = max(10.0, self.width() - left - 12.0)
            metrics = QtGui.QFontMetricsF(font)
            painter.drawText(
                QtCore.QRectF(left, 0, available, self.height()),
                int(
                    QtCore.Qt.AlignmentFlag.AlignVCenter
                    | QtCore.Qt.AlignmentFlag.AlignLeft
                ),
                metrics.elidedText(
                    self._text, QtCore.Qt.TextElideMode.ElideRight, int(available)
                ),
            )
        finally:
            painter.end()


# ------------------------------------------------- §1: files arriving by hand
#
# Everything a viewer can hand this player that did not come from the command line: a
# drop onto a window, and the recording the open menu has to describe. None of it
# touches Qt widgets, so all of it is checkable without one.


def dropped_files(data: QtCore.QMimeData | None) -> list[str]:
    """Every local file in a drag, in the order the window manager listed them.

    The order is the whole of why this is a list and not a set. §1 reads two files
    dropped together as a pair, `open.recording_from_files` makes the first of them
    screen 1, and `open.save_set` writes the index beside it - so "the first file" has
    to be the one the viewer picked up first.

    A URL that is not a local file - an address dragged out of a browser, a file on a
    share Windows will not map - is left out rather than refused, because one drop can
    carry both and the files in it are still openable.
    """
    if data is None:
        return []
    try:
        if not data.hasUrls():
            return []
        urls = list(data.urls())
    except (AttributeError, RuntimeError):
        # Not a QMimeData at all, or one whose C++ side has gone between the drag and
        # the drop. Either way there is nothing to open and nothing to say.
        return []
    found: list[str] = []
    for url in urls:
        local = url.toLocalFile()
        if local:
            found.append(local)
    return found


def _worth_showing(frame: Any) -> bool:
    """False for the empty frame Qt delivers when a file runs out.

    A frame object that cannot say - a stub in the tests, a Qt build without the
    method - is shown, because the old behaviour showed everything and this is only
    meant to drop the one frame that is known to be a wipe.
    """
    valid = getattr(frame, "isValid", None)
    if not callable(valid):
        return True
    try:
        return bool(valid())
    except (RuntimeError, TypeError):
        return True


def accept_file_drag(event: Any) -> bool:
    """Accept a drag carrying at least one local file; say whether it did.

    `ScreenView` and `TransportBar` each have the three Qt handlers and each one is two
    lines over this, because §1 says "onto any player window" and a window that ignores
    the drag shows the viewer a no-entry cursor over the one place they were aiming at.
    """
    data = getattr(event, "mimeData", None)
    if data is not None and dropped_files(data()):
        event.acceptProposedAction()
        return True
    event.ignore()
    return False


def recording_files(recording: Any) -> list[Path]:
    """The video file behind each stream, in stream order. Empty when it cannot tell.

    `engine.Recording.screens` holds `engine.Screen` dataclasses, and the stubs the
    tests build hold dictionaries of the same shape, so both are read. What this is
    for is §1.1 and §1.3 - adding a camera to the files already open, and writing the
    set back out - and both of those need the paths rather than the pictures.
    """
    screens = getattr(recording, "screens", None)
    if not isinstance(screens, (list, tuple)):
        return []
    found: list[Path] = []
    for entry in screens:
        raw = entry.get("file") if isinstance(entry, dict) else getattr(
            entry, "file", None
        )
        if raw in (None, ""):
            continue
        found.append(Path(str(raw)))
    return found


def recording_framerate(recording: Any) -> float:
    """Frames per second for §2.2's frame nudge, or `FALLBACK_FRAMERATE`.

    §2.1 is explicit that two cameras at 25 and 30 fps cannot step in lockstep and that
    the step uses the reference stream's rate. `engine.Recording` carries exactly one
    `framerate`, which is that rate, and the panel says so in the button's tooltip
    rather than implying both streams moved by one of their own frames.
    """
    raw = getattr(recording, "framerate", None)
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return FALLBACK_FRAMERATE
    return rate if rate > 0.0 else FALLBACK_FRAMERATE


def format_offset(milliseconds: int) -> str:
    """`+120 ms` / `-1480 ms`, with the sign always written.

    The sign is the whole meaning of the number (§2.1: positive means "this video runs
    ahead, skip into it"), and a value shown as `120` beside one shown as `-1480` reads
    as a magnitude rather than a direction. The unit is left in Latin because `мс` and
    `ms` are both two characters and the field, the summary and the tooltip have to
    agree; `pick` gives the Russian its own spelling where it is a word on its own.
    """
    value = int(milliseconds)
    return f"{value:+d} {pick('windows.sync.ms', 'ms')}"


class TextButton(QtWidgets.QAbstractButton):
    """A short label in a pill, sized to what it says.

    `SpeedChip` is the same pill and `surfaces.Chip` is the same family, and neither
    fits: the chip is 96 px at its narrowest with an icon and a chevron it does not
    need here, and the speed chip is a fixed 46 px that cycles a value of its own. The
    sync panel needs four buttons whose whole content is «−1 кадр», so this is that
    widget - and the fact that there are now three near-identical pills in this package
    is reported rather than hidden: one of them belongs in `ui/surfaces.py`.
    """

    def __init__(
        self,
        text: str,
        tooltip: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self.setFixedHeight(surfaces.BUTTON)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        if tooltip:
            self.setToolTip(tooltip)

    def text_value(self) -> str:
        return self._text

    def set_text_value(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        self.updateGeometry()
        self.update()

    def _font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(9.0)
        return font

    def sizeHint(self) -> QtCore.QSize:
        metrics = QtGui.QFontMetrics(self._font())
        return QtCore.QSize(
            metrics.horizontalAdvance(self._text) + 20, surfaces.BUTTON
        )

    def minimumSizeHint(self) -> QtCore.QSize:
        return self.sizeHint()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            colours = theme.COLOURS
            hovered = self.underMouse() and self.isEnabled()
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(colours["line_strong" if hovered else "line"]), 1
                )
            )
            painter.setBrush(
                QtGui.QColor(
                    colours["line"] if self.isDown()
                    else colours["hover"] if hovered
                    else colours["bg"]
                )
            )
            painter.drawRoundedRect(
                QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
                theme.RADIUS["chip"], theme.RADIUS["chip"],
            )
            painter.setFont(self._font())
            painter.setPen(
                QtGui.QColor(
                    colours["disabled"] if not self.isEnabled()
                    else colours["text"] if hovered
                    else colours["text_soft"]
                )
            )
            painter.drawText(
                QtCore.QRectF(0, 0, self.width(), self.height()),
                int(QtCore.Qt.AlignmentFlag.AlignCenter),
                self._text,
            )
        finally:
            painter.end()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class SyncPanel(QtWidgets.QWidget):
    """§2.2: slide one stream against the other until the clapperboards match.

    A second row under the transport's controls rather than a window of its own,
    because lining two cameras up is done **while watching**: the viewer pauses on the
    clap in one, nudges the other, and looks. A dialog would cover the picture they are
    looking at, and a dialog they have to close to see the result is a dialog they will
    close before the job is done.

    Three decisions §2.2 leaves open:

    **The panel emits an absolute offset, never a delta.** Every control here - the
    four nudges, the typed field, Reset, and the keyboard through `nudge` - works out
    the number and sends it whole, so the engine has one way in and
    `tests/test_player_window.py` has one number to read back. A delta would make the
    keyboard and the buttons two code paths that have to agree about clamping.

    **Nothing here touches the timeline.** The panel has no seek, no play and no
    position: it says which stream moved and by how much, and `SyncEngine.set_offset`
    does the rest. That is the §2.2 rule - "the viewer is sliding one video against the
    other, not scrubbing" - made structural rather than remembered.

    **The value is shown even when its stream is not the one picked.** `summary` names
    every stream that is not at zero, because §2.2 asks for the current value "at all
    times" and a field that shows screen 1's zero while screen 2 sits 1.5 s out is a
    panel that hides the thing it exists to show.

    **The limit is on what is typed here, not on what the recording may hold.**
    `OFFSET_LIMIT_MS` stops a digit too many; it is not a rule about the world.
    `engine.Recording` reads whatever `offset_ms` an index names and `SyncEngine`
    applies it, so an index written for two sources started 70 minutes apart is a
    recording that plays - and a panel whose field clamps it to an hour would show one
    number beside a summary showing another, which is the one disagreement this class
    exists to prevent. `_limit` therefore opens far enough to hold whatever the engine
    reports (`_accommodate`), and never narrower than `OFFSET_LIMIT_MS`. Without it
    «+100 мс» read the clamped −3 600 000 back out of the field's range and sent it as
    the new offset: a button labelled 100 ms that moved the video ten minutes.
    """

    #: Stream index, and the offset in milliseconds it should have from now on.
    offset_requested = QtCore.Signal(int, int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._titles: list[str] = []
        self._offsets: list[int] = []
        self._framerate = float(FALLBACK_FRAMERATE)
        self._frame_ms = max(1, int(round(1000.0 / FALLBACK_FRAMERATE)))
        self._moving = 0
        #: How far the field and `_clamped` will go, widened by `_accommodate` for an
        #: index that names a bigger offset than anyone would type. See the docstring.
        self._limit = OFFSET_LIMIT_MS
        #: True while the field is being written to from here. Qt's `valueChanged`
        #: does not distinguish a typed value from a set one, and without this a
        #: refresh after `offsets_changed` would emit the value straight back at the
        #: engine - a loop that is invisible until two streams start fighting.
        self._writing = False

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 8)
        row.setSpacing(6)

        self.picker = surfaces.Segmented([("0", "1"), ("1", "2")], self)
        # `Segmented` publishes a `sizeHint` and no `minimumSizeHint`, so a row that
        # runs out of width shrinks it to a sliver - measured: on a 900 px bar the
        # two cells came out 14 px wide between them, which is a control nobody can
        # hit and nobody can read. It is the only fixed-width thing in this row.
        self.picker.setFixedWidth(self.picker.sizeHint().width())
        self.picker.changed.connect(self._picked)
        row.addWidget(self.picker)

        self.frame_back = TextButton("")
        self.frame_back.clicked.connect(lambda: self.nudge(frames=-1))
        row.addWidget(self.frame_back)
        self.frame_on = TextButton("")
        self.frame_on.clicked.connect(lambda: self.nudge(frames=1))
        row.addWidget(self.frame_on)
        self.coarse_back = TextButton("")
        self.coarse_back.clicked.connect(lambda: self.nudge(milliseconds=-NUDGE_MS))
        row.addWidget(self.coarse_back)
        self.coarse_on = TextButton("")
        self.coarse_on.clicked.connect(lambda: self.nudge(milliseconds=NUDGE_MS))
        row.addWidget(self.coarse_on)

        self.field = QtWidgets.QSpinBox(self)
        self.field.setRange(-self._limit, self._limit)
        self.field.setSingleStep(NUDGE_MS)
        # Qt's default tracking emits `valueChanged` on every keystroke, so typing
        # «1480» asked the engine for 1, then 14, then 148, then 1480 - four real
        # `setPosition` calls that flush the decode pipeline, three of them to places
        # the viewer never asked for. §2.2 wants the adjustment to apply "without
        # stopping and without a visible restart", and that is four restarts. Off, the
        # field speaks on Enter and on losing focus; the nudges and the keyboard go
        # through `nudge()` and are untouched.
        self.field.setKeyboardTracking(False)
        self.field.setAccelerated(True)
        # The sign is the meaning (§2.1), so it is on the number itself and not only
        # in the sentence beside it, which a viewer reading the field will not be.
        self.field.setGroupSeparatorShown(False)
        self.field.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        self.field.setFixedWidth(104)
        self.field.valueChanged.connect(self._typed)
        row.addWidget(self.field)

        self.reset_button = TextButton("")
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.reset_button)

        self.summary = surfaces.label("", "text_dim", 9.0)
        row.addWidget(self.summary, 1)

        self.retranslate()
        i18n.notifier.changed.connect(self.retranslate)

    # -- text --------------------------------------------------------------

    def retranslate(self) -> None:
        """Every word on the panel, in the language now in force.

        The four nudge buttons carry a unit each, and the unit is part of the label
        rather than glued on in Python: «кадр» declines with the number in Russian and
        a translator handed `"-1 "` plus a word has no way to fix that.
        """
        self.frame_back.set_text_value(pick("windows.sync.frame.back", "−1 frame"))
        self.frame_on.set_text_value(pick("windows.sync.frame.on", "+1 frame"))
        self.coarse_back.set_text_value(pick("windows.sync.coarse.back", "−100 ms"))
        self.coarse_on.set_text_value(pick("windows.sync.coarse.on", "+100 ms"))
        self.reset_button.set_text_value(pick("windows.sync.reset", "Reset"))
        self.picker.setToolTip(
            pick("windows.sync.picker", "Which screen is being moved")
        )
        step = pick(
            "windows.sync.frame.hint",
            "One frame of the reference stream, at {rate} fps",
        ).format(rate=f"{self._framerate:g}")
        self.frame_back.setToolTip(step)
        self.frame_on.setToolTip(step)
        explained = pick(
            "windows.sync.sign",
            "Milliseconds. A plus means this video runs ahead — skip into it.",
        )
        self.field.setToolTip(explained)
        self.reset_button.setToolTip(
            pick("windows.sync.reset.hint", "Play this screen in step again")
        )
        self.field.setSuffix(" " + pick("windows.sync.ms", "ms"))
        self._write_summary()

    # -- what it is working on ---------------------------------------------

    def set_streams(
        self, titles: Sequence[str], offsets: Sequence[int], framerate: float
    ) -> None:
        """Name the streams, take their offsets, and size the frame nudge.

        The picker's cells stay «1» and «2» - they are the stream numbers and read the
        same in both languages - while the names go into the summary and the tooltip,
        because «Экран 2 — основной» will not fit in a 24 px cell and eliding it would
        leave two cells reading the same thing.
        """
        self._titles = [str(title) for title in titles]
        self._offsets = [int(value) for value in offsets]
        # The rate is kept as it was given, not read back out of `_frame_ms`:
        # rounding to whole milliseconds turns 30 fps into 33 ms and back into
        # 30.303 fps, and a tooltip that tells the viewer a rate their camera
        # does not have is worse than no tooltip.
        self._framerate = max(0.001, float(framerate))
        self._frame_ms = max(1, int(round(1000.0 / self._framerate)))
        # A different recording narrows the field again: `_accommodate` works out the
        # limit from the offsets alone, so it is reconciled rather than reset here -
        # writing `_limit` back by hand would make its early return skip the `setRange`
        # and leave the field open at the last recording's width.
        self._accommodate()
        if not 0 <= self._moving < len(self._offsets):
            self._moving = 0
        self.picker.set_current(str(self._moving))
        self.retranslate()
        self._show_current()

    def set_offsets(self, offsets: Sequence[int]) -> None:
        """Take the engine's numbers back. Emits nothing - see `_writing`."""
        self._offsets = [int(value) for value in offsets]
        self._accommodate()
        if not 0 <= self._moving < len(self._offsets):
            self._moving = 0
            self.picker.set_current(str(self._moving))
        self._show_current()

    def offsets(self) -> list[int]:
        return list(self._offsets)

    def moving(self) -> int:
        """Which stream the buttons and the keyboard are moving right now."""
        return self._moving

    def frame_ms(self) -> int:
        return self._frame_ms

    # -- the controls ------------------------------------------------------

    def _picked(self, key: str) -> None:
        """Move a different stream. Changes nothing about the recording."""
        try:
            index = int(key)
        except ValueError:  # a cell key this panel did not put there
            return
        if not 0 <= index < max(1, len(self._offsets)):
            return
        self._moving = index
        self._show_current()

    def _typed(self, value: int) -> None:
        if self._writing:
            return
        self.offset_requested.emit(self._moving, self._clamped(int(value)))

    def nudge(self, *, frames: int = 0, milliseconds: int = 0) -> None:
        """Move the picked stream by frames, by milliseconds, or by both.

        Public because §2.2 gives the same four movements to the keyboard, and a key
        press that walked the buttons would be a second implementation of the clamp.
        """
        step = int(frames) * self._frame_ms + int(milliseconds)
        if not step:
            return
        self.offset_requested.emit(
            self._moving, self._clamped(self.current() + step)
        )

    def reset(self) -> None:
        """§2.2's Сбросить: this stream plays in step with the reference again."""
        if self.current() == 0:
            return
        self.offset_requested.emit(self._moving, 0)

    def current(self) -> int:
        """The picked stream's offset as the engine last reported it."""
        if 0 <= self._moving < len(self._offsets):
            return self._offsets[self._moving]
        return 0

    def _clamped(self, value: int) -> int:
        return max(-self._limit, min(self._limit, int(value)))

    def _accommodate(self) -> None:
        """Open the field wide enough for the offsets the engine actually reports.

        An ordinary recording - every offset inside the typed limit - is left at
        exactly `OFFSET_LIMIT_MS`, which is what that limit is for. Only an index that
        already names a bigger one widens the field, and it widens it by a further
        hour rather than to the value itself: stopping dead on the number the file
        came with would leave «+100 мс» pressable and doing nothing in one of its two
        directions, which is the silent no-op this panel is written against.

        `setRange` can emit `valueChanged` when the value the field holds no longer
        fits, so it is written under `_writing` like every other change made here.
        """
        reach = max((abs(value) for value in self._offsets), default=0)
        wanted = (
            OFFSET_LIMIT_MS if reach <= OFFSET_LIMIT_MS else reach + OFFSET_LIMIT_MS
        )
        if wanted == self._limit:
            return
        self._limit = wanted
        self._writing = True
        try:
            self.field.setRange(-wanted, wanted)
        finally:
            self._writing = False

    # -- showing it --------------------------------------------------------

    def _show_current(self) -> None:
        self._writing = True
        try:
            self.field.setValue(self.current())
        finally:
            self._writing = False
        self._write_summary()

    def _write_summary(self) -> None:
        """Name every stream that is not in step, or say that none of them is.

        Only the streams that have moved: a line reading «Экран 1 +0 мс, Экран 2 +0 мс»
        is the common case, and a summary that is longest when there is nothing to say
        trains the eye to stop reading it.
        """
        moved = [
            pick("windows.sync.moved", "{name} {offset}").format(
                name=self._name(index), offset=format_offset(value)
            )
            for index, value in enumerate(self._offsets)
            if value
        ]
        self.summary.setText(
            ", ".join(moved) if moved
            else pick("windows.sync.in.step", "both playing in step")
        )

    def _name(self, index: int) -> str:
        if 0 <= index < len(self._titles):
            return self._titles[index]
        return _("Screen {number}").format(number=index + 1)


class TransportBar(surfaces.FloatingSurface):
    """Play/pause, the clock, a scrubber, frame step, speed, volume and close.

    Two of `FloatingSurface`'s rules are deliberately turned off here, and both for the
    same reason: this is not recorder chrome. It is *not* excluded from screen capture,
    because there is no capture running to keep it out of; and it *does* take focus,
    because the player's keyboard interface lives on it and a window carrying
    `WS_EX_NOACTIVATE` can never be the focus window.
    """

    play_pause_requested = QtCore.Signal()
    step_requested = QtCore.Signal(int)       # frames, signed
    seek_requested = QtCore.Signal(int)       # absolute milliseconds
    speed_requested = QtCore.Signal(float)
    mute_requested = QtCore.Signal()
    volume_requested = QtCore.Signal(float)   # 0..1
    #: The arrange button was pressed. The bar does not build the menu: what is in it
    #: depends on how many monitors there are, how many views are open and which of
    #: them is hidden, and none of that is the bar's to know. It carries the control
    #: and `PlayerWindows` carries the menu.
    arrange_requested = QtCore.Signal()
    #: §1's Open button, carried the same way as the arrange one and for the same
    #: reason: what is in the menu depends on the recordings folder and on how many
    #: streams are open, and neither is the bar's to know.
    open_requested = QtCore.Signal()
    #: §1: files dropped on the bar itself. A list, because two at once are a pair.
    files_dropped = QtCore.Signal(list)
    #: §1.2: which stream should be heard, or -1 for none of them.
    audio_source_requested = QtCore.Signal(int)
    #: §2.2: stream index, and the offset in milliseconds it should have.
    offset_requested = QtCore.Signal(int, int)
    close_requested = QtCore.Signal()
    #: The bar itself was closed - by Alt+F4, by the taskbar, or by the set shutting
    #: down. It carries a close button, so it is the obvious thing to close the player
    #: with, and without this the views would be left playing with nothing driving them.
    closed = QtCore.Signal()

    #: See the class docstring: nothing is being recorded, so nothing is hidden.
    hide_from_capture = False

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(
            radius=theme.RADIUS["bar"], fill="panel", border="line", parent=parent
        )
        # `FloatingSurface.showEvent` applies WS_EX_NOACTIVATE unless `_styled` is
        # already set. Setting it here is how this one window keeps the ability to be
        # focused, which it needs for Space and the arrow keys.
        self._styled = True
        self.setWindowFlags(
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, False
        )
        self.setWindowTitle(_("ScreenRec player"))
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(TRANSPORT_HEIGHT)
        self.setMinimumWidth(TRANSPORT_MIN_WIDTH)
        # §1: a drop lands on whichever window the pointer was over, and the bar is a
        # window like the views. Accepting it here as well is the difference between
        # "drop a file on the player" working and working everywhere but the controls.
        self.setAcceptDrops(True)

        self._duration = 0
        self._position = 0
        self._scrubbing = False
        self._volume = 1.0
        self._muted = False
        #: How many streams the host says there are. Decides whether the audio-source
        #: chip and the sync button are on the bar at all: §1's "a recording with one
        #: stream never asks anything" is about the whole interface.
        self._streams = 1
        #: Which stream is heard, as the host last said, or -1 for silence.
        self._audio_source = 0
        #: The stream names the host gave, for the audio menu and the sync summary.
        self._titles: list[str] = []
        #: Whether §2.2's panel can do anything at all: two streams, and a clock
        #: that can move an offset. One field rather than the test written out in
        #: four places, because the panel, the button, the height and the toggle all
        #: have to agree about it.
        self._can_sync = False
        #: What the play button is showing. Kept rather than read back off the button,
        #: because `retranslate` has to write the *same* tooltip again in the other
        #: language and a glyph name is not a sentence.
        self._playing = False
        self._emitted_closed = False
        self._drag = surfaces.SurfaceDrag(self)

        # The controls in a row, with room under them for the notice strip. The strip
        # is hidden until there is something to say, and the bar is only that much
        # taller while it is showing.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(6)
        outer.addLayout(row, 0)

        # First on the bar, before play: it is the only control that is about a
        # recording other than the one in front of the viewer, and a door belongs at
        # the edge rather than in the middle of the transport.
        self.open_button = PlayerButton(
            "folder", pick("windows.open.button", "Open a recording (Ctrl+O)")
        )
        self.open_button.clicked.connect(self.open_requested.emit)
        row.addWidget(self.open_button)

        self.play_button = PlayerButton("play", _("Play (Space)"))
        self.play_button.clicked.connect(self.play_pause_requested.emit)
        row.addWidget(self.play_button)

        self.clock = surfaces.label("00:00 / 00:00", "text_soft")
        clock_font = QtGui.QFont("Consolas")
        clock_font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        clock_font.setPointSizeF(9.5)
        self.clock.setFont(clock_font)
        self.clock.setMinimumWidth(104)
        self.clock.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter
        )
        row.addWidget(self.clock)

        self.scrubber = Scrubber(self)
        self.scrubber.setRange(0, 0)
        # No focus on any control, so key presses reach this window and the shortcuts
        # are not stolen by the slider's own arrow-key handling.
        self.scrubber.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.scrubber.setToolTip(_("Scrub"))
        self.scrubber.sliderPressed.connect(self._scrub_began)
        self.scrubber.sliderMoved.connect(self._scrub_moved)
        self.scrubber.sliderReleased.connect(self._scrub_ended)
        row.addWidget(self.scrubber, 1)

        self.back_button = PlayerButton("step_back", _("One frame back (,)"))
        self.back_button.clicked.connect(lambda: self.step_requested.emit(-1))
        row.addWidget(self.back_button)

        self.forward_button = PlayerButton("step_forward", _("One frame on (.)"))
        self.forward_button.clicked.connect(lambda: self.step_requested.emit(1))
        row.addWidget(self.forward_button)

        self.speed_chip = SpeedChip(self)
        self.speed_chip.changed.connect(self.speed_requested.emit)
        row.addWidget(self.speed_chip)

        # No tooltip passed in: `_write_volume_tooltip` is the single place that writes
        # this one, so the button cannot open saying one thing and be re-worded into
        # another by the first `set_muted`. That mismatch is why the catalogue's 'Mute'
        # never matched the sentence the button actually carried.
        self.volume_button = PlayerButton("volume")
        self._write_volume_tooltip()
        self.volume_button.clicked.connect(self.mute_requested.emit)
        row.addWidget(self.volume_button)

        # §1.2, and deliberately beside the volume button rather than inside it: the
        # volume is how loud, this is whose. Hidden for one stream, where there is no
        # question to ask, and the chip says the current answer at a glance so that a
        # viewer listening to the wrong camera can see why.
        self.audio_chip = surfaces.Chip("speaker", "")
        # Capped, and the cap matters: `Chip` sizes itself to its label and will take
        # 320 px for «Экран 2 — основной», which it would take out of the scrubber.
        # The chip elides in the middle, and the whole name is in the tooltip and in
        # the menu, so the cost of the cap is a few characters in the one place the
        # name is also written twice over.
        self.audio_chip.setMaximumWidth(AUDIO_CHIP_MAX_WIDTH)
        self.audio_chip.clicked.connect(self._open_audio_menu)
        self.audio_chip.hide()
        row.addWidget(self.audio_chip)

        # §2.2's panel, as a toggle. `set_on` is also how the bar says an offset is in
        # force while the panel is shut - §2.2 asks for the value to be visible at all
        # times, and an accented button with the number in its tooltip is that without
        # a second row of chrome over the picture.
        self.sync_button = PlayerButton("sync", "")
        self.sync_button.clicked.connect(self.toggle_sync)
        self.sync_button.hide()
        row.addWidget(self.sync_button)

        # Hidden until the host says otherwise: a one-stream recording has nothing to
        # arrange, and a button that opens an empty menu is worse than no button.
        self.arrange_button = PlayerButton("arrange", self._arrange_tooltip())
        self.arrange_button.clicked.connect(self.arrange_requested.emit)
        self.arrange_button.hide()
        row.addWidget(self.arrange_button)

        self.close_button = surfaces.IconButton("close", _("Close the player (Esc)"))
        self.close_button.clicked.connect(self.close_requested.emit)
        row.addWidget(self.close_button)

        self.sync_panel = SyncPanel(self)
        self.sync_panel.hide()
        self.sync_panel.offset_requested.connect(self.offset_requested.emit)
        outer.addWidget(self.sync_panel, 0)

        self.notice = Notice(self)
        self.notice.hide()
        outer.addWidget(self.notice, 0)
        self._notice_timer = QtCore.QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_MS)
        self._notice_timer.timeout.connect(self.clear_notice)
        self._write_sync_tooltip()
        i18n.notifier.changed.connect(self.retranslate)

    # -- text --------------------------------------------------------------

    def retranslate(self) -> None:
        """Say every word on this bar again, in the language now in force.

        Connected to `i18n.notifier.changed` in the constructor, so a switch in the
        settings reaches a player that is already open rather than waiting for the
        viewer to close it and open it again.

        Two things on the bar are deliberately not here. The speed chip looks after its
        own tooltip - it is a widget with a constructor of its own and it is connected
        to the same signal. And the notice strip is not retranslated: what is on it is
        a finished sentence handed in by the engine or by a view, and a sentence cannot
        be translated back into the language it was not written in. A notice raised
        before the switch therefore stays as it was for the few seconds it has left;
        the next one is in the new language.
        """
        self.setWindowTitle(_("ScreenRec player"))
        self.scrubber.setToolTip(_("Scrub"))
        self.back_button.setToolTip(_("One frame back (,)"))
        self.forward_button.setToolTip(_("One frame on (.)"))
        self.arrange_button.setToolTip(self._arrange_tooltip())
        self.open_button.setToolTip(
            pick("windows.open.button", "Open a recording (Ctrl+O)")
        )
        self.close_button.setToolTip(_("Close the player (Esc)"))
        self.play_button.setToolTip(self._play_tooltip())
        self._write_volume_tooltip()
        self._write_audio_chip()
        self._write_sync_tooltip()

    def _play_tooltip(self) -> str:
        """What the play button says in the state it is in."""
        return _("Pause (Space)") if self._playing else _("Play (Space)")

    def _arrange_tooltip(self) -> str:
        """The arrange button's sentence, with `L` in it only where `L` works.

        §7's `L` steps through §5's four schemes in the separate-windows half, and a
        scheme places **two** windows, so on a three-screen recording the key does
        nothing and is not advertised. The stage's layout chip carries the same
        «Расположение (L)» for the same key, which is the point: one table of
        shortcuts for the whole player, and a tooltip that names a key which does
        nothing here is worse than no tooltip - the reason the two step buttons say
        `(,)` and `(.)`.
        """
        return _("Arrange (L)") if self._streams == 2 else _("Arrange")

    def _write_volume_tooltip(self) -> None:
        """The volume button's sentence, with the level in it.

        Two whole templates rather than a translated word glued to a translated tail:
        Russian does not put the verb and the rest of the sentence together the way
        English does, and `"Unmute" + " - volume 60%…"` leaves the translator no way
        to move either half.
        """
        percent = round(self._volume * 100)
        template = (
            _("Unmute - volume {percent}%, scroll here to change it")
            if self._muted
            else _("Mute - volume {percent}%, scroll here to change it")
        )
        self.volume_button.setToolTip(template.format(percent=percent))

    # -- §1.2: which stream is heard ---------------------------------------

    def set_sources_available(
        self,
        streams: int,
        titles: Sequence[str] = (),
        *,
        audio: bool = True,
        sync: bool = True,
    ) -> None:
        """Say how many streams there are and what the engine can do about them.

        Two controls appear only for a pair - the audio source and the sync button -
        and the bar's floor width moves with them, because a bar whose minimum was
        sized for a pair would open a one-video recording with 180 px of empty strip.
        That one-video case is the regression that matters most, so it is the one
        spelled out here rather than left to fall out of the layout.

        `audio` and `sync` are the engine's answer rather than the recording's: both
        of `docs/PLAYER_SOURCES.md`'s calls are optional, and a control that cannot do
        what it says is worse than a control that is not there.
        """
        self._streams = max(1, int(streams))
        self._titles = [str(title) for title in titles]
        # The audio source is a question for any number of streams - the menu names
        # every one of them. The sync panel is **two**, as §2.2 says in so many
        # words: its picker is a two-cell switch, and a third stream would be a
        # control that could not address it. The three-screen case is a recorder set,
        # whose screens start together anyway - 17 ms apart, measured.
        self._can_sync = self._streams == 2 and bool(sync)
        self.audio_chip.setVisible(self._streams > 1 and bool(audio))
        self.sync_button.setVisible(self._can_sync)
        if not self._can_sync and not self.sync_panel.isHidden():
            # A set that has become a single stream - never today, but `open.py` may
            # rebuild this bar's host - must not keep a panel that moves nothing.
            self.sync_panel.hide()
            self._apply_height()
        self.setMinimumWidth(
            TRANSPORT_MIN_WIDTH_PAIR if self._streams > 1
            else TRANSPORT_MIN_WIDTH
        )
        # The stream count decides whether `L` does anything, and therefore whether
        # the arrange button may name it.
        self.arrange_button.setToolTip(self._arrange_tooltip())
        self._write_audio_chip()

    def set_audio_source(self, index: int) -> None:
        """Show which stream is heard. -1 means none of them is."""
        self._audio_source = int(index)
        self._write_audio_chip()

    def audio_source(self) -> int:
        return self._audio_source

    def _audio_name(self, index: int) -> str:
        if 0 <= index < len(self._titles):
            return self._titles[index]
        return _("Screen {number}").format(number=index + 1)

    def _write_audio_chip(self) -> None:
        """The chip's own label and its sentence, in one place.

        Two whole templates rather than a word glued to a word, for the reason
        `_write_volume_tooltip` gives: the two halves do not sit in the same order in
        both languages.
        """
        silent = not 0 <= self._audio_source < max(1, self._streams)
        self.audio_chip.set_value(
            "speaker_off" if silent else "speaker",
            pick("windows.audio.off", "No sound") if silent
            else self._audio_name(self._audio_source),
        )
        self.audio_chip.setToolTip(
            pick("windows.audio.hint.off", "Nothing is being heard — click to choose")
            if silent else
            pick(
                "windows.audio.hint", "The sound comes from {name} — click to change"
            ).format(name=self._audio_name(self._audio_source))
        )

    def _open_audio_menu(self) -> None:
        """§1.2's three answers: screen 1, screen 2, or off.

        Built on the press and never kept, as the arrange menu is: the stream names
        can be renamed from outside and the language can change under an open player,
        and a menu built once would be the one place neither reaches.

        `popup`, not `exec`: this is reached from `clicked`, while the chip is still
        handling its own mouse release, and a nested event loop started there is the
        crash this package has already paid for once.
        """
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(theme.style_sheet())
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        for index in range(self._streams):
            action = menu.addAction(self._audio_name(index))
            action.setCheckable(True)
            action.setChecked(index == self._audio_source)
            # `_checked` absorbs `triggered(bool)`; without it PySide hands `checked`
            # to the first parameter and every entry would ask for stream 0 or 1.
            action.triggered.connect(
                lambda _checked=False, which=index:
                self.audio_source_requested.emit(which)
            )
        menu.addSeparator()
        silence = menu.addAction(pick("windows.audio.off", "No sound"))
        silence.setCheckable(True)
        silence.setChecked(not 0 <= self._audio_source < max(1, self._streams))
        silence.triggered.connect(
            lambda _checked=False: self.audio_source_requested.emit(-1)
        )
        self.audio_chip.set_open(True)
        menu.aboutToHide.connect(lambda: self.audio_chip.set_open(False))
        menu.popup(
            self.audio_chip.mapToGlobal(
                QtCore.QPoint(0, -menu.sizeHint().height())
            )
        )

    # -- §2.2: the sync panel ----------------------------------------------

    def set_sync_streams(
        self, titles: Sequence[str], offsets: Sequence[int], framerate: float
    ) -> None:
        """Hand the panel what it needs, and light the button if anything has moved."""
        self.sync_panel.set_streams(titles, offsets, framerate)
        self._write_sync_tooltip()

    def set_offsets(self, offsets: Sequence[int]) -> None:
        """§2.2: the engine's offsets have changed. Emits nothing back at it."""
        self.sync_panel.set_offsets(offsets)
        self._write_sync_tooltip()

    def toggle_sync(self) -> None:
        """Open or shut the panel. The bar grows upwards, as it does for a notice."""
        self.set_sync_open(self.sync_panel.isHidden())

    def set_sync_open(self, open_: bool) -> None:
        """Show or hide the panel. Refused for one stream: there is nothing to slide.

        `isHidden`, not `isVisible`, throughout: a child of a window that has not been
        shown yet reports `isVisible() == False` whatever was asked of it, so a bar
        built and configured before `show()` - which is every bar this package builds -
        would measure its own height with the panel shut.
        """
        wanted = bool(open_) and self._can_sync
        if wanted == self.is_sync_open():
            return
        self.sync_panel.setVisible(wanted)
        self._apply_height()
        self._write_sync_tooltip()

    def is_sync_open(self) -> bool:
        return not self.sync_panel.isHidden()

    def _write_sync_tooltip(self) -> None:
        """What the sync button says, and whether it is lit.

        Lit means "something is not at zero", which is §2.2's "the current value shown
        at all times" for the case where the panel is shut: an offset nobody can see is
        a recording that looks broken for a reason nobody can find.
        """
        offsets = self.sync_panel.offsets()
        moved = [value for value in offsets if value]
        self.sync_button.set_on(bool(moved) or not self.sync_panel.isHidden())
        if moved:
            self.sync_button.setToolTip(
                pick(
                    "windows.sync.button.moved",
                    "Line the screens up — one is {offset} out",
                ).format(offset=format_offset(moved[0]))
            )
            return
        self.sync_button.setToolTip(
            pick("windows.sync.button", "Line the screens up by hand")
        )

    # -- §1: a file dropped on the bar -------------------------------------

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        accept_file_drag(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        accept_file_drag(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        files = dropped_files(event.mimeData())
        if not files:
            event.ignore()
            return
        event.acceptProposedAction()
        self.files_dropped.emit(files)

    # -- saying something to the viewer ------------------------------------

    def say(self, text: str) -> None:
        """Show one sentence under the controls for `NOTICE_MS`, then take it away.

        A second message replaces the first rather than queueing: these arrive from the
        engine when a file misbehaves, and the one on screen now is the one that still
        matters. The whole sentence is in the tooltip whatever the bar's width.
        """
        message = " ".join(str(text).split())
        if not message:
            return
        self.notice.set_text(message)
        if self.notice.isHidden():
            self.notice.show()
            self._apply_height()
        self._notice_timer.start()

    def clear_notice(self) -> None:
        """Put the bar back to its own height. Safe to call when nothing is showing."""
        self._notice_timer.stop()
        if self.notice.isHidden():
            return
        self.notice.hide()
        self.notice.set_text("")
        self._apply_height()

    def _apply_height(self) -> None:
        """The height the two optional rows add up to, applied once.

        Computed from both rows rather than set to a number by whoever changed one of
        them: with §2.2's panel there are four heights, and a notice arriving while the
        panel is open used to be a call that said "52 + 30" and shut the panel by
        arithmetic. Which row is showing is read off the widgets, so there is one
        answer and no flag to keep in step with them.
        """
        wanted = TRANSPORT_HEIGHT
        if not self.sync_panel.isHidden():
            wanted += SYNC_HEIGHT
        if not self.notice.isHidden():
            wanted += NOTICE_HEIGHT
        self._set_height(wanted)

    def _set_height(self, wanted: int) -> None:
        """Grow or shrink upwards, so the controls stay under the pointer."""
        if wanted == self.height():
            return
        bottom = self.y() + self.height()
        self.setFixedHeight(wanted)
        if self.isVisible():
            self.move(self.x(), bottom - wanted)

    # -- what the controller tells it --------------------------------------

    def set_playing(self, playing: bool) -> None:
        self._playing = bool(playing)
        self.play_button.set_kind("pause" if self._playing else "play")
        self.play_button.setToolTip(self._play_tooltip())

    def set_arrange_available(self, available: bool) -> None:
        """Show or hide the arrange button.

        Hidden rather than disabled: §1's "a recording with one stream never asks
        anything" is about the whole interface, not only the opening dialog, and a
        greyed control on the bar of a one-screen recording is still a question being
        asked. A disabled card in the *chooser* teaches something, because the viewer
        is choosing; a disabled button on a transport teaches nothing.
        """
        self.arrange_button.setVisible(bool(available))

    def set_duration(self, milliseconds: int) -> None:
        self._duration = max(0, int(milliseconds))
        self.scrubber.setRange(0, self._duration)
        # Qt's defaults are 10 and 1, which on a slider whose range is milliseconds
        # means a page step of a hundredth of a second. Over a short recording the page
        # is capped so it can never be the whole thing.
        self.scrubber.setPageStep(
            max(1, min(SCRUB_PAGE_MS, self._duration // 4)) if self._duration else
            SCRUB_PAGE_MS
        )
        self.scrubber.setSingleStep(SCRUB_STEP_MS)
        self._write_clock()

    def set_position(self, milliseconds: int) -> None:
        self._position = max(0, int(milliseconds))
        if not self._scrubbing:
            blocked = self.scrubber.blockSignals(True)
            try:
                self.scrubber.setValue(min(self._position, self._duration))
            finally:
                self.scrubber.blockSignals(blocked)
        self._write_clock()

    def set_speed(self, speed: float) -> None:
        self.speed_chip.set_value(speed)

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.volume_button.set_kind("volume_off" if self._muted else "volume")
        self._write_volume_tooltip()

    def duration(self) -> int:
        return self._duration

    # -- scrubbing ---------------------------------------------------------

    def _scrub_began(self) -> None:
        self._scrubbing = True

    def _scrub_moved(self, value: int) -> None:
        self._position = int(value)
        self._write_clock()
        self.seek_requested.emit(int(value))

    def _scrub_ended(self) -> None:
        self._scrubbing = False
        value = int(self.scrubber.value())
        # The release is the backstop for a press that moved the handle without a
        # `sliderMoved` - not a second seek to the place the drag already asked for.
        # `SyncEngine.seek` pauses every player, moves it and starts it again, so a
        # repeat at the same millisecond is a visible hitch and buys nothing.
        if value != self._position:
            self._position = value
            self.seek_requested.emit(value)

    def _write_clock(self) -> None:
        self.clock.setText(
            f"{format_clock(self._position)} / {format_clock(self._duration)}"
        )

    # -- volume ------------------------------------------------------------

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        steps = event.angleDelta().y() / 120.0
        if not steps:
            event.ignore()
            return
        self._volume = max(0.0, min(1.0, self._volume + steps * 0.05))
        self.set_muted(self._muted)  # refresh the tooltip's percentage
        self.volume_requested.emit(self._volume)
        event.accept()

    # -- moving the bar ----------------------------------------------------

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if surfaces.control_at(self, event.position().toPoint()) is not None:
            event.ignore()
            return
        self._drag.begin(event.globalPosition().toPoint())
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag.active():
            self._drag.to(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._drag.end()
        event.accept()

    # -- closing -----------------------------------------------------------

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Say so, exactly once, whatever closed it.

        This window is the one with a close button on it, it is in the taskbar and in
        Alt+Tab, and Alt+F4 works on it - so it is the gesture a viewer will reach for
        to close the player. Without this it closed alone and left two frameless
        windows still playing, a poll still ticking and the engine still running, with
        nothing on screen to stop them. `ScreenView` has carried the same signal from
        the start; the bar simply never grew one.
        """
        self._notice_timer.stop()
        if not self._emitted_closed:
            self._emitted_closed = True
            self.closed.emit()
        super().closeEvent(event)


def format_clock(milliseconds: int) -> str:
    """`00:14`, or `1:02:31` once there is an hour to show."""
    total = max(0, int(milliseconds)) // 1000
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


# --------------------------------------------------------------- the engine
#
# The engine is a sibling module. Rather than reach into it with bare attribute access
# and fail at the worst moment, every call it is asked for is resolved once, here, with
# the contract name first and the obvious synonym second, and anything required that is
# missing is reported at construction.


class _Engine:
    """Everything the windows ask of the playback engine, resolved once."""

    _CALLS: dict[str, tuple[str, ...]] = {
        "play": ("play",),
        "pause": ("pause",),
        "toggle": ("toggle_play", "toggle"),
        "step": ("step_frames", "step"),
        "seek": ("seek", "set_position"),
        "skip": ("skip", "nudge"),
        "set_speed": ("set_speed",),
        "set_muted": ("set_muted", "set_mute"),
        "set_volume": ("set_volume",),
        "close": ("close", "close_all", "stop"),
        # §6's per-stream end state, asked rather than waited for: a view built after
        # the shorter stream ran out would never hear `stream_ended`. Optional, so it
        # is read through `stream_has_ended` below and never through `_invoke`, which
        # would complain about an engine that simply predates the signal.
        "stream_has_ended": ("stream_has_ended",),
        # `docs/PLAYER_SOURCES.md`, all optional and all asked rather than assumed:
        # an engine written before it opens exactly as it did, without a sync panel
        # and without an audio-source chip, which is better than controls that do
        # nothing. See `can_sync` and `can_choose_audio`.
        "stream_is_waiting": ("stream_is_waiting",),
        "set_offset": ("set_offset",),
        "set_audio_source": ("set_audio_source", "set_audio_index"),
    }
    _VALUES: dict[str, tuple[str, ...]] = {
        "position": ("position_ms", "position"),
        "duration": ("duration_ms", "duration"),
        "playing": ("is_playing", "playing"),
        "muted": ("is_muted", "muted"),
        "speed": ("speed", "rate"),
        "sinks": ("sinks", "video_sinks"),
        "recording": ("recording",),
        "seeking": ("seeking", "is_seeking"),
        "offsets": ("offsets",),
        "audio_index": ("audio_index", "audio_source"),
    }
    _SIGNALS: dict[str, tuple[str, ...]] = {
        "position": ("position_changed", "positionChanged"),
        "duration": ("duration_changed", "durationChanged"),
        # `state_changed(bool)` is what `player.engine.SyncEngine` actually calls it.
        "playing": ("state_changed", "playing_changed", "playingChanged"),
        "problem": ("problem",),
        "finished": ("finished",),
        # Both are §6's, both are optional: an engine without them leaves the views
        # undimmed and unbadged, which is exactly how the player behaved before.
        "seeking": ("seeking_changed", "seekingChanged"),
        "stream_ended": ("stream_ended", "streamEnded"),
        # `docs/PLAYER_SOURCES.md`'s three, optional for the same reason as the calls.
        "stream_waiting": ("stream_waiting", "streamWaiting"),
        "offsets": ("offsets_changed", "offsetsChanged"),
        "audio": ("audio_changed", "audio_source_changed"),
    }
    #: Without these the player cannot do its job, so their absence is said out loud.
    _REQUIRED = ("toggle", "seek", "step", "close")

    def __init__(self, engine: Any) -> None:
        self.raw = engine
        #: Set by `PlayerWindows` once there is a transport bar to say things on.
        #: Everything said before that is kept in `notes` and drained then.
        self.listener: Callable[[str], None] | None = None
        self.notes: list[str] = []
        self._calls: dict[str, Callable[..., Any] | None] = {}
        for name, options in self._CALLS.items():
            self._calls[name] = self._find_callable(options)
        missing = [name for name in self._REQUIRED if self._calls[name] is None]
        if missing:
            self.complain(
                f"{type(engine).__name__} has no "
                + ", ".join(
                    f"{name}() ({'/'.join(self._CALLS[name])})" for name in missing
                )
                + "; those controls will do nothing."
            )

    def complain(self, message: str) -> None:
        """Report, and also tell whoever is showing things to the viewer.

        Everything here used to go to a `print` that `pythonw` throws away, which made
        a dropped control indistinguishable from a control that did nothing on purpose.
        """
        _report(message)
        self.notes.append(message)
        if self.listener is not None:
            try:
                self.listener(message)
            except Exception as exc:  # a broken view must not break the engine call
                _report(f"the notice could not be shown ({exc!r}): {message}")

    def drain_notes(self) -> list[str]:
        """Whatever was said before anyone was listening. Empties the list."""
        said, self.notes = self.notes, []
        return said

    # -- resolution

    def _find_callable(self, options: Sequence[str]) -> Callable[..., Any] | None:
        for name in options:
            found = getattr(self.raw, name, None)
            if callable(found):
                return found
        return None

    def _value(self, key: str, default: Any) -> Any:
        for name in self._VALUES[key]:
            if hasattr(self.raw, name):
                found = getattr(self.raw, name)
                return found() if callable(found) else found
        return default

    def signal(self, key: str) -> Any | None:
        """The engine's signal for `key`, or None when it does not publish one."""
        for name in self._SIGNALS[key]:
            found = getattr(self.raw, name, None)
            if found is not None and hasattr(found, "connect"):
                return found
        return None

    # -- state

    @property
    def recording(self) -> Any:
        return self._value("recording", None)

    @property
    def sinks(self) -> list[Any]:
        found = self._value("sinks", None)
        if found is None:
            self.complain(
                f"{type(self.raw).__name__} exposes no sinks "
                "(sinks / video_sinks); every view will stay black."
            )
            return []
        return list(found)

    @property
    def position(self) -> int:
        return int(self._value("position", 0) or 0)

    @property
    def duration(self) -> int:
        return int(self._value("duration", 0) or 0)

    @property
    def playing(self) -> bool:
        return bool(self._value("playing", False))

    @property
    def muted(self) -> bool:
        return bool(self._value("muted", False))

    @property
    def speed(self) -> float:
        return float(self._value("speed", 1.0) or 1.0)

    @property
    def seeking(self) -> bool:
        """Is a seek in flight right now? False for an engine that cannot say."""
        return bool(self._value("seeking", False))

    def stream_has_ended(self, index: int) -> bool:
        """Has this one screen's file run out while the others carry on (§6)?

        Silent about an engine that has no such call, unlike `_invoke`: this is a
        question with a safe answer, the signal that would have told us the same thing
        is optional too, and an engine written before §6 is not a fault to report on
        the viewer's transport bar.
        """
        call = self._calls.get("stream_has_ended")
        if call is None:
            return False
        try:
            return bool(call(int(index)))
        except Exception as exc:  # a stub, or an index it does not recognise
            self.complain(f"engine.stream_has_ended({index}) raised {exc!r}.")
            return False

    def stream_is_waiting(self, index: int) -> bool:
        """§2.1: has the timeline not reached this stream's content yet?

        Silent about an engine with no such call, exactly as `stream_has_ended` is: an
        engine that predates offsets simply has no waiting streams, and False is the
        true answer rather than a fault to put on the viewer's bar.
        """
        call = self._calls.get("stream_is_waiting")
        if call is None:
            return False
        try:
            return bool(call(int(index)))
        except Exception as exc:  # a stub, or an index it does not recognise
            self.complain(f"engine.stream_is_waiting({index}) raised {exc!r}.")
            return False

    # -- `docs/PLAYER_SOURCES.md`'s two optional abilities
    #
    # Asked once and used to decide whether a *control* exists, not whether a press is
    # dropped. A sync panel that moves nothing teaches the viewer that the player is
    # broken; no sync panel teaches them that this build cannot do it, which is true.

    def can_sync(self) -> bool:
        return self._calls.get("set_offset") is not None

    def can_choose_audio(self) -> bool:
        return self._calls.get("set_audio_source") is not None

    def offsets(self, count: int) -> list[int]:
        """§2.1's offsets, padded or cut to `count`. All zeroes for an older engine.

        The length is forced because everything downstream is index-aligned with the
        views: a list one short would make the sync panel's picker address a stream
        that is not there, and the failure would be an `IndexError` inside a paint.
        """
        raw = self._value("offsets", None)
        found: list[int] = []
        if raw is not None:
            try:
                found = [int(value) for value in raw]
            except (TypeError, ValueError) as exc:
                self.complain(f"engine.offsets() is not a list of numbers ({exc!r}).")
                found = []
        found = found[:count]
        return found + [0] * (count - len(found))

    @property
    def audio_index(self) -> int:
        """Which stream is heard, or -1 when none is.

        **An engine that cannot say is not assumed to be playing stream 0.** Every
        engine written before §1.2 is one of those, `SyncEngine` included, and the
        recording it was built from knows the answer: `Recording.master_index()` is
        the screen whose file the sidecar names in `"audio"`, which is exactly the
        one `SyncEngine` gave an audio output to.

        Taking 0 instead was not a cosmetic default. §1.3's Save writes this number
        into the new index's `"audio"`, so a recorder-made set whose sound is on
        screen 2 was re-saved as a set whose sound is on screen 1 - and opening the
        copy handed the sound *and* the clock to the other camera, silently. The
        fallback is read off the recording for that reason.
        """
        found = self._value("audio_index", None)
        if found is not None:
            return int(found)
        master = getattr(self.recording, "master_index", None)
        if not callable(master):
            return 0
        try:
            return int(master())
        except Exception as exc:  # noqa: BLE001 - a stub, or a broken screen list
            self.complain(f"recording.master_index() raised {exc!r}.")
            return 0

    # -- commands

    def _invoke(self, name: str, *args: Any) -> bool:
        call = self._calls.get(name)
        if call is None:
            self.complain(f"the engine has no {name}(); the request was dropped.")
            return False
        try:
            call(*args)
        except Exception as exc:  # an engine fault must not kill the key press
            self.complain(f"engine.{name}{args!r} raised {exc!r}.")
            return False
        return True

    def toggle(self) -> bool:
        return self._invoke("toggle")

    def step(self, frames: int) -> bool:
        return self._invoke("step", frames)

    def seek(self, milliseconds: int) -> bool:
        return self._invoke("seek", int(milliseconds))

    def skip(self, milliseconds: int) -> bool:
        """Relative seek. Falls back to an absolute one when the engine has no skip."""
        if self._calls.get("skip") is not None:
            return self._invoke("skip", int(milliseconds))
        return self.seek(max(0, self.position + int(milliseconds)))

    def set_speed(self, speed: float) -> bool:
        return self._invoke("set_speed", float(speed))

    def set_muted(self, muted: bool) -> bool:
        return self._invoke("set_muted", bool(muted))

    def set_volume(self, volume: float) -> bool:
        if self._calls.get("set_volume") is None:
            return False  # optional: a player with no volume control is still a player
        return self._invoke("set_volume", float(volume))

    def set_offset(self, index: int, milliseconds: int) -> bool:
        """§2.1. Never called unless `can_sync()`, which is why this is not silent."""
        return self._invoke("set_offset", int(index), int(milliseconds))

    def set_audio_source(self, index: int) -> bool:
        """§1.2. Never called unless `can_choose_audio()`, for the same reason."""
        return self._invoke("set_audio_source", int(index))

    def close(self) -> bool:
        return self._invoke("close")

    def release(self) -> None:
        """Let go of the files. Closing the player is not the same as stopping it.

        `SyncEngine` has no `close()`: the alias list above resolves the contract's
        `close` to its `stop`, which pauses every `QMediaPlayer` and stops the engine's
        own timer - and that is all. Nothing calls `setSource(QUrl())`, so after the
        last window has gone every decoder pipeline is still alive with its MP4 open,
        and the recorder this player was opened from may be asked to write over that
        file within the minute. A paused player is not a closed one.

        The aliases are deliberately left alone - `stop` is the right thing to call,
        and `close` is in `_REQUIRED`, so removing it would make every engine without
        one announce that its controls do nothing. This is the second half of the same
        shutdown instead.

        An engine with a real `release()` is asked and believed. Otherwise the players
        are found with `findChildren`, because `SyncEngine` builds each one parented to
        itself - which is a published Qt relationship rather than a private list this
        module would have no business reading. An engine that keeps them somewhere else
        has nothing found here and is no worse off than before.
        """
        direct = self._find_callable(("release", "dispose"))
        if direct is not None:
            try:
                direct()
                return
            except Exception as exc:
                self.complain(f"engine.release() raised {exc!r}; releasing by hand.")

        raw = self.raw
        if not isinstance(raw, QtCore.QObject):
            return
        try:
            players = raw.findChildren(QtMultimedia.QMediaPlayer)
        except Exception as exc:  # a stub engine, or a Qt build without the module
            self.complain(f"the engine's players could not be listed ({exc!r}).")
            return
        for player in players:
            try:
                player.stop()
                # This is the call that closes the file. Everything else here is
                # tidiness; this one is the reason the method exists.
                player.setSource(QtCore.QUrl())
                player.setAudioOutput(None)
                player.setVideoSink(None)
            except Exception as exc:
                self.complain(f"a player of this set would not let go ({exc!r}).")


# ------------------------------------------------------------------ geometry


def normalise_rects(raw: Any) -> list[tuple[int, int, int, int]]:
    """`layout_rects` in whatever shape it arrives, as a list of (x, y, w, h).

    A list of tuples, a list of `QRect`, or a mapping keyed by screen index are all
    accepted; anything else is reported and dropped, because an arrangement built from
    half-understood numbers would put a screen on the wrong side, which is the one thing
    this feature exists to get right.
    """
    if raw is None:
        return []
    items: list[Any]
    if isinstance(raw, dict):
        items = [raw[key] for key in sorted(raw)]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        _report(f"layout_rects is a {type(raw).__name__}; expected a list or a mapping.")
        return []

    rects: list[tuple[int, int, int, int]] = []
    for index, item in enumerate(items):
        if isinstance(item, QtCore.QRect):
            rects.append((item.x(), item.y(), item.width(), item.height()))
            continue
        if isinstance(item, (list, tuple)) and len(item) == 4:
            try:
                x, y, width, height = (int(round(float(value))) for value in item)
            except (TypeError, ValueError):
                _report(f"layout_rects[{index}] = {item!r} is not four numbers.")
                return []
            rects.append((x, y, width, height))
            continue
        _report(f"layout_rects[{index}] = {item!r} is not a rectangle.")
        return []
    return rects


def recording_rects(recording: Any) -> list[tuple[int, int, int, int]]:
    """`layout_rects`, whether the recording exposes it as a value or as a method.

    `player.engine.Recording.layout_rects(scale)` is a method taking a multiplier, and
    it is asked here for the unscaled set (`1.0`): the scaling that matters is the one
    that makes the set fit the *viewer's* screens, and `placements` works that out from
    the whole set at once rather than one rectangle at a time. A recording that exposes
    the rectangles as a plain attribute is read directly.
    """
    found = getattr(recording, "layout_rects", None)
    if not callable(found):
        return normalise_rects(found)
    try:
        return normalise_rects(found(1.0))
    except TypeError:
        pass  # not the (scale) signature after all - try it with no argument
    try:
        return normalise_rects(found())
    except TypeError as exc:
        _report(f"recording.layout_rects() takes neither 1.0 nor nothing ({exc}).")
        return []


def viewer_area() -> QtCore.QRect:
    """Where the set may be put: the available area of every screen, united."""
    area = QtCore.QRect()
    for screen in QtGui.QGuiApplication.screens():
        area = area.united(screen.availableGeometry())
    if area.isEmpty():
        _report("no screen reported an available area; falling back to 1280x720.")
        return QtCore.QRect(0, 0, 1280, 720)
    return area


def screen_areas() -> list[QtCore.QRect]:
    """The available area of each screen, separately."""
    return [screen.availableGeometry() for screen in QtGui.QGuiApplication.screens()]


def preferred_area(screens: Sequence[QtCore.QRect]) -> QtCore.QRect:
    """Where a window goes when nothing else points at a screen: the primary one.

    §6 sends a stranded window "to the primary one", and `screens[0]` is not that.
    Qt documents `QGuiApplication.screens()` as "a list of all the screens" and
    promises nothing about its order; only `primaryScreen()` answers the question. On
    Windows index 0 usually *is* the primary, which is why the assumption survived
    unnoticed here - it is wrong only on the desk nobody in this project has, which is
    exactly the desk the rule was written for.

    The primary is matched **by rectangle** rather than taken from Qt and used,
    because every caller is handed its screens as rectangles: the tests replace
    `screen_areas` wholesale to invent a desk this machine has not got, and a primary
    read straight from Qt would then be an 800x600 offscreen screen on none of those
    desks. A screen list Qt does not recognise therefore falls back to the first
    entry - the old behaviour, and the only honest answer about an invented desk.
    """
    if not screens:
        return QtCore.QRect()
    screen = QtGui.QGuiApplication.primaryScreen()
    if screen is not None:
        wanted = screen.availableGeometry()
        for area in screens:
            if area == wanted:
                return QtCore.QRect(area)
    return QtCore.QRect(screens[0])


def host_screen(
    box: QtCore.QRect | None, screens: Sequence[QtCore.QRect]
) -> QtCore.QRect:
    """Which single screen a window belongs to: the one showing most of `box`.

    The rule the whole module places windows by, pulled out of `transport_spot` so
    that the arrange menu's default monitor, the rescue after `screenRemoved` and the
    bar itself cannot answer it three different ways. Never the union of several
    screens - see `transport_spot` for the account of what that cost.

    A `box` no screen shows any of - which is the state a stranded window is in, and
    the state an unplaced window reports before it has been shown - falls to
    `preferred_area`, because §6 names the primary monitor for that case and the
    first entry of a screen list is not it.

    An empty screen list gives an empty rectangle, which every caller here treats as
    "there is nowhere to put this": a desk with no screens is a state Qt reports
    during a display change, and guessing a rectangle for it would place a window at
    coordinates that mean nothing a moment later.
    """
    if not screens:
        return QtCore.QRect()
    host = preferred_area(screens)
    if box is None or box.isEmpty():
        return host
    # Zero, not -1: a screen showing none of the box must not displace the primary,
    # or a window off the desk entirely would land on whichever screen Qt listed
    # first instead of the one §6 names.
    best = 0
    for area in screens:
        overlap = area.intersected(box)
        covered = overlap.width() * overlap.height()
        if covered > best:
            best, host = covered, area
    return host


def reachable(box: QtCore.QRect, screens: Sequence[QtCore.QRect]) -> bool:
    """Is enough of `box` on a real screen for somebody to use the window in it?

    Not "does it touch a screen at all". These windows are frameless: there is no
    title bar hanging into the visible area to drag them back by, so a window with a
    sliver showing is one that cannot be moved, resized or closed. `RESCUE_MIN_VISIBLE`
    is the share that has to be on one screen - on one, not spread over two, because
    half of a window on each of two monitors is reachable and half of it off the desk
    on both is not.
    """
    if box.isEmpty():
        return True  # nothing to rescue; a zero-sized window is a different problem
    whole = box.width() * box.height()
    for area in screens:
        overlap = area.intersected(box)
        if overlap.width() * overlap.height() >= whole * RESCUE_MIN_VISIBLE:
            return True
    return False


def rescue_into(box: QtCore.QRect, screens: Sequence[QtCore.QRect]) -> QtCore.QRect:
    """`box` where it is, or moved onto a screen that is still there (§6).

    Called after `screenRemoved`, which is the moment Qt leaves windows at coordinates
    belonging to a monitor that has been unplugged. The window keeps its size where
    the remaining screen can hold it and is shrunk where it cannot, because
    `layouts.clamp_into` does both - a window wider than the screen it was moved to
    would have its edges, and therefore its resize handles, off that screen as well.

    `host_screen` chooses which screen: the one already showing most of the window,
    so a window straddling the lost monitor and a live one slides onto the live one
    rather than being flung at the primary.
    """
    if not screens or reachable(box, screens):
        return QtCore.QRect(box)
    return layouts.clamp_into(QtCore.QRect(box), host_screen(box, screens))


def monitor_label(number: int, area: QtCore.QRect) -> str:
    """`Monitor 2 — 1440×2560`, for the menu that asks which monitor to tile on.

    The size is in the name because two monitors of the same make report the same
    everything to Qt, and "Monitor 1 / Monitor 2" over an identical pair tells the
    viewer nothing about which of the two in front of them is which. The number is
    1-based, as every other screen name this module produces is.
    """
    return _("Monitor {number} — {width}×{height}").format(
        number=number, width=area.width(), height=area.height()
    )


def transport_spot(
    boxes: Sequence[QtCore.QRect],
    screens: Sequence[QtCore.QRect],
    size: QtCore.QSize,
) -> QtCore.QRect:
    """Where the transport bar goes: the bottom of a real screen, never between two.

    The obvious placement - the bottom centre of the area the views span - is wrong the
    moment the desk is not a single row. With one screen above and to the left of the
    other, the bottom centre of their union falls in the empty corner where no screen
    is, and the bar is invisible and unreachable. That is what the user hit: every
    control was there, 760x52 at 1620,2480, on no monitor at all.

    So pick one screen - the one showing most of the first view, which is the screen
    whose audio is playing - and sit at the bottom of that one, clamped inside it.
    """
    if not screens:
        return QtCore.QRect(QtCore.QPoint(0, 0), size)

    host = host_screen(boxes[0] if boxes else None, screens)

    width = min(size.width(), host.width())
    height = min(size.height(), host.height())
    x = host.x() + (host.width() - width) // 2
    y = host.y() + host.height() - height - ARRANGE_MARGIN
    # A short screen can leave no room for the margin; being on screen beats the gap.
    y = max(host.y(), min(y, host.y() + host.height() - height))
    return QtCore.QRect(x, y, width, height)


def placements(
    rects: Sequence[tuple[int, int, int, int]], area: QtCore.QRect
) -> list[QtCore.QRect]:
    """Scale the recorded desktop rectangles down into `area`, keeping the arrangement.

    The whole set is scaled by one factor and centred, so a screen that was to the right
    of another is still to the right of it, and a tall screen beside a wide one stays
    tall. Nothing is ever scaled *up*: a recording of two small screens opens at its own
    size rather than being blown up to fill the viewer's desk.
    """
    if not rects:
        return []
    left = min(rect[0] for rect in rects)
    top = min(rect[1] for rect in rects)
    right = max(rect[0] + rect[2] for rect in rects)
    bottom = max(rect[1] + rect[3] for rect in rects)
    span_x = max(1, right - left)
    span_y = max(1, bottom - top)

    usable_w = max(1, area.width() - 2 * ARRANGE_MARGIN)
    usable_h = max(1, area.height() - 2 * ARRANGE_MARGIN)
    scale = min(1.0, usable_w / span_x, usable_h / span_y)

    offset_x = area.x() + (area.width() - span_x * scale) / 2
    offset_y = area.y() + (area.height() - span_y * scale) / 2

    out: list[QtCore.QRect] = []
    for x, y, width, height in rects:
        out.append(
            QtCore.QRect(
                int(round(offset_x + (x - left) * scale)),
                int(round(offset_y + (y - top) * scale)),
                max(MIN_VIEW_WIDTH, int(round(width * scale))),
                max(MIN_VIEW_HEIGHT, int(round(height * scale))),
            )
        )
    return out


def screen_titles(recording: Any, count: int) -> list[str]:
    """A name per view: the recording's own, the sidecar's screens, or "Screen N".

    Only the last two are translated. A name the recording carries is the viewer's own
    word for that screen and is handed back untouched, in whatever language they wrote
    it; "Screen 2" is this module's invention and is said in the language in force.
    Called again from `PlayerWindows.retranslate`, which is why it builds the names
    rather than reading a list it built once.
    """
    titles = getattr(recording, "titles", None)
    if isinstance(titles, (list, tuple)) and len(titles) >= count:
        return [str(title) for title in titles[:count]]

    screens = getattr(recording, "screens", None)
    if isinstance(screens, (list, tuple)) and len(screens) >= count:
        names: list[str] = []
        for position in range(count):
            entry = screens[position]
            if isinstance(entry, dict):
                index = entry.get("monitor")
                primary = bool(entry.get("primary"))
            else:
                index = getattr(entry, "monitor", None)
                primary = bool(getattr(entry, "primary", False))
            number = index + 1 if isinstance(index, int) else position + 1
            template = (
                _("Screen {number} - primary") if primary else _("Screen {number}")
            )
            names.append(template.format(number=number))
        return names

    return [
        _("Screen {number}").format(number=position + 1) for position in range(count)
    ]


# -------------------------------------------------------------- the windows


#: Players opened from inside another player, held so that they outlive the one that
#: opened them. §1 hands the new windows back to a caller that keeps them - `open.run`
#: keeps a local for the length of the event loop, `ui/app.py` keeps `self._player` -
#: and a player opened from the Open menu has no such caller: the host's reference is
#: to the *old* object.
#:
#: It used to be `PlayerWindows._successor` alone, and that chained every player to the
#: one before it. Measured on the two-screen set in `scratch/twofinal`, ten opens from
#: inside the player left **eleven** `PlayerWindows`, eleven `TransportBar` and
#: twenty-two `ScreenView` alive, growing by one player per open. A list here is one
#: entry long instead: `_replace_with` adds the new player and `close_all` takes the
#: old one out, so only the player in front of the viewer is held.
_OPEN_PLAYERS: list[Any] = []


def _open_players() -> list[Any]:
    """The players this module is holding. For tests and for reading in a debugger."""
    return list(_OPEN_PLAYERS)


def _hold_player(player: Any) -> None:
    """Keep `player` alive until it says it has closed."""
    if not any(held is player for held in _OPEN_PLAYERS):
        _OPEN_PLAYERS.append(player)


def _forget_player(player: Any) -> None:
    """Let go of `player`. Never raises: a player not held is the ordinary case."""
    for position, held in enumerate(_OPEN_PLAYERS):
        if held is player:
            del _OPEN_PLAYERS[position]
            return


class PlayerWindows(QtCore.QObject):
    """All the screen views plus one transport bar: §5's separate-windows mode.

    One object owns the set, so every rule about the set is a connection here and not a
    convention spread over the windows, and so the keyboard works whichever window has
    focus: every window has this object as an event filter and the shortcuts are
    handled in one place.

    What §5 added, and where the policy lives:

    * the **arrange menu** - the four schemes, which monitor to tile on, one window per
      monitor and the mapping that goes with it, and the way back to the desktop
      arrangement. The bar carries the button; the menu is built here, fresh on every
      press, because what may be in it changes with the display list and with which
      views are open;
    * **closing** - index 0 means stop, any other view hides and playback carries on;
    * **`screenRemoved`** - every window that can no longer be reached is moved onto a
      screen that is still there, and the transport says which;
    * **the engine's two §6 states** - `seeking_changed` dims every view, `stream_ended`
      badges one.

    `PlayerWindows(engine, parent=None)` and `show()` / `close_all()` / `closed` are
    unchanged: `player.open` builds this by name and signature, and a one-stream
    recording opens exactly as it did - one view, no arrange button, no menu.
    """

    closed = QtCore.Signal()
    #: Which of §5's four schemes is in force, so the caller can store `player_tiling`.
    #: Only ever one of `layouts.TILINGS`: the desktop arrangement is this player's
    #: default rather than a scheme, and `config` would reject `""` and write the
    #: default back, which would turn "no scheme chosen" into a scheme.
    tiling_changed = QtCore.Signal(str)
    #: §1: another recording was opened from inside this player, and this object is
    #: about to close. The argument is the new window layer, already shown. A host
    #: that keeps a reference to the player - `ui/app.py` does - can follow it;
    #: `_OPEN_PLAYERS` holds it meanwhile, so a host that does not is no worse off.
    opened = QtCore.Signal(object)

    def __init__(self, engine: Any, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self._engine = _Engine(engine)
        self._closing = False
        #: The player this one opened, while this one is still open. `_replace_with`
        #: is the only thing that sets it and `close_all` is the only thing that
        #: clears it. It is no longer what keeps the new player alive - `_OPEN_PLAYERS`
        #: is - because a reference that lived on the old player chained every player
        #: of a session to the one before it.
        self._successor: Any = None
        #: §1.1's position carry while it is in flight: the new clock, where it is to
        #: go, and whether it was running. Cleared by `_carry_landed`.
        self._carry: tuple[_Engine, int, bool] | None = None
        #: The one wire this object keeps after `close_all` - §1.1's carry, into the
        #: clock of the player that is replacing this one. Held apart from `_bonds`
        #: for exactly that reason; `_drop_carry` is what takes it out.
        self._carry_bond: tuple[Any, Any] | None = None
        #: The scheme last applied, or "" for "as the screens sat on the desktop" -
        #: which is what `arrange()` does and what this player has always opened with.
        #: `restore_arrangement` re-applies whichever of the two it is, so the menu
        #: entry works before any scheme has been chosen.
        self._tiling = ""
        #: True while `one_per_monitor` is the arrangement in force. A third state
        #: beside `_tiling`, not a value of it: `tiling_changed` promises to carry
        #: only one of `layouts.TILINGS`, and a caller storing `player_tiling` would
        #: otherwise be handed a layout id that `config` rejects. `restore_arrangement`
        #: and the menu's tick both read it, so «Вернуть раскладку» puts the windows
        #: back where the *last choice* put them rather than where the last scheme did.
        self._one_per_monitor = False
        #: Which monitor the arrangement is put on, or None for "the viewer has not
        #: said", in which case `preferred_area` answers - the primary, as §6 has it,
        #: and not whichever screen Qt happened to list first. An index into
        #: `screen_areas()`, re-checked every time it is used because a monitor can be
        #: unplugged between the choice and the next press.
        self._tiling_monitor: int | None = None
        #: Stream -> monitor for `one_per_monitor`, or None for "not chosen yet", in
        #: which case `layouts.best_pairing` decides. Kept as the user's own once they
        #: have changed it, because §5 makes the mapping theirs.
        self._pairing: tuple[int, ...] | None = None
        #: The views the viewer has closed. They are still playing and still connected
        #: to their sink; only the window is away. §5: hiding is not closing.
        self._hidden: set[int] = set()
        #: Every wire this object runs into something that outlives it - `i18n`, the
        #: application, the engine - as (signal, slot), so that `close_all` can take
        #: them all out again. Only the ones that *leave* this object are listed: a
        #: connection between two widgets it owns dies when they are deleted. `_bind`
        #: is the only thing that appends to it and `_unbind` the only thing that
        #: empties it.
        self._bonds: list[tuple[Any, Any]] = []
        #: §5's swap, in the separate-windows half: True once the viewer has asked for
        #: the two windows to change places. It is read where a box list is paired
        #: with the views - `arrange` and `apply_tiling` - so that re-applying an
        #: arrangement keeps the swap instead of undoing it. One screen per monitor
        #: does not use it: there the mapping *is* the arrangement, and `swap_windows`
        #: reverses `_pairing` instead.
        self._swapped = False

        recording = self._engine.recording
        rects = recording_rects(recording)
        sinks = self._engine.sinks
        if not rects and sinks:
            self._engine.complain(
                "the recording has no usable layout_rects; the views will be laid out "
                "in a row instead, left to right in file order."
            )
            rects = [(index * 1920, 0, 1920, 1080) for index in range(len(sinks))]
        if rects and sinks and len(rects) != len(sinks):
            self._engine.complain(
                f"the recording lists {len(rects)} screens but the engine has "
                f"{len(sinks)} sinks; {min(len(rects), len(sinks))} views will be shown."
            )
        count = min(len(rects), len(sinks)) if sinks else len(rects)

        self.rects: list[tuple[int, int, int, int]] = list(rects[:count])
        titles = screen_titles(recording, count)
        #: The name this object last gave each view. Compared against the view's own
        #: in `retranslate`, so that a name a caller set with `set_label` is never
        #: replaced by a generated one. Without it, a language switch would rename
        #: every view back to "Screen 2".
        self._given_titles: list[str] = list(titles)

        self.views: list[ScreenView] = []
        for index in range(count):
            sink = sinks[index] if index < len(sinks) else None
            view = ScreenView(titles[index], sink)
            view.clicked.connect(self._engine.toggle)
            # The index is bound as a default argument, not captured: a closure over
            # the loop variable would hand every view the last index, and the bug that
            # makes is "closing the second window ends the session", which is the one
            # behaviour §5 is changing.
            view.closed.connect(lambda which=index: self._view_closed(which))
            view.files_dropped.connect(self.open_paths)
            view.installEventFilter(self)
            self.views.append(view)
        if not self.views and getattr(self._engine.recording, "screens", None):
            # Only when the recording named screens and none of them became a view. A
            # recording with no screens at all is the player standing open with
            # nothing loaded - `open.empty_player`, built on purpose when there is
            # nothing in the output folder - and complaining at somebody who has not
            # chosen a file yet is the defect, not the state.
            self._engine.complain(
                "nothing to play: no screens were resolved from the engine."
            )

        self.transport = TransportBar()
        self.transport.installEventFilter(self)
        self.transport.closed.connect(self._transport_closed)
        self.transport.play_pause_requested.connect(self._engine.toggle)
        self.transport.step_requested.connect(self._engine.step)
        self.transport.seek_requested.connect(self._engine.seek)
        self.transport.speed_requested.connect(self._engine.set_speed)
        self.transport.volume_requested.connect(self._engine.set_volume)
        self.transport.mute_requested.connect(self._toggle_mute)
        self.transport.arrange_requested.connect(self._open_arrange_menu)
        self.transport.close_requested.connect(self.close_all)
        self.transport.set_arrange_available(len(self.views) > 1)
        # `docs/PLAYER_SOURCES.md`'s four controls. The bar carries them; every
        # decision about what they do is here, the way the arrange menu already works.
        self.transport.open_requested.connect(self._open_open_menu)
        self.transport.files_dropped.connect(self.open_paths)
        self.transport.audio_source_requested.connect(self.set_audio_source)
        self.transport.offset_requested.connect(self.set_offset)
        # What those controls are *made of* - the stream names, the offsets, the frame
        # rate - is handed over by `_refresh_source_controls` further down, once the
        # engine's §6 signals are connected, so there is one place that does it and
        # `retranslate` can call the same one.

        # The mute state is held here, not read back from the engine: `SyncEngine` can
        # be told to mute but publishes no `muted`, and a button that can only ever be
        # pressed on is worse than no button.
        self._muted = self._engine.muted

        self.transport.set_duration(self._engine.duration)
        self.transport.set_position(self._engine.position)
        self.transport.set_playing(self._engine.playing)
        self.transport.set_speed(self._engine.speed)
        self.transport.set_muted(self._muted)

        # From here on everything the resolver or the engine complains about has
        # somewhere to be seen. Whatever was said while the bar was being built - a
        # missing method, an engine with no sinks - is said now.
        self._engine.listener = self._show_notice
        for note in self._engine.drain_notes():
            self._show_notice(note)
        for view in self.views:
            if view.setup_problem:
                self._show_notice(view.setup_problem)

        # `_bind`, not `connect`: every one of these wires runs from the engine into
        # this object or into its bar, and the engine is not this object's to own. See
        # `close_all`, which takes them all out again.
        self._bind(self._engine.signal("problem"), self._engine_said)
        self._bind(self._engine.signal("finished"), self._engine_finished)

        self._bind(self._engine.signal("position"), self.transport.set_position)
        self._bind(self._engine.signal("duration"), self.transport.set_duration)
        self._bind(self._engine.signal("playing"), self.transport.set_playing)

        # §6's two states. Both signals are optional - an engine written before them
        # leaves the views exactly as this player always showed them - and both are
        # also *asked* once here, because a view is built before it is connected and a
        # stream that ran out a second ago would otherwise never be badged.
        self._bind(self._engine.signal("seeking"), self._seeking_changed)
        self._bind(self._engine.signal("stream_ended"), self._stream_ended)
        self._seeking_changed(self._engine.seeking)
        for index in range(len(self.views)):
            if self._engine.stream_has_ended(index):
                self._stream_ended(index)

        # §2.1's waiting state and §1.2's audio source, asked once for the same
        # reason: the engine may already be in either of them - a set saved with an
        # offset opens with one stream waiting - and a signal only announces changes.
        self._bind(self._engine.signal("stream_waiting"), self._stream_waiting)
        self._bind(self._engine.signal("offsets"), self._offsets_changed)
        # Through `_audio_announced` rather than straight onto the bar: §1.2 makes
        # silence a state of this player and not of the engine, so an engine saying
        # "the sound is on screen 2" while the viewer has muted must not light the
        # chip up with screen 2. Measured before this: it did.
        self._bind(self._engine.signal("audio"), self._audio_announced)
        for index in range(len(self.views)):
            if self._engine.stream_is_waiting(index):
                self._stream_waiting(index, True)
        self._refresh_source_controls()

        # §6's displays. `screenRemoved` is the only one of Qt's three display signals
        # that can strand a window: `screenAdded` gives more room and
        # `primaryScreenChanged` moves nothing. It is connected on the application
        # object rather than on a screen, because the screen it is about is the one
        # that has gone.
        application = QtGui.QGuiApplication.instance()
        if application is not None and hasattr(application, "screenRemoved"):
            self._bind(application.screenRemoved, self._screen_removed)

        #: Runs whatever the engine publishes, and for two reasons. It is the only
        #: source of a clock for an engine with no position signal; and `SyncEngine`,
        #: which has one, emits a duration only from its own tick, which only runs while
        #: something is playing - so without this the bar would open reading
        #: "00:00 / 00:00" with a dead scrubber until the viewer pressed play. Measured
        #: on this machine, there is no cumulative drift to chase, so the clock is the
        #: only thing needing a refresh at all and ten times a second is plenty.
        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._refresh)
        self._poll.start()

        self._bind(i18n.notifier.changed, self.retranslate)

    # -- what this object is wired to, and how it lets go ------------------

    def _bind(self, signal: Any, slot: Any) -> None:
        """Connect to something that outlives this player, and remember the wire.

        `i18n.notifier`, the application and the engine all go on existing after the
        last window of this player has closed, and a connection into one of them
        holds a reference to a bound method of this object - which holds the object,
        which holds its views and its bar. That is half of why ten opens from inside
        the player left eleven players alive; the other half is `close_all` not
        deleting the windows it closes.

        A `None` signal is not an error and is not recorded: every one of the
        engine's §6 and §2.1 signals is optional, and `_Engine.signal` answers None
        for an engine written before them.
        """
        if signal is None:
            return
        try:
            signal.connect(slot)
        except (RuntimeError, TypeError) as exc:
            # A stub engine with an attribute that looks like a signal and is not.
            # Said rather than raised: the player still works without the state this
            # signal would have announced, which is what `_Engine.signal` is for.
            _report(f"a signal could not be connected ({type(exc).__name__}: {exc}).")
            return
        self._bonds.append((signal, slot))

    def has_closed(self) -> bool:
        """True once `close_all` has run. Every control the arrange menu offers asks.

        §5's menu is popped with `WA_DeleteOnClose` and parented to the transport, so
        a menu whose player has gone is deleted *with* the transport - one turn of
        the event loop later. Between the close and that turn the menu is still on
        screen, still takes a press, and every entry on it used to act. Measured by
        calling each one after `close_all` on a two-screen set: «Показать второй
        экран» put a frameless video window back on the desk and left it there, and
        «Друг над другом», «Вернуть раскладку» and the stream -> monitor entries all
        moved windows that had just been closed, two of them answering True.

        The guard is on each entry's own method and not on the menu, because the menu
        is only the commonest way in: `ui/app.py` holds a player, `opened` hands one
        out, and a host calling any of these on a player it has closed is the same
        bug with no menu in it.
        """
        return self._closing

    def _unbind(self) -> None:
        """Take every wire out again. Called by `close_all`, before anything is deleted.

        **Disconnect first, delete after.** Deleting a Qt object while a signal is in
        flight is the class of bug this package has already paid for twice, so the
        wires come out while every object on both ends of them is still valid, and
        `deleteLater` runs afterwards.

        A wire that has already gone - the other end deleted, Qt having dropped the
        connection with it - raises `RuntimeError` or `TypeError` here and means
        nothing: there is no connection left to remove and no viewer to tell.
        """
        for signal, slot in self._bonds:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                # The other end has been deleted and Qt took the connection with it.
                # There is nothing left to remove and nobody to tell.
                pass
        self._bonds.clear()

    # -- the language ------------------------------------------------------

    def retranslate(self) -> None:
        """Re-name the views. Every window on the bar looks after its own text.

        What belongs to this object and to nothing else is the *name* on each view,
        because `screen_titles` is where a generic "Screen 2" is invented. It is asked
        again rather than remembered, so a recording that named its own screens gets
        those names back unchanged and a set that did not gets them in the language
        now in force.

        A name that has not changed is not written back: `set_label` wakes the label
        and restarts its fade, and a language switch must not flash a caption onto
        every window of a recording that named its screens itself.

        Nor is a view renamed when somebody else has renamed it. `_given_titles` is
        what this object last called each view; a view whose name no longer matches it
        was renamed by a caller, and that caller's word wins over a generated one for
        as long as the player is open.

        Nothing else here needs saying again. Every window on the bar looks after its
        own words - including the seek veil and the ended badge, which `ScreenView`
        re-reads - and the arrange menu is built fresh on every press, so it is in the
        language in force at the moment it opens rather than at the moment the player
        did.
        """
        if self._closing or not self.views:
            return
        titles = screen_titles(self._engine.recording, len(self.views))
        for index, (view, title) in enumerate(zip(self.views, titles)):
            if view.title() != self._given_titles[index]:
                continue  # renamed from outside; not ours to take back
            if title != view.title():
                view.set_label(title)
            self._given_titles[index] = title
        # The names are also what §1.2's audio menu and §2.2's summary call the
        # streams, and neither is a window that can re-read them for itself.
        self._refresh_source_controls()

    # -- showing and placing ----------------------------------------------

    def show(self) -> None:
        """Arrange the views, put them on screen, and give the transport the keyboard.

        `restore_arrangement`, not `arrange`: a host that has already said how this
        set is to sit - `one_per_monitor` from §2.1's dialog, or a scheme restored
        from `player_tiling` - must not have it thrown away and replaced by the
        desktop mock-up the moment the windows are shown. With nothing chosen the two
        are the same call, which is what a one-stream recording and every existing
        caller get.

        Refused once the player has closed, for `has_closed`'s reason and one of its
        own: the windows are deleted by then, so this would not put a player back on
        the desk - it would raise out of `view.show()` on a C++ object that is gone.
        """
        if self.has_closed():
            return
        self.restore_arrangement()
        # `visible_views`, not `views`: `show()` can be called again after the viewer
        # has put a screen away - by a host re-opening the set, or by a test - and a
        # window the viewer closed must not come back because something was shown.
        for view in self.visible_views():
            view.show()
        self.transport.show()
        self.transport.raise_()
        # The transport may take focus, unlike the recorder's chrome: Space and the
        # arrow keys have to land somewhere.
        self.transport.activateWindow()
        self.transport.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def arrange(self) -> None:
        """Lay the views out as the screens really sat, scaled into one real monitor.

        This is the player's default and §5 leaves it alone; it is also the way back
        from a scheme, which is why it clears the arrangement state rather than only
        moving windows. Nothing here is told to the engine: the whole method is
        `setGeometry`, so the position, the paused state and both decoders survive it
        untouched.

        **One monitor, not the union of all of them.** This used to scale the set into
        `viewer_area()`, every screen's available area united, and then centre it
        there - which is the placement §6 forbids by name and `transport_spot`'s
        docstring records the cost of. On an L-shaped or unequal-height desk the empty
        region of that union is on no monitor at all, and a frameless window centred
        into it has no title bar to drag back by. Measured on the user's own desk
        (2560x1400 and a portrait 1440x2560 beside it) with a recording of two stacked
        1920x1080 screens: the second view landed at 1040,1280 with 20.8 % of itself on
        any one monitor, and `rescue_windows()` - the thing that exists to undo this
        state - reported it the moment it was asked. So the set is scaled into
        `tiling_area()`, which is one screen: the monitor the viewer chose from the
        arrange menu, or the primary one.

        The cost is stated rather than hidden: on a two-monitor desk the set now opens
        smaller than it did, inside one screen, because that is the only size at which
        every window of it can be reached. §5's **One screen per monitor** is the
        arrangement that uses the whole desk, and §2.2 scores it 1.000 there and
        recommends it.

        `rescue_into` on the way out is the belt beside those braces: `placements`
        holds every view to `MIN_VIEW_WIDTH` x `MIN_VIEW_HEIGHT`, so a recording of
        many screens on a small monitor can still produce a box reaching past the area
        it was scaled into, and an arrangement nobody can touch is not an arrangement.
        """
        if self.has_closed() or not self.views:
            return
        area = self.tiling_area()
        if area.isEmpty():
            # No screen at all - a state Qt reports during a display change. There is
            # nowhere real to place into, so the old union is used: a window at
            # coordinates that mean nothing beats no window.
            area = viewer_area()
        screens = screen_areas()
        boxes = placements(self.rects, area)
        if len(boxes) != len(self.views):
            _report(
                f"arrange(): {len(boxes)} rectangles for {len(self.views)} views; "
                "the extra views are left where they are."
            )
        # `_in_placement_order`: §7's `S` is a swap of the two windows' places, so it
        # is the pairing of views to boxes that it changes, and re-arranging has to
        # keep it rather than undo it.
        order = self._in_placement_order(self.views)
        moved = {id(view) for view, _box in zip(order, boxes)}
        for view, box in zip(order, boxes):
            view.setGeometry(rescue_into(box, screens) if screens else box)
        # In *view* order, not placement order: `_place_transport` follows the first
        # view, which is the screen whose audio is playing, and a swap must not move
        # the bar to the other window.
        placed = [view.geometry() for view in self.views if id(view) in moved]
        self._tiling = ""
        self._one_per_monitor = False
        self._place_transport(placed, area)

    def _place_transport(
        self, boxes: Sequence[QtCore.QRect], area: QtCore.QRect | None = None
    ) -> None:
        """Put the bar at the bottom of one real screen, sized to the room there is.

        `boxes` is where the views have just been put; the bar follows the first of
        them, which is the screen whose audio is playing. `area` is only used for the
        bar's width and defaults to the whole desk - the width is a maximum, not a
        placement, and `transport_spot` does the placing.
        """
        room = viewer_area() if area is None else area
        width = max(
            self.transport.minimumWidth(),
            min(TRANSPORT_MAX_WIDTH, room.width() - 2 * ARRANGE_MARGIN),
        )
        # Anchored by its bottom edge, not its top: the bar is taller while a notice is
        # showing, and the controls must not move out from under the pointer when one
        # appears or goes away.
        height = self.transport.height() or TRANSPORT_HEIGHT
        spot = transport_spot(boxes, screen_areas(), QtCore.QSize(width, height))
        self.transport.resize(spot.width(), spot.height())
        self.transport.move(spot.x(), spot.y())

    # -- §5: the arrangements ----------------------------------------------

    def tiling(self) -> str:
        """The scheme in force, or "" while the windows sit as the desktop did."""
        return self._tiling

    def pairing(self) -> tuple[int, ...]:
        """Stream -> monitor for `one_per_monitor`, as it stands now.

        The user's own once they have changed it; otherwise `layouts.best_pairing`,
        which is what the chooser's card was scored against, so the windows land where
        the card promised. An empty tuple when there are not enough monitors to ask the
        question - `layouts.best_pairing` raises rather than guessing, and this is the
        caller that would otherwise stack two windows on one screen.
        """
        areas = screen_areas()
        streams = self._streams()
        if len(areas) < len(streams) or not streams:
            return ()
        checked = self._checked_pairing(self._pairing, areas, streams)
        if checked is not None:
            return checked
        return layouts.best_pairing(areas, streams)

    @staticmethod
    def _checked_pairing(
        order: Sequence[int] | None,
        areas: Sequence[QtCore.QRect],
        streams: Sequence[layouts.Stream],
    ) -> tuple[int, ...] | None:
        """A stream -> monitor mapping that is really one, or None.

        Three ways a mapping arriving from a menu built against an older display list
        can be wrong, and each of them places a window where nobody can watch it: the
        wrong number of entries, a monitor that is not there, and two streams on one
        screen. `layouts` raises on all three rather than clipping them into something
        plausible; here they only mean "fall back to the best arrangement", so they are
        checked instead of caught - an exception is not a control flow for a menu press.
        """
        if order is None or len(order) != len(streams):
            return None
        if any(not 0 <= int(where) < len(areas) for where in order):
            return None
        if len(set(int(where) for where in order)) != len(order):
            return None
        return tuple(int(where) for where in order)

    def _streams(self) -> list[layouts.Stream]:
        """This recording as `layouts` wants it: index, name, native size.

        The size comes from the recorded desktop rectangles, which is what the
        recording really holds - `layouts.best_pairing` reads it to decide which
        monitor shows a screen at its own size, and a portrait screen paired by area
        instead of by shape is the mistake that module's docstring names.
        """
        return [
            layouts.Stream(index, view.title(), (rect[2], rect[3]))
            for index, (view, rect) in enumerate(zip(self.views, self.rects))
        ]

    def tiling_area(self) -> QtCore.QRect:
        """The monitor the arrangement is put on, as a usable rectangle.

        The four schemes and the desktop arrangement of `arrange` both land here, so
        that "which monitor" is one answer rather than two that can disagree.

        Re-read from the live screen list on every call and not stored, because a
        monitor can be unplugged between the viewer choosing it and the next press of
        the menu; a stored rectangle would then tile two windows onto a screen that is
        not there, which is the one failure §6 names by name.

        With no monitor chosen the answer is `preferred_area` - the primary - and not
        the first screen Qt lists. That is the state the player opens in, so it is the
        one that decides where an ordinary two-screen recording appears.
        """
        areas = screen_areas()
        if not areas:
            return QtCore.QRect()
        if self._tiling_monitor is None:
            return preferred_area(areas)
        if 0 <= self._tiling_monitor < len(areas):
            return areas[self._tiling_monitor]
        # The chosen monitor has gone. The one showing most of the first view is the
        # same answer `transport_spot` gives, so the bar and the windows agree.
        first = self.views[0].geometry() if self.views else None
        return host_screen(first, areas)

    def set_tiling_monitor(self, index: int) -> None:
        """Choose which monitor the arrangement sits on, and move it there.

        Re-applied rather than only remembered: the entry is in a menu of
        arrangements, and a monitor chosen with nothing moving reads as a control that
        did nothing.

        `restore_arrangement`, not `apply_tiling`, and unconditionally. The old code
        re-applied only `self._tiling`, which is `""` until a scheme has been picked -
        so the entry did exactly nothing in the state the player opens in, which is
        the state a viewer meets it in. Whatever is in force is re-applied on the new
        monitor, and the desktop arrangement is one of the things in force.

        **One screen per monitor is left behind by this.** It is the one arrangement
        that is about all the monitors at once, so "put the windows on monitor 2" and
        "give every window a monitor of its own" cannot both be true. Choosing a
        monitor is the later instruction and the explicit one, so the set goes back to
        the desktop arrangement on the monitor just named - which is visible, where
        obeying the older choice would be the control that did nothing all over again.
        """
        if self.has_closed():
            return
        areas = screen_areas()
        if not 0 <= int(index) < len(areas):
            _report(f"set_tiling_monitor({index!r}): there is no such monitor.")
            return
        self._tiling_monitor = int(index)
        self._one_per_monitor = False
        self.restore_arrangement()

    def apply_tiling(self, tiling: str, monitor: int | None = None) -> bool:
        """Place the open views by §5's scheme `tiling`, inside one monitor's area.

        True when the windows were moved. **No player is told anything**: the whole
        method is `setGeometry`, so the clock, the position and the paused state are
        the same either side of it, which is what §6 demands of a re-tile and what
        `tests/test_player_window.py` reads the engine's position to prove.

        The rectangles come from `layouts.tile_rects` on the chosen monitor's
        `availableGeometry` - the taskbar is already excluded there, and no other
        application's window is touched, ever. A set with one view open is given the
        whole area rather than half of it: a scheme describes two windows, and leaving
        the one window that is open in a half-width box would be an arrangement of a
        window that is not there.
        """
        if self.has_closed():
            return False
        if tiling not in layouts.TILINGS:
            _report(f"apply_tiling({tiling!r}): not one of {layouts.TILINGS}.")
            return False
        if len(self.views) < 2:
            # §1: a one-stream recording never asks anything. A scheme is an
            # arrangement of two windows, and giving the single window the whole
            # monitor because a scheme was asked for would be this player deciding to
            # maximise a recording nobody said anything about.
            return False
        if monitor is not None:
            # Checked, and said out loud. `set_tiling_monitor` - the other door onto
            # this field - already refuses a monitor that is not there; this one used
            # to store it silently, after which `tiling_area` fell back on every later
            # tile and the stored index could never again be right. A caller restoring
            # a stale `player_tiling` monitor from the settings file got no word that
            # its choice had been dropped. Reported rather than refused: the viewer
            # asked for an arrangement, and the monitor in force is a better answer
            # than no arrangement at all.
            if 0 <= int(monitor) < len(screen_areas()):
                self._tiling_monitor = int(monitor)
            else:
                _report(
                    f"apply_tiling({tiling!r}, monitor={monitor!r}): there is no such "
                    "monitor; the one already chosen is used instead."
                )
        area = self.tiling_area()
        open_views = self.visible_views()
        if area.isEmpty() or not open_views:
            return False
        if len(open_views) > 2:
            # §5's schemes are "for **two** windows only". Three open windows is a
            # three-screen recording, where two rectangles would place two of them and
            # leave the third sitting over whichever it last covered.
            _report(
                f"apply_tiling({tiling!r}): {len(open_views)} windows are open and a "
                "scheme places two; nothing was moved."
            )
            return False
        if len(open_views) == 1:
            boxes = [QtCore.QRect(area)]
        else:
            boxes = layouts.tile_rects(area, tiling)
        # `_in_placement_order`, for `arrange`'s reason: §7's `S` swaps which window
        # gets which box, and re-applying a scheme must keep that.
        for view, box in zip(self._in_placement_order(open_views), boxes):
            view.setGeometry(box)
        self._one_per_monitor = False
        if self._tiling != tiling:
            # Only on a change. Re-applying the scheme in force is what «Вернуть
            # раскладку» does, and a caller saving `player_tiling` on every one of
            # those would write the same line to disk each time a window was dragged
            # back into place.
            self._tiling = tiling
            self.tiling_changed.emit(tiling)
        self._place_transport([view.geometry() for view in open_views], area)
        return True

    def apply_one_per_monitor(self, pairing: Sequence[int] | None = None) -> bool:
        """One window per monitor, each filling that monitor's usable area (§5).

        The same machinery as the schemes - geometry and nothing else - with
        `layouts.best_pairing` deciding which stream goes where unless the viewer has
        said. A hidden view is shown again by this: "one screen per monitor" with one
        of the screens away is not that arrangement, and the viewer asked for it by
        name.

        False, with a sentence on the transport, when there are fewer monitors than
        screens. `layouts.best_pairing` raises in that case rather than guessing, and
        this is the caller it is protecting: a guess here puts two borderless windows
        on one monitor, one exactly over the other.
        """
        if self.has_closed():
            return False
        areas = screen_areas()
        streams = self._streams()
        if not streams or len(areas) < len(streams):
            # The chooser's own words for the same impossibility, so the dialog and
            # the menu say one thing rather than two about the same desk.
            self._show_notice(_("Needs a second monitor"))
            return False
        order = self._checked_pairing(pairing, areas, streams)
        if order is None:
            if pairing is not None:
                # From a menu built against a display list that has since changed, or
                # from a settings file. Said out loud rather than used: the best
                # arrangement is a sensible desk, a half-valid mapping is not.
                _report(
                    f"apply_one_per_monitor({pairing!r}) is not a mapping of "
                    f"{len(streams)} streams onto {len(areas)} monitors; "
                    "the best arrangement is used instead."
                )
            order = layouts.best_pairing(areas, streams)
        self._pairing = tuple(order)
        for index, where in enumerate(order):
            # Only a view the *viewer* put away is brought back. Calling `_unhide`
            # unconditionally would put the whole set on screen the moment a host
            # arranged it, before `show()` had been called and before the transport
            # was there to drive it. `_unhide` and not `show_view`: this method is
            # about to place every window itself, and `show_view` re-applies the
            # arrangement, which from here would be this method calling itself.
            if index in self._hidden:
                self._unhide(index)
            self.views[index].setGeometry(areas[where])
        self._tiling = ""
        # Recorded, not only performed. Without this, «Вернуть раскладку» fell through
        # to `arrange()` and shrank both windows onto one monitor - the desktop
        # mock-up - which is the opposite of the arrangement the viewer had just
        # chosen, and the menu drew "As they sat on the desktop" ticked while one
        # window per monitor was on the desk.
        self._one_per_monitor = True
        self._place_transport([view.geometry() for view in self.views])
        return True

    def set_stream_monitor(self, stream: int, monitor: int) -> bool:
        """Put one screen on one monitor, and move whatever was there out of its way.

        A mapping of streams to monitors is a permutation, so moving a stream onto a
        monitor that is taken has to be a *swap*: anything else leaves two windows on
        one screen and one monitor black, and `layouts` refuses that pairing anyway -
        which would turn a menu entry into an error message.
        """
        if self.has_closed():
            return False
        order = list(self.pairing())
        if not order or not 0 <= stream < len(order):
            return False
        if not 0 <= monitor < len(screen_areas()):
            return False
        if order[stream] == monitor:
            return True
        if monitor in order:
            order[order.index(monitor)] = order[stream]
        order[stream] = monitor
        return self.apply_one_per_monitor(order)

    # -- §7: the two keys this half used to answer with silence ------------

    def _in_placement_order(
        self, views: Sequence[ScreenView]
    ) -> list[ScreenView]:
        """`views`, reversed once the viewer has swapped the two windows (§7's `S`).

        Only for exactly two of them: a swap of one window is nothing, and a swap of
        three is not defined - the same bound §5 puts on its schemes.
        """
        ordered = list(views)
        if self._swapped and len(ordered) == 2:
            ordered.reverse()
        return ordered

    def is_swapped(self) -> bool:
        """True while §7's `S` has put the two windows the other way round.

        Only about the arrangements that keep their order by position - the desktop
        one and the four schemes. One screen per monitor keeps it in the pairing
        instead and `swap_windows` reverses that, so a caller asking "which stream is
        on which monitor" asks `pairing()`, which is the answer there.
        """
        return self._swapped

    def swap_windows(self) -> bool:
        """§7's `S` in the separate-windows half: the two windows change places.

        The same gesture as the stage's «Поменять местами» and the same promise:
        **nothing is told to the engine.** The whole method is geometry, so the
        clock, the position and the paused state are identical either side of it,
        which is §6's rule for every arrangement.

        Recorded as well as performed, in whichever way the arrangement in force
        keeps its order. Under one screen per monitor the mapping *is* the
        arrangement, so the pairing is reversed and «Вернуть раскладку» puts the
        windows back swapped. Under a scheme and under the desktop arrangement the
        order is positional, so `_swapped` is flipped and `arrange` and `apply_tiling`
        read it - without which the next «Вернуть раскладку» would quietly undo the
        swap, which is the complaint `restore_arrangement` already carries about one
        screen per monitor.

        Refused, and nothing moved, on anything but exactly two open windows.
        """
        if self.has_closed() or len(self.views) != 2:
            return False
        if len(self.visible_views()) != 2:
            self._show_notice(
                _("Both screens have to be open before they can change places.")
            )
            return False
        if self._one_per_monitor:
            order = self.pairing()
            if len(order) != 2:
                return False
            return self.apply_one_per_monitor((order[1], order[0]))
        self._swapped = not self._swapped
        self.restore_arrangement()
        return True

    def cycle_arrangement(self) -> bool:
        """§7's `L` in the separate-windows half: the next arrangement, wrapping round.

        The stage's `L` steps through the three stage layouts; here the arrangements
        are §5's four schemes plus the desktop one this player opens with, so the
        same key means the same thing - "show me the next way of putting these
        windows" - whichever half of the player has the keyboard.

        **One screen per monitor is not in the cycle.** It needs a second monitor and
        it carries a mapping of its own, so a key that may or may not do anything
        depending on the desk is worse than the menu entry that says why, and the
        menu is where it stays. From one screen per monitor the next press starts the
        cycle at its beginning rather than doing nothing.

        Refused on a one-stream recording, where §1 says nothing is ever asked, and
        on three screens or more, where §5's schemes place two windows only.
        """
        if self.has_closed() or len(self.views) != 2:
            return False
        here = self.arrangement()
        if here not in ARRANGEMENT_CYCLE:
            here = ARRANGEMENT_CYCLE[-1]
        step = ARRANGEMENT_CYCLE[
            (ARRANGEMENT_CYCLE.index(here) + 1) % len(ARRANGEMENT_CYCLE)
        ]
        if step:
            return self.apply_tiling(step)
        self.arrange()
        return True

    def arrangement(self) -> str:
        """What is in force: `""` for the desktop one, a tiling id, or the layout id.

        One reader for the three states `_tiling` and `_one_per_monitor` make between
        them, so that nothing outside has to know they are two fields. `tiling()` is
        deliberately left as it was - it answers "which of §5's four schemes", which
        is the question a caller storing `player_tiling` is asking.
        """
        if self._one_per_monitor:
            return layouts.LAYOUT_ONE_PER_MONITOR
        return self._tiling

    def restore_arrangement(self) -> None:
        """§5's «Вернуть раскладку»: put the windows back where the last choice put them.

        The last choice, not the last scheme: before any scheme has been picked the
        arrangement in force is the desktop one this player opens with, and a menu
        entry that did nothing until a scheme had been chosen would be a control the
        viewer learns to distrust.

        One screen per monitor is a choice like the other two and is re-applied as
        one, with the mapping the viewer last set. It used to fall through to
        `arrange()`, which put both windows back as the scaled desktop mock-up inside
        one monitor - so «Вернуть раскладку» undid the arrangement instead of
        restoring it, which is the one thing it is named for.
        """
        if self.has_closed():
            return
        if self._one_per_monitor:
            # `self._pairing`, not nothing: the mapping is the viewer's once they have
            # changed it (§5), and re-deriving the best one here would quietly undo
            # "the portrait screen goes on the portrait monitor".
            if self.apply_one_per_monitor(self._pairing):
                return
            # A monitor has been unplugged since. `apply_one_per_monitor` has already
            # said so on the bar; falling through puts the windows somewhere real
            # instead of leaving them wherever the lost monitor had them.
            self._one_per_monitor = False
        if self._tiling:
            self.apply_tiling(self._tiling)
            return
        self.arrange()

    # -- §5: hiding a screen, and bringing it back -------------------------

    def visible_views(self) -> list[ScreenView]:
        """The views that are on screen, in order. A hidden one is still playing."""
        return [
            view for index, view in enumerate(self.views)
            if index not in self._hidden
        ]

    def is_hidden(self, index: int) -> bool:
        return index in self._hidden

    def hide_view(self, index: int) -> None:
        """Put one screen's window away without stopping its stream (§5).

        Index 0 is refused: it is the window that means "stop", and a path that hid it
        would leave the set alive with no main window and the viewer no way back to
        the behaviour they asked for.

        A player that has closed is refused too, for `_closing`'s reason below.
        """
        if self._closing:
            return
        if index <= 0 or index >= len(self.views) or index in self._hidden:
            return
        self._hidden.add(index)
        view = self.views[index]
        if view.isVisible():
            view.hide()

    def _unhide(self, index: int) -> bool:
        """Put one window back on screen where it stands. True if it had been away.

        Rescued on the way: the window kept the geometry it had when it was closed,
        and a monitor may have been unplugged since. A window restored to coordinates
        on a screen that is gone is one the viewer cannot find, which is the failure
        §6's display rule is about.

        This is the half of `show_view` that only shows. `apply_one_per_monitor`
        calls it rather than `show_view` because it is about to place every window
        itself, and `show_view` re-applies the arrangement - which would be that
        method calling itself once per screen.
        """
        if self._closing or not 0 <= index < len(self.views):
            return False
        was_away = index in self._hidden
        self._hidden.discard(index)
        view = self.views[index]
        view.setGeometry(rescue_into(view.geometry(), screen_areas()))
        view.show()
        view.raise_()
        return was_away

    def show_view(self, index: int) -> None:
        """Bring a hidden screen back, into its share of the arrangement in force.

        **Into its share, not back on top of the other window.** The window kept the
        geometry it had when the viewer put it away, and the set has gone on being
        arranged without it: `apply_tiling` deliberately gives one open view the
        *whole* monitor, because "a scheme describes two windows, and leaving the one
        window that is open in a half-width box would be an arrangement of a window
        that is not there". So the first window had grown to fill the screen while
        the second was away, and the second came back to its old half - entirely
        underneath it.

        Measured on one 2560x1400 monitor, two views, `equal_lr` applied, the second
        window closed and «Вернуть раскладку» pressed: view 0 at (0, 0, 2560, 1400),
        view 1 back at (1280, 0, 1280, 1400), **100 % of view 1 inside view 0**. The
        same on `equal_tb`. Afterwards both are back in their halves and the overlap
        is 0.

        The cost is stated rather than hidden: a window the viewer had dragged
        somewhere of their own goes back into its share when the other one returns.
        A window coming back changes how many windows there are, and §5's answer to
        "where do two of them go" is the arrangement in force - which is the same
        answer «Вернуть раскладку» gives, and the one the viewer last chose.
        """
        if self._closing or not self._unhide(index):
            return
        self.restore_arrangement()
        # After the arrangement, not before: the returning window is the one the
        # viewer just asked for, so it is the one that ends up on top of any overlap
        # the desktop arrangement leaves.
        self.views[index].raise_()

    # -- §6: a monitor unplugged while watching ----------------------------

    def _screen_removed(self, screen: Any = None) -> None:
        """Qt says a monitor has gone. Make sure no window went with it."""
        name = getattr(screen, "name", None)
        _report(f"a screen was removed ({name() if callable(name) else name!r}).")
        self.rescue_windows()

    def rescue_windows(self) -> list[str]:
        """Move every window that cannot be reached onto one that can, and say which.

        Returns the names of the windows that were moved, which is what the notice is
        built from and what the test reads. An empty list is the ordinary case: a desk
        with two monitors where the *other* one was unplugged strands nothing.

        The transport is rescued too and named in the sentence. It is a window like
        the others and it is the one carrying every control, so a bar left on a
        monitor that is gone is a player that cannot be paused or closed.
        """
        if self._closing:
            return []
        areas = screen_areas()
        if not areas:
            return []  # no screen at all: there is nowhere to move anything to
        moved: list[str] = []
        for index, view in enumerate(self.views):
            if index in self._hidden:
                continue  # hidden windows are rescued by `show_view`, when shown
            box = view.geometry()
            rescued = rescue_into(box, areas)
            if rescued != box:
                view.setGeometry(rescued)
                moved.append(view.title())
        bar = self.transport.geometry()
        rescued_bar = rescue_into(bar, areas)
        if rescued_bar != bar:
            self.transport.setGeometry(rescued_bar)
            moved.append(_("the controls"))
        if moved:
            self._show_notice(
                _("A monitor is gone — {names} moved to one that is still there.")
                .format(names=", ".join(moved))
            )
        return moved

    # -- §6: seeking, and a stream that has run out ------------------------

    def _seeking_changed(self, seeking: bool) -> None:
        """Dim every view while a seek is in flight, or take the dimming away.

        The ended badges go with it when a seek starts, and this is the only place
        they are cleared. `SyncEngine.seek` clears its own per-stream ended flags, so
        a badge left standing would be the one thing on screen still claiming a
        recording had finished - over a stream the viewer has just scrubbed back into
        the middle of.
        """
        if self._closing:
            return
        for view in self.views:
            view.set_busy(bool(seeking))
            if seeking:
                view.set_ended(False)

    def _stream_ended(self, index: int) -> None:
        """§6: the shorter stream has run out; hold its last frame with a badge."""
        if self._closing or not 0 <= int(index) < len(self.views):
            return
        self.views[int(index)].set_ended(True)

    # -- §2.1: a stream the timeline has not reached ------------------------

    def _stream_waiting(self, index: int, waiting: bool = True) -> None:
        """§2.1: black with a badge, rather than a frozen first frame."""
        if self._closing or not 0 <= int(index) < len(self.views):
            return
        self.views[int(index)].set_waiting(bool(waiting))

    # -- §2.2: the offsets --------------------------------------------------

    def set_offset(self, index: int, milliseconds: int) -> None:
        """Move one stream against the other. Never moves the timeline.

        The engine does the moving and this does not re-read anything afterwards: it
        answers with `offsets_changed`, which `_offsets_changed` takes, so a value the
        engine clamped or refused is the value the panel ends up showing. Writing the
        asked-for number into the field here instead would let the control and the
        recording disagree, which over a sign convention is the one disagreement a
        viewer cannot debug.
        """
        if self._closing or not self._engine.can_sync():
            return
        self._engine.set_offset(int(index), int(milliseconds))

    def _offsets_changed(self) -> None:
        """Take the engine's offsets back onto the bar. Emits nothing at the engine."""
        if self._closing:
            return
        self.transport.set_offsets(self._engine.offsets(len(self.views)))

    def _refresh_source_controls(self) -> None:
        """Re-hand the bar the stream names, the offsets and the frame rate.

        Called at start-up and after a rename: the audio menu, the sync summary and
        the frame nudge are all built out of the recording, and §2.1 is explicit that
        the frame step uses the reference stream's rate rather than a round 25.
        """
        if self._closing:
            return
        self.transport.set_sync_streams(
            self._given_titles,
            self._engine.offsets(len(self.views)),
            recording_framerate(self._engine.recording),
        )
        self.transport.set_sources_available(
            len(self.views),
            self._given_titles,
            audio=self._engine.can_choose_audio(),
            sync=self._engine.can_sync(),
        )
        self._show_audio_state()

    # -- §1.2: which stream is heard ---------------------------------------

    def set_audio_source(self, index: int) -> None:
        """§1.2: the sound comes from this stream from now on, or from none of them.

        Muting and choosing are kept apart on purpose. «Выключен» is the mute the
        volume button already does, so it goes through `_toggle_mute`'s state rather
        than through a second one - two controls that both silence the player and
        disagree about whether it is silent is the bug this avoids.

        Nothing here touches the picture. §1.2: "swapping which screen is on the left
        must not move the sound, and this control must not move the picture".
        """
        if self._closing:
            return
        wanted = int(index)
        if wanted < 0:
            if not self._muted:
                self._toggle_mute()
            self._show_audio_state()
            return
        if not 0 <= wanted < len(self.views):
            return
        if not self._engine.can_choose_audio():
            # The chip is not on the bar in this case, so this is reachable only from
            # a caller or a test. Said to the log, not to the viewer: there is no
            # control for them to have pressed.
            _report("the engine cannot choose an audio source; the request was dropped.")
            return
        if self._muted:
            self._toggle_mute()
        self._engine.set_audio_source(wanted)
        self._show_audio_state()

    def _audio_announced(self, _index: int = 0) -> None:
        """The engine says the sound has moved to another stream. Show it, or silence.

        The argument is read off the engine again rather than used, so that this slot
        and `_show_audio_state` cannot drift apart; the engine is the one that knows.
        """
        self._show_audio_state()

    def _show_audio_state(self) -> None:
        """Put on the bar which stream is heard, or that none is. One rule, one place.

        The chip is a view of two things at once - which stream the engine is playing
        and whether this player is muted - because §1.2 makes «Выключен» *be* the
        mute rather than a fourth audio source. So every path that changes either of
        them ends here, and the bar can no longer be told one thing by the volume
        button and another by the audio menu.

        The engine is never told about the silence, which is deliberate and is why
        unmuting can put the chip back on the right stream: `_Engine.audio_index`
        still knows which one carries the sound.
        """
        if self._closing:
            return
        self.transport.set_audio_source(
            -1 if self._muted else self._engine.audio_index
        )

    # -- §1: opening something else ----------------------------------------

    def open_menu(self, parent: QtWidgets.QWidget | None = None) -> QtWidgets.QMenu:
        """§1's menu, built as it should be *now*.

        Built on the press and never kept, for the arrange menu's reasons and one
        more: the library section is the recordings folder, and the recorder may have
        written another take into it while this player was open.

        The library rows are read **without** their durations. §1 asks a library row
        to carry a length, and a length costs an `ffprobe` per file - a process per row
        on the thread that is drawing the menu, with the player's own picture waiting
        behind it. A menu is built on a click and has to appear on that click, so this
        shows the name, the date and the size, which are a `stat` each. The full list
        with lengths belongs to a library *window*, which this is not.
        """
        menu = QtWidgets.QMenu(parent)
        menu.setStyleSheet(theme.style_sheet())
        menu.setToolTipsVisible(True)
        menu.addAction(
            pick("windows.open.dialog", "Open…"), self.choose_and_open
        ).setShortcut(QtGui.QKeySequence("Ctrl+O"))

        rows = self._library_rows()
        if rows:
            menu.addSeparator()
            for entry in rows:
                menu.addAction(
                    self._library_text(entry),
                    # `_checked` absorbs `triggered(bool)`; without it PySide hands
                    # `checked` to `paths` and every row would open `True`.
                    lambda _checked=False, where=str(entry.path):
                    self.open_paths([where]),
                )

        menu.addSeparator()
        if len(self.views) == 1:
            # §1.1 is about a video with no pair. A third camera is not in the
            # contract and is not invented here: `layouts` arranges two, and an
            # action that produced an arrangement nothing scores would be a worse
            # answer than no action. Reported as a decision rather than a limit.
            menu.addAction(
                pick("windows.open.second", "Add a second video…"), self.add_second
            )
        if len(self.views) > 1:
            menu.addAction(pick("windows.open.save", "Save this set"), self.save_set)
        return menu

    def _library_rows(self) -> list[Any]:
        """The newest `LIBRARY_ROWS` recordings in the output folder. Never raises."""
        try:
            from .. import config
            from . import library

            settings, _trouble = config.load_settings()
            found = library.find_entries(
                settings.resolved_output_dir(), durations=False
            )
        except Exception as exc:  # noqa: BLE001 - a menu must open whatever happens
            # A settings file that will not read, a folder on a drive that has gone.
            # The dialog is still in the menu above, so the viewer is not stuck.
            _report(f"the library could not be listed ({exc!r}).")
            return []
        return found[:LIBRARY_ROWS]

    @staticmethod
    def _library_text(entry: Any) -> str:
        """One row: what it is called, when it was made, and how big it is.

        The date is the first ten characters of the ISO stamp the index carries, which
        is the day. A menu row is read sideways and a full timestamp in it is four
        numbers nobody is comparing; the file dialog shows the minutes.
        """
        recorded = str(getattr(entry, "recorded", "") or "")[:10]
        megabytes = int(getattr(entry, "size_bytes", 0) or 0) / (1024 * 1024)
        screens = int(getattr(entry, "screens", 1) or 1)
        name = str(getattr(entry, "name", ""))
        if screens > 1:
            return pick(
                "windows.library.row.set",
                "{name} — {date}, {screens} screens, {size} MB",
            ).format(
                name=name, date=recorded, screens=screens, size=f"{megabytes:.0f}"
            )
        return pick(
            "windows.library.row", "{name} — {date}, {size} MB"
        ).format(name=name, date=recorded, size=f"{megabytes:.0f}")

    def _open_open_menu(self) -> None:
        """Pop §1's menu under the Open button.

        `popup`, not `exec`, for the reason `_open_arrange_menu` gives: a nested
        event loop started from inside a button's `clicked` is a crash waiting.
        """
        if self._closing:
            return
        menu = self.open_menu(self.transport)
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        if menu.isEmpty():
            menu.deleteLater()
            return
        button = self.transport.open_button
        menu.popup(button.mapToGlobal(QtCore.QPoint(0, -menu.sizeHint().height())))

    def choose_and_open(self) -> None:
        """§1's Открыть…: a file dialog that also takes two files at once.

        `getOpenFileNames`, not `getOpenFileName`, and that is the whole of the
        two-camera gesture in a dialog: a viewer who ctrl-clicks both files gets them
        as a pair, exactly as dropping both does, and one file is one recording.
        """
        chosen, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            self.transport,
            pick("windows.open.dialog.title", "Open a recording"),
            str(self._start_folder()),
            self._file_filter(),
        )
        if chosen:
            self.open_paths(list(chosen))

    def add_second(self) -> None:
        """§1.1's Добавить второй экран…: pair this video with another file."""
        chosen, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self.transport,
            pick("windows.open.second.title", "Add a second video"),
            str(self._start_folder()),
            self._video_filter(),
        )
        if chosen:
            self.add_second_video(chosen)

    def add_second_video(self, path: str | Path) -> bool:
        """§1.1: play `path` beside what is open, keeping the position and the pause.

        This is the one place in the player where the clock is rebuilt, and §1.1 says
        why it is allowed: a `QMediaPlayer` cannot grow a second file, and the viewer
        asked for this by name. Everything the rebuild must not cost is carried over
        by `_replace_with` - where the timeline was, and whether it was running.

        The chooser is not re-shown: the layout in force is passed through, so a
        viewer watching in separate windows gets separate windows with two streams in
        them rather than a dialog asking a question they have just answered.
        """
        files = [str(existing) for existing in recording_files(self._engine.recording)]
        if not files:
            self._show_notice(
                pick(
                    "windows.open.second.unknown",
                    "The file behind this picture is not known, so a second one "
                    "cannot be added to it.",
                )
            )
            return False
        files.append(str(path))
        return self._replace_with(
            files, layout=self._layout_in_force(), carry=True
        )

    def open_paths(self, paths: Sequence[str] | str | Path) -> bool:
        """§1: open whatever was dropped, chosen or clicked, in place of this player.

        One path is one recording - an index, a video or a folder, which
        `open.open_recording` already tells apart - and **two or more are a pair**,
        which is §1's two-camera case in one gesture. Neither decision is taken here:
        the list goes through whole, because `open.open_recording` is the module that
        knows what a path is and there must not be a second opinion about it.

        The position is **not** carried: this is a different recording, and landing a
        new take at 00:14 because the last one was there would be the player inventing
        a position nobody asked for. §1.1's add-a-camera is the case where carrying it
        is the point, and that one says so.

        **The layout is not asked about again either.** The layout in force is passed
        through, exactly as `add_second_video` passes it and for the stronger reason:
        with «Запомнить мой выбор» unticked, letting `open.open_recording` fall back to
        `choose_layout` put §2.1's modal dialog in front of every library row, every
        time, which makes the library unusable. `PlayerStage.open_paths` documents the
        same rule and this is the path that diverged from it.
        """
        if isinstance(paths, (str, Path)):
            paths = [str(paths)]
        wanted = [str(item) for item in paths if str(item)]
        if not wanted:
            return False
        return self._replace_with(
            wanted if len(wanted) > 1 else wanted[0],
            layout=self._layout_in_force(),
        )

    def save_set(self) -> bool:
        """§1.3: write the pair, the audio choice and the offsets out beside the first.

        Nothing is re-encoded and no source is written over - the index is a few lines
        of JSON - so this asks nothing and says where it went afterwards. A viewer who
        has spent a minute lining two cameras up should not then have to answer a
        dialog about where to put the result.
        """
        files = [str(path) for path in recording_files(self._engine.recording)]
        if len(files) < 2:
            self._show_notice(
                pick(
                    "windows.save.alone",
                    "There is one video open, so there is no set to save.",
                )
            )
            return False
        from . import open as sources

        audio = self._engine.audio_index
        where, note = sources.save_set(
            files,
            audio_index=audio if 0 <= audio < len(files) else 0,
            offsets=self._engine.offsets(len(files)),
        )
        if where is None:
            self._show_notice(note or pick("windows.save.failed", "Nothing was saved."))
            return False
        if note:
            self._show_notice(note)
        self._show_notice(
            pick("windows.save.done", "Saved as {name}").format(name=where.name)
        )
        return True

    # -- §1: the machinery under all four of them --------------------------

    def _layout_in_force(self) -> str:
        """Which of §1's layouts this player is, for a set that is being rebuilt.

        `separate` unless one window per monitor is on the desk, because those are the
        two layouts this module implements and §1.1 says the layout in force is kept.
        The four tiling schemes are not layouts - they are arrangements *inside*
        `separate` - so `arrangement()` is read for the one case that is a layout and
        ignored otherwise.
        """
        if self.arrangement() == layouts.LAYOUT_ONE_PER_MONITOR:
            return layouts.LAYOUT_ONE_PER_MONITOR
        return layouts.LAYOUT_SEPARATE

    def _start_folder(self) -> Path:
        """Where a file dialog opens: beside what is playing, or the output folder."""
        files = recording_files(self._engine.recording)
        if files:
            return files[0].parent
        try:
            from .. import config

            settings, _trouble = config.load_settings()
            return Path(settings.resolved_output_dir())
        except Exception as exc:  # noqa: BLE001 - a dialog must still open
            _report(f"the output folder could not be read ({exc!r}).")
            return Path.home()

    @staticmethod
    def _video_filter() -> str:
        from . import open as sources

        patterns = " ".join(f"*{suffix}" for suffix in sources.VIDEO_SUFFIXES)
        return pick("windows.filter.video", "Video ({patterns})").format(
            patterns=patterns
        )

    @staticmethod
    def _file_filter() -> str:
        """Indexes and videos in one filter, because §1 opens either from one dialog."""
        from .. import multi
        from . import open as sources

        patterns = " ".join(f"*{suffix}" for suffix in sources.VIDEO_SUFFIXES)
        return pick(
            "windows.filter.any", "Recordings (*{index} {patterns})"
        ).format(index=multi.SIDECAR_SUFFIX, patterns=patterns)

    def _replace_with(
        self,
        target: Any,
        *,
        layout: str | None = None,
        carry: bool = False,
    ) -> bool:
        """Build the new player, show it, carry what has to be carried, close this one.

        **In that order, and the order is the whole method.** Closing first would take
        the picture away while a `QMediaPlayer` opens a file, which on a cold cache is
        long enough to look like a crash; and it would run `_free_clock_with`'s
        `deleteLater` against a desk with nothing on it.

        **The new player is held by the module.** `open.open_recording` hands its
        windows back and leaves owning them to the caller - `run` keeps a local for
        the length of the event loop, `ui/app.py` keeps `self._player`. A player
        opened from inside this one has no such caller: the host's reference is to
        *this* object, so without `_hold_player` the new windows would be collected
        the moment this method returned, and §1 would be a menu that flashes a player
        and closes it. It used to be `_successor` that held it, which worked and cost
        a whole player per open - `_OPEN_PLAYERS` carries that measurement.
        """
        if self._closing:
            return False
        from . import open as sources

        position = self._engine.position
        playing = self._engine.playing
        try:
            windows, note = sources.open_recording(target, layout=layout)
        except Exception as exc:  # noqa: BLE001 - documented not to raise; belt
            self._show_notice(
                pick("windows.open.failed", "{path} could not be opened: {detail}")
                .format(path=target, detail=f"{type(exc).__name__}: {exc}")
            )
            return False
        if windows is None:
            # An empty note is §2.1's cancelled dialog: the viewer has just said it,
            # and a player that argues with Отмена is the thing `open.py` avoids.
            if note:
                self._show_notice(note)
            return False

        show = getattr(windows, "show", None)
        if callable(show):
            try:
                show()
            except Exception as exc:  # noqa: BLE001 - a half-built player must say so
                self._show_notice(
                    pick("windows.open.unshown", "The player would not open: {reason}")
                    .format(reason=f"{type(exc).__name__}: {exc}")
                )
                return False
        # Held by the module, not only by this object: `close_all` lets go of
        # `_successor` a few lines below, and without `_hold_player` the new windows
        # would be collected the moment this one was. See `_OPEN_PLAYERS`.
        _hold_player(windows)
        self._successor = windows
        if carry:
            self._carry_over(windows, position, playing)
        if note:
            say = getattr(windows, "say", None)
            if callable(say):
                say(note)
            else:
                _report(f"opening said: {note}")
        self.opened.emit(windows)
        self.close_all()
        return True

    def _carry_over(self, windows: Any, position: int, playing: bool) -> None:
        """§1.1: put the new clock where the old one was, in the state it was in.

        Done twice, and that is not belt and braces. `QMediaPlayer.setSource` is
        asynchronous: a `setPosition` issued before the file has been read can be
        dropped, and the viewer would be thrown back to 00:00 by the very action they
        took to keep watching. So the position is asked for now, and asked for again
        on the first duration the new clock announces, which is the moment it knows
        how long the file is and therefore that it has read it. `_carry` is cleared by
        the second attempt, so this never fights a viewer who scrubs in between.
        """
        clock = getattr(windows, "engine", None)
        if clock is None:
            _report(f"{type(windows).__name__} has no engine; the position was lost.")
            return
        self._carry = (_Engine(clock), int(position), bool(playing))
        self._apply_carry()
        signal = self._carry[0].signal("duration")
        if signal is not None:
            signal.connect(self._carry_landed)
            self._carry_bond = (signal, self._carry_landed)

    def _carry_landed(self, _duration: int = 0) -> None:
        """The new clock knows its length, so the position it was given will stick."""
        if self._carry is None:
            return
        self._apply_carry()
        self._carry = None
        self._drop_carry()

    def _drop_carry(self) -> None:
        """Take this object's one wire into the *new* clock out again.

        `close_all` cannot do it and `_unbind` is not given it. §1.1's carry is in
        flight across the close by design: the second `setPosition` has to happen
        after `setSource` has landed, which is after this player has gone. So the
        carry disconnects itself the moment it has landed, and that is what lets the
        player that opened the new one be collected - without it one husk of a player
        stays alive for as long as the new clock does, one per «Добавить второй
        экран».
        """
        bond = getattr(self, "_carry_bond", None)
        if bond is None:
            return
        self._carry_bond = None
        signal, slot = bond
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            # The new clock has been deleted since, which has taken the connection
            # with it. There is nothing left to disconnect and nothing to report.
            pass

    def _apply_carry(self) -> None:
        if self._carry is None:
            return
        clock, position, playing = self._carry
        clock.seek(position)
        # `toggle` rather than `play`/`pause`, because that is the one command every
        # engine in the contract has; a new clock opens paused, so one toggle is the
        # whole of "and it was playing".
        if playing and not clock.playing:
            clock.toggle()

    # -- §5: the menu ------------------------------------------------------

    def arrange_menu(self, parent: QtWidgets.QWidget | None = None) -> QtWidgets.QMenu:
        """Build the arrange menu as it should be *now*.

        Built on every press and never kept: what may be in it depends on the display
        list, on how many views are open and on which of them the viewer has closed,
        and all three change while the player is running. A menu built once would be
        the one place in this window a language switch and a replugged monitor could
        not reach.

        Nothing that cannot be done is in it. A one-stream recording gets an empty
        menu - and never sees it, because the button that opens it is hidden - and a
        recording with three screens gets no four-scheme section, because §5's schemes
        are "for **two** windows only".

        **Only the four schemes are limited to two windows.** "As they sat on the
        desktop" and "Restore the arrangement" used to be inside that same test, so a
        three-screen recording got a button whose menu held one greyed line and no way
        back to the arrangement the player had opened with. `arrange()` and
        `restore_arrangement()` work for any number of views - `arrange()` is what the
        player opens with - so they are offered whenever the button is.
        """
        menu = QtWidgets.QMenu(parent)
        menu.setStyleSheet(theme.style_sheet())
        # Qt leaves `toolTipsVisible` False, so the reason written on a disabled entry
        # below was never drawn: the viewer saw a greyed line and no explanation,
        # which is the case §2.1's "a disabled card that says why" is written against.
        # The style sheet does not change this; only this call does.
        menu.setToolTipsVisible(True)
        # An empty menu once the player has closed, rather than one built out of
        # windows that are gone: every entry is refused by `has_closed` anyway, and
        # the titles this reads come off `ScreenView`s that have been deleted.
        if self.has_closed() or len(self.views) < 2:
            return menu

        areas = screen_areas()
        if len(self.views) == 2:
            for tiling in layouts.TILINGS:
                action = menu.addAction(
                    tiling_icon(tiling), layouts.tiling_title(tiling)
                )
                action.setCheckable(True)
                action.setChecked(tiling == self.arrangement())
                action.triggered.connect(
                    lambda _checked=False, which=tiling: self.apply_tiling(which)
                )
        if len(self.views) == 2:
            # §7's `S` needs somewhere to be written down. The stage carries it on a
            # button beside the layout chip; this half has one menu and no room on
            # the bar for a second button, so it goes here with its key in the label
            # - the project's rule that a control and its help move together.
            # Not checkable: it is a gesture and not a state. One screen per monitor
            # swaps by reversing the pairing and leaves `_swapped` alone, so a tick
            # read off `_swapped` would be wrong in exactly the arrangement where
            # both windows are at their largest.
            swap = menu.addAction(_("Swap the screens (S)"))
            swap.triggered.connect(lambda _checked=False: self.swap_windows())
        desktop = menu.addAction(_("As they sat on the desktop"))
        desktop.setCheckable(True)
        # `arrangement()`, not `not self._tiling`: one screen per monitor clears the
        # scheme, so reading the scheme alone drew this ticked while one window per
        # monitor was on the desk - the menu reporting an arrangement that is not the
        # one in front of the viewer.
        desktop.setChecked(not self.arrangement())
        desktop.triggered.connect(lambda _checked=False: self.arrange())
        if len(areas) > 1:
            self._add_monitor_menu(menu, areas)
        menu.addSeparator()
        menu.addAction(
            _("Restore the arrangement"),
            self.restore_arrangement,
        )
        menu.addSeparator()

        per_monitor = menu.addAction(layouts.title(layouts.LAYOUT_ONE_PER_MONITOR))
        enough = len(areas) >= len(self.views)
        per_monitor.setEnabled(enough)
        per_monitor.setCheckable(True)
        per_monitor.setChecked(self._one_per_monitor)
        per_monitor.triggered.connect(
            lambda _checked=False: self.apply_one_per_monitor()
        )
        if not enough:
            # The reason is on the entry, as the chooser puts it on a disabled card:
            # a greyed line with no explanation is read as a fault in the player.
            per_monitor.setToolTip(_("Needs a second monitor"))
        elif len(self.views) > 1:
            self._add_pairing_menu(menu, areas)

        hidden = sorted(self._hidden)
        if hidden:
            menu.addSeparator()
        for index in hidden:
            # §5 names the two-screen case; anything beyond it has to name the screen,
            # because "the second screen" is not what the viewer put away.
            text = (
                _("Show the second screen") if index == 1 and len(self.views) == 2
                else _("Show {name}").format(name=self.views[index].title())
            )
            # `_checked` absorbs the `triggered(bool)` argument. Without it PySide
            # hands `checked` to the first parameter, so a menu press would call
            # `show_view(False)` - which is `show_view(0)`, the main window, and the
            # closed screen would stay closed while the other one jumped.
            menu.addAction(
                text,
                lambda _checked=False, which=index: self.show_view(which),
            )
        return menu

    def _add_monitor_menu(
        self, menu: QtWidgets.QMenu, areas: Sequence[QtCore.QRect]
    ) -> None:
        """The "tile on which monitor" submenu. Only built when there is a choice."""
        chooser = menu.addMenu(_("Tile on monitor"))
        current = self.tiling_area()
        for index, area in enumerate(areas):
            action = chooser.addAction(monitor_label(index + 1, area))
            action.setCheckable(True)
            action.setChecked(area == current)
            action.triggered.connect(
                lambda _checked=False, which=index: self.set_tiling_monitor(which)
            )

    def _add_pairing_menu(
        self, menu: QtWidgets.QMenu, areas: Sequence[QtCore.QRect]
    ) -> None:
        """"Which screen on which monitor": one submenu per screen, the monitors in it.

        Per screen rather than per monitor, because the question a viewer has is "the
        portrait one is on the wrong monitor" and not "monitor 2 is showing the wrong
        thing". Choosing is a swap, which `set_stream_monitor` does, so the mapping
        stays a permutation whatever is picked.
        """
        order = self.pairing()
        if not order:
            return
        chooser = menu.addMenu(_("Which screen on which monitor"))
        for index, view in enumerate(self.views):
            per_screen = chooser.addMenu(view.title())
            for where, area in enumerate(areas):
                action = per_screen.addAction(monitor_label(where + 1, area))
                action.setCheckable(True)
                action.setChecked(index < len(order) and order[index] == where)
                action.triggered.connect(
                    lambda _checked=False, stream=index, monitor=where:
                    self.set_stream_monitor(stream, monitor)
                )

    def _open_arrange_menu(self) -> None:
        """Pop the menu under the transport's arrange button.

        `popup`, not `exec`: `exec` runs a nested event loop, and this is reached from
        a button's `clicked` while the bar is in the middle of handling a mouse
        release. The menu is parented to the transport so it is deleted with it - a
        menu left behind by a window that has closed is a floating panel with no owner.
        """
        if self._closing:
            return
        button = self.transport.arrange_button
        menu = self.arrange_menu(self.transport)
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        if menu.isEmpty():
            # Nothing to offer, so nothing is popped - and the menu is dropped by
            # hand, because `WA_DeleteOnClose` only fires on a close and one that was
            # never shown would sit under the transport until the player ended.
            menu.deleteLater()
            return
        menu.popup(button.mapToGlobal(QtCore.QPoint(0, -menu.sizeHint().height())))

    # -- closing -----------------------------------------------------------

    def _view_closed(self, index: int = 0) -> None:
        """§5: the first window means stop; any other one is put away.

        The default of 0 is deliberate. The signal is connected with the view's index
        bound to it, but `ScreenView.closed` carries no argument and anything else
        connecting to it - a test, a later caller - means "a window of this player has
        been closed". Reading that as the main window keeps the old behaviour, which
        is the one that must not be weakened by accident.
        """
        if self._closing:
            return
        if index <= 0:
            # A half-closed player, with one screen still playing and no transport, is
            # a confusing thing to leave behind - and §5 gives this window the meaning
            # "stop" so that there is exactly one gesture that means it.
            self.close_all()
            return
        self.hide_view(index)
        # A sentence and not the menu entry's own words: the strip under the controls
        # is a line of news, and «Показать второй экран» written there reads as a
        # button that has lost its button. It says where the window went and where it
        # can be found, because a window that vanishes with nothing said is the
        # complaint this whole bar exists to answer.
        self._show_notice(
            _("{name} is hidden — the Arrange menu brings it back.").format(
                name=self.views[index].title()
            )
        )

    def _transport_closed(self) -> None:
        # And closing the bar closes the set, for the same reason and more so: the bar
        # is the only window with a close button, so Alt+F4 on it is what a viewer
        # means by "close the player".
        self.close_all()

    def close_all(self) -> None:
        """Close every view and the transport, stop the engine, let go of the files.

        **Every window is deleted, not only closed.** `close()` hides a frameless
        window and leaves the C++ object alive, so the lambdas each view's `closed`
        signal carries - which hold this object, which holds the views - stayed alive
        with it, in a cycle Python's collector cannot see through because one end of
        it is inside Qt. Measured before this method deleted anything: ten opens from
        inside the player left **eleven** `PlayerWindows`, eleven `TransportBar` and
        twenty-two `ScreenView` alive; after it, one of each.

        **Disconnect first, delete after.** `_unbind` takes out every wire into
        `i18n`, the application and the engine while both ends are still valid; the
        `deleteLater`s come last, after `closed` has been emitted, because
        `open._free_clock_with` hangs the clock's own `deleteLater` on that signal
        and a receiver reading `self.views` must not find a C++ object that is gone.

        §1.1's position carry is deliberately *not* unbound: it is a wire into the
        **new** clock, it is in flight across this close by design, and it takes
        itself out in `_drop_carry`.
        """
        if self._closing:
            return
        self._closing = True
        self._poll.stop()
        self._engine.listener = None
        self._unbind()
        for view in self.views:
            view.removeEventFilter(self)
            view.close()
        self.transport.removeEventFilter(self)
        self.transport.clear_notice()
        self.transport.close()
        self._engine.close()
        # `close()` resolves to `SyncEngine.stop()`, which pauses the players and
        # leaves every file open. See `_Engine.release`.
        self._engine.release()
        self.closed.emit()
        for view in self.views:
            view.deleteLater()
        self.transport.deleteLater()
        # The chain is cut here. `_OPEN_PLAYERS` holds whatever this player opened,
        # so letting go of it does not take the new player with it - which is what
        # `_successor` used to do the other way round, keeping every player of a
        # session alive through the one that replaced it.
        self._successor = None
        _forget_player(self)

    # -- the keyboard ------------------------------------------------------

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Type.KeyPress and isinstance(
            event, QtGui.QKeyEvent
        ):
            if self.handle_key(event):
                return True
        return super().eventFilter(watched, event)

    def handle_key(self, event: QtGui.QKeyEvent) -> bool:
        """§7's table: Space, the arrows, `,` and `.`, Home, End and Esc.

        **The arrows skip and the commas step**, which is the other way round from
        what this window did until now. §7 has one table for the whole player and
        `PlayerStage.handle_key` already follows it, so the same two keys meant two
        different things depending on which half of the same player had focus - and
        `,` and `.` did nothing at all here. The transport's two step buttons say
        `(,)` and `(.)` for the same reason: a tooltip naming a key that steps
        nothing is worse than no tooltip.

        Shift+arrow is kept as a second spelling of skip. It is not in §7's table,
        but it is what this window has always done and it now agrees with the bare
        arrow rather than contradicting it, so taking it away would break a habit to
        no purpose.

        `Key_Less` and `Key_Greater` alongside the commas: on most layouts those are
        the same physical keys with Shift held, and a viewer pressing Shift+. expects
        a frame rather than silence.

        **§2.2 takes Shift+arrow and Alt+arrow away from skipping, but only when
        there are two streams to slide.** A key cannot mean two things in one window,
        and `docs/PLAYER_SOURCES.md` claims both pairs by name. On a one-stream
        recording there is no offset to move, so Shift+arrow still skips exactly as it
        has from the start - which is the behaviour this change must not cost.

        **`S` and `L`, which this half used to answer with silence.** §6 of
        `docs/PLAYER_LAYOUTS.md` writes one keyboard table for the whole player, and
        `PlayerStage` answered both while this window answered neither - so the same
        two keys worked or did nothing depending on which half of the same player
        had focus, which is the complaint `_sync_key` already carries about
        Shift+arrow. Here they mean what they mean there: `S` swaps the two windows'
        places, `L` steps to the next arrangement. Both are geometry and neither says
        anything to the engine.

        Matched on `event.text()` and not on `Key_S` / `Key_L` alone, exactly as
        `PlayerStage.handle_key` matches them: on ЙЦУКЕН those two physical keys
        report `ы` and `д`, and a shortcut that stops working in the middle of a
        Russian sentence is not a shortcut. The digits, the arrows and the commas are
        the same code on both layouts and need no such help.

        **What this half deliberately does not answer**, so that the difference is
        written down rather than found: `1` and `2`, which §6 marks "stage layouts"
        and which choose the main stream of a stage that has no counterpart here -
        every window of this half is already showing its own stream - and `F11`,
        which is the stage window's own full screen and not in §6's table at all.
        These windows are frameless and borderless; there is nothing for them to go
        full screen *out of*.
        """
        key = event.key()
        letter = event.text().lower()
        if self._sync_key(key, event.modifiers()):
            event.accept()
            return True
        if key == QtCore.Qt.Key.Key_O and (
            event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
        ):
            self._open_open_menu()
        elif key == QtCore.Qt.Key.Key_Space:
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
        elif letter in ("s", "ы"):
            self.swap_windows()
        elif letter in ("l", "д"):
            self.cycle_arrangement()
        elif key == QtCore.Qt.Key.Key_Escape:
            self.close_all()
        else:
            return False
        event.accept()
        return True

    def _sync_key(
        self, key: int, modifiers: QtCore.Qt.KeyboardModifier
    ) -> bool:
        """§2.2's four keys: Shift+←/→ for a frame, Alt+←/→ for 100 ms.

        Answers False - and leaves the key to mean what it always did - whenever
        there is nothing to slide: one stream, three of them, or an engine with no
        `set_offset`. That is what keeps the one-video recording's Shift+arrow a
        skip.
        """
        if key not in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
            return False
        # Exactly two, the same test the panel itself is built under: with three
        # streams there is no picker that can say which one these keys move.
        if len(self.views) != 2 or not self._engine.can_sync():
            return False
        direction = -1 if key == QtCore.Qt.Key.Key_Left else 1
        if modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier:
            self.transport.sync_panel.nudge(frames=direction)
            return True
        if modifiers & QtCore.Qt.KeyboardModifier.AltModifier:
            self.transport.sync_panel.nudge(milliseconds=direction * NUDGE_MS)
            return True
        return False

    # -- the clock ---------------------------------------------------------

    def _show_notice(self, text: str) -> None:
        """Put one sentence where the viewer is looking.

        Every path that used to end in a `print` ends here as well. Guarded, because
        the transport is a window like any other and may already have been closed when
        a late signal arrives - and a problem report that raises is worse than the
        problem.
        """
        if self._closing:
            return
        transport = getattr(self, "transport", None)
        if transport is None:
            return
        try:
            transport.say(text)
        except RuntimeError as exc:  # the bar's C++ side has gone
            _report(f"the transport could not show {text!r} ({exc}).")

    def _engine_said(self, text: str) -> None:
        """Relay the engine's own complaints, so none of them is swallowed here.

        These are the sentences `SyncEngine` emits on `problem`: a file this machine
        cannot play, the one carrying the sound refusing to open, a screen that will
        not stay in step and has been left to run on its own. Every one of them tells
        the viewer why what they are watching is not what they expected, so every one
        of them goes on the bar as well as to the console.
        """
        _report(f"the engine says: {text}")
        self._show_notice(text)

    def _engine_finished(self) -> None:
        self.transport.set_playing(False)
        self.transport.set_position(self._engine.position)

    def _toggle_mute(self) -> None:
        """The volume button, and the one place the mute state changes.

        §1.2's «Звук выключен» *is* this state - `set_audio_source(-1)` reaches it
        through here rather than keeping a second one - so the chip has to follow the
        button as well as the button follow the chip. It followed only one way.
        Measured on a two-stream set: choose «Звук выключен» (chip -1, engine muted),
        press the volume button, and the engine unmutes while the chip still reads
        -1 and its tooltip still says nothing is being heard. `set_audio_source`'s
        own docstring names that disagreement as the thing it avoids.
        """
        if self._closing:
            return
        self._muted = not self._muted
        self._engine.set_muted(self._muted)
        self.transport.set_muted(self._muted)
        self._show_audio_state()

    def _refresh(self) -> None:
        self.transport.set_position(self._engine.position)
        self.transport.set_duration(self._engine.duration)
        self.transport.set_playing(self._engine.playing)
