"""User settings: one JSON file under %APPDATA%\\ScreenRec.

Loading never raises. A missing, unreadable or half-written file falls back to defaults and
says why through `load_settings`'s second return value, because an app that silently resets
your settings is worse than one that tells you it did.

The file is hand-editable, so every value that is not a plain number is checked against
what it is allowed to be - see `_coerce`. The interface language is one of those:
`apply_language` is what the application calls at start-up to put it in force.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths

SOURCE_MONITOR = "monitor"
SOURCE_REGION = "region"
SOURCE_WINDOW = "window"
#: Every monitor at once, one file per screen. Measured to be a process each:
#: see multi.py for why one process with several inputs is the worse answer.
SOURCE_ALL_SCREENS = "all_screens"

@dataclass(frozen=True)
class Profile:
    """One way of encoding, as a named trade-off rather than a pile of flags.

    **A profile names a codec, never an encoder.** `codec` is `"hevc"` or `"h264"`, and
    the ffmpeg name behind it - `hevc_nvenc`, `hevc_amf`, `hevc_mf`, `libx264` - is
    chosen once, at record time, by `capabilities.choose` out of what this machine's
    probe actually managed to encode. Until 2026-11-04 this field was
    `encoder="hevc_nvenc"`, which is a lie in the data on every machine without NVENC,
    and `ui/app.py` papered over it with an override that fired only when
    `pix_fmt == "yuv420p"`. That had two consequences, both real: the profile literally
    called "No GPU - x264" was silently overridden onto hardware, and the 4:4:4 profile
    was offered on machines that cannot encode 4:4:4 at all.

    `software_only` is the one thing the chooser cannot work out for itself: it says
    this profile exists *because* the processor is wanted, so no hardware encoder may be
    substituted into it however well it probed.

    **The old `gpu` flag is gone**, and the reason is worth writing down. It was exactly
    `not software_only` in all four profiles, and nothing outside this module ever read
    it - checked across `app`, `tools` and `tests` before removing it. It also claimed
    something no profile can promise: `gpu=True` read as "this profile uses the GPU",
    which is only true if the probe says so, on this machine, today. `software_only` is
    the same single bit written as the question that is actually asked at record time.

    `mb_per_minute` is measured, not guessed: `tools/benchmark_encoders.py` captured a
    window of scrolling coloured text losslessly in 4:4:4 and encoded that same clip with
    every one of these, at 1280x720 and 60 fps, with the settings the app really uses.
    The figures are therefore a busy screen - a still one costs a fraction of it, and a
    real 25 s desktop recording at 2560x1440 came out at 11 MB a minute against the 98
    this table predicts for it. See docs/HANDOVER.md.

    They were all measured on NVENC and on x264, which is the other half of the same
    honesty: a machine that ends up recording through AMF, QSV or Media Foundation is
    being shown somebody else's number until `docs/ENCODERS.md` §5's fourth device
    replaces it with its own first recording.

    The x264 row is the one that was not re-measured with the shipped settings; it is
    the right order of magnitude and no better than that.
    """

    key: str
    label: str
    #: "hevc" or "h264" - a codec, never an ffmpeg encoder name. `encoders.name_for`
    #: turns it into a name once the family is known.
    codec: str
    pix_fmt: str
    extra: tuple[str, ...]
    #: No hardware encoder may be substituted, however well it probes.
    software_only: bool
    note: str
    cq: dict[str, int]
    mb_per_minute: dict[str, float]  # at 1280x720, 60 fps, busy content


QUALITY_LEVELS = ("best", "high", "balanced", "small")
QUALITY_LABELS = {
    "best": "Best",
    "high": "High",
    "balanced": "Balanced",
    "small": "Small file",
}

PROFILES: dict[str, Profile] = {
    "hevc420": Profile(
        key="hevc420",
        label="Standard — H.265",
        codec="hevc",
        pix_fmt="yuv420p",
        extra=(),
        software_only=False,
        note="Plays in Windows, VLC and every editor. The default.",
        cq={"best": 16, "high": 19, "balanced": 23, "small": 27},
        mb_per_minute={"best": 55.4, "high": 38.6, "balanced": 24.5, "small": 14.9},
    ),
    "hevc444": Profile(
        key="hevc444",
        label="Sharp text — H.265 4:4:4",
        codec="hevc",
        pix_fmt="yuv444p",
        extra=("-profile:v", "rext"),
        software_only=False,
        note=(
            "Keeps every colour sample, so small red and blue text stays crisp, and is "
            "the smallest of the lot. Needs VLC, mpv or a video editor — Windows' "
            "own player does not decode 4:4:4."
        ),
        cq={"best": 16, "high": 19, "balanced": 22, "small": 26},
        mb_per_minute={"best": 23.8, "high": 21.5, "balanced": 19.0, "small": 15.2},
    ),
    "h264": Profile(
        key="h264",
        label="Most compatible — H.264",
        codec="h264",
        pix_fmt="yuv420p",
        extra=(),
        software_only=False,
        note="Opens anywhere at all, and is the largest for the quality.",
        cq={"best": 16, "high": 19, "balanced": 22, "small": 26},
        mb_per_minute={"best": 62.6, "high": 41.0, "balanced": 27.7, "small": 16.0},
    ),
    "x264": Profile(
        key="x264",
        label="No GPU — x264",
        codec="h264",
        pix_fmt="yuv420p",
        extra=(),
        software_only=True,
        note="For a machine with no NVENC. Costs CPU that the recording may need.",
        cq={"best": 16, "high": 19, "balanced": 22, "small": 26},
        mb_per_minute={"best": 40.0, "high": 26.0, "balanced": 18.0, "small": 11.0},
    ),
}

DEFAULT_PROFILE = "hevc420"
DEFAULT_QUALITY = "balanced"


def is_a_rectangle(region: Sequence[int]) -> bool:
    """Whether a stored region is four numbers that describe a rectangle with area.

    Four is not enough, and that was a real defect rather than a tidiness: until
    2026-11-05 `_coerce` checked only the length, so `"region": [100, 100, 0, 0]` -
    four honest integers - loaded with no complaint and then raised `ValueError` out of
    `recorder.surface_crop` ("The rectangle 100,100 0x0 does not overlap monitor 0")
    on every path that resolves the source. Reproduced on 2026-11-05: with that file in
    place both `Application.open_settings` and `Application.start_recording` raised,
    so the gear and the record button did nothing at all - and `ScreenRec.cmd` starts
    the program under `pythonw.exe`, which has no console for the traceback to reach.
    A negative width does the same.

    The width and the height are what must be positive. The origin may be anything,
    including a point on a screen that is not plugged in today; `ui/app.py` answers
    that with "Choose something to record first.", which is a sentence, not a crash.
    """
    return (
        len(region) == 4
        and all(isinstance(value, int) and not isinstance(value, bool)
                for value in region)
        and region[2] > 0
        and region[3] > 0
    )


def estimate_mb_per_minute(
    profile_key: str, quality_level: str, width: int, height: int, framerate: int
) -> float:
    """Scale the measured 720p60 figure to this capture. An estimate, and says so."""
    profile = PROFILES.get(profile_key) or PROFILES[DEFAULT_PROFILE]
    base = profile.mb_per_minute.get(quality_level)
    if base is None:
        base = profile.mb_per_minute[DEFAULT_QUALITY]
    pixels = max(1, width * height) / (1280 * 720)
    return base * pixels * (max(1, framerate) / 60)


#: The smallest frame each encoder will really encode, in pixels, as (width, height).
#:
#: Measured on this machine on 2026-11-05 through the real `ddagrab -> encoder` pipeline
#: that `recorder.build_command` builds, at the boundary and twice each, 0.4 s per run:
#:
#:     hevc_nvenc   128x720 FAILED   130x720 ok    720x32 FAILED   720x34 ok
#:     h264_nvenc   144x720 FAILED   146x720 ok    720x48 FAILED   720x50 ok
#:     hevc_mf      128x720 FAILED   130x720 ok    720x32 FAILED   720x34 ok
#:     h264_mf      144x720 FAILED   146x720 ok    720x48 FAILED   720x50 ok
#:     libx264      every shape from 2x2 to 720x720 ok, 2x720 and 720x2 among them
#:
#: Below the floor ffmpeg exits non-zero with `Nothing was written into output file`,
#: which is what a user met after pressing record on a rectangle they were allowed to
#: drag. `ui/app.py` records such a frame on the processor instead - see `min_frame`.
#:
#: **The two Media Foundation rows are not a second, independent measurement.** On this
#: machine Media Foundation dispatches to `NVIDIA HEVC Encoder MFT` (docs/ENCODERS.md
#: §3), so those numbers are NVIDIA's floor measured twice. What an AMD or an Intel
#: encoder refuses is unknown here - RTX 3070 Ti, no AMD and no Intel graphics - which
#: is exactly why `min_frame` answers an unmeasured hardware name with the largest
#: floor anybody here has seen rather than with no floor at all.
MIN_FRAME: dict[str, tuple[int, int]] = {
    "hevc_nvenc": (130, 34),
    "h264_nvenc": (146, 50),
    "hevc_mf": (130, 34),
    "h264_mf": (146, 50),
    "libx264": (2, 2),
}

#: What an encoder nobody here could measure is assumed to refuse: the largest floor in
#: `MIN_FRAME`. Guessing low would hand an AMD or Intel machine the same failed
#: recording this table exists to prevent; guessing high costs, at worst, a postage
#: stamp encoded on the processor, measured at 8.6% of one core (see `ui/app.py`).
UNMEASURED_MIN_FRAME = (146, 50)


def min_frame(encoder: str) -> tuple[int, int]:
    """The smallest frame `encoder` will take here, as (width, height).

    A name in `MIN_FRAME` answers with its measurement. Any other name is treated as
    hardware nobody on this project can test and gets `UNMEASURED_MIN_FRAME`.
    """
    return MIN_FRAME.get(encoder, UNMEASURED_MIN_FRAME)


#: What "record smaller than the screen" offers. 0 is the screen's own size; the rest
#: are heights, and only ever shrink. Even numbers because the encoders want an even
#: width and `scale=-2:h` derives it.
MAX_HEIGHTS: tuple[int, ...] = (0, 1440, 1080, 720)

#: Where the camera sits in the picture, and how much of it it takes. The values are
#: `webcam.CORNERS` and `webcam.SIZES`; they are spelled again here so that `config`
#: does not import a module that needs QtMultimedia just to validate a string.
WEBCAM_CORNERS: tuple[str, ...] = (
    "top_left", "top_right", "bottom_left", "bottom_right",
)
WEBCAM_SIZES: tuple[str, ...] = ("tiny", "small", "medium", "large")
#: How wide the camera is as a fraction of the recorded area, spelled again here for the
#: same reason as the names above: the settings window draws a diagram of the placement
#: and must not pull QtMultimedia in to find out how big "medium" is. `webcam` owns the
#: real values and `tests/test_webcam.py` fails if these two ever disagree.
WEBCAM_SIZE_FRACTIONS: dict[str, float] = {
    "tiny": 0.08, "small": 0.12, "medium": 0.17, "large": 0.24,
}
WEBCAM_SHAPES: tuple[str, ...] = ("rounded", "circle", "rectangle")
DEFAULT_WEBCAM_SHAPE = "rounded"
DEFAULT_WEBCAM_CORNER = "bottom_right"
DEFAULT_WEBCAM_SIZE = "medium"

AUDIO_MIX = "mix"
AUDIO_SEPARATE = "separate"

#: The interface languages, in the order the settings panel offers them. Repeated here
#: rather than imported from `i18n`, which imports Qt: this module is read by the
#: recorder side too and is deliberately free of PySide6. `tests/test_config.py` asserts
#: that the two lists agree, so the copy cannot drift.
LANGUAGES: tuple[str, ...] = ("en", "ru")

# ------------------------------------------------- the player's layout (§2.3)
#
# `docs/PLAYER_LAYOUTS.md` §2.3 is the contract for the `player_*` fields below. The
# ids are the ones `player/layouts.py` names as constants, copied for the same reason
# as `LANGUAGES` and checked by the same test.

PLAYER_LAYOUTS: tuple[str, ...] = (
    "side_by_side", "stacked", "focus", "separate", "one_per_monitor",
)
PLAYER_TILINGS: tuple[str, ...] = ("equal_lr", "big_left", "big_right", "equal_tb")

DEFAULT_PLAYER_TILING = "equal_lr"
#: Equal halves - where a double-click on the splitter puts it back (§3).
DEFAULT_PLAYER_SPLIT = 0.5

#: §2.3: what a stored fraction may be. Anything outside is a hand-edited file.
PLAYER_SPLIT_RANGE = (0.15, 0.85)


@dataclass
class Settings:
    """Everything the recorder needs, and nothing about the window's geometry."""

    source_kind: str = SOURCE_MONITOR
    monitor_index: int = 0
    region: list[int] = field(default_factory=list)  # x, y, w, h in desktop pixels
    window_title: str = ""  # remembered only to re-pick the window in the UI
    window_hwnd: int = 0  # the window chosen last; checked for life before use

    framerate: int = 60
    #: Record no taller than this many pixels; 0 is the screen's own size. A screen
    #: already smaller is left alone rather than scaled up.
    max_height: int = 0
    #: Show the camera in a corner of the recording. It is a window on the desktop, so
    #: it is visible to whoever is sitting at the machine too - see `webcam`.
    webcam_enabled: bool = False
    #: The camera's description as Windows reports it, or "" for whichever is first.
    webcam_device: str = ""
    webcam_corner: str = DEFAULT_WEBCAM_CORNER
    webcam_size: str = DEFAULT_WEBCAM_SIZE
    webcam_shape: str = DEFAULT_WEBCAM_SHAPE
    #: "WIDTHxHEIGHT" to run the camera in that mode, or "" to choose the smallest one
    #: that still covers the picture - see `webcam.choose_format`, and the measured
    #: reason it weighs frame rate first.
    webcam_resolution: str = ""
    profile: str = DEFAULT_PROFILE  # a key of PROFILES, not an ffmpeg encoder name
    quality_level: str = DEFAULT_QUALITY
    draw_mouse: bool = True
    click_highlight: bool = True
    capture_frame: bool = True  # the thin outline round what is being recorded

    mic_enabled: bool = False
    mic_device: str = ""  # the dshow "Alternative name", which survives a rename
    mic_label: str = ""
    system_audio_enabled: bool = False
    system_audio_device: int = -1  # pyaudiowpatch device index, -1 = default loopback
    #: The Windows name of that endpoint, and the thing that actually identifies it.
    #: `system_audio_device` is a PortAudio index, and PortAudio numbers devices in
    #: the order it finds them when it starts - so plugging in a speaker renumbers
    #: every device after it, and an index saved yesterday names something else today
    #: or nothing at all. The microphone has always been remembered by name for this
    #: reason (`mic_device` is a dshow identifier, not a number); this is the same
    #: thing for the playback side, added after a user found the Source list sitting
    #: on "Device 21 (not found)" on a machine where the devices had changed.
    system_audio_label: str = ""
    audio_mode: str = AUDIO_MIX
    #: Shifts the recorded sound against the picture, in milliseconds; positive delays it.
    #: Left at 0 because it was measured not to be needed - see docs/HANDOVER.md. It is a
    #: lever for a machine whose audio path behaves differently, not a tuning knob, which
    #: is why it has no control in the window.
    audio_offset_ms: int = 0

    output_dir: str = ""
    #: Whether the viewer has ever been asked where recordings should go. Separate
    #: from `output_dir` because an empty `output_dir` means "the default place", and
    #: somebody who chose the default on purpose must not be asked again - see
    #: `ui.app.Application._ask_where_to_save`. Remembered whatever the answer was,
    #: Cancel included: a question that returns every launch is the complaint this
    #: field exists to answer.
    output_dir_asked: bool = False
    filename_pattern: str = "ScreenRec_%Y-%m-%d_%H-%M-%S"
    hotkey: str = "Ctrl+Alt+R"
    hotkey_pause: str = "Ctrl+Alt+P"
    #: Switches between drawing on the screen and working with the application
    #: underneath. Deliberately not a single key: it has to work while another
    #: program has the keyboard.
    hotkey_annotate: str = "Ctrl+Alt+D"
    annotate_toolbar_pos: list[int] = field(default_factory=list)
    annotate_collapsed: bool = False
    ffmpeg_dir: str = ""
    countdown: int = 0  # seconds before recording actually starts

    show_island: bool = True  # the floating bar, which is the main interface
    island_pos: list[int] = field(default_factory=list)  # x, y in desktop pixels

    #: Stop on its own after this many minutes; 0 is off. For a long process you start
    #: and walk away from.
    auto_stop_minutes: int = 0
    #: "" and "auto" are the same thing: `ui/app.py` asks `if chosen and chosen !=
    #: "auto"`, so an empty string takes the automatic path exactly as the word does.
    #: Anything else is an ffmpeg encoder name hand-written into the JSON - there is no
    #: control for this in the window - and it is used as given. The automatic answer is
    #: `capabilities.choose`, which picks out of what this machine's probe really
    #: encoded, not out of what ffmpeg lists.
    encoder_choice: str = "auto"
    #: The preset last applied, remembered only so the UI can show which one is current.
    preset: str = ""
    #: What to do the moment a recording finishes: "", "open", "reveal", "copy".
    after_recording: str = ""

    #: The interface language: a code of `LANGUAGES`, or "" for "never chosen", in
    #: which case `i18n.default_language()` decides. See `apply_language`.
    language: str = ""

    # ------------------------------------------- the player's remembered layout
    #
    # docs/PLAYER_LAYOUTS.md §2.3. The player standalone reads this same file.

    #: A layout id of `PLAYER_LAYOUTS`; "" means ask every time.
    player_layout: str = ""
    #: The displays the choice was made on, `player.layouts.display_signature`. A
    #: different signature means the dialog is shown again rather than a layout
    #: restored onto screens it was never measured for.
    player_display_signature: str = ""
    #: Which stream is the main one.
    player_swapped: bool = False
    #: The splitter's fraction, `PLAYER_SPLIT_RANGE`.
    player_split: float = DEFAULT_PLAYER_SPLIT
    #: The separate-windows arrangement of §5: a value of `PLAYER_TILINGS`.
    player_tiling: str = DEFAULT_PLAYER_TILING

    def encoding(self) -> Profile:
        return PROFILES.get(self.profile) or PROFILES[DEFAULT_PROFILE]

    def cq(self) -> int:
        profile = self.encoding()
        return profile.cq.get(self.quality_level, profile.cq[DEFAULT_QUALITY])

    def resolved_output_dir(self) -> Path:
        return Path(self.output_dir) if self.output_dir else paths.default_output_dir()

    def has_audio(self) -> bool:
        return self.mic_enabled or self.system_audio_enabled


def _coerce(raw: dict[str, Any]) -> Settings:
    """Take only known keys of the right shape; ignore the rest.

    **A key this build does not have is skipped, never an error.** That is what lets a
    field be removed: `player_pip_corner` and `player_pip_fraction` were written to
    every settings file on this machine while picture in picture existed, and they are
    still in the file after it was taken out. Raising, or refusing the whole file,
    would lose every other setting with them - the output folder, the hotkeys, the
    language - over two numbers nothing reads any more.
    """
    settings = Settings()
    known = {f.name: f.type for f in fields(Settings)}
    for key, value in raw.items():
        if key not in known:
            continue
        current = getattr(settings, key)
        if isinstance(current, bool):
            if isinstance(value, bool):
                setattr(settings, key, value)
        elif isinstance(current, int):
            if isinstance(value, int) and not isinstance(value, bool):
                setattr(settings, key, value)
        elif isinstance(current, float):
            # JSON writes 0.5 as a float and 1 as an int; both are a fraction here.
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(settings, key, float(value))
        elif isinstance(current, str):
            if isinstance(value, str):
                setattr(settings, key, value)
        elif isinstance(current, list):
            if isinstance(value, list) and all(isinstance(v, int) for v in value):
                setattr(settings, key, list(value))

    if settings.profile not in PROFILES:
        settings.profile = DEFAULT_PROFILE
    if settings.quality_level not in QUALITY_LEVELS:
        settings.quality_level = DEFAULT_QUALITY
    if settings.source_kind not in (
        SOURCE_MONITOR, SOURCE_REGION, SOURCE_WINDOW, SOURCE_ALL_SCREENS
    ):
        settings.source_kind = SOURCE_MONITOR
    if settings.audio_mode not in (AUDIO_MIX, AUDIO_SEPARATE):
        settings.audio_mode = AUDIO_MIX
    settings.framerate = max(5, min(240, settings.framerate))
    if settings.max_height not in MAX_HEIGHTS:
        settings.max_height = 0
    if settings.webcam_corner not in WEBCAM_CORNERS:
        settings.webcam_corner = DEFAULT_WEBCAM_CORNER
    if settings.webcam_size not in WEBCAM_SIZES:
        settings.webcam_size = DEFAULT_WEBCAM_SIZE
    if settings.webcam_shape not in WEBCAM_SHAPES:
        settings.webcam_shape = DEFAULT_WEBCAM_SHAPE
    # A resolution is only ever a hint: the camera may no longer offer it, and
    # `choose_format` falls back rather than refusing. Anything shaped wrong is
    # dropped here so the combo does not show nonsense.
    text = str(settings.webcam_resolution or "")
    parts = text.split("x")
    if not (len(parts) == 2 and all(p.isdigit() and int(p) > 0 for p in parts)):
        settings.webcam_resolution = ""
    settings.countdown = max(0, min(10, settings.countdown))
    settings.audio_offset_ms = max(-500, min(500, settings.audio_offset_ms))
    settings.auto_stop_minutes = max(0, min(600, settings.auto_stop_minutes))
    if settings.after_recording not in ("", "open", "reveal", "copy"):
        settings.after_recording = ""
    if not is_a_rectangle(settings.region):
        # Dropped, not repaired: there is no honest guess at what rectangle somebody
        # meant. The bar then says "Choose a region" and the record button opens the
        # picker, which is the same state as never having chosen one.
        settings.region = []
    if len(settings.island_pos) != 2:
        settings.island_pos = []
    if len(settings.annotate_toolbar_pos) != 2:
        settings.annotate_toolbar_pos = []
    if settings.language not in ("",) + LANGUAGES:
        # A language nobody can read is worse than the default one: forget the choice
        # and let `apply_language` ask the system again.
        settings.language = ""
    if settings.player_layout not in ("",) + PLAYER_LAYOUTS:
        settings.player_layout = ""
    if settings.player_tiling not in PLAYER_TILINGS:
        settings.player_tiling = DEFAULT_PLAYER_TILING
    settings.player_split = _fraction(
        settings.player_split, PLAYER_SPLIT_RANGE, DEFAULT_PLAYER_SPLIT
    )
    return settings


def _fraction(value: float, limits: tuple[float, float], default: float) -> float:
    """A stored fraction pulled back into its range; NaN falls back to `default`.

    `json.loads` accepts the literal `NaN`, and no comparison with it is ever true, so
    a plain `min`/`max` clamp would let it through.
    """
    if value != value:  # NaN
        return default
    low, high = limits
    return max(low, min(high, float(value)))


def load_settings(path: Path | None = None) -> tuple[Settings, str]:
    """Return (settings, problem). `problem` is empty when the file loaded cleanly."""
    target = path or paths.config_path()
    if not target.is_file():
        return Settings(), ""
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Settings(), f"Settings file could not be read ({exc}); using defaults."
    if not isinstance(raw, dict):
        return Settings(), "Settings file is not a JSON object; using defaults."
    return _coerce(raw), ""


def apply_language(settings: Settings) -> str:
    """Put the stored language in force, and say which one that is.

    Called once, right after `load_settings`, before the first window is built. A
    settings file with no choice in it - or with a code this build does not have - asks
    the system, which is the only moment `i18n.default_language()` is consulted: once
    the user picks, their pick wins, including when it is the one the system suggested.

    `i18n` is imported here and not at the top of the module on purpose. It imports Qt,
    and this module is read by the recorder side, which runs without a GUI.
    """
    from . import i18n

    wanted = settings.language or i18n.default_language()
    if wanted not in i18n.LANGUAGES:
        wanted = i18n.default_language()
    return i18n.set_language(wanted)


def save_settings(settings: Settings, path: Path | None = None) -> str:
    """Write atomically. Returns an empty string, or the reason it failed."""
    target = path or paths.config_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temp.replace(target)
    except OSError as exc:
        return f"Settings could not be saved: {exc}"
    return ""
