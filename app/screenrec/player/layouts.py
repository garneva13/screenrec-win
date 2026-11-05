"""Where two recorded screens go on one viewer's desk - as arithmetic, not as widgets.

`docs/PLAYER_LAYOUTS.md` is the contract; this module is the half of it that can be
proved. Nothing here imports a widget, touches a `QMediaPlayer` or needs a window, and
exactly one function (`current_signature`) reads the live display list. That split is the
point: the dialog, the stage, the separate-window path and the "remember my choice"
decision all need the same numbers, and a recommendation that can only be exercised by
plugging in a second monitor cannot be checked at all on a machine that has one. Every
rule below is therefore a pure function over `QRect`s a test can invent.

Three decisions worth stating, because each had an alternative and each avoids a failure
that has a name.

**The geometric mean, not the arithmetic one** (§2.2). Two layouts with the same average
are not equally good: one that shows a stream at 0.8 and the other at 0.2 averages the
same 0.5 as one showing both at 0.5, and the first is unwatchable. The geometric mean
scores them 0.400 and 0.500, which is the ordering a viewer would give them. Those two
numbers are asserted in `tests/test_player_layouts.py` so the formula cannot quietly
become `sum(...) / n` again.

**The display signature is what makes "remember" safe** (§2.3). A remembered
`one_per_monitor` on a desk whose second monitor has been unplugged would open a window
on a screen that is not there - placed where it cannot be reached, dragged back or
closed. So the saved choice is stamped with the displays it was made on, and the stamp is
sorted by position rather than by enumeration order: Windows renumbers screens when one
is woken, re-plugged or made primary, and a signature that changed because of that would
throw the dialog at a user whose desk never moved.

**Which stream goes on which monitor is searched, not assumed** (§5). `areas` is the
screen list in Qt's order and `streams` is the order the sidecar lists them in; nothing
lines those two up, and §5 makes the mapping one "the user can change". Pairing stream
*i* with area *i* and calling the result the score of `one_per_monitor` is therefore a
guess, and on this desk a wrong guess is large: with the sidecar naming the portrait
screen first, the identity mapping scores 0.5625 and puts both windows on the other
one's monitor, while the swap scores 1.000 with both at 1:1. With streams of 1440x810
and 2560x1440 it is worse than a bad number - the identity mapping scores 0.750, `focus`
wins at 0.900, and the dialog recommends hiding half the recording although the other
pairing shows all of it at full size. So `best_pairing` tries every arrangement and
`score` is what the layout can actually do.

**The target area is `availableGeometry`, so the taskbar is already gone.** Scoring
against full `geometry()` overstates every scale by the height of the taskbar, which on
the user's own 2560x1440 is about 48 px - enough to turn "fits exactly" into "fits
exactly" on paper and a letterbox in practice.

**A layout that hides half the recording is never the answer** (§2.2). `focus` and
`separate` both score 0.0: they are offered on every card the dialog draws and neither
is ever recommended. `separate` because arranging two floating windows is a choice
somebody makes, not a default. `focus` because a recommendation answers "how do I watch
what I just recorded", and showing one stream and hiding the other is not an answer to
that question whatever arithmetic says. It did say otherwise: `scale(main) * 0.9` put
`focus` at 0.900, ahead of every layout that shows both streams on a single 2560x1440,
so the player's advice on the most ordinary desk there is was to watch half of the
recording. A penalty small enough to leave the hidden stream in front is not a penalty,
and no number between 0.9 and 0 would have been anything but a guess at where the two
curves cross.

Measured on the user's real desk (2560x1440 at 0,0 with a rotated 1440x2560 at 2560,0,
full geometry, streams at their native sizes), these are the scores this module gives:

    one_per_monitor  1.000      each stream on its own monitor, 1:1
    side_by_side     0.530      sqrt(0.500 * 0.563)
    stacked          0.375      sqrt(0.500 * 0.281)
    focus            0.000      offered, never recommended
    separate         0.000      offered, never recommended

With only the landscape monitor available, `one_per_monitor` drops out and
`side_by_side` wins at 0.530 - half the width each, which is the best a single monitor
can do with a 2560-wide recording and a portrait one once both have to be visible.
Picture in picture used to win that case at 0.870 by shrinking the second stream into a
717-pixel thumbnail; the viewer tried it and found the splitter of `side_by_side` did
everything it did, so the layout was removed rather than left on a card nobody picked.
The formula was **not** re-tuned for its absence - the remaining scores are the ones it
always gave.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Sequence

from PySide6 import QtCore, QtGui

from ..i18n import _

# ------------------------------------------------------------------ the names

LAYOUT_SIDE_BY_SIDE = "side_by_side"
LAYOUT_STACKED = "stacked"
LAYOUT_FOCUS = "focus"
LAYOUT_SEPARATE = "separate"
LAYOUT_ONE_PER_MONITOR = "one_per_monitor"

#: Every layout, in the order the dialog draws its cards - §1's table, top to bottom.
LAYOUTS: tuple[str, ...] = (
    LAYOUT_SIDE_BY_SIDE,
    LAYOUT_STACKED,
    LAYOUT_FOCUS,
    LAYOUT_SEPARATE,
    LAYOUT_ONE_PER_MONITOR,
)

#: Each card's heading, as the **English source text** - which is also the catalogue key
#: (see `i18n`). Read it through `title()`, never by indexing this dict: the value here
#: is the key, not the words a viewer is to be shown, and a card filled from the dict
#: stays English while the rest of the window turns Russian.
TITLES: dict[str, str] = {
    LAYOUT_SIDE_BY_SIDE: "Side by side",
    LAYOUT_STACKED: "Stacked",
    LAYOUT_FOCUS: "Focus on one",
    LAYOUT_SEPARATE: "Separate windows",
    LAYOUT_ONE_PER_MONITOR: "One screen per monitor",
}

#: One line under each card's title. Says what it looks like, not why it is good.
#: English source text, as `TITLES` is; read it through `description()`.
DESCRIPTIONS: dict[str, str] = {
    LAYOUT_SIDE_BY_SIDE: "Both screens in one window, left and right.",
    LAYOUT_STACKED: "Both screens in one window, one above the other.",
    LAYOUT_FOCUS: "One screen fills the window; a switch picks which.",
    LAYOUT_SEPARATE: "Each screen in its own window, which you can move.",
    LAYOUT_ONE_PER_MONITOR: "Each screen on its own monitor, filling it.",
}

#: One host window, one transport, one timeline (§3).
STAGE_LAYOUTS: frozenset[str] = frozenset(
    {LAYOUT_SIDE_BY_SIDE, LAYOUT_STACKED, LAYOUT_FOCUS}
)
#: Several top-level windows plus the floating transport - today's `PlayerWindows` (§5).
WINDOW_LAYOUTS: frozenset[str] = frozenset({LAYOUT_SEPARATE, LAYOUT_ONE_PER_MONITOR})

TILING_EQUAL_LR = "equal_lr"
TILING_BIG_LEFT = "big_left"
TILING_BIG_RIGHT = "big_right"
TILING_EQUAL_TB = "equal_tb"

#: The four arrangements of §5, in the order the menu draws them.
TILINGS: tuple[str, ...] = (
    TILING_EQUAL_LR,
    TILING_BIG_LEFT,
    TILING_BIG_RIGHT,
    TILING_EQUAL_TB,
)

#: English source text, as `TITLES` is; read it through `tiling_title()`.
TILING_TITLES: dict[str, str] = {
    TILING_EQUAL_LR: "Equal halves, left and right",
    TILING_BIG_LEFT: "Bigger on the left",
    TILING_BIG_RIGHT: "Bigger on the right",
    TILING_EQUAL_TB: "Equal halves, top and bottom",
}


def title(layout: str) -> str:
    """A layout's heading in the language in force *now*.

    Called when the card is drawn and again from the window's `retranslate`, never
    stored: a title read once at import is the one string a language switch cannot
    reach, and the dialog is the first thing a viewer sees.
    """
    if layout not in TITLES:
        raise ValueError(f"{layout!r} is not one of {', '.join(LAYOUTS)}")
    return _(TITLES[layout])


def description(layout: str) -> str:
    """The line under a layout's heading, in the language in force now."""
    if layout not in DESCRIPTIONS:
        raise ValueError(f"{layout!r} is not one of {', '.join(LAYOUTS)}")
    return _(DESCRIPTIONS[layout])


def tiling_title(tiling: str) -> str:
    """A separate-windows arrangement's menu entry, in the language in force now."""
    if tiling not in TILING_TITLES:
        raise ValueError(f"{tiling!r} is not one of {', '.join(TILINGS)}")
    return _(TILING_TITLES[tiling])

# --------------------------------------------------------------- the numbers

#: The splitter's minimum, per side, in `side_by_side` and `stacked` (§3).
MIN_PANE = 160
#: A press inside a pane that travelled more than this is a drag and not a click, so
#: it must not be read as "make this the main stream" (§4 calls that "the single most
#: annoying bug this feature can have"). It is deliberately one pixel looser than
#: `window.DRAG_SLOP`, which is 2: moving a window by accident costs a drag back,
#: swapping which stream is being read costs attention.
DRAG_SLOP = 3

#: Above this many stream -> monitor arrangements `best_pairing` stops enumerating them
#: and assigns greedily. 7! = 5040 geometric means take well under a millisecond; 10! is
#: 3.6 million and would freeze the dialog. A recording has one stream per recorded
#: screen, so no real desk comes near this: the bound is here so that a sidecar claiming
#: twelve streams cannot hang the player before it has shown anything.
_MAX_PAIRINGS = 5040
#: Scales this close together read as "the same size" in the reason line.
_SAME_SIZE = 0.02

#: Offered on a card like every other layout, never the recommendation (§2.2).
#:
#: `separate` because two free-floating windows is an arrangement somebody makes on
#: purpose, not a default. `focus` because it shows one stream and hides the other, and
#: the recommendation is the answer to "how do I watch what I just recorded" - hiding
#: half of it is not an answer to that question at any score. See `score`.
NEVER_RECOMMENDED: frozenset[str] = frozenset({LAYOUT_SEPARATE, LAYOUT_FOCUS})


@dataclass(frozen=True)
class Stream:
    """One recorded screen: which it is, what it is called, how big it really is.

    Frozen because a layout decision is a pure function of these three values, and a
    stream that could be edited between the score and the placement would let the
    dialog recommend one thing and the stage build another.
    """

    index: int
    title: str
    size: tuple[int, int]


# ------------------------------------------------------- the display signature


def display_signature(areas: Sequence[QtCore.QRect], ratios: Sequence[float]) -> str:
    """The §2.3 stamp: every screen's area and scaling, in one comparable string.

    For the user's own desk this is::

        2560x1440+0+0@1.0|1440x2560+2560+0@1.0

    Sorted by position, not by the order the screens arrived in. Windows renumbers
    screens when one is woken from sleep, re-plugged or made primary, and a signature
    that changed because of *that* would show the layout dialog to someone whose desk
    has not moved since yesterday - which is exactly the nag "Запомнить мой выбор" was
    checked to avoid. Moving a screen, unplugging one or rescaling one all still change
    it, because each of those changes a field rather than the order.

    `ratios` must be the same length as `areas`; a mismatch is a caller's bug that
    would otherwise produce a stamp silently missing a screen's scaling, so it is
    raised rather than padded.
    """
    if len(ratios) != len(areas):
        raise ValueError(
            f"display_signature got {len(areas)} areas but {len(ratios)} "
            "device pixel ratios; they must describe the same screens."
        )
    ordered = sorted(
        zip(areas, ratios),
        key=lambda pair: (
            pair[0].x(),
            pair[0].y(),
            pair[0].width(),
            pair[0].height(),
            float(pair[1]),
        ),
    )
    return "|".join(
        f"{area.width()}x{area.height()}+{area.x()}+{area.y()}@{_ratio(ratio)}"
        for area, ratio in ordered
    )


def _ratio(value: float) -> str:
    """`1.0`, `1.25`, `1.5` - never `1` and never `1.000000000002`.

    Rounded before formatting because Qt reports a 125% display as 1.25 on one machine
    and as 1.2500000000000002 on another, and two spellings of the same desk would be
    two different signatures.
    """
    text = f"{round(float(value), 3):g}"
    return text if "." in text else f"{text}.0"


def current_signature() -> str:
    """`display_signature` for the screens this process can see right now.

    The only function in this module that reads the live display list, so everything
    else stays testable with invented rectangles. An empty string means there is no
    `QGuiApplication` yet or it reports no screens: it matches no stored signature, so
    the dialog is shown, which is the safe direction to fail in.
    """
    if QtGui.QGuiApplication.instance() is None:
        return ""
    screens = QtGui.QGuiApplication.screens()
    if not screens:
        return ""
    return display_signature(
        [screen.availableGeometry() for screen in screens],
        [screen.devicePixelRatio() for screen in screens],
    )


# ------------------------------------------------------------------- fitting


def fit(box: QtCore.QRect, size: tuple[int, int]) -> QtCore.QRect:
    """The largest rectangle of `size`'s shape that fits in `box`, centred in it.

    Letterboxing, which §3 makes the only behaviour this round ships: aspect ratio
    kept, nothing cropped, nothing stretched, and **never scaled past 1:1** - a
    720p recording opened on a 4K monitor is shown at 720p rather than blown up into
    mush.

    A zero or negative size, and a box with no area, give an empty rectangle at the
    box's top-left instead of dividing by zero. A caller that paints it draws nothing,
    which is what it should do when it has been handed a stream with no dimensions.
    """
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0 or box.width() <= 0 or box.height() <= 0:
        return QtCore.QRect(box.x(), box.y(), 0, 0)
    factor = min(box.width() / width, box.height() / height, 1.0)
    # Clamped as well as rounded: at a factor of exactly box.w/width the rounding can
    # land a pixel outside the box, and a pane one pixel wider than its box is a
    # scrollbar or a clipped edge depending on who draws it.
    drawn_w = min(box.width(), max(1, int(round(width * factor))))
    drawn_h = min(box.height(), max(1, int(round(height * factor))))
    return QtCore.QRect(
        box.x() + (box.width() - drawn_w) // 2,
        box.y() + (box.height() - drawn_h) // 2,
        drawn_w,
        drawn_h,
    )


def scale_in(box: QtCore.QRect, size: tuple[int, int]) -> float:
    """How much of natural size `size` is shown in `box`: 1.0 is 1:1, 0.5 is half.

    Capped at 1.0 to match `fit`, and 0.0 for a degenerate box or size, so a caller
    can treat 0.0 as "this cannot be shown at all".
    """
    return _scale(box.width(), box.height(), size)


def _scale(width: float, height: float, size: tuple[int, int]) -> float:
    """`scale_in` on a box that does not have to be a whole number of pixels.

    `score` halves an area to build its boxes, and rounding 1279.5 to 1280 before
    dividing would make an odd-width monitor score differently from an even one for no
    reason a viewer could see.
    """
    native_w, native_h = int(size[0]), int(size[1])
    if native_w <= 0 or native_h <= 0 or width <= 0 or height <= 0:
        return 0.0
    return min(width / native_w, height / native_h, 1.0)


# ------------------------------------------------------------------- scoring


def possible(
    layout: str, areas: Sequence[QtCore.QRect], streams: Sequence[Stream]
) -> bool:
    """Can this layout be built on these monitors with these streams?

    Only `one_per_monitor` has a hardware condition (§1), and it is the reason the
    dialog can draw a disabled card with the reason written on it. Everything else
    needs one usable area and the two streams the whole feature is about: a one-stream
    recording never reaches this module, it opens exactly as it always did (§1).
    """
    if layout not in LAYOUTS:
        raise ValueError(f"{layout!r} is not one of {', '.join(LAYOUTS)}")
    if not areas or len(streams) < 2:
        return False
    if layout == LAYOUT_ONE_PER_MONITOR:
        return len(areas) >= len(streams)
    return True


def score(
    layout: str,
    areas: Sequence[QtCore.QRect],
    streams: Sequence[Stream],
    pairing: Sequence[int] | None = None,
) -> float:
    """§2.2's formulas, exactly. Higher is better; 0.0 means "do not recommend this".

    The first area is the stage's monitor - the one the player opens on. For
    `one_per_monitor`, `pairing` says which monitor each stream goes on; left out, the
    best arrangement is used (`best_pairing`), so the number is what the layout can
    actually do rather than what the sidecar's order happens to give. Pass the user's
    own arrangement once they have changed it, or the card will promise a size their
    windows will not have.

    `separate` and `focus` score 0.0 on purpose - `NEVER_RECOMMENDED`. Both are always
    offered, because two free-floating windows and one big picture with a switch are
    both real ways to watch this; neither is ever the advice, and the only way to say
    that in a number is to make it lose. The reason line is still built for them from
    the sizes they really give, so a card the viewer picks says what they will get.
    """
    if not possible(layout, areas, streams):
        return 0.0
    if layout in NEVER_RECOMMENDED:
        # `focus` used to score `scale(main) * 0.9`, which on a single 2560x1440 came
        # to 0.900 and beat every layout showing both streams: the player's advice on
        # the commonest desk there is was to watch half of what had just been recorded,
        # which is exactly why `separate` is kept out of the running. A penalty small
        # enough to leave the hidden stream in front is not a penalty. `_layout_scales`
        # still measures `focus`, so `_reason` can say what it does show.
        return 0.0
    scales = _layout_scales(layout, areas, streams, pairing)
    if not scales:
        return 0.0
    return _geometric_mean(scales)


def _layout_scales(
    layout: str,
    areas: Sequence[QtCore.QRect],
    streams: Sequence[Stream],
    pairing: Sequence[int] | None = None,
) -> list[float]:
    """The scale each *visible* stream is shown at under `layout`.

    `focus` returns one number because the second stream is not on screen, and the
    reason line says so in words rather than quoting a size nobody can see.

    `pairing` is read only by `one_per_monitor`; every other layout uses one monitor.
    """
    area = areas[0]
    count = len(streams)
    if layout == LAYOUT_SIDE_BY_SIDE:
        return [
            _scale(area.width() / count, area.height(), stream.size)
            for stream in streams
        ]
    if layout == LAYOUT_STACKED:
        return [
            _scale(area.width(), area.height() / count, stream.size)
            for stream in streams
        ]
    if layout == LAYOUT_FOCUS:
        return [_scale(area.width(), area.height(), streams[0].size)]
    if layout == LAYOUT_ONE_PER_MONITOR:
        order = _resolved_pairing(areas, streams, pairing)
        return _pairing_scales(areas, streams, order)
    return []  # separate: there is no single arrangement to measure


def _geometric_mean(values: Sequence[float]) -> float:
    """The n-th root of the product. Zero anywhere means zero, which is correct here.

    §2.2 asks for this rather than the average so that a layout showing one screen at
    full size and the other at a tenth cannot tie with one showing both at half.
    """
    if not values:
        return 0.0
    product = 1.0
    for value in values:
        if value <= 0.0:
            return 0.0
        product *= value
    return product ** (1.0 / len(values))


# ------------------------------------------ which stream on which monitor (§5)


def best_pairing(
    areas: Sequence[QtCore.QRect], streams: Sequence[Stream]
) -> tuple[int, ...]:
    """Which monitor each stream goes on: entry *i* is the area index for stream *i*.

    §5 makes the stream -> monitor mapping one "the user can change", so it cannot be
    the identity `i -> areas[i]`: the screen list arrives in Qt's order and the streams
    in the sidecar's, and nothing lines those two up. On the user's own desk - a
    2560x1440 beside a rotated 1440x2560 - a sidecar that names the portrait screen
    first makes the identity mapping score 0.5625 with each window on the other's
    monitor, where swapping them gives 1.000 and 1:1 on both.

    Every arrangement is measured and the best geometric mean wins. A greedy "the
    biggest stream takes the biggest monitor" is wrong as soon as shape matters more
    than area, which is precisely what a portrait monitor standing beside a landscape
    one is. Ties keep the arrangement found first, and `itertools.permutations` yields
    the identity first, so a sidecar that was already aligned is left in its own order
    rather than shuffled into an equally good one.

    This is the arrangement the dialog is scored against; a caller that lets the user
    change it passes the new one to `score` and `recommend` and places its windows from
    it, so the number under the card and the windows on the desk stay the same thing.

    Fewer monitors than streams raises `ValueError`: there is then no such thing as one
    monitor each, and a caller placing windows from a guess would stack two of them on
    one screen. `possible(LAYOUT_ONE_PER_MONITOR, ...)` is the question to ask first.
    """
    if len(areas) < len(streams):
        raise ValueError(
            f"one screen per monitor needs {len(streams)} monitors, got "
            f"{len(areas)}; ask possible() before pairing."
        )
    if not streams:
        return ()
    if math.perm(len(areas), len(streams)) > _MAX_PAIRINGS:
        return _greedy_pairing(areas, streams)
    best: tuple[int, ...] = tuple(range(len(streams)))
    best_score = -1.0
    for order in itertools.permutations(range(len(areas)), len(streams)):
        value = _geometric_mean(_pairing_scales(areas, streams, order))
        if value > best_score:
            best, best_score = order, value
    return best


def _pairing_scales(
    areas: Sequence[QtCore.QRect],
    streams: Sequence[Stream],
    order: Sequence[int],
) -> list[float]:
    """The scale each stream is shown at when it is placed on `order`'s monitor."""
    return [
        _scale(areas[where].width(), areas[where].height(), stream.size)
        for where, stream in zip(order, streams)
    ]


def _greedy_pairing(
    areas: Sequence[QtCore.QRect], streams: Sequence[Stream]
) -> tuple[int, ...]:
    """The fallback above `_MAX_PAIRINGS`: each stream, largest first, takes the free
    monitor that shows it best.

    Not guaranteed to be the best arrangement - the exhaustive search above is what is
    guaranteed - but it is deterministic, cheap, and it can never put two streams on
    one monitor. Only a stream count no hardware produces reaches it.
    """
    taken: set[int] = set()
    chosen = [0] * len(streams)
    for position in sorted(
        range(len(streams)),
        key=lambda i: (-streams[i].size[0] * streams[i].size[1], i),
    ):
        stream = streams[position]
        pick, pick_scale = -1, -1.0
        for index, area in enumerate(areas):
            if index in taken:
                continue
            value = _scale(area.width(), area.height(), stream.size)
            if value > pick_scale:
                pick, pick_scale = index, value
        if pick < 0:
            raise ValueError(
                f"ran out of monitors pairing stream {position} of "
                f"{len(streams)} with {len(areas)} monitors."
            )
        taken.add(pick)
        chosen[position] = pick
    return tuple(chosen)


def _resolved_pairing(
    areas: Sequence[QtCore.QRect],
    streams: Sequence[Stream],
    pairing: Sequence[int] | None,
) -> tuple[int, ...]:
    """`pairing` checked against this desk, or `best_pairing` when none was given.

    Checked rather than trusted because a pairing is the one input here that arrives
    from the settings file and from the user's own menu. Each mistake it can carry
    places a window somewhere nobody can watch it - off the desk, or underneath the
    other one - so each is named instead of being clipped into something plausible.
    """
    if pairing is None:
        return best_pairing(areas, streams)
    order = tuple(int(index) for index in pairing)
    if len(order) != len(streams):
        raise ValueError(
            f"a stream -> monitor pairing needs one entry per stream: got "
            f"{len(order)} entries for {len(streams)} streams."
        )
    outside = [index for index in order if not 0 <= index < len(areas)]
    if outside:
        raise ValueError(
            f"pairing {order} names monitor(s) {outside}, but there are only "
            f"{len(areas)}; that window would be placed where no screen is."
        )
    if len(set(order)) != len(order):
        raise ValueError(
            f"pairing {order} puts two screens on one monitor, which is not one "
            f"screen per monitor; {LAYOUT_SEPARATE!r} is the layout for that."
        )
    return order


# --------------------------------------------------- the recommendation, in words


def _percent(value: float) -> str:
    """`97%` in English, `97 %` in Russian - the space before the sign is a real rule."""
    return _("{percent}%").format(percent=round(value * 100))


def _reason(
    layout: str,
    areas: Sequence[QtCore.QRect],
    streams: Sequence[Stream],
    pairing: Sequence[int] | None = None,
) -> str:
    """One line, in the language in force now, built from the score's own numbers.

    Built from `_layout_scales` rather than from the score, because a score is a
    ranking and "0.53" tells a viewer nothing; "по 53 % от натуральной величины" tells
    them whether they will be able to read the text in the recording.

    Every piece goes through `_()` at the moment it is asked for, so the line is in
    whichever language is in force when the card is drawn. **Nothing may cache what
    this returns** - a cached line is the one string in the dialog a language switch
    cannot reach. `recommend` is called while the dialog is being built, which is why
    building the line there is both correct and live-switch-safe.

    `pairing` must be the one the score was computed with, or the line describes an
    arrangement other than the one the windows will be placed in.
    """
    if layout == LAYOUT_SEPARATE:
        return _("each screen in its own window — you choose the size")
    scales = _layout_scales(layout, areas, streams, pairing)
    if not scales:
        return _("there is nothing here to measure")
    if layout == LAYOUT_FOCUS:
        return _("only {name} is visible, {percent} of natural size").format(
            name=streams[0].title, percent=_percent(scales[0])
        )
    if layout == LAYOUT_ONE_PER_MONITOR:
        head = _("each screen on its own monitor")
    elif len(streams) == 2:
        head = _("both screens in full")
    else:
        head = _("all the screens in full")
    low, high = min(scales), max(scales)
    if high - low <= _SAME_SIZE:
        # "Almost" is for 95..99 %. At exactly 1.0 - which `_scale` caps at, so it
        # really is pixel for pixel - saying "почти" describes the one case the
        # arithmetic is certain about as an approximation, and talks a viewer out of
        # the layout that is as good as it gets.
        if low >= 1.0:
            return _("{what}, at natural size").format(what=head)
        if low >= 0.95:
            return _("{what}, almost at natural size").format(what=head)
        return _("{what}, {percent} of natural size each").format(
            what=head, percent=_percent(low)
        )
    sizes = ", ".join(
        _("{name} — {percent}").format(name=stream.title, percent=_percent(value))
        for stream, value in zip(streams, scales)
    )
    return _("{what}: {sizes} of natural size").format(what=head, sizes=sizes)


def recommend(
    areas: Sequence[QtCore.QRect],
    streams: Sequence[Stream],
    pairing: Sequence[int] | None = None,
) -> tuple[str, str]:
    """The best-scoring layout and the one line the dialog prints under its card.

    Ties go to `side_by_side` (§2.2), which falls out of walking `LAYOUTS` in the
    dialog's own order and only replacing the leader on a strictly better score.

    `pairing` is the stream -> monitor arrangement `one_per_monitor` is judged on; left
    out, the best one is used, and the caller gets it from `best_pairing` to place its
    windows with. Both the score and the line come from the same arrangement, so a
    recommendation cannot describe a desk the player will not build.

    With nothing to measure - no monitor reported an area, or the recording has no
    streams - `side_by_side` is returned with a reason that says the sizes are not
    known, rather than a confident percentage computed from zeroes.

    The reason comes back in the language in force at the moment of the call. The
    dialog calls this while it is building itself and again from its `retranslate`, so
    the line follows a language switch; **a caller that stores it does not**.
    """
    if not areas or not streams:
        return (
            LAYOUT_SIDE_BY_SIDE,
            _("the screen sizes are not known yet — the default layout"),
        )
    best = LAYOUT_SIDE_BY_SIDE
    best_score = -1.0
    for layout in LAYOUTS:
        value = score(layout, areas, streams, pairing)
        if value > best_score:
            best, best_score = layout, value
    if best_score <= 0.0:
        return (
            LAYOUT_SIDE_BY_SIDE,
            _("no layout fitted these screens — the default layout"),
        )
    return best, _reason(best, areas, streams, pairing)


# -------------------------------------------------------- separate windows (§5)


def tile_rects(area: QtCore.QRect, tiling: str) -> list[QtCore.QRect]:
    """Two rectangles filling `area` exactly, for the schemes of §5.

    Integer arithmetic with the second rectangle taking whatever the first left, so an
    odd width cannot leave a one-pixel strip of desktop showing between two borderless
    windows - which on a dark wallpaper reads as a rendering fault, and on a light one
    as a bright line down the middle of the recording.

    The caller has already chosen the monitor; `area` is that monitor's
    `availableGeometry`, so the taskbar is excluded and no other application's windows
    are touched.
    """
    if tiling not in TILINGS:
        raise ValueError(f"{tiling!r} is not one of {', '.join(TILINGS)}")
    x, y = area.x(), area.y()
    width, height = area.width(), area.height()
    if tiling == TILING_EQUAL_TB:
        top = height // 2
        return [
            QtCore.QRect(x, y, width, top),
            QtCore.QRect(x, y + top, width, height - top),
        ]
    if tiling == TILING_BIG_LEFT:
        left = width * 2 // 3
    elif tiling == TILING_BIG_RIGHT:
        left = width // 3
    else:
        left = width // 2
    return [
        QtCore.QRect(x, y, left, height),
        QtCore.QRect(x + left, y, width - left, height),
    ]


# ------------------------------------------------- keeping a window on screen


def clamp_into(rect: QtCore.QRect, area: QtCore.QRect) -> QtCore.QRect:
    """`rect`, moved - and if necessary shrunk - until all of it is inside `area`.

    Shrinking matters: a window wider than the screen it was moved to would always
    have a part of it off the edge, and the part off the edge is where the close box
    and the resize corner are. A control that cannot be reached is worse than a window
    that is smaller than it was asked to be.
    """
    width = min(rect.width(), area.width())
    height = min(rect.height(), area.height())
    x = min(max(rect.x(), area.x()), area.x() + area.width() - width)
    y = min(max(rect.y(), area.y()), area.y() + area.height() - height)
    return QtCore.QRect(x, y, width, height)


# ------------------------------------------------------- remembering (§2.3)


def layout_for_signature(
    saved_layout: str,
    saved_signature: str,
    areas: Sequence[QtCore.QRect],
    streams: Sequence[Stream],
) -> tuple[str, str, str]:
    """What to do with a remembered choice: `(layout, reason, "" | "ask")`.

    The third value is `""` when the saved choice applies silently and playback starts
    straight away, and `"ask"` when the dialog has to be shown. §2.3's three cases,
    plus the one that is not a case at all:

    * **nothing to apply** - `saved_layout` is `""` (the "ask every time" default) or
      is not a layout this version knows. Ask, preselecting the recommendation.
    * **the saved layout is no longer possible** - `one_per_monitor` remembered on a
      desk that now has one monitor. Ask, preselecting the recommendation for the desk
      as it is now; the dialog disables the impossible card and says why. Checked
      before the signature, because this is the case that would otherwise open a
      window on a monitor that is not there.
    * **the displays have changed** - the signature differs. Ask, and the reason says
      the displays changed, because a dialog appearing for no visible cause reads as a
      bug rather than as the safety check it is.
    * **the signature matches** - apply it, no dialog. The reason is still returned so
      the caller has something to put on a tooltip or a notice; nobody has to show it.

    An empty `saved_signature` never matches, which is how a choice stored by a build
    that did not record one is treated as "ask" rather than as "always right".
    """
    if saved_layout not in LAYOUTS:
        best, why = recommend(areas, streams)
        return best, why, "ask"
    if not possible(saved_layout, areas, streams):
        best, why = recommend(areas, streams)
        # Straight quotes in the English, guillemets in the Russian: one of the small
        # differences that makes a translated line read as written rather than as
        # converted. Same for the two lines below.
        reason = _(
            "\"{saved}\" does not fit now — \"{suggested}\" is recommended: {why}"
        ).format(saved=title(saved_layout), suggested=title(best), why=why)
        return best, reason, "ask"
    # The live signature is asked for through `current_signature`, not read out of
    # `areas`: `areas` carries no device pixel ratios, so a signature rebuilt from it
    # would miss a display that was only rescaled - which is one of the three things
    # §2.3 says must invalidate a remembered choice.
    if not saved_signature or saved_signature != current_signature():
        best, why = recommend(areas, streams)
        reason = _(
            "The displays have changed since last time — "
            "\"{suggested}\" is recommended: {why}"
        ).format(suggested=title(best), why=why)
        return best, reason, "ask"
    reason = _("Your saved choice, \"{saved}\": {why}").format(
        saved=title(saved_layout),
        why=_reason(saved_layout, areas, streams),
    )
    return saved_layout, reason, ""
