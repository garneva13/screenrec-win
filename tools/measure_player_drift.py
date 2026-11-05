"""Can two QMediaPlayer instances stay in step, and for how long?

The player for a multi-screen recording has to run two files as if they were one. The
question is not whether Qt can play two videos - it obviously can - but how far apart
they wander, and whether nudging one back costs a visible stutter.

So: play the same file twice, read both positions on a timer, and report the drift.
Then try the correction that the player would use - a seek only when the gap is worth
correcting - and see whether it holds.

Nothing is shown: both players render to nothing, because position is what is being
measured and a visible window would only compete for the GPU.

    .venv\\Scripts\\python.exe tools\\measure_player_drift.py [--seconds 60]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6 import QtCore, QtMultimedia, QtWidgets  # noqa: E402

from screenrec import paths  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
#: Below this a correction would cost more than the error. One frame at 60 fps is 16 ms.
TOLERANCE_MS = 40


def make_clip(seconds: int) -> Path:
    """A clip with a visible counter, so a human can check the result too."""
    target = ROOT / "scratch" / "playertest.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return target
    subprocess.run(
        [str(paths.ffmpeg_exe()), "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=60:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-y", str(target)],
        capture_output=True, creationflags=CREATE_NO_WINDOW,
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--correct", action="store_true",
                        help="nudge the follower back when it drifts")
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    clip = make_clip(int(args.seconds) + 20)
    if not clip.is_file():
        print("the test clip could not be made")
        return 1

    players, sinks, outputs = [], [], []
    for _ in range(2):
        sink = QtMultimedia.QVideoSink()
        player = QtMultimedia.QMediaPlayer()
        audio = QtMultimedia.QAudioOutput()
        audio.setMuted(True)
        player.setAudioOutput(audio)
        player.setVideoSink(sink)
        player.setSource(QtCore.QUrl.fromLocalFile(str(clip.resolve())))
        players.append(player)
        sinks.append(sink)
        outputs.append(audio)

    ready = time.monotonic() + 6
    while time.monotonic() < ready:
        if all(
            p.mediaStatus() in (
                QtMultimedia.QMediaPlayer.MediaStatus.LoadedMedia,
                QtMultimedia.QMediaPlayer.MediaStatus.BufferedMedia,
            )
            for p in players
        ):
            break
        app.processEvents()
        time.sleep(0.02)

    for player in players:
        player.play()

    print(f"playing two copies for {args.seconds:.0f} s"
          f"{' with correction' if args.correct else ', uncorrected'}")
    print("  elapsed    leader    follower      drift")

    began = time.monotonic()
    samples: list[float] = []
    corrections = 0
    next_report = 5.0
    while time.monotonic() - began < args.seconds:
        app.processEvents()
        time.sleep(0.02)
        elapsed = time.monotonic() - began
        leader = players[0].position()
        follower = players[1].position()
        if leader <= 0:
            continue
        drift = follower - leader
        samples.append(drift)
        if args.correct and abs(drift) > TOLERANCE_MS:
            players[1].setPosition(leader)
            corrections += 1
        if elapsed >= next_report:
            next_report += 5.0
            print(f"  {elapsed:6.1f}s  {leader / 1000:7.2f}s  {follower / 1000:8.2f}s  "
                  f"{drift:+6.0f} ms")

    for player in players:
        player.stop()

    if not samples:
        print("\nneither player reported a position; QtMultimedia may have no backend")
        return 1
    worst = max(abs(value) for value in samples)
    final = samples[-1]
    print(f"\n  samples {len(samples)}   worst drift {worst:.0f} ms   "
          f"drift at the end {final:+.0f} ms   corrections {corrections}")
    if worst <= TOLERANCE_MS:
        print("  -> two players stay together on their own; no correction needed")
    elif args.correct and worst <= TOLERANCE_MS * 3:
        print("  -> correction keeps them within a couple of frames")
    else:
        print("  -> they wander; the player needs a master clock, not two free runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
