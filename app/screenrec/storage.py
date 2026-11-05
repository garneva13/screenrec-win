"""How much room is left, said as time rather than as gigabytes.

"38 GB free" is not an answer to "can I record this meeting". The app already knows what
a minute costs for the chosen format, quality and screen - `config.estimate_mb_per_minute`
is measured rather than guessed, though it was measured on one machine's NVENC and x264 -
so the honest reading is how long the disk will last.

Nothing here stops a recording. The caller decides what to do with a warning; this module
only reports, because a recorder that refuses to start because it thinks the disk is
small is worse than one that says so and lets you decide.

The second half of the file is where that cost per minute comes from. `config.PROFILES`
has a measured table, but it was measured on one machine's NVENC; from the second half
down, this module keeps what *this* machine's own finished recordings cost and prefers
that. `size_estimate` is the one function a caller needs, and it says which of the two
answers it gave.

**The reading end is wired and the writing end is not**, so in practice every answer is
still `measured=False`. Checked by grep on 2026-11-04: `ui/settings_panel.py` and
`ui/app.py` ask `size_estimate`, and nothing ever stores a sample for it to find -
`recorder.RecordingSession` builds its `takes.Take` from the output path alone, so
`takes.measure` refuses every real recording for want of an encoder name and
`sizes.json` is never written. That seam is in a file this module does not own;
`grep -n "Take(" app/screenrec/recorder.py` is the check, and until it changes the
figure on screen is the table's - which on a machine recording through AMF, QSV or
Media Foundation is somebody else's number.
"""
from __future__ import annotations

import json
import shutil
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, paths
from .i18n import _

#: Below this the user is told before they press record.
WARN_MINUTES = 15.0
#: Below this a running recording is worth interrupting for.
CRITICAL_MINUTES = 3.0


@dataclass(frozen=True)
class Space:
    free_bytes: int
    total_bytes: int
    minutes_left: float  # at the current settings; inf when nothing is known
    problem: str = ""

    @property
    def free_gb(self) -> float:
        return self.free_bytes / 1e9

    @property
    def known(self) -> bool:
        return not self.problem

    @property
    def critical(self) -> bool:
        return self.known and self.minutes_left < CRITICAL_MINUTES

    @property
    def low(self) -> bool:
        return self.known and self.minutes_left < WARN_MINUTES

    def human(self) -> str:
        """The line the UI shows. Says gigabytes too, because people check that."""
        if self.problem:
            return self.problem
        return _("{free} GB free — about {duration}").format(
            free=f"{self.free_gb:.0f}", duration=duration(self.minutes_left)
        )


def duration(minutes: float) -> str:
    """Minutes as something readable: '12 min', '3 h 20 min', 'more than a day'."""
    if minutes == float("inf"):
        return _("an unknown amount of recording")
    if minutes >= 60 * 24:
        return _("more than a day of recording")
    if minutes < 1:
        return _("less than a minute of recording")
    hours, rest = divmod(int(minutes), 60)
    if hours and rest:
        return _("{hours} h {minutes} min of recording").format(
            hours=hours, minutes=rest
        )
    if hours:
        return _("{hours} h of recording").format(hours=hours)
    return _("{minutes} min of recording").format(minutes=rest)


def check(folder: Path, megabytes_per_minute: float) -> Space:
    """Free space on the volume holding `folder`, turned into minutes.

    The folder may not exist yet - a first run, or a path the user typed - so the
    nearest existing parent is measured instead. That is the same volume, which is what
    the question is about.
    """
    target = Path(folder)
    probe = target
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    try:
        usage = shutil.disk_usage(str(probe))
    except OSError as exc:
        return Space(
            0, 0, float("inf"),
            _("Free space on {path} is unreadable: {reason}").format(
                path=target, reason=exc
            ),
        )

    if megabytes_per_minute <= 0:
        minutes = float("inf")
    else:
        minutes = (usage.free / 1e6) / megabytes_per_minute
    return Space(usage.free, usage.total, minutes)


def warning_for(space: Space) -> str:
    """What to tell the user before they record, or '' when there is nothing to say."""
    if not space.known:
        return space.problem
    if space.critical:
        return _(
            "Only {free} GB left — about {duration}. "
            "The recording will stop when the disk fills."
        ).format(
            free=f"{space.free_gb:.1f}", duration=duration(space.minutes_left)
        )
    if space.low:
        return _("{free} GB left — about {duration}.").format(
            free=f"{space.free_gb:.0f}", duration=duration(space.minutes_left)
        )
    return ""


# ----------------------------------------- what a minute really cost on this machine
#
# `config.PROFILES[...].mb_per_minute` is measured, but it was measured *somewhere
# else*: `tools/benchmark_encoders.py` on NVENC and on x264, at 1280x720 and 60 fps, on
# deliberately busy content. A machine that records through AMF, QSV or Media Foundation
# is shown somebody else's number. `docs/ENCODERS.md` section 3 measured `hevc_mf`
# against `hevc_nvenc` at cq 23 twice on the same day: 1.03x the size on scrolling text
# and 4.9x on a busy desktop. Quoting the NVENC table there is wrong by a factor of five.
#
# So the machine measures itself. Every finished recording knows what it cost - its file
# size and its duration - and one real recording beats a table measured on hardware this
# machine does not have. `takes.finish` stores it when the take says which encoder wrote
# it; `size_estimate` reads it back and says which of the two answers it gave. On this
# build nothing ever says which encoder wrote it - see this module's docstring for the
# one missing line and where it lives.
#
# The key is (encoder name, quality level, pixel format). The encoder name is the point
# of the whole exercise. The quality level is obvious. The pixel format is there because
# `hevc420` and `hevc444` are the same encoder name at the same quality and cost
# measurably different amounts - 24.5 against 19.0 MB/min at `balanced` in
# `config.PROFILES`, which is this project's own benchmark - so one key for both would
# average two different things.

#: Bumped when `Sample`'s fields change: an older file is dropped rather than guessed at.
SIZES_VERSION = 1

#: At 1280x720 and 60 fps, which is what `config.Profile.mb_per_minute` is quoted at.
REFERENCE_PIXELS = 1280 * 720
REFERENCE_FRAMERATE = 60

#: Shorter than this and the recording is not asked to speak for the machine.
#:
#: Measured 2026-11-04 on this build, by splitting one capture into prefixes and
#: comparing each prefix's MB/minute with the whole file's. On deterministic uniform
#: content (testsrc2, 2560x1440 at 60 fps) the opening keyframe is worth little: a 0.5 s
#: prefix read 1.04x the 20 s figure through `hevc_nvenc` and 0.95x through `libx264`,
#: and both were inside 1% by 1.5-3 s. What makes a short recording useless is the
#: screen, not the encoder: the same prefixes of a real 20 s desktop capture read 0.40x
#: at 0.5 s, 0.62x at 2 s, 0.79x at 3 s and 1.00x at 5 s. Three seconds is a floor
#: against the degenerate case, not a cure for content - five recordings and a median
#: are what answer that.
MIN_MEASURED_SECONDS = 3.0

#: How far the caller's own clock may sit from the finished file before the measurement
#: is refused. The case this catches is a duration that counted a pause: a take paused
#: for half a minute and reported as wall-clock is tens of seconds out, not hundredths.
#:
#: Measured 2026-11-04: ffmpeg's `out_time` (what `recorder.Progress.seconds` is built
#: from) matched the finished file's probed duration to 0.0000, 0.0000 and 0.0003 s on
#: three cleanly stopped segments, and `finish`'s concat added 0.000 s over takes of 1,
#: 3 and 5 segments. The tolerance is set far wider than those figures because a capture
#: killed mid-frame has not been measured here.
DURATION_TOLERANCE = 0.05
DURATION_SLACK = 0.5

#: How many recordings are kept per key. The median of them is what is reported, because
#: one recording of a still screen and one of a game are both honest and far apart -
#: `docs/ENCODERS.md` section 3 measured 71.5 against 146.1 MB/min for identical settings
#: on different content. A median of a handful is the user's own typical content.
KEEP_SAMPLES = 5


@dataclass(frozen=True)
class Sample:
    """One finished recording, as a cost."""

    encoder: str
    quality: str
    pix_fmt: str
    #: What this recording actually cost, at the size and frame rate it was taken at.
    mb_per_minute: float
    #: The same figure scaled back to 1280x720 at 60 fps, so it can be compared with
    #: `config.Profile.mb_per_minute` and re-scaled to a different capture.
    base_mb_per_minute: float
    width: int
    height: int
    framerate: int
    seconds: float
    #: `time.time()` when it was stored, so the panel can say how fresh the answer is.
    at: float


@dataclass(frozen=True)
class Estimate:
    """What a minute costs, and whether anybody measured it here."""

    mb_per_minute: float
    #: True when this machine's own recordings produced the figure; False when it is
    #: `config.PROFILES`, which was measured on an RTX 3070 Ti and on x264.
    measured: bool
    encoder: str = ""
    #: How many recordings the figure is a median of. 0 when `measured` is False.
    samples: int = 0
    #: `time.time()` of the newest recording behind it. 0.0 when `measured` is False.
    at: float = 0.0


def capture_factor(width: int, height: int, framerate: int) -> float:
    """How much more a capture costs than the 1280x720 at 60 fps the table is quoted at.

    This is `config.estimate_mb_per_minute`'s own model, written out so a measured figure
    can be scaled the other way. It is deliberately a copy rather than an import of the
    arithmetic, for the same reason `config.LANGUAGES` is a copy, and for the same reason
    `tests/test_storage.py` asserts the two agree on a grid of sizes: the copy cannot
    drift without a failing test.

    It is a model, not a measurement, and `config.Profile`'s own docstring says how rough
    it is - a real 25 s desktop capture at 2560x1440 cost 11 MB a minute where the table
    predicted 98. Scaling a *measured* figure by it is still better than quoting a figure
    measured on hardware this machine does not have.
    """
    pixels = max(1, width * height) / REFERENCE_PIXELS
    return pixels * (max(1, framerate) / REFERENCE_FRAMERATE)


def sizes_path(store: Path | None = None) -> Path:
    """Beside the settings and the encoder probe cache, for the same reason."""
    return Path(store) if store is not None else paths.config_dir() / "sizes.json"


def _key(encoder: str, quality: str, pix_fmt: str) -> str:
    return f"{encoder}|{quality}|{pix_fmt}"


def load_samples(store: Path | None = None) -> dict[str, list[Sample]]:
    """Every stored recording, keyed as above. An unreadable file reads as empty.

    A corrupt or hand-edited file is not an error worth telling anybody about: the only
    consequence is that the estimate falls back to the table, which is where every
    machine starts anyway.
    """
    target = sizes_path(store)
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("version") != SIZES_VERSION:
        return {}
    table: dict[str, list[Sample]] = {}
    rows = raw.get("samples")
    if not isinstance(rows, dict):
        return {}
    for key, entries in rows.items():
        if not isinstance(entries, list):
            continue
        kept: list[Sample] = []
        for entry in entries:
            sample = _sample_from(entry)
            if sample is not None:
                kept.append(sample)
        if kept:
            table[key] = kept
    return table


def _sample_from(entry: object) -> Sample | None:
    """One stored row, or None when it is not one.

    The fields are converted rather than trusted. This file sits in `%APPDATA%` where
    anybody can open it, and a `base_mb_per_minute` of `"lots"` would otherwise travel
    all the way to `statistics.median` and take the settings panel down with it. Nothing
    here is worth an error message: a row that cannot be read is a recording that was
    never measured, and the table is still there.
    """
    if not isinstance(entry, dict):
        return None
    try:
        sample = Sample(**entry)
    except TypeError:
        return None  # a row from a shape this version does not know
    try:
        sample = Sample(
            encoder=str(sample.encoder),
            quality=str(sample.quality),
            pix_fmt=str(sample.pix_fmt),
            mb_per_minute=float(sample.mb_per_minute),
            base_mb_per_minute=float(sample.base_mb_per_minute),
            width=int(sample.width),
            height=int(sample.height),
            framerate=int(sample.framerate),
            seconds=float(sample.seconds),
            at=float(sample.at),
        )
    except (TypeError, ValueError):
        return None
    if not (sample.base_mb_per_minute > 0) or not (sample.mb_per_minute > 0):
        return None  # also catches NaN, which json.loads happily produces from NaN
    return sample


def save_samples(
    table: dict[str, list[Sample]], store: Path | None = None
) -> str:
    """Write the history. Returns '' or the reason it could not be written.

    Written to a sibling and renamed over the target, as `config.save_settings` does, so
    that a write which dies part-way cannot take the history with it. `Path.write_text`
    truncates the target the moment it opens it, and the reason that matters was
    reproduced on 2026-11-04 by standing in for a full disk - raising from the write
    after the open - against the plain `write_text` this replaced: `sizes.json` came back
    zero bytes long and a measurement that was already stored was gone. The rename then
    replaces the target in one step instead of emptying and refilling it;
    `tests/test_storage.py`'s `SaveTest` is the guard.

    The temporary name is the target's plus `.tmp`, which collides with neither of the
    other two stores in `%APPDATA%\\ScreenRec` (`settings.json.tmp` is `config`'s, and
    `encoders.json` is written by `capabilities` under its own name).
    """
    target = sizes_path(store)
    temp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps(
                {
                    "version": SIZES_VERSION,
                    "samples": {
                        key: [asdict(sample) for sample in samples]
                        for key, samples in table.items()
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        temp.replace(target)
    except OSError as exc:
        # A half-written temporary is of no use to anybody and would otherwise sit in
        # %APPDATA% for ever; the history itself is untouched either way.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            # Nothing to repair and nothing to report: the history itself was never
            # opened, and a temporary that cannot be removed is not the user's problem.
            pass
        return f"The recording sizes could not be saved: {exc}"
    return ""


def remember_recording(
    encoder: str,
    quality: str,
    pix_fmt: str,
    size_bytes: int,
    seconds: float,
    width: int,
    height: int,
    framerate: int,
    claimed_seconds: float | None = None,
    store: Path | None = None,
) -> str:
    """Store what one finished recording cost. Returns '' or why it was not stored.

    `seconds` must be the finished file's own duration - `takes.finish` probes it rather
    than trusting a clock. `claimed_seconds` is the caller's figure when it has one
    (`recorder.Progress.seconds`); when the two disagree the measurement is dropped,
    because one of them is describing a different file.

    The reason is English and diagnostic, like `capabilities.choose`'s: nothing here is
    shown to a user, so nothing here goes through `_()`. Whoever displays one owns the
    Russian pair.
    """
    if not encoder or not quality:
        return "No encoder or quality was given, so there is nothing to key this on."
    if size_bytes <= 0:
        return f"The file is {size_bytes} bytes; there is nothing to measure."
    if not (seconds > 0):
        return "The recording has no duration."
    if seconds < MIN_MEASURED_SECONDS:
        return (
            f"{seconds:.1f} s is too short to speak for this machine "
            f"(under {MIN_MEASURED_SECONDS:.0f} s)."
        )
    if width <= 0 or height <= 0 or framerate <= 0:
        return (
            "The capture size and frame rate are unknown, so the figure cannot be "
            "compared with anything."
        )
    if claimed_seconds is not None:
        slack = max(DURATION_SLACK, seconds * DURATION_TOLERANCE)
        if abs(claimed_seconds - seconds) > slack:
            return (
                f"The recorder counted {claimed_seconds:.1f} s and the file is "
                f"{seconds:.1f} s long; that is not the same recording."
            )

    rate = (size_bytes / 1e6) / (seconds / 60.0)
    sample = Sample(
        encoder=encoder,
        quality=quality,
        pix_fmt=pix_fmt,
        mb_per_minute=rate,
        base_mb_per_minute=rate / capture_factor(width, height, framerate),
        width=width,
        height=height,
        framerate=framerate,
        seconds=seconds,
        at=time.time(),
    )
    table = load_samples(store)
    key = _key(encoder, quality, pix_fmt)
    table[key] = (table.get(key, []) + [sample])[-KEEP_SAMPLES:]
    return save_samples(table, store)


def size_estimate(
    profile_key: str,
    quality_level: str,
    width: int,
    height: int,
    framerate: int,
    encoder: str = "",
    store: Path | None = None,
) -> Estimate:
    """What a minute costs here, measured if this machine has ever measured it.

    `encoder` is the ffmpeg name the recording would actually use - what
    `capabilities.choose` returns for this profile. Without it there is nothing to look
    up and the table's figure comes back with `measured` False, which is the right answer
    before the first recording and on a machine whose encoder has just changed.
    """
    table_figure = config.estimate_mb_per_minute(
        profile_key, quality_level, width, height, framerate
    )
    profile = config.PROFILES.get(profile_key)
    if not encoder or profile is None:
        return Estimate(table_figure, False)

    samples = load_samples(store).get(
        _key(encoder, quality_level, profile.pix_fmt), []
    )
    if not samples:
        return Estimate(table_figure, False)

    base = statistics.median(s.base_mb_per_minute for s in samples)
    return Estimate(
        mb_per_minute=base * capture_factor(width, height, framerate),
        measured=True,
        encoder=encoder,
        samples=len(samples),
        at=max(s.at for s in samples),
    )


def forget_measurements(store: Path | None = None) -> str:
    """Throw the history away - after a driver change, or because the user asked.

    Returns '' or the reason it could not be removed. A file that is not there is not a
    failure: the point is that afterwards there is no history, and there is none.
    """
    target = sizes_path(store)
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        return f"The recording sizes could not be cleared: {exc}"
    return ""
