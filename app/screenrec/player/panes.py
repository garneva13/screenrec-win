"""The picture as a child widget: what the stage layouts are built out of.

`window.ScreenView` is a top-level frameless window, and that is right for the two
*window* layouts of `docs/PLAYER_LAYOUTS.md` - `separate` and `one_per_monitor`. The
three *stage* layouts - `side_by_side`, `stacked` and `focus` - need the same picture
as a child inside one host window, under one transport. That is this module. It is a
separate class rather than a flag on `ScreenView`, because the two differ in every part
a window has: flags, moving, resizing, what the close box means, and whether there is a
close box at all.

**Why a pane owns no player, and why that is the whole point.**
The engine owns one `QMediaPlayer` and one `QVideoSink` per stream; a pane listens to
that sink's `videoFrameChanged` and pushes each frame into its own `QVideoWidget`'s
sink. That bridge is `ScreenView._connect_sink` exactly, for the reason stated there - a
`QVideoFrame` is implicitly shared, so this is a handle copy rather than a picture copy,
and Qt keeps its accelerated presentation path.

Here the bridge is not an implementation detail, it is the reason the class exists.
Because a pane holds nothing but a signal connection, **moving a stream from one pane to
another is a disconnect and a connect**: no `setSource`, no reload, no seek, and so no
position lost and no restart. §3 and §6 of the contract demand exactly that of every
layout change, every swap, every hide and every re-tile - "verified by reading the
position before and after and finding it unchanged" - and `set_stream` is how a stage
keeps that promise without ever touching the engine. A pane that owned a player could
not have the property at all, whatever care was taken elsewhere.

**A reconnection moves no picture by itself.** The corollary caught a paused viewer
out: a signal carries the *next* frame, and a paused stream has no next frame. So the
new sink's last frame is pushed in at the moment of connecting - `_show_held_frame` -
or the swap moves the labels while the two pictures stay crossed until playback
resumes.

**One connection, never two.** The failure this guards against is specific and ugly: a
pane pointed at a new stream while still connected to the old one shows both desktops
interleaved, at the sum of their frame rates. That looks like a broken decoder, not like
a signal connected twice, and it would be chased in the wrong module for an afternoon.
So `set_stream` disconnects before it connects, remembers precisely what it connected
to, and says so out loud when a disconnect it expected to succeed did not.

**A click is not a drag.** §4 calls this "the single most annoying bug this feature can
have": a press that slid across the pane must not also mean "play/pause" or "make this
the main stream". Travel is measured from the press against `layouts.DRAG_SLOP`, never
from the previous mouse position - a real mouse delivers one- and two-pixel steps, as
`ui.surfaces.SurfaceDrag` records, so a slow drag compared against the last position
never clears the slop and the drag would read as a click every time.

**What belongs to a stream and what belongs to the player.** `set_stream` is the swap,
and the state it leaves behind has to be sorted into those two piles. The ended badge of
§6 is one recording having run out, so it goes; the dimming veil is a seek, which is the
host's and applies to every pane at once, so it stays - clearing it would un-dim one
pane in the middle of a seek still in flight. The badge carried over was worse than
stale: `set_label` rewords a default badge, so a pane just handed Экран 1 announced
«Экран 1 — запись закончилась» over a stream that was playing underneath it.

**Two languages, and the three strings that have to be said again.** Everything a
viewer reads here goes through `i18n._()` with the English as the key, and
`retranslate` re-reads it when the language changes - the seek veil, the ended badge
and `setup_problem`. The pane's *label* is not one of them: it carries the stream's
name, which the host takes from the sidecar, so
translating it here would overwrite what the recording calls that screen. The reason
`setup_problem` is held as a kind ("no_sink", "no_signal") rather than as the finished
sentence is the same one: a sentence cannot be translated back.

**What is not measured.** The dimming veil of §6 is a translucent child widget drawn
over the `QVideoWidget`. Under the offscreen plugin, where the tests run, everything is
painted into one backing store and it composites. Over a hardware video surface it may
not, and this machine has no second screen recording to try it against - so the veil is
written, exercised and *not* claimed to dim a live picture until someone has watched it
do so.
"""
from __future__ import annotations

from typing import Any

from PySide6 import QtCore, QtGui, QtMultimediaWidgets, QtWidgets

from .. import i18n
from ..i18n import _
from ..ui import theme
from . import layouts
from .window import (
    LABEL_FADE_MS,
    LABEL_FADE_STEP,
    LABEL_HOLD_MS,
    ScreenLabel,
)
from .window import _report as _window_report

#: Clear space between an overlay and the pane's edge.
PANE_INSET = 10
#: How far the picture is dimmed while a seek is in flight. Not measured against
#: anybody's eyes: chosen so the frozen frame is still readable as context, because the
#: point of the state is "this is the old moment", not "there is nothing here".
BUSY_DIM = 140


def _report(message: str) -> None:
    """Say something on a failure path, on a stream that is actually there.

    Routed through `window._report` rather than copied, because that function already
    knows the two things this one would have to learn again: the player ships as
    `pythonw.exe -m screenrec.player`, where `sys.stdout` is `None` and CPython's
    `print` silently returns, and `player.open._say` is the writer the rest of the
    player uses. One place knowing that is the point; two places knowing it is how one
    of them stops knowing it.

    This is the developer's half. Anything a *viewer* has to know goes in
    `setup_problem`, which the host puts on the transport's notice strip.
    """
    _window_report(f"panes: {message}")


class _BusyVeil(QtWidgets.QWidget):
    """The §6 loading state: the picture dimmed, with one quiet word over it.

    A child widget rather than something the pane paints, for `ScreenLabel`'s reason -
    the picture is a `QVideoWidget` child and is drawn over anything the parent painted
    underneath it. It takes no mouse events, so a seek in flight never swallows the
    click that would make this pane the main stream.

    **It does not animate.** A spinner is a `QTimer` firing ten or more times a second
    for the 200 ms §6 is talking about, and a timer that is created once and left
    running is the version of that which survives into the release. A state that costs
    nothing when it is off is worth more here than a state that spins.
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


def _worth_showing(frame: Any) -> bool:
    """False for the empty frame Qt delivers when a file runs out. See `_on_frame`."""
    valid = getattr(frame, "isValid", None)
    if not callable(valid):
        return True
    try:
        return bool(valid())
    except (RuntimeError, TypeError):
        return True


class VideoPane(QtWidgets.QWidget):
    """One stream's picture, letterboxed on black, inside a host window.

    No window flags and no frame: this is a child widget, and where it sits is the
    host layout's business, not its own. What it owns is the picture, the corner label
    that says which screen this is, the two states of §6, and the answer to "was that a
    click?".

    The label fades like `ScreenView`'s, with `window`'s own numbers rather than copies
    of them, so the two cannot drift apart: a label that stays is in the picture
    forever, and a two-screen recording with no labels at all is a guessing game.
    """

    #: A press that was a click and not a drag.
    clicked = QtCore.Signal()

    def __init__(
        self,
        index: int,
        title: str,
        sink: Any,
        native_size: tuple[int, int] = (0, 0),
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """`sink` is anything with a `videoFrameChanged` signal, or None.

        Typed loosely on purpose: `_connect_sink` in `window` already checks for that
        signal by name rather than for the class, so a test double is as good as a
        `QVideoSink` here, and insisting on the concrete class would buy nothing but a
        reason to open real media in a test.

        `native_size` is the stream's own pixel size, from the sidecar. `(0, 0)`
        means "not told yet", and the first frame that arrives is then asked instead.
        """
        super().__init__(parent)
        self._index = int(index)
        self._title = str(title)
        #: Exactly what `videoFrameChanged` is connected to, or None. Not a flag: the
        #: disconnect has to be from the same object that was connected, and after a
        #: swap the caller's idea of "the old sink" is already the new one.
        self._connected: Any = None
        self._native = (int(native_size[0]), int(native_size[1]))
        self._size_known = self._native[0] > 0 and self._native[1] > 0
        self._busy = False
        self._ended = False
        #: The badge's words when the host chose its own. Kept rather than only
        #: painted, so renaming the stream cannot quietly replace a sentence the host
        #: wrote with this module's default one.
        self._ended_custom = ""
        self._press_global: QtCore.QPoint | None = None
        self._travelled = False
        #: Why this pane will stay black, as a *kind* and not as a sentence: "",
        #: "no_sink" or "no_signal". Kept because a finished sentence cannot be
        #: translated back into the other language, and `retranslate` has to produce
        #: the same complaint again after a language switch.
        self._problem = ""
        #: "" or one sentence saying why this pane will stay black. Read by the host
        #: once it has a transport to put sentences on, exactly as `ScreenView` is: a
        #: pane is built before there is anywhere to say anything, and a black picture
        #: with nobody saying why is the complaint this player already had once.
        self.setup_problem: str = ""

        self.setMouseTracking(True)
        self.setAutoFillBackground(True)
        ground = self.palette()
        ground.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#000000"))
        self.setPalette(ground)

        self.video = QtMultimediaWidgets.QVideoWidget(self)
        self.video.setAspectRatioMode(QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        # The picture must not eat the press that the pane reads as a click or a drag.
        self.video.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        body = QtWidgets.QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.video)

        # The overlays are free-positioned children and deliberately not in the layout:
        # a layout would give the picture a share of the pane instead of all of it.
        self.label = ScreenLabel(self._title, self)
        self.veil = _BusyVeil(_("Seeking…"), self)
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

        self._connect_sink(sink)
        self.wake_label()
        self._place_overlays()
        i18n.notifier.changed.connect(self.retranslate)

    # -- the language ------------------------------------------------------

    def retranslate(self, _language: str = "") -> None:
        """Say everything this pane says, again, in the language now in force.

        Connected to `i18n.notifier.changed` in the constructor, so a switch in the
        settings reaches a player that is already open. The argument is the new
        language code, which nothing here needs - `_()` reads the current one - and it
        has a default so the method can also be called directly.

        The pane's own three strings are the seek veil, the ended badge and
        `setup_problem`. The label is not one of them: it carries the *stream's* name,
        which the host sets from the sidecar, so translating it here would overwrite
        what the recording calls that screen.

        `setup_problem` is rebuilt, but the host read it when the pane was built and
        put it on the transport's notice strip; refreshing that strip is the host's
        own `retranslate`, not this one's.
        """
        self.veil.set_text(_("Seeking…"))
        if self._ended and not self._ended_custom:
            self.badge.set_text(self._ended_text())
        self.setup_problem = self._problem_text()
        self._place_overlays()

    # -- the stream --------------------------------------------------------

    def stream_index(self) -> int:
        """Which of the recording's streams this pane is showing."""
        return self._index

    def title(self) -> str:
        return self._title

    def native_size(self) -> tuple[int, int]:
        return self._native

    def set_native_size(self, size: tuple[int, int]) -> None:
        """Tell the pane the stream's real pixel size, from the sidecar.

        Worth calling even when a frame would eventually say the same thing: a host
        laying panes out wants the stream's shape *before* the first frame arrives,
        and on a paused recording the first frame can be a long time coming.
        """
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            _report(
                f"{self._title}: {size!r} is not a stream size; "
                f"keeping {self._native!r}."
            )
            return
        self._size_known = True
        if (width, height) == self._native:
            return
        self._native = (width, height)
        self._native_changed()

    def _native_changed(self) -> None:
        """The stream's shape changed, or stopped being known.

        Panes whose own size is cut from it override this. "Stopped being known" is a
        real case and not a tidiness one: `set_stream` forgets the size, and a pane
        that went on using the previous stream's shape until a frame arrived would cut
        itself to the wrong one in exactly the gap where nobody can see it is wrong.
        """

    def set_stream(self, index: int, title: str, sink: Any) -> None:
        """Point this pane at a different stream: disconnect the old, connect the new.

        This is the swap, and it is the whole of the swap. Nothing is reloaded, nothing
        is sought and no player is told anything, so the position and the paused state
        of both streams survive it untouched - which is what §6 requires of a layout
        change and of choosing which stream fills the window.

        Safe with `sink=None`, which is how a host says "this pane has nothing to show
        for the moment"; the old connection still goes, and `setup_problem` says why
        the pane is black.

        The stream's size is forgotten rather than kept: the other screen may well be
        the portrait one, and a host that went on being told the old shape would lay
        this pane out for a stream it no longer holds. The host should call
        `set_native_size` with the new stream's size; if
        it does not, the next frame is asked. Until one of those happens
        `native_size()` reports `(0, 0)` - "not told yet", the same as a pane that was
        built without one - rather than the previous stream's dimensions, because a
        host asking a pane how big its stream is must not be told the old one's size.

        The ended badge of §6 goes with the stream it was about. The host has no reason
        to call `set_ended(False)` as well: §4 calls the swap "a signal reconnection"
        with nothing else changed, and a badge left standing would not merely be stale,
        it would be re-worded by `set_label` into a sentence about the stream that has
        just arrived. The busy veil is deliberately *not* cleared: a seek belongs to the
        player and applies to every pane at once.

        The *picture* follows the stream at once as well, from the frame the new sink
        is already holding - see `_show_held_frame` for why a reconnection alone leaves
        a paused viewer looking at two crossed screens.
        """
        self._index = int(index)
        # Before the label: `set_label` rewrites a default badge, so clearing the ended
        # state afterwards would still have let «Экран 1 — запись закончилась» be
        # painted for an instant over a stream that is playing.
        self.set_ended(False)
        self.set_label(str(title))
        self._native = (0, 0)
        self._size_known = False
        self._native_changed()
        self._disconnect_sink()
        self._set_problem("")
        self._connect_sink(sink)

    def _disconnect_sink(self) -> None:
        """Drop the current connection, if there is one. Leaves `_connected` at None."""
        sink = self._connected
        self._connected = None
        if sink is None:
            return
        changed = getattr(sink, "videoFrameChanged", None)
        if changed is None:
            return
        try:
            changed.disconnect(self._on_frame)
        except (RuntimeError, TypeError) as exc:
            # PySide raises rather than returning False when the connection has already
            # gone - a sink whose C++ side was deleted, most likely. Said out loud
            # because the alternative reading is "the old stream is still connected",
            # and that is the interleaved-frames bug in the module docstring.
            _report(
                f"{self._title}: the old sink would not disconnect ({exc!r}); "
                "if the picture now shows two screens at once, this is why."
            )

    def _set_problem(self, kind: str) -> None:
        """Record *why* the pane will stay black, and write the sentence for it."""
        self._problem = kind
        self.setup_problem = self._problem_text()

    def _problem_text(self) -> str:
        """`_problem` as the sentence the host puts on the notice strip.

        Built here rather than stored, so a language switch can ask for it again. The
        signal's name is left untranslated inside it on purpose: it is the one word in
        the sentence that a search of the source has to find.
        """
        if self._problem == "no_sink":
            return _("{name}: no source of frames — this pane will stay black.").format(
                name=self._title
            )
        if self._problem == "no_signal":
            return _(
                "{name}: the source of frames has no videoFrameChanged signal "
                "— this pane will stay black."
            ).format(name=self._title)
        return ""

    def _connect_sink(self, sink: Any) -> None:
        """Bridge a sink's frames into this pane's own `QVideoWidget`."""
        if sink is None:
            self._set_problem("no_sink")
            _report(f"{self._title}: no video sink was given; the pane will stay black.")
            return
        changed = getattr(sink, "videoFrameChanged", None)
        if changed is None:
            self._set_problem("no_signal")
            _report(
                f"{self._title}: {type(sink).__name__} has no videoFrameChanged "
                "signal; the pane will stay black."
            )
            return
        changed.connect(self._on_frame)
        self._connected = sink
        self._show_held_frame(sink)

    def _show_held_frame(self, sink: Any) -> None:
        """Repaint from the frame the new sink is already holding.

        `videoFrameChanged` fires when the *next* frame is decoded, so a pane that has
        just been pointed at another stream keeps the previous stream's picture until
        one arrives. While playing that is 16 ms and invisible. While paused no frame
        will ever arrive, and a swap then moves the labels and leaves the pictures
        where they were: measured while paused, the main pane went on showing a 640x360
        frame of screen 1 under a label reading «Экран 2», and the other pane a 360x640
        frame of screen 2 under «Экран 1». The
        engine was not called and the position did not move - the invariant held - the
        two streams were simply named wrongly for as long as the viewer stayed paused.

        `QVideoSink` keeps the last frame it was given, so asking for it is the whole
        repair. It goes through `_push` rather than straight into the widget so that
        the held frame is treated exactly like a decoded one, `_adopt_size` included:
        `set_stream` forgets the stream's shape, and when the host has no sidecar size
        to give back, this frame is the first thing that can say it. `_push` and not
        `_on_frame`, because a sink holding nothing answers with an invalid frame and
        that is the truthful picture here - the pane has nothing of its own to show
        yet - while the same frame arriving from a decoder means a file has ended.

        A sink that cannot be asked - a double carrying the signal and nothing else -
        is left alone rather than blanked. Nothing is known about what it holds, and
        guessing "nothing" would throw away a picture that may well be right.
        """
        held = getattr(sink, "videoFrame", None)
        if not callable(held):
            return
        try:
            self._push(held())
        except (RuntimeError, TypeError) as exc:
            # A sink whose C++ side has already gone, or one whose `videoFrame` is not
            # a frame at all. Said out loud because the visible result is a pane
            # showing the stream it is no longer labelled with, which reads as a swap
            # bug rather than as a sink that could not be asked for its last frame.
            _report(
                f"{self._title}: the new sink would not give up its last frame "
                f"({exc!r}); this pane keeps the previous picture until the next "
                "frame arrives."
            )

    def _on_frame(self, frame: Any) -> None:
        """A frame decoded by the stream this pane is watching.

        Qt pushes an **invalid** frame when a file runs out - measured on 2026-11-05
        against this project's own recording, the last thing each sink was given was
        `isValid() == False`. Painting it is the same call `_blank()` makes on purpose,
        so the picture went black at the end of every recording while §5 of
        `docs/PLAYER_LAYOUTS.md` promises the last frame is held. Here it is dropped.

        `_push` is the way in for a frame that is *meant* to clear the pane, which is a
        different thing with the same shape: `_show_held_frame` asks a new sink what it
        is holding, and "nothing" is the truthful picture for a stream that has decoded
        nothing yet.
        """
        if not _worth_showing(frame):
            return
        self._push(frame)

    def _push(self, frame: Any) -> None:
        """Paint a frame, valid or not. See `_on_frame` for which calls which."""
        target = self.video.videoSink()
        if target is None:  # the widget was torn down between frames
            return
        if not self._size_known:
            self._adopt_size(frame)
        target.setVideoFrame(frame)

    def _adopt_size(self, frame: Any) -> None:
        """Learn the stream's shape from a frame, once, when nobody has said it.

        Runs at most once per stream - `_size_known` is set even when the frame cannot
        answer, so this is one attribute test per frame afterwards and not a hopeful
        retry sixty times a second.
        """
        size = getattr(frame, "size", None)
        shape = size() if callable(size) else None
        if not isinstance(shape, QtCore.QSize):
            self._size_known = True  # not a frame that can be asked; stop asking
            return
        if shape.width() <= 0 or shape.height() <= 0:
            return  # an invalid frame says nothing; the next one may
        self.set_native_size((shape.width(), shape.height()))

    # -- the label ---------------------------------------------------------

    def set_label(self, text: str) -> None:
        """Say which screen this is. §3: the name follows the stream, not the pane."""
        self._title = str(text)
        self.label.set_text(self._title)
        if self._ended and not self._ended_custom:
            self.badge.set_text(self._ended_text())
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

    # -- the two states of §6 ---------------------------------------------

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
        # Stacked by `_place_overlays` rather than by a `raise_()` here, so that this
        # module has one answer to "what is drawn over what". A subclass that raises
        # its own overlays after the veil would otherwise disagree with a veil raised
        # on its own - until the first `resizeEvent` of the seek ran `_place_overlays`
        # and silently put them back on top, which is two pictures of the same state
        # decided by whether the window happened to be nudged.
        self._place_overlays()

    def has_ended(self) -> bool:
        return self._ended

    def set_ended(self, ended: bool, text: str = "") -> None:
        """Hold the last frame with a small badge - §6, the shorter of two streams.

        Nothing here stops anything: the stream has already run out, the engine knows
        it, and the pane's job is to say so rather than to go black, because a black
        rectangle beside a playing one reads as a crash.
        """
        ended = bool(ended)
        self._ended = ended
        self._ended_custom = " ".join(str(text).split())
        if not ended:
            self.badge.hide()
            return
        self.badge.set_text(self._ended_custom or self._ended_text())
        self.badge.show()
        self.badge.raise_()
        self._place_overlays()

    def _ended_text(self) -> str:
        return _("{name} — the recording ends here").format(name=self._title)

    # -- geometry ----------------------------------------------------------

    def _place_overlays(self) -> None:
        self.label.move(PANE_INSET, PANE_INSET)
        self.label.raise_()
        self.badge.move(
            max(PANE_INSET, (self.width() - self.badge.width()) // 2),
            max(PANE_INSET, self.height() - self.badge.height() - PANE_INSET),
        )
        if self.badge.isVisible():
            self.badge.raise_()
        self.veil.setGeometry(self.rect())
        if self._busy:
            self.veil.raise_()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._place_overlays()

    # -- the mouse ---------------------------------------------------------

    def _note_travel(self, point: QtCore.QPoint) -> None:
        """Mark this press as a drag once it has gone further than the slop.

        Measured from the press and never from the previous position: a real mouse
        arrives in one- and two-pixel steps, so comparing against the last point never
        clears `layouts.DRAG_SLOP` and every drag would end as a click.
        """
        if self._press_global is None:
            return
        if (point - self._press_global).manhattanLength() > layouts.DRAG_SLOP:
            self._travelled = True

    def travelled(self) -> bool:
        """True when the press currently down has become a drag."""
        return self._travelled

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._press_global = event.globalPosition().toPoint()
        self._travelled = False
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self.wake_label()
        self._note_travel(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        # A press that was forgotten - or that never happened on this widget - cannot
        # be a click. Clearing `_press_global` is how a subclass disqualifies one.
        clean = self._press_global is not None and not self._travelled
        self._press_global = None
        self._travelled = False
        if clean:
            self.clicked.emit()
        event.accept()

    def enterEvent(self, event: QtGui.QEnterEvent) -> None:
        self.wake_label()
        super().enterEvent(event)
