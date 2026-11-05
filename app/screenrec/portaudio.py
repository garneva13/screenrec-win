"""One PortAudio for the whole application, because two of them crash it.

Measured on 2026-11-04, and it is not a subtle failure: a single `LevelMeter` runs
happily, and starting a second one in another thread segfaults the interpreter - exit
139, no traceback, no Python-level error to catch. Two `pyaudiowpatch.PyAudio()`
instances being constructed from different threads is enough to do it; PortAudio's WASAPI
backend initialises COM per process and does not survive the race.

That matters beyond the meters. A recording with system sound already runs
`audio.LoopbackFeeder` on its own thread, so a microphone meter started while recording
would have been the second instance - the crash would have landed in the middle of
somebody's recording rather than in a test.

So: exactly one instance, reference counted, created and destroyed under a lock. Every
caller takes a handle with `session()` and lets the context manager give it back.

    with portaudio.session() as pa:
        stream = pa.open(...)

`pa` is the real `pyaudiowpatch.PyAudio`, so nothing else about the calling code changes.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_lock = threading.RLock()
_instance = None
_users = 0
_import_error = ""


def available() -> bool:
    """Whether PortAudio can be used at all. Cheap, and safe to call anywhere."""
    try:
        import pyaudiowpatch  # noqa: F401
    except ImportError:
        return False
    return True


def acquire():
    """The shared instance, with the caller counted. Raises RuntimeError if unavailable."""
    global _instance, _users, _import_error
    with _lock:
        if _instance is None:
            try:
                import pyaudiowpatch as pyaudio
            except ImportError as exc:
                _import_error = str(exc)
                raise RuntimeError(
                    "pyaudiowpatch is not installed; sound cannot be captured or metered."
                ) from exc
            _instance = pyaudio.PyAudio()
        _users += 1
        return _instance


def release() -> None:
    """Give the instance back. The last caller out terminates it."""
    global _instance, _users
    with _lock:
        if _users > 0:
            _users -= 1
        if _users == 0 and _instance is not None:
            handle, _instance = _instance, None
            try:
                handle.terminate()
            except Exception:
                # Shutting PortAudio down is best effort; a failure here cannot be acted
                # on and must not mask whatever the caller was really doing.
                pass


@contextmanager
def session():
    """Hold the shared PortAudio for the length of a block."""
    handle = acquire()
    try:
        yield handle
    finally:
        release()


@contextmanager
def opened(**kwargs):
    """Open a stream on the shared instance and always close it.

    `open` itself is serialised: two threads opening WASAPI streams at the same moment
    is the other half of the race the shared instance exists to avoid.
    """
    handle = acquire()
    stream = None
    try:
        with _lock:
            stream = handle.open(**kwargs)
        yield stream
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                # Teardown only - the caller's own error, if any, is more interesting.
                pass
        release()


def users() -> int:
    """How many callers hold the instance. For tests and for a leak check."""
    with _lock:
        return _users
