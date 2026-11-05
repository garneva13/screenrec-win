"""Open real video files as a pair, slide one against the other, and save the set.

`docs/PLAYER_SOURCES.md` makes promises about files the recorder never wrote: two
cameras, two independent `.mp4`s, no index, and a start time that differs by whatever
it differs by. The unit tests drive stubs, and a stub seeks instantly and knows its
length at once - which is exactly the part real media gets wrong. So this harness asks
the questions on the actual files:

* two loose videos open as one recording, and one video still opens alone (§1);
* a video gains a second one and the position and the paused state survive the one
  rebuild the player is allowed (§1.1);
* an offset does not move the timeline, and it does move the stream (§2.1, §2.2);
* a stream whose content has not started shows as waiting rather than as a frozen
  first frame (§2.1);
* the duration follows the offsets (§2.1);
* a set saved and reopened brings back its files, its audio choice and its offsets,
  including a file in another folder (§1.3);
* **a recorder-made set still behaves exactly as it did** (§3's regression).

Offscreen on purpose: it needs no screen and never takes the mouse, so it can be run
while someone is working. What it therefore cannot check is what any of it looks like.

Usage:  .venv\\Scripts\\python.exe tools\\verify_sources.py [--set <folder>]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

# Before PySide6 is touched: no window may appear, and nothing may wait for a click.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from screenrec import multi  # noqa: E402
from screenrec.player import engine as engine_mod  # noqa: E402
from screenrec.player import open as open_mod  # noqa: E402
from screenrec.player import stage as stage_mod  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

#: A real seek on real media lands on a keyframe and takes time. Anything inside this
#: is "did not move"; anything outside it is a seek nobody asked for.
SLACK_MS = 150


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


def settle(qt: QtWidgets.QApplication, clock: engine_mod.SyncEngine,
           seconds: float = 2.0) -> None:
    """Wait for every seek in flight, but no longer than `seconds`."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if not clock.seeking():
            # One more slice: `seeking()` goes false before the last frame arrives.
            pump(qt, 0.25)
            return
        time.sleep(0.01)


def inner_position(clock: engine_mod.SyncEngine, index: int) -> int:
    """Where the stream's own playhead is - not the timeline. §2.1's left-hand side."""
    players = getattr(clock, "_players", [])
    if index >= len(players) or players[index] is None:
        return -1
    return int(players[index].position())


def audio_outputs(clock: engine_mod.SyncEngine) -> int:
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
    parser.add_argument("--set", dest="folder",
                        default=str(ROOT / "scratch" / "twofinal"))
    args = parser.parse_args()

    qt = QtWidgets.QApplication(sys.argv)

    folder = Path(args.folder)
    videos = sorted(folder.glob("*.mp4"))
    sets = multi.find_sets(folder)
    if len(videos) < 2 or not sets:
        print(f"need a two-screen recording in {folder}; "
              "run tools/verify_two_screens.py first")
        return 2
    first, second = videos[0], videos[1]
    print(f"files: {first.name}, {second.name}")

    scratch = Path(tempfile.mkdtemp(prefix="screenrec_sources_"))

    # -- §1: two loose files, and one ------------------------------------
    print("\n=== §1: what opens ===")
    pair, note = open_mod.recording_from_files([first, second])
    check("two loose videos open as one recording",
          pair is not None and len(pair.screens) == 2,
          f"{len(pair.screens) if pair else 0} screens"
          + (f"; {note}" if note else ""))
    if pair is not None:
        sizes = [tuple(s.size) for s in pair.screens]
        check("each file keeps its own frame size",
              all(w > 0 and h > 0 for w, h in sizes) and sizes[0] != sizes[1],
              f"{sizes}")
        check("the pair has a believable frame rate",
              1.0 <= float(pair.framerate) <= 1000.0, f"{pair.framerate} fps")

    alone, note = open_mod.recording_from_files([first])
    check("one video still opens alone",
          alone is not None and len(alone.screens) == 1,
          f"{len(alone.screens) if alone else 0} screens")

    broken, note = open_mod.recording_from_files([first, folder / "not_here.mp4"])
    check("a missing file is named and the rest still opens",
          broken is not None and len(broken.screens) == 1 and "not_here" in note,
          f"note: {note!r}")

    nothing, note = open_mod.recording_from_files([folder / "not_here.mp4"])
    check("nothing openable is refused in a sentence",
          nothing is None and bool(note.strip()), f"note: {note!r}")

    rows = open_mod.scan(folder)
    check("the library lists this folder",
          any(row.is_set for row in rows) and len(rows) >= 1,
          f"{len(rows)} rows, newest {rows[0].path.name if rows else '-'}")

    # -- the clock over a hand-made pair ----------------------------------
    print("\n=== the clock over two cameras ===")
    if pair is None:
        print("  (no pair; the rest cannot run)")
        return 1
    clock = engine_mod.SyncEngine(pair)
    settle(qt, clock, 3.0)
    check("exactly one audio output", audio_outputs(clock) == 1,
          f"found {audio_outputs(clock)}")
    duration_at_rest = clock.duration()
    check("it knows its length", duration_at_rest > 0, f"{duration_at_rest} ms")

    clock.seek(4000)
    settle(qt, clock)
    clock.pause()
    pump(qt, 0.4)
    before = clock.position()
    check("it seeks where it is told", abs(before - 4000) <= 400, f"at {before} ms")

    # -- §2: the offset ----------------------------------------------------
    print("\n=== §2: sliding one against the other ===")
    inner_before = inner_position(clock, 1)
    clock.set_offset(1, -1480)
    settle(qt, clock)
    moved = abs(clock.position() - before)
    check("a negative offset does not move the timeline", moved <= SLACK_MS,
          f"at {clock.position()} ms, was {before} ms")
    inner_after = inner_position(clock, 1)
    check("a negative offset does move the stream",
          abs((inner_after - inner_before) + 1480) <= SLACK_MS,
          f"stream 2 went {inner_before} -> {inner_after} ms")
    check("the stream sits at timeline + offset",
          abs(inner_after - (clock.position() - 1480)) <= SLACK_MS,
          f"{inner_after} ms against {clock.position() - 1480} ms")

    clock.set_offset(1, 2000)
    settle(qt, clock)
    check("a positive offset does not move the timeline either",
          abs(clock.position() - before) <= SLACK_MS,
          f"at {clock.position()} ms, was {before} ms")
    check("a positive offset winds the stream forward",
          abs(inner_position(clock, 1) - (clock.position() + 2000)) <= SLACK_MS,
          f"stream 2 at {inner_position(clock, 1)} ms")

    only_first = clock.stream_duration(0)
    second_span = clock.stream_duration(1) - 2000
    check("the duration follows the offsets",
          abs(clock.duration() - max(only_first, second_span)) <= 50,
          f"duration {clock.duration()} ms, spans {only_first} and {second_span} ms")

    clock.set_offset(1, 0)
    settle(qt, clock)
    check("reset does not move the timeline", abs(clock.position() - before) <= SLACK_MS,
          f"at {clock.position()} ms")
    check("reset puts the length back",
          abs(clock.duration() - duration_at_rest) <= 50,
          f"{clock.duration()} ms, was {duration_at_rest} ms")

    # §2.1: before its content starts, a stream has nothing to show.
    clock.set_offset(1, -3000)
    clock.seek(0)
    settle(qt, clock)
    check("a stream whose content has not started is waiting",
          clock.stream_is_waiting(1) and not clock.stream_is_waiting(0),
          f"stream 2 waiting={clock.stream_is_waiting(1)}, "
          f"stream 1 waiting={clock.stream_is_waiting(0)}")
    clock.seek(5000)
    settle(qt, clock)
    check("past that point it is not waiting any more",
          not clock.stream_is_waiting(1),
          f"at {clock.position()} ms, stream 2 at {inner_position(clock, 1)} ms")

    # §2.2: live while playing, and the timeline still must not jump.
    clock.set_offset(1, 0)
    settle(qt, clock)
    clock.play()
    pump(qt, 1.2)
    running = clock.position()
    clock.set_offset(1, -900)
    pump(qt, 0.8)
    onward = clock.position()
    check("adjusting while playing does not restart the timeline",
          clock.playing() and onward > running
          and (onward - running) < 2500,
          f"{running} -> {onward} ms, playing={clock.playing()}")
    clock.pause()
    pump(qt, 0.4)
    clock.set_offset(1, 0)
    settle(qt, clock)

    # -- §1.3: saving and reopening ----------------------------------------
    print("\n=== §1.3: saving the set ===")
    away = scratch / "another folder"
    away.mkdir(parents=True, exist_ok=True)
    moved_file = away / second.name
    shutil.copy2(second, moved_file)

    written, note = open_mod.save_set(
        [first, moved_file], audio_index=1, offsets=[0, -1480],
        target=scratch / "pair.screenrec.json")
    check("the set is written", written is not None and written.exists(),
          f"{written}" + (f"; {note}" if note else ""))
    if written is not None:
        body = json.loads(written.read_text(encoding="utf-8"))
        check("it is a version 2 index", body.get("version") == 2,
              f"version {body.get('version')}")
        spellings = [s.get("file") for s in body.get("screens", [])]
        check("a file in another folder is written as an absolute path",
              any(Path(str(s)).is_absolute() for s in spellings), f"{spellings}")

        back, note = engine_mod.Recording.load(written)
        check("it reopens as the same pair",
              back is not None and len(back.screens) == 2,
              f"{len(back.screens) if back else 0} screens"
              + (f"; {note}" if note else ""))
        if back is not None:
            check("the offsets came back",
                  [s.offset_ms for s in back.screens] == [0, -1480],
                  f"{[s.offset_ms for s in back.screens]}")
            check("the audio choice came back", back.master_index() == 1,
                  f"audio stream {back.master_index()}")
            check("both files were found where the index says",
                  all(Path(s.file).exists() for s in back.screens),
                  f"{[str(s.file) for s in back.screens]}")

    # A file that has moved away since: named, and the rest still opens.
    moved_file.unlink()
    hurt, note = engine_mod.Recording.load(written) if written else (None, "")
    check("a file gone since is named and what is left still opens",
          hurt is not None and second.name in note,
          f"note: {note!r}")

    # -- §1.1: the one rebuild ---------------------------------------------
    print("\n=== §1.1: adding the second video ===")
    one, _note = open_mod.recording_from_files([first])
    solo = engine_mod.SyncEngine(one)
    settle(qt, solo, 3.0)
    solo.seek(5000)
    settle(qt, solo)
    solo.pause()
    pump(qt, 0.5)
    place = solo.position()
    stage = stage_mod.PlayerStage(solo, streams=None)
    stage.show()
    pump(qt, 0.8)
    clocks: list[object] = []
    stage.clock_changed.connect(clocks.append)
    added = stage.add_video(second)
    pump(qt, 3.0)
    grown = clocks[-1] if clocks else solo
    check("the second video was added", bool(added) and len(clocks) == 1,
          f"added={added}, {len(clocks)} new clocks")
    check("the recording now has two screens",
          len(getattr(grown, "recording").screens) == 2,
          f"{len(getattr(grown, 'recording').screens)} screens")
    carried = grown.position()
    check("the position was carried over the rebuild",
          abs(carried - place) <= 600, f"at {carried} ms, was {place} ms")
    check("it is still paused", not grown.playing())
    stage.close()
    pump(qt, 0.5)

    # -- §3: the regression that matters most -------------------------------
    print("\n=== §3: a recorder-made set, unchanged ===")
    made, note = engine_mod.Recording.load(sets[0])
    check("the recorder's own index still opens",
          made is not None and len(made.screens) == 2,
          f"{len(made.screens) if made else 0} screens"
          + (f"; {note}" if note else ""))
    if made is not None:
        check("a version 1 index reads every offset as zero",
              [s.offset_ms for s in made.screens] == [0, 0],
              f"{[s.offset_ms for s in made.screens]}")
        plain = engine_mod.SyncEngine(made)
        settle(qt, plain, 3.0)
        check("it has one audio output", audio_outputs(plain) == 1,
              f"found {audio_outputs(plain)}")
        longest = max(plain.stream_duration(i) for i in range(len(made.screens)))
        check("its duration still covers the longest stream",
              abs(plain.duration() - longest) <= 1,
              f"duration {plain.duration()} ms, longest {longest} ms")
        check("neither stream is waiting",
              not any(plain.stream_is_waiting(i) for i in range(2)))
        plain.play()
        pump(qt, 1.5)
        check("it plays", plain.playing() and plain.position() > 0,
              f"at {plain.position()} ms")
        plain.pause()
        pump(qt, 0.3)

    shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n{len(PASSED)} of {len(PASSED) + len(FAILED)} checks passed")
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    print("\nNot covered here, because it needs eyes: whether two clapperboards")
    print("actually line up on screen once the offset says they do.")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
