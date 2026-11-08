# ScreenRec — module contract

A small Windows screen recorder. **Recording only** — no editor, no timeline, no export
presets. Python 3.13 + PySide6 + a bundled ffmpeg driven as a subprocess.

Everything below marked **measured** was verified on this machine on 2026-11-03. Do not
"fix" a measured fact from memory of how ffmpeg usually behaves.

## Layout

```
the checkout root\
  .venv\                     Python 3.13, PySide6 6.12, pyaudiowpatch, comtypes
  tools\ffmpeg\              ffmpeg.exe, ffprobe.exe (BtbN gpl, N-128352)
  app\screenrec\
    paths.py      DONE   binary + config + output locations
    winapi.py     DONE   DXGI monitors, window list, DPI
    config.py     DONE   Settings dataclass, JSON load/save
    audio.py      DONE   dshow mic list, WASAPI loopback feeder over a named pipe
    recorder.py   DONE   ffmpeg command builder (N sources) + process lifecycle
    hotkeys.py    TODO
    clicks.py     TODO   low-level mouse hook
    overlay.py    TODO   region selector + click highlight
    webcam.py     DONE   the camera in a corner, drawn on the desktop so the
                         capture picks it up - see its own docstring for why.
                         Corner, size, outline (rounded / circle / rectangle)
                         and the camera's own capture mode; the automatic mode
                         weighs frame rate before size, because this camera's
                         two largest modes run at 2 fps
    ui/theme.py   TODO
    ui/main_window.py  TODO (assembled last)
  tests\
  docs\
```

Run anything with `.venv\Scripts\python.exe`. **Do not pip install
anything** — the venv is shared and already has what is needed.

## House rules

* Code, identifiers, comments, docstrings and all UI strings in **English**.
* Type hints, `from __future__ import annotations`, dataclasses where they fit.
* Every failure path says something. No bare `except: pass` that hides a real error, no
  silent no-op branch — if a thing did not happen, the caller must be able to find out.
* Windows calls go through `ctypes`; check return values and report the error code.
* Keep Qt enums fully qualified (`Qt.AlignmentFlag.AlignCenter`), PySide6 spellings
  (`QtGui.QAction`, `QtGui.QShortcut`).
* Every timer is a `QTimer(self)`. Never raise inside a `QPainter` block — an exception
  with a live painter takes the interpreter down.
* No third-party packages beyond what is already installed.

## Measured ffmpeg facts

* `ddagrab` feeds `h264_nvenc` **directly**, no filter in between; output is yuv420p.
* `hwmap=derive_device=cuda` **fails** on this build. There is no GPU filter path.
* Any filtering means `hwdownload,format=bgra,…` and must end `format=yuv420p`
  (otherwise libx264 writes yuv444p, which many players refuse).
* A rotated screen arrives in the panel's native orientation; DXGI rotation 2 needs
  `transpose=1` (verified by eye on the user's portrait monitor).
* ffmpeg reads a Windows named pipe (`\\.\pipe\name`) as an input — verified.
* Writing `q` to ffmpeg's stdin makes it write the MP4 trailer and exit 0 — verified.
  **stdin is therefore reserved**; system audio goes down the named pipe.
* `-thread_queue_size` is an output option on this build and errors on inputs.
* NVENC flags that exist here: `-preset p1..p7 -tune hq|ll -rc vbr|cbr -cq N -b:v 0`.

## Machine

* GPU: NVIDIA RTX 3070 Ti (NVENC h264 + hevc).
* Monitor DXGI 0 = `\\.\DISPLAY2`, 2560×1440 at (0,0), not rotated, primary.
* Monitor DXGI 1 = `\\.\DISPLAY1`, 1440×2560 at (2560,0), **rotated 90°**.
* `ddagrab`'s `output_idx` is the DXGI output index — `winapi.list_monitors()` returns
  exactly that index, so never renumber it.

## Existing API the TODO modules build on

```python
# winapi.py
enable_dpi_awareness() -> bool
@dataclass(frozen=True) Monitor:
    index, device_name, x, y, width, height, rotation, adapter
    .rotated -> bool          # rotation in (2, 4)
    .label -> str
    .contains(x, y) -> bool
list_monitors() -> list[Monitor]           # x/y/width/height are DESKTOP coords
monitor_at(monitors, x, y) -> Monitor | None
@dataclass(frozen=True) WindowInfo: hwnd, title, process, x, y, width, height; .label
list_windows(min_size=80) -> list[WindowInfo]
window_rect(hwnd) -> (x, y, w, h) | None
window_is_alive(hwnd) -> bool
cursor_pos() -> (x, y)

# config.py
SOURCE_MONITOR = "monitor"; SOURCE_REGION = "region"; SOURCE_WINDOW = "window"
AUDIO_MIX = "mix"; AUDIO_SEPARATE = "separate"
ENCODERS: dict[str, tuple[str, str, bool]]      # key -> (encoder, label, needs_gpu)
@dataclass Settings: ...                         # see the file
    .resolved_output_dir() -> Path
    .has_audio() -> bool
load_settings(path=None) -> (Settings, problem_str)
save_settings(settings, path=None) -> problem_str

# audio.py
SAMPLE_RATE = 48000; CHANNELS = 2
@dataclass(frozen=True) MicDevice: name, alt_name; .ffmpeg_id
parse_dshow_devices(text) -> list[MicDevice]
list_microphones(ffmpeg) -> list[MicDevice]
@dataclass(frozen=True) LoopbackDevice: index, name, channels, rate
list_loopback_devices() -> list[LoopbackDevice]
default_loopback_index() -> int
class LoopbackFeeder(pipe_name, device_index=-1): start(), stop(), .error, .frames_written

# recorder.py
even(v) -> int
rotation_filter(rotation) -> str
surface_crop(monitor, x, y, w, h) -> (offset_x, offset_y, w, h)   # desktop -> surface
@dataclass(frozen=True) VideoSource: monitor, crop=None, name=""
    .ddagrab_args(framerate, draw_mouse) -> str
    .output_size() -> (w, h)
@dataclass RecordingPlan: sources, outputs, encoder, quality, framerate, draw_mouse,
                          layout, mic_device, loopback_pipe, audio_mode
build_command(ffmpeg, plan) -> list[str]
@dataclass Progress: frames, fps, seconds, size_bytes, dropped
class Recorder(ffmpeg):
    start(plan, on_progress=None, loopback_device=-1) -> problem_str
    stop(timeout=12.0) -> problem_str
    .running, .elapsed, .progress, .log_text(), .command
output_path(settings, when, suffix="") -> Path
pipe_name(tag="") -> str
```

## Modules to build

### `hotkeys.py`

A system-wide hotkey, so start/stop works while another app has focus.

```python
parse_hotkey(text: str) -> tuple[int, int] | None   # "Ctrl+Alt+R" -> (modifiers, vk)
format_hotkey(modifiers: int, vk: int) -> str       # the inverse, for display
class HotkeyListener:
    def __init__(self, on_pressed: Callable[[], None]) -> None
    def start(self, hotkey_text: str) -> str   # "" or why it failed
    def stop(self) -> None
    @property
    def active(self) -> bool
    @property
    def error(self) -> str
```

* `RegisterHotKey` is bound to the thread that called it, and `WM_HOTKEY` arrives on that
  thread's message queue. So the listener owns a thread running `GetMessageW`; `stop()`
  must wake it (`PostThreadMessageW` with `WM_QUIT` or a private message) and join it.
* `RegisterHotKey` **fails** if another process already holds the combination. That is the
  single most likely failure and must be reported as text the user can act on, naming the
  combination — not swallowed.
* `on_pressed` fires on the listener thread. Say so in the docstring; the UI will marshal.
* Support Ctrl / Alt / Shift / Win and A–Z, 0–9, F1–F24. `MOD_NOREPEAT` so holding the
  key does not retrigger.

### `clicks.py`

```python
@dataclass(frozen=True) ClickEvent: x: int; y: int; button: str  # "left"|"right"|"middle"
class ClickWatcher:
    def __init__(self, on_click: Callable[[ClickEvent], None]) -> None
    def start(self) -> str      # "" or why it failed
    def stop(self) -> None
    @property
    def active(self) -> bool
```

* `SetWindowsHookExW(WH_MOUSE_LL, …)`. A low-level hook needs a message loop on the
  installing thread, so it gets its own thread like the hotkey listener.
* **Keep a reference to the `CFUNCTYPE` callback object** for the hook's lifetime. If it
  is garbage-collected the process crashes the next time the hook fires.
* Never block in the callback — `CallNextHookEx` immediately, and hand the event off.
* Coordinates in the hook are already physical desktop pixels.

### `overlay.py`

Two always-on-top, frameless, translucent Qt widgets.

```python
class RegionSelector(QtWidgets.QWidget):
    """Full-desktop dim; drag to choose a rectangle. Esc cancels, Enter accepts."""
    selected = QtCore.Signal(int, int, int, int)   # desktop x, y, w, h
    cancelled = QtCore.Signal()
    @staticmethod
    def choose(parent=None) -> tuple[int, int, int, int] | None   # modal convenience

class ClickHighlight(QtWidgets.QWidget):
    """A click-through overlay that draws a fading ring wherever the mouse is pressed."""
    def __init__(self, parent=None) -> None
    def start(self) -> str      # "" or why it failed
    def stop(self) -> None
    def flash(self, x: int, y: int, button: str = "left") -> None
```

* `RegionSelector` must cover **all** monitors, including the rotated one, so it is sized
  to the union of `list_monitors()` rectangles. Show the live size in pixels while
  dragging; snap nothing. Returned coordinates are desktop pixels, and the caller will
  make them even — do not round here.
* `ClickHighlight` must **not** take input: `Qt.WindowType.WindowTransparentForInput`,
  `Qt.WidgetAttribute.WA_TransparentForMouseEvents`, plus `WS_EX_TRANSPARENT |
  WS_EX_LAYERED | WS_EX_NOACTIVATE` through ctypes for good measure. A click must reach
  the application underneath — if it does not, the feature is worse than useless.
* The highlight is drawn **on the real desktop**, which is what DDA captures, so it needs
  no involvement from the ffmpeg pipeline at all. Rings fade over ~450 ms; drive them
  from one `QTimer(self)` at ~60 Hz that stops itself when no ring is alive.
* Windows flags that matter: `Qt.WindowType.FramelessWindowHint`,
  `WindowStaysOnTopHint`, `Tool` (so it never appears in the task bar),
  `WA_TranslucentBackground`, `WA_ShowWithoutActivating`.

### `ui/theme.py`

A standalone dark look in the spirit of the user's Maya tools — **do not import anything
from the Maya repo**; this project stands alone.

```python
COLOURS: dict[str, str]        # bg, panel, text, text_dim, line, accent, accent_text,
                               # record, record_dim, warn, ok, field, hover
def style_sheet(extra: str = "") -> str
def apply(app: QtWidgets.QApplication) -> None
class Card(QtWidgets.QFrame)   # titled panel
def caption(text: str) -> QtWidgets.QLabel
def row(label: str, widget) -> QtWidgets.QWidget
```

* One accent colour family; a distinct red for the record state.
* Buttons get a role via `objectName` (`primary`, `record`, `stop`, `ghost`) and the
  sheet styles them — colour means what the button does.
* `QComboBox`, `QSpinBox`, `QCheckBox`, `QLineEdit`, `QPushButton`, `QGroupBox`,
  `QToolButton`, `QProgressBar`, `QScrollArea` all need rules, or Windows' native style
  shows through and the window looks half-finished. A spin box's arrows in particular are
  drawn by the style from a geometry the sheet changed — give them explicit geometry.
* Readable: body text must reach contrast ratio 4.5 against its background. State in a
  module docstring comment what you measured.

### `tests/`

`pytest` is **not** installed and must not be. Write plain `unittest` files runnable as
`python -m unittest discover -s tests`. No test may require a GPU, a recording, or a
visible window (use `QT_QPA_PLATFORM=offscreen` where a widget is needed).
