"""One clock for several files: the thing that makes a multi-screen take a recording.

`screenrec.multi` writes one MP4 per screen plus a `.screenrec.json` sidecar, because two
screens squeezed into one frame is not what was asked for - two screens *watched the way
they were used* is. That only works if something plays the files in step, and nothing
does. This module is that something.

The shape is the simple one: **one master, the rest follow.** The master is the file that
carries the audio, because sound is the one track a listener hears a seam in; a video
frame arriving a frame late is invisible, a stutter in speech is not. Every follower is
created without an audio output at all, so two copies of the same voice can never reach
the speakers.

What the correction is allowed to do is set by measurement, not by taste.

**Measured here on 2026-11-04.** Two `QMediaPlayer` instances playing the same file sit a
*constant* 33 ms apart across 45 seconds, worst sample 67 ms, and the distance does not
grow - there is no cumulative drift to fight. A multi-screen recording's two files start
17 ms apart, which is one frame at 60 fps. So the correction threshold is **80 ms**: five
frames, comfortably above the 67 ms worst sample that is simply how far apart two Media
Foundation pipelines sit, and well under anything an eye moving between two screens would
catch. Correcting below that would seek constantly, and a seek is a visible hitch - it
would buy a number nobody can see and pay for it in stutter.

The second rule the order of operations gives us: the master moves first and the
followers after it, and a seek lands on every player *before* playback resumes. Seeking a
follower while the master is already running means measuring it against a clock that has
moved on since, and the correction chases its own tail.

A file that will not load is reported through `problem` and dropped; the rest still play.
Half a recording is better than a dead window. The same holds once playback has started:
a master that turns out to be unplayable hands the clock to a healthy file and says so,
because a clock that never moves is not only silent - it drags every other screen back to
where it is stuck.

**Two states the views need, and why the clock is the only place that can know them.**
`docs/PLAYER_LAYOUTS.md` §6 asks the engine for two things a pane cannot work out on its
own, because a pane holds nothing but a signal connection to a sink.

The first is *seeking*. A seek lands on several decoders at different moments, and in
that gap each pane is showing whatever frame it last received - two instants presented
as one. §6: "Showing frames from two different moments as if they were synchronised is
worse than showing nothing for 200 ms." So `seeking_changed` goes True the moment a seek
starts and False only once every player has actually arrived; `seek()` returning means
only that the requests were made. It has a timeout, because a file that cannot seek would
otherwise leave the picture dimmed for the rest of the session - a permanent symptom
worse than the transient one it was meant to cover.

The second is *unequal lengths*. Two screens are not recorded for the same number of
milliseconds, and the master is the file carrying the sound, which is nothing to do with
which file is longest. So `duration()` is the longest stream, `stream_duration()` is one
stream's own, and `stream_ended` says which screen has run out while the others carry on.
`finished` keeps its old meaning and fires when the *last* stream ends - ending the
session when the master ran out would cut the other screen off mid-sentence, and that is
the ordinary case rather than a corner one.

Both additions are inert for a single-stream recording, which is the common case: one
stream cannot disagree with itself about what moment it is showing, and there is no
"others still play" for it to end in front of.

**Two files that did not start together.** `docs/PLAYER_SOURCES.md` §2.1 adds the case
this engine was not built for: two cameras, started by two hands, where nothing paired
the files and nobody pressed one button. Each stream carries an offset and one line
governs everything:

    position inside stream i  =  timeline position + offset_ms[i]

Three consequences are easy to get wrong and are therefore written down here rather than
discovered. First, **the correction's target is `master position + offset_i`, not
equality**: aiming at equality would drag the streams back together every tick and fight
the viewer's own adjustment, which is the failure this design exists to avoid. Second,
**the timeline is not the master's playhead** - it is the master's playhead minus the
master's own offset, because §2.1 says there is no privileged stream and the file
carrying the sound may be the one that was slid. Third, a stream whose target is
*negative* has not started yet; it is held paused at its first frame, exempted from the
correction exactly as an ended stream is, and announced through `stream_waiting` so a
view can hold black. Letting it run would show its opening frames early and then have
the correction yank it back - a stutter caused by nothing but arithmetic.

All of it is inert when every offset is 0, which is every recording this program has
ever made: the targets collapse to the timeline, nothing ever waits, and not one extra
seek is issued.

**Which playhead is allowed to be the clock.** The timeline is read off a stream rather
than counted, so that there is only ever one clock - but not every stream can be read at
every instant, and the two ways a playhead stops meaning anything are symmetrical. One
is a stream the timeline has not reached, pinned at its first frame; that was always
skipped. The other is a stream pinned at its *last* frame while the timeline still
covers more, and it was not, which froze the whole transport. `setPosition` clamps at a
file's own duration - a real `QMediaPlayer` and the suite's stub alike - so the moment a
stream runs out, or is slid past its own end by the viewer's own control, its playhead
is a constant; and once that stream's offset is subtracted from the constant the
timeline does not merely stop, it jumps *backwards* by the overshoot. One press of
«+100 мс» near the end used to walk the scrubber back by 33 ms, and a typed 5 s used to
walk it back by 3.7 s.

So a stream is the clock only while it can still tell the time, and `_tells_the_time` is
the single place that decides it. The test is not "has it ended" but "does it cover the
end of the timeline": a stream whose own length reaches `duration()` is the longest one
and its last frame *is* the end of the timeline, so nothing is gained by skipping it -
which is what leaves the one-file case and the equal-length recorder-made set untouched
by any of this. Only a stream the timeline outlives is dropped, and when no stream can
speak at all the value the last reading pinned is returned while `_tick` counts on from
it: the same arithmetic the lead-in already used, now applied at both ends.

**Which file the sound comes from (§1.2).** Two cameras both recorded sound and one of
them recorded it better, so the stream carrying the audio is the viewer's choice and not
the file order's. There is one `QAudioOutput` for the engine's life and it is *moved*
between players rather than made per stream: that is the only arrangement in which two
copies of one voice cannot reach the speakers, and it carries the volume and the mute
across the change for free. Moving it moves the clock with it, because this module's
first rule is that the master is the file carrying the sound - a stream the correction
may seek is a stream with an audible seam in it. Nothing is sought by the change itself:
the streams are inside `CORRECTION_MS` of each other by construction, so the picture
does not move, which is what §1.2 asks for in as many words.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6 import QtCore, QtMultimedia

from .. import multi
from ..i18n import _

#: How far a follower may sit from the master before it is worth a seek, in milliseconds.
#: Five frames at 60 fps. See the module docstring: two players on the same file were
#: measured a constant 33 ms apart, worst sample 67 ms, with no growth over 45 s, so a
#: smaller threshold would fire forever and correct nothing real.
CORRECTION_MS = 80

#: One timer drives both the correction and the position signal. 100 ms is fine for a
#: scrubber and leaves the decoders alone between checks.
TICK_MS = 100

#: A follower that has just been pulled into line is left alone for this long. Without it
#: a file that cannot seek would be hammered every tick.
SETTLE_MS = 500

#: How long the §6 seeking state may last before it is dropped although no player ever
#: reported landing. This is `SETTLE_MS`, deliberately not a new number: it is already
#: this module's measured answer to "how long does a player need after it has been moved
#: before its position means anything", and the question asked here is the same one from
#: the other side. The other measured number, `CORRECTION_MS`, is a *distance* in the
#: timeline rather than a duration, so it is reused below as "near enough to the target
#: to count as arrived" and not as this timeout. Without a timeout a file that cannot
#: seek dims the picture for the rest of the session, which is a worse fault than the
#: one the dimming exists to hide.
SEEK_TIMEOUT_MS = SETTLE_MS

#: After this many corrections in a row that did not help, the follower is declared
#: hopeless, said so once, and left to run free rather than stuttered forever.
GIVE_UP_AFTER = 5

#: How long the master may sit exactly where playback started, while it claims to be
#: playing, before the engine says so out loud. A file that is present but corrupt does
#: not always reach `errorOccurred`; it can simply never advance, and a transport frozen
#: at 0:00 with no sentence anywhere is the one thing this player must not do.
STALL_WARNING_MS = 3000

#: Used for frame stepping when the sidecar does not name a frame rate. `multi` does not
#: write one today; the recorder's own default is 60.
DEFAULT_FRAMERATE = 60.0

#: The rectangle given to a screen whose index names none - a pair of videos the viewer
#: put together, which never came off a monitor at all. Laid out side by side so the
#: layouts have a sane arrangement to read, and 16:9 because that is what a camera most
#: often hands over. It is a stand-in and is documented as one wherever it is used.
NOMINAL_DESKTOP = (1920, 1080)

#: How far a stream may be slid from the others, in either direction, in milliseconds.
#: An hour, and the number is `player.window.OFFSET_LIMIT_MS` so that the panel's own
#: field and an index read off disk are bounded by the same rule rather than by two.
#:
#: The bound is not politeness about what two cameras are plausibly out by. An offset
#: reaches Qt as a position - a `qint64`, which raises `OverflowError` out of
#: `setPosition` rather than clamping - and leaves again inside `duration()`, which a
#: transport puts into a scrubber's `int32` range. So an index with a hand-edited
#: `offset_ms` of 1e20, or a digit too many typed into a field that is not this
#: module's to police, used to be a traceback out of `SyncEngine.__init__`, a window
#: that never opened, and a poll timer left printing into the log for the rest of the
#: session. Clamping here closes every one of those, and says so to the viewer.
OFFSET_LIMIT_MS = 3_600_000

_MIN_RATE = 0.1
_MAX_RATE = 4.0


@dataclass(frozen=True)
class Screen:
    """One file of a multi-screen take, and where its screen sat on the desktop.

    `offset_ms` is §2.1 of `docs/PLAYER_SOURCES.md`: how far into this file the
    timeline's zero falls. Positive means the file was started *earlier* than the
    reference and has to be wound forward; negative means it started later and has
    nothing to show until the timeline reaches it. A recorder-made set has 0 everywhere
    and so does a version 1 index, which names no offsets at all.

    This is the value the index was loaded with. While a set is playing the live value
    belongs to `SyncEngine.offset`, because the viewer can move it and this dataclass is
    frozen on purpose - two places holding a number one of them can change is how they
    come to disagree.
    """

    file: Path
    monitor: int
    desktop: tuple[int, int, int, int]
    size: tuple[int, int]
    rotation: int
    primary: bool
    offset_ms: int = 0


@dataclass
class Recording:
    """A set of files loaded from a sidecar, ready to be played as one."""

    screens: list[Screen]
    audio_file: Path
    recorded: str
    folder: Path
    framerate: float = DEFAULT_FRAMERATE

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> tuple[Recording | None, str]:
        """Read a `.screenrec.json` sidecar of either version. Never raises.

        Returns the recording and a message. The message is not only for the failure
        case: a set whose second file has been deleted still loads, with the remaining
        screens, and says which file is gone. A player that refuses to open anything
        because one of three files was moved is less useful than one that opens two and
        says so.

        **Version 1 and version 2 both read, and mean the same thing where they overlap**
        (`docs/PLAYER_SOURCES.md` §1.3). A version 1 index names no `offset_ms`, and a
        missing one is 0 - the recorder's screens were started together, so there is
        nothing to slide. Nothing is converted and nothing is rewritten on the way in.

        **A `"file"` is an absolute path or a bare name, and which it is decides where it
        is looked for.** A bare name - what the recorder writes - is resolved beside the
        index, so a whole folder can be moved without breaking a set. An absolute path -
        what `multi.write_set` writes for a file the viewer picked out of some other
        folder - is used as it stands, because two cameras are not guaranteed to live in
        one place. `multi.resolve_file` is the one place that rule lives.
        """
        sidecar = Path(path)
        try:
            raw, problem = multi.read_sidecar(sidecar)
        except Exception as exc:  # noqa: BLE001 - "never raises" has to mean it
            # `multi` is finished and reads the index trustingly: an entry that is not a
            # dictionary (a hand-edited or truncated file) throws inside it, before the
            # per-entry defence below ever runs. The player must say what is wrong
            # rather than fall over on the way to its first window.
            return None, _(
                "{name} is not a readable ScreenRec index: {detail}"
            ).format(name=sidecar.name, detail=exc)
        if not raw:
            return None, problem or _("{name} could not be read.").format(
                name=sidecar.name
            )

        folder = sidecar.parent
        notes: list[str] = []
        screens: list[Screen] = []
        missing: list[str] = []

        entries = raw.get("screens")
        if not isinstance(entries, list):
            return None, _("{name} lists no screens.").format(name=sidecar.name)

        for position, entry in enumerate(entries):
            screen, why = _screen_from_entry(entry, folder, position)
            if why:
                # A note arrives with a usable screen too - an unreadable offset does
                # not cost us the file, only the viewer's alignment - so it is kept
                # before the `is None` test rather than inside it.
                notes.append(why)
            if screen is None:
                continue
            if not screen.file.is_file():
                # A file named by an absolute path is reported as one: "camera_b.mp4 is
                # missing" is no help when the index points at another folder entirely
                # and there is a camera_b.mp4 sitting next to the index.
                missing.append(
                    screen.file.name
                    if screen.file.parent == folder
                    else str(screen.file)
                )
                continue
            screens.append(screen)

        if missing:
            notes.append(
                _("Named in the index but not there, so left out: {files}").format(
                    files=", ".join(missing)
                )
            )
        if not screens:
            notes.append(
                _("None of the files named in {name} are there.").format(
                    name=sidecar.name
                )
            )
            return None, "  ".join(n for n in notes if n)

        named = str(raw.get("audio") or "").strip()
        # Resolved by the same rule as a screen's own file, or an index that names its
        # audio by an absolute path would hand the clock to the wrong stream.
        audio_file = multi.resolve_file(folder, named) if named else screens[0].file
        if audio_file not in [s.file for s in screens]:
            if named:
                notes.append(
                    _(
                        "The file carrying the sound ({named}) is not among the "
                        "screens that loaded; {heir} will drive the others."
                    ).format(named=named, heir=screens[0].file.name)
                )
            audio_file = screens[0].file

        framerate = _positive_float(raw.get("framerate"), DEFAULT_FRAMERATE)

        recording = cls(
            screens=screens,
            audio_file=audio_file,
            recorded=str(raw.get("recorded") or ""),
            folder=folder,
            framerate=framerate,
        )
        return recording, "  ".join(n for n in notes if n)

    # -- geometry ----------------------------------------------------------

    def master_index(self) -> int:
        """Which screen carries the audio, and therefore the clock."""
        for index, screen in enumerate(self.screens):
            if screen.file == self.audio_file:
                return index
        return 0

    def layout_rects(self, scale: float) -> list[tuple[int, int, int, int]]:
        """Window rectangles that keep the screens' arrangement, scaled to fit.

        The desktop rectangles in the sidecar are in Windows' virtual-desktop
        coordinates, where the primary screen starts at 0,0 and **a screen to its left
        has a negative x**. Those coordinates are not window positions, so everything is
        moved by the bounding box's own origin first: the result starts at 0,0 and a
        screen that was to the right of another is still to the right of it.

        `scale` is a plain multiplier on desktop pixels - 0.25 gives windows a quarter of
        the size the screens were. A scale of zero or less would collapse every window to
        nothing, so it is read as 1.0.
        """
        if not self.screens:
            return []
        factor = float(scale) if scale and scale > 0 else 1.0

        origin_x = min(s.desktop[0] for s in self.screens)
        origin_y = min(s.desktop[1] for s in self.screens)

        rects: list[tuple[int, int, int, int]] = []
        for screen in self.screens:
            x, y, width, height = screen.desktop
            rects.append(
                (
                    int(round((x - origin_x) * factor)),
                    int(round((y - origin_y) * factor)),
                    max(1, int(round(width * factor))),
                    max(1, int(round(height * factor)))
                )
            )
        return rects


def _positive_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _screen_from_entry(
    entry: Any, folder: Path, position: int
) -> tuple[Screen | None, str]:
    """Turn one sidecar entry into a `Screen`, or say why it could not be used."""
    if not isinstance(entry, dict):
        return None, _(
            "Entry {position} in the index is not a screen and was ignored."
        ).format(position=position)

    name = str(entry.get("file") or "").strip()
    if not name:
        return None, _(
            "Entry {position} in the index names no file and was ignored."
        ).format(position=position)

    if "desktop" in entry:
        # Present but unusable is a broken index and still a refusal: a rectangle that
        # was meant to say where the screen was and does not is worse than none.
        desktop = _four_ints(entry.get("desktop"))
        if desktop is None:
            return None, _(
                "{name} has no usable desktop rectangle in the index and was ignored."
            ).format(name=name)
    else:
        # Absent is not broken any more. A set the player saved (`multi.write_set`)
        # names no rectangles because its files never came off a monitor; the nominal
        # side-by-side arrangement gives the layouts something sane to read and is a
        # stand-in, not a measurement.
        desktop = (position * NOMINAL_DESKTOP[0], 0, *NOMINAL_DESKTOP)

    size = _two_ints(entry.get("size"))
    if size is None:
        # The frame size is only used for aspect ratios; the desktop rectangle is a
        # perfectly good stand-in, so this is worth a default rather than a refusal.
        size = (desktop[2], desktop[3])

    try:
        monitor = int(entry.get("monitor", position))
    except (TypeError, ValueError):
        monitor = position
    try:
        rotation = int(entry.get("rotation", 0))
    except (TypeError, ValueError):
        rotation = 0

    offset, note = _offset_from_entry(entry, name)

    return (
        Screen(
            file=multi.resolve_file(folder, name),
            monitor=monitor,
            desktop=desktop,
            size=size,
            rotation=rotation,
            primary=bool(entry.get("primary", False)),
            offset_ms=offset,
        ),
        note,
    )


def _offset_from_entry(entry: dict, name: str) -> tuple[int, str]:
    """This screen's `offset_ms`, and a note if the index said something unusable.

    Missing is 0 and says nothing: that is every version 1 index and every set this
    recorder writes, and announcing it would put a note on the commonest case there is.
    *Present and not a number* is said out loud instead of being swallowed - the viewer
    spent time lining those two files up and is owed the news that it did not survive.

    *Present and absurd* is clamped rather than refused, for the reason `OFFSET_LIMIT_MS`
    gives: a number past a `qint64` is not a bad alignment, it is an `OverflowError` out
    of `setPosition` before the first window opens, and a number past an `int32` is one
    out of the scrubber's own `setRange` afterwards. Both of those are a file this
    program will not open at all, which is a worse answer than an hour.
    """
    if "offset_ms" not in entry:
        return 0, ""
    try:
        wanted = int(entry["offset_ms"])
    except (TypeError, ValueError, OverflowError):
        # OverflowError is in the tuple for `Infinity`, which `json` accepts and
        # `int()` refuses with that rather than with a ValueError.
        return 0, _(
            "{name} has an unreadable offset in the index; it is being played in "
            "step with the others."
        ).format(name=name)
    clamped = _within_the_offset_limit(wanted)
    if clamped != wanted:
        return clamped, _(
            "{name} is slid {wanted} ms in the index, which is further than two "
            "recordings can be lined up by; {clamped} ms is being used."
        ).format(name=name, wanted=wanted, clamped=clamped)
    return clamped, ""


def _within_the_offset_limit(milliseconds: int) -> int:
    """`milliseconds` brought inside `OFFSET_LIMIT_MS`, in either direction."""
    return max(-OFFSET_LIMIT_MS, min(OFFSET_LIMIT_MS, milliseconds))


def _four_ints(value: Any) -> tuple[int, int, int, int] | None:
    numbers = _ints(value, 4)
    if numbers is None:
        return None
    x, y, width, height = numbers
    if width <= 0 or height <= 0:
        return None
    return (x, y, width, height)


def _two_ints(value: Any) -> tuple[int, int] | None:
    numbers = _ints(value, 2)
    if numbers is None or numbers[0] <= 0 or numbers[1] <= 0:
        return None
    return (numbers[0], numbers[1])


def _ints(value: Any, count: int) -> tuple[int, ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) != count:
        return None
    try:
        return tuple(int(v) for v in value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- the clock


class SyncEngine(QtCore.QObject):
    """One master, the rest follow. The master is the file carrying the audio."""

    position_changed = QtCore.Signal(int)  # milliseconds
    duration_changed = QtCore.Signal(int)
    state_changed = QtCore.Signal(bool)  # playing
    #: True while a seek is in flight, False once every player has arrived or the wait
    #: ran out. §6's loading state: the views dim while it is True.
    seeking_changed = QtCore.Signal(bool)
    #: The index into `recording.screens` of a stream that has reached its own end while
    #: at least one other is still playing. Never said about the last one - that is
    #: `finished` - never said at all for a single-stream recording, and never said
    #: about a stream that covers the end of the timeline: the two screens of one
    #: recording are a frame apart and the shorter one has not "ended early".
    #:
    #: Said *again* on each seek that lands in the tail of the same stream, because
    #: every seek clears the ended flags first: the state it announces is "ended as of
    #: this playhead", and a view that already shows the badge sets it again for
    #: nothing. `stream_has_ended` is the same state for a pane built later.
    stream_ended = QtCore.Signal(int)
    #: A stream and whether the timeline is currently *before* its content starts, which
    #: happens only when its offset is negative (§2.1). True means it has nothing to
    #: show and a view should hold black with a badge - showing its frozen first frame
    #: as if it were live is the thing that document asks us not to do.
    stream_waiting = QtCore.Signal(int, bool)
    #: An offset has been moved. No argument: the control reads `offset()` back, and a
    #: signal carrying the new value would go stale the moment two of them changed.
    offsets_changed = QtCore.Signal()
    #: §1.2: which stream is heard now, or -1 for «Звук: Выключен». Carries the value
    #: rather than making the transport ask, because there is exactly one of them and
    #: it cannot go stale the way a pair of offsets can.
    audio_changed = QtCore.Signal(int)
    finished = QtCore.Signal()
    problem = QtCore.Signal(str)

    def __init__(
        self,
        recording: Recording,
        parent: QtCore.QObject | None = None,
        *,
        player_factory: Callable[[QtCore.QObject], Any] | None = None,
        audio_factory: Callable[[QtCore.QObject], Any] | None = None,
    ) -> None:
        """Build one player per screen.

        `player_factory` and `audio_factory` exist so the engine can be checked without
        real media or a real audio device; they default to `QMediaPlayer` and
        `QAudioOutput`, and nothing but the tests passes them.
        """
        super().__init__(parent)
        self.recording = recording
        self._make_player = player_factory or (lambda parent: QtMultimedia.QMediaPlayer(parent))
        self._make_audio = audio_factory or (lambda parent: QtMultimedia.QAudioOutput(parent))

        self._sinks: list[QtMultimedia.QVideoSink] = []
        self._players: list[Any | None] = []
        self._startup_problems: list[str] = []

        self._playing = False
        self._finished = False
        self._seeking = False
        self._rate = 1.0
        self._last_duration = -1
        self._last_position = -1
        self._settle_left: list[int] = []
        self._corrections: list[int] = []
        self._given_up: list[bool] = []
        #: One flag per screen: has this stream been put where the timeline is since
        #: Qt finished reading its file? See `_place_after_loading`.
        self._placed_after_load: list[bool] = []

        # The §6 seeking state. `_seeking` above is a different thing and keeps its
        # name: it guards `_tick` against running while `setPosition` calls are still
        # being made, and it is false again before `seek()` returns. This one lasts
        # until the players have caught up, which is long after that.
        self._seek_pending = False
        self._seek_target = 0
        self._seek_waited = 0

        #: One flag per screen: has this stream reached its own end? Index-aligned with
        #: `recording.screens`, `sinks()` and `_players`, like every other list here.
        self._ended_streams: list[bool] = []

        #: §2.1's offsets, index-aligned with everything else. The engine's copy is the
        #: live one: `Screen` is frozen, and a viewer sliding a stream must not have to
        #: rebuild the recording to do it.
        self._offsets: list[int] = [int(s.offset_ms) for s in recording.screens]
        #: One flag per screen: is the timeline before this stream's first frame?
        self._waiting: list[bool] = [False] * len(recording.screens)
        #: The timeline position, for the moments it cannot be read off a stream: while
        #: every stream is still waiting, and before anything has been built. Everywhere
        #: else `position()` derives it, so there is no second clock to drift.
        self._timeline = 0

        # Is the clock actually running? A master that is present but unplayable reports
        # the same position for ever, and dragging the healthy screens back to it every
        # tick would be the worst of both worlds: a dead transport *and* a stutter.
        self._progress_from = 0
        self._master_advancing = False
        self._stalled_ms = 0
        self._stall_reported = False

        self._audio: Any | None = None
        self._status_source: Any | None = None
        self._master_index = recording.master_index()

        #: §1.2: which stream the sound is to come from, or -1 for «Звук: Выключен».
        #: The default is the master, which for a recorder-made set is whatever the
        #: sidecar names in `"audio"` - the existing behaviour, untouched. It is a
        #: *choice* rather than a measurement of what is audible, which is why it is
        #: set here and moved only where that choice changes: by the viewer, or by
        #: `_build` and `_hand_over_clock` when the chosen file turns out not to play.
        self._audio_index = self._master_index

        self._build()

        if any(self._offsets):
            # Every player sits at its own 0 once its source is set, which is the
            # timeline's 0 only when nothing is slid. Guarded rather than unconditional
            # because a recorder-made set must cost exactly the seeks it costs today,
            # and that number is none.
            self._place_positions(0)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(TICK_MS)
        # Precise rather than Qt's default coarse type, because this timer is the only
        # thing counting the timeline while no stream can tell the time. Measured on
        # 2026-11-05 under the offscreen platform in a real event loop, 3 s a run: a
        # coarse 100 ms timer fires every 109.45 ms on average (26 gaps, 105.74 min,
        # 122.73 max), a precise one every 99.99 ms (29 gaps, 99.04 min, 101.25 max).
        # Those 9.5% are exactly how much too long the lead-in before a negative offset
        # used to run. `_since_the_last_tick` is the other half of the fix.
        self._timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

        #: Wall clock between two ticks, for the stretch where the timeline has to be
        #: counted rather than read off a playhead. See `_since_the_last_tick`.
        self._tick_clock = QtCore.QElapsedTimer()

        # A timer of its own rather than a job for `_tick`, which only runs while
        # playback does: a seek made while paused - a scrubber drag, a frame step - is
        # exactly the case where the picture would otherwise stay dimmed for ever.
        #
        # Precise, and its waiting counted off a clock, for the same reason the tick
        # above is: left coarse and counting its own ticks, `SEEK_TIMEOUT_MS` of 500 ms
        # took **538.5, 541.9 and 544.5 ms** of wall clock - measured on 2026-11-05 in
        # a real event loop against `scratch/twofinal` with `_has_landed` answering no,
        # which is the file that cannot seek this timeout exists for. Same fault, same
        # 8-9%, one timer further on; three runs of this body gave 499.7, 499.8 and
        # 499.8 ms.
        self._landing = QtCore.QTimer(self)
        self._landing.setInterval(TICK_MS)
        self._landing.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._landing.timeout.connect(self._check_landing)

        #: Wall clock between two landing checks. The dimming's half of `_tick_clock`.
        self._landing_clock = QtCore.QElapsedTimer()

        # Problems found while opening the files happen before anyone can have connected
        # to `problem`, so they are kept and announced once control returns to the event
        # loop. `startup_problems()` reads the same list for a caller with no loop.
        self._announce = QtCore.QTimer(self)
        self._announce.setSingleShot(True)
        self._announce.setInterval(0)
        self._announce.timeout.connect(self._announce_startup_problems)
        if self._startup_problems:
            self._announce.start()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        for index, screen in enumerate(self.recording.screens):
            # One sink per screen whatever happens, so `sinks()` stays index-aligned with
            # `recording.screens` and a window for a file that failed shows black instead
            # of showing some other screen's picture.
            self._sinks.append(QtMultimedia.QVideoSink(self))
            self._settle_left.append(0)
            self._corrections.append(0)
            self._given_up.append(False)
            self._ended_streams.append(False)
            self._placed_after_load.append(False)

            if not screen.file.is_file():
                self._players.append(None)
                self._startup_problems.append(
                    _(
                        "{name} is not there, so screen {monitor} will not play."
                    ).format(name=screen.file.name, monitor=screen.monitor)
                )
                continue

            player = self._make_player(self)
            self._players.append(player)
            _call(player, "setVideoSink", self._sinks[index])

            if index == self._master_index:
                audio = self._make_audio(self)
                if audio is not None:
                    self._audio = audio
                    _call(player, "setAudioOutput", audio)
            else:
                # Muted at the source: a follower is given no audio output at all, so
                # there is no volume anywhere that could leak a second copy of the voice.
                _call(player, "setAudioOutput", None)

            _call(player, "setSource", QtCore.QUrl.fromLocalFile(str(screen.file)))
            self._connect_errors(player, index)
            self._connect_duration(player, index)
            self._connect_loaded(player, index)
            if index == self._master_index:
                self._connect_status(player)

        if self._master_player() is None and any(p is not None for p in self._players):
            # The audio file is the one that failed. Something still has to be the clock.
            fallback = next(i for i, p in enumerate(self._players) if p is not None)
            self._startup_problems.append(
                _(
                    "The file carrying the sound could not be opened; screen "
                    "{monitor} will drive the others and there will be no sound."
                ).format(monitor=self.recording.screens[fallback].monitor)
            )
            self._master_index = fallback
            self._audio = None
            # The file §1.2's choice named is the one that failed, so there is no
            # stream the sound comes from. The heir drives the others and is silent.
            self._audio_index = -1
            self._connect_status(self._players[fallback])
        elif self.recording.screens and not any(p is not None for p in self._players):
            # Only when there were files and not one opened. A recording with no
            # screens at all is the player standing open with nothing loaded - see
            # `open.empty_player` - and calling that a failure would put a complaint
            # in front of somebody who has simply not chosen a file yet.
            self._startup_problems.append(
                _("Not one file of this recording could be opened.")
            )

        self._show_the_first_frame()

    def _show_the_first_frame(self) -> None:
        """Pause every player once, so that a recording nobody has played has a picture.

        Qt delivers a frame to a `QVideoSink` on `pause()`, **not** on `setSource` and
        **not** on `setPosition`. Measured on 2026-11-05 with a bare `QMediaPlayer` and
        one of this project's own files, no ScreenRec code anywhere in it:

            after setSource      state=StoppedState  pos=    0   frames: []
            after setPosition    state=StoppedState  pos= 4000   frames: []
            after pause()        state=PausedState   pos= 4000   frames: [3983]
            after another seek   state=PausedState   pos= 7000   frames: [3983, 6983]

        So without this a freshly opened recording is a black window whose scrubber
        moves while the picture does not, and §2.2 of `docs/PLAYER_SOURCES.md` - nudge
        one stream a frame at a time and watch it line up - cannot work at all until
        somebody has pressed play once. One `pause()` per player fixes it for the whole
        session; the state is the one the player is opened in anyway.
        """
        for player in self._players:
            if player is not None:
                _call(player, "pause")

    def _connect_errors(self, player: Any, index: int) -> None:
        """Hook up what the player offers. A stub offering less is not an error."""
        signal = getattr(player, "errorOccurred", None)
        if signal is None:
            return
        try:
            signal.connect(lambda _error, text, i=index: self._on_error(i, text))
        except (AttributeError, TypeError):
            pass  # not a signal; a stub without one simply cannot report errors

    def _connect_duration(self, player: Any, index: int) -> None:
        """Say the recording's length as soon as a decoder reports its own.

        `_announce_duration` was reached from `_tick` and from `set_offset` and from
        nowhere else, and `_tick` runs only while playback does - so **a player that
        was opened and not played never announced its length at all**. Measured on
        2026-11-05 against `scratch/twofinal`: a freshly built engine left paused for
        3 s emitted `duration_changed` not once, while `duration()` answered 10217 the
        whole time. On this body the same engine says it once, with the 10217 in it.

        What hangs on it is §1.1 of `docs/PLAYER_SOURCES.md`: the position is carried
        into a newly opened player twice, once at once and once on the first length the
        new clock announces, because `setSource` is asynchronous and the first attempt
        can be dropped. A player opened from a *paused* one therefore never got its
        second attempt and came up at 00:00.

        **A length is not proof that a position will stick**, which is the half of
        that the player had wrong. Measured on 2026-11-05 in bare Qt, one of this
        project's own files and no ScreenRec code in it, three runs alike:

            durationChanged(10217)  status = LoadingMedia
            setPosition(5000) from inside that slot -> position 0, and 0 still
            2.5 s later

        So a carry that seeks on hearing the length seeks into a file Qt is still
        opening, and loses the position for good. `_connect_loaded` is the half that
        knows when it would not be lost.

        A stub without the signal simply has no length to announce, exactly as
        `_connect_errors` treats one without `errorOccurred`.
        """
        signal = getattr(player, "durationChanged", None)
        if signal is None:
            return
        try:
            signal.connect(lambda _length: self._announce_duration())
        except (AttributeError, TypeError):
            pass  # not a signal; nothing is lost but the early announcement

    def _connect_loaded(self, player: Any, index: int) -> None:
        """Put this stream where the timeline is, once Qt has really read its file.

        One connection per player, which is what tells it apart from
        `_connect_status`: that one follows the *master* and moves with the clock,
        this one is about a file being opened and belongs to the file.
        """
        signal = getattr(player, "mediaStatusChanged", None)
        if signal is None:
            return
        try:
            signal.connect(lambda status, i=index: self._on_loaded(status, i))
        except (AttributeError, TypeError):
            pass  # a stub without the signal cannot have dropped a seek either

    def _on_loaded(self, status: Any, index: int) -> None:
        if status == QtMultimedia.QMediaPlayer.MediaStatus.LoadedMedia:
            self._place_after_loading(index)

    def _place_after_loading(self, index: int) -> None:
        """Put a freshly loaded stream where the timeline already is.

        **A `setPosition` made before Qt has read the file is dropped**, and until now
        the engine simply lost it: a `seek(5000)` on an engine built a moment earlier
        left the recording at 0, three runs of three, and so did the offsets
        `__init__` applies to a slid set. Where the dropping stops is measured, in
        bare Qt, same file, three runs - the status the seek was made from on the
        left, where the player settled on the right:

            LoadingMedia  -> 0       the length is reported here, and it is too early
            LoadedMedia   -> 5000    and it holds through BufferingMedia
            BufferedMedia -> never arrives at all for a player that is only paused

        So `LoadedMedia` is the moment, and it is asked for once per stream: every
        later status is Qt getting on with the file, and a seek there would be a
        hitch in the middle of playback.

        `_timeline` and not `position()`: a player that dropped its seek is sitting at
        0 and answering 0, so the playhead is exactly the thing that cannot be trusted
        here. `_timeline` is what the transport last *asked* for, which is the value
        this is meant to restore.

        A recording that nobody has moved costs no seek at all by this: its timeline
        is 0 and its players are at 0, which is inside the one threshold this module
        has for "near enough".
        """
        if not 0 <= index < len(self._players) or self._placed_after_load[index]:
            return
        player = self._players[index]
        if player is None:
            return
        self._placed_after_load[index] = True
        target = self._timeline + self._offsets[index]
        here = int(_call(player, "position") or 0)
        if abs(here - max(0, target)) <= CORRECTION_MS:
            return
        _call(player, "setPosition", max(0, target))
        if target < 0:
            _call(player, "pause")
        elif self._playing:
            _call(player, "play")
        # It has just been put where it belongs; the correction must not read its old
        # position on the next tick and call that a drift.
        self._settle_left[index] = SETTLE_MS

    def _connect_status(self, player: Any) -> None:
        signal = getattr(player, "mediaStatusChanged", None)
        if signal is None:
            return
        try:
            signal.connect(self._on_status)
        except (AttributeError, TypeError):
            return  # the tick's end-of-file backstop still covers the master
        # Remembered so that a master handing the clock over can take its own status
        # signal with it: `_on_status` reads `self._master_index`, so a stale connection
        # would blame the new master for the dead one's news.
        self._status_source = player

    def _disconnect_status(self) -> None:
        player, self._status_source = self._status_source, None
        if player is None:
            return
        signal = getattr(player, "mediaStatusChanged", None)
        if signal is None:
            return
        try:
            signal.disconnect(self._on_status)
        except (AttributeError, TypeError, RuntimeError):
            pass  # never connected, or already gone; either way there is nothing to do

    def _announce_startup_problems(self) -> None:
        for text in self._startup_problems:
            self.problem.emit(text)

    def startup_problems(self) -> list[str]:
        """What went wrong while opening the files, for a caller with no event loop."""
        return list(self._startup_problems)

    # -- the parts a window needs -----------------------------------------

    def sinks(self) -> list[QtMultimedia.QVideoSink]:
        """One video sink per screen, in `recording.screens` order."""
        return list(self._sinks)

    def _master_player(self) -> Any | None:
        if 0 <= self._master_index < len(self._players):
            return self._players[self._master_index]
        return None

    def _followers(self) -> list[tuple[int, Any]]:
        return [
            (i, p)
            for i, p in enumerate(self._players)
            if p is not None and i != self._master_index
        ]

    def _live(self) -> list[tuple[int, Any]]:
        """Every player that opened, master included, with its screen's index."""
        return [(i, p) for i, p in enumerate(self._players) if p is not None]

    def _in_clock_order(self) -> list[tuple[int, Any]]:
        """Every live player with the master first.

        The order is the module docstring's second rule: the clock moves before the
        things that are measured against it. Every loop that seeks or starts players
        goes through here so that rule cannot be forgotten in one of them.
        """
        master = self._master_player()
        order = [(self._master_index, master)] if master is not None else []
        order.extend(self._followers())
        return order

    # -- transport ---------------------------------------------------------

    def play(self) -> None:
        """Start, or start again from the beginning when there is nothing left to play.

        **Pressing play at the very end restarts the recording.** The alternative - a
        play button that does nothing on the last frame - was what this used to do by
        accident, and it leaves the transport's only control inert with no way back to
        the beginning but a scrub. The decision is written here rather than in the
        window because the window cannot see which streams have frames left.

        What was wrong was not the decision but its test. `_finished` is set by
        `_on_finished`, which only `_tick` and the master's `EndOfMedia` reach - so a
        recording *played* to its end restarted, and one *scrubbed* to its end did not:
        `_resume_streams` started players that were already on their last frame, Qt
        answered `EndOfMedia` at once, and `_note_ends` finished the session again.
        Measured on 2026-11-05 against `scratch/twofinal`, scrubbed to 10267 of 10267
        and then played: position 10267, `playing()` False, nothing decoded, and a
        second press needed before it would start. So the test is "has the timeline
        outrun every stream", which is true in both cases.

        **"The very end" is the last instant and not a zone**, which is the half of
        this that was wrong until 2026-11-05: the test allowed `CORRECTION_MS`, so play
        pressed anywhere in the final 80 ms - five frames at 60 fps - rewound instead of
        playing them, and so did play pressed after one «-1 кадр» from the end.
        Measured on the take `scratch/twofinal` held at 02:54 that morning, 10217 ms
        and 9717 ms, with the old body: `seek(10137)` then play went to 383, while
        `seek(9917)` then play ran on to 10217 as it should.
        `_streams_the_timeline_has_outrun` carries the rest of the measurements and
        the width.
        """
        master = self._master_player()
        if master is None:
            self.problem.emit(
                _("There is nothing to play: no file of this set opened.")
            )
            return
        if self._timeline_is_empty():
            self.problem.emit(
                _(
                    "Every file of this set is slid past the end of the timeline, so "
                    "there is nothing to play. Reset the offsets to bring them back."
                )
            )
            return
        if self._finished or self._nothing_left_to_play():
            # Pressing play at the end starts again rather than doing nothing.
            self.seek(0)
        self._resume_streams()
        self._finished = False
        self._playing = True
        self._reset_correction_state()
        # The lead-in is counted from here, not from whenever the last tick happened to
        # be: without this the first tick after a pause would add the whole pause.
        self._tick_clock.start()
        self._timer.start()
        self.state_changed.emit(True)

    def pause(self) -> None:
        """Stop where we are - **including** the streams Qt has already stopped.

        Through `_park` rather than `pause` for the reason `_park` measures: pausing a
        player that has reached the end of its file rewinds it to zero and pushes frame
        zero into its sink. `_on_finished` was given that care and this was not, so
        pausing while the shorter stream had already run out sent it back to its first
        frame, and resuming replayed its opening seconds against a timeline that was
        elsewhere. Measured on 2026-11-05 with a real 10 s and a real 4 s clip: paused
        at 7466 ms the short stream went 4000 -> 0, and 1.5 s after resuming it was at
        1433 ms against a timeline of 8933 - `drift()` -7500 ms, and not a word said.
        """
        for _index, player in self._in_clock_order():
            _park(player)
        self._timer.stop()
        if self._playing:
            self._playing = False
            self.state_changed.emit(False)

    def toggle(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        master = self._master_player()
        if master is not None:
            _call(master, "stop")
        for _index, follower in self._followers():
            _call(follower, "stop")
        self._timer.stop()
        self._finished = False
        self._timeline = 0
        if any(self._offsets):
            # `stop` puts every player back at its own 0, which is the timeline's 0 only
            # when nothing is slid. Without this the two streams would sit on frames
            # that are not the same instant and `position()` would read the difference
            # back as a timeline that is not 0. Guarded, so an ordinary recording still
            # stops with no seek at all.
            self._place_positions(0)
        self._reset_correction_state()
        self._reset_ended_state()
        # Everything is back at nought, so there is nothing in flight to dim for.
        self._end_seeking()
        was_playing = self._playing
        self._playing = False
        self._last_position = 0
        self.position_changed.emit(0)
        if was_playing:
            self.state_changed.emit(False)

    def seek(self, milliseconds: int) -> None:
        """Put every player at the same instant of the *timeline*, master first.

        Playback is stopped for the move and started again afterwards, so that no
        follower is ever positioned against a master that is still running - that is the
        tail-chasing the correction must not be made to do.

        "The same instant" is a timeline instant, not a file position: each stream goes
        to `milliseconds + its own offset` (§2.1), which is the same frame in each file
        and two different places inside them when they did not start together.

        **A seek that takes a badge away says so even mid-drag.** `seeking_changed`
        going True is the one thing that clears «запись закончилась» in the views, and
        `_begin_seeking` deliberately keeps quiet while an earlier seek is still in
        flight - a False/True pair in the middle of a scrubber drag is a flash. But a
        drag out of the tail of a shorter stream is made of seeks one `sliderMoved`
        apart, well inside `SEEK_TIMEOUT_MS`, so the engine cleared its own ended flags
        and the viewer was never told. Measured on 2026-11-05 against the take in
        `scratch/twofinal` with the second stream slid 7000 ms, so that it runs out at
        2717 of a 10217 ms timeline: `seek(9000)` emitted `stream_ended(1)`, and
        `seek(2000)` 60 ms later left `stream_has_ended(1)` False while
        `seeking_changed` had said **True once** for the two seeks - the badge standing
        over a stream playing its middle. The same run on this body says
        **True, True, False**. Said again only when there is a badge to take away:
        True after True is not a flash, a False in between would be.
        """
        master = self._master_player()
        if master is None:
            self.problem.emit(
                _("There is nothing to seek: no file of this set opened.")
            )
            return

        target = max(0, int(milliseconds))
        length = self.duration()
        if length > 0:
            target = min(target, length)
        elif self._timeline_is_empty():
            # A timeline 0 ms long has exactly one instant, and it is 0. Without this
            # the clamp above was skipped - `duration()` of 0 also means "no decoder
            # has reported a length yet", which must not be clamped against - and a
            # set whose offsets leave nothing to show accepted any position at all.
            target = 0

        # Before anything is told to move: §6 wants the views dimmed for the whole of
        # the gap, and the first `setPosition` is where the gap opens.
        self._begin_seeking(target, clearing_a_badge=any(self._ended_streams))
        # The playhead is leaving where it was, so a stream that had run out has not
        # run out any more - and if the target is past its end again, the next tick
        # will say so again.
        self._reset_ended_state()

        was_playing = self._playing
        if was_playing:
            # `_park`, not `pause`, for the reason `pause()` gives: a stream that has
            # already run out is stopped at its last frame, and pausing it there would
            # rewind it to zero before `_place_positions` had said where it belongs.
            for _index, player in self._in_clock_order():
                _park(player)

        self._seeking = True
        try:
            self._place_positions(target)
        finally:
            self._seeking = False

        # The playhead has been put somewhere; whatever that instant is past has run
        # out, and a scrub made while paused is the one case `_tick` cannot say so for.
        self._badge_the_outrun(self._streams_the_timeline_has_outrun(target))

        self._finished = False
        self._reset_correction_state()

        if was_playing:
            self._resume_streams()

        self._last_position = target
        self.position_changed.emit(target)

    def step(self, frames: int) -> None:
        """Nudge by whole frames, forwards or backwards. Stepping implies pausing."""
        if frames == 0:
            return
        if self._playing:
            self.pause()
        frame_ms = max(1, int(round(1000.0 / self.recording.framerate)))
        self.seek(self.position() + int(frames) * frame_ms)

    def set_speed(self, rate: float) -> None:
        try:
            wanted = float(rate)
        except (TypeError, ValueError):
            self.problem.emit(
                _("{value} is not a playback speed.").format(value=repr(rate))
            )
            return
        clamped = min(_MAX_RATE, max(_MIN_RATE, wanted))
        if clamped != wanted:
            self.problem.emit(
                _(
                    "A speed of {wanted}x is outside {least}x - {most}x; "
                    "using {used}x."
                ).format(
                    wanted=f"{wanted:g}",
                    least=f"{_MIN_RATE:g}",
                    most=f"{_MAX_RATE:g}",
                    used=f"{clamped:g}",
                )
            )
        self._rate = clamped
        master = self._master_player()
        if master is not None:
            _call(master, "setPlaybackRate", clamped)
        for _index, follower in self._followers():
            _call(follower, "setPlaybackRate", clamped)

    def speed(self) -> float:
        return self._rate

    # -- two files that did not start together, §2.1 -----------------------

    def offset(self, index: int) -> int:
        """How far into this stream the timeline's zero falls, in milliseconds."""
        if not 0 <= index < len(self._offsets):
            return 0
        return self._offsets[index]

    def offsets(self) -> list[int]:
        """Every offset, in `recording.screens` order: what "Save this set" writes."""
        return list(self._offsets)

    def set_offset(self, index: int, milliseconds: int) -> None:
        """Slide one stream against the others, live, without moving the timeline.

        §2.2 is emphatic about the two halves of this. **Live**: the moved stream
        reseeks at once and the picture updates while paused, because lining two
        clapperboards up by eye is impossible otherwise; while playing it applies
        without stopping anything else. **Without moving the timeline**: the viewer is
        sliding one video against the other, not scrubbing, so the position is read
        before the change and pinned across it.

        No seeking state is entered. `seeking_changed` dims every view, and dimming the
        whole stage on each press of "+1 frame" is exactly the visible restart §2.2 says
        this adjustment must not cause - and one stream moving is not the two-instants
        problem that state exists for.
        """
        if not 0 <= index < len(self._offsets):
            self.problem.emit(
                _("There is no screen {index} in this recording to line up.").format(
                    index=index
                )
            )
            return
        try:
            wanted = int(milliseconds)
        except (TypeError, ValueError, OverflowError):
            # OverflowError is in the tuple because `int(float('inf'))` raises that and
            # not a ValueError. Every other bad input here - 'abc', None, [1], nan -
            # already became a sentence, and a public method with one input that turns
            # into a sentence and one that aborts the caller is the worse of the two.
            self.problem.emit(
                _("{value} is not an offset in milliseconds.").format(
                    value=repr(milliseconds)
                )
            )
            return
        inside = _within_the_offset_limit(wanted)
        if inside != wanted:
            # Said rather than silently obeyed, and clamped rather than passed on: a
            # value past a qint64 leaves `_call(player, 'setPosition', ...)` as an
            # OverflowError, which `_call` deliberately does not guard.
            self.problem.emit(
                _(
                    "An offset of {wanted} ms is outside {least} - {most} ms; "
                    "using {used} ms."
                ).format(
                    wanted=wanted,
                    least=-OFFSET_LIMIT_MS,
                    most=OFFSET_LIMIT_MS,
                    used=inside,
                )
            )
            wanted = inside
        if wanted == self._offsets[index]:
            return  # a no-op must cost nothing: no seek, no signal, no stutter

        here = self.position()
        self._offsets[index] = wanted
        # Pinned before anything moves. If the stream being slid is the master, its
        # playhead is about to stop meaning what it meant, and this is the value
        # `position()` falls back on while that is true.
        self._timeline = here

        player = self._players[index]
        target = here + wanted
        if player is not None:
            _call(player, "setPosition", max(0, target))
            if target < 0:
                _call(player, "pause")
            elif self._playing:
                _call(player, "play")
            # It was just put where it belongs, so the correction must not read its
            # old position on the next tick and call that a drift.
            self._settle_left[index] = SETTLE_MS
            self._corrections[index] = 0
            self._given_up[index] = False
        self._set_waiting(index, target < 0)

        # The playhead has moved inside this stream, so whatever it had run out of, it
        # has not run out of any more. The next tick says so again if it has.
        self._ended_streams[index] = False

        self._announce_duration()
        self.offsets_changed.emit()

    def stream_is_waiting(self, index: int) -> bool:
        """Is the timeline before this stream's first frame?

        Askable as well as announced, for the same reason `stream_has_ended` is: a pane
        built after the signal went out would otherwise never know.
        """
        return 0 <= index < len(self._waiting) and self._waiting[index]

    def _place_positions(self, timeline: int) -> None:
        """Put every live stream where `timeline` falls inside it. Master first.

        Nothing is started or stopped here - the callers own that - except that a stream
        which turns out to have nothing to show is paused, because a player parked at
        its first frame and left running would play its opening seconds early.
        """
        self._timeline = timeline
        for index, player in self._in_clock_order():
            target = timeline + self._offsets[index]
            _call(player, "setPosition", max(0, target))
            if target < 0:
                _call(player, "pause")
            self._set_waiting(index, target < 0)

    def _resume_streams(self) -> None:
        """Start every live stream that has something to show. Master first.

        Two kinds of stream are left where they are, and they are the same exemption
        seen from the two ends of a file. One is *waiting*: the timeline has not
        reached its first frame, and starting it would play its opening seconds early.
        The other has been *outrun*: the timeline is past its last frame, so there is
        nothing there to start - and starting it anyway is what made a pause in the
        tail of an unequal pair replay the short stream from zero. Qt gives a stopped
        player at `EndOfMedia` no way to "play nothing"; `play()` on it starts the file
        again from the beginning.

        An outrun stream is badged on the way past, so the viewer is told once that
        this screen has run out rather than being shown the wrong seconds of it.

        **Which streams are outrun is now measured and not allowed for.** The test
        used to call the last `CORRECTION_MS` of a file "nothing there", so a stream
        with 50 ms left was never started; `_streams_the_timeline_has_outrun` has the
        measurements for why that is wrong at the transport's end of it. The protection
        this exemption exists for does not weaken, because the player Qt would restart
        from zero is the player Qt has already stopped, and `_at_the_end` reads that
        from Qt rather than inferring it from a distance.
        """
        here = self.position()
        outrun = self._streams_the_timeline_has_outrun(here)
        self._badge_the_outrun(outrun)
        skip = set(outrun)
        for index, player in self._in_clock_order():
            if self._waiting[index] or index in skip:
                continue
            _call(player, "play")

    def _set_waiting(self, index: int, waiting: bool) -> None:
        """Record and announce the waiting state, saying it only when it changes."""
        if not 0 <= index < len(self._waiting):
            return
        if self._waiting[index] == waiting:
            return
        self._waiting[index] = waiting
        self.stream_waiting.emit(index, waiting)

    def _update_waiting(self, here: int) -> None:
        """Let streams in and out of the waiting state as the timeline passes them.

        Only the transitions do anything. A stream that has been waiting for a second
        is not seeked again every tick, and a recording with no offsets never enters
        this branch at all.
        """
        for index, player in self._in_clock_order():
            target = here + self._offsets[index]
            waiting = target < 0
            if waiting == self._waiting[index]:
                continue
            if waiting:
                _call(player, "pause")
                _call(player, "setPosition", 0)
            else:
                # Its first frame has arrived. Put it exactly where it belongs rather
                # than letting the correction discover it a tick later.
                _call(player, "setPosition", max(0, target))
                if self._playing:
                    _call(player, "play")
            self._settle_left[index] = SETTLE_MS
            self._set_waiting(index, waiting)

    def _announce_duration(self) -> None:
        """Say the length if it has changed since anyone was last told."""
        length = self.duration()
        if length == self._last_duration:
            return
        self._last_duration = length
        self.duration_changed.emit(length)

    # -- sound -------------------------------------------------------------

    def audio_index(self) -> int:
        """Which stream is heard, or -1 when none is. §1.2.

        A method rather than an attribute so that a view asking late gets the live
        answer; `player.window._Engine` reads either spelling.
        """
        return self._audio_index

    def set_audio_source(self, index: int) -> None:
        """Move the sound to another stream, or to none at all. §1.2.

        «Звук: Экран 1 / Экран 2 / Выключен», and it is deliberately a different
        question from which screen is on the left: swapping the picture does not move
        the sound and this does not move the picture.

        **One output, moved, never a second one made.** The engine holds a single
        `QAudioOutput` for its life and hands it from player to player, which is the
        only arrangement in which two copies of one voice cannot reach the speakers -
        §5 of `PLAYER_LAYOUTS.md` is unchanged by this - and which carries the volume
        and the mute across the change rather than resetting them to a device default
        in the middle of a sentence.

        **The clock goes with it.** The master is the file carrying the sound, for the
        reason the module docstring opens with: a follower is the stream the correction
        is allowed to seek, and a seek in the audio track is the one seam a listener
        hears. So the stream that gains the sound gains the clock, takes the master's
        status signal with it, and the correction starts afresh against it. Nothing is
        sought: the two streams are inside `CORRECTION_MS` of each other or the
        correction would already have said so, which is why §2.2's promise that this
        kind of control does not move the picture survives it.

        Silencing (-1) leaves the clock where it is. There is still a recording to
        play, something still has to be its master, and taking the sound away is no
        reason to hand the timeline to a different file.
        """
        try:
            wanted = int(index)
        except (TypeError, ValueError, OverflowError):
            self.problem.emit(
                _("{value} is not a screen to take the sound from.").format(
                    value=repr(index)
                )
            )
            return
        if wanted >= 0 and not 0 <= wanted < len(self._players):
            self.problem.emit(
                _(
                    "There is no screen {index} in this recording to take the sound "
                    "from."
                ).format(index=wanted)
            )
            return
        if wanted >= 0 and self._players[wanted] is None:
            self.problem.emit(
                _("{name} did not open, so the sound cannot come from it.").format(
                    name=self._name_of(wanted)
                )
            )
            return
        if wanted < 0:
            wanted = -1
        if wanted == self._audio_index:
            return  # a no-op costs nothing: no detach, no signal, no gap in the sound

        if 0 <= self._audio_index < len(self._players):
            # Detached before anything is attached, so there is no instant at which two
            # players hold the same output.
            _call(self._players[self._audio_index], "setAudioOutput", None)

        if wanted < 0:
            self._audio_index = -1
            self.audio_changed.emit(-1)
            return

        if self._audio is None:
            # None when this machine had no audio device at build time, or when the
            # file that carried the sound failed and the clock was handed on. Asking
            # again is worth it: the viewer has just said they want to hear something.
            self._audio = self._make_audio(self)
        if self._audio is None:
            self._audio_index = -1
            self.problem.emit(
                _(
                    "There is no sound output on this machine to play the recording "
                    "through."
                )
            )
            self.audio_changed.emit(-1)
            return

        _call(self._players[wanted], "setAudioOutput", self._audio)
        self._audio_index = wanted
        self._take_the_clock(wanted)
        self.audio_changed.emit(wanted)

    def _take_the_clock(self, index: int) -> None:
        """Make `index` the master, without moving anything that is playing.

        The timeline is pinned first because the clock is about to be read off a
        different playhead: the two sit a measured 33 ms apart on one file, and that is
        the value `position()` falls back on for the instant between the two readings.
        """
        if index == self._master_index:
            return
        self._timeline = self.position()
        self._disconnect_status()
        self._master_index = index
        self._connect_status(self._players[index])
        # The old master is a follower now and has never been measured as one; the new
        # one is exempt from a correction it may have been the subject of a tick ago.
        self._reset_correction_state()

    def set_muted(self, muted: bool) -> None:
        if self._audio is None:
            self.problem.emit(_("This recording has no sound to mute."))
            return
        _call(self._audio, "setMuted", bool(muted))

    def set_volume(self, value: float) -> None:
        if self._audio is None:
            self.problem.emit(
                _("This recording has no sound to set the volume of.")
            )
            return
        try:
            wanted = float(value)
        except (TypeError, ValueError):
            self.problem.emit(
                _("{value} is not a volume.").format(value=repr(value))
            )
            return
        _call(self._audio, "setVolume", min(1.0, max(0.0, wanted)))

    # -- where we are ------------------------------------------------------

    def position(self) -> int:
        """Where the *timeline* is, which is not where any one file is.

        Read off a stream rather than counted here, so there is only ever one clock:
        `stream position - its offset` is the timeline, and with no offsets anywhere
        that is the master's playhead exactly as it always was.

        The master is preferred and a stream that cannot tell the time is skipped -
        pinned at its first frame because the timeline has not reached it, or pinned at
        its last one while the timeline covers more. `_tells_the_time` owns that rule
        and the module docstring says why it is one rule and not two. While *no* stream
        can speak - a lead-in the viewer created by sliding both files late, or a tail
        where everything has run out - there is no playhead to read and the last value
        pinned is returned, which `_tick` keeps fresh and advances.
        """
        for index, player in self._in_clock_order():
            if not self._tells_the_time(index, player):
                continue
            inside = int(_call(player, "position") or 0)
            return max(0, inside - self._offsets[index])
        return self._timeline

    def _tells_the_time(self, index: int, player: Any) -> bool:
        """May this stream's playhead be read as the timeline right now?

        Two refusals, one from each end of the file, and the module docstring sets out
        why they are the same question asked twice.

        The far end is deliberately **not** "has this stream ended". A stream whose own
        length reaches `duration()` is the longest thing in the recording and its last
        frame is the timeline's last frame, so its playhead stays the clock to the very
        end - which is how a one-video recording and an equal-length recorder-made set
        go through here without one line of behaviour changing. Only a stream the
        timeline *outlives* is dropped, because only that one has a wrong answer to
        give: its playhead stops where its file does while the recording runs on.

        A length of 0 is a decoder that has not reported yet rather than an empty file,
        and nothing can be concluded from it, so such a stream is believed.
        """
        if self._waiting[index]:
            return False
        length = int(_call(player, "duration") or 0)
        if length <= 0:
            return True
        if length - self._offsets[index] >= self.duration():
            return True
        here = int(_call(player, "position") or 0)
        # `CORRECTION_MS` as the tolerance for the same reason `_has_landed` uses it: a
        # player's last reported position stops a frame or two short of the length it
        # advertises, so "exactly at its end" is a state no real decoder reaches.
        return here < length - CORRECTION_MS

    def _nobody_tells_the_time(self) -> bool:
        """Can no live stream be read as the timeline? Then `_tick` has to count."""
        return not any(
            self._tells_the_time(index, player)
            for index, player in self._in_clock_order()
        )

    def duration(self) -> int:
        """How much of the timeline is covered by something: §2.1's line.

            duration = max(0, max_i(duration_i - offset_ms[i]))

        §6's "the transport's duration is the longest stream" with the offsets folded
        in: a stream wound forward by two seconds has two seconds less to give at the
        end, and one slid late has that much more. With no offsets it is the longest
        file, as it always was. `stream_duration` is the per-stream number and stays a
        file's own length, not a span of timeline.
        """
        lengths = [
            int(_call(player, "duration") or 0) - self._offsets[index]
            for index, player in self._live()
        ]
        return max(0, max(lengths)) if lengths else 0

    def stream_duration(self, index: int) -> int:
        """How long one screen's own file is, in milliseconds. 0 when unknown.

        A view cannot work this out from `duration()` and `position()`: those describe
        the recording, and the question the §6 badge answers is about one stream. 0 is
        returned for a screen whose file never opened as well as for one whose length
        the decoder has not reported yet - neither can be badged, and a caller that
        wants to know which is which has `sinks()` and `startup_problems()`.
        """
        if not 0 <= index < len(self._players):
            return 0
        return int(_call(self._players[index], "duration") or 0)

    def stream_has_ended(self, index: int) -> bool:
        """Whether `stream_ended` has already been said about this screen.

        A signal is news, and a pane built after the shorter stream ran out would never
        hear it. The state has to be askable as well as announced.

        Exactly what the signal says and not the flag behind it, which is why
        `_covers_the_end` is asked here too: the flag also exempts a stream from the
        correction, and `PlayerWindows` badges a pane it builds late out of this
        answer. A stream that ran out *with* the recording would otherwise reach a new
        pane as a badge the signal had deliberately not sent.
        """
        return (
            0 <= index < len(self._ended_streams)
            and self._ended_streams[index]
            and not self._covers_the_end(index)
        )

    def seeking(self) -> bool:
        """True while a seek is in flight: §6's loading state, for a late subscriber."""
        return self._seek_pending

    def playing(self) -> bool:
        return self._playing

    def drift(self) -> list[int]:
        """One entry per screen: how far it sits from where it should be, in ms.

        "Where it should be" is `timeline + its offset`, not the master's playhead -
        a stream the viewer has deliberately slid is not drifting. With no offsets the
        two are the same number and this is what it always was: distance from the
        master.

        The master's own entry is 0, and so is a screen whose file never opened, so the
        list lines up with `recording.screens` and with `sinks()`.

        **A stream the timeline has outrun is measured against its own last frame**,
        because that is where it should be: the correction is forbidden to pull it any
        further and `setPosition` could not put it there anyway. The target is clamped
        at the stream's own length, which is what `_has_landed` already does with the
        same word - "a stream shorter than the target cannot land on it, so sitting on
        that frame is as arrived as it will ever be".

        Measured without that clamp, on 2026-11-05, a real 10.217 s stream beside a
        4.0 s one, half a second apart each reading: **-433, -933, -1483, -1983,
        -2433, -2933, -3433** - a number growing by the length of every tick, which
        says nothing about the recording and fails `tools/verify_player.py`'s sync
        check on any pair whose streams are not the same length.

        What it does **not** hide is the fault §13.2 was: a stream Qt rewound to zero
        and let replay reads as the whole of its own length away from where it should
        be, because the clamp moves the target, not the reading.
        """
        master = self._master_player()
        if master is None:
            return [0] * len(self._players)
        here = self.position()
        out: list[int] = []
        for index, player in enumerate(self._players):
            if player is None or index == self._master_index:
                out.append(0)
            else:
                there = int(_call(player, "position") or 0)
                target = here + self._offsets[index]
                length = int(_call(player, "duration") or 0)
                if length > 0:
                    # Clamped where `setPosition` clamps, so a stream the timeline has
                    # run past is measured against the frame it is meant to be holding.
                    target = min(target, length)
                out.append(there - target)
        return out

    # -- the seeking state of §6 -------------------------------------------

    def _begin_seeking(self, target: int, *, clearing_a_badge: bool = False) -> None:
        """Enter the loading state, unless there is nothing for it to protect.

        **A single-stream recording never dims.** The state exists because two panes
        can be showing two different instants while one of the decoders is still on its
        way; one stream cannot disagree with itself, so for it the dimming would be a
        flicker on every scrubber drag and every frame step of the commonest recording
        there is. §6 and the task that asked for this both say the one-stream case must
        not get worse, and this is where that is decided.

        A seek that arrives while another is still in flight moves the target and
        restarts the wait without saying `seeking_changed` again: the picture is already
        dimmed, and a False/True pair would be a flash in the middle of a scrub.

        `clearing_a_badge` is the one exception, and `seek()` has the measurement: the
        views clear «запись закончилась» on True and on nothing else, so a seek that
        has just un-ended a stream has to be heard even mid-drag. It is a True said
        twice over, never a False and a True, so there is nothing for it to flash.
        """
        if len(self._live()) < 2:
            return
        self._seek_target = target
        self._seek_waited = 0
        self._landing_clock.start()
        self._landing.start()
        if self._seek_pending and not clearing_a_badge:
            return
        self._seek_pending = True
        self.seeking_changed.emit(True)

    def _end_seeking(self) -> None:
        """Leave the loading state. Safe to call when it was never entered."""
        self._landing.stop()
        if not self._seek_pending:
            return
        self._seek_pending = False
        self.seeking_changed.emit(False)

    def _check_landing(self) -> None:
        """Have the players arrived yet? Driven by `_landing`, not by `_tick`.

        The wait is bounded by `SEEK_TIMEOUT_MS` and the bound is not a safety net that
        should never be reached - it is the ordinary outcome for a file that cannot
        seek, and for a seek into the tail of a recording whose shorter stream has no
        frame there to land on. Both of those must leave the picture lit.

        The waiting is counted off a clock and not in ticks, for the reason the
        constructor measured beside this timer: counted in ticks of a coarse timer, the
        500 ms bound really took 546-557 ms.
        """
        if not self._seek_pending:
            self._landing.stop()
            return
        self._seek_waited += self._measured_gap(self._landing_clock)
        live = self._live()
        if not live or self._seek_waited >= SEEK_TIMEOUT_MS:
            self._end_seeking()
            return
        if all(self._has_landed(index, player) for index, player in live):
            self._end_seeking()

    def _has_landed(self, index: int, player: Any) -> bool:
        """Is this player near enough to its own target to stop dimming for it?

        `_seek_target` is a timeline instant; the place this player has to reach is
        that plus its offset, clamped at 0 for a stream the timeline has not got to
        yet. Measuring every stream against the bare timeline would leave a slid pair
        dimmed until the timeout on every single seek.

        `CORRECTION_MS` is the tolerance rather than zero, for the reason the module
        docstring gives it: two Media Foundation pipelines on one file were measured a
        constant 33 ms apart, so "exactly on the target" is a state no real player
        reaches and waiting for it would run into the timeout every single time.
        """
        target = max(0, self._seek_target + self._offsets[index])
        here = int(_call(player, "position") or 0)
        if abs(here - target) <= CORRECTION_MS:
            return True
        length = int(_call(player, "duration") or 0)
        # A stream shorter than the target cannot land on it - the target is past its
        # last frame - so sitting on that frame is as arrived as it will ever be.
        # Without this, every seek into the tail of an unequal-length recording would
        # dim the picture for the full `SEEK_TIMEOUT_MS` and then give up.
        if length <= 0 or target <= length:
            return False
        return here >= length - CORRECTION_MS

    # -- the ends of unequal streams, §6 -----------------------------------

    def _check_ends(self) -> None:
        """Mark every stream that has reached its own end since the last tick.

        Nothing here pauses or stops a stream: §6 says an ended stream holds its last
        frame, the decoder does that by itself, and what the view needs from the clock
        is to be told which screen it is.
        """
        if self._finished:
            return
        fresh: list[int] = []
        for index, player in self._live():
            if self._ended_streams[index]:
                continue
            length = int(_call(player, "duration") or 0)
            if length <= 0:
                continue  # a length the decoder has not reported yet is not an end
            # Within `CORRECTION_MS` of its own duration counts as ended: a player's
            # last reported position stops a frame or two short of the length it
            # advertises, and 80 ms is this module's measured name for a distance not
            # worth waiting out.
            if int(_call(player, "position") or 0) < length - CORRECTION_MS:
                continue
            fresh.append(index)
        self._note_ends(fresh)

    def _note_ends(self, indexes: list[int]) -> None:
        """Announce streams that have just run out - or finish, if none is left.

        Takes a list rather than one index because two streams of the same length run
        out on the same tick. Marking them one at a time would make the first one
        "a stream that ended while another still plays" for the instant before the
        second was looked at, so an ordinary equal-length recording would flash
        «Экран 1 — запись закончилась» over its final frame on the way to `finished`.

        **A stream that covers the end of the timeline is marked and not badged**, and
        batching alone was not enough to make that true. The two screens of one
        recording are a frame apart, not equal, and the paths into here do not all
        arrive in one batch: `_on_status` carries the master alone, and
        `_badge_the_outrun` carries whatever a scrub went past. Measured on 2026-11-05
        on a pair cut from this project's own footage to the 10200 ms and 10183 ms that
        one frame apart means, paused, scrubbed through the last 120 ms with the old
        body:

            120 ms from the end -> nothing    90 ms -> stream_ended(1)
            100 ms from the end -> nothing    87 ms -> stream_ended(1)
             80 ms from the end -> nothing    20 ms -> nothing

        - the second screen announced as finished, for a scrubber's width, over a
        recording the viewer would call one; the window is where the shorter stream is
        inside `CORRECTION_MS` of its end and the longer one is not. The master's own
        `EndOfMedia` did the same thing from the other path: with the old body it
        badged screen 1 while the second stream sat on its last frame.

        `_covers_the_end` is the test, and what such a stream gets instead is
        `finished`, once the last of them has gone. Both measurements are nothing on
        this body.
        """
        fresh = [
            index
            for index in indexes
            if 0 <= index < len(self._ended_streams) and not self._ended_streams[index]
        ]
        if not fresh:
            return
        for index in fresh:
            self._ended_streams[index] = True
        still_playing = [i for i, _ in self._live() if not self._ended_streams[i]]
        if not still_playing:
            # The last one. §6 keeps `finished` meaning the end of the session, so the
            # transport stops here and nothing is badged: there is no "others" left for
            # the badge to distinguish this screen from.
            self._on_finished()
            return
        for index in fresh:
            if self._covers_the_end(index):
                continue
            self.stream_ended.emit(index)

    def _streams_the_timeline_has_outrun(self, here: int) -> list[int]:
        """Which live streams have no frame at timeline instant `here`.

        **Two tests, and neither of them is a tolerance.** This allowed `CORRECTION_MS`
        until 2026-11-05, and `CORRECTION_MS` is a *sync distance* - how far two
        pipelines on one file sit apart - not a width at the end of a file. Borrowed
        here it made the last 80 ms of every recording, five frames at 60 fps, count as
        "there is nothing there", and `play()` pressed on any of them started the
        recording again from zero. Measured on 2026-11-05 against the two-screen take
        `scratch/twofinal` held at 02:54 that morning, 10217 ms and 9717 ms - that
        folder is re-recorded from time to time, so the lengths in it will not stay
        these - paused at each spot and then played for 400 ms, old body then this
        one:

            seek(duration - 300) -> 9917    play -> 10217 | 10217, the end as it should
            seek(duration -  80) -> 10137   play ->   383 | 10217
            seek(duration -  17) -> 10200   play ->   383 | 10217
            seek(duration)       -> 10217   play ->   383 |   383, which is §13.1

        One «-1 кадр» back from the end and then play, same take: 333 with the old
        body, 10217 with this one.

        A playhead *put* at the end needs no slack to be recognised: `setPosition`
        clamps at the length the decoder advertises and the player then reports that
        number exactly - measured on the same pair, `seek(10217)` left the master
        reading 10217 and the shorter stream reading 9717, each its own length.

        The second test is for the other way of arriving. A stream *played* to its end
        stops a frame short of what it advertises - measured 10250 of 10267 on the
        master of the take that folder held earlier the same day - so the arithmetic
        above would call it unfinished. `_at_the_end` asks Qt rather than guessing, and
        it is what keeps `_resume_streams` from handing `play()` to a player Qt would
        restart from zero.

        A length of 0 is a decoder that has not reported yet rather than an empty file,
        and nothing can be concluded from it, so such a stream is believed to have
        something to show - the same reading `_tells_the_time` and `_check_ends` give
        it.
        """
        out: list[int] = []
        for index, player in self._live():
            if _at_the_end(player):
                out.append(index)
                continue
            length = int(_call(player, "duration") or 0)
            if length <= 0:
                continue
            if here + self._offsets[index] >= length:
                out.append(index)
        return out

    def _frame_ms(self) -> int:
        """One frame of this recording, in whole milliseconds. 17 at 60 fps."""
        return max(1, int(round(1000.0 / self.recording.framerate)))

    def _covers_the_end(self, index: int) -> bool:
        """Does this stream reach the end of the timeline, give or take two frames?

        The question `_tells_the_time` asks of the clock - "is this stream the longest
        thing in the recording" - asked here of the badge, and with a width, because
        the two screens of one recording are not cut to the same millisecond. Measured
        on 2026-11-05 on the take `scratch/twofinal` held that morning, one this
        recorder made: **10267 ms and 10250 ms**, 17 ms apart, which is the one frame
        at 60 fps that the README's "the screens start 17 ms apart" names. A stream
        that little short of the end did not run out *before* the others, it ran out
        *with* them, and the news for that is `finished`. Two frames rather than the
        one measured, so that a set landing on the boundary is not decided by a
        decoder's rounding.

        **It is a width and not an amnesty.** The take re-recorded into that folder at
        02:54 the same day is 10217 ms and 9717 ms - half a second apart, nowhere near
        two frames - and its second screen is badged on a scrub into that half second,
        measured, which is what §6 asks for.

        A stream whose length the decoder has not reported cannot be judged and is
        answered "no" - the same reading every other end test here gives a 0.
        """
        if not 0 <= index < len(self._players):
            return False
        length = int(_call(self._players[index], "duration") or 0)
        if length <= 0:
            return False
        return length - self._offsets[index] >= self.duration() - 2 * self._frame_ms()

    def _badge_the_outrun(self, outrun: list[int]) -> None:
        """Say `stream_ended` about streams the playhead has just been put past.

        `_check_ends` does this while playing, and it is driven by `_tick`, which does
        not run while playback does not. So scrubbing into the tail of a shorter stream
        while paused left that pane with no badge at all, although the mirror-image
        case - scrubbing back into the lead-in of a stream slid late - is badged on the
        same scrub by `_update_waiting`. Measured on 2026-11-05 with a real 10 s and a
        real 4 s clip: paused, `seek(8000)`, `stream_has_ended(1)` False and nothing
        emitted; the same engine with a -5000 ms offset answered
        `stream_is_waiting(1)` True.

        Nothing is said when *every* stream has been outrun. `stream_ended` is defined
        as a stream that ran out while at least one other is still playing, and the end
        of them all is `finished` - which a scrub made while paused is not.
        """
        if not outrun:
            return
        still = [index for index, _ in self._live() if index not in outrun]
        if not still:
            return
        self._note_ends(outrun)

    def _nothing_left_to_play(self) -> bool:
        """Has the timeline outrun every live stream? Then play starts again.

        Deliberately not `self._finished`: that flag means "this engine saw the session
        end", and a playhead scrubbed onto the last frame while paused is in exactly
        the same place with the flag unset. `play()`'s docstring has the measurement.

        **How wide "the end" is lives in `_streams_the_timeline_has_outrun`**, and it
        is as narrow as it can be: the playhead at or past the stream's own advertised
        length, or Qt saying `EndOfMedia`. While this question was asked with
        `CORRECTION_MS` of slack, play pressed in the last 80 ms - five frames at
        60 fps, a frame-step back from the end among them - restarted the recording
        instead of playing them.
        """
        live = self._live()
        if not live:
            return False
        return len(self._streams_the_timeline_has_outrun(self.position())) == len(live)

    def _timeline_is_empty(self) -> bool:
        """Has every stream been slid off the end of the timeline? §2.1's degenerate set.

        `duration()` is `max(0, max_i(length_i - offset_ms[i]))`, so an offset larger
        than a stream's own length takes that stream's whole contribution away; when
        every stream's does, the timeline is 0 ms long and no instant of it has a frame
        to show. Measured on 2026-11-05 with a real 10 s and a real 4 s clip, both given
        an offset of 20 000 ms: `duration()` 0, `seek(5000)` accepted, and `position()`
        then reported 5000 on a timeline 0 ms long.

        A `duration()` of 0 because no decoder has reported a length yet is the other
        thing, and is every engine for its first few hundred milliseconds, so it is told
        apart here rather than treated as this.
        """
        live = self._live()
        if not live:
            return False
        if any(int(_call(player, "duration") or 0) <= 0 for _index, player in live):
            return False
        return self.duration() <= 0

    def _reset_ended_state(self) -> None:
        """Nothing has run out any more. Called where the playhead actually moves.

        Deliberately not folded into `_reset_correction_state`, which `play()` also
        calls: resuming from a pause does not un-end a stream, and clearing the flags
        there would have the next tick find the same end again and say `stream_ended`
        about it a second time. A seek and a stop do move the playhead, and both clear
        it here.
        """
        for index in range(len(self._ended_streams)):
            self._ended_streams[index] = False

    # -- the one timer -----------------------------------------------------

    def _since_the_last_tick(self) -> int:
        """Milliseconds since the previous tick: measured rather than assumed.

        `_tick` used to add `TICK_MS` whatever the real gap was, and Qt's default timer
        type is coarse: a 100 ms coarse timer was measured here on 2026-11-05 firing
        every 109.45 ms on average. So a lead-in the arithmetic said was 5000 ms took
        **5.456-5.479 s** of wall clock, three runs with a real event loop and a real
        clip - about 9.5% long, which is the timer's own lateness and nothing else.
        With the timer made precise and this gap measured, that run-up is the 5000 ms
        it says it is: measured again on 2026-11-05 over `scratch/twofinal` with both
        streams slid -5000 ms, the engine said 5000, 5001 and 5001 ms at the instant
        the wall clock said 5000.1, 5000.8 and 5000.3 ms.
        """
        return self._measured_gap(self._tick_clock)

    def _measured_gap(self, clock: QtCore.QElapsedTimer) -> int:
        """How long since this clock was last read, floored at one `TICK_MS`.

        **The floor is for the hand-driven call, and the stated reason for it used to
        be wrong.** It said "a precise timer does not fire early", and the constructor
        a few hundred lines above had already measured one firing at 99.04 ms. Measured
        again on 2026-11-05, offscreen, two runs of 3 s each: a precise 100 ms timer
        gave 28 and 29 gaps, mean 99.95 and 100.00 ms, min 98.65 ms, max 101.53 - and
        **15 of 28 and 15 of 29 of those gaps were under 100 ms**. A precise timer
        fires early about half the time; it simply does not fire *much* early.

        That is what the floor really rests on, and it is a gap rather than a
        statement. A tick nobody's timer sent - `_tick()` called by hand, which is how
        the suite drives this engine - comes back **0 ms** (1000 back-to-back reads of
        a `QElapsedTimer`, min 0, max 0), while nothing a real timer sent came within
        48 ms of that. So flooring at one interval reads a hand call as one interval
        and leaves every real tick alone but for the sub-millisecond rounding.

        The cost of that rounding is measured rather than argued: over 5 s of real
        precise ticks, `max(TICK_MS, int(gap))` counts **+3.2 to +4.6 ms**, three runs,
        under a tenth of one percent - which is why the floor stays and no cleverer
        rule replaces it.
        """
        if not clock.isValid():
            clock.start()
            return TICK_MS
        return max(TICK_MS, int(clock.restart()))

    def _tick(self) -> None:
        # Read first and unconditionally, so the gap stays the gap between two ticks
        # however this one ends.
        elapsed = self._since_the_last_tick()
        if self._seeking:
            return
        master = self._master_player()
        if master is None:
            self._timer.stop()
            return

        if self._playing and self._nobody_tells_the_time():
            # No playhead can be read: every stream is either waiting for its first
            # frame - the lead-in a viewer makes by sliding both files late - or parked
            # on its last one while the timeline still covers more. The timeline is
            # advanced here instead, and the moment one stream can speak again
            # `position()` goes back to reading it off that stream, so this cannot
            # accumulate.
            self._timeline += int(round(elapsed * self._rate))

        here = self.position()
        # The fallback, kept fresh. `_timeline` is what `position()` returns once no
        # stream can tell the time, and the tick that discovers that is one tick too
        # late to work the value out - so it is written down while a stream still can.
        self._timeline = here
        if here != self._last_position:
            self._last_position = here
            self.position_changed.emit(here)

        if here != self._progress_from:
            self._master_advancing = True
        elif self._playing:
            # The measured gap, not the interval: this counter is quoted to the viewer
            # in seconds a sentence below, and a tick late by a tenth would have said
            # "3 s" after 3.3 s of a frozen picture.
            self._stalled_ms += elapsed
            if self._stalled_ms >= STALL_WARNING_MS and not self._stall_reported:
                self._stall_reported = True
                self.problem.emit(
                    _(
                        "{name} is not playing: it has not moved in {seconds} s. "
                        "The other screens are being left to run on their own."
                    ).format(
                        name=self._name_of(self._master_index),
                        seconds=f"{self._stalled_ms / 1000:g}",
                    )
                )

        self._announce_duration()

        # Before the ends and before the correction: a stream that has just reached its
        # first frame has to be let out of the waiting state before anything measures
        # it, or it would be corrected towards a place it is deliberately not at.
        self._update_waiting(here)

        # The ends are looked at before the correction and not after it, so that a
        # stream is exempt from being dragged about on the very tick it runs out. It is
        # also the backstop for a player that never says EndOfMedia - a stub, or a file
        # whose status signal we could not connect to - and it is a stronger one than
        # the `here >= length` test it replaced: that compared the *master's* position
        # against the *longest* stream's length, so a master that was the shorter file
        # never reached it and the recording never ended at all.
        self._check_ends()
        if not self._finished:
            self._correct(here, elapsed)

    def _correct(self, here: int, elapsed: int = TICK_MS) -> None:
        if not self._master_advancing:
            # The clock has not moved since playback started, so `here` is not a time
            # anything should be dragged to. Seeking every follower back to a stuck
            # master's position is how one bad file turns into two stuttering windows.
            return
        for index, follower in self._followers():
            if self._given_up[index]:
                continue
            if self._waiting[index]:
                # The timeline has not reached this stream's first frame, so there is
                # no frame to pull it to - the same exemption, from the other end, as
                # the ended one below. Without it the correction would seek it to a
                # negative position every tick and then complain that it will not stay
                # in step: the player fighting the viewer's own adjustment, which
                # §2.1 names as the failure to design against.
                continue
            if self._ended_streams[index]:
                # A stream that has run out cannot be pulled to where the master is:
                # there is no frame there to pull it to. Left alone it sits on its last
                # one, which is what §6 asks for. Without this exemption the correction
                # would seek it past its own end every tick and then announce it as a
                # file that "will not stay in step" - a complaint about nothing but an
                # ordinary recording whose two screens are not the same length. The
                # thresholds and the correction itself are untouched; this is the same
                # kind of exemption `_given_up` already is.
                #
                # There is no matching exemption for an ended *master* and none is
                # needed: `here` can no longer be an ended stream's frozen playhead,
                # because `_tells_the_time` stops reading the clock off a stream the
                # timeline outlives. That is where that half of this was fixed, and
                # fixing it here instead would have left the transport dead and only
                # stopped the engine complaining about it.
                continue
            if self._settle_left[index] > 0:
                # Counted off the same measured gap the timeline is, so that
                # `SETTLE_MS` is half a second of the viewer's time rather than five
                # ticks of whatever length the machine felt like giving us.
                self._settle_left[index] -= elapsed
                continue
            there = int(_call(follower, "position") or 0)
            # The target is where this stream is *meant* to be, offset and all. §2.1:
            # "the sync correction works on the offset target, not on equality."
            target = here + self._offsets[index]
            if abs(there - target) <= CORRECTION_MS:
                self._corrections[index] = 0
                continue
            _call(follower, "setPosition", target)
            self._settle_left[index] = SETTLE_MS
            self._corrections[index] += 1
            if self._corrections[index] >= GIVE_UP_AFTER:
                self._given_up[index] = True
                screen = self.recording.screens[index]
                self.problem.emit(
                    _(
                        "{name} will not stay in step (it is {distance} ms from the "
                        "sound after {tries} corrections); it is being left to run "
                        "on its own."
                    ).format(
                        name=screen.file.name,
                        distance=there - target,
                        tries=GIVE_UP_AFTER,
                    )
                )

    def _reset_correction_state(self) -> None:
        for index in range(len(self._players)):
            self._settle_left[index] = 0
            self._corrections[index] = 0
            self._given_up[index] = False
        # Where the clock is being started from, so the next tick can tell a master that
        # is running from one that is merely claiming to. A timeline value rather than
        # the master's raw playhead, because with an offset in force those are two
        # different numbers and the tick compares against this one.
        self._progress_from = self.position()
        self._master_advancing = False
        self._stalled_ms = 0
        self._stall_reported = False

    # -- what the players tell us -----------------------------------------

    def _on_error(self, index: int, text: str) -> None:
        name = self._name_of(index)
        # The text is Qt's own, and Qt says it in the language Windows is in.
        self.problem.emit(_("{name}: {detail}").format(name=name, detail=text))
        # A file that is on disk but broken is the case `_build`'s own fallback cannot
        # see: it only knows about files that are not there, and `Recording.load` has
        # already dropped those. If the clock itself is the broken one, hand the clock
        # on - unless it is plainly playing anyway, in which case the error was not
        # fatal and taking the sound away would be the bigger loss.
        if index == self._master_index and not self._master_advancing:
            self._hand_over_clock(
                _("{name} could not be played ({detail}).").format(
                    name=name, detail=text
                )
            )

    def _on_status(self, status: Any) -> None:
        if status == QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia:
            # The *master's* end, which §6 does not let us read as the recording's: the
            # master is the file carrying the sound, and it is perfectly ordinary for it
            # to be the shorter of the two. `_note_ends` finishes the session only when
            # nothing else is still running.
            self._note_ends([self._master_index])
        elif status == QtMultimedia.QMediaPlayer.MediaStatus.InvalidMedia:
            name = self._name_of(self._master_index)
            self._hand_over_clock(
                _("{name} is not something this machine can play.").format(name=name)
            )

    def _name_of(self, index: int) -> str:
        if 0 <= index < len(self.recording.screens):
            return self.recording.screens[index].file.name
        return "a file of this recording"

    def _hand_over_clock(self, why: str) -> None:
        """The file carrying the clock has failed: give the clock to a healthy player.

        Reached when a master that is present on disk turns out to be unplayable - a
        truncated or corrupt file. Without this the whole player freezes: the position
        never leaves 0, nothing ever finishes, and every follower is dragged back to 0
        on each tick until the correction gives up on it. One of them taking over costs
        the sound and says so; it does not cost the recording.
        """
        index = self._master_index
        dead = self._players[index] if 0 <= index < len(self._players) else None
        if dead is None:
            return  # already handed over, and the news arrived twice

        self._disconnect_status()
        _call(dead, "stop")
        _call(dead, "setAudioOutput", None)
        self._players[index] = None
        if self._audio_index == index:
            # §1.2 lets the sound sit on a stream of the viewer's choosing, and that
            # stream is the master by construction - so the sound dies with this file.
            # Guarded all the same: silencing a recording whose audio the viewer had
            # already moved elsewhere would be this method taking away something it
            # was not asked about.
            self._audio = None
            self._audio_index = -1
            self.audio_changed.emit(-1)

        heir = next((i for i, p in enumerate(self._players) if p is not None), None)
        if heir is None:
            self._timer.stop()
            was_playing = self._playing
            self._playing = False
            self.problem.emit(
                _("{why} There is nothing else in this set to play.").format(why=why)
            )
            if was_playing:
                self.state_changed.emit(False)
            return

        self._master_index = heir
        self._connect_status(self._players[heir])
        self._reset_correction_state()
        self.problem.emit(
            _(
                "{why} Screen {monitor} will drive the others and there will be no "
                "sound."
            ).format(why=why, monitor=self.recording.screens[heir].monitor)
        )
        if self._playing:
            # The new master may have been paused by a correction, or never started.
            # Through `_resume_streams` so that a stream the timeline has not reached
            # is not started early by the hand-over.
            self._resume_streams()

    def _on_finished(self) -> None:
        if self._finished:
            return
        self._finished = True
        # A seek still in flight when the recording ran out would leave the last frame
        # dimmed until the timeout, with nothing left to wait for.
        self._end_seeking()
        for _index, follower in self._followers():
            _park(follower)
        _park(self._master_player())
        self._timer.stop()
        if self._playing:
            self._playing = False
            self.state_changed.emit(False)
        self.finished.emit()


def _at_the_end(player: Any) -> bool:
    """True when Qt has run this player off the end of its file.

    Asked through `mediaStatus()`, which the stub players in the tests do not have -
    and a stub that does not have it has not run off the end of anything either, so
    "no" is the right answer for them.
    """
    status = _call(player, "mediaStatus")
    if status is None:
        return False
    end = getattr(
        getattr(QtMultimedia.QMediaPlayer, "MediaStatus", None), "EndOfMedia", None
    )
    return end is not None and status == end


def _park(player: Any) -> None:
    """Stop a player playing **without moving it**, which `pause()` does not do.

    Measured on 2026-11-05 in bare Qt, with one of this project's own files and no
    ScreenRec code in it at all:

        at EndOfMedia   position = 10267   state = StoppedState   frame: invalid
        after pause()   position =     0   state = PausedState    frame: 0

    So pausing a player that has reached the end of its file **rewinds it to the
    beginning and pushes frame zero into its sink**. `_on_finished` paused every player,
    which is why letting a recording play to the end threw the viewer back to 00:00 and
    showed the first frame of both files - on an ordinary recording, every time.

    A player already at the end needs no pausing: Qt has stopped it where it is, and
    leaving it there is what keeps the scrubber, the clock and the picture at the end
    where the viewer left them.
    """
    if player is None or _at_the_end(player):
        return
    _call(player, "pause")


def _call(target: Any, name: str, *args: Any) -> Any:
    """Call a method on a player if it has one.

    The engine is written against `QMediaPlayer` but must also run against the stub the
    tests inject, and against a Qt build whose player is missing something. A method that
    is not there is not a failure path worth a message on every tick - the behaviour it
    would have produced simply does not happen, and the drift check sees that.
    """
    if target is None:
        return None
    method = getattr(target, name, None)
    if method is None:
        return None
    return method(*args)
