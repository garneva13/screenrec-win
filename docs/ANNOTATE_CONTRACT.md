# ScreenRec — annotation layer (contract)

Draw over the screen while recording: circle a thing, point an arrow at it, write a note.
The drawings go into the video; the toolbar does not.

Functional reference: Epic Pen. This is our own implementation, in ScreenRec's
architecture and ScreenRec's look.

---

## The architecture question, already settled by measurement

**Do not assume a transparent window is captured, or that the toolbar can be kept out.**
Both were measured on this machine on 2026-11-04, against the real capture path:

* An ordinary always-on-top translucent window **is** captured by `ddagrab` — the Desktop
  Duplication API takes the composed desktop. Proven earlier with the click highlight:
  the ring appears in a captured frame.
* `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE = 0x11)` makes a window stay on
  screen and **vanish from the capture**. Two identical bright panels side by side, one
  excluded: the kept one contributed 3551 pixels to the frame, the excluded one
  contributed **zero**.

So the architecture is **two windows, and the encoding pipeline is not touched at all**:

| | window | captured? |
|---|---|---|
| the drawings | `annotate.canvas.Canvas` — transparent, full desktop | **yes**, naturally |
| the toolbar, popovers, hints, the text caret | excluded via `surfaces.exclude_from_capture` | no |

That answers §4 of the brief for all three modes at once — whole monitor, region and
window are all crops of the same composed desktop, so a drawing on it is in all of them.
There is no compositing step, no second render, no double-draw, and no added latency: the
frame the encoder sees already has the drawing in it, at the moment it was drawn.

`exclude_from_capture` needs **Windows 10 2004 or newer** and returns whether it took.
On an older build it returns False and the toolbar *would* be recorded — the controller
must say so plainly rather than let it happen quietly.

---

## What already exists

```python
# ui/theme.py     COLOURS, RADIUS, style_sheet, apply   (violet accent, green = live, red = recording)
# ui/icons.py     KINDS, draw(painter, kind, rect, colour, weight), pixmap(...)
# ui/surfaces.py  FloatingSurface (hide_from_capture = True by default, .excluded_from_capture),
#                 IconButton, Chip, ToggleGroup, Segmented, label, exclude_from_capture
# winapi.py       Monitor (index, x, y, width, height, rotation), list_monitors(),
#                 monitor_at(), WindowInfo, list_windows(), window_rect(), cursor_pos()
# hotkeys.py      parse_hotkey, format_hotkey, HotkeyListener(on_pressed).start(text)
# config.py       Settings (a dataclass persisted as JSON), PROFILES, QUALITY_LEVELS
# annotate/model.py   WRITTEN ALREADY — read it; it is the spine (see below)
```

House rules are in `docs/UI_CONTRACT.md` and they all apply here: no drop shadows, every
`paintEvent` wrapped in `try/finally: painter.end()`, every timer a `QTimer(self)`,
fully-qualified Qt enums, English code and UI strings, every failure path says something,
no `pip install`.

---

## Coordinates — one rule, stated once

**Everything in the model is in physical desktop pixels**, the same space
`winapi.list_monitors()` reports and the same space `recorder.surface_crop` consumes.
Qt gives logical units on a scaled display, so every point coming from a mouse event is
converted on the way in and never again.

* The process is per-monitor-DPI-aware v2 (`winapi.enable_dpi_awareness`), so a 150%
  monitor reports its real pixels.
* Monitors may have **negative coordinates** — a screen to the left of the primary is at
  a negative x. The canvas spans the union rectangle of all monitors, which may therefore
  start at a negative origin. Never assume (0, 0).
* A rotated monitor needs nothing here: desktop coordinates are already in the
  orientation the user sees. The rotation only matters inside `recorder.surface_crop`,
  which is tested.

**Binding for the three recording modes.** Drawings are anchored to the **screen**, which
is the only coherent answer, because the recording is too: a region or window capture is
a fixed rectangle of the desktop and does not follow the window. So a drawing stays where
it was put relative to the video frame, and moving the recorded window moves the window
out from under both the recording and the drawing together. Say this in the help; do not
promise anchoring to anything inside the recorded application.

---

## `annotate/model.py` — already written, read it first

Pure Python, no Qt painting, fully unit tested. The canvas renders it; the toolbar edits
the tool settings; nothing else may own drawing state.

```python
TOOL_PEN, TOOL_MARKER, TOOL_LINE, TOOL_ARROW, TOOL_RECT, TOOL_ELLIPSE, TOOL_TEXT, TOOL_ERASER
@dataclass ToolSettings: colour, width, opacity, font_size
@dataclass(frozen=True) Point: x, y                      # physical desktop pixels
@dataclass Item: kind, points, text, colour, width, opacity, font_size, item_id
    .bounds(pad) -> (x, y, w, h)        # for the dirty rectangle
    .hit(x, y, slack) -> bool           # for the eraser
class Document:
    items -> list[Item]            (visible ones only; see hidden)
    add(item) / begin(kind, settings, point) / extend(point) / finish() -> Item | None
    erase_at(x, y) -> Item | None
    clear() -> int                 # undoable, like everything else
    undo() -> bool / redo() -> bool / can_undo / can_redo
    hidden: bool                   # hide the drawings without deleting them
    is_empty -> bool
    dirty_rect() -> tuple | None   # union of what changed since the last clear_dirty()
    clear_dirty()
    settings_for(tool) -> ToolSettings      # each tool remembers its own
```

Everything that changes the picture goes through the document, so undo, redo and the
dirty rectangle are never out of step with what is on screen.

---

## Modules to build

### `annotate/canvas.py`

```python
class Canvas(QtWidgets.QWidget):
    """The transparent layer the drawings live on. THIS one is recorded."""
    MODE_PASSTHROUGH = "passthrough"
    MODE_DRAW = "draw"
    stroke_finished = QtCore.Signal(object)     # the Item
    text_requested = QtCore.Signal(int, int)    # desktop x, y - the controller opens an editor
    escaped = QtCore.Signal()
    def __init__(self, document: model.Document, parent=None)
    def set_mode(self, mode: str) -> None
    def mode(self) -> str
    def set_tool(self, tool: str) -> None
    def refresh_geometry(self) -> None          # monitors changed
    def desktop_rect(self) -> QtCore.QRect
```

* `hide_from_capture` is **False** here — this is the one surface that must be recorded.
  It is a plain `QWidget`, not a `FloatingSurface`.
* Frameless, always on top, `WA_TranslucentBackground`, `WA_ShowWithoutActivating`, `Tool`.
  Spans the union of all monitors.
* **Pass-through mode**: `Qt.WindowType.WindowTransparentForInput` plus
  `WA_TransparentForMouseEvents`, and `WS_EX_TRANSPARENT | WS_EX_LAYERED` through ctypes —
  exactly what `overlay.ClickHighlight` does and which is proven click-through. Drawings
  stay visible; the mouse and keyboard reach the application underneath.
* **Draw mode**: input is taken. A press must not reach the application below.
* Switching modes must **not** recreate the window — a rebuilt window loses its
  capture-visibility and flickers in the recording. Change the flags on the live window
  and reassert them.
* **Shift** constrains: a square or circle for the shapes, and a line snapped to 15°.
* Repaint **only the dirty rectangle** (`document.dirty_rect()`), never the whole desktop.
  Full-screen repaints next to a 2560×1440 capture are the thing to design against.
* The marker is translucent; draw it with `QPainter.CompositionMode_SourceOver` at its
  own opacity so what is underneath stays readable.
* Never raise out of `paintEvent`.

### `annotate/toolbar.py`

```python
class Toolbar(surfaces.FloatingSurface):
    tool_changed = QtCore.Signal(str)
    mode_toggled = QtCore.Signal()
    undo_requested / redo_requested / clear_requested = QtCore.Signal()
    visibility_toggled = QtCore.Signal()
    settings_changed = QtCore.Signal(str)       # the tool whose settings changed
    collapsed_changed = QtCore.Signal(bool)
    moved = QtCore.Signal(int, int)
    def __init__(self, document: model.Document, parent=None)
    def set_mode(self, mode: str) -> None
    def set_tool(self, tool: str) -> None
    def set_history(self, can_undo: bool, can_redo: bool) -> None
    def set_hidden_drawings(self, hidden: bool) -> None
    def set_collapsed(self, collapsed: bool) -> None
    def place(self, x: int, y: int) -> None
```

Main row, left to right: a drag grip, the **mode button** (cursor icon = work with the
application, pencil = draw), a separator, then pen, marker, arrow, shapes (with a small
chevron choosing rectangle or ellipse), text, eraser, a separator, the **colour swatch**,
the **thickness** control, a separator, undo, redo, the eye (hide the drawings without
deleting them), the bin (clear), and a chevron that collapses the panel.

* Secondary settings open in a **popover under the control they belong to**, not in a
  dialog: the colour row, a thickness slider with a live preview stroke, opacity for the
  marker, font size for text.
* Every button has a tooltip naming the tool **and its shortcut**.
* Collapsed: a small pill with the grip, the current tool, the colour, and the mode
  button — enough to get back out of draw mode, which must never become unreachable.
* Draggable, snaps to screen edges, remembers its position.
* **It must never sit under the pointer when a stroke starts, and never cover the text
  box being typed into.** Expose `avoid(rect)`: nudge the panel out of the way of that
  desktop rectangle, along the shortest axis, staying on screen.

### `annotate/textbox.py`

```python
class TextBox(QtWidgets.QWidget):
    committed = QtCore.Signal(str)      # the text; empty means cancelled
    cancelled = QtCore.Signal()
    def __init__(self, settings: model.ToolSettings, parent=None)
    def open_at(self, x: int, y: int) -> None      # desktop pixels
```

* An in-place editor at the click point, in the current colour and font size, over a
  faint backing so it is legible on any wallpaper.
* **Russian must work**: do not filter key events by keycode, let Qt's input method
  deliver the text. Test with Cyrillic.
* Enter commits, Shift+Enter is a new line, Esc cancels. Clicking elsewhere commits.
* This widget *is* chrome while it is being typed into (a caret and a box the user should
  not see in the video), so it is **excluded from capture**; the committed text becomes a
  document item drawn by the canvas, which is recorded.

### `tests/`

Plain `unittest`, offscreen, no GPU, no recording, no visible window. Cover at least:
geometry across monitors with negative coordinates; Shift constraints; the dirty
rectangle actually bounding the change; mode switching not recreating the window; the
eraser hitting what it should and missing what it should not; Cyrillic text surviving
the round trip; every widget painting in every state.
