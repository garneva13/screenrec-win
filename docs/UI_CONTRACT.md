# ScreenRec — UI contract (redesign, 2026-11-03)

The engine is finished and must not be touched: `recorder`, `audio`, `winapi`, `config`,
`paths` stay exactly as they are. This redesign replaces the *interface layer only*.

Read `docs/CONTRACT.md` for the engine's API and for the measured ffmpeg facts, and
`docs/HANDOVER.md` for what has been proven about it.

## The shape of the thing

The old window was a settings form: four cards, twelve controls, all at one level of
importance, and you had to read all of them to press one button. Recording is an action,
not a configuration, so the new interface is built around the action:

1. **The bar** — a small floating always-on-top strip. It is the application. Source
   chip, device toggles (microphone, system sound, camera, cursor), **one level meter
   per audio source**, two quick settings, the record button, a gear. The meters are on
   the recording row too: a playback device that changes mid-take shows up there and
   nowhere else. One meter fed `max(mic, system)` until 2026-11-13, when it cost a
   recorded call - the microphone moved the needle while the system sound recorded
   silence. The camera toggle is on the recording row as well: it is
   the one setting worth changing mid-take, because the camera is a desktop window and
   taking it away costs the recording nothing.
2. **Popovers** — a floating surface above or below the bar, one per chip. The source
   picker, the device lists, the quality list.
3. **The settings window** — everything you change once a month, behind the gear. A
   sidebar of five pages (Recording, Sound, Camera, Control, General), the chosen page
   beside it, and a footer that says what happens to a change. It was one column 1375 px
   tall until 2026-11-08, which was taller than the screen it had to open on.
4. **The capture frame** — a thin outline round whatever is being recorded.

Nothing is modal. Nothing needs a window manager. The bar remembers where it was put and
what was last recorded, so the second launch records the same thing with one click.

## What already exists and must be used

```python
# ui/theme.py
COLOURS: dict[str, str]   # see below
RADIUS: dict[str, int]    # bar 17, surface 15, card 14, control 11, chip 10, small 8
style_sheet(extra="") -> str
apply(app, extra="") -> None
palette() -> QPalette

# ui/icons.py  — stroked into a painter, never a file, never a QIcon cache
KINDS: tuple[str, ...]    # 25 names; add to this tuple to add an icon
draw(painter, kind, rect, colour, weight=1.9) -> bool   # False for an unknown name
pixmap(kind, size, colour, weight=1.9) -> QPixmap

# ui/surfaces.py
BUTTON = 32; ICON = 17
class FloatingSurface(QWidget)   # frameless, on top, painted, rounded, no shadow
    (radius=RADIUS["surface"], fill="panel_raised", border="line", parent=None)
class IconButton(QAbstractButton)
    (kind, tooltip="", role="quiet"|"accent"|"record", size=BUTTON, parent=None)
    .set_on(bool) / .is_on() / .set_kind(str)
class Chip(QAbstractButton)      # icon + elided label + chevron
    (kind="monitor", text="", parent=None); .set_value(kind, text); .set_open(bool)
class ToggleGroup(QWidget)       # hairline outline round related buttons; .add(w)
class Segmented(QWidget)         # exclusive choices shown side by side
    (options=[(key, label), …]); .changed = Signal(str); .set_current(key)
label(text, role="text", size=9.5) -> QLabel
```

## Colours — the whole vocabulary

Violet is **selection** (the chosen tile, an armed toggle). Green is **live** (a device
actually feeding us, the level meter, the ready dot). Red is **recording**, and nothing
else is ever red.

`bg` `field` `panel` `panel_raised` `hover` `line` `line_strong` · `text` `text_soft`
`text_dim` `text_faint` `disabled` · `accent` `accent_light` `accent_hover`
`accent_press` `accent_text` `live` · `record` `record_hover` `record_press` `record_dim`
`record_text` `error_text` · `warn` `ok`

Contrast is tested (`tests/test_theme.py`), so a new colour needs its ratio checked.

## House rules

* **No drop shadows.** Qt draws a square shadow behind a rounded translucent window.
  The hairline border plus the step in surface colour carries elevation.
* Every `paintEvent` wraps its body in `try/finally: painter.end()`. An exception
  escaping a live QPainter takes the interpreter down.
* Every timer is a `QTimer(self)`.
* Fully-qualified Qt enums (`Qt.AlignmentFlag.AlignCenter`), PySide6 spellings.
* English code, comments and UI strings. Type hints. `from __future__ import annotations`.
* Every failure path says something. No `except: pass` without a comment giving the
  reason.
* **Nothing takes focus.** A floating surface uses `WA_ShowWithoutActivating` and the
  `Tool` flag; `surfaces._no_activate` adds `WS_EX_NOACTIVATE`. Clicking Record must not
  pull the user out of what they were about to demonstrate.
* Do not `pip install` anything. Interpreter:
  `.venv\Scripts\python.exe`. Check Qt work with
  `QT_QPA_PLATFORM=offscreen`.
* Tests are plain `unittest` under `tests/`, runnable headless.

## Modules to build

### `ui/thumbnails.py`

```python
@dataclass(frozen=True) class Thumb: pixmap: QPixmap; width: int; height: int
def monitor_thumb(monitor: winapi.Monitor, width: int = 240) -> Thumb | None
def window_thumb(info: winapi.WindowInfo, width: int = 240) -> Thumb | None
class ThumbnailLoader(QObject):
    """Grabs thumbnails off the GUI thread and hands them back by signal."""
    ready = Signal(str, object)      # key, Thumb
    def request(self, key: str, monitor=None, window=None, width=240) -> None
    def stop(self) -> None
```

* Grab one frame with the bundled ffmpeg: `ddagrab` with the same offsets
  `recorder.VideoSource` would use, `-frames:v 1`, scaled down, to PNG on disk in a
  temp folder, then loaded as a `QPixmap`. Reuse
  `recorder.VideoSource` and `recorder._filter_for` rather than rebuilding the geometry —
  a rotated screen must come back upright, and that mapping is already measured.
* **A QPixmap may only be built on the GUI thread.** The worker makes a `QImage` (or
  reads bytes) and the GUI slot converts. Getting this wrong gives random crashes.
* Grabbing is slow enough to matter (~200–400 ms each). Never block the GUI thread;
  cache by key and hand back the cached one immediately.
* Report failure: `ready` with `None` rather than silence, so the card can show a
  placeholder and the log can say why.

### `ui/sources.py`

```python
class SourcePicker(FloatingSurface):
    chosen = Signal(str, object)   # config.SOURCE_*, payload (monitor index / hwnd / rect)
    cancelled = Signal()
    def __init__(self, settings: config.Settings, parent=None)
    def refresh(self) -> None
    def popup_near(self, widget: QWidget) -> None   # place above or below, on screen
```

* A `Segmented` at the top: Screen / Region / Window.
* **Screen** and **Window**: a grid of cards, two per row, each a 16:10.5 thumbnail well
  over a name and a detail line (`2560 × 1440 · primary`, or the process name). The
  chosen card gets a 1.5 px `accent` border and a soft outer ring.
* **Region**: one button, "Select an area…", plus the remembered rectangle if there is
  one. Emits `SOURCE_REGION` with the rect the existing `overlay.RegionSelector` returns.
* The window list uses `winapi.list_windows()`; refresh re-reads it. A window that has
  closed since the list was built must be refused in words, not crash.
* Width 460. Height follows the content, capped at 520 with the grid scrolling.

### `ui/bar.py`

```python
class Bar(FloatingSurface):
    record_requested = Signal()
    stop_requested = Signal()
    discard_requested = Signal()
    source_clicked = Signal()
    settings_clicked = Signal()
    mic_toggled = Signal(bool)
    system_toggled = Signal(bool)
    clicks_toggled = Signal(bool)
    mic_menu_requested = Signal()
    system_menu_requested = Signal()
    fps_clicked = Signal()
    format_clicked = Signal()
    folder_clicked = Signal()
    last_file_clicked = Signal()
    moved = Signal(int, int)
    def set_source(self, kind: str, text: str) -> None
    def set_toggles(self, mic: bool, system: bool, clicks: bool) -> None
    def set_quick(self, fps_text: str, format_text: str) -> None
    def set_footer(self, folder: str, last_file: str, hotkey: str) -> None
    def set_recording(self, recording: bool, in_shot: bool = False) -> None
    def set_progress(self, seconds, megabytes, fps, dropped=0) -> None
    def set_mic_level(self, level: float) -> None     # 0..1, drives the meter
    def place(self, x: int, y: int) -> None
    def place_default(self, monitors) -> None
    def move_off(self, recorded_index: int, monitors) -> bool
```

Idle layout, left to right: the source `Chip`; a `ToggleGroup` of three `IconButton`s
(microphone, speaker, pointer) — each toggles, and each has a small chevron hit-area on
its right edge that emits the `*_menu_requested` signal instead; **a live green level
meter beside the microphone**, 5 bars, driven by `set_mic_level`; a thin separator; two
quick pills showing the frame rate and the format, each clickable; the red record button
(36 px); the gear.

Under that, a footer strip inside the same surface, separated by a 1 px line: a folder
button with the output path, "Last file" (disabled until there is one), and the hotkey
drawn as a **keycap** — a small rounded box with a hairline border, not plain text.

Recording layout: a red stop button, the elapsed clock in tabular figures, `128 MB ·
60 fps` beside it in `text_dim` (or the dropped-frame count in `warn` when frames are
being dropped), a discard button (trash), and the folder button. No footer.

**There is no pause.** ffmpeg cannot pause a capture cleanly, and a button that lies is
worse than one that is missing. If the user asks, the answer is stop and record again.

Dragging the body moves the bar; it snaps to a screen edge within 28 px and emits
`moved`. A click that did not drag is a click. Right-click opens a `QMenu` styled with
`theme.style_sheet()`.

### `ui/settings_panel.py`

```python
class Switch(QAbstractButton):          # a real toggle switch, not a check box
    def __init__(self, parent=None); toggled is inherited
class SettingsPanel(FloatingSurface):
    changed = Signal()                  # the panel wrote into the Settings it was given
    closed = Signal()
    def __init__(self, settings: config.Settings, parent=None)
    def refresh_devices(self, mics, loopbacks) -> None
    def popup_near(self, widget: QWidget) -> None
```

Width 360, header "Recording settings" with a close button, then labelled sections:

* **Video** — Frame rate, Format (the four `config.PROFILES` by label), Quality (the four
  `config.QUALITY_LEVELS`), and a live line estimating megabytes per minute from
  `config.estimate_mb_per_minute`. Under the format picker, the chosen profile's `note`
  in `text_dim` — it is where the 4:4:4 compatibility warning lives.
* **Audio** — Microphone, System sound, Tracks (mixed / separate; only enabled when both
  are on).
* **More** — Show the cursor, Highlight clicks, Capture frame, Floating bar, Countdown.
  The first four are `Switch`es.
* **Output** — the folder, with a browse button.
* **Hotkey** — a line edit; invalid text must be refused in words.

Writes straight into the `Settings` object it was given and emits `changed`; the caller
saves. Dropdowns are `QComboBox` with `theme.style_sheet()` applied by the application.

### `ui/captureframe.py`

```python
class CaptureFrame(QWidget):
    """A thin outline round the rectangle being recorded."""
    def show_for(self, monitor: winapi.Monitor, rect: tuple[int,int,int,int] | None,
                 label: str = "Recording") -> bool
    def hide_frame(self) -> None
```

* 2 px `record` outline with a small rounded tag in the top-left corner: the tag is the
  `panel` colour with `record` text and a `record` border, **not** white on red (white on
  this red is 3.7:1).
* **It must never appear in the file.** Two ways, both required:
  * a **region or window** capture is framed from *outside* the rectangle, inset by its
    own thickness plus a pixel — this needs nothing of Windows and is preferred wherever
    it fits;
  * a **whole-monitor** capture has no outside, so the window covers the monitor, the
    outline is stroked along its very edge, and the window carries
    `WDA_EXCLUDEFROMCAPTURE` (`surfaces.exclude_from_capture`). This path is taken *only*
    when that call is read back as having taken effect; where it has not — Windows older
    than 10 version 2004 — `show_for` returns `False` and draws nothing, and the caller
    says so.
  Prove both: `tools/verify_capture_frame.py` records each case with `ddagrab`, counts
  the outline colour in the recording's outer ring (must be 0) and in the widget's own
  grab (must not be), so "clean" cannot be passed by never painting.
* Click-through, always on top, never focusable — `Qt.WindowTransparentForInput`,
  `WA_TransparentForMouseEvents`, plus `WS_EX_TRANSPARENT | WS_EX_LAYERED |
  WS_EX_NOACTIVATE`. `overlay.ClickHighlight` already does this correctly; copy the
  approach, do not import the class.
* **Every screen in the set gets one.** `ui/app.py` keeps a list of frames and shows one
  per recorded monitor, so an all-screens recording outlines all of them.
* A window that is being recorded can move; the recording does not follow it. The frame
  stays where the recording is, which is the honest thing to show.

### `tests/`

One file per module, plain `unittest`, `QT_QPA_PLATFORM=offscreen`, no GPU, no recording,
no visible window. At minimum: every widget builds and `grab()`s without raising in every
state it has; every signal fires on the interaction that should fire it; geometry helpers
are checked against known numbers.
