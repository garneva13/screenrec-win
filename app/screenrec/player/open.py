"""Working out what a path is, and handing it to the player.

Three things arrive at the player and all of them have to work, because a viewer does
not know or care which one they double-clicked:

* a ``.screenrec.json`` index - the real case, several screens played as one;
* a plain video file - a single-screen recording, which `multi` never wrote an index
  for. Refusing it with "that is not a recording set" would be technically true and
  useless, so it opens in the same player with one view;
* a folder - the newest recording in it, which is what "open my recordings" means;
* **a list of paths** - §1 of `docs/PLAYER_SOURCES.md`, two cameras that nothing paired
  and no index describes. `recording_from_files` builds the `engine.Recording` that a
  sidecar would otherwise have described, and `save_set` writes that description back
  out so the pairing survives the window being closed.

Nothing here raises. Every path out of `open_recording` is a pair, and the string is the
only place a problem is ever reported. A non-empty string next to a window is a *note*,
not a failure: a set whose third file was deleted still opens with the other two and says
which one is gone (see `engine.Recording.load`), and that is worth saying rather than
swallowing.

**The three layers.** This module knows files and nothing else. It loads an
`engine.Recording`, hands that to `engine.SyncEngine` - the clock that keeps the files in
step - and hands the clock to the window layer, which owns every widget. Those are
imported inside the function that needs them, so `load` and `most_recent` stay usable
with no Qt windowing at all, and a window layer that will not import is a sentence
rather than an `ImportError` at the top of the file.

**This is also the door onto §2 of `docs/PLAYER_LAYOUTS.md`, and it is the only one.**
Both ways into the player - `run` for the standalone and `ui/app.py`'s "watch last" -
come through `open_recording`, so a layout chosen anywhere else would be chosen nowhere:
until this module asked, `chooser.LayoutChooser` and `stage.PlayerStage` were two
finished modules that nothing in the program ever built, and a two-screen recording
opened straight into separate windows with no way to get the two pictures into one. So
the order here is the contract's own:

1. `load` the recording - files only, no clock and no window yet;
2. **ask**, through `chooser.choose`, *before playback starts* (§2.1). It answers
   without a dialog in the three cases §2.3 and §1 give it: one stream, a saved choice
   whose display signature still matches, and a platform where nobody can click;
3. build the clock, and then the one window the answer needs - `stage.PlayerStage` for
   the three stage layouts of §3, `window.PlayerWindows` for `separate` and
   `one_per_monitor`;
4. **remember**, if the viewer ticked the box, and keep remembering: the layout's own
   state (the swap, the splitter, the arrangement) is written back as they change it,
   not only as it was at the first frame.

Two decisions §2.3 leaves open, taken here:

* **Nothing is written to the settings file unless «Запомнить мой выбор» was ticked.**
  The alternative - storing the splitter fraction and the swap always, and only the
  layout id on a tick - is defensible, because §2.3 restores those two only when the
  signature matches anyway. It was not taken: the checkbox is the only
  thing a viewer is shown about any of this, and a player that writes to their settings
  after they declined to be remembered is doing the thing they declined.
* **A remembered `player_tiling` is re-applied only when the saved layout really was
  `separate` and the display signature matches.** `config` cannot store "no scheme
  chosen" - it rejects `""` and writes `equal_lr` back - so the stored value alone
  cannot be told from a default, and applying it on every open would tile two windows
  for a viewer who never asked for a scheme.

A **cancel** - Отмена in §2.1's dialog - comes back as `(None, "")`: no window, and
nothing to say, because the viewer has just said it. It is the one case where the first
value is None and the second is empty, and `run` reads exactly that and exits 0.

**Which of these sentences are translated.** Every string here ends up in two places at
once - on `stderr` through `_say`, and, when there is no console to say it on, in a
message box through `_show_note` or `_complain`. The box is what a viewer actually
reads, so the sentences a viewer can provoke by hand - a file of the wrong kind, a path
that is not there, an argument the player does not take, `--help` - go through `_()`.
**What `_probe_video` says belongs to the viewer too**: a container whose frame rate
ffprobe will not read, and an ffprobe that is not installed at all, are facts about
their file and their machine rather than bugs in this program, and they reached a
Russian window in English because they were written as f-strings, which can never be a
catalogue key. The detail inside the brackets is still the tool's own text, in English,
exactly as `{reason}` is everywhere else here. The ones that can only come from a fault
inside the program - a window layer that would not import, a clock that would not
build, `TypeError` from a pluggable factory - are left in English on purpose: they carry an exception's own text and a class name, they
are a developer's half of a problem, and a half-translated traceback is harder to search
for than an English one. `usage()` is a function rather than the constant it was for the
same reason `_()` is called at the call site: a constant is translated once, at import,
and the language can change after that.
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from PySide6 import QtCore, QtWidgets

from .. import config, multi, paths
from ..i18n import _
from . import engine

#: Opened as a single-view recording when there is no index beside them. Deliberately a
#: short list of what this recorder and Windows actually produce.
VIDEO_SUFFIXES: tuple[str, ...] = (".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm")

#: Used for a plain video file when ffprobe cannot be asked for the real one. Only the
#: shape of the window depends on it.
FALLBACK_SIZE: tuple[int, int] = (1920, 1080)

#: A frame rate outside this band is ffprobe answering some other question: `0/0` from a
#: container that names no rate at all, or the 1000 fps a variable-rate file reports as
#: its *ceiling*. Either one would make «+1 кадр» move something that is not a frame, so
#: it is refused here and `engine.DEFAULT_FRAMERATE` is used with a note saying so.
RATE_BAND: tuple[float, float] = (1.0, 1000.0)

CREATE_NO_WINDOW = 0x08000000  # ffprobe must not flash a console under pythonw

def usage() -> str:
    """The `--help` text, in the language in force.

    The command line itself is not translated and never will be: `python -m
    screenrec.player` is what the reader has to type, and a translated argument name is
    an argument that does not work. Only the sentences round it are.
    """
    return "\n".join(
        (
            _("ScreenRec Player - plays a multi-screen recording as one thing."),
            "",
            "  python -m screenrec.player [path]",
            "",
            "  path   " + _("a .screenrec.json index, a video file, or a folder."),
            "         " + _(
                "With no path, the newest recording in the output folder is opened."
            ),
        )
    )

_window_factory_override: Callable[..., Any] | None = None


def use_window_factory(factory: Callable[..., Any] | None) -> None:
    """Pin the window layer instead of searching for it. For tests and for embedding."""
    global _window_factory_override
    _window_factory_override = factory


# --------------------------------------------------------------------- finding a file


@dataclass(frozen=True)
class Candidate:
    """One thing in a folder that the player can open, as `scan` found it.

    Deliberately files and numbers only: `library` turns these into a list a viewer
    reads, and `most_recent` takes the first one. Neither needs a `Recording` built,
    which would open every video in the folder to answer "what is in here".
    """

    #: What `open_recording` is handed: an index, or the video itself.
    path: Path
    #: Every video this entry plays - one for a loose file, several for an index.
    files: tuple[Path, ...]
    #: ISO text, from the index when it names one and the file's date otherwise.
    recorded: str
    #: `st_mtime` of `path`, which is what "newest" is sorted on.
    modified: float

    @property
    def is_set(self) -> bool:
        return self.path.name.endswith(multi.SIDECAR_SUFFIX)


def _answered(path: Path, question: Callable[[Path], bool]) -> tuple[bool, str]:
    """`question(path)`, and the sentence to say when the filesystem refuses to answer.

    **`Path.exists()` and its two neighbours are only safe for files that are not
    there.** `pathlib` swallows a "not found"-shaped error and re-raises everything
    else, which is how a folder on a share came to raise out of three functions whose
    docstrings promise they never do. Measured on this machine, patching `Path.stat`
    with the errors Windows really gives:

        FileNotFoundError [WinError 53] The network path was not found
            is_file() -> False, exists() -> False    (a host that is not there)
        PermissionError  [WinError 5]  Access is denied
            is_file() -> raises, exists() -> raises
        OSError          [WinError 64] The specified network name is no longer available
            is_file() -> raises, exists() -> raises

    **A host that is not there answers, and slowly, and the seconds are not ours.**
    Measured here with `Path.exists()` on ten UNC names nothing had ever asked about:
    5.41-5.45 s each, every one `False` and not one raise; the same name asked again
    came back in under 1 ms. A reviewer measured 1.27 s for the same thing on another
    day, and this comment said 2.5 s before that - all three are a name lookup and its
    cache, not a property of this code. What the table is about, and what did hold in
    every measurement, is that the answer arrives as `False` rather than as an
    exception.

    So a share that has gone *missing* is quiet and a share that is there and will not
    answer - dropped mid-session, or one whose permissions have changed - took
    `open.load`, `open.scan`, `open.save_set`, `recording_from_files` and
    `multi.read_sidecar` down with it. Every one of them says it never raises.

    The refusal carries the operating system's own words, by the key `load` already
    uses for a file it cannot read, because "There is no file at X" is the wrong
    sentence for a file that may well be there.
    """
    try:
        return bool(question(Path(path))), ""
    except OSError as exc:
        return False, _("{name} could not be read: {detail}").format(
            name=Path(path).name or str(path), detail=exc
        )


def _is_dir(path: Path) -> tuple[bool, str]:
    """`(is it a folder, the refusal)`. See `_answered`."""
    return _answered(path, Path.is_dir)


def _is_file(path: Path) -> tuple[bool, str]:
    """`(is it a file, the refusal)`. See `_answered`."""
    return _answered(path, Path.is_file)


def _exists(path: Path) -> tuple[bool, str]:
    """`(is it there at all, the refusal)`. See `_answered`."""
    return _answered(path, Path.exists)


def _beside(folder: Path, name: str) -> Path:
    """Where one index entry's `"file"` really is. §1.3 of `docs/PLAYER_SOURCES.md`.

    A bare name resolves beside the index, as every recorder-written set expects; an
    absolute path is taken as it stands, because two cameras are not guaranteed to live
    in one folder. `Path.__truediv__` already returns the right-hand side when it is
    absolute, so this is the same rule `engine._screen_from_entry` applies.
    """
    return folder / Path(name.strip())


def scan(folder: Path) -> list[Candidate]:
    """Everything in `folder` the player can open, newest first. Never raises.

    A multi-screen take is its index, not one of its files, so the files an index claims
    are taken out of the running first - otherwise "open the newest thing" would open
    one screen of a two-screen recording, which is exactly the thing this player exists
    to stop happening. What is left is single-screen recordings, and the newest of
    either kind wins. An index wins a tie, because it is written after its files.

    An index nobody can read is not a candidate. This is the no-argument launch - the
    shortcut's whole purpose - so one bad file in the output folder must not make the
    folder unopenable: it is dropped here, and whatever is playable beside it wins
    instead. An index that *did* read but names a file that has gone is kept, because
    that set still opens with the screens it has left and says which one is missing.

    This is the one scanner. `most_recent` is its first element and `find_entries` in
    `library` is the whole list dressed for a viewer; a second walk of the same folder
    would be a second set of rules about what a recording is, and the two would drift.
    """
    root = Path(folder)
    if not _is_dir(root)[0]:
        # A folder that will not answer lists nothing, exactly as one that is not there
        # does: this is the no-argument launch, and it has to end in a window.
        return []

    try:
        sets = multi.find_sets(root)
    except OSError:
        # `find_sets` keeps this promise itself now - it swallows the walk and dates a
        # file it cannot stat as 0 - but this guard stays: it is one line, and "never
        # raises" is what a viewer with no console is owed whatever a sibling does
        # next. The loose files below are worth finding either way.
        sets = []

    found: list[Candidate] = []
    claimed: set[str] = set()
    for sidecar in sets:
        try:
            raw, _problem = multi.read_sidecar(sidecar)
        except Exception:
            # `multi` is finished and reads the index trustingly: a `screens` entry
            # that is not a dictionary - a hand-edited or truncated file - throws
            # inside it. One such file anywhere in the output folder must not stop the
            # player opening anything at all.
            continue
        if not raw:
            # `read_sidecar` refused it outright: bad JSON, or not an index. Offering
            # it as "the newest recording" would hide every playable file beside it.
            continue
        files: list[Path] = []
        entries = raw.get("screens") if isinstance(raw, dict) else None
        for entry in entries or []:
            if isinstance(entry, dict):
                name = str(entry.get("file") or "").strip()
                if name:
                    resolved = _beside(root, name)
                    files.append(resolved)
                    if resolved.parent == root:
                        # Only a file that really is in this folder can be in the loose
                        # listing below, and an index naming `D:/shoot/b.mp4` must not
                        # hide an unrelated `b.mp4` sitting here.
                        claimed.add(resolved.name.lower())
        found.append(
            Candidate(
                path=sidecar,
                files=tuple(files),
                recorded=str(raw.get("recorded") or "") or _recorded_text(sidecar),
                modified=_modified(sidecar),
            )
        )

    try:
        loose = [
            path
            for path in root.iterdir()
            if path.is_file()
            and path.suffix.lower() in VIDEO_SUFFIXES
            and path.name.lower() not in claimed
        ]
    except OSError:
        # Unreadable folder: the indexes above may still have been found, so carry on
        # with those rather than pretending the folder is empty.
        loose = []

    found.extend(
        Candidate(
            path=path,
            files=(path,),
            recorded=_recorded_text(path),
            modified=_modified(path),
        )
        for path in loose
    )
    # An index wins a tie with a video of the same age, so the second key is its own
    # flag rather than the file's name.
    found.sort(key=lambda found: (found.modified, found.is_set), reverse=True)
    return found


def _modified(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def most_recent(folder: Path) -> Path | None:
    """The newest recording in `folder`, or None if there is nothing to play."""
    found = scan(folder)
    return found[0].path if found else None


# ------------------------------------------------------------------ loading a recording


def load(path: Path) -> tuple[engine.Recording | None, str]:
    """Turn a path into something playable. Never raises.

    Public because it is the headless half of `open_recording`: everything here can be
    checked without a window.
    """
    target = Path(path)

    folder, refusal = _is_dir(target)
    if refusal:
        return None, refusal
    if folder:
        newest = most_recent(target)
        if newest is None:
            return None, _("There is no recording in {folder}.").format(folder=target)
        target = newest

    there, refusal = _exists(target)
    if refusal:
        return None, refusal
    if not there:
        return None, _("There is no file at {path}.").format(path=target)
    file, refusal = _is_file(target)
    if refusal:
        return None, refusal
    if not file:
        return None, _("{path} is not a file.").format(path=target)

    if target.name.endswith(multi.SIDECAR_SUFFIX):
        try:
            return engine.Recording.load(target)
        except Exception as exc:
            # `Recording.load` already guards the trusting reader in `multi` and does
            # not raise today. This is the belt beside those braces: "never raises" is
            # this module's whole contract with a viewer who has no console to read a
            # traceback in, and it should not depend on a sibling keeping its promise.
            return None, _("{name} could not be read: {detail}").format(
                name=target.name, detail=exc
            )
    if target.suffix.lower() in VIDEO_SUFFIXES:
        return _single_file(target)

    return None, _(
        "{name} is not something the player can open. It opens a ScreenRec "
        "index ({index}) or a video file ({videos})."
    ).format(
        name=target.name,
        index=multi.SIDECAR_SUFFIX,
        videos=", ".join(VIDEO_SUFFIXES),
    )


def _single_file(path: Path) -> tuple[engine.Recording, str]:
    """One video as a one-screen recording, so the same player can show it.

    The frame rate is the file's own, not `engine.DEFAULT_FRAMERATE`: that default is
    what *this recorder* writes, and a camera's 25 fps clip stepped at 60 moves 17 ms
    at a time - two thirds of a frame - while the transport says it moved one.
    """
    probe, note = _probe_video(path)
    screen = engine.Screen(
        file=path,
        monitor=0,
        desktop=(0, 0, probe.width, probe.height),
        size=(probe.width, probe.height),
        rotation=0,
        primary=True,
    )
    recording = engine.Recording(
        screens=[screen],
        audio_file=path,
        recorded=_recorded_text(path),
        folder=path.parent,
        framerate=probe.framerate,
    )
    return recording, note


def _recorded_text(path: Path) -> str:
    """When the file was written - the nearest thing a lone video has to a date."""
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime)
    except (OSError, OverflowError, ValueError):
        return ""
    return stamp.isoformat(timespec="seconds")


@dataclass(frozen=True)
class _Probe:
    """What one video says about itself: the frame size, and the frame rate.

    Both answers come out of a single ffprobe run on purpose. The size decides the shape
    of the window; the rate decides what «+1 кадр» moves - §2.1 of
    `docs/PLAYER_SOURCES.md` says frame stepping "uses the reference stream's rate and
    says so", and §2.3's whole workflow is nudging a frame at a time until two
    clapperboards match, so a frame has to be a frame. A second run per file would
    double the cost of opening a pair to learn what the first run was already holding.

    **`measured` says whether these are the file's numbers or the stand-ins.** The
    fields alone cannot be asked: `FALLBACK_SIZE` is 1920x1080, which is also the
    commonest real frame size there is, and `DEFAULT_FRAMERATE` is 60, which is what
    this recorder writes. `save_set` has to tell them apart, because §1.3's index says
    "unknown" by leaving the key out and would otherwise write a guess into a file that
    is read as a measurement for ever after.
    """

    width: int
    height: int
    framerate: float
    #: False when `FALLBACK_SIZE` was used because ffprobe could not say.
    measured_size: bool = True
    #: False when `engine.DEFAULT_FRAMERATE` was used for the same reason.
    measured_rate: bool = True


def _probe_video(path: Path) -> tuple[_Probe, str]:
    """What ffprobe says about `path`, or the stand-ins and a sentence saying why.

    Nothing here is a refusal: a player that will not open a file because ffprobe was
    missing would be absurd. The two stand-ins are wrong in different ways, so they get
    their own notes - a window sized 1920x1080 for a 4K file is only the wrong shape,
    while a frame rate that is not the file's makes the frame nudge move something other
    than a frame, which is the one measurement §2.3 needs to be exact.

    **The fields are read by name.** `-of csv` prints them in ffprobe's own order rather
    than in the order they were asked for, so a rate parsed where the height was
    expected would size a window 25 px tall.

    `r_frame_rate` first and `avg_frame_rate` behind it: the first is the stream's own
    time base, which is what a frame step has to land on, and the second is only a
    measured average - but it is there for the containers that leave `r_frame_rate` as
    `0/0`, where an average is better than this recorder's own 60.
    """
    fallback = _Probe(
        FALLBACK_SIZE[0],
        FALLBACK_SIZE[1],
        engine.DEFAULT_FRAMERATE,
        measured_size=False,
        measured_rate=False,
    )
    try:
        exe = paths.ffprobe_exe()
    except paths.BinaryNotFound as exc:
        return fallback, _("The video's size could not be read: {reason}").format(
            reason=exc
        )

    command = [
        str(exe),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
        "-of", "default=noprint_wrappers=1",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return fallback, _("{name}: ffprobe could not be run ({reason}).").format(
            name=path.name, reason=exc
        )

    fields = _probe_fields(result.stdout or "")
    width, height = _as_int(fields.get("width")), _as_int(fields.get("height"))
    rate = _as_rate(fields.get("r_frame_rate")) or _as_rate(fields.get("avg_frame_rate"))

    notes: list[str] = []
    measured_size = True
    measured_rate = True
    if width <= 0 or height <= 0:
        detail = (
            (result.stderr or "").strip() or (result.stdout or "").strip() or "no output"
        )
        width, height = FALLBACK_SIZE
        measured_size = False
        notes.append(
            _(
                "{name}: the frame size could not be read ({reason}); the window is "
                "sized {width}x{height}."
            ).format(
                name=path.name,
                reason=detail,
                width=FALLBACK_SIZE[0],
                height=FALLBACK_SIZE[1],
            )
        )
    if rate <= 0.0:
        rate = engine.DEFAULT_FRAMERATE
        measured_rate = False
        notes.append(
            _(
                "{name}: the frame rate could not be read; a frame step uses {rate} fps."
            ).format(name=path.name, rate=f"{rate:g}")
        )
    return (
        _Probe(width, height, rate, measured_size, measured_rate),
        "  ".join(notes),
    )


def _probe_fields(text: str) -> dict[str, str]:
    """ffprobe's `key=value` lines as a dictionary. Anything else is ignored."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def _as_rate(raw: Any) -> float:
    """`"30000/1001"` or `"25"` as frames per second; 0.0 when it is neither.

    ffprobe writes a rate as an exact rational, so 29.97 fps arrives as `30000/1001`
    and `float()` on its own would refuse the commonest frame rate in the world.
    """
    text = str(raw or "").strip()
    if not text:
        return 0.0
    top, slash, bottom = text.partition("/")
    try:
        value = float(top) / float(bottom) if slash else float(top)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
    low, high = RATE_BAND
    return value if low <= value <= high else 0.0


# ----------------------------------------------------- §1: files nobody paired for us


def recording_from_files(
    files: Sequence[str | Path], *, audio_index: int = 0
) -> tuple[engine.Recording | None, str]:
    """Any videos at all, as one `engine.Recording`. Never raises.

    §1 of `docs/PLAYER_SOURCES.md`: two cameras pointed at one thing are two files that
    nothing wrote an index for, and the player has to be able to build a set out of
    them. `engine.Recording` is otherwise only ever built by reading a sidecar, so this
    is the other door into the same room - and it is here rather than in `engine`
    because everything it does is file work: probing a video, deciding what is missing,
    and saying so in a sentence.

    **The desktop rectangles are invented, and they have to be.** Nothing recorded these
    files together, so there is no arrangement to restore: the screens are laid out left
    to right at their own frame size, which is what `layouts` then scores and what
    `one_per_monitor` then spreads across the desk. Any other guess - stacking them,
    overlapping them - would be the same amount of invention and worse to look at.

    A file that is not there, or that is not a video, is **named** and left out; the
    rest still open, exactly as a sidecar missing one of its three files still opens.
    None of them opening is the only failure, and it comes back as `(None, sentence)`.

    `audio_index` counts the files that **opened**, not the files that were asked for,
    and anything outside that range is read as the first one. §1.2 is explicit that the
    choice belongs to the viewer rather than to the file order, so this is a default and
    not a rule: the transport moves it afterwards.

    **Whose frame rate the set gets.** `engine.Recording` carries one `framerate` and
    two cameras need not share one, so one of them has to be picked: it is the rate of
    the stream carrying the sound, because that stream is the clock. `SyncEngine` reads
    the timeline off the master, so a step of one of *its* frames lands on a frame
    boundary and a step of the other camera's does not. §2.1 calls it "the reference
    stream", which for a pair nobody paired is not a fixed thing - the sync panel lets
    the viewer move either one - so the clock's own stream is the only answer here that
    is not arbitrary. `engine.DEFAULT_FRAMERATE` is used only when ffprobe could not
    say, and `_probe_video` says so in a note when that happens.
    """
    notes: list[str] = []
    screens: list[engine.Screen] = []
    rates: list[float] = []
    left = 0
    for raw in files or ():
        path, why = _as_path(raw)
        if path is None:
            notes.append(why)
            continue
        there, refusal = _exists(path)
        if refusal:
            # Named and left out, like a file that is not there: the other camera
            # still opens, which is what this function is for.
            notes.append(refusal)
            continue
        if not there:
            notes.append(_("There is no file at {path}.").format(path=path))
            continue
        a_file, refusal = _is_file(path)
        if refusal:
            notes.append(refusal)
            continue
        if not a_file or path.suffix.lower() not in VIDEO_SUFFIXES:
            notes.append(
                _("{name} is not a video file, so it was left out.").format(
                    name=path.name
                )
            )
            continue
        probe, trouble = _probe_video(path)
        if trouble:
            notes.append(trouble)
        screens.append(
            engine.Screen(
                file=path,
                monitor=len(screens),
                desktop=(left, 0, probe.width, probe.height),
                size=(probe.width, probe.height),
                rotation=0,
                primary=not screens,
            )
        )
        rates.append(probe.framerate)
        left += probe.width

    if not screens:
        notes.append(_("None of those files could be opened."))
        return None, "  ".join(n for n in notes if n)

    chosen = audio_index if 0 <= audio_index < len(screens) else 0
    recording = engine.Recording(
        screens=screens,
        audio_file=screens[chosen].file,
        recorded=_recorded_text(screens[0].file),
        folder=screens[0].file.parent,
        framerate=rates[chosen],
    )
    return recording, "  ".join(n for n in notes if n)


def _as_path(raw: Any) -> tuple[Path | None, str]:
    """One item of whatever a caller passed, as a path - or why it is not one.

    A drop carries whatever the window manager put on the clipboard and a host may pass
    anything at all, so this is the edge where "never raises" is actually enforced.
    """
    if raw is None:
        # `str(None)` is a perfectly good relative path called "None", which would be
        # reported as a file that is not there rather than as the nothing it is.
        return None, _("No recording was named.")
    try:
        text = os.fsdecode(raw).strip()
    except (TypeError, ValueError) as exc:
        # `os.fsdecode` takes a string, bytes or anything with `__fspath__`, which is
        # every shape a dialog or a drop produces. Anything else is somebody's bug.
        return None, _("{path} could not be opened.").format(path=f"{raw!r} ({exc})")
    if not text:
        return None, _("No recording was named.")
    return Path(text).expanduser(), ""


# ------------------------------------------------------------------ §1.3: saving a set


def save_set(
    files: Sequence[str | Path],
    *,
    audio_index: int = 0,
    offsets: Sequence[int] | None = None,
    sizes: Sequence[Sequence[int] | None] | None = None,
    framerate: float | None = None,
    target: str | Path | None = None,
) -> tuple[Path | None, str]:
    """Write the open pair back out as a `.screenrec.json`. Never raises.

    §1.3: two files, an audio choice and an offset are work, and work that dies when the
    window closes is work nobody does twice. The index goes **beside the first file**
    unless `target` names somewhere else, and the path it was actually written to comes
    back, because the viewer is about to be told where their set is.

    The writing itself is `multi.write_set`'s - this module does not know the format and
    must not grow a second opinion about it. What is here is the part that is file work:
    which files really exist, which offsets go with them, and a name that does not write
    over somebody else's index - but does write over this set's own, because Save is not
    Save As and pressing it twice is not two sets. See `_free_index_name`.

    `offsets` is in milliseconds and lines up with `files`; a short list is read as
    zeroes, which is the one-camera case and the recorder's own.

    **The frame sizes and the frame rate are carried through, and measured when nobody
    hands them over.** `write_set` has taken `sizes` since it was written and this call
    site never passed them, so everything a saved set knew about its own shape was lost
    on the way out: the real two-screen pair in `scratch/twofinal` went in as
    (2560, 1440) beside (1440, 2560) and came back from its own index as two 1920x1080
    screens, because that is `Recording.load`'s stand-in for an entry with no size. The
    frame rate went the same way through a parameter `write_set` did not have at all - a
    25 fps pair reopened at 60, and the frame step §2.3 lines two clapperboards up with
    moved 17 ms instead of 40. Both measured.

    `sizes` and `framerate` are the caller's measurements when it has them - the window
    layer already probed every file on the way in - and are otherwise taken here, with
    one `ffprobe` per file. A file that cannot be probed contributes **no** size rather
    than `FALLBACK_SIZE`: the index's way of saying "nobody knows" is to leave the key
    out, and 1920x1080 written as a fact would be a measurement nobody made. The rate
    is the one carrying the sound, because `engine.Recording` holds one rate and that
    is the stream `SyncEngine` runs its clock on.
    """
    kept, shifts, notes = _set_to_write(files, offsets)
    if not kept:
        notes.append(_("There is nothing to save: none of those files are there."))
        return None, "  ".join(n for n in notes if n)

    writer = getattr(multi, "write_set", None)
    if not callable(writer):
        # English on purpose, like every other sentence here that can only come from a
        # fault inside the program: `multi` not exporting its own writer is a developer's
        # half of a problem, not a viewer's.
        return None, (
            "screenrec.multi exports no write_set, so a set cannot be saved."
        )

    if target is not None:
        where, refusal = _free_index_name(Path(str(target)).expanduser(), kept)
    else:
        where, refusal = _free_index_name(
            kept[0].with_suffix(multi.SIDECAR_SUFFIX), kept
        )
    if refusal:
        return None, refusal
    if where is None:
        return None, _(
            "A name could not be found for the set beside {name}; every one is taken."
        ).format(name=kept[0].name)
    folder, refusal = _is_dir(where.parent)
    if refusal:
        return None, refusal
    if not folder:
        return None, _("{folder} is not a folder, so the set was not saved.").format(
            folder=where.parent
        )

    # Measured only once there is somewhere to write: a refusal above costs no ffprobe.
    chosen = audio_index if 0 <= audio_index < len(kept) else 0
    shapes, rate = _shape_of_the_set(kept, sizes, framerate, chosen)

    try:
        problem, dated = _write_through(
            writer, where, kept, audio_index, shifts, shapes, rate
        )
    except Exception as exc:  # noqa: BLE001 - "never raises" has to mean it
        # `write_set` is being written in the same round of work as this call site, so
        # a signature that has moved under us must be a sentence beside a Save button
        # rather than a traceback nobody sees.
        return None, _("The set could not be saved: {reason}").format(
            reason=f"{type(exc).__name__}: {exc}"
        )
    if problem:
        notes.append(str(problem))
        return None, "  ".join(n for n in notes if n)
    if not dated:
        # The writer takes no date of its own, so it stamped the index with the moment
        # Save was pressed. §1.3's index carries the shoot's date, not the save's.
        trouble = _stamp_recorded(where, _recorded_text(kept[0]))
        if trouble:
            notes.append(trouble)
    return where, "  ".join(n for n in notes if n)


def _set_to_write(
    files: Sequence[str | Path], offsets: Sequence[int] | None
) -> tuple[list[Path], list[int], list[str]]:
    """The files that are really there, their offsets, and what was left out.

    The offsets are carried through the same filter as the files: dropping a file
    without dropping its offset would shift every later stream by somebody else's
    number, which is the kind of bug that looks like a drifting camera.
    """
    given = list(offsets or ())
    kept: list[Path] = []
    shifts: list[int] = []
    notes: list[str] = []
    for position, raw in enumerate(files or ()):
        path, why = _as_path(raw)
        if path is None:
            notes.append(why)
            continue
        a_file, refusal = _is_file(path)
        if refusal:
            # **Kept, not dropped.** A file the filesystem will not answer about may
            # well be there, and leaving it out would quietly save a pair as one
            # camera - the one thing §1.3 exists to prevent. The index names files; a
            # name that turns out to be wrong is reported by `read_sidecar` on the way
            # back in, which is a far cheaper mistake than losing the pairing.
            notes.append(refusal)
        elif not a_file:
            notes.append(
                _("{name} is not there, so it was left out of the set.").format(
                    name=path.name
                )
            )
            continue
        kept.append(path)
        shifts.append(_as_int(given[position]) if position < len(given) else 0)
    return kept, shifts, notes


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _free_index_name(base: Path, files: Sequence[Path]) -> tuple[Path | None, str]:
    """`base` while it is free or this set's own; otherwise `base-2`, `base-3`...

    A pair is usually saved beside a recorder-made take, and that take's index carries
    the only copy of its desktop rectangles. Overwriting it from a button labelled
    «Сохранить набор» would lose a recording's geometry to a gesture that promised to
    save something, so a name that belongs to something else is stepped over rather
    than written through.

    **A name belonging to this very set is not something else.** Stepping over every
    taken name went too far and was measured as a defect of its own: three presses of
    Save on copies of the real pair from `scratch/twofinal` left
    `..._screen0.screenrec.json`, `..._screen0-2.screenrec.json` and
    `..._screen0-3.screenrec.json`, `library.find_entries` then listed one recording as
    three rows, and `most_recent` pointed at the third. A viewer who nudges an offset and presses Save again has not
    made a second set. So `multi.written_by_the_player_for` is asked, and a name held by
    an index that names these same files and carries nothing the recorder writes is used
    where it stands - which is also the whole of what the old code did before the step
    was put in, for the one case it was right about.

    **`target=` goes through here too**, and that is the fix for a hole that was wide
    open: `save_set(target=...)` took the path as given and `write_set` writes with
    `Path.write_text`, so a caller pointing at a folder full of recordings could land
    on somebody else's `.screenrec.json` and replace a two-screen take's desktop
    rectangles with a pair that has none. A `target` naming this set's own index is
    written where it was told, so a Save As dialog that has already asked the viewer
    about an overwrite is not second-guessed; a `target` naming anything else is still
    stepped over, and whoever named it is told where the set really went - `save_set`
    returns the path.

    The step keeps whatever tail the name came with, so `pair.screenrec.json` steps to
    `pair-2.screenrec.json` and not to `pair.screenrec-2.json`, and it stops at the
    first name that is free *or* ours - so a set saved twice beside a take that already
    holds the plain name settles on `-2` instead of climbing.

    `(None, "")` means every name is taken; `(None, sentence)` means the filesystem
    would not say whether they are, which is a different thing to tell the viewer.
    """
    taken, refusal = _exists(base)
    if refusal:
        # The filesystem will not say whether anything is there. Stepping through a
        # thousand names it will not answer about either would only be slow, and
        # writing blind is how somebody else's index gets replaced.
        return None, refusal
    if not taken or _ours_to_write_again(base, files):
        return base, ""
    name = base.name
    if name.endswith(multi.SIDECAR_SUFFIX):
        stem, tail = name[: -len(multi.SIDECAR_SUFFIX)], multi.SIDECAR_SUFFIX
    else:
        stem, tail = base.stem, base.suffix
    for number in range(2, 1000):
        candidate = base.with_name(f"{stem}-{number}{tail}")
        busy, refusal = _exists(candidate)
        if refusal:
            return None, refusal
        if not busy or _ours_to_write_again(candidate, files):
            return candidate, ""
    return None, ""


def _ours_to_write_again(path: Path, files: Sequence[Path]) -> bool:
    """Is the index already at `path` this set's own, so Save may write it again?

    The question is about the index *format*, so it is `multi`'s to answer: `save_set`
    says above that this module does not know the format and must not grow a second
    opinion about it, and "which keys only the recorder writes" is exactly such an
    opinion.

    A `multi` that does not export the answer says no, like every other `getattr` in
    this module - the two files are written in the same round of work - and no is the
    behaviour this call replaced: a taken name is stepped over. Nothing raises out of
    here, because the caller is a Save button.
    """
    asked = getattr(multi, "written_by_the_player_for", None)
    if not callable(asked):
        return False
    try:
        return bool(asked(Path(path), list(files)))
    except Exception:  # noqa: BLE001 - "never raises" has to mean it
        return False


def _shape_of_the_set(
    kept: list[Path],
    sizes: Sequence[Sequence[int] | None] | None,
    framerate: float | None,
    chosen: int,
) -> tuple[list[tuple[int, int] | None], float | None]:
    """What each file looks like and how fast the set runs: `(sizes, framerate)`.

    Whatever the caller measured is believed and nothing is probed; otherwise one
    `ffprobe` per file answers both halves at once, which is the same run `load` makes
    when a file is opened. An ffprobe that could not say leaves a `None` in the list and
    `None` for the rate, and `multi.write_set` then leaves the key out - see `_Probe`
    for why the stand-ins cannot simply be written instead.

    A short `sizes` is padded with `None` rather than refused: a caller that measured
    one of two cameras still knows more than this function would by probing both.
    """
    given = list(sizes) if sizes is not None else []
    shapes: list[tuple[int, int] | None] = [
        _as_frame_size(given[index]) if index < len(given) else None
        for index in range(len(kept))
    ]
    rate = _as_given_rate(framerate)

    for index, path in enumerate(kept):
        if shapes[index] is not None and not (rate is None and index == chosen):
            # Nothing is left to ask about this file, so it is not opened at all.
            continue
        probe, _note = _probe_video(path)
        if shapes[index] is None and probe.measured_size:
            shapes[index] = (probe.width, probe.height)
        if rate is None and index == chosen and probe.measured_rate:
            rate = probe.framerate
    return shapes, rate


def _as_given_rate(value: Any) -> float | None:
    """A caller's own frame rate, or None when it is not a rate at all.

    `None`, 0 and `nan` all mean "nobody measured it", and all three have to come out
    as the same `None` - `multi.write_set` leaves the key out for it, and a `nan` that
    reached the index would come back as a frame step of `1000 / nan`.
    """
    if value is None:
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return rate if 0.0 < rate < float("inf") else None


def _as_frame_size(value: Any) -> tuple[int, int] | None:
    """A caller's own `(width, height)`, or None for anything that is not one.

    The same refusal `multi._frame_size` makes, made here as well so that a caller's
    `(0, 1080)` does not count as measured: it is probed instead, and only then written
    out or left out.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        width, height = int(value[0]), int(value[1])
    except (TypeError, ValueError, OverflowError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _write_through(
    writer: Callable[..., Any],
    where: Path,
    files: list[Path],
    audio_index: int,
    offsets: list[int],
    sizes: list[tuple[int, int] | None] | None = None,
    framerate: float | None = None,
) -> tuple[str, bool]:
    """Call `multi.write_set` by whatever names it takes: `(problem, took the date)`.

    The index path and the files are its first two arguments; everything else is
    offered by name and only the names it actually declares are passed. `write_set` is
    being added by another hand in the same round of work as this module, so a keyword
    spelled `audio_file` there and `audio` here would otherwise be a `TypeError` in
    front of a viewer who has just pressed Save - and the one thing §1.3 is for is not
    losing their work.

    **Offering everything is the whole point, and two things were missing from the
    offer.** `write_set` has declared a `sizes` parameter since the day it was written
    and this call never named it, so the one thing it was added for - a portrait camera
    coming back portrait - never happened; `framerate` was in neither place. Both are
    offered here now, in the same two spellings as the rest, so that a writer which
    takes them gets them and one which does not is unchanged.

    The second value says whether `recorded` was one of the names that got through.
    Today's `multi.write_set` declares no such parameter and dates the index
    `datetime.now()`, so the offer is silently dropped and `save_set` puts the shoot's
    date in afterwards. When the writer does grow the parameter this comes back True
    and nothing touches the file twice.
    """
    chosen = audio_index if 0 <= audio_index < len(files) else 0
    shapes = list(sizes or [])
    offered: dict[str, Any] = {
        "audio": files[chosen],
        "audio_file": files[chosen],
        "audio_index": chosen,
        "offsets": list(offsets),
        "offsets_ms": list(offsets),
        "recorded": _recorded_text(files[0]),
    }
    if any(shape is not None for shape in shapes):
        # All-None means nothing could be measured, and a list of nothing is worth no
        # keyword at all: `write_set` writes the same index either way.
        offered["sizes"] = shapes
        offered["frame_sizes"] = shapes
    if framerate is not None:
        offered["framerate"] = framerate
        offered["frame_rate"] = framerate
    try:
        parameters = inspect.signature(writer).parameters
    except (TypeError, ValueError):  # a C callable with no signature
        return str(writer(where, list(files)) or ""), False
    kinds = inspect.Parameter
    named = {
        name
        for name, parameter in parameters.items()
        if parameter.kind in (kinds.POSITIONAL_OR_KEYWORD, kinds.KEYWORD_ONLY)
    }
    if any(p.kind is kinds.VAR_KEYWORD for p in parameters.values()):
        # A writer taking `**kwargs` declares no names at all, and passing it nothing
        # would drop the audio choice and the offsets in silence - which is the one
        # thing §1.3 cannot afford. Its canonical spellings go through.
        named |= {"audio", "offsets", "recorded", "sizes", "framerate"}
    extra = {name: value for name, value in offered.items() if name in named}
    problem = str(writer(where, list(files), **extra) or "")
    return problem, "recorded" in extra


def _stamp_recorded(where: Path, recorded: str) -> str:
    """Date a written index by the footage rather than by the moment Save was pressed.

    §1.3's example index carries a `"recorded"` that belongs to the shoot, and
    `library.Entry.recorded` is the date the viewer then reads and sorts on: a pair
    saved from last month's footage, dated today, sits in the library above the take it
    was made from. `multi.write_set` declares no `recorded` parameter and writes
    `datetime.now()`, so `_write_through` cannot hand it the date and it goes in here.

    Only that one key is touched, and only when the writer did not take the date itself
    - this module still has no second opinion about the format, it corrects one field of
    a file the writer owns. A failure is a note rather than a refusal: the set *is*
    saved, and a date that is a few weeks out is worth a sentence, not throwing the
    viewer's pairing away.
    """
    if not recorded:
        # `_recorded_text` could not stat the file. There is nothing better to put
        # there than the writer's own `now`, so it stays.
        return ""
    written, refusal = _is_file(where)
    if refusal:
        # The set is on disk as far as the writer is concerned and only its date is in
        # doubt, so this is the half-success sentence rather than a failure.
        return _("The set was saved, but its date could not be set: {reason}").format(
            reason=refusal
        )
    if not written:
        # The writer reported no problem and wrote nothing. That is its business - a
        # pinned test double does exactly this - and there is no index here to date.
        _say(f"player.open: {where} is not there after it was written; its date is "
             "whatever the writer put in it.")
        return ""
    try:
        raw = json.loads(where.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # ValueError covers JSONDecodeError. The index was written by somebody else and
        # may be anything at all; what it must not do is cost the viewer their set.
        return _("The set was saved, but its date could not be set: {reason}").format(
            reason=f"{type(exc).__name__}: {exc}"
        )
    if not isinstance(raw, dict):
        _say(f"player.open: {where.name} is not a JSON object, so its date was left "
             "as the writer put it.")
        return ""
    if str(raw.get("recorded") or "") == recorded:
        return ""
    raw["recorded"] = recorded
    try:
        where.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        return _("The set was saved, but its date could not be set: {reason}").format(
            reason=f"{type(exc).__name__}: {exc}"
        )
    return ""


# ------------------------------------------------------------------- §2: which layout


def choose_layout(
    recording: engine.Recording, settings: Any = None
) -> tuple[str, bool] | None:
    """§2's answer for this recording: `(layout id, remember)`, or None for a cancel.

    A thin seam over `chooser.choose`, which owns every rule in §2.1 and §2.3 and is
    tested against them. What is added here is only the two things a file-level module
    knows: how to turn an `engine.Recording` into the `layouts.Stream`s the chooser
    scores, and what to do when the window layer will not import at all.

    §1 is honoured by the chooser rather than repeated here: a recording with fewer
    than two streams is never asked anything and comes back as None, which this
    module's caller reads as "open it the way it has always opened".
    """
    try:
        from . import chooser, stage
        from .window import screen_areas
    except Exception as exc:
        # The same failure `window_factory` reports, met a step earlier. Opening on
        # the default beats refusing: the viewer asked to watch a recording, not to
        # pick a layout.
        _say(
            "The player's layout chooser could not be loaded "
            f"({type(exc).__name__}: {exc}); opening in separate windows."
        )
        return None

    streams = stage.streams_from(recording)
    return chooser.choose(streams, screen_areas(), settings)


def _stream_count(recording: engine.Recording) -> int:
    """How many screens this recording has, as §1 and §2.1 count them."""
    screens = getattr(recording, "screens", None)
    return len(screens) if isinstance(screens, (list, tuple)) else 0


class _LayoutMemory(QtCore.QObject):
    """§2.3's fields, written back as the viewer changes them.

    Only built when «Запомнить мой выбор» was ticked - see the module docstring for
    why nothing is written otherwise.

    **The write is deferred by `SAVE_DELAY_MS`.** `PlayerStage.split_changed` is wired
    to `QSplitter.splitterMoved`, which fires on every mouse move of a drag; saving on
    each would rewrite the settings file dozens of times a second while a handle is
    being dragged. One single-shot timer, restarted on every change, turns a drag into
    one write.

    The timer is parented to the player's window, so it dies with it. A save still
    pending when the player closes is flushed by `_closing`, because the last thing a
    viewer did before closing is exactly the thing they will expect to find next time.
    """

    SAVE_DELAY_MS = 500

    def __init__(self, settings: Any, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.SAVE_DELAY_MS)
        self._timer.timeout.connect(self.flush)

    def set(self, **fields: Any) -> None:
        """Change some of §2.3's fields and schedule the write."""
        for name, value in fields.items():
            if not hasattr(self.settings, name):
                # A field this build of `config` does not have. Said rather than set:
                # a `Settings` object grown an attribute by assignment would be
                # written to disk and read back by nothing.
                _say(f"player.open: settings has no {name!r}; it was not remembered.")
                continue
            setattr(self.settings, name, value)
        self._timer.start()

    def flush(self) -> None:
        """Write now. Safe to call when nothing is pending."""
        self._timer.stop()
        problem = config.save_settings(self.settings)
        if problem:
            # English on purpose: it carries an OSError's own text, and it is the
            # developer's half of a problem the viewer can do nothing about.
            _say(f"player.open: {problem}")

    def _closing(self) -> None:
        if self._timer.isActive():
            self.flush()


def _remember_stage(memory: _LayoutMemory, view: Any) -> None:
    """Wire §3's three signals to the settings file."""
    view.layout_changed.connect(
        lambda layout: memory.set(player_layout=str(layout))
    )
    view.swapped.connect(lambda state: memory.set(player_swapped=bool(state)))
    view.split_changed.connect(
        lambda fraction: memory.set(player_split=float(fraction))
    )
    view.closed.connect(memory._closing)


def _remember_windows(memory: _LayoutMemory, windows: Any) -> None:
    """Wire §5's arrangement to the settings file.

    `tiling_changed` carries only one of `layouts.TILINGS` - never `""` - which is
    what makes it safe to store straight into `player_tiling`, a field `config`
    validates against the same four ids.
    """
    windows.tiling_changed.connect(
        lambda tiling: memory.set(player_tiling=str(tiling))
    )
    windows.closed.connect(memory._closing)


# --------------------------------------------------------------------- finding the view


def window_factory() -> tuple[Callable[..., Any] | None, str]:
    """The callable that turns a `SyncEngine` into windows, or why there is none."""
    if _window_factory_override is not None:
        return _window_factory_override, ""

    try:
        from . import window
    except Exception as exc:
        # Imported here and not at the top of the module so that `load` and
        # `most_recent` work whatever state the widgets are in.
        return None, (
            "The player's window layer could not be loaded: "
            f"{type(exc).__name__}: {exc}"
        )

    factory = getattr(window, "PlayerWindows", None)
    if not callable(factory):
        return None, (
            "screenrec.player.window exports no PlayerWindows, so there is nothing to "
            "open the recording in."
        )
    return factory, ""


def _accepts_parent(factory: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return False  # a C callable with no signature: pass the recording alone
    if "parent" in parameters:
        return True
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )


def _stage_arguments(
    recording: engine.Recording, layout: str, settings: Any
) -> dict[str, Any]:
    """What `stage.PlayerStage` is opened with, under §2.3's signature rule.

    The layout id always; the geometry remembered *inside* a layout - the swap and the
    splitter fraction - only when the display signature matches. §2.3 is explicit about
    the difference, and the reason is in the numbers: a fraction is safe to carry onto a
    different desk and a pixel rectangle is not, but a splitter restored to 0.8 on a
    monitor half the width is a pane below `layouts.MIN_PANE`, which the viewer then
    has to find and drag back.
    """
    from . import layouts

    wanted: dict[str, Any] = {"layout": layout}
    stamp = str(getattr(settings, "player_display_signature", "") or "")
    if not stamp or stamp != layouts.current_signature():
        return wanted
    if str(getattr(settings, "player_layout", "") or "") != layout:
        # A remembered splitter belongs to the layout it was dragged in. Carrying it
        # onto a layout the viewer has only just picked would open their first
        # `side_by_side` at whatever `stacked` was left at.
        return wanted
    wanted["swapped"] = bool(getattr(settings, "player_swapped", False))
    wanted["split"] = float(getattr(settings, "player_split", 0.5))
    return wanted


def _apply_arrangement(windows: Any, layout: str, settings: Any) -> None:
    """§5: open the separate windows in the arrangement the answer asks for.

    Before `show()`, never after: `PlayerWindows.show` places the set by whatever
    arrangement is in force, so setting it here means the windows appear where they
    belong rather than appearing and then jumping.

    Every call is guarded by `getattr`, because the window factory is pluggable and a
    host that pinned its own is not required to have grown §5's methods.
    """
    from . import layouts

    if layout == layouts.LAYOUT_ONE_PER_MONITOR:
        per_monitor = getattr(windows, "apply_one_per_monitor", None)
        if callable(per_monitor):
            # Its own False - fewer monitors than screens - is already a sentence on
            # the transport, and the set then opens in the desktop arrangement, which
            # is the right answer for a desk that cannot do what was asked.
            per_monitor()
        return

    # `separate`. A stored scheme is re-applied only when the viewer really chose
    # `separate` last time on this same desk: `config` cannot hold "no scheme chosen"
    # - it rejects "" and writes `equal_lr` back - so the stored value on its own
    # cannot be told apart from the default, and tiling every viewer's windows
    # because of a default is not a choice anybody made.
    if str(getattr(settings, "player_layout", "") or "") != layouts.LAYOUT_SEPARATE:
        return
    stamp = str(getattr(settings, "player_display_signature", "") or "")
    if not stamp or stamp != layouts.current_signature():
        return
    tiling = str(getattr(settings, "player_tiling", "") or "")
    apply_tiling = getattr(windows, "apply_tiling", None)
    if tiling in layouts.TILINGS and callable(apply_tiling):
        apply_tiling(tiling)


def _view_factory(layout: str) -> tuple[Callable[..., Any] | None, str, bool]:
    """The class this layout opens in: `(factory, problem, is_stage)`.

    A pinned `use_window_factory` wins over everything, including a stage layout. An
    embedder that has supplied its own window layer owns what a recording opens in,
    and handing it a `PlayerStage` it never asked for would be this module overruling
    the host - which is the one thing a pluggable factory exists to prevent.
    """
    if _window_factory_override is not None:
        return _window_factory_override, "", False

    from . import layouts

    if layout not in layouts.STAGE_LAYOUTS:
        factory, problem = window_factory()
        return factory, problem, False

    try:
        from . import stage
    except Exception as exc:
        return None, (
            "The player's stage could not be loaded: "
            f"{type(exc).__name__}: {exc}"
        ), True
    factory = getattr(stage, "PlayerStage", None)
    if not callable(factory):
        return None, (
            "screenrec.player.stage exports no PlayerStage, so the one-window "
            "layouts cannot be opened."
        ), True
    return factory, "", True


def _build_windows(
    recording: engine.Recording,
    parent: Any,
    layout: str = "",
    remember: bool = False,
    settings: Any = None,
) -> tuple[object | None, str]:
    """Clock first, then the window the chosen layout needs, which owns it from then on.

    Anything the clock says at start-up - a file QtMultimedia refused, say - comes back
    as a note beside the windows, because a set that opens with one of its two screens
    black must say which one and why.

    "Own" has to include letting go. Closing the windows stops the clock but does not
    delete it, and a stopped `QMediaPlayer` still holds its file open - on Windows the
    recording then cannot be moved or deleted until the process ends. Standalone that
    is invisible, because the process does end; embedded in the recorder it is a locked
    recording and another whole engine left behind on every open. So the clock's life
    is tied to the windows here, in the layer that made it.

    `layout` is a layout id of §1; `""` means "whatever the window layer opens with",
    which is what a pinned factory and a one-stream recording get. The clock is built
    once whichever window it goes to - **there is one clock, and nothing here may make
    a second** (§6), so the layout decides which widget listens to it and nothing else.
    """
    factory, problem, is_stage = _view_factory(layout)
    if factory is None:
        return None, problem

    try:
        clock = engine.SyncEngine(recording, parent)
    except Exception as exc:
        return None, (
            "The player's clock could not be built: "
            f"{type(exc).__name__}: {exc}"
        )

    notes: list[str] = []
    startup = getattr(clock, "startup_problems", None)
    if callable(startup):
        try:
            notes.extend(str(n) for n in (startup() or []))
        except Exception as exc:
            notes.append(f"The clock could not be asked how it started: {exc}")

    extra = _stage_arguments(recording, layout, settings) if is_stage else {}
    try:
        if parent is not None and _accepts_parent(factory):
            windows = factory(clock, parent=parent, **extra)
        else:
            windows = factory(clock, **extra)
    except Exception as exc:
        name = getattr(factory, "__name__", repr(factory))
        return None, (
            f"The player's windows could not be built by {name}: "
            f"{type(exc).__name__}: {exc}"
        )
    if windows is None:
        name = getattr(factory, "__name__", repr(factory))
        return None, f"{name} made no windows for {recording.folder}."

    if not is_stage and layout:
        _apply_arrangement(windows, layout, settings)
    if remember and settings is not None:
        notes.extend(_start_remembering(windows, layout, settings, is_stage))

    notes.extend(_free_clock_with(windows, clock))
    return windows, "  ".join(notes)


def _start_remembering(
    windows: Any, layout: str, settings: Any, is_stage: bool
) -> list[str]:
    """§2.3: store the choice now, and keep storing what the viewer does to it.

    Written once straight away rather than only on the first change: a viewer who
    ticks the box and then watches the recording without touching anything has still
    chosen, and a choice that is only saved if something is dragged is the kind of
    half-kept promise that teaches people not to tick boxes.

    The signature goes with it, every time. A layout id stored without the desk it was
    measured on is a layout that will be restored onto screens it was never meant for,
    which is the whole of what §2.3's stamp is for.
    """
    from . import layouts

    owner = windows if isinstance(windows, QtCore.QObject) else None
    memory = _LayoutMemory(settings, owner)
    memory.set(
        player_layout=layout,
        player_display_signature=layouts.current_signature(),
    )
    # Flushed rather than left on the timer: this is the choice itself, and a viewer
    # who ticks the box and closes the player a second later has still chosen.
    memory.flush()
    if owner is None:
        # A pinned window layer that is not a `QObject` cannot parent the timer, and
        # a `_LayoutMemory` nothing holds a reference to is collected before the first
        # change ever reaches it - so the viewer's tick would be kept for exactly one
        # write and then stop working, silently. Hung on the object whose life it is
        # meant to share, under a name no window layer in this package uses.
        try:
            windows._screenrec_layout_memory = memory
        except AttributeError as exc:
            return [
                f"{type(windows).__name__} cannot hold the layout memory ({exc}), so "
                "only the layout itself is remembered."
            ]
    try:
        if is_stage:
            _remember_stage(memory, windows)
        else:
            _remember_windows(memory, windows)
    except (AttributeError, RuntimeError, TypeError) as exc:
        # The factory is pluggable and a host may pin a window layer with none of
        # these signals. The layout itself is already stored; what is lost is only
        # the later changes, and that is worth a sentence rather than a silence.
        return [
            f"{type(windows).__name__} does not publish its layout changes "
            f"({type(exc).__name__}: {exc}), so only the layout itself is remembered."
        ]
    return []


def _free_clock_with(windows: Any, clock: Any) -> list[str]:
    """Delete the clock when the windows say they have closed.

    `deleteLater` and not `del`: the signal is emitted from inside the windows' own
    teardown, so the players have to outlive the call that is closing them. One turn of
    the event loop later the engine and every `QMediaPlayer` under it are gone and the
    files are released - measured; `stop()` alone leaves the handles open.

    The factory is pluggable, so a window layer with no `closed` signal is possible.
    That is not an error, but it does mean nothing will free the clock, and this module
    says what it knows rather than leaking in silence.
    """
    closed = getattr(windows, "closed", None)
    connect = getattr(closed, "connect", None)
    delete = getattr(clock, "deleteLater", None)
    if not callable(delete):
        return [
            f"{type(clock).__name__} cannot be deleted, so the recording's files will "
            "stay open until this process ends."
        ]
    if not callable(connect):
        return [
            f"{type(windows).__name__} has no closed signal, so the recording's files "
            "will stay open until this process ends."
        ]
    try:
        connect(delete)
    except (RuntimeError, TypeError) as exc:
        return [
            "The player's clock could not be tied to its windows "
            f"({type(exc).__name__}: {exc}), so the recording's files will stay open "
            "until this process ends."
        ]
    return []


# ---------------------------------------------------------------------------- the door


def _load_any(path: Any) -> tuple[engine.Recording | None, str]:
    """`load` for one path, `recording_from_files` for several. Never raises.

    A list of one is **not** a pair: it goes through `load`, so a folder dropped on the
    window still opens the newest recording in it and a `.screenrec.json` still opens as
    the set it describes. Only from two paths upwards is this §1's two-camera case,
    where every path has to be a video because nothing can pair an index with a file.
    """
    if isinstance(path, (list, tuple)):
        given = list(path)
        if not given:
            return None, _("No recording was named.")
        if len(given) > 1:
            return recording_from_files(given)
        path = given[0]

    single, why = _as_path(path)
    if single is None:
        return None, why
    return load(single)


def empty_player(folder: Path | None = None, parent: Any = None) -> tuple[Any, str]:
    """The player with nothing loaded: the transport, the Open menu, the library.

    Opened when there is nowhere to look or nothing to find, which until 2026-11-05 was
    a refusal: `run` printed "there are no recordings in …" and returned 2, and under
    the shipped `pythonw` launcher that is a shortcut that does nothing when clicked.
    The user deleted their old recordings and reported exactly that.

    Every control a person would reach for next works with no recording loaded - §1's
    `Открыть…`, the library of the output folder, and dropping a file on the window -
    so there was never a reason not to show them.
    """
    recording = engine.Recording(
        screens=[], audio_file=Path(""), recorded="",
        folder=Path(folder) if folder else Path.cwd(),
    )
    return _build_windows(recording, parent)


def open_recording(
    path: Path | str | Sequence[Path | str],
    parent=None,
    *,
    layout: str | None = None,
    settings: Any = None,
) -> tuple[object | None, str]:
    """Open a .screenrec.json set, or a plain video file, and return the window.

    `path` may also be a folder, in which case the newest recording in it is opened, or
    a **list of paths**, in which case they are opened together as one set - §1's two
    cameras, dropped on the window in a single gesture. The single-path call is the one
    `__main__` and the recorder make and it is untouched: one path behaves exactly as it
    did before the list was allowed, including a list holding exactly one path.

    The second value is a problem when the first is None, and a note worth showing when
    it is not - with one exception, documented at the top of this module: a **cancel**
    in §2.1's dialog returns `(None, "")`, because the viewer has already said it.

    `layout` pins §1's layout id and skips the dialog, for a host that has asked the
    question itself and for the tests. `settings` is read for §2.3's remembered fields
    and written back when the viewer ticked the box; left out, the stored settings are
    loaded here, which is what both real callers want.
    """
    if path is None:
        return None, _("No recording was named.")

    if QtWidgets.QApplication.instance() is None:
        return None, (
            "A Qt application has to be running before the player can open a window."
        )

    recording, note = _load_any(path)
    if recording is None:
        return None, note or _("{path} could not be opened.").format(path=path)

    notes = [note] if note else []
    if settings is None:
        settings, trouble = config.load_settings()
        if trouble:
            # Not a refusal: the defaults are a usable desk and the recording is what
            # was asked for. It is said because a settings file that will not read is
            # also why a remembered layout did not come back.
            _say(f"player.open: {trouble}")
            notes.append(trouble)

    remember = False
    if layout is None:
        answer = choose_layout(recording, settings)
        if answer is None:
            if _stream_count(recording) >= 2:
                # Отмена. Nothing was opened and there is nothing to report: saying
                # "that recording could not be opened" to somebody who has just
                # pressed Cancel is the player arguing with them.
                _say("player.open: the layout dialog was cancelled; nothing opened.")
                return None, ""
            # §1: a one-stream recording is never asked, and opens as it always has.
            layout = ""
        else:
            layout, remember = answer

    windows, said = _build_windows(recording, parent, layout, remember, settings)
    if windows is None:
        return None, said
    if said:
        notes.append(said)
    return windows, "  ".join(notes)


# ------------------------------------------------------------------- standalone running


def _say(text: str) -> None:
    """Print, even under pythonw where both streams can be None."""
    stream = sys.stderr or sys.stdout
    if stream is None:
        return
    try:
        print(text, file=stream)
    except (OSError, ValueError):  # a closed or detached stream; nothing else to do
        pass


#: Platforms with nobody to click the button. A modal box on one of these waits for an
#: answer that can never come, so the message is printed and that is all.
_HEADLESS_PLATFORMS = frozenset({"offscreen", "minimal", "vnc", ""})

#: Notes being shown without blocking. A `QMessageBox` with no parent and nothing
#: holding it is collected the moment `_show_note` returns, taking the window with it.
_SHOWING: list[QtWidgets.QMessageBox] = []


def _has_console() -> bool:
    """Is there anywhere for `_say` to say it?

    Measured on this machine: a process started detached from `pythonw.exe` - exactly
    what `ScreenRecPlayer.cmd` does - has `sys.stdout` and `sys.stderr` both None, so
    `print` reaches nobody and every note is silence.
    """
    return sys.stdout is not None or sys.stderr is not None


def _can_show_a_box() -> bool:
    """Is a window the right place for a note? Only when the console is not."""
    if _has_console():
        return False
    try:
        platform = QtWidgets.QApplication.platformName()
    except Exception:  # no QGuiApplication yet; there is nothing to show a box on
        return False
    return str(platform).lower() not in _HEADLESS_PLATFORMS


def _forget(box: QtWidgets.QMessageBox) -> None:
    try:
        _SHOWING.remove(box)
    except ValueError:  # already dropped; nothing to do
        pass


def _show_note(text: str, *, wait: bool = False) -> None:
    """Put a note in a window, when and only when there is no console for it.

    The box does not block, because the player is running behind it and a note must
    not hold the first frame hostage. `wait` is for the one case with nothing behind
    it: `--help`, where returning immediately would close the box again.
    """
    if not text or not _can_show_a_box():
        return
    try:
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Icon.Information, "ScreenRec Player", text
        )
        box.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
        if wait:
            box.exec()
            return
        _SHOWING.append(box)
        box.finished.connect(lambda _result, shown=box: _forget(shown))
        box.show()
    except Exception:
        # No display, or a platform plugin with no dialogs. There is no console either
        # - that is why we are here - so there is nowhere left to put it.
        pass


def _tell(text: str, *, wait: bool = False) -> None:
    """Say something that is not a failure, where the viewer will actually see it.

    Under the launcher that ships there is no console (see `_has_console`), so "the
    recording opened, but screen 2 is black because its file is gone" would otherwise
    be said to nobody - the player would report every refusal and no half-success,
    which is exactly the wrong way round.
    """
    _say(text)
    _show_note(text, wait=wait)


def _complain(text: str) -> None:
    """Say it on the console, and in a box - the player is usually started without one."""
    _say(text)
    try:
        platform = QtWidgets.QApplication.platformName()
    except Exception:  # no QGuiApplication yet; there is nothing to show a box on
        platform = ""
    if str(platform).lower() in _HEADLESS_PLATFORMS:
        return
    try:
        QtWidgets.QMessageBox.warning(None, "ScreenRec Player", text)
    except Exception as exc:  # no display, or a platform plugin that has no dialogs
        _say(f"(the message could not be shown in a window: {exc})")


def run(argv: list[str]) -> int:
    """Open a recording and run the event loop. Returns a process exit code.

    Nothing reaches the caller as an exception and nothing is said only to a console
    that is not there: every refusal goes through `_complain`, and the notes gathered
    on the way in are shown together once the windows are up, rather than three boxes
    deep before the first frame.

    The application is built before the arguments are read, so that `--help` has
    somewhere to put itself when there is no console.
    """
    arguments = list(argv)

    application = QtWidgets.QApplication.instance()
    if application is None:
        application = QtWidgets.QApplication(sys.argv[:1])
    application.setApplicationName("ScreenRec Player")
    # The player is its own process, so nothing has put the stored language in
    # force here; without this the recorder is Russian and the player is not.
    config.apply_language(config.load_settings()[0])

    notes: list[str] = []
    try:
        from ..ui import theme

        theme.apply(application)
    except Exception as exc:
        # Unstyled is ugly, not broken; the recording still matters more.
        notes.append(f"The player could not be styled: {type(exc).__name__}: {exc}")
        _say(notes[-1])

    if any(a in ("-h", "--help", "/?") for a in arguments):
        _tell(usage(), wait=True)
        return 0

    flags = [a for a in arguments if a.startswith("-") and a not in ("-",)]
    if flags:
        refusal = _("ScreenRec Player takes a path, not {flags}.").format(
            flags=" ".join(flags)
        )
        _complain(f"{refusal}\n\n{usage()}")
        return 2

    if arguments:
        target = Path(arguments[0])
        if len(arguments) > 1:
            notes.append(
                _("Only the first path is opened; ignoring {paths}").format(
                    paths=", ".join(arguments[1:])
                )
            )
            _say(notes[-1])
    else:
        settings, settings_problem = config.load_settings()
        if settings_problem:
            notes.append(settings_problem)
            _say(notes[-1])
        target = settings.resolved_output_dir()

    opening_the_usual_place = not arguments
    try:
        windows, problem = open_recording(target)
        if windows is None and problem and opening_the_usual_place:
            # Nobody named a file: this is the shortcut, and the answer to "there is
            # nothing here" is an open player with its Open menu, not a refusal. A
            # path that *was* named and could not be opened still refuses, because a
            # script passing a bad path should be told so.
            windows, build_problem = empty_player(target)
            if windows is not None:
                notes.append(problem)
                _say(problem)
                problem = build_problem
    except Exception as exc:
        # `open_recording` is documented not to raise and every path inside it returns
        # a pair. This is the last stop before a traceback printed to a console that
        # does not exist, which under the shipped launcher looks like double-clicking
        # the shortcut and nothing happening at all.
        _complain(
            _("{path} could not be opened: {detail}").format(
                path=target, detail=f"{type(exc).__name__}: {exc}"
            )
        )
        return 2
    if windows is None:
        if not problem:
            # The one shape of answer that is not a failure: §2.1's dialog was
            # cancelled. A box saying the recording could not be opened would be the
            # player arguing with somebody who has just pressed Отмена, and exit 2
            # would tell a script the same untruth.
            return 0
        _complain(problem)
        return 2
    if problem:
        notes.append(problem)
        _say(problem)

    show = getattr(windows, "show", None)
    if callable(show):
        try:
            show()
        except Exception as exc:
            _complain(f"The player's windows could not be shown: {exc}")
            return 2

    # Each note was said on the console as it happened; the box comes after the
    # windows, so it sits over a player that is already there, and carries all of them
    # at once rather than one box per sentence.
    _show_note("\n\n".join(notes))

    return int(application.exec())
