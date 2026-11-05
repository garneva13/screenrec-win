"""Do two screens recorded together really start at the same instant?

That is the whole promise of recording every monitor at once: a viewer moving their eyes
from one file to the other must be seeing the same moment. Comparing how long the two
files are does not answer it - two recordings can be the same length and still be half a
second apart.

So: flash a white square on **both** screens at the same instant, in the capture, and
find which frame it lands on in each file. The difference between those two frame times
is the real offset, and it is the number the player would have to correct for.

Both shapes are measured, because they are the architecture choice:
  * one ffmpeg with two ddagrab inputs - one clock, slightly fewer frames;
  * two ffmpeg processes - full frame rate, two clocks.

    .venv\\Scripts\\python.exe tools\\measure_two_sync.py
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import paths, recorder, winapi  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
OUT = ROOT / "scratch" / "twosync"
SQUARE = 260
FLASH_AT = 2.0
FLASH_MS = 120


class Flash(QtWidgets.QWidget):
    """A white square in the corner of one screen. Deliberately NOT excluded from
    capture - it is the mark being measured."""

    def __init__(self, monitor: winapi.Monitor) -> None:
        super().__init__()
        self.lit = False
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setGeometry(
            monitor.x + 40, monitor.y + 40, SQUARE, SQUARE
        )

    def set_lit(self, value: bool) -> None:
        if value != self.lit:
            self.lit = value
            self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.fillRect(
                self.rect(),
                QtGui.QColor("white") if self.lit else QtGui.QColor("black"),
            )
        finally:
            painter.end()


def flash_time(clip: Path, monitor: winapi.Monitor) -> float | None:
    """When the square went white in this file, in seconds. None if never.

    The corner is cropped out and its average luma watched; the mark is a step from
    black to white, which nothing on a desktop does by accident in one frame.
    """
    args = [
        str(paths.ffmpeg_exe()), "-hide_banner", "-loglevel", "info", "-i", str(clip),
        # The square sits at 40,40 of the screen; in a whole-screen recording that is
        # 40,40 of the frame too.
        "-vf", f"crop={SQUARE}:{SQUARE}:40:40,scale=16:16,signalstats,"
               "metadata=print:key=lavfi.signalstats.YAVG",
        "-an", "-f", "null", "-",
    ]
    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    text = result.stdout + result.stderr
    samples: list[tuple[float, float]] = []
    pts: float | None = None
    for line in text.splitlines():
        match = re.search(r"pts_time:([0-9.]+)", line)
        if match:
            pts = float(match.group(1))
            continue
        match = re.search(r"YAVG=([0-9.]+)", line)
        if match and pts is not None:
            samples.append((pts, float(match.group(1))))
            pts = None
    if not samples:
        return None
    floor = min(value for _, value in samples)
    peak = max(value for _, value in samples)
    if peak - floor < 40:
        return None
    threshold = floor + (peak - floor) * 0.5
    for when, value in samples:
        if value >= threshold:
            return when
    return None


def measure(app, monitors, flashes, seconds: float, two_processes: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tag = "twoproc" if two_processes else "oneproc"
    for old in OUT.glob(f"{tag}*.mp4"):
        old.unlink()

    sources = [recorder.VideoSource(monitor=m) for m in monitors]
    outputs = [OUT / f"{tag}_{m.index}.mp4" for m in monitors]
    recorders: list[recorder.Recorder] = []

    if two_processes:
        for source, target in zip(sources, outputs):
            rec = recorder.Recorder(str(paths.ffmpeg_exe()))
            problem = rec.start(
                recorder.RecordingPlan(
                    sources=[source], outputs=[target], framerate=60,
                    draw_mouse=False, crash_safe=False,
                )
            )
            if problem:
                print(f"  could not start: {problem}")
                return
            recorders.append(rec)
    else:
        rec = recorder.Recorder(str(paths.ffmpeg_exe()))
        problem = rec.start(
            recorder.RecordingPlan(
                sources=sources, outputs=outputs,
                layout=recorder.LAYOUT_SEPARATE, framerate=60,
                draw_mouse=False, crash_safe=False,
            )
        )
        if problem:
            print(f"  could not start: {problem}")
            return
        recorders.append(rec)

    began = time.monotonic()
    while time.monotonic() - began < seconds:
        now = time.monotonic() - began
        lit = FLASH_AT <= now < FLASH_AT + FLASH_MS / 1000
        for flash in flashes:
            flash.set_lit(lit)
        app.processEvents()
        time.sleep(0.004)
    for flash in flashes:
        flash.set_lit(False)
    for rec in recorders:
        rec.stop()

    times: list[float | None] = []
    for target, monitor in zip(outputs, monitors):
        times.append(flash_time(target, monitor))

    shown = ", ".join("not found" if t is None else f"{t:.3f}s" for t in times)
    print(f"  the mark lands at: {shown}")
    if all(t is not None for t in times) and len(times) > 1:
        offset = (max(times) - min(times)) * 1000
        print(f"  the two screens are {offset:.0f} ms apart")
        if offset < 34:
            print("  -> within two frames: a viewer cannot see this")
        elif offset < 100:
            print("  -> noticeable only if you look for it")
        else:
            print("  -> the player would have to correct for this")
    for target in outputs:
        if target.is_file():
            probe = subprocess.run(
                [str(paths.ffprobe_exe()), "-v", "error", "-select_streams", "v:0",
                 "-count_frames", "-show_entries", "stream=nb_read_frames,duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(target)],
                capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
            ).stdout.split()
            if len(probe) >= 2:
                try:
                    frames, duration = int(probe[0]), float(probe[1])
                    print(f"    {target.name}: {frames} frames / {duration:.2f}s = "
                          f"{frames / duration:.1f} fps")
                except ValueError:
                    # ffprobe gave something unparseable for a clip that exists; the
                    # offset above is the measurement that matters and it already
                    # printed. Nothing to act on here.
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    winapi.enable_dpi_awareness()
    app = QtWidgets.QApplication(sys.argv)
    monitors = winapi.list_monitors()
    if len(monitors) < 2:
        print("This needs two monitors.")
        return 2

    flashes = [Flash(m) for m in monitors]
    for flash in flashes:
        flash.show()
    app.processEvents()
    time.sleep(0.6)

    print("ONE ffmpeg process, two ddagrab inputs, two files")
    measure(app, monitors, flashes, args.seconds, two_processes=False)
    print()
    print("TWO ffmpeg processes, one per screen")
    measure(app, monitors, flashes, args.seconds, two_processes=True)

    for flash in flashes:
        flash.close()
    print()
    print("The mark is a white square flashed on BOTH screens at the same instant,")
    print("found again in each file. This is the real offset, not a difference in length.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
