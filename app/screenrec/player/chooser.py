"""The one question the player asks before it shows anything: where do the screens go?

`docs/PLAYER_LAYOUTS.md` §2.1 puts a dialog in front of a two-screen recording, and this
is it. Everything it knows about geometry it asks `layouts` for; everything it knows
about the clock is nothing at all, because there is no clock yet - the dialog is shown
*before* playback starts, so the engine does not exist while this window is open and
cannot be disturbed by it. That is the cheapest way to honour §6's "none of this may
create a second clock": do not import the engine.

Four decisions worth stating, because each had an alternative.

**The thumbnails are drawn, not shipped.** A card showing what `stacked` looks like
could have been five PNGs. It is five `QPainter` calls instead: nothing to ship,
nothing to lose, nothing to re-export when the palette moves, and the pictures follow
the theme's colours and the card's selected state for free. The shapes themselves come
from `layouts.tile_rects` rather than from arithmetic written here, so a card cannot
promise an arrangement the stage will not build. `_strips` is the one place that
computes a rectangle, and only for a recording with more than two screens - a case §5's
four two-window schemes do not define.

**`one_per_monitor` is shown and disabled, never hidden.** §2.1 says so and gives the
reason: "a disabled card that says why teaches more than a card that is not there". The
decision is `layouts.possible`'s, never this module's, so there is exactly one answer to
"can this be built" in the program.

**Every card that can be chosen carries a reason line.** §2.2 ends on exactly that:
`separate` and `focus` "are never *recommended* ... but both are always offered, with a
card and a reason line of their own", and `layouts.score` says it from the other side -
the sentence is built for a layout that can never win "so a card the viewer picks says
what they will get". An earlier draft here printed the line under the recommended card
only, reading §2.2's "the dialog says the reason in one line" as a rule about the
recommendation rather than about the cards. The cost of that reading was the viewer who
does not take the advice: choosing `focus` or `separate` told them nothing, while the
sentence describing what they were about to get sat one function call away. The
recommended card's line is the one `layouts.recommend()` returned; every other enabled
card's is `layouts._reason`'s for that card. Reaching past the underscore is deliberate
and is the whole of `_card_reason`'s argument: `recommend` returns that sentence only
for the winner, and the alternative is a second copy of five sentences in this file,
which is the one thing `layouts` exists to prevent. They are told apart by colour rather
than by presence - the recommendation in the accent, the rest quiet - because five
accent lines down a dialog would leave nothing marking the card advised. Nothing
is cached: `_reason`'s own docstring forbids it, a cached line being the one string in
the dialog a language switch cannot reach, and this dialog is the first thing a viewer
sees.

**The generated screen names are re-generated on a language switch, when the caller
says they may be.** The reason lines name the streams - "Экран 1 целиком, 97 %" - and
those names were invented by `window.screen_titles` at the moment the caller built its
`Stream` list, so the sentence around them would follow a switch while the names
themselves stayed in the old language. `PlayerStage._rename_streams` solves the same
problem and this is the same solution, with the same guard: the names are only rebuilt
when a `recording` was handed in, because a caller that invented its own titles - or
passed the viewer's own word for a screen - owns those words and must not find them
replaced with "Screen 2". `screen_titles` is what knows the difference, so it is asked
rather than second-guessed here.

**An ordinary framed dialog, not one of `ui.surfaces`' painted planes.** The rest of the
application is frameless because it has to sit over a recording without reading as
application chrome. Nothing is being recorded here, this window must take focus (§7's
keyboard is the point of it), and a modal window with no title bar is one the viewer
cannot move off the thing they are trying to look at. The cards inside it are painted
the way `ui.sources.SourceCard` is painted, so it still reads as the same program.

**Where it opens.** `dialog_spot` centres the window inside *one real screen's*
available area, chosen by overlap with the area the stage will use, exactly as
`window.transport_spot` picks the transport's screen and for the same reason recorded
there: a window placed from the bounding box of several monitors lands in the empty
corner between them, where it cannot be reached, dragged back or closed. A dialog that
cannot be reached is worse than no dialog, because it is also modal.
"""
from __future__ import annotations

from typing import Any, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n
from ..i18n import _, pick
from ..ui import icons, theme
from . import layouts
from .window import _report as _window_report
from .window import screen_areas, screen_titles

# ------------------------------------------------------------------ the sizes

#: One card, and the grid they sit in. Three columns by two rows holds §1's five
#: layouts without scrolling on any screen this program is likely to meet, and the
#: scroll area round them is what saves the one it is not.
#:
#: Five into three leaves the last row short, and a short row is **centred** rather
#: than left hanging: see `_place_cards`. Picture in picture was the sixth card and
#: its removal is what made the row short; `COLUMNS` was deliberately not retuned,
#: because three across is what makes the dialog fit the screens it was measured on.
CARD_W = 236
CARD_H = 244
CARD_GAP = 12
COLUMNS = 3

CARD_PAD = 12
#: The thumbnail well inside a card, and the inset from it to the drawn arrangement.
THUMB_H = 100
THUMB_INSET = 10
TITLE_H = 20
#: Three lines of description, which is one more than any of §1's five needs on this
#: machine. The slack is deliberate: these boxes are sized in pixels and filled with a
#: translated sentence in whatever font the system has, and `drawText` answers a box
#: that is one line too short by cutting the sentence off rather than by scrolling.
DESC_H = 38
#: The reason line under the recommended card, or the "why not" under a disabled one.
#: Three lines, not two: a Russian reason naming both streams and both percentages -
#: "Экран 1 — 97 %, Экран 2 — 50 % от натуральной величины" - and a line that is
#: word-wrapped into a box too short for it is clipped by `drawText`, so the viewer
#: loses the end of the one sentence the recommendation is made of.
NOTE_H = 48

#: The "Recommended" pill, drawn in the well's top-right corner.
BADGE_H = 18
BADGE_PAD = 8
BADGE_INSET = 6

DIALOG_PAD = 20

#: The corner of one screen in a drawn arrangement. Small enough to read as a monitor
#: at thumbnail size and not as a button.
_SCREEN_RADIUS = 3.0

#: Platforms with nobody to answer a modal dialog. `player/open.py` names the same list
#: for the same reason and is deliberately not imported for it: `open` is what opens a
#: recording, so it imports the player's windows, and a top-level import back from here
#: would be a cycle that breaks opening a recording at all. Four platform names are a
#: smaller cost than that.
HEADLESS_PLATFORMS = frozenset({"offscreen", "minimal", "vnc", ""})


def _report(message: str) -> None:
    """Say something on a failure path, on a stream that is actually there.

    Routed through `window._report` rather than copied: that function already knows the
    player ships as `pythonw.exe -m screenrec.player`, where `sys.stdout` is `None` and
    CPython's `print` silently returns. This is the developer's half of a problem;
    anything a *viewer* has to know is a line in the dialog itself.
    """
    _window_report(f"chooser: {message}")


# ------------------------------------------------------------- words with forms


def _screens_word(count: int) -> str:
    """The right form of "screen" for `count` - three of them in Russian."""
    return i18n.plural(
        count,
        pick("plural.screen.one", "screen"),
        pick("plural.screen.few", "screens"),
        pick("plural.screen.many", "screens"),
    )


def monitors_available(count: int) -> str:
    """The chip over the cards: "Доступен 1 монитор", "Доступно 5 мониторов".

    The whole sentence has three forms rather than just the noun, which is why these
    are `pick()` keys and not one `_()` template with a word substituted into it.
    Russian agrees the predicate with the number as well as the noun - *Доступен* one
    monitor, *Доступно* five of them - so a template with a hole for "монитор" would
    be right for 1 and wrong for every other count. English takes the usual two forms
    out of the same call, since `i18n.plural` returns `one` for 1 and `many` otherwise
    whenever the language is not Russian.
    """
    return i18n.plural(
        count,
        pick("chooser.monitors.one", "{count} monitor available"),
        pick("chooser.monitors.few", "{count} monitors available"),
        pick("chooser.monitors.many", "{count} monitors available"),
    ).format(count=count)


def heading_for(count: int) -> str:
    """"This recording has two screens", in the language in force now.

    Two is spelled out because that is the sentence a viewer reads nine times in ten
    and "В записи 2 экрана" reads like a log line. Any other count goes through the
    plural rule, and **both** languages take the counted word from it. The English
    used to read "This recording has {count} screens" with the `{word}` hole only in
    the Russian, which `str.format` quietly ignored: correct for 3 and 5, and "This
    recording has 1 screens" for the one-stream recording that `choose()` refuses but
    that the constructor, and the dialog's own tests, reach directly.
    """
    if count == 2:
        return _("This recording has two screens")
    return _("This recording has {count} {word}").format(
        count=count, word=_screens_word(count)
    )


# --------------------------------------------------------- the drawn thumbnails


def _strips(frame: QtCore.QRect, count: int, across: bool) -> list[QtCore.QRect]:
    """`count` equal bands filling `frame`, left to right or top to bottom.

    Two bands are `layouts.tile_rects`' own equal-halves arithmetic, so they are asked
    for rather than computed - that is the contract's answer to "what does half of this
    area look like", including the rule that the second rectangle takes whatever the
    first left so no strip of background shows between them.

    More than two screens has no entry in §5's table of schemes, so the general split
    is done here. It is thumbnail decoration and nothing places a window from it; a
    recording with three screens still gets a picture that says "side by side".
    """
    if count <= 1:
        return [QtCore.QRect(frame)]
    if count == 2:
        tiling = layouts.TILING_EQUAL_LR if across else layouts.TILING_EQUAL_TB
        return layouts.tile_rects(frame, tiling)
    out: list[QtCore.QRect] = []
    span = frame.width() if across else frame.height()
    start = frame.x() if across else frame.y()
    for index in range(count):
        head = start + span * index // count
        tail = start + span * (index + 1) // count
        if across:
            out.append(QtCore.QRect(head, frame.y(), tail - head, frame.height()))
        else:
            out.append(QtCore.QRect(frame.x(), head, frame.width(), tail - head))
    return out


def _paint_screen(
    painter: QtGui.QPainter,
    rect: QtCore.QRect,
    number: int,
    lit: bool,
) -> None:
    """One numbered rectangle of an arrangement: a filled plane with its stream's digit.

    The digit is not translated and never will be: §3 names the panes "Экран 1" and
    "Экран 2", and the part that identifies them is the number. At this size a word
    would not fit beside it anyway.
    """
    if rect.width() < 2 or rect.height() < 2:
        return
    colours = theme.COLOURS
    box = QtCore.QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.setPen(
        QtGui.QPen(QtGui.QColor(colours["accent" if lit else "line_strong"]), 1.0)
    )
    painter.setBrush(QtGui.QColor(colours["panel_raised"]))
    painter.drawRoundedRect(box, _SCREEN_RADIUS, _SCREEN_RADIUS)
    if rect.width() < 14 or rect.height() < 12:
        return  # a rectangle this small is a shape, not a label
    font = QtGui.QFont(painter.font())
    font.setPointSizeF(8.5)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(colours["accent_light" if lit else "text_dim"]))
    painter.drawText(box, int(QtCore.Qt.AlignmentFlag.AlignCenter), str(number))


def _paint_switch(painter: QtGui.QPainter, frame: QtCore.QRect, lit: bool) -> None:
    """The two-button **1** | **2** switch that says `focus` is not just one screen.

    Without it the card is a single empty rectangle, and nothing on it says the other
    stream can be brought back. §3 gives `focus` exactly this control, so drawing it is
    describing the layout rather than decorating the card.
    """
    colours = theme.COLOURS
    width, height = 34, 15
    left = frame.center().x() - width // 2
    top = frame.bottom() - height - 6
    box = QtCore.QRectF(left, top, width, height)
    painter.setPen(QtGui.QPen(QtGui.QColor(colours["line_strong"]), 1.0))
    painter.setBrush(QtGui.QColor(colours["bg"]))
    painter.drawRoundedRect(box, 4.0, 4.0)
    half = QtCore.QRectF(box.left(), box.top(), box.width() / 2.0, box.height())
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(colours["accent" if lit else "line"]))
    painter.drawRoundedRect(half.adjusted(1.5, 1.5, -0.5, -1.5), 3.0, 3.0)
    font = QtGui.QFont(painter.font())
    font.setPointSizeF(6.5)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(colours["accent_text" if lit else "text_dim"]))
    painter.drawText(half, int(QtCore.Qt.AlignmentFlag.AlignCenter), "1")
    painter.setPen(QtGui.QColor(colours["text_faint"]))
    painter.drawText(
        half.translated(box.width() / 2.0, 0),
        int(QtCore.Qt.AlignmentFlag.AlignCenter),
        "2",
    )


def _paint_stand(painter: QtGui.QPainter, rect: QtCore.QRect) -> None:
    """A monitor's foot, so `one_per_monitor`'s thumbnail reads as two monitors."""
    colours = theme.COLOURS
    width = max(6, rect.width() // 4)
    left = rect.center().x() - width // 2
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(colours["line_strong"]))
    painter.drawRect(QtCore.QRect(left, rect.bottom() + 2, width, 2))


def paint_arrangement(
    painter: QtGui.QPainter,
    frame: QtCore.QRect,
    layout: str,
    sizes: Sequence[tuple[int, int]],
    lit: bool,
) -> None:
    """Draw `layout`'s shape inside `frame`: numbered rectangles, nothing else.

    `sizes` are the streams' native sizes. Only their *number* is read now - a
    recording of three screens is drawn as three strips, not two - but the sizes
    themselves are kept in the signature because every caller has them to hand and a
    layout that needs a shape again would otherwise change this function's callers too.
    `lit` is the card's selected state, which the arrangement follows so that the
    picture and the card are one object rather than a picture inside a frame.
    """
    count = max(2, len(sizes))
    if layout == layouts.LAYOUT_SIDE_BY_SIDE:
        for index, rect in enumerate(_strips(frame, count, True)):
            _paint_screen(painter, rect.adjusted(0, 0, -1, 0), index + 1, lit)
        return
    if layout == layouts.LAYOUT_STACKED:
        for index, rect in enumerate(_strips(frame, count, False)):
            _paint_screen(painter, rect.adjusted(0, 0, 0, -1), index + 1, lit)
        return
    if layout == layouts.LAYOUT_FOCUS:
        _paint_screen(painter, frame, 1, lit)
        _paint_switch(painter, frame, lit)
        return
    if layout == layouts.LAYOUT_SEPARATE:
        # Inset and nudged apart, so two free-floating windows do not read as one
        # window split down the middle - which is `side_by_side`, three cards up.
        for index, rect in enumerate(_strips(frame, count, True)):
            drawn = rect.adjusted(5, 9 if index % 2 else 3, -5, -3 if index % 2 else -9)
            _paint_screen(painter, drawn, index + 1, lit)
        return
    if layout == layouts.LAYOUT_ONE_PER_MONITOR:
        for index, rect in enumerate(_strips(frame, count, True)):
            drawn = rect.adjusted(4, 2, -4, -6)
            _paint_screen(painter, drawn, index + 1, lit)
            _paint_stand(painter, drawn)
        return
    _report(f"no thumbnail is drawn for the unknown layout {layout!r}")


def _pill_width(text: str, font: QtGui.QFont) -> int:
    """How wide a pill has to be to hold `text` without eliding it."""
    return QtGui.QFontMetrics(font).horizontalAdvance(text) + 2 * BADGE_PAD


def _paint_pill(
    painter: QtGui.QPainter,
    box: QtCore.QRectF,
    text: str,
    fill: str,
    ink: str,
) -> None:
    """A small rounded label: the "Recommended" badge and the monitor-count chip."""
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(theme.COLOURS[fill]))
    painter.drawRoundedRect(box, box.height() / 2.0, box.height() / 2.0)
    painter.setPen(QtGui.QColor(theme.COLOURS[ink]))
    painter.drawText(box, int(QtCore.Qt.AlignmentFlag.AlignCenter), text)


def _font(size: float, bold: bool = False) -> QtGui.QFont:
    font = QtGui.QFont()
    font.setPointSizeF(size)
    font.setBold(bold)
    return font


# ----------------------------------------------------------------- the pieces


class MonitorChip(QtWidgets.QWidget):
    """"Доступен 1 монитор" beside the heading, with a monitor glyph.

    A painted widget rather than a styled `QLabel` because the pill shape, the glyph
    and the dimmed ink are the same three things `ui.surfaces.Chip` is made of, and a
    style sheet rule for one label in one dialog is a rule somebody has to find later.
    It is not a `surfaces.Chip`: that one is a *button*, with a chevron promising a menu
    this value does not have.
    """

    def __init__(self, count: int = 0, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._count = int(count)
        self.setFixedHeight(26)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed
        )
        i18n.notifier.changed.connect(self.retranslate)
        self.retranslate()

    def set_count(self, count: int) -> None:
        self._count = int(count)
        self.retranslate()

    def text(self) -> str:
        """The sentence as it reads now. Never stored - see `monitors_available`."""
        return monitors_available(self._count)

    def retranslate(self) -> None:
        """Re-measure and repaint. The words are read in `text()`, not kept here.

        The accessible name is set as well as painted: the whole chip is a drawing,
        so without it a screen reader finds an unnamed blank beside the heading.
        """
        self.setAccessibleName(self.text())
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QtCore.QSize:
        width = _pill_width(self.text(), _font(8.5)) + 20
        return QtCore.QSize(width, 26)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            colours = theme.COLOURS
            box = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["line"]), 1.0))
            painter.setBrush(QtGui.QColor(colours["panel"]))
            radius = box.height() / 2.0
            painter.drawRoundedRect(box, radius, radius)
            glyph = 13.0
            icons.draw(
                painter,
                "monitor",
                QtCore.QRectF(9.0, (self.height() - glyph) / 2.0, glyph, glyph),
                colours["text_dim"],
                weight=1.6,
            )
            painter.setFont(_font(8.5))
            painter.setPen(QtGui.QColor(colours["text_soft"]))
            painter.drawText(
                QtCore.QRectF(26.0, 0.0, self.width() - 34.0, self.height()),
                int(
                    QtCore.Qt.AlignmentFlag.AlignLeft
                    | QtCore.Qt.AlignmentFlag.AlignVCenter
                ),
                self.text(),
            )
        finally:
            painter.end()


class _CardScroll(QtWidgets.QScrollArea):
    """A scroll area that asks for the room its contents actually need.

    `QScrollArea.sizeHint` goes through `viewportSizeHint`, which bounds the widget's
    own hint by `maximumViewportSize()` - a function of the scroll area's *current*
    size, which before the first layout is nothing. Measured here: a grid whose hint is
    732x460 came back as 432x288, and the dialog opened at two thirds of its own width
    with the cards already scrolled sideways. So the hint is answered from the widget,
    and only the frame is added.

    `minimumSizeHint` is deliberately left alone. It is what lets the window shrink on
    a short screen, which is the whole reason the cards are in a scroll area.
    """

    def sizeHint(self) -> QtCore.QSize:
        held = self.widget()
        if held is None:
            return super().sizeHint()
        hint = held.sizeHint()
        frame = 2 * self.frameWidth()
        return QtCore.QSize(hint.width() + frame, hint.height() + frame)


class LayoutCard(QtWidgets.QAbstractButton):
    """One layout, offered: a drawn arrangement over a title, a line and a reason.

    Painted whole, the way `ui.sources.SourceCard` is, rather than assembled from child
    labels. The reason is the same one that card gives: a card is one object with one
    selected state, and a stack of styled children is five objects that have to be kept
    agreeing about it.

    Nothing textual is cached. `title_text` and `description_text` ask `layouts` at the
    moment they are called, so a language switch needs only a repaint; the reason and
    the "why not" line are *set* by the dialog, because they are built from numbers only
    the dialog has, and the dialog re-sets them on a switch.
    """

    #: Clicked, or Space - "this is the one I want", not "open it".
    chosen = QtCore.Signal(str)
    #: Double-click, or Enter - "open it".
    activated = QtCore.Signal(str)
    #: An arrow key, as (dx, dy) in the card grid. Handled here rather than in the
    #: dialog because the cards live inside a `QScrollArea`, and a `QScrollArea` eats
    #: arrow keys to scroll with: an event that reached the dialog's `keyPressEvent`
    #: would have had to get past it first, and would not.
    arrow = QtCore.Signal(int, int)

    def __init__(
        self,
        layout: str,
        sizes: Sequence[tuple[int, int]],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if layout not in layouts.LAYOUTS:
            raise ValueError(f"{layout!r} is not one of {', '.join(layouts.LAYOUTS)}")
        self.layout_id = layout
        self._sizes = [tuple(size) for size in sizes]
        self._selected = False
        self._recommended = False
        self._reason = ""
        self._note = ""
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._on_clicked)
        i18n.notifier.changed.connect(self.retranslate)
        self.retranslate()

    # -- what it says ------------------------------------------------------

    def title_text(self) -> str:
        return layouts.title(self.layout_id)

    def description_text(self) -> str:
        return layouts.description(self.layout_id)

    def badge_text(self) -> str:
        return _("Recommended")

    def reason_text(self) -> str:
        """What choosing this card gives, in one line, or "" on a disabled card.

        Every offered layout has one (§2.2); a disabled card shows `note_text` in its
        place, because the only thing a viewer has to read there is why it is dead.
        """
        return self._reason

    def note_text(self) -> str:
        """Why this card is disabled, or "" when it is not."""
        return self._note

    def set_reason(self, text: str) -> None:
        if text != self._reason:
            self._reason = text
            self.update()

    def set_note(self, text: str) -> None:
        if text != self._note:
            self._note = text
            self.update()

    def retranslate(self) -> None:
        """Re-read every string this card shows.

        A repaint is the whole of it: the title and the description are read from
        `layouts` while the card is being drawn, so they cannot go stale. The three
        strings Qt holds on our behalf - the tooltip and the two accessible ones - are
        set again here. The accessible name matters more than usual on this widget:
        everything it shows is painted, so a screen reader has nothing to read off it
        unless it is told.
        """
        self.setToolTip(f"{self.title_text()}\n{self.description_text()}")
        self.setAccessibleName(self.title_text())
        self.setAccessibleDescription(self.description_text())
        self.update()

    # -- state -------------------------------------------------------------

    def set_selected(self, value: bool) -> None:
        if value != self._selected:
            self._selected = value
            self.update()

    def is_selected(self) -> bool:
        return self._selected

    def set_recommended(self, value: bool) -> None:
        if value != self._recommended:
            self._recommended = value
            self.update()

    def is_recommended(self) -> bool:
        return self._recommended

    # -- input -------------------------------------------------------------

    def _on_clicked(self) -> None:
        self.chosen.emit(self.layout_id)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        """Open straight away. The single click before it has already selected this."""
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.activated.emit(self.layout_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        """Arrows move, Enter opens. Space is left to `QAbstractButton`, which selects.

        Enter is taken here rather than left to the dialog's default button because a
        card has the focus while the arrows are being used, and a viewer who has just
        arrowed onto a card means "this one" by pressing Enter. Routing it through the
        dialog's `activated` handler keeps that the same action as a double-click.
        """
        key = event.key()
        steps = {
            QtCore.Qt.Key.Key_Left: (-1, 0),
            QtCore.Qt.Key.Key_Right: (1, 0),
            QtCore.Qt.Key.Key_Up: (0, -1),
            QtCore.Qt.Key.Key_Down: (0, 1),
        }
        step = steps.get(key)
        if step is not None:
            self.arrow.emit(step[0], step[1])
            event.accept()
            return
        if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            self.activated.emit(self.layout_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.update()
        super().leaveEvent(event)

    # -- painting ----------------------------------------------------------

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(CARD_W, CARD_H)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        colours = theme.COLOURS
        usable = self.isEnabled()
        hovered = self.underMouse() and usable
        rect = QtCore.QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        radius = float(theme.RADIUS["card"])

        if self._selected:
            ring = QtGui.QColor(colours["accent"])
            ring.setAlpha(70)
            painter.setPen(QtGui.QPen(ring, 2.5))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                rect.adjusted(-1.0, -1.0, 1.0, 1.0), radius + 1, radius + 1
            )

        if not usable:
            fill = colours["bg"]
        elif self.isDown():
            fill = colours["line"]
        elif hovered:
            fill = colours["hover"]
        else:
            fill = colours["panel"]
        if self._selected:
            pen = QtGui.QPen(QtGui.QColor(colours["accent"]), 1.5)
        else:
            pen = QtGui.QPen(
                QtGui.QColor(colours["line_strong" if hovered else "line"]), 1.0
            )
        painter.setPen(pen)
        painter.setBrush(QtGui.QColor(fill))
        painter.drawRoundedRect(rect, radius, radius)

        if self.hasFocus():
            # The focus ring is drawn as well as the selection ring, never instead of
            # it: arrowing across the grid moves both together, but Tab can leave the
            # focus on a card the viewer has not chosen, and a dialog that forgot to
            # say where the keyboard is cannot be used without a mouse.
            painter.setPen(QtGui.QPen(QtGui.QColor(colours["accent_light"]), 1.0))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                rect.adjusted(2.5, 2.5, -2.5, -2.5), radius - 2, radius - 2
            )

        well = QtCore.QRectF(
            rect.left() + CARD_PAD,
            rect.top() + CARD_PAD,
            rect.width() - 2 * CARD_PAD,
            float(THUMB_H),
        )
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(colours["bg"]))
        small = float(theme.RADIUS["small"])
        painter.drawRoundedRect(well, small, small)

        painter.save()
        try:
            if not usable:
                # Half-transparent rather than a second set of greyed colours: the
                # arrangement is still worth seeing on a card that says why it cannot
                # be used, and one opacity cannot drift out of step with the palette.
                painter.setOpacity(0.35)
            frame = well.adjusted(
                THUMB_INSET, THUMB_INSET, -THUMB_INSET, -THUMB_INSET
            ).toRect()
            paint_arrangement(
                painter, frame, self.layout_id, self._sizes, self._selected and usable
            )
        finally:
            painter.restore()

        if self._recommended:
            painter.setFont(_font(7.5, bold=True))
            text = self.badge_text()
            width = _pill_width(text, painter.font())
            _paint_pill(
                painter,
                QtCore.QRectF(
                    well.right() - BADGE_INSET - width,
                    well.top() + BADGE_INSET,
                    float(width),
                    float(BADGE_H),
                ),
                text,
                "accent",
                "accent_text",
            )

        self._paint_text(painter, rect, well, usable, hovered)

    def _paint_text(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        well: QtCore.QRectF,
        usable: bool,
        hovered: bool,
    ) -> None:
        colours = theme.COLOURS
        left = rect.left() + CARD_PAD
        width = rect.width() - 2 * CARD_PAD
        left_top = int(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
        )

        title_font = _font(9.5, bold=True)
        painter.setFont(title_font)
        lit = (hovered or self._selected) and usable
        if not usable:
            painter.setPen(QtGui.QColor(colours["disabled"]))
        else:
            painter.setPen(QtGui.QColor(colours["text" if lit else "text_soft"]))
        title_box = QtCore.QRectF(left, well.bottom() + 8, width, float(TITLE_H))
        painter.drawText(
            title_box,
            left_top,
            QtGui.QFontMetricsF(title_font).elidedText(
                self.title_text(), QtCore.Qt.TextElideMode.ElideRight, int(width)
            ),
        )

        painter.setFont(_font(8.5))
        painter.setPen(
            QtGui.QColor(colours["disabled" if not usable else "text_dim"])
        )
        description_box = QtCore.QRectF(
            left, title_box.bottom() + 2, width, float(DESC_H)
        )
        painter.drawText(
            description_box,
            left_top | int(QtCore.Qt.TextFlag.TextWordWrap),
            self.description_text(),
        )

        note_box = QtCore.QRectF(
            left, description_box.bottom() + 4, width, float(NOTE_H)
        )
        if self._note:
            # Said in the warning colour even while the card is disabled: this line is
            # the only thing on a disabled card a viewer has to be able to read.
            painter.setPen(QtGui.QColor(colours["warn"]))
            painter.drawText(
                note_box, left_top | int(QtCore.Qt.TextFlag.TextWordWrap), self._note
            )
            return
        if self._reason:
            # Every offered card carries this line (§2.2), so presence cannot be what
            # marks the recommendation any more: the advised card says it in the
            # accent, the others quietly. `text_soft` rather than the description's
            # `text_dim`, so the two lines under a title do not read as one paragraph.
            painter.setPen(
                QtGui.QColor(
                    colours["accent_light" if self._recommended else "text_soft"]
                )
            )
            painter.drawText(
                note_box, left_top | int(QtCore.Qt.TextFlag.TextWordWrap), self._reason
            )


# ------------------------------------------------------------ the card grid


def _place_card(index: int, total: int) -> tuple[int, int, int]:
    """Where card `index` of `total` sits: `(row, column, column span)`.

    The grid is laid out in **half-columns** - `2 * COLUMNS` of them, every card two
    wide - for one reason: a short last row has to be centred, and a `QGridLayout`
    cannot centre a widget on the seam between two of its own columns. Half a card's
    width is exactly what the seam is worth, so the short row is pushed right by
    `COLUMNS - rest` half-columns and comes out centred under the full row above it.

    Why it is needed: §1 had six layouts, picture in picture was taken out, and five
    into three columns leaves cell (1, 2) empty - 236 px of blank card space under
    "Focus on one", which reads as a card that failed to draw rather than as a row
    that is short. `COLUMNS` is deliberately left at three (see the constant) and the
    row is centred instead.

    The numbering is read left to right and top to bottom as the contract orders it, and
    `_move` keeps working on the logical `index // COLUMNS` grid rather than on this
    one - the keyboard walks the cards, not the columns they are drawn in.
    """
    row, column = divmod(int(index), COLUMNS)
    rows = (int(total) + COLUMNS - 1) // COLUMNS
    rest = int(total) - (rows - 1) * COLUMNS
    shift = COLUMNS - rest if row == rows - 1 else 0
    return row, column * 2 + shift, 2


# ----------------------------------------------------------- where it opens


def dialog_spot(
    size: QtCore.QSize,
    screens: Sequence[QtCore.QRect],
    preferred: QtCore.QRect | None = None,
) -> QtCore.QRect:
    """`size`, centred inside one real screen and clamped into it.

    §6's rule, and `window.transport_spot`'s pattern for the same reason recorded
    there: a window placed from the bounding box of several screens lands in the empty
    corner between two monitors, where nobody can reach it. The screen chosen is the
    one overlapping `preferred` most - the area the stage is about to open on - so the
    question is answered on the monitor it is about to change.

    The size is clamped as well as the position, because a dialog taller than the
    screen has its buttons below the bottom edge, and those are the only two controls
    that end it.
    """
    if not screens:
        return QtCore.QRect(QtCore.QPoint(0, 0), size)
    host = screens[0]
    if preferred is not None and not preferred.isNull():
        best = -1
        for area in screens:
            overlap = area.intersected(preferred)
            covered = overlap.width() * overlap.height()
            if covered > best:
                best, host = covered, area
    width = min(size.width(), host.width())
    height = min(size.height(), host.height())
    return QtCore.QRect(
        host.x() + (host.width() - width) // 2,
        host.y() + (host.height() - height) // 2,
        width,
        height,
    )


# --------------------------------------------------------------- the dialog


class LayoutChooser(QtWidgets.QDialog):
    """§2.1's dialog: how should these screens be shown?

    Built to be answerable without a person. `cards` is a dict keyed by layout id,
    `select()` is the selection, `choice()` is the answer, and every line of text has a
    reader - so every rule in §2.1 and §2.2 can be checked by a test rather than by
    opening it and looking.

    It touches no player and no engine: playback has not started when this is shown,
    which is the whole of its relationship with §6's one clock.

    `recording` is optional and is read for one thing only: re-generating the screen
    names on a language switch, through the `window.screen_titles` that invented them.
    Pass it when `streams` was built from that recording - `stage.streams_from` is the
    caller that does - and leave it out when the titles came from somewhere else, so
    that a name belonging to the viewer is never overwritten with "Screen 2".
    """

    def __init__(
        self,
        streams: Sequence[layouts.Stream],
        areas: Sequence[QtCore.QRect],
        settings: Any = None,
        recording: Any = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        #: The recording the `streams` were named from, or `None`. Read by
        #: `_rename_streams` and by nothing else: this dialog never opens a file,
        #: never asks the recording how long it is and never touches an engine.
        self._recording = recording
        self._streams = list(streams)
        self._areas = [QtCore.QRect(area) for area in areas]
        self._sizes = [stream.size for stream in self._streams]
        self._saved_layout = str(getattr(settings, "player_layout", "") or "")
        self._saved_signature = str(
            getattr(settings, "player_display_signature", "") or ""
        )
        self._choice: tuple[str, bool] | None = None
        self._selected = ""
        self._recommended = layouts.LAYOUT_SIDE_BY_SIDE
        self._placed = False

        self.setModal(True)
        self.setWindowTitle(_("ScreenRec player"))
        self.cards: dict[str, LayoutCard] = {}
        self._build()
        self._fill_texts()
        self._preselect()
        i18n.notifier.changed.connect(self.retranslate)
        self.place_on(screen_areas())

    # -- building ----------------------------------------------------------

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(
            DIALOG_PAD, DIALOG_PAD, DIALOG_PAD, DIALOG_PAD
        )
        outer.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(2)
        self.heading = QtWidgets.QLabel()
        self.heading.setObjectName("heading")
        self.subtitle = QtWidgets.QLabel()
        self.subtitle.setObjectName("caption")
        titles.addWidget(self.heading)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        self.monitor_chip = MonitorChip(len(self._areas))
        header.addWidget(
            self.monitor_chip, 0, QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        outer.addLayout(header)

        self.notice = QtWidgets.QLabel()
        self.notice.setObjectName("warn")
        self.notice.setWordWrap(True)
        self.notice.hide()
        outer.addWidget(self.notice)

        self.grid_holder = QtWidgets.QWidget()
        self.grid_holder.setObjectName("cardBody")
        grid = QtWidgets.QGridLayout(self.grid_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(CARD_GAP)
        for index, layout in enumerate(layouts.LAYOUTS):
            card = LayoutCard(layout, self._sizes)
            card.chosen.connect(self.select)
            card.activated.connect(self._activate)
            card.arrow.connect(self._move)
            self.cards[layout] = card
            row, column, span = _place_card(index, len(layouts.LAYOUTS))
            grid.addWidget(card, row, column, 1, span)
        grid.setRowStretch(grid.rowCount(), 1)

        self.scroller = _CardScroll()
        self.scroller.setWidgetResizable(True)
        self.scroller.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroller.setWidget(self.grid_holder)
        outer.addWidget(self.scroller, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(12)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(2)
        self.remember_box = QtWidgets.QCheckBox()
        self.hint = QtWidgets.QLabel()
        self.hint.setObjectName("caption")
        left.addWidget(self.remember_box)
        left.addWidget(self.hint)
        footer.addLayout(left, 1)
        self.cancel_button = QtWidgets.QPushButton()
        self.cancel_button.setObjectName("ghost")
        self.cancel_button.clicked.connect(self.reject)
        self.open_button = QtWidgets.QPushButton()
        self.open_button.setObjectName("primary")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self.accept)
        footer.addWidget(
            self.cancel_button, 0, QtCore.Qt.AlignmentFlag.AlignBottom
        )
        footer.addWidget(self.open_button, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(footer)

    # -- the words ---------------------------------------------------------

    def _note_for(self, layout: str) -> str:
        """Why a card is disabled, or "" when it is not. `layouts.possible` decides.

        The wording is this module's; the decision is not. §2.1 wants the reason on the
        card, and the only layout with a hardware condition is `one_per_monitor` - but
        a recording whose streams have not arrived disables every card, and a card that
        is simply dead with no line on it is the thing §2.1 is written against.
        """
        if layouts.possible(layout, self._areas, self._streams):
            return ""
        if layout == layouts.LAYOUT_ONE_PER_MONITOR:
            if len(self._streams) == 2:
                return _("Needs a second monitor")
            return _("Needs one monitor for each screen")
        return _("Not available for this recording")

    def _card_reason(self, layout: str) -> str:
        """What choosing `layout` would give, in one line, or "" when it is disabled.

        §2.2's closing rule is that `separate` and `focus` get "a card and a reason
        line of their own", and `layouts` already builds a sentence for each of its
        five layouts - what it publishes is `recommend`, which returns only the
        winner's. `_reason` is the rest of them, and it is asked for past its
        underscore on purpose: the only other way to put a line under the four cards
        that are not the advice is to write those sentences a second time here, where
        they would drift away from the percentages `layouts` computes the moment
        either side is touched.

        A disabled card is not measured. It shows `_note_for`'s "why not" in this
        line's place, and `one_per_monitor` on a single monitor cannot be measured at
        all: `best_pairing` raises rather than pair two streams onto one screen.
        """
        if not layouts.possible(layout, self._areas, self._streams):
            return ""
        return layouts._reason(layout, self._areas, self._streams)

    def _notice_line(self) -> str:
        """The line above the cards when the saved choice no longer applies, else "".

        §2.3's decision belongs to `layouts.layout_for_signature` and is asked of it
        here rather than re-derived: it is the function that knows a remembered
        `one_per_monitor` on a desk that has lost a monitor must not be applied, and
        that an unplugged or rescaled display changes the stamp. Its own sentence is
        shown, because it names the saved layout and what is recommended instead.

        Nothing is said when there was no saved choice: the dialog is then appearing
        for the ordinary reason, and a notice with no cause reads as a fault.
        """
        if self._saved_layout not in layouts.LAYOUTS:
            return ""
        _chosen, reason, mode = layouts.layout_for_signature(
            self._saved_layout, self._saved_signature, self._areas, self._streams
        )
        return reason if mode == "ask" else ""

    def _fill_texts(self) -> None:
        """Every string in the window, read fresh. Called at build and on a switch."""
        self.setWindowTitle(_("ScreenRec player"))
        self.heading.setText(heading_for(len(self._streams)))
        self.subtitle.setText(_("How should they be shown?"))
        self.monitor_chip.set_count(len(self._areas))
        self.remember_box.setText(_("Remember my choice"))
        self.hint.setText(_("You can change this while watching"))
        self.cancel_button.setText(_("Cancel"))
        self.open_button.setText(_("Open"))

        notice = self._notice_line()
        self.notice.setText(notice)
        self.notice.setVisible(bool(notice))

        self._recommended, reason = layouts.recommend(self._areas, self._streams)
        for layout, card in self.cards.items():
            note = self._note_for(layout)
            card.setEnabled(not note)
            card.set_note(note)
            recommended = layout == self._recommended and not note
            card.set_recommended(recommended)
            if note:
                card.set_reason("")
            elif recommended:
                # The winner's line comes from `recommend` rather than from
                # `_card_reason`, although for a layout that scored they are the same
                # sentence: `recommend` is also the function with something to say
                # when *nothing* scored ("no layout fitted these screens"), and that
                # answer belongs under the card it preselects.
                card.set_reason(reason)
            else:
                card.set_reason(self._card_reason(layout))
            card.retranslate()

    def _rename_streams(self) -> None:
        """Re-generate the screen names, so the reason lines are not half-translated.

        The reason lines name the streams, and those names are not translated where
        they are used - they arrived inside the `Stream`s, generated once in whatever
        language was in force when the caller built them. Without this, a switch
        leaves "Screen 1 целиком, 97 %": the sentence turns over and the names do not.
        `PlayerStage._rename_streams` is the same fix for the same reason.

        `window.screen_titles` is asked rather than a template applied here, because
        it is the one function that knows the difference between a name this program
        invented and a name the recording carries - the second kind is handed back
        untouched, in the language the viewer wrote it in. Nothing is done without a
        `recording`, which is the caller saying these names came from there.

        A count that does not match is left alone rather than zipped short: a dialog
        showing one of the two names in the new language is worse than one showing
        both in the old.
        """
        if self._recording is None:
            return
        titles = screen_titles(self._recording, len(self._streams))
        if len(titles) != len(self._streams):
            _report(
                f"the recording named {len(titles)} screen(s) but the dialog holds "
                f"{len(self._streams)}; the names are left as they were."
            )
            return
        self._streams = [
            layouts.Stream(index=stream.index, title=title, size=stream.size)
            for stream, title in zip(self._streams, titles)
        ]

    def retranslate(self) -> None:
        """Re-read every sentence. See `i18n.notifier`.

        The reason line and the notice are rebuilt rather than re-shown, which is what
        `layouts.recommend`'s docstring asks of a caller: they are sentences assembled
        from numbers, and the assembled string is the one thing a language switch
        cannot translate. The screen names inside those sentences are rebuilt first,
        for the same reason one level down - see `_rename_streams`.
        """
        self._rename_streams()
        self._fill_texts()

    def notice_text(self) -> str:
        """The displays-have-changed line, or "" when there is none."""
        return self.notice.text()

    # -- the choice --------------------------------------------------------

    def _preselect(self) -> None:
        """Start on the layout §2.3 says to start on, falling back to a usable card.

        `layout_for_signature` answers both halves of §2.3 - which layout, and whether
        to ask - and the dialog only exists in the "ask" case, so its layout is the
        recommendation for the desk as it is now. It is still checked against the
        cards: a preselected card that is disabled would leave the dialog opening on a
        choice its own Open button refuses.
        """
        wanted, _reason, _mode = layouts.layout_for_signature(
            self._saved_layout, self._saved_signature, self._areas, self._streams
        )
        if self.select(wanted):
            return
        for layout in layouts.LAYOUTS:
            if self.select(layout):
                _report(
                    f"{wanted!r} cannot be built on this desk; the dialog opens on "
                    f"{layout!r} instead."
                )
                return
        # Every card disabled: fewer than two streams, or no screen reported an area.
        # Open would promise a layout nothing can build, so it is refused rather than
        # left armed.
        self.open_button.setEnabled(False)
        _report("no layout can be built from these streams and screens.")

    def select(self, layout: str) -> bool:
        """Make `layout` the chosen card. False when it is unknown or disabled.

        The refusal is the whole of §2.1's "disabled with the reason written on it":
        a disabled card cannot be reached by the mouse, the arrows step over it, and
        this is the third door - a caller, or a saved choice, asking for it by name.
        """
        card = self.cards.get(layout)
        if card is None or not card.isEnabled():
            return False
        self._selected = layout
        for name, other in self.cards.items():
            other.set_selected(name == layout)
        self.open_button.setEnabled(True)
        return True

    def selected(self) -> str:
        """The chosen layout's id, or "" when nothing can be chosen."""
        return self._selected

    def remember(self) -> bool:
        """Whether the viewer asked for this choice to be remembered."""
        return self.remember_box.isChecked()

    def set_remember(self, value: bool) -> None:
        self.remember_box.setChecked(bool(value))

    def choice(self) -> tuple[str, bool] | None:
        """`(layout id, remember)` once Open was pressed, `None` after a cancel.

        `None` until the dialog is answered, so a caller that reads it without having
        shown the dialog is told "no choice" rather than handed a default it would
        believe a person made.
        """
        return self._choice

    # -- input -------------------------------------------------------------

    def _activate(self, layout: str) -> None:
        """Enter or a double-click on a card: choose it and open."""
        if self.select(layout):
            self.accept()

    def _move(self, dx: int, dy: int) -> None:
        """Step the selection across the card grid, skipping what cannot be chosen.

        It keeps going in the same direction over a disabled card rather than stopping
        on it: `one_per_monitor` sits last in §1's order, so on a one-monitor desk a
        stop would make the bottom-right corner of the grid a dead end the keyboard
        could not leave.
        """
        order = list(layouts.LAYOUTS)
        index = order.index(self._selected) if self._selected in order else 0
        rows = (len(order) + COLUMNS - 1) // COLUMNS
        row, column = divmod(index, COLUMNS)
        while True:
            row += dy
            column += dx
            if not (0 <= row < rows and 0 <= column < COLUMNS):
                return
            target = row * COLUMNS + column
            if target >= len(order):
                return
            name = order[target]
            card = self.cards[name]
            if card.isEnabled() and self.select(name):
                card.setFocus(QtCore.Qt.FocusReason.TabFocusReason)
                self.scroller.ensureWidgetVisible(card)
                return

    def accept(self) -> None:
        """Open: record the answer before the dialog stops being able to give one."""
        if not self._selected:
            _report("Open was pressed with nothing selected; treating it as a cancel.")
            self.reject()
            return
        self._choice = (self._selected, self.remember())
        super().accept()

    def reject(self) -> None:
        """Cancel, Esc, or the close box - all of them mean no choice was made."""
        self._choice = None
        super().reject()

    # -- geometry ----------------------------------------------------------

    def place_on(self, screens: Sequence[QtCore.QRect]) -> None:
        """Size the dialog to its contents and put it on a screen that exists.

        `sizeHint` rather than `adjustSize`: `adjustSize` caps a top-level window at
        two thirds of the screen, which here is a smaller window with the cards
        scrolled rather than a window that fits. The clamp that belongs in this
        decision is `dialog_spot`'s, against the one screen the dialog will open on.
        """
        wanted = self.sizeHint().expandedTo(self.minimumSizeHint())
        preferred = self._areas[0] if self._areas else None
        self.setGeometry(dialog_spot(wanted, screens, preferred))

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        """Place it once more, with the final sizes, before anyone sees it.

        `__init__` already placed it, which is what a caller reading `geometry()`
        without showing the dialog gets; but a style sheet is applied at polish time
        and can change a label's height, so the placement that matters is the one made
        when the window is about to appear. Done once: a viewer who moved the dialog
        and brought it back must not find it jumping to the middle again.
        """
        if not self._placed:
            self._placed = True
            self.place_on(screen_areas())
        super().showEvent(event)


# ------------------------------------------------------------------ the entry


def _headless() -> bool:
    """Is there a platform that can show a modal dialog and somebody to answer it?"""
    if QtGui.QGuiApplication.instance() is None:
        return True
    return QtGui.QGuiApplication.platformName() in HEADLESS_PLATFORMS


def choose(
    streams: Sequence[layouts.Stream],
    areas: Sequence[QtCore.QRect],
    settings: Any = None,
    recording: Any = None,
) -> tuple[str, bool] | None:
    """Ask how to show these screens: `(layout id, remember)`, or `None` on a cancel.

    The four answers it can give without putting a window on screen, each of them a
    rule from the contract rather than a shortcut:

    * **fewer than two streams** - §1: "A recording with one stream never asks
      anything." `None` is returned and nothing is shown. The caller has no choice to
      apply, which is exactly right: there is nothing to arrange.
    * **the saved choice still applies** - §2.3: signature matches, so playback starts
      straight away. `(saved layout, True)` comes back without a dialog; the `True`
      says the choice is still a remembered one, so a caller writing it back stores
      what it already had rather than quietly forgetting it.
    * **no screen reported an area** - the recommendation is returned unremembered.
      Returning `None` here would read as a cancel and leave the viewer with no player
      at all because Qt could not describe their desk; opening on the default layout
      is the direction that still shows them their recording.
    * **nobody can answer** - an offscreen or minimal platform, where `exec()` would
      wait for a click that cannot come and hang the process. Same answer, and a line
      on stderr saying which layout was taken.

    `settings` is read with `getattr`, so `None` and any object carrying the two
    `player_*` fields of §2.3 both work; nothing is written back. Storing the answer is
    the caller's, because the caller is what owns the settings file.

    `recording` is the one `streams` was named from, and is passed on for the single
    use `LayoutChooser` has for it: re-generating those names if the language changes
    while the dialog is up. Leave it out when the titles are not `screen_titles`'.
    """
    wanted = list(streams)
    if len(wanted) < 2:
        _report(
            f"a recording with {len(wanted)} stream(s) has nothing to arrange; "
            "no layout was asked for."
        )
        return None
    if QtWidgets.QApplication.instance() is None:
        _report("there is no QApplication, so no layout could be asked for.")
        return None

    boxes = [QtCore.QRect(area) for area in areas]
    if not boxes:
        boxes = screen_areas()
    if not boxes:
        fallback, _why = layouts.recommend(boxes, wanted)
        _report(
            "no screen reported an available area; opening with "
            f"{fallback!r} without asking."
        )
        return fallback, False

    saved = str(getattr(settings, "player_layout", "") or "")
    stamp = str(getattr(settings, "player_display_signature", "") or "")
    remembered, reason, mode = layouts.layout_for_signature(
        saved, stamp, boxes, wanted
    )
    if mode != "ask":
        _report(f"the saved layout {remembered!r} still applies: {reason}")
        return remembered, True

    dialog = LayoutChooser(wanted, boxes, settings, recording)
    try:
        if _headless():
            taken = dialog.selected() or layouts.LAYOUT_SIDE_BY_SIDE
            _report(
                f"no one can answer on the {QtGui.QGuiApplication.platformName()!r} "
                f"platform; opening with {taken!r} and not remembering it."
            )
            return taken, False
        dialog.exec()
        return dialog.choice()
    finally:
        dialog.deleteLater()
