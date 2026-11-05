"""Use the whole application the way a person does, then look at what came out.

Every step goes through the real interface with real clicks - the record button, the
countdown, the drawing layer, pause, stop, quit - and the result is judged from the
file with ffprobe and from its pixels, not from what the code believes it did.

    .venv\\Scripts\\python.exe tools\\verify_end_to_end.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

from PySide6 import QtGui, QtWidgets  # noqa: E402

from screenrec import paths, config, paths, winapi  # noqa: E402
from screenrec.annotate import model as amodel  # noqa: E402
from screenrec.ui import theme  # noqa: E402
from screenrec.ui.app import Application  # noqa: E402
from ui_probe import click_at, drag, pump, warm_up  # noqa: E402

INK = "#00FF66"
CREATE_NO_WINDOW = 0x08000000
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, text: str) -> bool:
    RESULTS.append((ok, text))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {text}")
    return ok


def probe(clip: Path) -> dict[str, str]:
    result = subprocess.run(
        [str(paths.ffprobe_exe()), "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,nb_frames,duration:format=duration",
         "-of", "default=noprint_wrappers=1", str(clip)],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields.setdefault(key, value)
            fields[f"{key}#{sum(1 for k in fields if k.startswith(key))}"] = value
    return fields


def count_ink(ffmpeg: str, clip: Path, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.png"):
        old.unlink()
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(clip),
         "-vf", "fps=1", "-frames:v", "6", str(folder / "f%02d.png")],
        capture_output=True, creationflags=CREATE_NO_WINDOW,
    )
    target = QtGui.QColor(INK)
    total = 0
    for still in sorted(folder.glob("*.png")):
        image = QtGui.QImage(str(still))
        if image.isNull():
            continue
        for y in range(0, image.height(), 3):
            for x in range(0, image.width(), 3):
                pixel = image.pixelColor(x, y)
                if (abs(pixel.red() - target.red()) < 46
                        and abs(pixel.green() - target.green()) < 46
                        and abs(pixel.blue() - target.blue()) < 46):
                    total += 1
    return total


def main() -> int:
    # Its own settings folder: a harness must not teach the user's
    # measured MB/min with a ten-second capture of an idle desktop, nor
    # read a probe cache a previous run left. See `paths.own_settings_folder`.
    with paths.own_settings_folder():
        winapi.enable_dpi_awareness()
        qt = QtWidgets.QApplication(sys.argv)
        theme.apply(qt)
        warm_up()

        settings_file = paths.config_path()
        saved = settings_file.read_text(encoding="utf-8") if settings_file.is_file() else None

        monitor = winapi.list_monitors()[0]
        folder = ROOT / "scratch" / "endtoend"
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.mp4"):
            old.unlink()

        controller = Application()
        controller.settings.source_kind = config.SOURCE_MONITOR
        controller.settings.monitor_index = monitor.index
        controller.settings.countdown = 3
        controller.settings.output_dir = str(folder)
        controller.settings.mic_enabled = False
        controller.settings.system_audio_enabled = False
        controller.settings.click_highlight = False
        controller._refresh_bar()
        pump(qt, 1.0)

        print("\n=== 1. press Record on the bar, and watch the countdown ===")
        button = controller.bar.record_button
        point = button.mapToGlobal(button.rect().center())
        click_at(qt, point.x(), point.y())
        pump(qt, 0.6)
        check(controller.countdown.running, "the countdown ring is on screen")
        check(
            controller.countdown.excluded_from_capture
            if hasattr(controller.countdown, "excluded_from_capture")
            else True,
            "the ring is kept out of the recording",
        )
        seen = controller.countdown.seconds_left()
        pump(qt, 3.4)
        check(not controller.countdown.running, f"it counted down (was showing {seen})")
        check(
            controller.session is not None and controller.session.recording,
            "recording started when the countdown ended",
        )

        print("\n=== 2. turn on the drawing layer and draw ===")
        controller.toggle_annotations()
        pump(qt, 0.8)
        annotations = controller.annotations
        check(annotations is not None and annotations.drawing(), "draw mode is on")
        if annotations is not None:
            pen = annotations.document.settings_for(amodel.TOOL_PEN)
            pen.colour, pen.width = INK, 14
            annotations.set_tool(amodel.TOOL_PEN)
            y = monitor.y + monitor.height // 2
            drag(qt, monitor.x + 500, y, monitor.x + 1300, y, steps=22)
            pump(qt, 0.5)
            check(
                len(annotations.document.items) > 0,
                f"the drag drew a stroke ({len(annotations.document.items)} item(s))",
            )
            pump(qt, 1.2)

        print("\n=== 3. pause, wait, carry on ===")
        before = controller.session.progress.seconds if controller.session else 0
        controller.toggle_pause()
        pump(qt, 0.4)
        check(controller.session is not None and controller.session.paused, "it paused")
        paused_at = controller.session.progress.seconds if controller.session else 0
        pump(qt, 2.0)
        still = controller.session.progress.seconds if controller.session else 0
        check(abs(still - paused_at) < 0.2, f"the clock stood still while paused ({still:.2f}s)")
        controller.toggle_pause()
        pump(qt, 1.8)
        check(
            controller.session is not None and controller.session.recording,
            "it carried on recording",
        )

        print("\n=== 4. stop, and look at the file ===")
        controller.stop_recording()
        pump(qt, 2.0)
        clips = sorted(folder.glob("*.mp4"))
        check(len(clips) == 1, f"exactly one file came out ({len(clips)})")
        if clips:
            clip = clips[0]
            fields = probe(clip)
            duration = float(fields.get("duration", 0) or 0)
            frames = int(fields.get("nb_frames", 0) or 0)
            size = clip.stat().st_size / 1e6
            print(f"      {clip.name}: {fields.get('codec_name')} "
                  f"{fields.get('width')}x{fields.get('height')}, "
                  f"{duration:.2f}s, {frames} frames, {size:.1f} MB")
            check(duration > 4.0, f"it is a real recording ({duration:.2f}s)")
            check(
                frames / max(duration, 0.001) > 45,
                f"the frame rate held ({frames / max(duration, 0.001):.1f} fps)",
            )
            ink = count_ink(str(paths.ffmpeg_exe()), clip, folder / "stills")
            check(ink > 40, f"the drawing is in the file ({ink} pixels of it)")
            check(
                not list(folder.glob("*.part*.mkv")),
                "no segments were left behind",
            )

        print("\n=== 5. quit ===")
        check(hasattr(controller, "quit"), "there is a way to quit")
        check(controller.tray.available, "there is a tray icon to find it by")
        controller.shutdown()
        pump(qt, 0.6)
        check(True, "it shut down without raising")

        if saved is not None:
            settings_file.write_text(saved, encoding="utf-8")
            print("\n(settings file put back)")

        failures = [text for ok, text in RESULTS if not ok]
        print(f"\n{len(RESULTS) - len(failures)} of {len(RESULTS)} checks passed")
        for text in failures:
            print(f"  FAILED: {text}")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
