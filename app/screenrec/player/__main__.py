"""``python -m screenrec.player [path]`` - open a recording and play it.

With no path it opens the newest recording in the folder the recorder is writing to, so
that the player can be started from a shortcut with nothing to type. `ScreenRecPlayer.cmd`
beside the project's `ScreenRec.cmd` does exactly that.

Everything that can go wrong is handled in `open.run`, which reports it and returns an
exit code rather than letting a traceback reach a user who started this from a shortcut
and has no console to read it in.
"""
from __future__ import annotations

import sys

from .open import run


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
