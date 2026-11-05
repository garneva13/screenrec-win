"""How much of the measured A/V offset is the audio path, and not the recorder.

`measure_sync.py` reports a constant offset between a flash and the tone that was played
with it. That offset is not an error in the file: it is the time the sound spends getting
from a write call, through WASAPI, through the virtual cable, and back into a loopback
capture - and none of that is the recorder's doing.

This isolates it. The same tone track is played and captured at the same time, with no
recording and no ffmpeg in the way, and the delay between writing a burst and seeing it
is measured directly. Subtract this from measure_sync's offset and what remains is the
recorder's own contribution, give or take the display pipeline.

Silent: it plays into VB-Audio Virtual Cable and listens to that cable's loopback.

Usage:  .venv\\Scripts\\python.exe tools\\measure_audio_latency.py
"""
from __future__ import annotations

import array
import math
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

from measure_sync import build_tone_track, find_loopback_device, find_render_device  # noqa: E402
from screenrec import audio as audio_mod  # noqa: E402

RATE = audio_mod.SAMPLE_RATE
SECONDS = 14.0
MARKS = [2.0, 6.0, 10.0]


def main() -> int:
    import pyaudiowpatch as pyaudio

    render = find_render_device("CABLE Input")
    loopback = find_loopback_device("CABLE Input")
    if render is None or loopback is None:
        print("VB-Audio Virtual Cable not found; this test would be audible. Stopping.")
        return 2
    print(f"playing into device {render}, listening on loopback {loopback}")

    tone = build_tone_track(SECONDS, MARKS)
    handle = pyaudio.PyAudio()

    captured = array.array("h")
    capture_started = threading.Event()
    stop = threading.Event()
    start_time = [0.0]

    def capture() -> None:
        stream = handle.open(
            format=pyaudio.paInt16, channels=2, rate=RATE,
            frames_per_buffer=480, input=True, input_device_index=loopback,
        )
        try:
            # Drain anything already buffered, then declare t=0.
            deadline = time.monotonic() + 0.4
            while time.monotonic() < deadline:
                if stream.get_read_available() >= 480:
                    stream.read(480, exception_on_overflow=False)
            start_time[0] = time.monotonic()
            capture_started.set()
            while not stop.is_set():
                if stream.get_read_available() >= 480:
                    block = array.array("h")
                    block.frombytes(stream.read(480, exception_on_overflow=False))
                    captured.extend(block)
                else:
                    time.sleep(0.001)
        finally:
            stream.stop_stream()
            stream.close()

    reader = threading.Thread(target=capture, name="capture", daemon=True)
    reader.start()
    capture_started.wait(timeout=5)

    play_offset = time.monotonic() - start_time[0]
    stream = handle.open(
        format=pyaudio.paInt16, channels=2, rate=RATE,
        output=True, output_device_index=render, frames_per_buffer=480,
    )
    block = 480 * 2 * 2
    for offset in range(0, len(tone), block):
        stream.write(tone[offset : offset + block])
    stream.stop_stream()
    stream.close()

    time.sleep(0.5)
    stop.set()
    reader.join(timeout=3)
    handle.terminate()

    frames = len(captured) // 2
    print(f"captured {frames / RATE:.2f} s; playback began {play_offset * 1000:.0f} ms "
          f"after the capture clock started")

    # Short-window RMS, then the first crossing of each burst.
    step = int(RATE * 0.002)
    envelope = []
    for start in range(0, frames - step, step):
        total = 0
        for i in range(start * 2, (start + step) * 2, 2):
            total += captured[i] * captured[i]
        envelope.append((start / RATE, math.sqrt(total / step)))
    if not envelope:
        print("nothing was captured")
        return 1

    peak = max(v for _, v in envelope)
    if peak < 200:
        print(f"the tone never arrived (peak RMS {peak:.0f}); is the cable the default?")
        return 1
    threshold = peak * 0.25

    found: list[float] = []
    armed = True
    for when, value in envelope:
        if value >= threshold:
            if armed and (not found or when - found[-1] > 1.0):
                found.append(when)
            armed = False
        else:
            armed = True

    print(f"\nbursts written at: {', '.join(f'{m:.3f}' for m in MARKS)} (playback clock)")
    print(f"bursts heard at  : {', '.join(f'{f:.3f}' for f in found)} (capture clock)\n")

    pairs = min(len(found), len(MARKS))
    if pairs == 0:
        print("no bursts were detected")
        return 1
    delays = [(found[i] - (MARKS[i] + play_offset)) * 1000 for i in range(pairs)]
    for i, delay in enumerate(delays):
        print(f"  burst {i + 1}: {delay:7.1f} ms")
    mean = sum(delays) / len(delays)
    print(f"\nAUDIO PATH LATENCY: {mean:.1f} ms "
          f"(write -> WASAPI -> virtual cable -> loopback capture)")
    print("Subtract this from measure_sync.py's offset: what is left is the recorder's "
          "own A/V offset, plus however long the desktop takes to show a frame.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
