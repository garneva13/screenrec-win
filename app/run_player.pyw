"""Entry point for the player. Run with the project venv:

    .venv\\Scripts\\pythonw.exe app\\run_player.pyw [path]

The recorder's twin, `run.pyw`, and the same two lines. It exists because a packaged
build needs a script to point an executable at - `python -m screenrec.player` is not
something PyInstaller can be given - and because the two programs are two programs, so
each has its own way in.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screenrec.player.open import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
