"""The loopback feeder's guards, every one of which exists because it failed in a real run.

Nothing here needs a sound device: the feeder's logic is exercised directly with
stand-ins, so the suite stays runnable on a machine with no audio at all. The *pipe*,
though, is the real thing - a real `CreateNamedPipeW` with no ffmpeg at the other end,
because the defect these tests were written for lives in the Win32 half and a stand-in
pipe would have reproduced nothing.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import unittest
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from screenrec import audio  # noqa: E402

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.GetProcessHandleCount.restype = wintypes.BOOL
kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(wintypes.DWORD)]
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3

_pipe_counter = 0


def unique_pipe_name() -> str:
    """A pipe name no other test and no other process is using."""
    global _pipe_counter
    _pipe_counter += 1
    return r"\\.\pipe\screenrec_test_%d_%d" % (os.getpid(), _pipe_counter)


def open_as_client(name: str, timeout: float = 5.0) -> int:
    """Stand in for ffmpeg: open the read end of the pipe and hold it.

    Retried, because the feeder's thread creates the pipe a moment after `start`
    returns and ffmpeg is just as late in the real thing.
    """
    deadline = time.monotonic() + timeout
    while True:
        handle = kernel32.CreateFileW(name, GENERIC_READ, 0, None, OPEN_EXISTING,
                                      0, None)
        if handle and handle != audio.INVALID_HANDLE_VALUE:
            return handle
        if time.monotonic() > deadline:
            raise AssertionError("the pipe never appeared: error %d"
                                 % ctypes.get_last_error())
        time.sleep(0.01)


def process_handles() -> int:
    count = wintypes.DWORD()
    kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count))
    return count.value


def feeder_threads() -> list[str]:
    """Every thread the feeder owns, so "stop() left threads running" can be asserted.

    `loopback-flush` is in here because it holds the pipe handle while it runs: a flush
    left behind is exactly as bad as a connect left behind.
    """
    return sorted(t.name for t in threading.enumerate()
                  if t.name in ("loopback", "loopback-connect", "loopback-flush"))


def close_bounded(case: unittest.TestCase, feeder: audio.LoopbackFeeder,
                  timeout: float = 5.0) -> None:
    """Let the pipe go on a thread, so a regression fails the run rather than hanging it.

    `_close_pipe` is the one call in `audio.py` that can block without a bound of its
    own - measured on 2026-11-05 at 8.01 s and still parked, with a client connected
    that had stopped reading, coming back only when that client closed its handle - and
    `addCleanup(feeder._close_pipe)` ran it on the main thread, where a test it is
    blocking can report nothing at all. On a thread the worst case is `timeout` and a
    named failure.
    """
    done = threading.Event()
    threading.Thread(target=lambda: (feeder._close_pipe(), done.set()),
                     daemon=True).start()
    case.assertTrue(done.wait(timeout), "_close_pipe blocked instead of letting go")


def fill_pipe(feeder: audio.LoopbackFeeder, blocks: int = 16,
              size: int = 64 * 1024) -> int:
    """Fill the pipe's 1 MB buffer through the feeder's own `_write`, and say how much.

    A flush only waits when there is something unread in the pipe, so a test about the
    flush has to put it there.
    """
    written = 0
    for _ in range(blocks):
        if not feeder._write(bytes(size)):
            break
        written += size
    return written


class SilentStream:
    """A capture stream that is open and delivers nothing, ever.

    This is what an unplugged playback endpoint looks like through PortAudio: it opens,
    it is listed, and `get_read_available` stays at zero. A blocking `read` on it never
    returns, which is how a recording ended up as a zero-byte file.
    """

    def __init__(self) -> None:
        self.blocking_reads = 0

    def get_read_available(self) -> int:
        return 0

    def read(self, frames: int, exception_on_overflow: bool = True) -> bytes:
        self.blocking_reads += 1
        time.sleep(30)  # a real one blocks forever; fail the test rather than hang it
        return b""


class LiveStream:
    """A stream that always has a block of (quiet) audio waiting."""

    def __init__(self, frames: int = audio.BLOCK_FRAMES) -> None:
        self.frames = frames
        self.reads = 0

    def get_read_available(self) -> int:
        return self.frames

    def read(self, frames: int, exception_on_overflow: bool = True) -> bytes:
        self.reads += 1
        return bytes(frames * audio.SAMPLE_WIDTH * audio.CHANNELS)


class _Feeder(audio.LoopbackFeeder):
    """A feeder whose pipe is a list, so nothing Win32 is needed."""

    def __init__(self) -> None:
        super().__init__(r"\\.\pipe\screenrec_test_never_created")
        self.written: list[bytes] = []
        self.fail_after = 10_000

    def _write(self, data: bytes) -> bool:
        if len(self.written) >= self.fail_after:
            return False
        self.written.append(data)
        return True


class _PipeFeeder(audio.LoopbackFeeder):
    """The feeder with its sound device left out and its real pipe left in.

    `_run` is PortAudio plus the pipe, and the defect being tested is entirely in the
    pipe: the device half is replaced by a stream that delivers nothing, so `start` and
    `stop` below are the real ones and the suite still needs no sound card.
    """

    def __init__(self, pipe_name: str, stream=SilentStream) -> None:
        super().__init__(pipe_name)
        self.ran = threading.Event()
        #: The stand-in capture stream. `SilentStream` leaves the pipe nearly empty;
        #: `LiveStream` fills it, which is what a flush has to wait on.
        self.stream = stream

    def _run(self) -> None:
        try:
            if self._stop.is_set() or not self._create_pipe():
                return
            if not self._wait_for_ffmpeg(self.stream()):
                return
            self._pump(self.stream(), audio.SAMPLE_RATE, audio.CHANNELS)
        finally:
            self.ran.set()
            self._close_pipe()


class WaitForFfmpegTest(unittest.TestCase):
    """A real pipe that no ffmpeg ever opens, which is the failure being reproduced."""

    def setUp(self) -> None:
        self.name = unique_pipe_name()
        original = audio.CONNECT_TIMEOUT
        audio.CONNECT_TIMEOUT = 0.3
        self.addCleanup(setattr, audio, "CONNECT_TIMEOUT", original)

    def test_it_does_not_block_on_a_stream_that_delivers_nothing(self) -> None:
        """The first bug: a blocking read on a dead endpoint never returned.

        The wait then never noticed that ffmpeg had connected, the pump never started,
        ffmpeg sat on an input that would never deliver, and the recording was a
        zero-byte file with no error anywhere.
        """
        feeder = audio.LoopbackFeeder(self.name)
        self.assertTrue(feeder._create_pipe())
        self.addCleanup(close_bounded, self, feeder)
        stream = SilentStream()

        began = time.monotonic()
        connected = feeder._wait_for_ffmpeg(stream)
        took = time.monotonic() - began

        self.assertFalse(connected, "it should report that ffmpeg never connected")
        self.assertLess(took, 5.0, "it blocked instead of timing out")
        self.assertEqual(stream.blocking_reads, 0, "it read a stream with nothing waiting")
        self.assertTrue(feeder.error, "a timeout must be reported, not swallowed")

    def test_the_connect_thread_is_cancelled_rather_than_abandoned(self) -> None:
        """The second bug, and the expensive one.

        `ConnectNamedPipe` is synchronous: the thread that calls it is parked in the
        kernel until a client arrives. Timing out without cancelling left
        `loopback-connect` parked for the life of the process, which is what made
        `_close_pipe` hang and the pipe name stay taken. Measured against the old body:
        the thread was still alive here, and `_close_pipe` below never returned.
        """
        feeder = audio.LoopbackFeeder(self.name)
        self.assertTrue(feeder._create_pipe())
        self.assertFalse(feeder._wait_for_ffmpeg(SilentStream()))

        self.assertEqual(feeder_threads(), [],
                         "the connect thread was left parked on the pipe")
        closed = threading.Event()
        threading.Thread(target=lambda: (feeder._close_pipe(), closed.set()),
                         daemon=True).start()
        self.assertTrue(closed.wait(5.0), "_close_pipe blocked instead of letting go")
        self.assertIsNone(feeder._handle, "the handle was not closed")

    def test_the_timeout_says_what_the_user_can_do(self) -> None:
        """"ffmpeg never opened the pipe" names the machinery and offers no way out."""
        feeder = audio.LoopbackFeeder(self.name)
        self.assertTrue(feeder._create_pipe())
        self.addCleanup(close_bounded, self, feeder)
        feeder._wait_for_ffmpeg(SilentStream())

        self.assertIn("no system sound", feeder.error)
        self.assertIn("again", feeder.error, "it must offer something to do")
        self.assertNotIn("error 231", feeder.error,
                         "231 is the symptom of the previous failure, not of this one")

    def test_a_client_that_is_there_is_seen_and_reported_connected(self) -> None:
        feeder = audio.LoopbackFeeder(self.name)
        self.assertTrue(feeder._create_pipe())
        self.addCleanup(close_bounded, self, feeder)
        client = open_as_client(self.name)
        self.addCleanup(kernel32.CloseHandle, wintypes.HANDLE(client))

        self.assertTrue(feeder._wait_for_ffmpeg(SilentStream()))
        self.assertTrue(feeder.connected.is_set())
        self.assertEqual(feeder.error, "")
        self.assertEqual(feeder_threads(), [])

    def test_a_connect_windows_refuses_is_not_mistaken_for_a_connection(self) -> None:
        """The old body ignored `ConnectNamedPipe`'s return value altogether.

        A refused connect then looked exactly like a successful one, and the feeder
        pumped audio into a pipe with nobody on it, reporting nothing.
        """
        feeder = audio.LoopbackFeeder(self.name)
        self.assertTrue(feeder._create_pipe())
        self.addCleanup(close_bounded, self, feeder)

        class Refuses:
            """kernel32 with a ConnectNamedPipe that fails the way Windows fails."""

            @staticmethod
            def ConnectNamedPipe(handle, overlapped):
                ctypes.set_last_error(6)  # ERROR_INVALID_HANDLE
                return 0

            @staticmethod
            def CancelIoEx(handle, overlapped):
                return 1

        original = audio.kernel32
        audio.kernel32 = Refuses()
        self.addCleanup(setattr, audio, "kernel32", original)

        self.assertFalse(feeder._wait_for_ffmpeg(SilentStream()))
        self.assertFalse(feeder.connected.is_set())
        self.assertIn("6", feeder.error)


class PipeHandleTest(unittest.TestCase):
    """One failed attempt used to kill system sound until the program was restarted.

    Measured on 2026-11-05 against the old body, three recordings in a row on one name:
    the first timed out after 10.2 s and `stop()` returned in 1.51 s with the `loopback`
    and `loopback-connect` threads both alive and handle 524 still open; the second and
    third died at once with *Could not create the audio pipe (error 231)* - 231 being
    ERROR_PIPE_BUSY, the name still held by the handle the first attempt never let go.
    """

    def setUp(self) -> None:
        original = audio.CONNECT_TIMEOUT
        audio.CONNECT_TIMEOUT = 0.3
        self.addCleanup(setattr, audio, "CONNECT_TIMEOUT", original)

    def run_one(self, name: str) -> _PipeFeeder:
        feeder = _PipeFeeder(name)
        feeder.start()
        self.assertTrue(feeder.ran.wait(10.0), "the capture thread never finished")
        feeder.stop()
        return feeder

    def test_stop_returns_with_every_thread_finished_and_the_handle_closed(self) -> None:
        feeder = _PipeFeeder(unique_pipe_name())
        feeder.start()
        self.assertTrue(feeder.ran.wait(10.0))
        began = time.monotonic()
        feeder.stop()
        took = time.monotonic() - began

        self.assertEqual(feeder_threads(), [], "stop() left threads running")
        self.assertIsNone(feeder._handle, "stop() left the pipe handle open")
        # Measured at 0.001 s; the old body took 1.51 s and left both threads behind.
        self.assertLess(took, 1.0, "stop() is not bounded")

    def test_the_next_recording_gets_the_same_pipe_name(self) -> None:
        """This is the defect: the second attempt used to fail with error 231."""
        name = unique_pipe_name()
        first = self.run_one(name)
        self.assertIn("no system sound", first.error)

        second = self.run_one(name)
        self.assertNotIn("231", second.error)
        self.assertNotIn("could not be opened", second.error)
        self.assertIn("no system sound", second.error,
                      "the second attempt should fail the same honest way as the first")

    def test_failed_attempts_on_fresh_names_leak_no_handles(self) -> None:
        """A fresh pipe name each time, which is the case the name itself cannot save.

        Reusing one name makes the second attempt fail at `CreateNamedPipeW`, so it
        never reaches the leak - the count stops growing for the wrong reason. With a
        name per attempt every one of them gets a handle, and the old body kept every
        one: measured 143 handles before the first attempt and 247 after it. Measured
        against this body, doing exactly what this test does: 141 handles before the
        three attempts and 141 after them, twice over in one process.
        """
        self.run_one(unique_pipe_name())  # the first pays for the thread pool
        before = process_handles()
        for _attempt in range(3):
            self.run_one(unique_pipe_name())
        self.assertLessEqual(process_handles() - before, 2,
                             "a handle was kept for every failed recording")

    def test_stop_is_bounded_when_ffmpeg_connected_and_then_died(self) -> None:
        name = unique_pipe_name()
        feeder = _PipeFeeder(name)
        feeder.start()
        client = open_as_client(name)
        self.assertTrue(_wait_until(feeder.connected.is_set, 5.0),
                        "the feeder never saw the client")
        kernel32.CloseHandle(wintypes.HANDLE(client))  # ffmpeg dies mid-recording

        began = time.monotonic()
        feeder.stop()
        took = time.monotonic() - began
        self.assertEqual(feeder_threads(), [])
        self.assertIsNone(feeder._handle)
        # Measured at 0.000 s once the client has gone.
        self.assertLess(took, 1.0, "stop() is not bounded after the client goes")

    def test_stop_is_bounded_while_ffmpeg_is_still_connected(self) -> None:
        """The client is held open and never reads, which is the case that can block.

        `FlushFileBuffers` waits for a reader; `stop` cancels it rather than trust one.
        """
        name = unique_pipe_name()
        feeder = _PipeFeeder(name)
        feeder.start()
        client = open_as_client(name)
        self.addCleanup(kernel32.CloseHandle, wintypes.HANDLE(client))
        self.assertTrue(_wait_until(feeder.connected.is_set, 5.0))

        began = time.monotonic()
        feeder.stop()
        took = time.monotonic() - began
        self.assertEqual(feeder_threads(), [])
        self.assertIsNone(feeder._handle)
        # Measured at 0.001 s with the client still open and not reading.
        self.assertLess(took, 3.0, "stop() blocked on a client that was not reading")

    def test_stopping_before_the_thread_starts_leaves_no_pipe_behind(self) -> None:
        """`Recorder.start` stops the feeder 0.15 s in if it has already failed."""
        name = unique_pipe_name()
        feeder = _PipeFeeder(name)
        feeder.stop()  # the race: stop before _run has created anything
        feeder.start()
        self.assertTrue(feeder.ran.wait(10.0))
        feeder.stop()

        self.assertIsNone(feeder._handle)
        probe = audio.LoopbackFeeder(name)
        self.assertTrue(probe._create_pipe(), "the name was left taken: " + probe.error)
        probe._close_pipe()

    def test_a_busy_name_is_explained_rather_than_numbered(self) -> None:
        """231 can still happen - a second copy of ScreenRec, or one that was killed."""
        name = unique_pipe_name()
        holder = audio.LoopbackFeeder(name)
        self.assertTrue(holder._create_pipe())
        self.addCleanup(close_bounded, self, holder)

        second = audio.LoopbackFeeder(name)
        self.assertFalse(second._create_pipe())
        self.assertIn("copy of ScreenRec", second.error)
        self.assertIn("restart", second.error.lower())
        self.assertNotIn("231", second.error, "a number is not something to act on")


class FlushBoundTest(unittest.TestCase):
    """An ffmpeg that is still connected and has stopped reading.

    `connected` does not tell that apart from an ffmpeg that is draining the pipe, and
    the flush `_close_pipe` keeps for the draining one waits for ever on this one.
    """

    def test_close_pipe_returns_when_the_client_has_stopped_reading(self) -> None:
        """Measured on 2026-11-05 against the old body, which issued its `CancelIoEx`
        *before* its `FlushFileBuffers` so its own cancel could never cover its own
        flush: a client connected with 1 MB buffered and not a byte read left
        `_close_pipe` parked after **8.01 s**, and it came back only when the client
        closed its handle. Measured against this body: 0.28 s.
        """
        name = unique_pipe_name()
        feeder = audio.LoopbackFeeder(name)
        self.assertTrue(feeder._create_pipe())
        client = open_as_client(name)
        self.addCleanup(kernel32.CloseHandle, wintypes.HANDLE(client))
        feeder.connected.set()  # what `_wait_for_ffmpeg` does when ffmpeg arrives
        self.assertGreater(fill_pipe(feeder), 0, "nothing was buffered to flush")

        done = threading.Event()
        threading.Thread(target=lambda: (feeder._close_pipe(), done.set()),
                         daemon=True).start()
        self.assertTrue(done.wait(3.0),
                        "_close_pipe waited on a flush that nothing can cancel")
        self.assertIsNone(feeder._handle, "the handle was not closed")

    def test_stop_is_bounded_when_the_client_has_stopped_reading(self) -> None:
        """`test_stop_is_bounded_while_ffmpeg_is_still_connected` runs on `SilentStream`,
        so the pipe holds a little padding and the flush has nothing to wait for. With
        the buffer full it has.

        Measured on 2026-11-05 against the old body: `stop()` took 2.02, 2.04 and
        2.05 s over three runs - the capture thread parked in `_close_pipe`'s flush, so
        the first `join` ran its whole 2 s before the next `_cancel_io` broke it. `stop`
        is called on the Qt main thread, so that was two seconds of frozen window.
        Measured against this body: 0.27-0.30 s.
        """
        name = unique_pipe_name()
        feeder = _PipeFeeder(name, stream=LiveStream)
        feeder.start()
        client = open_as_client(name)
        self.addCleanup(kernel32.CloseHandle, wintypes.HANDLE(client))
        self.assertTrue(_wait_until(feeder.connected.is_set, 5.0),
                        "the feeder never saw the client")
        # The client never reads, so the pump fills the 1 MB buffer and then blocks in
        # `WriteFile`. 1 000 000 bytes is the last whole 10 ms block before that.
        self.assertTrue(
            _wait_until(lambda: feeder.frames_written * audio.SAMPLE_WIDTH
                        * audio.CHANNELS >= 1_000_000, 10.0),
            "the pipe buffer never filled, so this is not testing the flush",
        )

        began = time.monotonic()
        feeder.stop()
        took = time.monotonic() - began
        self.assertEqual(feeder_threads(), [])
        self.assertIsNone(feeder._handle)
        self.assertLess(took, 1.5,
                        "stop() waited on a flush that nothing can cancel")


class WriteFailureTest(unittest.TestCase):
    def test_a_write_after_the_handle_is_let_go_is_not_a_fault(self) -> None:
        """`stop`'s backstop `_close_pipe` runs on another thread and clears `_handle`.

        The pump reads it between its own `_stop` check and the write, so this really
        happens at the end of a recording. Measured on 2026-11-05 against the old body,
        which handed the cleared handle straight to `WriteFile`: it failed with error 6
        and put *"Writing system sound failed (error 6)."* in front of the user, for a
        recording that had been shut down normally.
        """
        feeder = audio.LoopbackFeeder(unique_pipe_name())
        self.assertIsNone(feeder._handle, "nothing has opened a pipe here")

        self.assertFalse(feeder._write(bytes(1920)))
        self.assertEqual(feeder.error, "",
                         "the teardown blamed the recording for its own shutdown")

    def test_a_write_cancelled_by_stop_is_not_reported_as_a_fault(self) -> None:
        """`stop` breaks a stuck write with CancelIoEx, which reports error 995.

        Measured in the probe that led to this: a `WriteFile` into a full buffer whose
        reader had stopped came back 0.02 s after the cancel with error 995. Calling
        that "Writing system sound failed (error 995)" blames the user's recording for
        its own shutdown.
        """
        feeder = audio.LoopbackFeeder(unique_pipe_name())
        feeder._handle = 1  # never touched: the stub below answers for it

        class Aborts:
            @staticmethod
            def WriteFile(handle, data, size, written, overlapped):
                ctypes.set_last_error(audio.ERROR_OPERATION_ABORTED)
                return 0

        original = audio.kernel32
        audio.kernel32 = Aborts()
        self.addCleanup(setattr, audio, "kernel32", original)

        self.assertFalse(feeder._write(b"\0\0\0\0"))
        self.assertEqual(feeder.error, "", "a cancelled write is how a recording ends")

    def test_a_write_that_really_failed_is_still_reported(self) -> None:
        feeder = audio.LoopbackFeeder(unique_pipe_name())
        feeder._handle = 1

        class Fails:
            @staticmethod
            def WriteFile(handle, data, size, written, overlapped):
                ctypes.set_last_error(1117)  # ERROR_IO_DEVICE
                return 0

        original = audio.kernel32
        audio.kernel32 = Fails()
        self.addCleanup(setattr, audio, "kernel32", original)

        self.assertFalse(feeder._write(b"\0\0\0\0"))
        self.assertIn("1117", feeder.error)


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class SilenceReportTest(unittest.TestCase):
    def test_a_device_that_produced_nothing_is_named(self) -> None:
        feeder = _Feeder()
        feeder.frames_written = 48000 * 10
        feeder.silence_frames = feeder.frames_written
        feeder._report_silence({"name": "Headphones (unplugged)"})
        self.assertIn("Headphones (unplugged)", feeder.error)
        self.assertIn("silence", feeder.error.lower())

    def test_a_little_padding_is_not_worth_complaining_about(self) -> None:
        feeder = _Feeder()
        feeder.frames_written = 48000 * 10
        feeder.silence_frames = 480  # ten milliseconds of hiccup
        feeder._report_silence({"name": "Speakers"})
        self.assertEqual(feeder.error, "")

    def test_an_earlier_error_is_not_overwritten(self) -> None:
        feeder = _Feeder()
        feeder.error = "the device went away"
        feeder.frames_written = 4800
        feeder.silence_frames = 4800
        feeder._report_silence({"name": "Speakers"})
        self.assertEqual(feeder.error, "the device went away")

    def test_nothing_is_said_when_nothing_was_written(self) -> None:
        """A recording that never started is not a silent one; it has its own message."""
        feeder = _Feeder()
        feeder._report_silence({"name": "Speakers"})
        self.assertEqual(feeder.error, "")


class PacingTest(unittest.TestCase):
    def test_a_gap_is_padded_so_the_timeline_keeps_real_time(self) -> None:
        """Raw PCM has no timestamps, so a missing second is not silence - it is a shift.

        Measured before this was added: a 40 s recording's audio ran 1.6 s short and
        everything after the gap sat early against the picture.
        """
        feeder = _Feeder()
        stream = SilentStream.__new__(SilentStream)  # available=0, never read
        stream.blocking_reads = 0
        stopper = feeder._stop

        def stop_soon() -> None:
            time.sleep(0.35)
            stopper.set()

        import threading

        threading.Thread(target=stop_soon, daemon=True).start()
        feeder._pump(stream, audio.SAMPLE_RATE, audio.CHANNELS)

        self.assertGreater(feeder.silence_frames, 0, "nothing was padded")
        self.assertEqual(feeder.silence_frames, feeder.frames_written)
        # The padding tracks the clock rather than running away with it.
        self.assertLess(feeder.seconds_written, 1.0)

    def test_real_audio_is_passed_through_and_not_padded(self) -> None:
        feeder = _Feeder()
        stream = LiveStream()
        stopper = feeder._stop

        def stop_soon() -> None:
            time.sleep(0.2)
            stopper.set()

        import threading

        threading.Thread(target=stop_soon, daemon=True).start()
        feeder._pump(stream, audio.SAMPLE_RATE, audio.CHANNELS)

        self.assertGreater(stream.reads, 0)
        self.assertEqual(feeder.silence_frames, 0, "padded while audio was available")
        # The read counter is bumped by the pump thread, so it can be one block ahead of
        # what was written by the time this reads both.
        self.assertAlmostEqual(
            feeder.frames_written / audio.BLOCK_FRAMES, stream.reads, delta=1
        )

    def test_the_pump_stops_when_the_pipe_closes(self) -> None:
        """ffmpeg closing its end at the end of a recording is the normal ending."""
        feeder = _Feeder()
        feeder.fail_after = 3
        stream = LiveStream()
        feeder._pump(stream, audio.SAMPLE_RATE, audio.CHANNELS)
        self.assertEqual(len(feeder.written), 3)
        self.assertEqual(feeder.error, "", "a closed pipe is not an error to report")


class DshowParsingTest(unittest.TestCase):
    def test_the_alternative_name_is_preferred(self) -> None:
        device = audio.MicDevice(name="Microphone", alt_name="@device_cm_{...}")
        self.assertEqual(device.ffmpeg_id, "@device_cm_{...}")

    def test_the_friendly_name_is_the_fallback(self) -> None:
        self.assertEqual(audio.MicDevice(name="Mic", alt_name="").ffmpeg_id, "Mic")


if __name__ == "__main__":
    unittest.main()


class DeviceInfoTest(unittest.TestCase):
    """Which endpoint gets captured when the one that was asked for is not there.

    PortAudio numbers its devices in the order it found them when it started, so a
    saved index names a different endpoint - or none - as soon as the set of devices
    changes. `paInvalidDevice`, errno -9996, is what that looks like, and on
    2026-11-13 it stopped a recording from happening at all.
    """

    class _Audio:
        """PortAudio as `_device_info` uses it: two calls, either of which may fail."""

        def __init__(self, by_index=None, default=None) -> None:
            self._by_index = by_index
            self._default = default
            self.asked_for_default = False

        def get_device_info_by_index(self, index):
            if self._by_index is None:
                raise OSError(-9996, "Invalid device info")
            return dict(self._by_index, index=index)

        def get_default_wasapi_loopback(self):
            self.asked_for_default = True
            if self._default is None:
                raise OSError(-9996, "Invalid device info")
            return self._default

    def _feeder(self, device_index=-1):
        return audio.LoopbackFeeder(r"\.\pipe\screenrec_device_test", device_index)

    def test_the_chosen_device_is_used_when_it_is_there(self) -> None:
        stack = self._Audio(by_index={"name": "Speakers"}, default={"name": "Other"})
        feeder = self._feeder(7)
        self.assertEqual(feeder._device_info(stack)["name"], "Speakers")
        self.assertFalse(stack.asked_for_default)
        self.assertFalse(feeder.fell_back)

    def test_a_chosen_device_that_has_gone_falls_back_to_the_default(self) -> None:
        stack = self._Audio(by_index=None, default={"name": "Speakers", "index": 3})
        feeder = self._feeder(7)
        info = feeder._device_info(stack)
        self.assertEqual(info["index"], 3, "the default is recorded instead")
        self.assertTrue(feeder.fell_back, "and the user is told which one it was")
        self.assertEqual(feeder.error, "", "a substitution is not a failure")

    def test_no_endpoint_at_all_is_said_in_words(self) -> None:
        feeder = self._feeder(-1)
        self.assertIsNone(feeder._device_info(self._Audio(default=None)))
        self.assertEqual(feeder.reason, audio.REASON_NO_DEVICE)
        self.assertIn("playback device", feeder.error)
        self.assertIn(
            "picture and the microphone", feeder.error,
            "it has to say what is NOT lost, or it reads like the take is ruined",
        )

    def test_the_first_failure_is_the_one_kept(self) -> None:
        """These cascade: a dead device makes the pipe time out, which makes the
        shutdown complain. The first is the one that explains the rest."""
        feeder = self._feeder(-1)
        feeder._fail(audio.REASON_NO_DEVICE, "first")
        feeder._fail(audio.REASON_PIPE_FAILED, "second")
        self.assertEqual(feeder.error, "first")
        self.assertEqual(feeder.reason, audio.REASON_NO_DEVICE)
