"""What is in the recordings folder, as rows rather than widgets.

§1 of `docs/PLAYER_SOURCES.md` asks for a list of what the recorder has written - every
`.screenrec.json` set and every loose video, newest first, with its name, date, length
and size - and is equally clear about what it is not: "a list, not a media manager: no
tags, no renaming, no deleting". So this module answers one question, `find_entries`,
and the interface agent builds the list widget on top of it.

**No widgets here, on purpose.** Everything a row shows is decided by arithmetic over
files, and arithmetic is testable without a screen. A list that puts the newest
recording second, or calls a two-screen take one video, is a bug that should fail in a
unit test rather than be noticed by somebody scrolling.

**One scanner, not two.** What counts as a recording - an index wins over the files it
claims, an unreadable index is not offered at all, a loose video is a recording in its
own right - is decided once, in `open.scan`, because `most_recent` has to agree with
this list exactly. A viewer who opens "the newest" from a shortcut and then opens the
library must not see a different file at the top.

**The length costs an ffprobe and the size does not.** A file's size is a `stat`; its
duration has to be decoded out of the container, which is a process per file. So
`find_entries` takes `durations`, and a caller that is building a list in front of a
viewer can ask for the cheap half now and fill the rest in afterwards with
`duration_ms`. A set is measured by its first file alone: the recorder starts its
screens together (17 ms apart, measured - see `multi`), so probing the others would buy
a number that is already known.

Nothing here raises. A folder that cannot be read is an empty list, and a length that
cannot be measured is zero, which every caller has to be ready for anyway because a
video that is still being written has no duration yet.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import multi, paths
from . import open as sources

#: Long enough for a container that has to be scanned, short enough that a folder of
#: broken files does not freeze a window. The player is not waiting on this number.
PROBE_TIMEOUT_S = 10


@dataclass(frozen=True)
class Entry:
    """One row of the library: what it is, what it is called, and how big it is."""

    #: What `open.open_recording` is handed when this row is clicked.
    path: Path
    #: The name a viewer reads - the take's own, without `.screenrec.json` on it.
    name: str
    #: ISO text as the index spells it, or the file's own date. May be `""`.
    recorded: str
    #: Milliseconds, or 0 when it was not measured or could not be.
    duration_ms: int
    #: Every video this row plays, together.
    size_bytes: int
    #: The files themselves, so a caller can pair, re-save or reveal them.
    files: tuple[Path, ...]
    #: True for a `.screenrec.json`, False for a video with no index beside it.
    is_set: bool

    @property
    def screens(self) -> int:
        """How many streams play at once. One, for anything the recorder wrote alone."""
        return len(self.files) or 1


def find_entries(folder: Path | str, *, durations: bool = True) -> list[Entry]:
    """Every recording in `folder`, newest first. Never raises.

    `durations=False` returns the same rows with `duration_ms` left at 0 and no ffprobe
    run at all, for a window that wants to appear before it is complete.
    """
    return [
        _entry(found, durations=durations) for found in sources.scan(Path(folder))
    ]


def _entry(found: sources.Candidate, *, durations: bool) -> Entry:
    files = found.files or (found.path,)
    length = 0
    if durations:
        # The first file, not the longest: the screens of a take start together, and a
        # process per file would make opening the library cost more than playing it.
        length = duration_ms(files[0])
    return Entry(
        path=found.path,
        name=display_name(found.path),
        recorded=found.recorded,
        duration_ms=length,
        size_bytes=sum(_size(path) for path in files),
        files=tuple(files),
        is_set=found.is_set,
    )


def display_name(path: Path) -> str:
    """What the row is called: the take's name, with neither suffix on it.

    `Path.stem` is wrong for an index - `take.screenrec.json` has the stem
    `take.screenrec` - and a list in which every set is called "something.screenrec" is
    a list that shows the file format in the place where the name should be.
    """
    name = path.name
    if name.endswith(multi.SIDECAR_SUFFIX):
        return name[: -len(multi.SIDECAR_SUFFIX)]
    return path.stem


def _size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        # Named by an index but not there, or on a drive that has gone. The row is
        # still worth showing: opening it says which file is missing, by name.
        return 0


def duration_ms(path: Path) -> int:
    """How long a video is, in milliseconds, or 0 when it cannot be read.

    Zero means "not known" and never "empty": a file still being written, a container
    with no duration in its header, and an ffprobe that is not installed all land here,
    and a row that shows no length is better than a row that claims 0:00.

    **A duration that is not a finite number is "not known" as well.** `float()` takes
    `inf` as happily as it takes `12.5`, and a header saying so used to come out of this
    module as `OverflowError: cannot convert float infinity to integer` - measured, in
    the interpreter: `int(float("inf") * 1000)` and `int(float("1e400") * 1000)` both
    raise it - out of a function whose first line says it returns 0 when the length
    cannot be read, and into `find_entries`, which says it never raises. `nan` never got
    that far, because `nan > 0` is False.
    """
    try:
        exe = paths.ffprobe_exe()
    except paths.BinaryNotFound:
        # Said by `open._probe_video` already whenever a file is actually opened; a
        # library row is not the place to repeat it on every line.
        return 0

    command = [
        str(exe),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            creationflags=sources.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return 0

    first = (result.stdout or "").strip().splitlines()
    if not first:
        return 0
    try:
        seconds = float(first[0].strip())
    except ValueError:
        # "N/A", which ffprobe prints for a container whose header has no duration.
        return 0
    if not 0.0 < seconds < float("inf"):  # false for 0, a negative, nan and inf
        return 0
    return int(seconds * 1000)
