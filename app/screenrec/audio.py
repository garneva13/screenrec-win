"""Audio sources.

Two different mechanisms, for a reason:

* **The microphone** goes straight into ffmpeg as a DirectShow input. ffmpeg owns the device,
  the timestamps and the resampling, and there is nothing for us to get wrong.
* **System sound has no ffmpeg input on Windows** - mainline ffmpeg has no WASAPI loopback
  device. So we capture it ourselves with PortAudio's WASAPI loopback (pyaudiowpatch) and
  feed raw PCM to ffmpeg down a named pipe.

Why a named pipe and not ffmpeg's stdin: stdin is reserved for sending ``q``, which is what
makes ffmpeg write the MP4 trailer instead of leaving a headless file behind. Both were
measured working on this build before the design was fixed.

**Start-of-stream alignment.** Raw PCM carries no timestamps, so ffmpeg calls the first byte
t=0. The capture therefore starts *before* ffmpeg and throws everything away until ffmpeg
connects to the pipe; the first sample written is the first sample after the connection, so
t=0 of the audio is the moment ffmpeg opened the input.

**The sentences in `error` are English only, and that is a known hole.** The house rule is
that anything a user reads goes through `i18n._()` and has a Russian pair. It cannot here:
`i18n` imports `QtCore`, and this module is deliberately Qt-free - its only import outside
the standard library is `portaudio`, and its code runs on a capture thread that must not
touch Qt. `error` reaches the bar through `ui/app.py`, which is where the translation would
have to be done; until somebody owns that, a Russian bar shows these in English. The same
holds for `levels.py`, for the same reason.
"""
from __future__ import annotations

import ctypes
import re
import subprocess
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from . import portaudio

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2  # s16le
BLOCK_FRAMES = 480  # 10 ms

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
kernel32.CreateNamedPipeW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
]
kernel32.ConnectNamedPipe.restype = wintypes.BOOL
kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
kernel32.FlushFileBuffers.restype = wintypes.BOOL
kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
#: The lever the whole teardown rests on. A null OVERLAPPED means "cancel every pending
#: request on this handle", whichever thread issued it - including a *synchronous* one,
#: which is what `ConnectNamedPipe` is here. Measured on this machine on 2026-11-05: a
#: `ConnectNamedPipe` parked on another thread came back after the cancel, and both a
#: `FlushFileBuffers` stuck behind one and a `WriteFile` into a full buffer whose reader
#: had stopped returned 0.02 s after it.
kernel32.CancelIoEx.restype = wintypes.BOOL
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
PIPE_ACCESS_OUTBOUND = 0x00000002
PIPE_TYPE_BYTE = 0x00000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_NO_DATA = 232
ERROR_BROKEN_PIPE = 109
#: A client was already there before `ConnectNamedPipe` was called. Windows reports this
#: as a *failure*, and it means the pipe is connected.
ERROR_PIPE_CONNECTED = 535
#: The name is taken: a handle to a pipe of this name is still open somewhere. Before
#: this file let its handle go, that somewhere was this very process - see `stop`.
ERROR_PIPE_BUSY = 231
#: What a call broken by `CancelIoEx` reports. Measured in the probe above.
ERROR_OPERATION_ABORTED = 995

CREATE_NO_WINDOW = 0x08000000
#: How long to wait for ffmpeg to open its end of the pipe before giving up.
CONNECT_TIMEOUT = 10.0
#: How long a cancelled Win32 call on the pipe is given to come back before the feeder
#: stops waiting for it. Measured at 0.02 s on this machine, so this is pure slack.
CANCEL_TIMEOUT = 2.0
#: How long `_close_pipe` lets `FlushFileBuffers` run before it cancels it. Measured on
#: 2026-11-05: 0.0002 s, worst of five, with a client that is draining the pipe - which is
#: the only case the flush is kept for - and never at all with one that has stopped
#: reading. See `_flush`.
FLUSH_TIMEOUT = 0.25


# ------------------------------------------------------------------ enumeration


@dataclass(frozen=True)
class MicDevice:
    """A DirectShow audio capture device as ffmpeg sees it."""

    name: str  # the friendly name, for the UI
    alt_name: str  # the "Alternative name", stable across renames

    @property
    def ffmpeg_id(self) -> str:
        """What goes after ``audio=``. The alternative name is preferred: two devices can
        share a friendly name, and ffmpeg then picks whichever it enumerated first."""
        return self.alt_name or self.name


_DEVICE_LINE = re.compile(r'"([^"]+)"\s+\((audio|video)\)')
_ALT_LINE = re.compile(r'Alternative name\s+"([^"]+)"')


def parse_dshow_devices(text: str) -> list[MicDevice]:
    """Pull the audio devices out of ``ffmpeg -list_devices true -f dshow -i dummy``.

    Kept separate from the subprocess call so it can be tested against captured output.
    """
    devices: list[MicDevice] = []
    pending: str | None = None
    for line in text.splitlines():
        alt = _ALT_LINE.search(line)
        if alt and pending is not None:
            devices.append(MicDevice(name=pending, alt_name=alt.group(1)))
            pending = None
            continue
        match = _DEVICE_LINE.search(line)
        if match:
            if pending is not None:  # previous device had no alternative name
                devices.append(MicDevice(name=pending, alt_name=""))
            pending = match.group(1) if match.group(2) == "audio" else None
    if pending is not None:
        devices.append(MicDevice(name=pending, alt_name=""))
    return devices


def list_microphones(ffmpeg: str) -> list[MicDevice]:
    """Ask ffmpeg itself, so the names are exactly the ones it will accept."""
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    # Listing devices always "fails" (there is no input called dummy); the list is on stderr.
    return parse_dshow_devices(proc.stderr or "")


@dataclass(frozen=True)
class LoopbackDevice:
    index: int
    name: str
    channels: int
    rate: int


def list_loopback_devices() -> list[LoopbackDevice]:
    """WASAPI loopback endpoints - one per playback device that is currently present."""
    if not portaudio.available():
        return []
    found: list[LoopbackDevice] = []
    try:
        with portaudio.session() as audio:
            for info in audio.get_loopback_device_info_generator():
                found.append(
                    LoopbackDevice(
                        index=int(info["index"]),
                        name=str(info["name"]),
                        channels=int(info["maxInputChannels"]),
                        rate=int(info["defaultSampleRate"]),
                    )
                )
    except Exception:
        # Whatever was listed before the failure is still useful to the caller.
        return found
    return found


def default_loopback_index() -> int:
    if not portaudio.available():
        return -1
    try:
        with portaudio.session() as audio:
            return int(audio.get_default_wasapi_loopback()["index"])
    except Exception:
        # No default playback endpoint, or PortAudio would not start. -1 means "let
        # the caller choose"; the feeder reports it properly if it then fails.
        return -1


# -------------------------------------------------------------- loopback feeder


class LoopbackFeeder:
    """Capture system sound and write it to a named pipe as s16le/48k/stereo.

    The object is single-use: `start`, then `stop`. `error` holds the first failure, and
    `frames_written` is there so a caller can prove audio actually flowed rather than
    assume it did.
    """

    def __init__(self, pipe_name: str, device_index: int = -1) -> None:
        self.pipe_name = pipe_name
        self.device_index = device_index
        self.error: str = ""
        self.frames_written = 0
        self.silence_frames = 0  # how much had to be invented to keep the clock
        self.connected = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._handle: int | None = None
        #: Held only to decide who tears the handle down, never across a blocking call.
        self._close_lock = threading.Lock()
        self._closing = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="loopback", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop capturing, and do not return until the pipe is really gone.

        **The handle is the whole point.** A named pipe's *name* belongs to its handles,
        and this one is created with room for a single instance, so a handle left open
        means the next recording's `CreateNamedPipeW` fails with ERROR_PIPE_BUSY (231)
        and system sound is dead until the program is restarted.

        Measured on 2026-11-05, before this was written: one recording where ffmpeg
        never connected left `stop()` returning in 1.51 s with the `loopback` and
        `loopback-connect` threads both still alive and handle 524 still open, and the
        two recordings after it failed instantly with error 231. The capture thread was
        parked in `FlushFileBuffers`, which never returns while a `ConnectNamedPipe` is
        pending and no client ever came, and the connect thread was parked in that
        `ConnectNamedPipe`.

        So nothing here waits on a call that can block for ever: `_cancel_io` breaks
        whatever is in flight first, and only then is the thread joined. The ceiling is
        `timeout` twice plus `CANCEL_TIMEOUT`.

        **It is not uniformly quick, and this said it was.** Measured on 2026-11-05:
        0.001 s when ffmpeg never came, 0.001 s with ffmpeg connected and reading,
        0.000 s with ffmpeg connected and then gone - but **2.02-2.05 s, three runs**,
        with ffmpeg connected and no longer reading. That case parks the capture thread
        in `_close_pipe`'s `FlushFileBuffers`, which the cancel issued before it cannot
        reach, so the first `join` ran its whole 2 s before the second `_cancel_io`
        broke it. `stop` is called on the Qt main thread, so that was two seconds of
        frozen window. `_close_pipe` now bounds its own flush, which brings that case
        to 0.27-0.30 s, three runs, measured the same way - see `_flush`.

        Six failed attempts on one pipe name left the process on exactly the handle
        count it started with - measured on 2026-11-05 at 141 before and 141 after,
        twice over in one process. (The absolute number is whatever that process
        happened to hold; what is measured is that it does not grow.)
        """
        self._stop.set()
        self._cancel_io()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                # Something it started after the first cancel, or a call that was still
                # being set up when the cancel went past. One more round - the capture
                # thread has no unbounded wait left in it.
                self._cancel_io()
                thread.join(timeout=timeout)
        # Normally a no-op: the capture thread closes the handle in its own `finally`
        # and this finds nothing left. It is here for the thread that is wedged anyway,
        # because a pipe name held for the life of the process costs every later
        # recording its sound, and a handle closed under a thread that is never coming
        # back costs nothing.
        self._close_pipe()
        deadline = time.monotonic() + CANCEL_TIMEOUT
        while self._handle is not None and time.monotonic() < deadline:
            # A `_close_pipe` already running on the capture thread owns the teardown;
            # keep breaking what it is stuck on rather than call the handle leaked.
            self._cancel_io()
            time.sleep(0.01)
        if self._handle is not None:
            self.error = self.error or (
                "System sound could not be shut down cleanly. Restart ScreenRec "
                "before recording system sound again."
            )

    @property
    def seconds_written(self) -> float:
        return self.frames_written / SAMPLE_RATE

    @property
    def seconds_padded(self) -> float:
        return self.silence_frames / SAMPLE_RATE

    # -- internals ---------------------------------------------------------

    def _create_pipe(self) -> bool:
        handle = kernel32.CreateNamedPipeW(
            self.pipe_name,
            PIPE_ACCESS_OUTBOUND,
            PIPE_TYPE_BYTE,
            1,  # one instance
            1 << 20,
            1 << 20,
            0,
            None,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            code = ctypes.get_last_error()
            if code == ERROR_PIPE_BUSY:
                # Not this process any more - `stop` now gives the handle back - so it
                # is a second copy of ScreenRec, or one that was killed outright.
                self.error = (
                    "The channel ScreenRec uses for system sound is still held by "
                    "something else. Close any other copy of ScreenRec and start the "
                    "recording again; if there is no other copy, restart ScreenRec."
                )
            else:
                self.error = (
                    f"The channel for system sound could not be opened (Windows error "
                    f"{code}). Record with system sound switched off, or restart "
                    f"ScreenRec and try again."
                )
            return False
        self._handle = handle
        return True

    def _run(self) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self.error = "pyaudiowpatch is not installed; system sound cannot be captured."
            return

        if self._stop.is_set():
            # Stopped before this thread got going. Creating the pipe now would hand
            # `stop` - which has already run - a handle to let go of.
            return
        if not self._create_pipe():
            return

        # The SHARED PortAudio, never a new one: a second instance alive at the same
        # time segfaults the interpreter - measured - and the level meters hold one
        # whenever the bar is on screen. See portaudio.py.
        try:
            with portaudio.session() as audio:
                info = self._device_info(audio)
                if info is None:
                    return
                device_rate = int(info["defaultSampleRate"])
                device_channels = min(2, max(1, int(info["maxInputChannels"])))

                with portaudio.opened(
                    format=pyaudio.paInt16,
                    channels=device_channels,
                    rate=device_rate,
                    frames_per_buffer=BLOCK_FRAMES,
                    input=True,
                    input_device_index=int(info["index"]),
                ) as stream:
                    # Drain whatever accumulates while ffmpeg is still starting up, so
                    # the first byte on the pipe is "now" and not a stale buffer.
                    if not self._wait_for_ffmpeg(stream):
                        return
                    if self._stop.is_set():
                        return
                    self._pump(stream, device_rate, device_channels)
                    self._report_silence(info)
        except Exception as exc:  # PortAudio raises plain OSError subclasses
            if not self.error:
                self.error = f"System sound capture failed: {exc}"
        finally:
            self._close_pipe()

    def _device_info(self, audio) -> dict | None:
        try:
            if self.device_index >= 0:
                return audio.get_device_info_by_index(self.device_index)
            return audio.get_default_wasapi_loopback()
        except Exception as exc:
            self.error = f"No system-sound device available: {exc}"
            return None

    def _wait_for_ffmpeg(self, stream) -> bool:
        """Wait for ffmpeg to open its end, discarding audio meanwhile. False on timeout.

        **Never block on the capture here.** This used a blocking `stream.read`, and on a
        playback endpoint that produces nothing - a headset that is listed but unplugged
        - that read never returned. The loop therefore never noticed that ffmpeg had
        connected, never started pumping, and ffmpeg sat waiting on an input that would
        never deliver: no frames muxed, a zero-byte file, and not a word of explanation.
        Only what is already waiting is read, and the wait is on the connect event.

        **And never walk away from the connect.** `ConnectNamedPipe` is synchronous, so
        the thread that calls it is parked in the kernel until a client arrives.
        Timing out without cancelling left that thread parked for the life of the
        process with the pipe handle open, which is the defect this method was rewritten
        for: see `stop`. Every way out of here now cancels the connect and joins the
        thread, so the method returns with `loopback-connect` finished whatever
        happened.

        The return value of `ConnectNamedPipe` is read, too, which it was not before: a
        connect that fails looked exactly like one that succeeded, and the feeder went
        on to pump audio into a pipe with nobody on it.
        """
        finished = threading.Event()  # the call came back, whatever it said
        got_client = threading.Event()  # ...and there is a client on the other end
        refusal: list[int] = []  # ...or Windows gave a reason there is not

        def connect() -> None:
            handle = self._handle
            try:
                if not handle:
                    return
                if kernel32.ConnectNamedPipe(wintypes.HANDLE(handle), None):
                    got_client.set()
                    return
                code = ctypes.get_last_error()
                if code == ERROR_PIPE_CONNECTED:
                    # ffmpeg opened the pipe between `CreateNamedPipeW` and this call.
                    # Windows calls that a failure; it is the happiest outcome there is.
                    got_client.set()
                else:
                    refusal.append(code)
            finally:
                finished.set()

        waiter = threading.Thread(target=connect, name="loopback-connect", daemon=True)
        waiter.start()
        deadline = time.monotonic() + CONNECT_TIMEOUT
        timed_out = False
        while not finished.is_set() and not self._stop.is_set():
            if time.monotonic() > deadline:
                timed_out = True
                break
            try:
                if stream.get_read_available() >= BLOCK_FRAMES:
                    stream.read(BLOCK_FRAMES, exception_on_overflow=False)
                    continue
            except Exception:
                # Only draining stale audio while ffmpeg starts, and the value of what is
                # read here is nil - it is thrown away either way. A device that cannot
                # be read at all is reported by `_pump`, which runs next and is where it
                # actually matters.
                pass
            time.sleep(0.005)

        if not finished.is_set():
            self._cancel_io()
        waiter.join(timeout=CANCEL_TIMEOUT)
        if got_client.is_set():
            self.connected.set()
            return True
        if timed_out:
            self.error = (
                f"ffmpeg did not pick up the system sound within "
                f"{CONNECT_TIMEOUT:g} seconds, so this recording has no system "
                f"sound - the picture and the microphone are unaffected. Start the "
                f"recording again; if it happens every time, read the ffmpeg log in "
                f"the error dialog, or switch system sound off."
            )
        elif refusal and refusal[0] != ERROR_OPERATION_ABORTED:
            # 995 is `stop` cancelling this wait, which is not a failure to report.
            # Anything else is Windows turning the connection down, and the old code
            # ignored the return value entirely and pumped audio into nothing.
            self.error = (
                f"Windows would not let ffmpeg onto the system-sound channel (error "
                f"{refusal[0]}), so this recording has no system sound. Start the "
                f"recording again, or switch system sound off."
            )
        return False

    def _pump(self, stream, device_rate: int, device_channels: int) -> None:
        """Keep the pipe fed at real-time pace, inventing silence where sound is missing.

        Raw PCM has no timestamps: ffmpeg lays the bytes down end to end and calls the
        first one t=0. So a second of audio that never arrives is not a second of silence
        in the file - it does not exist, and everything after it slides a second earlier.
        That happens for ordinary reasons: a dropped block when the thread is descheduled,
        or an endpoint that stops producing.

        The guard is to compare what has been written against the wall clock and pad the
        difference, but only when the capture has nothing waiting - otherwise audio that
        is merely late would be written twice over and the track would run long. How much
        was invented is counted, because a recording that is half-invented should say so.
        """
        start = time.monotonic()
        while not self._stop.is_set():
            try:
                available = stream.get_read_available()
            except Exception as exc:
                self.error = self.error or f"System sound capture stopped: {exc}"
                break

            if available >= BLOCK_FRAMES:
                try:
                    data = stream.read(BLOCK_FRAMES, exception_on_overflow=False)
                except Exception as exc:
                    self.error = self.error or f"System sound capture stopped: {exc}"
                    break
                if device_channels == 1:
                    data = _mono_to_stereo(data)
                if device_rate != SAMPLE_RATE:
                    data = _resample(data, device_rate, SAMPLE_RATE)
                if not self._write(data):
                    break
                self.frames_written += len(data) // (SAMPLE_WIDTH * CHANNELS)
                continue

            expected = int((time.monotonic() - start) * SAMPLE_RATE)
            deficit = expected - self.frames_written
            if deficit >= BLOCK_FRAMES:
                padding = min(deficit, SAMPLE_RATE)  # never more than a second at once
                if not self._write(bytes(padding * SAMPLE_WIDTH * CHANNELS)):
                    break
                self.frames_written += padding
                self.silence_frames += padding
                continue

            time.sleep(0.002)

    def _report_silence(self, info: dict) -> None:
        """Say so when the chosen endpoint delivered nothing at all.

        The pacing above keeps the timeline right by inventing silence, which is the
        correct thing to do for a hiccup and the wrong thing to leave unsaid when the
        whole recording was invented: that means the playback device produced nothing -
        typically one that is listed but unplugged - and the user would otherwise find a
        silent track and no reason for it.
        """
        if self.error or self.frames_written == 0:
            return
        if self.silence_frames >= self.frames_written * 0.999:
            name = str(info.get("name", "the chosen device"))
            self.error = (
                f"No sound at all arrived from \"{name}\" - the whole track is silence. "
                f"Is that playback device actually connected and in use?"
            )

    def _write(self, data: bytes) -> bool:
        handle = self._handle
        if not handle:
            # `stop`'s backstop `_close_pipe` runs on another thread and clears the
            # handle, which can land between this thread's `_stop` check and the write
            # below. The pipe is being taken down, which is how a recording ends.
            # Measured on 2026-11-05: handing the cleared handle to `WriteFile` instead
            # fails with error 6 and puts "Writing system sound failed (error 6)." in
            # front of the user, for a recording that was shut down normally.
            return False
        written = wintypes.DWORD()
        ok = kernel32.WriteFile(
            wintypes.HANDLE(handle), data, len(data), ctypes.byref(written), None
        )
        if not ok:
            code = ctypes.get_last_error()
            # 232 and 109 are ffmpeg closing its end, which is how a recording ends.
            # 995 is this write being cancelled by `_cancel_io` from `stop`, which is
            # how a recording ends when ffmpeg has stopped reading - measured at 0.02 s
            # to come back. None of the three is a fault to put in front of the user.
            if code not in (ERROR_NO_DATA, ERROR_BROKEN_PIPE, ERROR_OPERATION_ABORTED):
                self.error = f"Writing system sound failed (error {code})."
            return False
        return True

    def _cancel_io(self) -> None:
        """Break whatever Win32 call is parked on the pipe at this moment.

        This is what makes `stop` bounded: a synchronous `ConnectNamedPipe` waiting for
        an ffmpeg that will never come, a `FlushFileBuffers` stuck behind it, or a
        `WriteFile` into a buffer whose reader has stopped. Measured on 2026-11-05: all
        three came back within 0.02 s of the cancel, the write reporting error 995.

        The handle is read without taking `_close_lock` deliberately. `_close_pipe` can
        be inside a blocking call while it owns the teardown, and this is the call that
        frees it - waiting for that lock would deadlock the two against each other.
        `_close_pipe` clears `_handle` in the instruction before `CloseHandle`, so what
        is read here is a handle that is still open, or None.
        """
        handle = self._handle
        if not handle:
            return
        # Not wrapped in a try: `ctypes.WinDLL(..., use_last_error=True)` reports a
        # failed Win32 call by its return value, it does not raise. Measured on
        # 2026-11-05 against a handle that had already been closed, and against a null
        # one: `CancelIoEx` returned 0 with the last error at 6 and nothing was raised.
        kernel32.CancelIoEx(wintypes.HANDLE(handle), None)

    def _flush(self, handle: int) -> bool:
        """Push the last block to a client that is still reading, with a bound on it.

        `FlushFileBuffers` on the server end of a pipe waits until the client has read
        everything still buffered, and it is the one call in this file that nothing in
        `stop` could shorten: `_close_pipe` issued its `CancelIoEx` *before* the flush,
        so its own cancel could never cover its own flush.

        Measured on 2026-11-05 with a client connected, 1 MB buffered and not one byte
        read - an ffmpeg that is still there and has stopped reading, which is as much
        what `connected` means as an ffmpeg that is draining: `_close_pipe` was still
        parked after **8.01 s** and came back only when the client closed its handle.
        Through `stop` that cost **2.02-2.05 s**, three runs, on the Qt main thread.

        So the flush runs on a thread of its own and is cancelled if it does not come
        back. The case it is kept for is cheap - 0.0002 s, worst of five, with a client
        draining the pipe, against a `FLUSH_TIMEOUT` of 0.25 s - and a `CancelIoEx`
        issued *while* the flush is pending does break it: measured at 0.02 s after the
        cancel, three runs, which is what makes the bound real rather than hopeful. With
        this in place the 8.01 s `_close_pipe` above comes back in 0.28 s.

        Returns whether the flush thread really finished, so that a leak of it is
        something a test can see rather than something it has to guess at.
        """
        done = threading.Event()

        def flush() -> None:
            try:
                kernel32.FlushFileBuffers(wintypes.HANDLE(handle))
            finally:
                done.set()

        worker = threading.Thread(target=flush, name="loopback-flush", daemon=True)
        worker.start()
        if not done.wait(FLUSH_TIMEOUT):
            kernel32.CancelIoEx(wintypes.HANDLE(handle), None)
        # Joined, not merely waited on: `done` is set inside the thread, so between
        # that and the thread ending it is still alive and still listed.
        worker.join(timeout=CANCEL_TIMEOUT)
        return not worker.is_alive()

    def _close_pipe(self) -> None:
        """Let the handle go. Safe to call from either thread, and from both.

        The flush is kept for the one case where it earns its place, an ffmpeg still
        reading the last block, and skipped when nobody ever connected: measured on this
        machine it **never returns** when a `ConnectNamedPipe` is pending and no client
        ever came - precisely the failed-recording case. `connected` does not tell a
        draining ffmpeg from a stalled one, so the flush that is kept is bounded rather
        than trusted - see `_flush`.
        """
        with self._close_lock:
            if self._handle is None or self._closing:
                return
            self._closing = True
        handle = self._handle
        try:
            # Nothing may be left in flight: a pending connect is what makes the flush
            # below wait for ever, and a pending write keeps the handle busy.
            kernel32.CancelIoEx(wintypes.HANDLE(handle), None)
            if self.connected.is_set():
                self._flush(handle)
            kernel32.DisconnectNamedPipe(wintypes.HANDLE(handle))
        finally:
            # No `except` here: these are `use_last_error=True` ctypes calls, which
            # report a failed Win32 call by returning 0 - measured on 2026-11-05, a
            # closed handle gave 0 and last error 6 from every one of them, and nothing
            # was raised. The `finally` is what guarantees the handle is let go.
            #
            # Cleared before the close, so a `_cancel_io` racing this reaches either a
            # handle that is still open or nothing - never one Windows has recycled.
            self._handle = None
            self._closing = False
            kernel32.CloseHandle(wintypes.HANDLE(handle))


def _mono_to_stereo(data: bytes) -> bytes:
    import array

    samples = array.array("h")
    samples.frombytes(data)
    doubled = array.array("h", [0]) * (len(samples) * 2)
    doubled[0::2] = samples
    doubled[1::2] = samples
    return doubled.tobytes()


def _resample(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Nearest-sample rate conversion for the rare endpoint that is not 48 kHz.

    Deliberately crude: it keeps the stream length right, which is what sync depends on.
    Endpoints on this machine run at 48 kHz, so in practice this path is not taken; if a
    44.1 kHz endpoint ever becomes the normal case, this wants a real resampler.
    """
    import array

    samples = array.array("h")
    samples.frombytes(data)
    frames = len(samples) // CHANNELS
    out_frames = max(1, round(frames * dst_rate / src_rate))
    out = array.array("h", [0]) * (out_frames * CHANNELS)
    for i in range(out_frames):
        j = min(frames - 1, int(i * src_rate / dst_rate))
        out[i * CHANNELS] = samples[j * CHANNELS]
        out[i * CHANNELS + 1] = samples[j * CHANNELS + 1]
    return out.tobytes()
