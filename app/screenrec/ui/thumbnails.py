"""Live pictures of what you are about to record, grabbed with the bundled ffmpeg.

The source picker shows a card per screen and per window, and a card without a picture is
a list entry with extra spacing. So one frame is grabbed per source with ``ddagrab``,
scaled down, written as a PNG and loaded.

Four things decide the shape of this module, and all four are reasons not to simplify it:

* **The geometry is not ours to write.** A `recorder.VideoSource` already knows how to
  address a screen (``output_idx`` is the DXGI index, never renumbered) and how to map a
  desktop rectangle onto the duplicated surface, which on a rotated screen is not a
  translation. `recorder._filter_for` already knows that a rotated panel arrives sideways
  and needs ``transpose``. Both are measured and tested, so this module *calls* them; it
  does not restate them. A private name is imported on purpose - duplicating the mapping
  is exactly how a thumbnail starts disagreeing with the recording.
* **A QPixmap may only be constructed on the GUI thread.** Qt is explicit about this and
  the punishment is a random crash somewhere else entirely. The worker thread therefore
  produces *PNG bytes* and nothing else; the conversion to QImage and QPixmap happens in
  `ThumbnailLoader._deliver`, which runs on the GUI thread because the signal that calls
  it is connected as a queued connection.
* **A grab is slow** - a few hundred milliseconds, most of it ffmpeg starting up. It can
  never run on the GUI thread, and a key that has been grabbed once is kept for
  `CACHE_TTL`, so a picker that is rebuilt twice in a row costs one grab. The cache is
  deliberately short-lived: this is a picture of what you are *about to* record, and an
  always-on-top application that runs all day would otherwise show the frame it grabbed
  this morning, with nothing on the card saying so. Window keys are HWNDs and Windows
  recycles a handle once a window has closed, which is a second reason not to keep one.
* **A grab can fail**, and silence is the one answer that is not allowed. A window closes
  between being listed and being grabbed; ddagrab refuses an output; the bundled ffmpeg is
  missing. Every path ends in `ready(key, None)` plus a sentence in `problems[key]` and a
  line in the log, so the card can draw a placeholder and the user can be told why.

Scaling is done by ffmpeg, not by Qt: ``scale=240:-2`` in the filter chain means a
2560x1440 desktop is never carried through this process at full size.
"""
from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6 import QtCore, QtGui

from .. import paths, recorder, winapi

__all__ = [
    "CACHE_TTL",
    "DEFAULT_WIDTH",
    "GRAB_FRAMERATE",
    "GRAB_TIMEOUT",
    "KILL_DRAIN",
    "STOP_JOIN_TIMEOUT",
    "Thumb",
    "ThumbRequest",
    "ThumbnailLoader",
    "grab_png",
    "last_problem",
    "monitor_thumb",
    "window_thumb",
]

log = logging.getLogger(__name__)

#: The card in the source picker is 16:10.5 and about this wide.
DEFAULT_WIDTH = 240

#: ddagrab's own frame rate for a one-frame grab. It is the rate at which the duplication
#: loop hands frames on, so a very low number only means waiting longer for the first one.
GRAB_FRAMERATE = 30

#: A grab that has not finished by now is not going to. ddagrab can sit waiting for a
#: frame on an output that never changes, and a wedged child process must not wedge the
#: worker thread behind it. This is the ceiling for a grab **nobody is waiting on**;
#: `ThumbnailLoader.stop` does not wait it out, it kills the child (see `STOP_JOIN_TIMEOUT`).
GRAB_TIMEOUT = 15.0

#: How long a killed ffmpeg is given to actually die and hand back its pipes. A kill the
#: kernel has accepted is not usually refused for this long; the wait is here so that one
#: that is bounds the worker rather than blocking it.
KILL_DRAIN = 3.0

#: How long `stop()` waits for the worker thread. It is *not* compared with `GRAB_TIMEOUT`,
#: because `stop()` never waits for a grab to time out: it kills the child, and a killed
#: child is drained within `KILL_DRAIN`. The margin over `KILL_DRAIN` covers the winapi
#: calls in `_source_for` that happen before any child exists.
STOP_JOIN_TIMEOUT = 5.0

#: How long a grabbed frame is worth showing. A thumbnail is a live picture of what you
#: are about to record, so a stale one is a lie told silently; but a picker that is torn
#: down and rebuilt within a few seconds (opening it re-runs `refresh()`) should not pay
#: for the same grab twice. Seconds, measured with `time.monotonic`.
CACHE_TTL = 3.0

#: The last failure any grab in this process reported, for a caller that has only the
#: `None` from `monitor_thumb` / `window_thumb` to go on. `ThumbnailLoader` keeps its own
#: per-key record in `problems`, which is the one to prefer.
_LAST_PROBLEM = ""


def last_problem() -> str:
    """Why the most recent failed grab failed, or an empty string."""
    return _LAST_PROBLEM


def _first_line(text: str) -> str:
    """The first line worth reading out of an ffmpeg complaint, without its ``[tag @ ptr]``.

    ffmpeg prefixes each line with the filter instance and its address, which is noise in
    front of a user and changes on every run, so it is stripped here rather than shown.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[") and "] " in line:
            line = line.split("] ", 1)[1].strip()
        if line:
            return line
    return ""


def _fail(reason: str) -> str:
    """Record `reason` in the log and in `last_problem`, and hand it back."""
    global _LAST_PROBLEM
    _LAST_PROBLEM = reason
    log.warning("thumbnail: %s", reason)
    return reason


@dataclass(frozen=True)
class Thumb:
    """One grabbed frame, ready to draw."""

    pixmap: QtGui.QPixmap
    width: int
    height: int


@dataclass(frozen=True)
class ThumbRequest:
    """What to grab. Exactly one of `monitor` and `window` is set.

    It is a value, not a widget, so it crosses to the worker thread safely: `Monitor` and
    `WindowInfo` are both frozen dataclasses of plain numbers and strings.
    """

    key: str
    monitor: winapi.Monitor | None = None
    window: winapi.WindowInfo | None = None
    width: int = DEFAULT_WIDTH


#: What `ThumbnailLoader` calls to do the work: PNG bytes, or empty bytes and a reason.
#: Injectable so the loader's threading, caching and failure handling can be tested
#: without a GPU.
GrabFunc = Callable[[ThumbRequest], "tuple[bytes, str]"]


# --------------------------------------------------------------------- grabbing


def _source_for(request: ThumbRequest) -> tuple[recorder.VideoSource | None, str]:
    """Turn a request into the `VideoSource` the recorder would use for it.

    Returns (source, "") or (None, reason). The reason is a sentence, because it ends up
    in front of the user as the explanation under an empty card.
    """
    if request.monitor is not None and request.window is not None:
        return None, _fail(
            f"Thumbnail {request.key!r} asked for a monitor and a window at once; "
            f"a grab is one or the other."
        )

    if request.monitor is not None:
        return recorder.VideoSource(monitor=request.monitor, name=request.monitor.label), ""

    if request.window is None:
        return None, _fail(
            f"Thumbnail {request.key!r} asked for neither a monitor nor a window."
        )

    info = request.window
    if not winapi.window_is_alive(info.hwnd):
        return None, _fail(f"The window “{info.title}” has closed.")

    # Re-read the rectangle: the list the picker was built from may be seconds old and the
    # window may have been moved or resized since. Falling back to the listed rectangle is
    # better than refusing, and is what the recording itself would capture anyway.
    rect = winapi.window_rect(info.hwnd)
    if rect is None:
        rect = (info.x, info.y, info.width, info.height)
    x, y, width, height = rect
    if width <= 0 or height <= 0:
        return None, _fail(
            f"The window “{info.title}” has no visible rectangle "
            f"({width}x{height})."
        )

    monitors = winapi.list_monitors()
    if not monitors:
        return None, _fail("No DXGI output is attached to the desktop; nothing to grab.")
    # The centre first, so a window hanging over an edge is grabbed from the screen it is
    # mostly on; the top-left is the fallback for a window wider than a screen.
    monitor = winapi.monitor_at(monitors, x + width // 2, y + height // 2)
    if monitor is None:
        monitor = winapi.monitor_at(monitors, x, y)
    if monitor is None:
        return None, _fail(
            f"The window “{info.title}” is at {x},{y} {width}x{height}, which is "
            f"not on any monitor - minimised, or on another virtual desktop."
        )

    source = recorder.VideoSource(
        monitor=monitor, crop=(x, y, width, height), name=info.title
    )
    try:
        source.output_size()  # runs surface_crop, which refuses a rectangle off the screen
    except ValueError as exc:
        return None, _fail(f"The window “{info.title}” cannot be cropped: {exc}")
    return source, ""


def _filter_chain(source: recorder.VideoSource, width: int) -> str:
    """The filter chain for a one-frame PNG: down off the GPU, upright, scaled.

    ``force_download=True`` says the frames have to come down into system memory, which
    writing a PNG obviously requires. This used to be said by passing the literal
    ``"libx264"`` as the encoder, which stopped being true the day `_filter_for` began
    dispatching on the name. ``bgra`` keeps full chroma - a thumbnail is small, and
    yuv420p would throw away colour for nothing.
    """
    chain = recorder._filter_for(source, "libx264", "bgra", force_download=True)
    out_width, _out_height = source.output_size()
    target = recorder.even(min(width, out_width))
    # Only ever downwards: enlarging a 120 px window to 240 costs memory and gives a
    # blurrier picture than letting the card scale it. A source already smaller than the
    # card is sent at its own size.
    if 2 <= target < out_width:
        # Scaled in ffmpeg, not in Qt, so a 2560x1440 frame is never carried through this
        # process at full size. -2 keeps the height even and the aspect ratio intact.
        scale = f"scale={target}:-2"
        chain = f"{chain},{scale}" if chain else scale
    return chain


def _run_ffmpeg(
    args: list[str],
    register: Callable[[subprocess.Popen | None], None] | None = None,
) -> tuple[int, str]:
    """Run ffmpeg to completion. Returns (returncode, stderr-or-reason).

    `register` is handed the live process so `ThumbnailLoader.stop` can kill a grab that
    is still running instead of waiting out `GRAB_TIMEOUT`.
    """
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=recorder.CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return -1, f"ffmpeg could not be started: {exc}"

    if register is not None:
        register(proc)
    try:
        _, stderr = proc.communicate(timeout=GRAB_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            _, stderr = proc.communicate(timeout=KILL_DRAIN)
        except subprocess.TimeoutExpired:
            stderr = b""
        return -1, (
            f"ffmpeg did not produce a frame within {GRAB_TIMEOUT:.0f} s and was killed."
        )
    finally:
        if register is not None:
            register(None)
    return proc.returncode, stderr.decode("utf-8", "replace").strip()


def grab_png(
    request: ThumbRequest,
    register: Callable[[subprocess.Popen | None], None] | None = None,
) -> tuple[bytes, str]:
    """Grab one frame for `request` and return it as PNG bytes.

    Returns (bytes, "") or (b"", reason). **Safe on any thread** - it touches no Qt object,
    which is the whole point: the bytes cross back to the GUI thread and become a QPixmap
    there.
    """
    source, problem = _source_for(request)
    if source is None:
        return b"", problem

    try:
        ffmpeg = str(paths.ffmpeg_exe())
    except paths.BinaryNotFound as exc:
        return b"", _fail(str(exc))

    lavfi = source.ddagrab_args(GRAB_FRAMERATE, draw_mouse=False)
    chain = _filter_chain(source, request.width)

    # A folder of our own, removed below, so two grabs at once cannot collide and nothing
    # is left behind in %TEMP% if ffmpeg writes a partial file.
    try:
        folder = Path(tempfile.mkdtemp(prefix="screenrec-thumb-"))
    except OSError as exc:
        return b"", _fail(f"No temporary folder for the thumbnail: {exc}")

    out_path = folder / "frame.png"
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i", lavfi,
    ]
    if chain:
        args += ["-vf", chain]
    args += ["-frames:v", "1", "-f", "image2", "-y", str(out_path)]

    try:
        code, message = _run_ffmpeg(args, register)
        if code != 0:
            if message:
                # ffmpeg says the same thing four ways when an input will not open. The
                # whole dump goes to the log; the sentence the card shows is the first
                # line, which is the one that names the actual fault.
                log.info("thumbnail: ffmpeg said:\n%s", message)
            detail = _first_line(message) or f"exit code {code}"
            what = source.name or "the source"
            return b"", _fail(f"ddagrab could not grab {what}: {detail}")
        try:
            data = out_path.read_bytes()
        except OSError as exc:
            return b"", _fail(f"ffmpeg reported success but wrote no thumbnail: {exc}")
        if not data:
            return b"", _fail("ffmpeg wrote an empty thumbnail file.")
        return data, ""
    finally:
        # Best effort: a leftover temp folder is untidy, not broken, and the reason it
        # could not go is worth one log line rather than an exception over a thumbnail.
        shutil.rmtree(folder, ignore_errors=True)


def _thumb_from_png(data: bytes) -> tuple[Thumb | None, str]:
    """Turn PNG bytes into a `Thumb`. **GUI thread only** - it builds a QPixmap."""
    image = QtGui.QImage()
    if not image.loadFromData(data, "PNG"):
        return None, _fail("The grabbed frame could not be decoded as a PNG.")
    pixmap = QtGui.QPixmap.fromImage(image)
    if pixmap.isNull():
        return None, _fail("The grabbed frame decoded to an empty pixmap.")
    return Thumb(pixmap=pixmap, width=pixmap.width(), height=pixmap.height()), ""


def monitor_thumb(monitor: winapi.Monitor, width: int = DEFAULT_WIDTH) -> Thumb | None:
    """One frame of `monitor`, scaled to `width`. `None` if it could not be grabbed.

    **Blocking, and GUI thread only** (it builds a QPixmap). A few hundred milliseconds,
    so anything drawing more than one of these wants `ThumbnailLoader` instead. The reason
    for a `None` is in `last_problem()`.
    """
    data, problem = grab_png(ThumbRequest(key="", monitor=monitor, width=width))
    if problem or not data:
        return None
    thumb, _ = _thumb_from_png(data)
    return thumb


def window_thumb(info: winapi.WindowInfo, width: int = DEFAULT_WIDTH) -> Thumb | None:
    """One frame of the desktop where `info` is, scaled to `width`.

    DDA duplicates the desktop, so this is the window *as it appears* - anything sitting on
    top of it is in the picture, and so it will be in the recording. `None` if it could not
    be grabbed; the reason is in `last_problem()`. **Blocking, GUI thread only.**
    """
    data, problem = grab_png(ThumbRequest(key="", window=info, width=width))
    if problem or not data:
        return None
    thumb, _ = _thumb_from_png(data)
    return thumb


# ----------------------------------------------------------------- the loader


class ThumbnailLoader(QtCore.QObject):
    """Grabs thumbnails off the GUI thread and hands them back by signal.

    One worker thread drains one queue, so grabs happen in the order they were asked for
    and never more than one ffmpeg at a time. `ready` fires on the GUI thread for every
    request, successful or not::

        loader.ready.connect(self._on_thumb)
        loader.request("mon:0", monitor=monitors[0])

    The caller has a single code path: a key that is already in the cache is answered
    through the same signal, posted to the event loop rather than emitted inside
    `request`, so the answer never arrives before the call that asked for it returns.

    Failures are not cached. A window that was closing, or an output that was busy, is
    worth another try the next time the picker opens; a successful grab is kept, but only
    for `CACHE_TTL` - a thumbnail claims to be a live picture, and an entry that never
    expires turns the second and every later opening of the picker into a photograph of
    the first. The caller does not have to remember to invalidate anything; `clear()` is
    there for an immediate one.

    `stop()` before the loader is destroyed. It kills a grab in flight - including one
    whose child process has not started yet, because `_register` checks the stop flag
    under the same lock - and joins the worker for `STOP_JOIN_TIMEOUT`. That join is
    best-effort rather than a guarantee: a grab wedged somewhere unkillable would outlive
    it, so every emission from the worker is also guarded against the object having been
    deleted underneath it.
    """

    #: key, Thumb | None
    ready = QtCore.Signal(str, object)

    #: Internal: key, payload (PNG bytes from the worker, or a cached Thumb), problem.
    #: Connected as a queued connection, which is what moves the work onto the GUI thread.
    _finished = QtCore.Signal(str, object, str)

    def __init__(
        self,
        parent: QtCore.QObject | None = None,
        grab: GrabFunc | None = None,
    ) -> None:
        super().__init__(parent)
        self._grab: GrabFunc = grab if grab is not None else self._grab_with_kill
        self._queue: queue.Queue[ThumbRequest | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._stopped = False
        self._pending: set[str] = set()
        #: key -> (monotonic time of the grab, thumb). The timestamp is what makes the
        #: entry expire; see `CACHE_TTL`.
        self._cache: dict[str, tuple[float, Thumb]] = {}

        #: Why each failed key failed. Readable, keyed, and never cleared by a success
        #: elsewhere - the card that failed can still say why minutes later.
        self.problems: dict[str, str] = {}
        #: The most recent entry added to `problems`, for a status line.
        self.last_problem: str = ""

        self._finished.connect(self._deliver, QtCore.Qt.ConnectionType.QueuedConnection)

    # -- the public two ----------------------------------------------------

    def request(
        self,
        key: str,
        monitor: winapi.Monitor | None = None,
        window: winapi.WindowInfo | None = None,
        width: int = DEFAULT_WIDTH,
    ) -> None:
        """Ask for the thumbnail of `key`. `ready(key, thumb_or_None)` follows.

        An entry grabbed less than `CACHE_TTL` ago is answered from the cache; an older
        one is thrown away and grabbed again, so the picture cannot silently age.
        """
        cached = self._fresh(key)
        if cached is not None:
            # Through the same signal, and queued, so a caller that connects right after
            # calling request() still hears about it.
            self._emit_finished(key, cached, "")
            return

        if self._stopped:
            self._emit_finished(key, None, "The thumbnail loader has been stopped.")
            return

        if key in self._pending:
            return  # already queued; the one answer serves both callers

        self._pending.add(key)
        self._queue.put(ThumbRequest(key=key, monitor=monitor, window=window, width=width))
        self._ensure_thread()

    def stop(self) -> None:
        """Stop the worker and kill any grab in flight.

        Safe to call twice, and safe when nothing was ever requested.

        The flag is raised **inside the lock** that `_register` takes. That is what makes
        the two orderings both safe: either the child was already registered and is killed
        here, or it is registered a moment later and `_register` kills it itself. Without
        that, a grab whose `Popen` had not run yet survived `stop()` and ran on for
        `GRAB_TIMEOUT`, leaving a stray ffmpeg behind the closing application.
        """
        with self._lock:
            self._stopped = True
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError as exc:
                # The child is already gone in the usual case; anything else is worth a
                # line, because it means a stray ffmpeg may be left running.
                log.warning("thumbnail: could not kill the grab in flight: %s", exc)

        thread = self._thread
        if thread is None:
            return  # nothing was ever requested, or stop() was already called
        self._queue.put(None)  # wake the worker out of its blocking get()
        thread.join(timeout=STOP_JOIN_TIMEOUT)
        if thread.is_alive():
            log.warning(
                "thumbnail: the worker thread did not stop within %.0f s; it is a daemon "
                "and will not hold the process open, and its answer is discarded, but a "
                "grab may still be running.",
                STOP_JOIN_TIMEOUT,
            )
        else:
            self._thread = None
        self._pending.clear()

    # -- state a caller may want -------------------------------------------

    def cached(self, key: str) -> Thumb | None:
        """The thumbnail held for `key`, without asking for one.

        `None` once the entry is older than `CACHE_TTL`, so this agrees with what
        `request` would do rather than reporting a picture the loader would re-grab.
        """
        return self._fresh(key)

    def clear(self) -> None:
        """Forget every cached thumbnail, so the next request grabs a fresh frame.

        `_pending` goes with it. A key whose grab was already in the queue would
        otherwise be skipped by the next `request` as "already in flight", and the frame
        delivered for it was grabbed *before* the caller asked for fresh ones - exactly
        the stale picture `clear()` was called to get rid of.
        """
        self._cache.clear()
        self._pending.clear()
        self.problems.clear()
        self.last_problem = ""

    # -- internals ---------------------------------------------------------

    def _fresh(self, key: str) -> Thumb | None:
        """The cached thumb for `key` if it is younger than `CACHE_TTL`, else `None`.

        An expired entry is dropped on the way past, so the cache cannot grow a tail of
        frames for windows that closed hours ago.
        """
        entry = self._cache.get(key)
        if entry is None:
            return None
        grabbed_at, thumb = entry
        if time.monotonic() - grabbed_at <= CACHE_TTL:
            return thumb
        del self._cache[key]
        return None

    def _emit_finished(self, key: str, payload: object, problem: str) -> None:
        """Emit `_finished`, surviving the loader having been deleted underneath us.

        The worker can outlive `stop()` - the join is bounded - and `SourcePicker` stops
        its loader from `aboutToQuit`, with the C++ object deleted immediately after. An
        emit into that corpse raises `RuntimeError: Signal source has been deleted` on
        the worker thread, which is an unhandled traceback on exit. There is nothing to
        do about it but say so and let the thread end.
        """
        try:
            self._finished.emit(key, payload, problem)
        except RuntimeError as exc:
            log.warning(
                "thumbnail: the answer for %r was dropped, the loader is gone (%s)",
                key,
                exc,
            )

    def _grab_with_kill(self, request: ThumbRequest) -> tuple[bytes, str]:
        """The real grab, with the live process registered so `stop` can kill it."""
        return grab_png(request, register=self._register)

    def _register(self, process: subprocess.Popen | None) -> None:
        """Remember the live child, or kill it at once if `stop()` has already run.

        Called from the worker thread, under the same lock `stop()` raises the flag in,
        which is what closes the window between `stop()` reading `_process` and this
        grab's `Popen` returning.
        """
        with self._lock:
            self._process = process
            if process is None or not self._stopped:
                return
        # Outside the lock: killing can block, and nothing else needs the lock meanwhile.
        if process.poll() is not None:
            return
        try:
            process.kill()
        except OSError as exc:
            log.warning(
                "thumbnail: a grab started after stop() could not be killed: %s", exc
            )
        else:
            log.info("thumbnail: killed a grab that started after stop() was called.")

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        thread = threading.Thread(target=self._work, name="thumbnails", daemon=True)
        self._thread = thread
        thread.start()

    def _work(self) -> None:
        """The worker. Produces bytes only - never a QPixmap, never a QImage."""
        while True:
            request = self._queue.get()
            if request is None:
                return
            if self._stopped:
                # Answer anyway, so a card that is still on screen stops waiting.
                self._emit_finished(
                    request.key, None, "The thumbnail loader stopped before this grab ran."
                )
                continue
            try:
                data, problem = self._grab(request)
            except Exception as exc:  # a grab must not take the worker thread with it
                data, problem = b"", _fail(
                    f"Grabbing {request.key!r} raised {type(exc).__name__}: {exc}"
                )
            if self._stopped:
                # stop() ran while this grab was in flight. Whatever came back is a frame
                # nobody is going to look at, and caching it would make it the answer the
                # *next* loader-less moment serves. Say so instead of delivering it.
                self._emit_finished(
                    request.key, None, "The thumbnail loader stopped during this grab."
                )
                continue
            self._emit_finished(request.key, data if data else None, problem)

    @QtCore.Slot(str, object, str)
    def _deliver(self, key: str, payload: object, problem: str) -> None:
        """GUI thread. Build the QPixmap here, because only here is it allowed."""
        self._pending.discard(key)

        if isinstance(payload, Thumb):
            # A cache hit on its way back out. The entry keeps its original timestamp on
            # purpose: a picker reopened every two seconds must still get a fresh grab
            # once `CACHE_TTL` has passed, rather than keep the first frame alive by
            # asking for it.
            self.ready.emit(key, payload)
            return

        thumb: Thumb | None = None
        if isinstance(payload, (bytes, bytearray)) and payload:
            thumb, decode_problem = _thumb_from_png(bytes(payload))
            if thumb is None:
                problem = decode_problem

        if thumb is None:
            reason = problem or f"The thumbnail for {key!r} could not be grabbed."
            self.problems[key] = reason
            self.last_problem = reason
        else:
            self._cache[key] = (time.monotonic(), thumb)
            self.problems.pop(key, None)

        self.ready.emit(key, thumb)
