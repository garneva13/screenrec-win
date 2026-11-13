"""Write down the exceptions nobody sees.

ScreenRec is a windowed build: `pythonw.exe` and the PyInstaller `--noconsole` exe
both run with no console, and a traceback printed to `sys.stderr` goes to a handle
that is not there. Qt makes that worse rather than better - an exception raised inside
a slot does not propagate to the code that emitted the signal; Qt catches it, hands it
to `sys.excepthook`, and carries on with the event loop. So an exception in, say, the
countdown's `finished` slot leaves the program running, looking well, and doing
nothing, with not one word anywhere about why.

That has now cost two long diagnoses in one day - 2026-11-13, a recording that would
not start, and before it a start that wedged the whole interface - both of which a
single line in a file would have answered in a minute.

So: `install()` puts a hook in front of `sys.excepthook` and `threading.excepthook`
that appends the traceback to `%APPDATA%\\ScreenRec\\errors.log`, then calls whatever
hook was there before, so nothing that already worked stops working. The file is
capped and rotated once, because a loop that throws on every timer tick would
otherwise fill the disk the recordings are going to.
"""
from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

#: Rotate at this size. One traceback is of the order of a kilobyte, so this keeps a
#: few hundred of them - far more than any single session produces, and small enough
#: that two of these files are not worth noticing on the disk.
MAX_BYTES = 512 * 1024

_installed = False
_lock = threading.Lock()


def log_path() -> Path:
    from . import paths

    return paths.config_dir() / "errors.log"


def _rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            previous = path.with_suffix(".log.1")
            previous.unlink(missing_ok=True)
            path.rename(previous)
    except OSError:
        # A log that cannot be rotated is still a log worth appending to, and this
        # runs while something has already gone wrong. Never raise from here.
        pass


def write(text: str) -> Path | None:
    """Append one entry, stamped. Returns the file, or None if it could not be written.

    No timestamp is taken from the caller: the point of the file is to be read later,
    and an entry without a clock in it cannot be lined up with what the user remembers.
    """
    from datetime import datetime

    path = log_path()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"\n===== {stamp} =====\n{text.rstrip()}\n")
        except OSError:
            return None
    return path


def _format(kind, value, tb) -> str:
    return "".join(traceback.format_exception(kind, value, tb))


def install() -> None:
    """Start recording unhandled exceptions. Safe to call twice; the second does nothing."""
    global _installed
    if _installed:
        return
    _installed = True

    previous = sys.excepthook

    def hook(kind, value, tb):
        write(_format(kind, value, tb))
        previous(kind, value, tb)

    sys.excepthook = hook

    # Threads have their own hook, and ScreenRec runs several that matter: the capture
    # feeder, the meters, the hotkey listener. An exception on any of them used to be
    # printed to the console this build has not got.
    previous_thread = threading.excepthook

    def thread_hook(args):
        name = getattr(args.thread, "name", "?")
        write(f"on thread {name}:\n" + _format(args.exc_type, args.exc_value, args.exc_traceback))
        previous_thread(args)

    threading.excepthook = thread_hook
