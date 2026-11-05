"""Record a whole monitor with the red frame up, then look for the frame in the file.

The frame now draws *inside* the captured rectangle for a whole-monitor recording, which
is only safe because its window carries ``WDA_EXCLUDEFROMCAPTURE``. That is a claim about
what Windows does, not about what this code does, so it is measured against the real
capture path rather than argued:

* the frame window must be up, covering the monitor, and Windows must say it is excluded;
* the recording's outermost pixels must be the desktop's, not the frame's;
* the region case, which places the frame outside the rectangle, must still be clean too.

The border is counted rather than the whole image, because a desktop may legitimately
contain this red somewhere; the outer ring is where the outline is, so that is where its
absence is worth proving.

Usage:  .venv\\Scripts\\python.exe tools\\verify_capture_frame.py [--seconds 4]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6 import QtGui, QtWidgets  # noqa: E402

from screenrec import paths, recorder, winapi  # noqa: E402
from screenrec.ui import theme  # noqa: E402
from screenrec.ui.captureframe import THICKNESS, CaptureFrame  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
OUT = ROOT / "scratch" / "captureframe"
#: How deep into the image to look for the outline. The stroke is THICKNESS pixels on the
#: very edge; a couple more are taken in case a scaled desktop rounds it inwards.
RING = THICKNESS + 2


def count_in_ring(image: QtGui.QImage, target: QtGui.QColor, tolerance: int = 40) -> int:
    """How many pixels of `target` lie in the outermost `RING` pixels of `image`."""
    hits = 0

    def near(x: int, y: int) -> bool:
        pixel = image.pixelColor(x, y)
        return (
            abs(pixel.red() - target.red()) < tolerance
            and abs(pixel.green() - target.green()) < tolerance
            and abs(pixel.blue() - target.blue()) < tolerance
        )

    for y in list(range(RING)) + list(range(image.height() - RING, image.height())):
        for x in range(image.width()):
            if near(x, y):
                hits += 1
    for x in list(range(RING)) + list(range(image.width() - RING, image.width())):
        for y in range(RING, image.height() - RING):
            if near(x, y):
                hits += 1
    return hits


def stills_of(ffmpeg: str, clip: Path, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.png"):
        old.unlink()
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(clip),
         "-vf", "fps=1", "-frames:v", "3", str(folder / "f%02d.png")],
        capture_output=True, creationflags=CREATE_NO_WINDOW,
    )
    return sorted(folder.glob("*.png"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=4.0)
    args = parser.parse_args()

    winapi.enable_dpi_awareness()
    qt = QtWidgets.QApplication(sys.argv)
    theme.apply(qt)
    OUT.mkdir(parents=True, exist_ok=True)

    ffmpeg = str(paths.ffmpeg_exe())
    monitors = winapi.list_monitors()
    monitor = monitors[0]
    record = QtGui.QColor(theme.COLOURS["record"])

    frame = CaptureFrame()
    ok = True

    cases = [
        ("whole monitor", None),
        ("region", (monitor.x + 300, monitor.y + 300, 900, 500)),
    ]
    for name, crop in cases:
        shown = frame.show_for(monitor, crop, "Recording")
        qt.processEvents()
        time.sleep(0.6)
        print(f"\n--- {name} ---")
        print(f"  frame shown: {shown}"
              + ("" if shown else f"  ({frame.problem})"))
        print(f"  hidden from the capture: {frame.excluded_from_capture}")
        print(f"  frame window: {frame.window_rect}")
        if not shown:
            print("  nothing to measure; this build cannot hide the window.")
            ok = False
            continue

        # Zero red in the file is also what a frame that never painted would give, so
        # the widget itself is grabbed and counted: this is the half that proves the
        # user can see it.
        drawn = count_in_ring(frame.grab().toImage(), record)
        print(f"  outline pixels the widget actually paints: {drawn}  "
              f"{'VISIBLE' if drawn > 100 else 'NOTHING IS DRAWN'}")
        if drawn <= 100:
            ok = False

        clip = OUT / f"{name.replace(' ', '_')}.mp4"
        source = recorder.VideoSource(monitor=monitor, crop=crop)
        plan = recorder.RecordingPlan(
            sources=[source], outputs=[clip], framerate=60, draw_mouse=False,
            crash_safe=False,
        )
        rec = recorder.Recorder(ffmpeg)
        problem = rec.start(plan)
        if problem:
            print(f"  could not start: {problem}")
            ok = False
            continue
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            qt.processEvents()
            time.sleep(0.02)
        progress = rec.progress
        stop_problem = rec.stop()
        if stop_problem:
            print(f"  {stop_problem}")

        hits = 0
        looked = 0
        for still in stills_of(ffmpeg, clip, OUT / name.replace(" ", "_")):
            image = QtGui.QImage(str(still))
            if image.isNull():
                continue
            looked += 1
            hits += count_in_ring(image, record)
        clean = hits == 0
        print(f"  {progress.frames} frames recorded, {looked} stills examined")
        print(f"  outline pixels found in the recording's border: {hits}  "
              f"{'CLEAN' if clean else 'THE FRAME IS IN THE FILE'}")
        print(f"  stills to look at: {OUT / name.replace(' ', '_')}")
        if not clean or looked == 0:
            ok = False

    frame.hide_frame()
    print("\n" + ("PASS: the frame was on the screen and out of every file."
                  if ok else "FAIL: see above."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
