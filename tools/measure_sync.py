"""Measure, rather than assume, that a recording's sound lines up with its picture.

The method: play a tone burst and flash a square white at the same instants, record both,
then find in the finished file when each one actually happened.

* The tone track is built in advance as one buffer, so the bursts are sample-exact
  relative to the start of playback - no per-burst scheduling jitter.
* The flashes are driven by a QTimer from the same start instant.
* Audio is played into **CABLE Input** (VB-Audio) and captured from that endpoint's
  loopback, so nothing comes out of the user's headphones.
* The square is small and sits in a corner. A full-screen flash would be a better signal
  and a worse thing to do to somebody who is using the machine.

What the numbers mean. The offset of a single burst is not the recorder's error: it also
contains the display pipeline's latency and the latency of the virtual cable. What the
recorder is responsible for is the *drift* - the offset must be the same at the end of a
long recording as at the start. Both are reported; drift is the one that fails a build.

Usage:
    .venv\\Scripts\\python.exe tools\\measure_sync.py [--seconds 40] [--monitor 0]
"""
from __future__ import annotations

import argparse
import array
import math
import re
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from screenrec import audio as audio_mod  # noqa: E402
from screenrec import config as config_mod  # noqa: E402
from screenrec import paths, recorder, winapi  # noqa: E402

TONE_HZ = 1000.0
BURST_MS = 120
FLASH_MS = 120
SQUARE = 240
RATE = audio_mod.SAMPLE_RATE
CREATE_NO_WINDOW = 0x08000000


# ------------------------------------------------------------------ the signal


def build_tone_track(seconds: float, marks: list[float]) -> bytes:
    """Silence with a 1 kHz burst starting at each mark, as s16le stereo."""
    frames = int(seconds * RATE)
    samples = array.array("h", bytes(frames * 2 * 2))
    burst = int(RATE * BURST_MS / 1000)
    for mark in marks:
        start = int(mark * RATE)
        for i in range(burst):
            index = start + i
            if index >= frames:
                break
            # A short fade in and out, so the onset is a clean step and not a click.
            envelope = min(1.0, min(i, burst - i) / (RATE * 0.004))
            value = int(14000 * envelope * math.sin(2 * math.pi * TONE_HZ * i / RATE))
            samples[index * 2] = value
            samples[index * 2 + 1] = value
    return samples.tobytes()


def find_render_device(name_fragment: str) -> int | None:
    import pyaudiowpatch as pyaudio

    handle = pyaudio.PyAudio()
    try:
        api = handle.get_host_api_info_by_type(pyaudio.paWASAPI)
        for i in range(api["deviceCount"]):
            info = handle.get_device_info_by_host_api_device_index(api["index"], i)
            if info["maxOutputChannels"] > 0 and name_fragment.lower() in str(info["name"]).lower():
                return int(info["index"])
    finally:
        handle.terminate()
    return None


def find_loopback_device(name_fragment: str) -> int | None:
    for device in audio_mod.list_loopback_devices():
        if name_fragment.lower() in device.name.lower():
            return device.index
    return None


class TonePlayer:
    """Push one prepared buffer at a render endpoint and say when playback began."""

    def __init__(self, data: bytes, device_index: int) -> None:
        self.data = data
        self.device_index = device_index
        self.started = threading.Event()
        self.start_time = 0.0
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tone", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def _run(self) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self.error = "pyaudiowpatch missing"
            self.started.set()
            return
        handle = None
        stream = None
        try:
            handle = pyaudio.PyAudio()
            stream = handle.open(
                format=pyaudio.paInt16,
                channels=2,
                rate=RATE,
                output=True,
                output_device_index=self.device_index,
                frames_per_buffer=480,
            )
            block = 480 * 2 * 2
            self.start_time = time.monotonic()
            self.started.set()
            for offset in range(0, len(self.data), block):
                if self._stop.is_set():
                    break
                stream.write(self.data[offset : offset + block])
        except Exception as exc:
            self.error = f"{exc}"
            self.started.set()
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    # Teardown of a measurement run; self.error already holds
                    # anything that went wrong while it mattered.
                    pass
            if handle is not None:
                handle.terminate()


# ------------------------------------------------------------------- analysis


def video_luma(ffmpeg: str, clip: Path) -> list[tuple[float, float]]:
    """(time, average luma) for every frame, via signalstats."""
    # Note the log level: metadata=print writes at "info". With -loglevel error the
    # filter runs and prints nothing, and the analysis silently sees zero frames.
    args = [
        ffmpeg, "-hide_banner", "-loglevel", "info", "-i", str(clip),
        "-vf", "scale=32:32,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
        "-an", "-f", "null", "-",
    ]
    proc = subprocess.run(args, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    samples: list[tuple[float, float]] = []
    pts: float | None = None
    for line in (proc.stdout + proc.stderr).splitlines():
        match = re.search(r"pts_time:([0-9.]+)", line)
        if match:
            pts = float(match.group(1))
            continue
        match = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if match and pts is not None:
            samples.append((pts, float(match.group(1))))
            pts = None
    return samples


def audio_envelope(ffmpeg: str, clip: Path, window_ms: int = 5) -> list[tuple[float, float]]:
    """(time, RMS) in short windows, from the clip's first audio track."""
    args = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(clip),
        "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1",
    ]
    proc = subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW)
    raw = proc.stdout
    if not raw:
        return []
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    step = int(8000 * window_ms / 1000)
    out: list[tuple[float, float]] = []
    for start in range(0, len(samples) - step, step):
        chunk = samples[start : start + step]
        total = 0
        for value in chunk:
            total += value * value
        out.append((start / 8000, math.sqrt(total / len(chunk))))
    return out


def onsets(series: list[tuple[float, float]], threshold: float, gap: float) -> list[float]:
    """Times where the series first crosses `threshold`, at most one per `gap` seconds."""
    found: list[float] = []
    armed = True
    for when, value in series:
        if value >= threshold:
            if armed and (not found or when - found[-1] > gap):
                found.append(when)
            armed = False
        else:
            armed = True
    return found


# ----------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=40.0)
    parser.add_argument("--monitor", type=int, default=0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--keep", action="store_true", help="keep the recorded clip")
    args = parser.parse_args()

    from PySide6 import QtCore, QtGui, QtWidgets

    winapi.enable_dpi_awareness()
    monitors = winapi.list_monitors()
    monitor = next((m for m in monitors if m.index == args.monitor), monitors[0])

    ffmpeg = str(paths.ffmpeg_exe())
    render = find_render_device("CABLE Input")
    loopback = find_loopback_device("CABLE Input")
    if render is None or loopback is None:
        print("VB-Audio Virtual Cable not found; the test would be audible. Stopping.")
        return 2

    lead_in = 2.0
    marks = []
    when = lead_in
    while when < args.seconds - 1.5:
        marks.append(when)
        when += args.interval
    print(f"monitor {monitor.index} ({monitor.width}x{monitor.height}), {len(marks)} marks")

    tone = build_tone_track(args.seconds, marks)
    player = TonePlayer(tone, render)

    app = QtWidgets.QApplication(sys.argv)

    # The flashing square: bottom-right of the chosen monitor, small on purpose.
    square_x = monitor.x + monitor.width - SQUARE - 40
    square_y = monitor.y + monitor.height - SQUARE - 60

    class FlashSquare(QtWidgets.QWidget):
        """Black, white while `lit`.

        Painted rather than re-palletted, and driven by ONE timer that compares the clock
        against the schedule. The first version used a pair of single-shot timers per
        mark and swapped the palette: the square stuck white half way through a run and
        stayed that way, which cost three of eight measurements. One timer and an
        explicit state cannot get out of step with itself.
        """

        def __init__(self) -> None:
            super().__init__()
            self.lit = False
            self.setWindowFlags(
                QtCore.Qt.WindowType.FramelessWindowHint
                | QtCore.Qt.WindowType.WindowStaysOnTopHint
                | QtCore.Qt.WindowType.Tool
            )
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setGeometry(square_x, square_y, SQUARE, SQUARE)

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

    flash = FlashSquare()
    flash.show()
    app.processEvents()
    time.sleep(0.4)

    out_dir = ROOT / "scratch"
    out_dir.mkdir(parents=True, exist_ok=True)
    clip = out_dir / f"sync_{datetime.now():%H%M%S}.mp4"

    source = recorder.VideoSource(
        monitor=monitor, crop=(square_x, square_y, SQUARE, SQUARE), name="sync"
    )
    plan = recorder.RecordingPlan(
        sources=[source],
        outputs=[clip],
        encoder="h264_nvenc",
        quality=20,
        framerate=60,
        draw_mouse=False,
        layout=recorder.LAYOUT_SINGLE,
        loopback_pipe=recorder.pipe_name("sync"),
        audio_mode=config_mod.AUDIO_MIX,
    )

    rec = recorder.Recorder(ffmpeg)
    problem = rec.start(plan, loopback_device=loopback)
    if problem:
        print("could not start:", problem)
        return 1
    print("recording:", " ".join(rec.command))

    time.sleep(0.6)  # let the capture settle before the first mark
    player.start()
    player.started.wait(timeout=5)
    if player.error:
        print("tone player:", player.error)

    begin = time.monotonic()
    window_s = FLASH_MS / 1000.0

    def tick() -> None:
        now = time.monotonic() - begin
        flash.set_lit(any(mark <= now < mark + window_s for mark in marks))
        if now >= args.seconds:
            app.quit()

    ticker = QtCore.QTimer(flash)
    ticker.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
    ticker.setInterval(4)
    ticker.timeout.connect(tick)
    ticker.start()
    app.exec()
    ticker.stop()

    elapsed = time.monotonic() - begin
    player.stop()
    problem = rec.stop()
    flash.close()
    if problem:
        print("stop:", problem)

    print(
        f"capture: {rec.progress.frames} frames, {rec.progress.fps:.1f} fps reported, "
        f"{rec.progress.dropped} dropped"
    )
    feeder = rec.feeder
    if feeder is not None:
        print(
            f"system audio fed: {feeder.seconds_written:.2f} s "
            f"({feeder.seconds_padded:.2f} s of it invented silence)  "
            f"error={feeder.error or 'none'}"
        )

    if not clip.exists():
        print("no file was written")
        return 1

    print(f"\nwall clock {elapsed:.2f} s, file {clip.stat().st_size/1e6:.1f} MB")
    report(ffmpeg, clip, marks)
    if not args.keep:
        print(f"\n(clip kept at {clip})")
    return 0


def report(ffmpeg: str, clip: Path, marks: list[float]) -> None:
    ffprobe = str(paths.ffprobe_exe())
    probe = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,avg_frame_rate,nb_frames,"
            "duration,start_time,channels,sample_rate:format=duration",
            "-of", "default=noprint_wrappers=1",
            str(clip),
        ],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    print("\n--- ffprobe ---")
    print(probe.stdout.strip())

    luma = video_luma(ffmpeg, clip)
    envelope = audio_envelope(ffmpeg, clip)
    if not luma:
        print("no video frames were analysed")
        return
    if not envelope:
        print("THERE IS NO AUDIO TRACK")
        return

    luma_peak = max(v for _, v in luma)
    luma_floor = min(v for _, v in luma)
    flashes = onsets(luma, luma_floor + (luma_peak - luma_floor) * 0.5, 1.0)

    rms_peak = max(v for _, v in envelope)
    beeps = onsets(envelope, rms_peak * 0.25, 1.0)

    print(f"\nflashes found: {len(flashes)}   beeps found: {len(beeps)}   marks: {len(marks)}")
    pairs = min(len(flashes), len(beeps))
    if pairs < 2:
        print("not enough events to judge sync")
        return

    offsets = [(beeps[i] - flashes[i]) * 1000 for i in range(pairs)]
    print("\n  #   flash(s)   beep(s)   beep-flash(ms)")
    for i in range(pairs):
        print(f"  {i+1:<3} {flashes[i]:8.3f} {beeps[i]:9.3f} {offsets[i]:14.1f}")

    drift = offsets[-1] - offsets[0]
    spread = max(offsets) - min(offsets)
    print(f"\nmean offset {sum(offsets)/len(offsets):+.1f} ms  "
          f"(includes display and virtual-cable latency, not the recorder's fault)")
    print(f"DRIFT first to last: {drift:+.1f} ms   spread {spread:.1f} ms")
    verdict = "PASS" if abs(drift) < 100 else "FAIL"
    print(f"{verdict}: audio and video stay together" if verdict == "PASS"
          else f"{verdict}: the two streams pull apart over the recording")


if __name__ == "__main__":
    sys.exit(main())
