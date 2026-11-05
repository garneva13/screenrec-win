"""What has been drawn: objects, not pixels.

Strokes and shapes are kept as objects with their points and their settings. That is what
makes undo, redo, erasing one stroke, hiding everything and redrawing at another scale
possible at all - and it is why a mouse move costs a few floats instead of a screenshot
of a 2560x1440 desktop.

Everything that changes the picture goes through `Document`, so three things can never
drift apart: what is on screen, what undo will take back, and which rectangle needs
repainting.

**Coordinates are physical desktop pixels throughout**, the same space
`winapi.list_monitors()` reports. Qt hands out logical units on a scaled display; the
conversion happens once, at the canvas boundary, and never again. A monitor to the left
of the primary one has negative coordinates, so nothing here may assume an origin at
(0, 0).

No Qt. This module is pure Python so it can be tested without a display.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field, replace

TOOL_PEN = "pen"
TOOL_MARKER = "marker"
TOOL_LINE = "line"
TOOL_ARROW = "arrow"
TOOL_RECT = "rect"
TOOL_ELLIPSE = "ellipse"
TOOL_TEXT = "text"
TOOL_ERASER = "eraser"

#: The tools that draw something, in the order the toolbar shows them.
DRAWING_TOOLS: tuple[str, ...] = (
    TOOL_PEN, TOOL_MARKER, TOOL_LINE, TOOL_ARROW, TOOL_RECT, TOOL_ELLIPSE, TOOL_TEXT,
)
ALL_TOOLS: tuple[str, ...] = DRAWING_TOOLS + (TOOL_ERASER,)

#: Tools that are dragged out between two corners rather than followed freehand.
DRAGGED_TOOLS: frozenset[str] = frozenset(
    {TOOL_LINE, TOOL_ARROW, TOOL_RECT, TOOL_ELLIPSE}
)

#: The eight pen colours, chosen to stay visible on both a dark and a light screen.
PALETTE: tuple[str, ...] = (
    "#FFFFFF", "#111111", "#F2564B", "#F59E0B",
    "#FACC15", "#22C55E", "#38BDF8", "#A78BFA",
)

WIDTHS: tuple[int, ...] = (2, 4, 6, 10, 16)
FONT_SIZES: tuple[int, ...] = (14, 18, 24, 32, 48)

_ids = itertools.count(1)


@dataclass
class ToolSettings:
    """What one tool remembers. Each tool keeps its own, which is the point."""

    colour: str = "#F2564B"
    width: int = 4
    opacity: float = 1.0
    font_size: int = 24

    def copy(self) -> ToolSettings:
        return replace(self)


def default_settings(tool: str) -> ToolSettings:
    if tool == TOOL_MARKER:
        # Wide and see-through, so what is underneath stays readable.
        return ToolSettings(colour="#FACC15", width=16, opacity=0.35)
    if tool == TOOL_TEXT:
        return ToolSettings(colour="#FFFFFF", width=2, font_size=24)
    if tool in (TOOL_RECT, TOOL_ELLIPSE, TOOL_LINE, TOOL_ARROW):
        return ToolSettings(colour="#F2564B", width=4)
    return ToolSettings()


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass
class Item:
    """One drawn thing."""

    kind: str
    points: list[Point] = field(default_factory=list)
    text: str = ""
    colour: str = "#F2564B"
    width: int = 4
    opacity: float = 1.0
    font_size: int = 24
    item_id: int = field(default_factory=lambda: next(_ids))

    # -- geometry ----------------------------------------------------------

    def bounds(self, pad: float = 0.0) -> tuple[float, float, float, float]:
        """(x, y, w, h) round everything this item touches, padded.

        The pad exists because a stroke is drawn with a round cap of its own width, and
        text hangs below its anchor; a dirty rectangle that is merely the point cloud
        leaves a trail of un-repainted ink behind the pointer.
        """
        if not self.points:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        margin = pad + self.width
        left, top = min(xs) - margin, min(ys) - margin
        right, bottom = max(xs) + margin, max(ys) + margin
        if self.kind == TOOL_TEXT:
            # The anchor is the top-left of the first line; allow for the text itself.
            right = max(right, left + self._text_width())
            bottom = max(bottom, top + self._text_height())
        if self.kind == TOOL_ARROW:
            right += self.width * 4
            bottom += self.width * 4
            left -= self.width * 4
            top -= self.width * 4
        return (left, top, right - left, bottom - top)

    def _lines(self) -> list[str]:
        return self.text.split("\n") if self.text else [""]

    def _text_width(self) -> float:
        # A rough advance per character. The canvas measures properly with QFontMetrics;
        # this only has to be generous enough that the dirty rectangle covers the text.
        longest = max((len(line) for line in self._lines()), default=0)
        return longest * self.font_size * 0.72 + self.font_size

    def _text_height(self) -> float:
        return len(self._lines()) * self.font_size * 1.35 + self.font_size * 0.5

    def hit(self, x: float, y: float, slack: float = 6.0) -> bool:
        """Is (x, y) close enough to count as touching this item?

        Used by the eraser, so it is deliberately generous: a stroke is a thin thing to
        aim at, and an eraser that misses is more annoying than one that is eager.
        """
        if not self.points:
            return False
        reach = slack + self.width / 2

        if self.kind == TOOL_TEXT:
            left, top, width, height = self.bounds()
            return left - slack <= x <= left + width + slack and (
                top - slack <= y <= top + height + slack
            )

        if self.kind in (TOOL_RECT, TOOL_ELLIPSE) and len(self.points) >= 2:
            left, top, right, bottom = _corners(self.points[0], self.points[-1])
            if self.kind == TOOL_RECT:
                near_x = abs(x - left) <= reach or abs(x - right) <= reach
                near_y = abs(y - top) <= reach or abs(y - bottom) <= reach
                inside_x = left - reach <= x <= right + reach
                inside_y = top - reach <= y <= bottom + reach
                # An unfilled rectangle is its edges, not its middle.
                return (near_x and inside_y) or (near_y and inside_x)
            cx, cy = (left + right) / 2, (top + bottom) / 2
            rx, ry = max(1.0, (right - left) / 2), max(1.0, (bottom - top) / 2)
            distance = math.hypot((x - cx) / rx, (y - cy) / ry)
            tolerance = reach / max(rx, ry)
            return abs(distance - 1.0) <= max(0.08, tolerance)

        if len(self.points) == 1:
            return math.hypot(x - self.points[0].x, y - self.points[0].y) <= reach

        for first, second in zip(self.points, self.points[1:]):
            if _distance_to_segment(x, y, first, second) <= reach:
                return True
        return False


def _corners(a: Point, b: Point) -> tuple[float, float, float, float]:
    return (min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))


def _distance_to_segment(x: float, y: float, a: Point, b: Point) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    if dx == 0 and dy == 0:
        return math.hypot(x - a.x, y - a.y)
    t = ((x - a.x) * dx + (y - a.y) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(x - (a.x + t * dx), y - (a.y + t * dy))


def constrain_shape(start: Point, end: Point) -> Point:
    """Shift on a rectangle or ellipse: make it square, keeping the drag's direction."""
    dx, dy = end.x - start.x, end.y - start.y
    size = max(abs(dx), abs(dy))
    return Point(
        start.x + math.copysign(size, dx or 1.0),
        start.y + math.copysign(size, dy or 1.0),
    )


def constrain_angle(start: Point, end: Point, step_degrees: float = 15.0) -> Point:
    """Shift on a line or arrow: snap to a multiple of `step_degrees`.

    Fifteen degrees is the step that gives horizontal, vertical and both diagonals
    without making a freehand angle impossible to reach.
    """
    dx, dy = end.x - start.x, end.y - start.y
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return end
    step = math.radians(step_degrees)
    angle = round(math.atan2(dy, dx) / step) * step
    return Point(start.x + length * math.cos(angle), start.y + length * math.sin(angle))


def _union(
    a: tuple[float, float, float, float] | None,
    b: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if a is None:
        return b
    if b is None:
        return a
    left = min(a[0], b[0])
    top = min(a[1], b[1])
    right = max(a[0] + a[2], b[0] + b[2])
    bottom = max(a[1] + a[3], b[1] + b[3])
    return (left, top, right - left, bottom - top)


class Document:
    """Everything drawn, and the history of it."""

    def __init__(self) -> None:
        self._items: list[Item] = []
        self._undo: list[tuple[str, list[Item]]] = []
        self._redo: list[tuple[str, list[Item]]] = []
        self._drawing: Item | None = None
        self._dirty: tuple[float, float, float, float] | None = None
        self._settings: dict[str, ToolSettings] = {
            tool: default_settings(tool) for tool in ALL_TOOLS
        }
        self.hidden = False

    # -- reading -----------------------------------------------------------

    @property
    def items(self) -> list[Item]:
        """Finished items. The one being drawn is `pending`."""
        return list(self._items)

    @property
    def pending(self) -> Item | None:
        return self._drawing

    @property
    def is_empty(self) -> bool:
        return not self._items and self._drawing is None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def settings_for(self, tool: str) -> ToolSettings:
        return self._settings.setdefault(tool, default_settings(tool))

    def set_settings(self, tool: str, settings: ToolSettings) -> None:
        self._settings[tool] = settings

    # -- the dirty rectangle ----------------------------------------------

    def dirty_rect(self) -> tuple[float, float, float, float] | None:
        """What has changed since `clear_dirty`, or None when nothing has."""
        return self._dirty

    def clear_dirty(self) -> None:
        self._dirty = None

    def mark(self, rect: tuple[float, float, float, float] | None) -> None:
        self._dirty = _union(self._dirty, rect)

    def mark_everything(self) -> None:
        """For a change whose extent is the whole document: hide, clear, undo of those."""
        total: tuple[float, float, float, float] | None = None
        for item in self._items:
            total = _union(total, item.bounds(pad=4))
        self._dirty = total

    # -- drawing -----------------------------------------------------------

    def begin(self, kind: str, settings: ToolSettings, point: Point) -> Item:
        """Start a stroke or a shape. Replaces any unfinished one."""
        self._drawing = Item(
            kind=kind,
            points=[point],
            colour=settings.colour,
            width=settings.width,
            opacity=settings.opacity,
            font_size=settings.font_size,
        )
        self.mark(self._drawing.bounds(pad=4))
        return self._drawing

    def extend(self, point: Point) -> None:
        """Carry on the stroke. A dragged shape keeps two points, freehand accumulates."""
        item = self._drawing
        if item is None:
            return
        self.mark(item.bounds(pad=4))
        if item.kind in DRAGGED_TOOLS:
            if len(item.points) < 2:
                item.points.append(point)
            else:
                item.points[-1] = point
        else:
            last = item.points[-1]
            # Drop a sample that has not moved: a pen held still would otherwise grow
            # the point list without end.
            if abs(point.x - last.x) >= 0.5 or abs(point.y - last.y) >= 0.5:
                item.points.append(point)
        self.mark(item.bounds(pad=4))

    def finish(self) -> Item | None:
        """Commit the stroke. Returns it, or None if there was nothing worth keeping."""
        item = self._drawing
        self._drawing = None
        if item is None:
            return None
        if item.kind in DRAGGED_TOOLS and len(item.points) < 2:
            self.mark(item.bounds(pad=4))
            return None
        if item.kind not in DRAGGED_TOOLS and len(item.points) < 2:
            # A single click with the pen is a dot, which is a legitimate thing to draw.
            item.points.append(Point(item.points[0].x + 0.1, item.points[0].y + 0.1))
        self._items.append(item)
        self._push_undo("add", [item])
        self.mark(item.bounds(pad=4))
        return item

    def cancel(self) -> None:
        item = self._drawing
        self._drawing = None
        if item is not None:
            self.mark(item.bounds(pad=4))

    def add(self, item: Item) -> Item:
        """Put a finished item in - the text box uses this."""
        self._items.append(item)
        self._push_undo("add", [item])
        self.mark(item.bounds(pad=4))
        return item

    # -- erasing and clearing ---------------------------------------------

    def erase_at(self, x: float, y: float, slack: float = 6.0) -> Item | None:
        """Remove the topmost item under the point. Returns it, or None."""
        for item in reversed(self._items):
            if item.hit(x, y, slack):
                self._items.remove(item)
                self._push_undo("remove", [item])
                self.mark(item.bounds(pad=4))
                return item
        return None

    def clear(self) -> int:
        """Remove everything. Undoable, which is the whole point of doing it here."""
        if not self._items:
            return 0
        removed = list(self._items)
        self.mark_everything()
        self._items.clear()
        self._push_undo("remove", removed)
        return len(removed)

    def set_hidden(self, hidden: bool) -> None:
        """Hide or show the drawings without touching them or the history."""
        if hidden == self.hidden:
            return
        self.hidden = hidden
        self.mark_everything()

    # -- history -----------------------------------------------------------

    def _push_undo(self, action: str, items: list[Item]) -> None:
        self._undo.append((action, items))
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        action, items = self._undo.pop()
        if action == "add":
            for item in items:
                if item in self._items:
                    self._items.remove(item)
                self.mark(item.bounds(pad=4))
        else:  # "remove" - put them back where they were
            for item in items:
                self._items.append(item)
                self.mark(item.bounds(pad=4))
        self._redo.append((action, items))
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        action, items = self._redo.pop()
        if action == "add":
            for item in items:
                self._items.append(item)
                self.mark(item.bounds(pad=4))
        else:
            for item in items:
                if item in self._items:
                    self._items.remove(item)
                self.mark(item.bounds(pad=4))
        self._undo.append((action, items))
        return True

    def reset(self) -> None:
        """Start a new recording with a clean sheet, keeping the tool settings.

        Deliberately not `clear()`: this is not an edit, it is a new document, and
        carrying the old drawings' undo history into it would let the user undo their
        way into the previous recording's picture.
        """
        self._items.clear()
        self._undo.clear()
        self._redo.clear()
        self._drawing = None
        self.hidden = False
        self._dirty = None
