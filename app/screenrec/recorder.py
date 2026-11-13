"""Turning a choice of screen into an ffmpeg command, and running it.

Everything measured on this machine's ffmpeg (N-128352, BtbN gpl) on 2026-11-03, because
the right pipeline here is not the one the documentation suggests:

* ``ddagrab`` feeds ``h264_nvenc`` **directly**, with no filter at all. The frames stay in
  D3D11 memory and the encoder takes them. Output pix_fmt is yuv420p.
* ``hwmap=derive_device=cuda`` FAILS on this build ("Failed to created derived device
  context: -40"), so there is no GPU filtering path. Any filter at all means
  ``hwdownload`` and a CPU round trip.
* A CPU path must end in ``format=yuv420p``. Left alone, ``hwdownload,format=bgra`` into
  libx264 produces **yuv444p**, which a good many players refuse.
* A rotated screen arrives in the panel's native orientation - DDA does not un-rotate it -
  so a portrait monitor needs ``transpose``, which drags it onto the CPU path.

The builder takes a list of sources so that recording several screens at once is a matter
of passing more of them, not of rewriting this module.
"""
from __future__ import annotations

import dataclasses
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import audio as audio_mod
from . import config as config_mod
from . import encoders as encoders_mod
from . import takes as takes_mod
from .i18n import _
from .winapi import Monitor

CREATE_NO_WINDOW = 0x08000000

#: How long a new ffmpeg is watched before the recording is reported as begun.
#: Measured on this build on 2026-11-05, five runs each: every way a recording can be
#: doomed by its own arguments was over long before this - ddagrab on a DXGI output
#: that does not exist 0.196-0.213 s, a dshow microphone that is not plugged in
#: 0.265-0.281 s, the loopback pipe with nobody feeding it 0.247-0.251 s, an output
#: path on a drive that is not there 0.247-0.249 s - while a capture that works was
#: still running. Re-measured 2026-11-05, three runs each: output_idx=9 dies at 0.197,
#: 0.197, 0.198 s and an output on a missing drive at 0.232, 0.234, 0.236 s. The
#: margin is what is left over the slowest of them - 69 ms over the dshow microphone
#: above - not the width of the window, so this number is not comfortable, only
#: sufficient. A start that outlives it is caught by `_tick` 200 ms later as before.
#:
#: No frames are lost to it - ffmpeg is already capturing while this watches - but it
#: is not free either, and the comment here used to say it was. `Recorder.start`,
#: `RecordingSession.start` and `RecordingSession.resume` are called straight from Qt
#: slots, so waiting it out **blocks the window**: measured 0.355-0.363 s per call for
#: a healthy start, on top of the loopback feeder's 0.15 s. That is why `wait=False`
#: and `settle()` exist. A caller starting several recorders at once must use them:
#: waiting between launches is what sets the screens of an all-screens recording
#: 0.35 s apart, and there is no cheaper proof to wait for - the first `-progress`
#: line of a healthy ddagrab capture arrives at 0.729-0.742 s, measured, long after
#: a doomed one is already dead.
START_GRACE = 0.35

LAYOUT_SINGLE = "single"
LAYOUT_SEPARATE = "separate"  # one file per source
LAYOUT_SIDE_BY_SIDE = "side_by_side"
LAYOUT_STACKED = "stacked"
LAYOUT_PIP = "pip"

LAYOUTS = frozenset(
    {LAYOUT_SINGLE, LAYOUT_SEPARATE, LAYOUT_SIDE_BY_SIDE, LAYOUT_STACKED, LAYOUT_PIP}
)


# ------------------------------------------------------------------- geometry


def even(value: int) -> int:
    """Round down to an even number; H.264 in yuv420p cannot encode odd dimensions."""
    return value - (value % 2)


def rotation_filter(rotation: int) -> str:
    """The filter that puts a rotated output the right way up.

    Verified by eye against the user's portrait screen (DXGI rotation 2): ``transpose=1``
    produces an upright image. The other two follow from the same convention.
    """
    if rotation == 2:  # DXGI_MODE_ROTATION_ROTATE90
        return "transpose=1"
    if rotation == 4:  # ROTATE270
        return "transpose=2"
    if rotation == 3:  # ROTATE180
        return "transpose=1,transpose=1"
    return ""


def surface_crop(
    monitor: Monitor, x: int, y: int, width: int, height: int
) -> tuple[int, int, int, int]:
    """Map a desktop-space rectangle onto the duplicated surface ddagrab actually sees.

    On an unrotated screen this is only a translation. On a rotated one the surface axes
    are the panel's, not the desktop's, so the rectangle has to be turned with it - and
    ddagrab's ``offset_x``/``offset_y``/``video_size`` are in surface space.

    Returns (offset_x, offset_y, width, height) for ddagrab, already clamped and even.
    """
    # Intersect the selection with the screen; do not slide it inwards. An earlier version
    # clamped the origin to the monitor and kept the requested size, so a rectangle dragged
    # from off the left edge recorded a region of the full size further to the right - the
    # wrong part of the screen, at the right size, with nothing said about it.
    left = max(monitor.x, x)
    top = max(monitor.y, y)
    right = min(monitor.x + monitor.width, x + width)
    bottom = min(monitor.y + monitor.height, y + height)

    local_x = left - monitor.x
    local_y = top - monitor.y
    local_w = right - left
    local_h = bottom - top

    # A selection that misses this screen entirely has no sensible answer. The caller
    # chooses the monitor from the rectangle, so this means the two disagree.
    if local_w <= 0 or local_h <= 0:
        raise ValueError(
            f"The rectangle {x},{y} {width}x{height} does not overlap monitor "
            f"{monitor.index} ({monitor.x},{monitor.y} {monitor.width}x{monitor.height})."
        )

    # ddagrab will not take a zero or odd dimension, so the minimum is two pixels; where
    # fewer are left, the origin moves back rather than the size reaching off the surface.
    if local_w < 2:
        local_x = max(0, min(local_x, monitor.width - 2))
        local_w = 2
    if local_h < 2:
        local_y = max(0, min(local_y, monitor.height - 2))
        local_h = 2

    if monitor.rotation == 2:  # desktop is the surface turned 90 deg clockwise
        s_x = local_y
        s_y = monitor.width - (local_x + local_w)
        s_w, s_h = local_h, local_w
    elif monitor.rotation == 4:
        s_x = monitor.height - (local_y + local_h)
        s_y = local_x
        s_w, s_h = local_h, local_w
    elif monitor.rotation == 3:
        s_x = monitor.width - (local_x + local_w)
        s_y = monitor.height - (local_y + local_h)
        s_w, s_h = local_w, local_h
    else:
        s_x, s_y = local_x, local_y
        s_w, s_h = local_w, local_h

    return max(0, s_x), max(0, s_y), even(s_w), even(s_h)


@dataclass(frozen=True)
class VideoSource:
    """One ddagrab input: a whole screen, or a rectangle on one screen."""

    monitor: Monitor
    crop: tuple[int, int, int, int] | None = None  # desktop space
    name: str = ""

    @property
    def needs_cpu(self) -> bool:
        """A rotated screen cannot stay on the GPU, because transpose is a CPU filter."""
        return self.monitor.rotated

    def ddagrab_args(self, framerate: int, draw_mouse: bool) -> str:
        parts = [
            f"output_idx={self.monitor.index}",
            f"framerate={framerate}",
            f"draw_mouse={1 if draw_mouse else 0}",
        ]
        if self.crop is not None:
            offset_x, offset_y, width, height = surface_crop(self.monitor, *self.crop)
            parts.append(f"offset_x={offset_x}")
            parts.append(f"offset_y={offset_y}")
            parts.append(f"video_size={width}x{height}")
        return "ddagrab=" + ":".join(parts)

    def output_size(self) -> tuple[int, int]:
        """What the encoded frame will measure, after any rotation."""
        if self.crop is not None:
            _, _, width, height = surface_crop(self.monitor, *self.crop)
            if self.monitor.rotated:
                width, height = height, width
            return even(width), even(height)
        if self.monitor.rotated:
            return even(self.monitor.width), even(self.monitor.height)
        return even(self.monitor.width), even(self.monitor.height)


# -------------------------------------------------------------------- the plan


@dataclass
class RecordingPlan:
    """Everything the command builder needs, already resolved to concrete values."""

    sources: list[VideoSource]
    outputs: list[Path]
    encoder: str = "hevc_nvenc"
    pix_fmt: str = "yuv420p"
    encoder_extra: tuple[str, ...] = ()
    quality: int = 23
    #: The named quality level this `quality` number came from - "balanced" and so on.
    #: The number is what ffmpeg is told; the name is what a measurement is filed under,
    #: because `-cq 23` and `-crf 23` and `-quality 68` are the same level and different
    #: numbers. Empty means "not said", and then nothing is measured.
    quality_level: str = ""
    framerate: int = 60
    #: Record no taller than this, in pixels; 0 keeps every source at its own size.
    #: Only ever shrinks - see `scale_for`.
    max_height: int = 0
    draw_mouse: bool = True
    layout: str = LAYOUT_SINGLE
    mic_device: str = ""
    loopback_pipe: str = ""
    audio_mode: str = config_mod.AUDIO_MIX
    audio_offset_ms: int = 0
    #: Written as Matroska with packets flushed, because a plain MP4 whose process is
    #: killed leaves a file nothing can open - measured. The finished recording is
    #: remuxed to MP4 by `takes.finish`, which costs 0.03 s and re-encodes nothing.
    crash_safe: bool = True

    def container_args(self) -> list[str]:
        """Muxer options for the segment, chosen by the container it is written to."""
        if not self.crash_safe:
            return []
        suffix = self.outputs[0].suffix.lower() if self.outputs else ""
        if suffix == ".mkv":
            return list(takes_mod.SEGMENT_FLAGS)
        if suffix == ".mp4":
            # Not the container this is meant to use, but if a caller insists on MP4 a
            # fragmented one at least survives a kill. It costs size; MKV does not.
            return ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"]
        return []

    @property
    def has_mic(self) -> bool:
        return bool(self.mic_device)

    @property
    def has_system_audio(self) -> bool:
        return bool(self.loopback_pipe)

    @property
    def has_audio(self) -> bool:
        return self.has_mic or self.has_system_audio


def _video_codec_args(plan: RecordingPlan) -> list[str]:
    """What to say to the encoder this plan names. See `encoders.codec_args`.

    This used to recognise exactly `h264_nvenc` and `hevc_nvenc` and fall through to
    libx264 arguments for everything else - which meant that on an AMD machine, an Intel
    machine, or any NVIDIA card new enough for `av1_nvenc` to pass the probe, the
    recorder wrote `-c:v libx264` while `_filter_for` left the frames on the GPU, and
    ffmpeg died on "Impossible to convert between the formats". Eight of the eleven probe
    candidates built a command that could not run. Now an unwritten name raises.
    """
    return encoders_mod.codec_args(
        plan.encoder,
        quality=plan.quality,
        framerate=plan.framerate,
        pix_fmt=plan.pix_fmt,
        extra=plan.encoder_extra,
    )


def _filter_for(
    source: VideoSource,
    encoder: str,
    pix_fmt: str = "yuv420p",
    force_download: bool = False,
    max_height: int = 0,
) -> str:
    """The per-source filter chain, or '' to stay on the direct GPU path.

    4:4:4 always costs a trip through system memory. Measured on this build: asking for
    ``-pix_fmt yuv444p`` straight off ddagrab fails with "Impossible to convert between
    the formats supported by the filter 'Parsed_null_0' and the filter 'auto_scale_0'" -
    there is no hardware conversion from the d3d11 surface to a 4:4:4 plane, so ffmpeg
    inserts a scaler it cannot build. The download path does hold full resolution all the
    same: 2560x1440 at 60 fps measured 58.9 fps through it.
    """
    return encoders_mod.frame_path(
        encoder,
        rotate=rotation_filter(source.monitor.rotation),
        pix_fmt=pix_fmt,
        force_download=force_download,
        scale_to=scale_for(source, max_height),
    )


def scale_for(source: VideoSource, max_height: int) -> int:
    """The height to scale this source to, or 0 to leave it alone.

    **Downscale only, and only when it changes something.** A source already at or
    below the wanted height is left on whatever path it was on - asking for 1080 on a
    1080p monitor must not drag the recording through system memory to divide by one.
    A source taller than the limit comes down to it; the width follows.
    """
    if max_height <= 0:
        return 0
    height = source.output_size()[1]
    if height <= max_height:
        return 0
    return int(max_height)


def build_command(
    ffmpeg: str, plan: RecordingPlan, extra_global: list[str] | None = None
) -> list[str]:
    """The full argument list. Pure - no side effects, so it can be unit tested.

    `extra_global` goes with the other global options, before the first input. ffmpeg is
    position-sensitive and an option that lands after the output file either applies to
    the wrong thing or is rejected outright.
    """
    if not plan.sources:
        raise ValueError("A recording needs at least one video source.")

    # Refuse a layout we do not know rather than quietly building a command for one source
    # and discarding the rest. A mis-cased constant used to produce a clean command that
    # captured both screens and wrote only the first, with nothing logged.
    if plan.layout not in LAYOUTS:
        raise ValueError(
            f"Unknown layout {plan.layout!r}. Known layouts: {', '.join(sorted(LAYOUTS))}."
        )
    if plan.layout == LAYOUT_SINGLE and len(plan.sources) != 1:
        raise ValueError(
            f"Layout 'single' records one source; {len(plan.sources)} were given. Use "
            f"'separate' for a file per screen, or one of the composited layouts."
        )
    if plan.layout == LAYOUT_PIP and len(plan.sources) != 2:
        raise ValueError(
            f"Picture in picture needs exactly two sources; {len(plan.sources)} were given."
        )
    if plan.layout in (LAYOUT_SIDE_BY_SIDE, LAYOUT_STACKED) and len(plan.sources) < 2:
        raise ValueError(f"Layout {plan.layout!r} needs at least two sources.")

    if plan.layout == LAYOUT_SEPARATE:
        if len(plan.outputs) != len(plan.sources):
            raise ValueError("Layout 'separate' needs one output file per source.")
    elif len(plan.outputs) != 1:
        raise ValueError(f"Layout {plan.layout!r} needs exactly one output file.")

    # Note the absence of -nostdin: stdin is how the recording is stopped cleanly.
    args: list[str] = [ffmpeg, "-hide_banner", "-loglevel", "warning"]
    if extra_global:
        args += list(extra_global)

    for source in plan.sources:
        args += ["-f", "lavfi", "-i", source.ddagrab_args(plan.framerate, plan.draw_mouse)]

    audio_inputs: list[int] = []
    index = len(plan.sources)
    # -itsoffset shifts an input's timestamps and must come before that input.
    offset: list[str] = []
    if plan.audio_offset_ms:
        offset = ["-itsoffset", f"{plan.audio_offset_ms / 1000:.4f}"]

    if plan.has_mic:
        args += offset + [
            "-f", "dshow",
            "-audio_buffer_size", "50",
            "-i", f"audio={plan.mic_device}",
        ]
        audio_inputs.append(index)
        index += 1
    if plan.has_system_audio:
        args += offset + [
            "-f", "s16le",
            "-ar", str(audio_mod.SAMPLE_RATE),
            "-ac", str(audio_mod.CHANNELS),
            "-i", plan.loopback_pipe,
        ]
        audio_inputs.append(index)
        index += 1

    filter_parts: list[str] = []
    video_maps: list[str] = []

    for i, source in enumerate(plan.sources):
        chain = _filter_for(
            source, plan.encoder, plan.pix_fmt, max_height=plan.max_height
        )
        if chain:
            filter_parts.append(f"[{i}:v]{chain}[v{i}]")
            video_maps.append(f"[v{i}]")
        else:
            video_maps.append(f"{i}:v")

    if plan.layout in (LAYOUT_SIDE_BY_SIDE, LAYOUT_STACKED, LAYOUT_PIP) and len(plan.sources) > 1:
        # Composition is a CPU filter, so every source must come down off the GPU first.
        prepared: list[str] = []
        for i, source in enumerate(plan.sources):
            label = f"c{i}"
            chain = _filter_for(
                source,
                plan.encoder,
                plan.pix_fmt,
                force_download=True,
                max_height=plan.max_height,
            )
            filter_parts = [p for p in filter_parts if not p.startswith(f"[{i}:v]")]
            filter_parts.append(f"[{i}:v]{chain}[{label}]")
            prepared.append(f"[{label}]")
        if plan.layout == LAYOUT_SIDE_BY_SIDE:
            height = min(s.output_size()[1] for s in plan.sources)
            scaled = []
            for i, label in enumerate(prepared):
                scaled.append(f"s{i}")
                filter_parts.append(f"{label}scale=-2:{even(height)}[s{i}]")
            filter_parts.append(
                "".join(f"[{name}]" for name in scaled)
                + f"hstack=inputs={len(scaled)}[vout]"
            )
        elif plan.layout == LAYOUT_STACKED:
            width = min(s.output_size()[0] for s in plan.sources)
            scaled = []
            for i, label in enumerate(prepared):
                scaled.append(f"s{i}")
                filter_parts.append(f"{label}scale={even(width)}:-2[s{i}]")
            filter_parts.append(
                "".join(f"[{name}]" for name in scaled)
                + f"vstack=inputs={len(scaled)}[vout]"
            )
        else:  # picture in picture: the first source is the backdrop
            base_w = plan.sources[0].output_size()[0]
            filter_parts.append(f"{prepared[1]}scale={even(base_w // 4)}:-2[pip]")
            filter_parts.append(f"{prepared[0]}[pip]overlay=W-w-24:H-h-24[vout]")
        video_maps = ["[vout]"]

    audio_labels: list[str] = []
    if len(audio_inputs) == 2 and plan.audio_mode == config_mod.AUDIO_MIX:
        # normalize=0 matters: amix otherwise divides every input by the input count and
        # the recording comes out at half volume.
        mix = (
            f"[{audio_inputs[0]}:a][{audio_inputs[1]}:a]"
            "amix=inputs=2:duration=longest:normalize=0,"
            "aresample=async=1:first_pts=0"
        )
        # A filter output label may be consumed once. Mapping one [aout] into two files
        # makes ffmpeg refuse the whole command ("does not exist in any defined filter
        # graph, or was already used elsewhere"), so a file-per-screen recording needs
        # the mix split as many ways as there are files.
        wanted = len(plan.outputs) if plan.layout == LAYOUT_SEPARATE else 1
        if wanted == 1:
            filter_parts.append(mix + "[aout]")
            audio_labels = ["[aout]"]
        else:
            audio_labels = [f"[aout{i}]" for i in range(wanted)]
            filter_parts.append(mix + f",asplit={wanted}" + "".join(audio_labels))

    if filter_parts:
        args += ["-filter_complex", ";".join(filter_parts)]

    if plan.layout == LAYOUT_SEPARATE:
        for i, output in enumerate(plan.outputs):
            args += ["-map", video_maps[i]]
            args += _audio_maps(audio_labels[i] if audio_labels else None, audio_inputs)
            args += _video_codec_args(plan)
            args += _audio_codec_args(plan, audio_inputs)
            args += plan.container_args()
            args += ["-y", str(output)]
    else:
        args += ["-map", video_maps[0]]
        args += _audio_maps(audio_labels[0] if audio_labels else None, audio_inputs)
        args += _video_codec_args(plan)
        args += _audio_codec_args(plan, audio_inputs)
        args += plan.container_args()
        args += ["-y", str(plan.outputs[0])]

    return args


def _audio_maps(audio_map: str | None, audio_inputs: list[int]) -> list[str]:
    """A filter label when the audio was mixed, otherwise the raw input streams.

    Raw input streams may be mapped into several outputs; a filter label may not, which
    is what `asplit` above is for.
    """
    if audio_map:
        return ["-map", audio_map]
    return [arg for index in audio_inputs for arg in ("-map", f"{index}:a")]


def _audio_codec_args(plan: RecordingPlan, audio_inputs: list[int]) -> list[str]:
    if not audio_inputs:
        return []
    args = ["-c:a", "aac", "-b:a", "192k", "-ar", str(audio_mod.SAMPLE_RATE)]
    if len(audio_inputs) == 2 and plan.audio_mode == config_mod.AUDIO_SEPARATE:
        # Name the tracks so a player's track menu is readable.
        args += ["-metadata:s:a:0", "title=Microphone", "-metadata:s:a:1", "title=System"]
    return args


# ------------------------------------------------------------------ the runner


@dataclass
class Progress:
    frames: int = 0
    fps: float = 0.0
    seconds: float = 0.0
    size_bytes: int = 0
    dropped: int = 0


class Recorder:
    """Owns one ffmpeg process and, when system sound is on, one loopback feeder.

    State is readable from any thread; callbacks fire on the reader threads, so a Qt UI
    must marshal them (the window does this with a signal).
    """

    def __init__(self, ffmpeg: str) -> None:
        self.ffmpeg = ffmpeg
        self.process: subprocess.Popen | None = None
        self.feeder: audio_mod.LoopbackFeeder | None = None
        self.plan: RecordingPlan | None = None
        self.progress = Progress()
        self.started_at: float = 0.0
        self.log: deque[str] = deque(maxlen=200)
        self.error: str = ""
        self.callback_error: str = ""
        #: Why system sound is not in this recording, when it was asked for and could
        #: not be had. "" when it is there, or was never asked for. A sentence for the
        #: user, not an errno: see `start`.
        self.dropped_audio: str = ""
        #: The `audio.REASON_*` code beside it, so `ui/app.py` can say it in the
        #: user's language - `audio.py` is Qt-free and cannot translate itself.
        self.dropped_reason: str = ""
        self.command: list[str] = []
        self._on_progress = None
        self._threads: list[threading.Thread] = []
        #: When the start grace runs out, as `time.monotonic` counts. 0.0 means there
        #: is nothing left to watch for - `settle` has already answered, or the
        #: process never got as far as being launched.
        self._settle_by: float = 0.0

    # -- state -------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return time.monotonic() - self.started_at

    def log_text(self) -> str:
        return "\n".join(self.log)

    # -- lifecycle ---------------------------------------------------------

    def start(
        self, plan: RecordingPlan, on_progress=None, loopback_device: int = -1,
        wait: bool = True,
    ) -> str:
        """Begin recording. Returns an empty string, or the reason it could not start.

        `wait` is whether to sit out the start grace here. With it, this method answers
        for the recording: a process that dies of its own arguments has died by the
        time it returns, and its exit code and log come back as the reason. Without it,
        the answer is deferred to `settle`, and until then "" means only that ffmpeg
        was launched.

        A caller that starts **several** recorders for one recording must pass False
        and call `settle` on each afterwards, because the wait is paid per call and in
        a loop it lands the captures a grace window apart - see `START_GRACE`.
        """
        if self.running:
            return _("A recording is already running.")

        self.plan = plan
        self.progress = Progress()
        self.log.clear()
        self.error = ""
        self.callback_error = ""
        self.dropped_audio = ""
        self.dropped_reason = ""
        self._on_progress = on_progress
        self._threads = []
        self._settle_by = 0.0

        for output in plan.outputs:
            try:
                output.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return _("Output folder could not be created: {reason}").format(
                    reason=exc
                )

        if plan.has_system_audio:
            # The feeder must own the pipe before ffmpeg tries to open it, and it drops
            # audio until ffmpeg connects so the first sample lines up with t=0.
            self.feeder = audio_mod.LoopbackFeeder(plan.loopback_pipe, loopback_device)
            self.feeder.start()
            time.sleep(0.15)
            if self.feeder.error:
                # **System sound is not worth a recording.** This used to `return` the
                # feeder's error, and the whole take never happened: on 2026-11-13 the
                # user pressed record, watched the countdown, and got
                # `No system-sound device available: [Errno -9996] Invalid device info`
                # and no file - though the screen and the microphone were both ready
                # and would have recorded perfectly. One optional track must never take
                # the other two down with it. So the pipe is dropped from the plan, the
                # recording goes ahead without system sound, and the reason is carried
                # in `dropped_audio` for the bar to say at the start and again at the end.
                self.dropped_audio = self.feeder.error
                self.dropped_reason = self.feeder.reason
                self.feeder.stop()
                self.feeder = None
                plan = dataclasses.replace(plan, loopback_pipe="")
                self.plan = plan

        try:
            self.command = build_command(
                self.ffmpeg, plan, extra_global=["-progress", "pipe:1", "-nostats"]
            )
        except (ValueError, encoders_mod.UnknownEncoder) as exc:
            # `build_command` refuses an unknown layout and, since the dispatch, an
            # encoder nobody wrote arguments for. Both are programming errors rather
            # than conditions, but they arrive here *after* the loopback feeder has
            # taken the named pipe - and this method's contract is to return a sentence,
            # not to raise into a Qt slot and leave a half-started session behind.
            if self.feeder:
                self.feeder.stop()
                self.feeder = None
            return str(exc)
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            if self.feeder:
                self.feeder.stop()
                self.feeder = None
            return f"ffmpeg could not be started: {exc}"

        self.started_at = time.monotonic()
        self._settle_by = self.started_at + START_GRACE
        self._spawn(self._read_progress, "progress")
        self._spawn(self._read_stderr, "ffmpeg-log")
        return self.settle() if wait else ""

    def settle(self) -> str:
        """Wait out what is left of the start grace. '' or why the recording is not one.

        A started process is not a running recording. ffmpeg validates its inputs
        after it is launched, so every way a recording can fail from its own arguments
        - a screen that is not there, a microphone that was unplugged, an output
        folder on a drive that went away - used to be reported as success, and the
        user watched a red dot over nothing until `_tick` noticed 200 ms later.

        Safe to call more than once and on a recorder that was never started: the
        second call has nothing left to watch and answers "". `wait` returns the
        moment the process dies, so a doomed start costs only the time it took to die.
        Measured on this build: `wait=False` and this immediately afterwards costs the
        same as `wait=True` did; the point of the split is that a caller may launch
        every recorder first and then settle them, which it cannot do in one call.
        """
        if self.process is None or not self._settle_by:
            return ""
        remaining = max(0.0, self._settle_by - time.monotonic())
        self._settle_by = 0.0
        try:
            self.process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            return ""  # still alive after the window: it really is recording
        self._finish()  # joins the log reader, so the tail below is complete
        return self._exit_problem() or _(
            "ffmpeg exited with code {code}."
        ).format(code=self.process.returncode)

    def stop(self, timeout: float = 12.0) -> str:
        """Ask ffmpeg to finish and write the MP4 trailer.

        Sending ``q`` is what closes the container; killing the process leaves a file with
        no moov atom that nothing will play. Only if ``q`` is ignored do we escalate, and
        then the caller is told the file may be damaged.
        """
        if self.process is None:
            return ""
        # A stop answers for this process from here on; an unsettled start grace has
        # nothing left to say that this will not say better.
        self._settle_by = 0.0
        proc = self.process
        if proc.poll() is not None:
            # Already gone, which on this branch means it died by itself rather than
            # being asked to stop. It used to return '' here - the exit code and the
            # whole log thrown away, and the caller told the recording was saved.
            self._finish()
            return self._exit_problem()

        asked = ""
        try:
            if proc.stdin:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            else:
                asked = "ffmpeg was started without a stdin to ask it to stop"
        except OSError as exc:
            # Not fatal by itself - ffmpeg may be exiting already - but if a kill ends up
            # being needed, this is why, and the user should be told rather than left
            # with a damaged file and no explanation.
            asked = f"the stop request could not be delivered ({exc})"

        try:
            proc.wait(timeout=timeout)
            problem = ""
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
            problem = _(
                "ffmpeg did not stop on request and had to be killed; the file may "
                "be unplayable."
            )
            if asked:
                problem += f" ({asked})"

        self._finish()
        if not problem:
            problem = self._exit_problem()
        return problem

    def _exit_problem(self) -> str:
        """Why this process's exit code is a problem, with the log that explains it.

        '' when it exited cleanly or has not exited. Writing ``q`` to ffmpeg's stdin
        makes it write the trailer and exit 0 - measured, and recorded in
        `docs/CONTRACT.md` - so any other code on a stop is a recording that did not
        end the way it was asked to, and the log is the only place that says why.
        """
        proc = self.process
        if proc is None or proc.returncode in (0, None):
            return ""
        tail = "\n".join(list(self.log)[-6:])
        return (
            _("ffmpeg exited with code {code}.").format(code=proc.returncode)
            + f"\n{tail}"
        ).strip()

    def _finish(self) -> None:
        if self.feeder is not None:
            self.feeder.stop()
        for thread in self._threads:
            thread.join(timeout=1.0)
        self.started_at = 0.0
        # Nothing left to watch for: a `settle` after this would only wait on a
        # process that has already been dealt with.
        self._settle_by = 0.0

    # -- internals ---------------------------------------------------------

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _read_progress(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            try:
                if key == "frame":
                    self.progress.frames = int(value)
                elif key == "fps":
                    self.progress.fps = float(value)
                elif key == "out_time_us":
                    self.progress.seconds = int(value) / 1_000_000
                elif key == "total_size":
                    self.progress.size_bytes = int(value)
                elif key == "drop_frames":
                    self.progress.dropped = int(value)
            except ValueError:
                continue
            if key == "progress" or key == "out_time_us":
                callback = self._on_progress
                if callback is not None:
                    try:
                        callback(self.progress)
                    except Exception as exc:
                        # The callback belongs to the caller, usually a Qt signal. Letting
                        # it raise here would kill the reader thread and freeze the
                        # elapsed-time display, so it is recorded once and reading goes
                        # on; a UI bug then shows up in the log instead of vanishing.
                        if not self.callback_error:
                            self.callback_error = f"progress callback failed: {exc}"
                            self.log.append(self.callback_error)

    def _read_stderr(self) -> None:
        proc = self.process
        if proc is None or proc.stderr is None:
            return
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.log.append(line)


# ---------------------------------------------------------------- plan helpers


def output_path(settings: config_mod.Settings, when: datetime, suffix: str = "") -> Path:
    """Build the file name from the pattern, keeping it safe for Windows."""
    try:
        stem = when.strftime(settings.filename_pattern)
    except ValueError:
        stem = when.strftime("ScreenRec_%Y-%m-%d_%H-%M-%S")
    for bad in '<>:"/\\|?*':
        stem = stem.replace(bad, "-")
    if suffix:
        stem = f"{stem}_{suffix}"
    return settings.resolved_output_dir() / f"{stem}.mp4"


def pipe_name(tag: str = "") -> str:
    """A pipe name unique to this process, so two copies of the app do not collide."""
    import os

    return rf"\\.\pipe\screenrec_{os.getpid()}{('_' + tag) if tag else ''}"


def _describe_shape(shape: tuple) -> str:
    """One readable clause for what `RecordingSession._shape_of` returned.

    The user reads this inside the refusal in `_begin_segment`, so the clause is
    translated and only the encoder name, the frame size and the pixel format - words
    ffmpeg chose, the same in both languages - go through a placeholder. The track
    count has its own sentence per value rather than a plural rule, because the two
    that `_shape_of` can produce are "one" and "two".
    """
    encoder, pix_fmt, width, height, tracks = shape
    said = f"{encoder} {width}x{height} {pix_fmt}"
    if tracks == 1:
        return _("{video} with one sound track").format(video=said)
    if tracks:
        return _("{video} with {count} sound tracks").format(
            video=said, count=tracks
        )
    return _("{video} with no sound").format(video=said)


# ---------------------------------------------------------------- the session


class RecordingSession:
    """One recording as the user means it: start, pause, resume, stop - one file.

    A `Recorder` owns one ffmpeg process, which is the right unit for ffmpeg and the
    wrong one for a person: pressing pause and carrying on is still *one recording*.
    This is that unit. Each run of the capture is a segment written to its own crash-safe
    Matroska file, and `stop` joins them with ``-c copy`` into the MP4 that was asked
    for - no re-encoding, measured at 0.03 s.

    `plan_for(segment_path)` is supplied by the caller and must return a fresh
    `RecordingPlan` writing to that path. Rebuilding it per segment is deliberate: a
    window that moved, or a device that was unplugged during the pause, is picked up on
    resume instead of failing silently.

    `start` and `resume` take `wait`, which they pass to `Recorder.start`. One
    recording of one screen leaves it alone: the press of the button and the answer
    about it belong together. A caller recording several screens as one take must
    pass False to every session and then call `settle` on every session, or each
    capture begins a start grace after the one before it - see `START_GRACE`.
    """

    def __init__(self, ffmpeg: str, final: Path, plan_for) -> None:
        self.ffmpeg = ffmpeg
        self.take = takes_mod.Take(final=final)
        self.plan_for = plan_for
        self.recorder: Recorder | None = None
        self.error: str = ""
        self._banked_seconds = 0.0
        self._banked_bytes = 0
        self._on_progress = None
        self._loopback_device = -1
        self._paused = False
        #: The first segment's shape - see `_shape_of`. The take's contract: `None`
        #: until the first segment is planned, and never replaced after that.
        self._shape: tuple | None = None
        #: What `_end_segment` found worth telling the user. `ui/app.py` reads it
        #: when the recording stops.
        self.feeder_note: str = ""
        #: The `audio.REASON_*` code behind `feeder_note`, when there is one.
        self.feeder_reason: str = ""
        #: The segment a `wait=False` start launched and `settle` has still to answer
        #: for, with whether the take was paused before it: a start that turns out to
        #: have failed must leave the session exactly where it found it.
        self._settling: tuple[Path, bool] | None = None

    # -- state -------------------------------------------------------------

    @property
    def recording(self) -> bool:
        return self.recorder is not None and self.recorder.running

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def active(self) -> bool:
        """Started and not yet stopped, whether or not it is paused right now."""
        return self.recording or self._paused

    @property
    def dropped_audio(self) -> str:
        """Why the running segment has no system sound, though it was asked for.

        Read by the bar the moment a recording starts, so that a take missing a track
        says so while it can still be restarted - rather than at the end, when the
        thing that was being recorded is over.
        """
        return self.recorder.dropped_audio if self.recorder is not None else ""

    @property
    def dropped_reason(self) -> str:
        """The `audio.REASON_*` code behind `dropped_audio`, for translation."""
        return self.recorder.dropped_reason if self.recorder is not None else ""

    @property
    def segments(self) -> int:
        return len(self.take.segments)

    @property
    def progress(self) -> Progress:
        """The whole take, not the current segment.

        A clock that restarted at zero on every resume would be worse than no clock.
        """
        total = Progress()
        total.seconds = self._banked_seconds
        total.size_bytes = self._banked_bytes
        if self.recorder is not None:
            current = self.recorder.progress
            total.seconds += current.seconds
            total.size_bytes += current.size_bytes
            total.frames = current.frames
            total.fps = current.fps
            total.dropped = current.dropped
        return total

    def log_text(self) -> str:
        return self.recorder.log_text() if self.recorder is not None else ""

    # -- lifecycle ---------------------------------------------------------

    def start(
        self, on_progress=None, loopback_device: int = -1, wait: bool = True
    ) -> str:
        if self.active:
            return "That recording is already running."
        self._on_progress = on_progress
        self._loopback_device = loopback_device
        return self._begin_segment(wait=wait)

    def settle(self) -> str:
        """Answer for a start or resume that was launched with `wait=False`.

        '' when the capture is running, or why it is not - and then the session is put
        back exactly where the launch found it: no recorder, the segment it would have
        written dropped from the take, and the pause flag as it was. A session with
        nothing outstanding answers "".
        """
        recorder = self.recorder
        settling = self._settling
        self._settling = None
        if recorder is None or settling is None:
            return ""
        problem = recorder.settle()
        if not problem:
            return ""
        segment, was_paused = settling
        self.recorder = None
        self.error = problem
        self._paused = was_paused
        if self.take.segments and self.take.segments[-1] == segment:
            self.take.segments.pop()
        # Whatever a process that died inside the start grace left behind is not a
        # segment: it never reached its encode loop. Measured on this build, twice
        # each: ddagrab on a DXGI output that does not exist, and a dshow microphone
        # that is not there, both leave **no file at all** - ffmpeg dies before it
        # opens the output. An empty one is cleared anyway: `takes.orphans` skips it,
        # but `Take.next_segment` skips a name that exists, so it would sit beside the
        # finished recording for good and push the next segment's number along.
        try:
            if segment.is_file() and segment.stat().st_size == 0:
                segment.unlink()
        except OSError:
            pass  # a file we could not remove is not a reason to fail the start
        return problem

    def pause(self) -> str:
        """Finish the current segment and keep it. Returns '' or the reason it failed."""
        if not self.recording:
            return _("Nothing is recording.")
        problem = self._end_segment()
        self._paused = True
        return problem

    def resume(self, wait: bool = True) -> str:
        if self.recording:
            return "That recording is already running."
        if not self._paused:
            return _("That recording was not paused.")
        problem = self._begin_segment(wait=wait)
        if problem:
            # Still paused, because nothing resumed. Clearing the flag first made a
            # failed resume leave the take neither recording nor paused: `active`
            # went False with the segments still on disk, so the next press of the
            # record hotkey built a *new* session and the take was orphaned - 282 kB
            # of recording with no way back to it from the interface.
            return problem
        self._paused = False
        return ""  # a `wait=False` resume is only provisional until `settle`

    def stop(self) -> tuple[Path | None, str]:
        """End the take and assemble the file. Returns (path, problem)."""
        problem = ""
        self._settling = None
        if self.recorder is not None:
            # Not `if self.recording`: an ffmpeg that died on its own leaves a
            # recorder that is not running, and skipping it here left its feeder
            # running, its threads unjoined and its exit code unread, so the take
            # was assembled and reported as saved without a word about why it is
            # short.
            problem = self._end_segment()
        self._paused = False
        # What the recorder's own clock made the take, so that `takes.measure` can
        # refuse to file a cost for a file that is not the recording it counted. It is
        # filled here rather than in `_describe` because it is not known until the
        # last segment has been banked. `storage.remember_recording` allows the larger
        # of 0.5 s and 5%, which is room enough: measured twice on this build, a real
        # 5 s capture of monitor 0 with a pause in the middle counted 4.983 s and
        # 5.017 s here against 5.084 s and 5.117 s in the finished file - 0.10 s out,
        # and the cost was filed both times.
        self.take.seconds = self._banked_seconds
        path, join_problem = takes_mod.finish(self.ffmpeg, self.take)
        if join_problem:
            problem = f"{problem}  {join_problem}".strip()
        return path, problem

    def discard(self) -> str:
        """Throw the whole take away, segments and all."""
        self._settling = None
        if self.recorder is not None:
            self._end_segment()
        self._paused = False
        removed, failed = 0, []
        for segment in self.take.segments:
            try:
                if segment.is_file():
                    segment.unlink()
                    removed += 1
            except OSError as exc:
                failed.append(f"{segment.name}: {exc}")
        self.take.segments.clear()
        if failed:
            return _(
                "Some of the recording could not be deleted - {details}"
            ).format(details="; ".join(failed))
        return ""

    # -- internals ---------------------------------------------------------

    def _begin_segment(self, wait: bool = True) -> str:
        was_paused = self._paused
        segment = self.take.next_segment()
        try:
            plan = self.plan_for(segment)
        except Exception as exc:
            self.error = _("The recording could not be set up: {reason}").format(
                reason=exc
            )
            return self.error
        if plan is None:
            self.error = _("The recording could not be set up.")
            return self.error

        # What is about to write this take, so that `takes.finish` can file what it
        # really cost. Taken from the first segment's plan rather than from the
        # constructor, because the plan is rebuilt per segment and the constructor is
        # given none of this. A resumed take keeps the first segment's answer: a
        # recording whose encoder changed half way through is not a measurement of
        # either encoder, and `_describe` therefore refuses to overwrite.
        self._describe(plan)

        shape = self._shape_of(plan)
        first = self._shape is None
        if first:
            self._shape = shape
        elif shape != self._shape:
            # A take is one file and `takes.finish` joins it with `-c copy`, which
            # describes the whole track by the first segment's header. A segment that
            # does not match it is not a recording that can be joined - see
            # `takes.shape_problem` for what each mismatch measured. Refusing here
            # costs the user nothing: the take stays paused with everything recorded
            # so far intact, and Stop still writes a file that plays.
            #
            # The whole sentence is the key, placeholders and all. It used to be the
            # translated frame "The recording could not be set up: {reason}" with this
            # written into `{reason}` in English, so a Russian user read a Russian
            # clause followed by the only advice in the message in English. The frame's
            # other two callers interpolate an exception, which is machine text in any
            # language; this is prose and belongs in the catalogue.
            self.error = _(
                "This recording is {current} and cannot continue as {wanted}. Stop it "
                "to save what has been recorded, then start a new one with the new "
                "settings."
            ).format(
                current=_describe_shape(self._shape), wanted=_describe_shape(shape)
            )
            return self.error

        self.recorder = Recorder(self.ffmpeg)
        problem = self.recorder.start(
            plan, on_progress=self._on_progress,
            loopback_device=self._loopback_device, wait=wait,
        )
        if problem:
            self.recorder = None
            self.error = problem
            return problem
        if first and self.recorder.plan is not None:
            # The take's contract is what was *recorded*, not what was planned. The
            # recorder may drop system sound when its device will not open (see
            # `Recorder.start`), and a first segment written without it followed by a
            # resumed one written with it is two different files that `-c copy` cannot
            # join. Re-fixing the shape here makes that mismatch refuse itself, with
            # the sentence `_describe_shape` already writes.
            self._shape = self._shape_of(self.recorder.plan)
        self.take.add(segment)
        if not wait:
            self._settling = (segment, was_paused)
        return ""

    @staticmethod
    def _shape_of(plan: RecordingPlan) -> tuple:
        """What this plan will write, to the detail a `-c copy` join cares about.

        Encoder, pixel format, frame size and the number of audio tracks - the four
        things `takes.shape_problem` measured to break a joined take. The frame rate
        is left out for the reason given there: a 30 fps segment followed by a 60 fps
        one joined with 138 frames and no decode errors at all.

        The track count, rather than the device names: a different microphone is
        still one track and joins perfectly well, while turning the system sound on
        during a pause turns one track into two and does not.
        """
        width, height = plan.sources[0].output_size() if plan.sources else (0, 0)
        if plan.has_mic and plan.has_system_audio:
            tracks = 2 if plan.audio_mode == config_mod.AUDIO_SEPARATE else 1
        else:
            tracks = 1 if plan.has_audio else 0
        return (plan.encoder, plan.pix_fmt, width, height, tracks)

    def _describe(self, plan: RecordingPlan) -> None:
        """Fill the take's measurement fields from the plan, once per take."""
        if self.take.encoder:
            return
        width, height = plan.sources[0].output_size() if plan.sources else (0, 0)
        self.take.encoder = plan.encoder
        self.take.quality = plan.quality_level
        self.take.pix_fmt = plan.pix_fmt
        self.take.width = width
        self.take.height = height
        self.take.framerate = plan.framerate

    def _end_segment(self) -> str:
        recorder = self.recorder
        self.recorder = None
        if recorder is None:
            return ""
        problem = recorder.stop()
        banked = recorder.progress
        self._banked_seconds += banked.seconds
        segment = self.take.segments[-1] if self.take.segments else None
        if segment is not None and segment.is_file():
            self._banked_bytes += segment.stat().st_size
        else:
            self._banked_bytes += banked.size_bytes
        self.feeder_note = self._feeder_note(recorder)
        self.feeder_reason = self._feeder_reason(recorder)
        return problem

    @staticmethod
    def _feeder_reason(recorder: Recorder) -> str:
        """The `audio.REASON_*` code for whatever `_feeder_note` just said.

        The same order as `_feeder_note`, and it has to stay the same order: a note
        and a reason that disagree would show the user a translated sentence about a
        different failure from the one that happened.
        """
        if recorder.dropped_audio:
            return recorder.dropped_reason
        feeder = recorder.feeder
        if feeder is None:
            return ""
        if feeder.error:
            return feeder.reason
        if feeder.fell_back:
            return audio_mod.REASON_FELL_BACK
        return ""

    @staticmethod
    def _feeder_note(recorder: Recorder) -> str:
        if recorder.dropped_audio:
            # The take went ahead without system sound rather than not happening at
            # all; the reason is said once at the start and once here, because a
            # recording that is missing a track should not be a surprise on playback.
            return recorder.dropped_audio
        feeder = recorder.feeder
        if feeder is None:
            return ""
        if feeder.error:
            return feeder.error
        if feeder.fell_back:
            # Sound was recorded, but not from the endpoint the user picked - theirs
            # had gone and the default was taken instead. Said, because the whole
            # point of picking one is knowing which one you got.
            return (
                "The playback device chosen for system sound was not available, so "
                "the current default one was recorded instead."
            )
        if feeder.frames_written == 0:
            return _("System sound was on but no audio arrived.")
        if feeder.audible_frames == 0:
            # The endpoint was alive and carried nothing. On a machine with two
            # playback devices this is what recording the one the sound is *not*
            # going to looks like, and it used to pass as a good recording.
            return _(
                "System sound was recorded, but it was silent from start to finish - "
                "check which playback device is being recorded."
            )
        return ""
