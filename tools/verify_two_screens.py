"""Record every screen through the application, then prove the files belong together.

Three things have to be true, and each is checked against the files rather than argued:
the screens are all there at full resolution and full frame rate; they start at the
same instant; and the sidecar describes the set well enough for a player to lay the
windows out the way the screens really sat.

The offset is measured the only way that answers it - a white square flashed on every
screen at the same moment, found again in each file. Comparing how long the files are
does not answer it, which is the mistake the first attempt at this made.

    .venv\\Scripts\\python.exe tools\\verify_two_screens.py [--seconds 10]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import paths, config, multi, paths, winapi  # noqa: E402
from screenrec.ui import theme  # noqa: E402
from screenrec.ui.app import Application  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
SQUARE = 260
FLASH_AT = 2.0
FLASH_MS = 120
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, text: str) -> bool:
    RESULTS.append((ok, text))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {text}")
    return ok


class Flash(QtWidgets.QWidget):
    """A white square in a screen's corner. In the capture on purpose - it is the mark."""

    def __init__(self, monitor: winapi.Monitor) -> None:
        super().__init__()
        self.lit = False
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setGeometry(monitor.x + 40, monitor.y + 40, SQUARE, SQUARE)

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


def flash_time(clip: Path) -> float | None:
    result = subprocess.run(
        [str(paths.ffmpeg_exe()), "-hide_banner", "-loglevel", "info", "-i", str(clip),
         "-vf", f"crop={SQUARE}:{SQUARE}:40:40,scale=16:16,signalstats,"
                "metadata=print:key=lavfi.signalstats.YAVG",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
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
    floor = min(v for _, v in samples)
    peak = max(v for _, v in samples)
    if peak - floor < 40:
        return None
    threshold = floor + (peak - floor) * 0.5
    for when, value in samples:
        if value >= threshold:
            return when
    return None


def probe(clip: Path) -> tuple[int, int, int, float]:
    result = subprocess.run(
        [str(paths.ffprobe_exe()), "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries",
         "stream=width,height,nb_read_frames,duration",
         "-of", "default=noprint_wrappers=1", str(clip)],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    return (
        int(fields.get("width", 0)),
        int(fields.get("height", 0)),
        int(fields.get("nb_read_frames", 0)),
        float(fields.get("duration", 0) or 0),
    )


def main() -> int:
    # Its own settings folder: a harness must not teach the user's
    # measured MB/min with a ten-second capture of an idle desktop, nor
    # read a probe cache a previous run left. See `paths.own_settings_folder`.
    with paths.own_settings_folder():
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--seconds", type=float, default=10.0)
        args = parser.parse_args()

        winapi.enable_dpi_awareness()
        qt = QtWidgets.QApplication(sys.argv)
        theme.apply(qt)

        monitors = winapi.list_monitors()
        if len(monitors) < 2:
            print("This needs two monitors.")
            return 2

        folder = ROOT / "scratch" / "twofinal"
        folder.mkdir(parents=True, exist_ok=True)
        for old in list(folder.glob("*.mp4")) + list(folder.glob("*.json")):
            old.unlink()

        settings_file = paths.config_path()
        saved = settings_file.read_text(encoding="utf-8") if settings_file.is_file() else None

        controller = Application()
        controller.settings.source_kind = config.SOURCE_ALL_SCREENS
        controller.settings.output_dir = str(folder)
        controller.settings.countdown = 0
        controller.settings.mic_enabled = False
        controller.settings.system_audio_enabled = False
        controller.settings.click_highlight = False
        controller.settings.capture_frame = False
        controller._refresh_bar()

        flashes = [Flash(m) for m in monitors]
        for flash in flashes:
            flash.show()
        qt.processEvents()
        time.sleep(0.5)

        print(f"\nrecording {len(monitors)} screens for {args.seconds:.0f} s")
        controller.start_recording()
        began = time.monotonic()
        while time.monotonic() - began < args.seconds:
            now = time.monotonic() - began
            lit = FLASH_AT <= now < FLASH_AT + FLASH_MS / 1000
            for flash in flashes:
                flash.set_lit(lit)
            qt.processEvents()
            time.sleep(0.004)
        for flash in flashes:
            flash.set_lit(False)

        check(controller.multi is not None and controller.multi.recording,
              "all screens are recording")
        progress = controller.multi.progress if controller.multi else None
        if progress:
            print(f"      {progress.screens} screens, slowest {progress.worst_fps:.1f} fps, "
                  f"{progress.dropped} dropped")

        controller.stop_recording()
        for _ in range(80):
            qt.processEvents()
            time.sleep(0.05)
        for flash in flashes:
            flash.close()

        clips = sorted(folder.glob("*_screen*.mp4"))
        check(len(clips) == len(monitors),
              f"one file per screen ({len(clips)} of {len(monitors)})")

        rates, times = [], []
        for clip, monitor in zip(clips, monitors):
            width, height, frames, duration = probe(clip)
            fps = frames / duration if duration else 0
            rates.append(fps)
            print(f"      {clip.name}: {width}x{height}, {frames} frames / "
                  f"{duration:.2f}s = {fps:.1f} fps, "
                  f"{clip.stat().st_size / 1e6:.1f} MB")
            expected = (
                (monitor.width, monitor.height) if not monitor.rotated
                else (monitor.width, monitor.height)
            )
            check(
                (width, height) == expected,
                f"screen {monitor.index} is at its own full size "
                f"({width}x{height}, wanted {expected[0]}x{expected[1]})",
            )
            times.append(flash_time(clip))

        if rates:
            check(min(rates) > 50, f"every screen held its frame rate "
                                   f"(slowest {min(rates):.1f} fps)")

        if all(t is not None for t in times) and len(times) > 1:
            offset = (max(times) - min(times)) * 1000
            check(offset <= 50, f"the screens start together ({offset:.0f} ms apart)")
        else:
            check(False, "the sync mark was found in every file")

        sidecars = list(folder.glob(f"*{multi.SIDECAR_SUFFIX}"))
        check(len(sidecars) == 1, f"a sidecar describes the set ({len(sidecars)})")
        if sidecars:
            document, problem = multi.read_sidecar(sidecars[0])
            check(not problem, f"the sidecar reads back cleanly ({problem or 'no problem'})")
            screens = document.get("screens", [])
            check(len(screens) == len(monitors), "it lists every screen")
            check(
                all("desktop" in s and len(s["desktop"]) == 4 for s in screens),
                "each screen carries the rectangle it occupied, so a player can arrange them",
            )
            lefts = [s["desktop"][0] for s in screens]
            check(
                lefts == sorted(lefts) or len(set(lefts)) == len(lefts),
                "the screens can be told apart by position",
            )
            print("      " + json.dumps(document, ensure_ascii=False)[:220] + "…")

        check(
            not list(folder.glob("*.part*.mkv")),
            "no segments were left behind",
        )

        controller.shutdown()
        qt.processEvents()
        if saved is not None:
            settings_file.write_text(saved, encoding="utf-8")

        failures = [text for ok, text in RESULTS if not ok]
        print(f"\n{len(RESULTS) - len(failures)} of {len(RESULTS)} checks passed")
        for text in failures:
            print(f"  FAILED: {text}")
        print(f"\nthe files are in {folder}")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
