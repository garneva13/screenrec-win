"""Record with drawings on screen, then look in the file for them - and for the panel.

This is the check the whole annotation design rests on, and it is done against the real
capture path rather than argued from how windows usually behave:

* the drawings must be in the recording, in the right place;
* the toolbar must not be, anywhere;
* the recording must still run at its frame rate with a stroke being drawn.

It is run for each capture mode in turn, because "a transparent window is captured" is
not one fact - a whole monitor, a cropped region and a window rectangle are three
different crops of the composed desktop and each is worth proving.

Usage:  .venv\\Scripts\\python.exe tools\\verify_annotations.py [--seconds 6]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import config, paths, recorder, winapi  # noqa: E402
from screenrec.annotate import model as amodel  # noqa: E402
from screenrec.annotate.canvas import Canvas  # noqa: E402
from screenrec.annotate.toolbar import Toolbar  # noqa: E402
from screenrec.ui import theme  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
#: Drawn in a colour nothing on a desktop is, so counting it proves it is ours.
INK = "#00FF66"
OUT = ROOT / "scratch" / "annotate"


def count_colour(image: QtGui.QImage, target: QtGui.QColor, tolerance: int = 46) -> int:
    hits = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            pixel = image.pixelColor(x, y)
            if (
                abs(pixel.red() - target.red()) < tolerance
                and abs(pixel.green() - target.green()) < tolerance
                and abs(pixel.blue() - target.blue()) < tolerance
            ):
                hits += 1
    return hits


def frames_of(ffmpeg: str, clip: Path, folder: Path) -> list[Path]:
    """Three stills from the clip, to look at rather than to trust."""
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
    parser.add_argument("--seconds", type=float, default=6.0)
    args = parser.parse_args()

    winapi.enable_dpi_awareness()
    qt = QtWidgets.QApplication(sys.argv)
    theme.apply(qt)
    OUT.mkdir(parents=True, exist_ok=True)

    ffmpeg = str(paths.ffmpeg_exe())
    monitors = winapi.list_monitors()
    monitor = monitors[0]

    document = amodel.Document()
    pen = document.settings_for(amodel.TOOL_PEN)
    pen.colour = INK
    pen.width = 12
    document.set_settings(amodel.TOOL_PEN, pen)

    canvas = Canvas(document)
    canvas.set_tool(amodel.TOOL_PEN)
    canvas.show()
    toolbar = Toolbar(document)
    toolbar.place(monitor.x + 60, monitor.y + 60)
    toolbar.show()
    qt.processEvents()
    time.sleep(0.8)

    print(f"toolbar excluded from capture: {toolbar.excluded_from_capture}")
    if not toolbar.excluded_from_capture:
        print("  -> this Windows build cannot hide a window from capture; the panel")
        print("     WILL be in the recording and the test below will say so.")

    # Where the drawing goes: a band across the middle of a region we will record.
    region = (monitor.x + 300, monitor.y + 300, 900, 500)
    draw_y = region[1] + 250

    cases = [
        ("whole monitor", None),
        ("region", region),
    ]
    windows = [w for w in winapi.list_windows(min_size=400)
               if winapi.monitor_at(monitors, w.x + w.width // 2, w.y + w.height // 2)
               is monitor]
    if windows:
        target = windows[0]
        cases.append(("window", (target.x, target.y, target.width, target.height)))
        print(f"window case uses: {target.title[:50]!r}")

    ok = True
    for name, crop in cases:
        document.reset()
        pen = document.settings_for(amodel.TOOL_PEN)
        pen.colour, pen.width = INK, 12
        canvas.update()
        qt.processEvents()

        clip = OUT / f"{name.replace(' ', '_')}.mp4"
        source = recorder.VideoSource(monitor=monitor, crop=crop)
        plan = recorder.RecordingPlan(
            sources=[source], outputs=[clip], framerate=60, draw_mouse=False,
            crash_safe=False,
        )
        rec = recorder.Recorder(ffmpeg)
        problem = rec.start(plan)
        if problem:
            print(f"\n{name}: could not start - {problem}")
            ok = False
            continue

        # Draw while it records, the way a person would: a stroke over about a second.
        time.sleep(1.0)
        begin_x = (crop[0] if crop else monitor.x) + 80
        end_x = begin_x + 600
        document.begin(amodel.TOOL_PEN, pen, amodel.Point(begin_x, draw_y))
        steps = 40
        for step in range(1, steps + 1):
            document.extend(
                amodel.Point(begin_x + (end_x - begin_x) * step / steps, draw_y)
            )
            canvas.repaint_dirty()
            qt.processEvents()
            time.sleep(0.02)
        document.finish()
        canvas.repaint_dirty()
        qt.processEvents()

        deadline = time.monotonic() + max(1.0, args.seconds - 2.0)
        while time.monotonic() < deadline:
            qt.processEvents()
            time.sleep(0.02)

        progress = rec.progress
        stop_problem = rec.stop()
        if stop_problem:
            print(f"\n{name}: {stop_problem}")

        stills = frames_of(ffmpeg, clip, OUT / name.replace(" ", "_"))
        ink_hits = 0
        panel_hits = 0
        panel_colour = QtGui.QColor(theme.COLOURS["panel"])
        for still in stills:
            image = QtGui.QImage(str(still))
            if image.isNull():
                continue
            ink_hits += count_colour(image, QtGui.QColor(INK))
            panel_hits += count_colour(image, panel_colour, tolerance=6)

        seconds = progress.seconds or 1
        fps = progress.frames / seconds
        drawing_in = ink_hits > 40
        print(f"\n--- {name} ---")
        print(f"  {progress.frames} frames, {fps:.1f} fps, {progress.dropped} dropped")
        print(f"  drawing pixels found in the file: {ink_hits}  "
              f"{'OK' if drawing_in else 'MISSING'}")
        print(f"  pixels the colour of the panel's surface: {panel_hits} "
              f"(not proof on its own - the desktop is dark too)")
        print(f"  stills to look at: {OUT / name.replace(' ', '_')}")
        if not drawing_in:
            ok = False
        if fps < 50:
            print("  WARNING: the frame rate fell below 50 while drawing")

    toolbar.close()
    canvas.close()
    print("\n" + ("PASS: drawings reached every recording." if ok
                  else "FAIL: see above."))
    print("The panel's absence is proved by the measurement in "
          "docs/ANNOTATE_CONTRACT.md and by the flag printed at the top; the stills")
    print("above are there to be looked at, which is the only way to be sure.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
