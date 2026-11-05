"""Open a real multi-screen recording and check the two files really play as one.

The engine is tested headlessly with stub players; this uses the actual recording made
by `verify_two_screens.py` and real media, because "two QMediaPlayer instances stay in
step" is a claim about Windows and not about our arithmetic.

Nothing is left on screen: the views are shown only long enough to play, and closed.

    .venv\\Scripts\\python.exe tools\\verify_player.py [--seconds 20]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6 import QtWidgets  # noqa: E402

from screenrec import multi  # noqa: E402
from screenrec.player import engine as engine_mod  # noqa: E402
from screenrec.ui import theme  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, text: str) -> bool:
    RESULTS.append((ok, text))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {text}")
    return ok


def pump(app: QtWidgets.QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument(
        "--folder", default=str(ROOT / "scratch" / "twofinal"),
        help="where the recording set is",
    )
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    theme.apply(app)

    folder = Path(args.folder)
    sets = multi.find_sets(folder)
    if not sets:
        print(f"No recording set in {folder}. Run tools/verify_two_screens.py first.")
        return 2
    sidecar = sets[0]
    print(f"opening {sidecar.name}")

    recording, problem = engine_mod.Recording.load(sidecar)
    check(recording is not None, f"the set loads ({problem or 'no problem'})")
    if recording is None:
        return 1
    check(len(recording.screens) >= 2, f"{len(recording.screens)} screens in the set")
    check(
        recording.audio_file.name == recording.screens[0].file.name,
        "exactly one file carries the sound, and it is the first",
    )

    rects = recording.layout_rects(1.0)
    check(len(rects) == len(recording.screens), "a rectangle for every screen")
    if len(rects) >= 2:
        order_in_recording = [s.desktop[0] for s in recording.screens]
        order_in_layout = [r[0] for r in rects]
        check(
            sorted(range(len(order_in_recording)), key=lambda i: order_in_recording[i])
            == sorted(range(len(order_in_layout)), key=lambda i: order_in_layout[i]),
            "a screen that was on the right is laid out on the right",
        )

    engine = engine_mod.SyncEngine(recording)
    problems: list[str] = []
    engine.problem.connect(problems.append)
    pump(app, 3.0)

    duration = engine.duration()
    check(duration > 1000, f"the recording has a length ({duration / 1000:.2f}s)")
    check(not problems, f"nothing failed to load ({'; '.join(problems) or 'clean'})")

    engine.play()
    pump(app, 1.0)
    check(engine.playing(), "it plays")

    worst = 0
    samples = 0
    began = time.monotonic()
    while time.monotonic() - began < args.seconds:
        pump(app, 0.2)
        drifts = engine.drift()
        if drifts:
            worst = max(worst, max(abs(d) for d in drifts))
            samples += 1
        if engine.position() >= duration - 500:
            break
    print(f"      {samples} samples, worst drift {worst} ms")
    check(worst <= 120, f"the screens stay together while playing ({worst} ms worst)")

    engine.pause()
    pump(app, 0.4)
    check(not engine.playing(), "it pauses")
    at_pause = engine.position()
    pump(app, 1.0)
    check(abs(engine.position() - at_pause) < 80, "paused really means paused")

    target = max(0, duration // 2)
    engine.seek(target)
    pump(app, 1.2)
    landed = engine.position()
    check(abs(landed - target) < 600,
          f"a seek lands where it was told ({landed} against {target})")
    drifts = engine.drift()
    check(
        not drifts or max(abs(d) for d in drifts) <= 200,
        f"the screens are still together after a seek "
        f"({max((abs(d) for d in drifts), default=0)} ms)",
    )

    engine.stop()
    pump(app, 0.5)
    check(not engine.playing(), "it stops")

    failures = [text for ok, text in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failures)} of {len(RESULTS)} checks passed")
    for text in failures:
        print(f"  FAILED: {text}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
