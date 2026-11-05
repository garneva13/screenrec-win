"""Pause, crash safety, and the join that makes both of them one file.

Two things the recorder could not do, built on one mechanism.

**Pause.** ffmpeg has no pause command, and for a long time that was taken here to mean
the product could not pause. It was the wrong conclusion: a paused recording is simply
more than one segment. Pausing stops the capture, resuming starts another, and at the end
the segments are joined with ``-c copy`` - no re-encoding, no quality cost. Measured on
this build: three segments of 3.07 + 3.08 + 2.05 s joined into one 8.20 s file in
**0.03 s**, frame counts adding up exactly.

The price of that join is that the segments have to agree. ``-c copy`` copies whatever
bitstream it is handed into one track described by the *first* segment's header, and the
settings panel is reachable while a recording is paused - so a take whose format changed
half way through used to be written, reported as saved, and decode as garbage from the
join on. `shape_problem` is the guard, and it carries the measurements; `finish` refuses
rather than write that file. The other half of the same mistake is that ffmpeg's default
stream mapping keeps one stream of each kind, which quietly dropped the second audio
track of every two-track recording - hence the ``-map 0`` in `finish`.

**Crash safety.** A plain MP4 keeps its index in a ``moov`` atom written at the very end,
so a recording whose process is killed leaves a file nothing can open. Measured by killing
a 12-second recording:

    plain mp4              2.10 MB on disk   UNREADABLE
    fragmented mp4         3.41 MB           plays, 10.15 s
    plain mkv              1.31 MB           plays, 480 frames
    mkv, -flush_packets 1  1.23 MB           plays, 600 frames   <- best

So every segment is written as **Matroska with packets flushed**, and the finished
recording is remuxed into the MP4 the user asked for with ``-c copy`` - measured at
0.03 s, because nothing is re-encoded. The crash case keeps what reached the disk; the
normal case costs a few hundredths of a second.

One consequence worth knowing: a killed MKV plays but carries **no duration header**. The
remux recomputes it, which is why `recover` exists - an orphaned segment from a crashed
session is still worth something.

**And one measurement falls out of the join for free.** A finished take is the only
moment at which this program knows what a minute of recording really costs *on this
machine* - the file is on disk and its duration is in it. `config.PROFILES`' figures
were measured on an RTX 3070 Ti and on x264; a machine recording through AMF, QSV or
Media Foundation is being quoted somebody else's number, and `docs/ENCODERS.md` section
3 measured that gap at 4.9x on a busy desktop. So `finish` hands the finished file to
`storage.remember_recording`. A take that does not say which encoder wrote it is simply
not measured, and nothing about the join changes either way.

**And on this build every real recording is that take.** It was not until 2026-11-05.
`recorder.RecordingSession` used to build its `Take` from the output path alone, so
`measure` answered "The take does not say which encoder wrote it." for every recording
the program made, while `ui/settings_panel.py` and `ui/app.py` read back a history
nothing had written. `RecordingSession._describe` now fills the encoder, quality, pixel
format, frame size and frame rate from the first segment's plan, and
`RecordingSession.stop` fills `seconds` from the take's own clock, so a finished
recording is measured and `sizes.json` grows. The check is
`grep -nE "_describe|take.seconds" app/screenrec/recorder.py`, not
`grep -n "Take("`: those fields are filled after the `Take` is constructed, so
searching the constructor answers the wrong question.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

CREATE_NO_WINDOW = 0x08000000

#: The muxer flags that make a segment survive its process being killed.
SEGMENT_FLAGS = ["-flush_packets", "1"]
SEGMENT_SUFFIX = ".mkv"


@dataclass
class Take:
    """One recording, as the user thinks of it: possibly several segments, one file.

    The fields below `segments` are what the finished file needs to be worth anything as
    a measurement, and every one of them defaults to "not said". A caller that fills
    them in gets this machine's own MB-per-minute figure stored on `finish`; a caller
    that does not - `recover`, rebuilding orphans from a session that died, which knows
    none of this - gets exactly the behaviour there was before.

    Since 2026-11-05 `recorder.RecordingSession` fills them in for every recording the
    program makes - `_describe` from the first segment's plan, `seconds` in `stop` -
    so the measurement is live rather than a test fixture; see the module docstring.
    """

    final: Path
    segments: list[Path] = field(default_factory=list)
    #: The ffmpeg encoder name that actually wrote it, e.g. "hevc_nvenc" - what
    #: `capabilities.choose` returned, never the profile key.
    encoder: str = ""
    #: The quality level: a key of `config.QUALITY_LEVELS`.
    quality: str = ""
    #: The profile's pixel format, because 4:2:0 and 4:4:4 cost different amounts
    #: through the same encoder at the same quality.
    pix_fmt: str = ""
    width: int = 0
    height: int = 0
    framerate: int = 0
    #: What the recorder's own clock made the take, if it has one
    #: (`recorder.Progress.seconds`). Cross-checked against the finished file rather
    #: than believed: a clock that counted a pause describes a different recording.
    seconds: float = 0.0
    #: Set by `finish`: '' when the cost was stored, otherwise why it was not. Never a
    #: reason to fail the join, and never shown to a user as it stands - it is English
    #: diagnostic text, like `capabilities.choose`'s reason.
    measurement_problem: str = ""

    def next_segment(self) -> Path:
        """The path the next segment writes to. Never collides with an existing one."""
        index = len(self.segments)
        while True:
            candidate = self.final.with_name(
                f"{self.final.stem}.part{index:02d}{SEGMENT_SUFFIX}"
            )
            if not candidate.exists():
                return candidate
            index += 1

    def add(self, segment: Path) -> None:
        self.segments.append(segment)

    def written(self) -> list[Path]:
        """Segments that actually reached the disk with something in them."""
        return [p for p in self.segments if p.is_file() and p.stat().st_size > 0]


def concat_list(segments: list[Path], target: Path) -> Path:
    """Write ffmpeg's concat list beside the output.

    Paths go in with forward slashes and single quotes doubled, which is the escaping
    the concat demuxer actually wants; a Windows backslash in this file is read as an
    escape and the segment is silently not found.
    """
    listing = target.with_suffix(".concat.txt")
    lines = []
    for segment in segments:
        text = segment.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{text}'\n")
    listing.write_text("".join(lines), encoding="utf-8")
    return listing


def ffprobe_beside(ffmpeg: str) -> str:
    """The ffprobe belonging to this ffmpeg, or '' when there is none.

    They ship in one folder (`tools/ffmpeg`), so the sibling is the right answer, and
    looking there first keeps a test that passes a fake ffmpeg from quietly reaching the
    bundled one. `paths` is the fallback for a settings override naming an ffmpeg.exe
    somewhere else.
    """
    sibling = Path(ffmpeg).with_name("ffprobe.exe")
    if sibling.is_file():
        return str(sibling)
    try:
        return str(paths.ffprobe_exe())
    except (paths.BinaryNotFound, OSError):
        return ""  # no probe, so no duration, so no measurement. Not an error.


def probe_seconds(ffprobe: str, path: Path) -> float:
    """The finished file's own duration, or 0.0 when it cannot be read.

    The file is asked rather than the clock. A take's recorded seconds and its wall
    clock are the same number only when it was never paused, and the whole point of this
    figure is that it describes *this file*.

    Measured 2026-11-04 on this build, on a 171 MB MP4: 0.24 s cold, then 0.11, 0.10,
    0.09, 0.08 s. The concat it follows is the same order of magnitude, and this runs
    once per recording.
    """
    args = [ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path)]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if result.returncode != 0:
        return 0.0
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (ValueError, TypeError, KeyError):
        return 0.0


def segment_shape(ffprobe: str, path: Path) -> tuple[tuple, ...] | None:
    """Each stream of `path` as (type, codec, width, height, pix_fmt, channels), or None.

    None means "could not be read" - there was no ffprobe, the probe failed, or its
    output did not parse. It is the answer `probe_seconds` gives for the same reasons,
    and `shape_problem` carries on without the check rather than refusing a join it
    cannot justify. `unreadable_problem` is the one caller that treats it as an answer,
    and only when another segment of the same take did probe.

    `channels` is here because width and height are 0 on an audio stream, so a
    microphone that went from stereo to mono over a pause was invisible to a guard
    built on the first five. Measured 2026-11-05 on this build: aac 2ch followed by
    aac 1ch joins, exits 0, decodes with 0 errors - and the finished file's header
    declares 2 channels for the whole take, which is the second half described
    wrongly. The sample rate is not here because `recorder._audio_codec_args` always
    passes ``-ar 48000``, so it cannot drift.

    The frame rate is deliberately absent; see `shape_problem`.

    Measured 2026-11-05 on this build, on a 1.5 s segment: 0.025, 0.024, 0.025,
    0.025, 0.025, 0.025 s. `finish` asks only when a take has more than one segment,
    so a recording that was never paused pays nothing for this.
    """
    if not ffprobe:
        return None
    args = [ffprobe, "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt,channels",
            "-of", "json", str(path)]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout)["streams"]
    except (ValueError, TypeError, KeyError):
        return None
    return tuple(
        (stream.get("codec_type", ""), stream.get("codec_name", ""),
         stream.get("width", 0), stream.get("height", 0),
         stream.get("pix_fmt", ""), stream.get("channels", 0))
        for stream in streams
    )


def describe_shape(shape: tuple[tuple, ...]) -> str:
    """One readable line for what `segment_shape` returned."""
    parts = []
    for kind, codec, width, height, pix_fmt, channels in shape:
        if kind == "video":
            parts.append(f"{codec} {width}x{height} {pix_fmt}")
        elif channels:
            parts.append(f"{codec or kind} {channels}ch")
        else:
            parts.append(codec or kind)
    return ", ".join(parts) or "empty"


def shape_problem(ffmpeg: str, segments: list[Path]) -> str:
    """'' when these segments may be joined with `-c copy`, or what disagrees.

    A take is one file, and its segments were written by separate ffmpeg processes
    from separately built plans - the settings panel is reachable while a recording
    is paused. The first segment is the contract, because it is the one the user
    watched start, and every later one is held to it.

    What has to agree was measured on this build on 2026-11-05 by recording 1.5 s,
    pausing, changing exactly one thing, recording 1.5 s more and then decoding the
    joined MP4 right through:

        hevc_nvenc -> h264_nvenc    46 of 90 frames decoded, 125 decode errors
                                    ("Invalid NAL unit size (1677729452 > 28885).
                                    Error splitting the input into NAL units.")
        yuv420p -> yuv444p          all 91 frames arrive, 3 decode errors, and the
                                    picture is wrong from the join on: the file's
                                    header still says yuv420p, so the second half is
                                    decoded against the first half's parameter sets
                                    ("The cu_qp_delta -1094995529 is outside the
                                    valid range [-26, 25]")
        640x480 -> 800x600          the same again: header still says 640x480, 3
                                    decode errors, garbage after the join
        2 audio tracks -> 1         ffmpeg exits 0 and says nothing at all; the
                                    finished file simply carries one track for the
                                    whole take
        aac 2ch -> aac 1ch          joins, exits 0 and decodes with 0 errors, and
                                    the finished file's header says 2 channels for
                                    the whole take. Measured 2026-11-05; see
                                    `segment_shape` for why it used to be invisible

    And one thing they need NOT agree about, measured on this build on 2026-11-05 by
    joining two 1.5 s libx264 segments into the MP4 and then decoding it right
    through:

        30 fps -> 60 fps            all 135 frames decode and the duration is right
                                    (3.00 s for 3.0 s in). The finished file declares
                                    one rate throughout, so the faster half's
                                    timestamps repeat: a later remux of it reports 45
                                    "Application provided invalid, non monotonically
                                    increasing dts" lines. 10 -> 30 fps gives 60
                                    frames and 29 of them; 60 -> 30 fps and
                                    30 -> 60 fps on libx265 give none at all, so the
                                    count depends on the direction and the codec.
                                    No picture is lost either way, and refusing a
                                    join that keeps every frame would lose the user
                                    more than it saves them - so the frame rate is
                                    not part of the contract. It is not "clean",
                                    which this said until the dts lines were counted.

    Note what is being guarded against: ffmpeg does not report any of this. All five
    broken joins above exited 0.

    `finish` does not call this: it needs the same probes for `unreadable_problem` and
    reaches `_disagreement` with them, so a take is probed once rather than twice.
    """
    if len(segments) < 2:
        return ""
    shapes = _shapes_of(ffmpeg, segments)
    return _disagreement(segments, shapes)


def _shapes_of(ffmpeg: str, segments: list[Path]) -> list[tuple[tuple, ...] | None]:
    """`segment_shape` for each segment, from one lookup of the probe."""
    ffprobe = ffprobe_beside(ffmpeg)
    return [segment_shape(ffprobe, segment) for segment in segments]


def _disagreement(
    segments: list[Path], shapes: list[tuple[tuple, ...] | None]
) -> str:
    first = shapes[0]
    if first is None:
        return ""
    for segment, other in zip(segments[1:], shapes[1:]):
        if other is None:
            continue  # unreadable, not different - `unreadable_problem` owns that
        if other != first:
            return (
                f"{segments[0].name} is {describe_shape(first)} and "
                f"{segment.name} is {describe_shape(other)}"
            )
    return ""


def unreadable_problem(
    segments: list[Path], shapes: list[tuple[tuple, ...] | None]
) -> str:
    """'' when every segment can be read, or which of them the join would drop.

    The concat demuxer **exits 0 over a segment it cannot open**. Measured on this
    build on 2026-11-05, four 3.0 s segments with the second one's EBML header
    overwritten with zeroes:

        ffmpeg returncode 0, and on stderr:
            [matroska,webm] 0x00 at pos 0 (0x0) invalid as first byte of an EBML number
            [matroska,webm] EBML header parsing failed
            Impossible to open '...part01.mkv'
            Error during demuxing: Invalid data found when processing input
        9.0 s went in and a 3.0 s file came out - and `finish` then deleted all four
        segments, because the return code and the output file both said it had worked.

    Only a *later* segment is lost this quietly: with the first one damaged, ffmpeg
    fails to open the input at all and exits non-zero, measured on the same files
    ("Error opening input files: Invalid data found when processing input"), and a
    single damaged segment fails its remux the same way. So this guard matters from
    the second segment on - which is exactly a take that was paused.

    A probe that cannot speak at all must not block a join: with no ffprobe every
    shape is None, and `segment_shape` says the same for an ffprobe that failed. So a
    segment is only called unreadable when another segment of the same take was read,
    which is the proof that the probe works on these files. The case this does *not*
    catch is a segment truncated by a killed process - measured: it probes perfectly
    well, it joins, and what reached the disk is kept, which is the whole point of
    writing Matroska with the packets flushed.
    """
    if not any(shape is not None for shape in shapes):
        return ""
    lost = [
        segment.name for segment, shape in zip(segments, shapes) if shape is None
    ]
    if not lost:
        return ""
    return ", ".join(lost)


def measure(
    ffmpeg: str, take: Take, path: Path, store: Path | None = None
) -> str:
    """Store what this finished recording cost per minute. '' or why it was not stored.

    `storage` is imported here and not at the top of the module because it reaches
    `i18n`, which imports PySide6. Nothing else in this file touches Qt and the recorder
    side is better off keeping it that way; the import costs one dictionary lookup per
    recording.
    """
    from . import storage

    if not take.encoder:
        return "The take does not say which encoder wrote it."
    ffprobe = ffprobe_beside(ffmpeg)
    if not ffprobe:
        return "ffprobe was not found, so the file's own duration is unknown."
    seconds = probe_seconds(ffprobe, path)
    if seconds <= 0:
        return "The finished file's duration could not be read."
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        return f"The finished file could not be measured: {exc}"
    return storage.remember_recording(
        encoder=take.encoder,
        quality=take.quality,
        pix_fmt=take.pix_fmt,
        size_bytes=size_bytes,
        seconds=seconds,
        width=take.width,
        height=take.height,
        framerate=take.framerate,
        claimed_seconds=take.seconds if take.seconds > 0 else None,
        store=store,
    )


def finish(
    ffmpeg: str, take: Take, keep_segments: bool = False,
    size_store: Path | None = None,
) -> tuple[Path | None, str]:
    """Turn a take's segments into the single file the user asked for.

    Returns (path, problem). One segment is a remux; several is a concat; neither
    re-encodes. A problem is returned as text and the segments are left on disk, because
    a failed join with the segments deleted would be the worst outcome available.

    On success the finished file is also measured into `storage` - see `measure` - and
    whatever that had to say lands in `take.measurement_problem`. It cannot fail the
    join: a cost that was not recorded costs the user a better estimate next time and
    nothing else. `size_store` overrides where the history is kept, for tests.

    Everything this returns is read out to the user, so it is translated. `i18n` is
    imported here rather than at the top of the module for the reason `measure` gives
    for `storage`: it reaches PySide6, and nothing else in this file touches Qt. What
    the placeholders carry - file names, folders, ffmpeg's codec and format words, and
    its own error text - is machine text in any language, so only the sentences around
    them are keys.
    """
    from .i18n import _

    written = take.written()
    if not written:
        return None, _("Nothing was recorded - no segment reached the disk.")

    # `-c copy` will join anything it is handed and exit 0 - over a file that does not
    # decode, and over one it could not open at all - so the segments are asked about
    # themselves first. `unreadable_problem` and `shape_problem` carry what each case
    # measured. One pass of probes answers both. The segments are left alone on a
    # refusal: they hold everything that was recorded, and each whole one plays on its
    # own.
    if len(written) > 1:
        shapes = _shapes_of(ffmpeg, written)
        lost = unreadable_problem(written, shapes)
        if lost:
            return None, _(
                "Part of the recording could not be read, and joining it would "
                "quietly leave that part out: {names}. Every segment is still in "
                "{folder}, and the ones that are whole still play."
            ).format(names=lost, folder=written[0].parent)
        disagreement = _disagreement(written, shapes)
        if disagreement:
            return None, _(
                "The recording's settings changed while it was paused, so its parts "
                "cannot be joined without re-encoding it: {details}. Every segment "
                "is still in {folder} and holds what was recorded."
            ).format(details=disagreement, folder=written[0].parent)

    take.final.parent.mkdir(parents=True, exist_ok=True)
    listing: Path | None = None
    if len(written) == 1:
        args = [ffmpeg, "-hide_banner", "-loglevel", "error",
                "-i", str(written[0])]
    else:
        listing = concat_list(written, take.final)
        args = [ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(listing)]
    # `-map 0` or ffmpeg's default mapping takes the best *one* stream of each kind,
    # and a take recorded with both a microphone and the system sound as separate
    # tracks loses one of them. Measured 2026-11-05: a segment holding
    # [video, audio "Microphone", audio "System"] came out of both the remux and the
    # concat as [video, audio] until this was added, with nothing said and exit 0.
    args += ["-map", "0",
             "-c", "copy", "-movflags", "+faststart", "-y", str(take.final)]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, _(
            "The recording could not be assembled: {reason}. The segments are still "
            "in {folder} and can be joined by hand."
        ).format(reason=exc, folder=written[0].parent)

    if listing is not None:
        listing.unlink(missing_ok=True)

    if result.returncode != 0 or not take.final.is_file():
        tail = (result.stderr or "").strip().splitlines()
        reason = tail[-1] if tail else f"ffmpeg exited {result.returncode}"
        return None, _(
            "The recording could not be assembled: {reason}. The segments are still "
            "in {folder} and can be joined by hand."
        ).format(reason=reason, folder=written[0].parent)

    take.measurement_problem = measure(ffmpeg, take, take.final, size_store)

    if not keep_segments:
        for segment in written:
            segment.unlink(missing_ok=True)
    return take.final, ""


def orphans(folder: Path) -> list[Path]:
    """Segments left behind by a session that died, newest first.

    Offered to the user on the next launch: a killed MKV still holds everything that
    reached the disk, and throwing it away silently would be the opposite of crash
    safety.
    """
    if not folder.is_dir():
        return []
    found = [
        path for path in folder.glob(f"*.part*{SEGMENT_SUFFIX}")
        if path.is_file() and path.stat().st_size > 0
    ]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def recover(ffmpeg: str, segments: list[Path]) -> tuple[Path | None, str]:
    """Rebuild one MP4 from orphaned segments of the same take.

    What it returns reaches the user through `ui/app.py`'s recovery offer, so it is
    translated; `finish` says why the import is here and not at the top.
    """
    if not segments:
        from .i18n import _

        return None, _("There is nothing to recover.")
    stem = segments[0].name.split(".part")[0]
    take = Take(final=segments[0].with_name(f"{stem}.mp4"))
    take.segments = sorted(segments)
    return finish(ffmpeg, take)


def group_orphans(segments: list[Path]) -> dict[str, list[Path]]:
    """Orphans belong to takes; a take is everything sharing the stem before .partNN."""
    takes: dict[str, list[Path]] = {}
    for path in segments:
        takes.setdefault(path.name.split(".part")[0], []).append(path)
    for key in takes:
        takes[key].sort()
    return takes
