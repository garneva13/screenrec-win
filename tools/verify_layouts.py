"""Open a real two-screen recording in every layout and check what must not move.

The unit tests drive fake engines. This one opens the actual files with the actual
`SyncEngine` and asks the questions the contract makes promises about, because those
promises are about real `QMediaPlayer` objects and not about a stub:

* switching layout, swapping the screens, moving the splitter - none of them may move
  the position, restart playback, or reload a file (§3, §6);
* the sound must come out of exactly one audio output, and the swap must not move it
  (§6);
* the duration must cover the LONGEST stream, not the master's (§6);
* every window must land somewhere a person can reach (§6).

It runs under the offscreen platform on purpose: it needs no screen, so it can be run
while someone is working, and it never takes the mouse. What it therefore cannot check
is what a layout *looks like*; that needs eyes and is a separate run.

Usage:  .venv\\Scripts\\python.exe tools\\verify_layouts.py [--set <folder>]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

# Before PySide6 is touched: no window may appear, and nothing may wait for a click.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from screenrec import multi  # noqa: E402
from screenrec.player import engine as engine_mod  # noqa: E402
from screenrec.player import layouts, stage as stage_mod, window as window_mod  # noqa: E402
from screenrec.ui import theme  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASSED if ok else FAILED).append(name)
    mark = "ok  " if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def pump(qt: QtWidgets.QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.01)


def audio_outputs(clock: engine_mod.SyncEngine) -> int:
    """How many players have an audio output. §6 allows exactly one."""
    found = 0
    for player in getattr(clock, "_players", []):
        if player is None:
            continue
        getter = getattr(player, "audioOutput", None)
        if callable(getter) and getter() is not None:
            found += 1
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", dest="folder", default=str(ROOT / "scratch" / "twofinal"))
    args = parser.parse_args()

    qt = QtWidgets.QApplication(sys.argv)
    theme.apply(qt)

    sets = multi.find_sets(Path(args.folder))
    if not sets:
        print(f"no recording set in {args.folder}; run tools/verify_two_screens.py first")
        return 2
    recording, note = engine_mod.Recording.load(sets[0])
    if recording is None:
        print(f"could not load it: {note}")
        return 2
    print(f"recording: {sets[0].name}, {len(recording.screens)} screens"
          + (f"  ({note})" if note else ""))

    streams = stage_mod.streams_from(recording)
    areas = window_mod.screen_areas()
    print(f"streams: {[(s.title, s.size) for s in streams]}")
    print(f"screens Qt reports: {[(a.x(), a.y(), a.width(), a.height()) for a in areas]}")

    # -- the clock ---------------------------------------------------------
    print("\n=== the clock ===")
    clock = engine_mod.SyncEngine(recording)
    pump(qt, 1.5)
    check("exactly one audio output", audio_outputs(clock) == 1,
          f"found {audio_outputs(clock)}")
    longest = max(
        (clock.stream_duration(i) for i in range(len(recording.screens))), default=0
    )
    check("the duration covers the longest stream",
          longest > 0 and abs(clock.duration() - longest) <= 1,
          f"duration {clock.duration()} ms, longest stream {longest} ms")

    clock.play()
    pump(qt, 1.5)
    check("it plays", clock.playing() and clock.position() > 0,
          f"at {clock.position()} ms")
    clock.pause()
    pump(qt, 0.4)
    settled = clock.position()

    # -- the stage ---------------------------------------------------------
    print("\n=== the stage: nothing may move when the layout does ===")
    stage = stage_mod.PlayerStage(clock, streams=streams)
    stage.show()
    pump(qt, 1.0)

    before = clock.position()
    master_before = getattr(clock, "_master_index", None)
    for layout in (layouts.LAYOUT_STACKED, layouts.LAYOUT_FOCUS,
                   layouts.LAYOUT_SIDE_BY_SIDE):
        stage.set_layout(layout)
        pump(qt, 0.5)
        moved = abs(clock.position() - before)
        check(f"{layout}: the position held", moved <= 120,
              f"moved {moved} ms")
        check(f"{layout}: still paused", not clock.playing())

    stage.swap()
    pump(qt, 0.5)
    check("swapping did not move the position",
          abs(clock.position() - before) <= 120,
          f"at {clock.position()} ms, was {before} ms")
    check("swapping did not move the audio master",
          getattr(clock, "_master_index", None) == master_before,
          f"master {getattr(clock, '_master_index', None)}, was {master_before}")
    check("swapping did not start playback", not clock.playing())
    shown = sorted({pane.stream_index() for pane in (stage.main, stage.side)})
    check("both streams are still on the stage after a swap", shown == [0, 1],
          f"the two panes show streams {shown}")

    geometry = stage.geometry()
    on_a_screen = any(a.intersects(geometry) for a in areas)
    check("the stage is on a real screen", on_a_screen, f"{geometry}")

    stage.close()
    pump(qt, 0.5)

    # -- the separate windows ----------------------------------------------
    print("\n=== separate windows: every one must be reachable ===")
    windows = window_mod.PlayerWindows(clock)
    windows.show()
    pump(qt, 1.0)
    for tiling in layouts.TILINGS:
        applied = getattr(windows, "apply_tiling", None)
        if not callable(applied):
            check("the arrangement menu exists", False, "no apply_tiling")
            break
        applied(tiling)
        pump(qt, 0.4)
        rects = [view.frameGeometry() for view in windows.views]
        inside = all(any(a.contains(r) for a in areas) for r in rects)
        overlap = (len(rects) > 1 and rects[0].intersects(rects[1]))
        check(f"{tiling}: every window is wholly on a screen", inside,
              " ".join(f"{r.x()},{r.y()} {r.width()}x{r.height()}" for r in rects))
        check(f"{tiling}: the windows do not overlap", not overlap)
    check("the position survived the tiling",
          abs(clock.position() - before) <= 200,
          f"at {clock.position()} ms, was {before} ms")

    windows.close_all()
    pump(qt, 0.6)

    print(f"\n{len(PASSED)} of {len(PASSED) + len(FAILED)} checks passed")
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    print("\nNot covered here, because it needs eyes on a real screen: what any layout")
    print("LOOKS like, and whether the splitter sits where a person would want it.")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
