"""Entry point. Run with the project venv: .venv\\Scripts\\pythonw.exe app\\run.pyw"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screenrec.ui.app import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
