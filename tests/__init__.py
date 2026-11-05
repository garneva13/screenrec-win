"""Test package for ScreenRec.

The application package lives at ``B:\\ScreenRec\\app\\screenrec`` rather than at the
repository root, so ``app`` has to be on ``sys.path`` before any test module imports
``screenrec``. Doing it here means it happens exactly once, at package import time, which
``unittest discover -s tests -t .`` triggers before it loads a single test module - so no
test file needs its own path preamble.

The same hook makes the output streams safe for non-ASCII. Measured here on 2026-11-03:
with the suite's output redirected, ``sys.stdout`` comes up as **cp1251**, which cannot
encode the ``x`` of ``Monitor.label`` (U+00D7). ``sys.stderr`` survives it because Python
gives that stream ``errors='backslashreplace'``, and ``python -m unittest`` happens to
report to stderr - but a runner pointed at stdout dies mid-report:
``UnicodeEncodeError: 'charmap' codec can't encode character '\\xd7'``, which takes the
rest of the run with it. One failing test must not be able to hide every later one.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"


def _make_streams_unicode_safe() -> None:
    """Put stdout and stderr on UTF-8 so a label in a failure message cannot kill the run.

    Both streams are left alone if they cannot be reconfigured - under ``pythonw`` they
    are ``None``, and a harness may hand us a plain object with no ``reconfigure``. The
    fallback error handler is ``backslashreplace`` rather than ``replace`` so that an
    unencodable character is still identifiable in the report instead of becoming "?".
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError) as exc:  # detached, or already read from
            print(
                f"tests: could not switch {getattr(stream, 'name', stream)!r} to UTF-8 "
                f"({exc}); a non-ASCII failure message may be unreadable.",
                file=sys.__stderr__,
            )


_make_streams_unicode_safe()

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

if not (APP_DIR / "screenrec" / "__init__.py").is_file():
    raise RuntimeError(
        f"The screenrec package was not found under {APP_DIR}. "
        "Run the suite from the project root: "
        'python -m unittest discover -s tests -t .'
    )
