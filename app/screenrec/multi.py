"""Recording every screen at once: one file per screen, kept together.

The idea the user asked for is not two screens squeezed into one frame. It is two
recordings watched the way the screens were used - eyes moving from one to the other -
which means two files at full resolution, and something that plays them in step.

**One ffmpeg process per screen.** That was measured, twice, because the first answer was
wrong. Recording both screens with one process and two ``ddagrab`` inputs looked like the
obvious choice - one clock, one thing to stop - and it costs frames: 53.4 and 59.2 fps
against 59.8 for a single screen. Two processes hold **59.7 and 59.1**.

The first measurement also said two processes drifted half a second apart, and that was
an artefact of the measurement: it compared how *long* the files were, and they were
stopped one after the other. Flashing a white square on both screens at the same instant
and finding it again in each file gives the real answer:

    one process, two inputs   the mark lands 50 ms apart
    two processes             the mark lands 17 ms apart   <- one frame

So two processes are better on both counts, and the architecture is the simple one.

**Audio belongs to exactly one file.** Recording the microphone into both would give two
copies of the same voice and a player that has to mute one. The first source carries it,
and the sidecar says which that is.

**Version 2 of the index, and why the recorder's own output did not change with it.**
`docs/PLAYER_SOURCES.md` lets a viewer pair two files this recorder never paired - two
cameras, started by two hands - and slide one against the other until they match. That
needs two things the version 1 index cannot say: a per-screen `offset_ms`, and a `file`
that is an absolute path, because two cameras are not guaranteed to live in one folder.
Both are written by `write_set`, which is the *player's* saver.

`MultiSession.write_sidecar` is not changed beyond the version number. §1.3 of that
document is explicit about it - "The recorder never writes `offset_ms`, because its own
screens start together and a field that is always 0 is a field that rots" - and the
recorder's two screens were measured 17 ms apart, one frame, which is not an offset
anybody would want to correct. A reader treats the missing field as 0, so a version 1
index and a recorder-made version 2 one mean exactly the same thing.
"""
from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import recorder
from .i18n import _
from .winapi import Monitor

SIDECAR_SUFFIX = ".screenrec.json"

#: 2 since the player learned to save a pair of its own (`write_set`). The number says
#: what a reader *may* find, not what every writer puts there: `offset_ms` is optional
#: and absent means 0, so a version 1 index needs no conversion and never will.
SIDECAR_VERSION = 2


@dataclass
class ScreenTake:
    """One screen's recording inside a multi-screen take."""

    source: recorder.VideoSource
    final: Path
    session: recorder.RecordingSession | None = None
    problem: str = ""

    @property
    def monitor(self) -> Monitor:
        return self.source.monitor


@dataclass
class MultiProgress:
    seconds: float = 0.0
    size_bytes: int = 0
    worst_fps: float = 0.0
    dropped: int = 0
    screens: int = 0


class MultiSession:
    """Several screens recorded as one take.

    Start, pause, resume and stop all fan out to every screen; the clock and the size
    the user sees are the take's, not one screen's. A screen that fails to start is
    reported and the others carry on - half a recording is better than none, and
    silently recording one screen when two were asked for is the thing to avoid.
    """

    def __init__(self, ffmpeg: str, stem: Path, plan_for) -> None:
        self.ffmpeg = ffmpeg
        self.stem = stem  # the final path without the per-screen suffix
        self.plan_for = plan_for  # plan_for(source, segment, with_audio) -> RecordingPlan
        self.takes: list[ScreenTake] = []
        self.started_at: datetime | None = None
        self.notes: list[str] = []
        self._paused = False

    # -- building ----------------------------------------------------------

    def add(self, source: recorder.VideoSource) -> ScreenTake:
        index = len(self.takes)
        name = f"{self.stem.stem}_screen{source.monitor.index}{self.stem.suffix}"
        take = ScreenTake(source=source, final=self.stem.with_name(name))
        self.takes.append(take)
        return take

    # -- state -------------------------------------------------------------

    @property
    def active(self) -> bool:
        return any(t.session is not None and t.session.active for t in self.takes)

    @property
    def recording(self) -> bool:
        return any(t.session is not None and t.session.recording for t in self.takes)

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def progress(self) -> MultiProgress:
        total = MultiProgress()
        rates: list[float] = []
        for take in self.takes:
            if take.session is None:
                continue
            one = take.session.progress
            total.seconds = max(total.seconds, one.seconds)
            total.size_bytes += one.size_bytes
            total.dropped += one.dropped
            if one.fps:
                rates.append(one.fps)
            total.screens += 1
        total.worst_fps = min(rates) if rates else 0.0
        return total

    def log_text(self) -> str:
        return "\n".join(
            t.session.log_text() for t in self.takes if t.session is not None
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self, on_progress=None, loopback_device: int = -1) -> str:
        """Start every screen, as close together as the operating system allows.

        They are launched in a tight loop with nothing between them, because the gap
        between the first and the last is the offset a viewer would see.

        **The loop's own gap is 2.0-2.3 ms**, measured five times on an idle machine by
        timing both `Recorder.start` calls, while the whole of this method takes
        355-365 ms - the start grace, paid once at the end rather than once per screen.

        What a viewer sees is larger than 2 ms and is not this code's to give: it is
        when each `ddagrab` hands over its first frame, which differs per monitor.
        `tools/verify_two_screens.py` finds the flash in each finished file and reported
        0, 16, 50, 0, 84, 33, 0, 83 ms over eight runs on that same idle machine -
        median 33 ms, and every value a whole number of frames at 60 fps, because that
        is all the measurement can resolve. The 17 ms this once claimed was one sample
        of that distribution, not a constant.

        **Nothing may be waited for inside that loop**, and that is the whole reason
        this is two loops rather than one. `Recorder.start` sits out a start grace so
        that a capture which dies of its own arguments says so instead of reporting
        success - measured 0.355-0.363 s - and paying it per screen is paying it
        *between* screens. Measured when this method did: `verify_two_screens` reported
        the screens 334-367 ms apart over three runs, twenty frames, against 17 ms
        before. So every screen is launched with `wait=False`, and every screen is
        asked afterwards, by `settle`, which answers from the same deadline and
        therefore costs nothing extra once the launches are done.
        """
        if self.active:
            return _("That recording is already running.")
        self.started_at = datetime.now()
        self.notes = []
        failures: list[str] = []

        for index, take in enumerate(self.takes):
            with_audio = index == 0  # exactly one file carries the sound
            session = recorder.RecordingSession(
                self.ffmpeg,
                take.final,
                lambda segment, s=take.source, a=with_audio: self.plan_for(s, segment, a),
            )
            problem = session.start(
                on_progress=on_progress if index == 0 else None,
                loopback_device=loopback_device if with_audio else -1,
                wait=False,
            )
            if problem:
                take.problem = problem
                failures.append(
                    _("screen {index}: {problem}").format(
                        index=take.monitor.index, problem=problem
                    )
                )
                continue
            take.session = session

        # Now, and only now, ask each one whether it is really recording. A capture
        # that failed on its own arguments is already dead by the time its grace is
        # out, and `settle` puts that session back exactly where the launch found it.
        for take in self.takes:
            session = take.session
            if session is None:
                continue
            problem = session.settle()
            if not problem:
                continue
            take.session = None
            take.problem = problem
            failures.append(
                _("screen {index}: {problem}").format(
                    index=take.monitor.index, problem=problem
                )
            )

        if not any(t.session is not None for t in self.takes):
            return (
                _("No screen could be recorded.") + " " + "  ".join(failures)
            )
        if failures:
            self.notes.extend(failures)
            return ""
        return ""

    def pause(self) -> str:
        problems = [
            t.session.pause() for t in self.takes if t.session is not None
        ]
        self._paused = True
        return "  ".join(p for p in problems if p)

    def resume(self) -> str:
        problems = [
            t.session.resume() for t in self.takes if t.session is not None
        ]
        self._paused = False
        return "  ".join(p for p in problems if p)

    def stop(self) -> tuple[list[Path], str]:
        """Stop every screen, then assemble each and write the sidecar.

        Every ``q`` is sent before any file is assembled: joining the first recording
        while the second is still capturing would make the second one longer by however
        long the join took.
        """
        live = [t for t in self.takes if t.session is not None]
        for take in live:
            session = take.session
            if session is not None and session.recording:
                proc = session.recorder.process if session.recorder else None
                if proc is not None and proc.poll() is None and proc.stdin:
                    try:
                        proc.stdin.write(b"q")
                        proc.stdin.flush()
                    except OSError:
                        # It is already going; the stop below waits for it either way.
                        pass

        paths: list[Path] = []
        problems: list[str] = []
        for take in live:
            session = take.session
            if session is None:
                continue
            path, problem = session.stop()
            if problem:
                problems.append(f"screen {take.monitor.index}: {problem}")
            if path is not None:
                paths.append(path)
        self._paused = False

        if paths:
            sidecar_problem = self.write_sidecar(paths)
            if sidecar_problem:
                problems.append(sidecar_problem)
        return paths, "  ".join(problems)

    def discard(self) -> str:
        problems = [
            t.session.discard() for t in self.takes if t.session is not None
        ]
        self._paused = False
        return "  ".join(p for p in problems if p)

    # -- the sidecar -------------------------------------------------------

    def sidecar_path(self) -> Path:
        return self.stem.with_suffix(SIDECAR_SUFFIX)

    def write_sidecar(self, written: list[Path]) -> str:
        """Describe the set, so a player can put the screens where they really were.

        The desktop rectangles are the point: a viewer should be able to throw the two
        windows onto their own two screens in the same arrangement, which is what makes
        moving your eyes between them feel like the original.
        """
        screens = []
        for take in self.takes:
            if take.session is None or take.final not in written:
                continue
            monitor = take.monitor
            width, height = take.source.output_size()
            screens.append(
                {
                    "file": take.final.name,
                    "monitor": monitor.index,
                    "desktop": [monitor.x, monitor.y, monitor.width, monitor.height],
                    "size": [width, height],
                    "rotation": monitor.rotation,
                    "primary": monitor.x == 0 and monitor.y == 0,
                }
            )
        if not screens:
            return "Nothing was recorded, so no sidecar was written."

        document = {
            "version": SIDECAR_VERSION,
            "recorded": (self.started_at or datetime.now()).isoformat(timespec="seconds"),
            "audio": screens[0]["file"],
            "screens": screens,
        }
        target = self.sidecar_path()
        try:
            target.write_text(
                json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            return _(
                "The recording's index could not be written: {reason}"
            ).format(reason=exc)
        return ""


def resolve_file(folder: Path, name: str) -> Path:
    """Where an index's `"file"` entry actually is.

    One rule, in one place, because two readers that disagree about it would open
    different files from the same index: **an absolute path is used as it stands, and
    anything else is resolved beside the index.** A bare name is what the recorder
    writes and it still means "next to this sidecar"; an absolute path is what
    `write_set` writes for a file the viewer picked out of some other folder.

    Nothing is resolved against the working directory. A drive-relative spelling such as
    ``D:take.mp4`` is not absolute on Windows, so it is treated as a name beside the
    index and will simply not be found - which is the honest answer, rather than opening
    whatever happens to sit in that drive's current directory.
    """
    candidate = Path(str(name))
    if candidate.is_absolute():
        return candidate
    return Path(folder) / candidate


def write_set(
    path: Path,
    files: list[Path],
    audio_index: int = 0,
    offsets: list[int] | None = None,
    sizes: Sequence[Sequence[int] | None] | None = None,
    framerate: float | None = None,
) -> str:
    """Write the player's "Save this set": two files a viewer paired by hand.

    This is the other half of `docs/PLAYER_SOURCES.md` §1.3. Two files, which one carries
    the sound and how far each is slid from the other are work, and losing that on close
    is what makes people stop using a feature - so it is written as an ordinary index
    that `Recording.load` opens with nothing to redo.

    What is deliberately *not* written is anything about **monitors**. These files were
    not recorded by this program and nothing here knows what screen they came from;
    inventing a desktop rectangle would be a lie that the layouts would then act on.
    `Recording.load` fills in a nominal side-by-side arrangement for an entry that names
    no rectangle, which is the honest fallback.

    The **frame size** is a different matter, and used to be lumped in with the monitor
    for no better reason than that they arrive together. It is not unknowable: the
    player ffprobed every file on the way in, and §1.3's own example index carries
    `"size": [1920, 1080]` on every entry. Throwing the measurement away cost the
    reopened set its shape - `Recording.load` falls back to a nominal 1920x1080 - so a
    portrait camera paired with a landscape one came back as two 16:9 windows and the
    layout chooser quoted a percentage it had computed from a number nobody measured.
    So `sizes` is written when the caller knows it and the key is simply left out when
    it does not, because an entry with no size reads as "unknown" and an entry with the
    wrong one reads as a fact.

    The **frame rate** is the same kind of measurement and was lost the same way.
    `Recording.load` reads a top-level `"framerate"` and falls back to
    `engine.DEFAULT_FRAMERATE`, which is 60 because that is what *this recorder* writes;
    a pair of 25 fps cameras saved and reopened therefore came back at 60, and
    `SyncEngine`'s frame step - the one control §2.3 of `docs/PLAYER_SOURCES.md` lines
    two clapperboards up with - moved **17 ms instead of 40**, measured on a 25 fps pair
    made with the bundled ffmpeg. One rate is written for the set, because
    `engine.Recording` carries one: the caller picks it, and `open.save_set` picks the
    rate of the file carrying the sound, which is the stream the clock runs on.

    Each file is written as a bare name when it sits beside the index and as an absolute
    path when it does not, so a pair saved in one folder stays movable as a folder.

    `offsets` and `sizes` are padded and truncated to the number of files, so a caller
    that has not got round to either can leave it out. A `framerate` that is not a
    positive number leaves the key out, the same way an unmeasured size leaves `"size"`
    out. Returns a problem for the user, or "" when the file was written.
    """
    target = Path(path)
    if not files:
        # Read in a message box beside the player's Save button, so it is the viewer's
        # sentence and not a developer's: translated, like every other refusal §1.3
        # can put in front of somebody who has just paired two cameras by hand.
        return _("There is nothing to save: this set has no files.")

    folder = target.parent
    slid = list(offsets or [])
    slid = [int(v) for v in slid[: len(files)]]
    slid.extend([0] * (len(files) - len(slid)))

    measured = list(sizes or [])
    measured = measured[: len(files)]
    measured.extend([None] * (len(files) - len(measured)))

    screens = []
    for index, one in enumerate(files):
        entry: dict[str, object] = {
            "file": _index_spelling(folder, Path(one)),
            "monitor": index,
        }
        frame = _frame_size(measured[index])
        if frame is not None:
            entry["size"] = list(frame)
        entry["offset_ms"] = slid[index]
        screens.append(entry)

    # An audio index that is not one of the files would leave the player hunting for a
    # stream that is not there, and `Recording.load` would quietly fall back to the
    # first. Falling back here as well keeps the file and the loader saying one thing.
    chosen = audio_index if 0 <= audio_index < len(screens) else 0
    document: dict[str, object] = {
        "version": SIDECAR_VERSION,
        "recorded": datetime.now().isoformat(timespec="seconds"),
        "audio": screens[chosen]["file"],
        "screens": screens,
    }
    rate = _frame_rate(framerate)
    if rate is not None:
        document["framerate"] = rate
    try:
        target.write_text(
            json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        return _(
            "The recording's index could not be written: {reason}"
        ).format(reason=exc)
    return ""


def _frame_size(value: object) -> tuple[int, int] | None:
    """`value` as a width and a height, or None for anything that is not one.

    Refused rather than repaired, and silently: a caller that could not measure a file
    passes None for it, and the entry is then written without a `"size"` at all. That
    is the one spelling `Recording.load` reads as "nobody knows", and it is a better
    answer than a pair of zeroes or a nominal rectangle presented as a measurement.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        width, height = int(value[0]), int(value[1])
    except (TypeError, ValueError, OverflowError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _frame_rate(value: object) -> float | None:
    """`value` as frames per second, or None for anything that is not a rate.

    Refused rather than repaired, exactly as `_frame_size` refuses a size: a caller
    that could not measure the rate passes None, the key is left out, and
    `Recording.load` then uses its own default and says nothing. `float` accepts
    `nan` and `inf`, and either one written into the index would come back as a frame
    step of `max(1, int(round(1000 / rate)))` on a number that is not one - so both are
    refused here by the same comparison that refuses 0 and a negative.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0.0 < rate < float("inf"):  # false for nan, inf, 0 and anything negative
        return None
    return rate


def _index_spelling(folder: Path, file: Path) -> str:
    """A bare name for a file beside the index, an absolute path for any other.

    **The file is made absolute first, against the working directory.** A relative path
    reaches here whenever a caller passes one straight through - `open.save_set` keeps
    the spelling it was handed - and a relative path written into an index means
    something else entirely when it is read back: `resolve_file` resolves it *beside the
    index*, not beside the working directory. Measured: with the working directory on
    the footage and the index written one folder along, `вложенная/камера_Б.mp4` went in
    verbatim and `read_sidecar` then looked for it inside the index's own folder and
    reported it missing. Resolving first is what makes the two ends agree, and it is
    also the only way the comparison below can be right: `Path("камера_А.mp4").parent`
    is `.`, which is the working directory and not the index's folder.
    """
    here = Path(file)
    try:
        if not here.is_absolute():
            here = Path.cwd() / here
        beside = here.parent.resolve() == Path(folder).resolve()
    except OSError:
        # A path that cannot be resolved (an unreachable share) is still a path; the
        # absolute spelling is the one that stands a chance of being found again.
        beside = False
    return here.name if beside else str(here)


#: The keys only `MultiSession.write_sidecar` writes: where a monitor sat on the
#: desktop, how it was turned, and whether it was the primary one. `write_set` writes
#: none of them - the files it describes were not recorded by this program - so an
#: index carrying any of them holds the only copy of a take's geometry.
RECORDER_ONLY_KEYS = ("desktop", "rotation", "primary")


def written_by_the_player_for(path: Path, files: Sequence[Path]) -> bool:
    """Is the index at `path` this very set's own, as `write_set` left it? Never raises.

    `open.save_set` asks this before it writes over a name that is already taken, and
    the format is this module's to read: two quite different documents wear
    `SIDECAR_SUFFIX`. A take's own index carries `RECORDER_ONLY_KEYS`, the only copy of
    where its screens were; a set this player saved carries nothing a second Save would
    not write again. Stepping over both was measured as a defect of its own - three
    presses of Save on copies of the real pair from `scratch/twofinal` left
    `..._screen0.screenrec.json`, `..._screen0-2...` and `..._screen0-3...`, and the
    library listed one recording three times.

    Both halves are asked, because either one alone is wrong:

    * **the same files, in the same order**, resolved through `resolve_file` so that a
      bare name beside the index and an absolute path mean what they mean to a reader.
      An index naming some other pair is somebody's work, whatever wrote it;
    * **no recorder-only key anywhere, and `offset_ms` on every entry** - the field §1.3
      of `docs/PLAYER_SOURCES.md` says the recorder never writes. A recorder-made index
      names the same files as the set a viewer just paired out of them, so the file list
      alone would hand its desktop rectangles to `write_set`, which writes none.

    Anything that cannot be read, is not JSON, is not an index, or names another set is
    `False`: a name stepped over costs one file, and a name written through costs
    somebody their recording's geometry.

    It is one small read and a `resolve` per file: measured here at 0.35-0.39 ms an ask
    on a 423-byte index, three runs of a hundred, beside the two `ffprobe` calls
    `save_set` makes in the same press.
    """
    wanted = [Path(f) for f in files]
    if not wanted:
        return False
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Not there, will not answer, not JSON, or not UTF-8 - `UnicodeDecodeError` is
        # a `ValueError`. None of them is an index of ours, and none of them may raise
        # out of here: the caller is a Save button.
        return False
    if not isinstance(raw, dict):
        return False
    screens = raw.get("screens")
    if not isinstance(screens, list) or len(screens) != len(wanted):
        return False
    folder = target.parent
    for entry, file in zip(screens, wanted):
        if not isinstance(entry, dict):
            return False
        if "offset_ms" not in entry:
            return False
        if any(key in entry for key in RECORDER_ONLY_KEYS):
            return False
        named = entry.get("file")
        if not isinstance(named, str) or not named:
            return False
        if not _one_file(resolve_file(folder, named), file):
            return False
    return True


def _one_file(left: Path, right: Path) -> bool:
    """Two spellings of one file, or two files.

    Resolved on both sides, which is the same rule `_index_spelling` writes by: a
    relative path means "beside the working directory" and is made absolute against it.
    A path the filesystem will not resolve is *not* the same file - the safe answer,
    because the only thing this decides is whether something already written may be
    written over.
    """
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def read_sidecar(path: Path) -> tuple[dict, str]:
    """Load a set written by `MultiSession` or by `write_set`. Never raises.

    **An entry that is not an object is not a screen.** It used to be read as one:
    `{"screens": ["камера_А.mp4"]}` - a hand-edited index, or one truncated and
    repaired by somebody - reached `s.get("file")` on a `str` and left here as an
    `AttributeError`, out of a function whose first line promises it never raises.
    `open.scan` has a bare `except Exception` round this call written for exactly that,
    and `Recording.load` ignores such an entry with a note of its own, so the whole
    index was being thrown away over one bad line.

    So the junk is skipped: the files named by the real entries are still checked, and
    the loader is left to tell the viewer which line it could not use. An index where
    *nothing* is an object lists no screens, and is refused by the same sentence as an
    empty list - it would otherwise reach the library as a row that opens nothing.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # The player's own wording for the same refusal, by the same key.
        return {}, _("{name} could not be read: {detail}").format(
            name=Path(path).name, detail=exc
        )
    if not isinstance(raw, dict) or "screens" not in raw:
        return {}, _("{name} is not a ScreenRec recording index.").format(
            name=Path(path).name
        )
    screens = raw.get("screens")
    if not isinstance(screens, list) or not screens:
        return {}, _("{name} lists no screens.").format(name=Path(path).name)
    entries = [s for s in screens if isinstance(s, dict)]
    if not entries:
        return {}, _("{name} lists no screens.").format(name=Path(path).name)
    folder = Path(path).parent
    try:
        missing = [
            s.get("file") for s in entries
            if not resolve_file(folder, str(s.get("file", ""))).is_file()
        ]
    except OSError as exc:
        # `Path.is_file()` only swallows a "not found"; a share that is there and will
        # not answer - `[WinError 64] The specified network name is no longer
        # available`, `[WinError 5] Access is denied` - comes straight back out of it.
        # Measured in `open._answered`, which holds the table. This function says it
        # never raises, and the index itself did read, so the refusal is reported by
        # the same key as an index that could not be opened at all.
        return {}, _("{name} could not be read: {detail}").format(
            name=Path(path).name, detail=exc
        )
    if missing:
        return raw, _(
            "These files are named in the index but are not beside it: {files}"
        ).format(files=", ".join(str(m) for m in missing))
    return raw, ""


def find_sets(folder: Path) -> list[Path]:
    """Every multi-screen recording in a folder, newest first. Never raises.

    **The listing is not worth an exception.** The sort used to call `p.stat()` with
    nothing round it, so one file deleted between the glob and the stat took the whole
    listing down - and a folder that stops answering mid-walk does the same, which on
    Windows is `OSError [WinError 53] Не найден сетевой путь` out of a share that has
    gone. `open.scan` and `library.find_entries` both promise a viewer that they never
    raise and both are this function's callers, so the promise is kept here rather than
    rebuilt in a guard at each call site.

    A file whose date cannot be read sorts as 0 - last, with the oldest - rather than
    dropping out of the list: it is still a recording, and the player says what is
    wrong with it when somebody opens it.
    """
    try:
        if not Path(folder).is_dir():
            return []
        found = [p for p in Path(folder).glob(f"*{SIDECAR_SUFFIX}") if p.is_file()]
    except OSError:
        return []
    return sorted(found, key=_modified_time, reverse=True)


def _modified_time(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
