"""Can this machine record both screens at once, and in what shape?

The first attempt at this measured 13.8 and 11.5 fps and was written off as "the GA104
has one NVENC chip". That measurement was taken with the heavy encoder tuning which was
later measured to cost 14% of the frames on a *single* screen and was removed. Designing
stage 2 around a stale number would be the easy mistake, so everything is measured again
with the settings the application actually ships.

Six shapes are tried, because the answer decides the architecture:

* one ffmpeg, two ddagrab inputs, two files - the shape that gives a common clock;
* the same at 30 fps, and at a cheaper quality, to see what the GPU is short of;
* two separate ffmpeg processes, one per screen - more encoder sessions, no common clock;
* side by side and picture in picture, which composite on the CPU into one file.

What is reported for each: the frame rate each file actually achieved, how far the two
files' lengths differ, and the size.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from screenrec import paths, recorder, winapi  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
OUT = ROOT / "scratch" / "twoscreen"


@dataclass
class Clip:
    path: Path
    frames: int
    duration: float
    megabytes: float

    @property
    def fps(self) -> float:
        return self.frames / self.duration if self.duration else 0.0


def inspect(path: Path) -> Clip | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    result = subprocess.run(
        [str(paths.ffprobe_exe()), "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries",
         "stream=nb_read_frames,duration", "-of", "default=noprint_wrappers=1",
         str(path)],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    try:
        return Clip(
            path=path,
            frames=int(fields["nb_read_frames"]),
            duration=float(fields["duration"]),
            megabytes=path.stat().st_size / 1e6,
        )
    except (KeyError, ValueError):
        return None


def run_plan(plan: recorder.RecordingPlan, seconds: float) -> list[Clip]:
    rec = recorder.Recorder(str(paths.ffmpeg_exe()))
    problem = rec.start(plan)
    if problem:
        print(f"    could not start: {problem}")
        return []
    time.sleep(seconds)
    stop_problem = rec.stop()
    if stop_problem:
        print(f"    {stop_problem}")
    tail = rec.log_text().strip().splitlines()[-2:]
    for line in tail:
        if line.strip():
            print(f"    ffmpeg: {line[:110]}")
    return [c for c in (inspect(p) for p in plan.outputs) if c is not None]


def run_two_processes(sources, seconds: float, framerate: int) -> list[Clip]:
    """One ffmpeg per screen. More encoder sessions, but no shared clock."""
    recorders, outputs = [], []
    for index, source in enumerate(sources):
        target = OUT / f"separate_процесс_{index}.mp4".replace("процесс", "process")
        plan = recorder.RecordingPlan(
            sources=[source], outputs=[target], framerate=framerate,
            draw_mouse=False, crash_safe=False,
        )
        rec = recorder.Recorder(str(paths.ffmpeg_exe()))
        problem = rec.start(plan)
        if problem:
            print(f"    screen {index} could not start: {problem}")
            continue
        recorders.append(rec)
        outputs.append(target)
    if not recorders:
        return []
    time.sleep(seconds)
    for rec in recorders:
        rec.stop()
    return [c for c in (inspect(p) for p in outputs) if c is not None]


def report(name: str, clips: list[Clip], wanted: int) -> None:
    if not clips:
        print(f"  {name:<34} NOTHING RECORDED")
        return
    rates = ", ".join(f"{c.fps:.1f}" for c in clips)
    sizes = sum(c.megabytes for c in clips)
    worst = min(c.fps for c in clips)
    spread = (max(c.duration for c in clips) - min(c.duration for c in clips)
              if len(clips) > 1 else 0.0)
    verdict = "holds" if worst >= wanted * 0.9 else (
        "degraded" if worst >= wanted * 0.6 else "FAILS"
    )
    print(f"  {name:<34} {rates:>14} fps   {sizes:5.1f} MB   "
          f"lengths differ by {spread * 1000:5.0f} ms   {verdict}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=12.0)
    args = parser.parse_args()

    winapi.enable_dpi_awareness()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.mp4"):
        old.unlink()

    monitors = winapi.list_monitors()
    if len(monitors) < 2:
        print("This needs two monitors.")
        return 2
    sources = [recorder.VideoSource(monitor=m, name=f"mon{m.index}") for m in monitors]
    print("screens: " + ", ".join(m.label for m in monitors))
    print(f"each run is {args.seconds:.0f} s\n")

    print("shape                                       fps achieved")
    print("-" * 104)

    # 1. one process, two inputs, two files, at 60
    clips = run_plan(
        recorder.RecordingPlan(
            sources=sources,
            outputs=[OUT / "one_proc_60_a.mp4", OUT / "one_proc_60_b.mp4"],
            layout=recorder.LAYOUT_SEPARATE, framerate=60, draw_mouse=False,
            crash_safe=False,
        ),
        args.seconds,
    )
    report("one process, two files, 60 fps", clips, 60)

    # 2. the same at 30
    clips = run_plan(
        recorder.RecordingPlan(
            sources=sources,
            outputs=[OUT / "one_proc_30_a.mp4", OUT / "one_proc_30_b.mp4"],
            layout=recorder.LAYOUT_SEPARATE, framerate=30, draw_mouse=False,
            crash_safe=False,
        ),
        args.seconds,
    )
    report("one process, two files, 30 fps", clips, 30)

    # 3. 60 fps at a cheaper quality - is it the encoder or the capture?
    clips = run_plan(
        recorder.RecordingPlan(
            sources=sources,
            outputs=[OUT / "one_proc_cheap_a.mp4", OUT / "one_proc_cheap_b.mp4"],
            layout=recorder.LAYOUT_SEPARATE, framerate=60, quality=30,
            draw_mouse=False, crash_safe=False,
        ),
        args.seconds,
    )
    report("one process, 60 fps, cheap quality", clips, 60)

    # 4. H.264 instead of H.265 - a lighter encode
    clips = run_plan(
        recorder.RecordingPlan(
            sources=sources,
            outputs=[OUT / "one_proc_h264_a.mp4", OUT / "one_proc_h264_b.mp4"],
            layout=recorder.LAYOUT_SEPARATE, framerate=60, encoder="h264_nvenc",
            draw_mouse=False, crash_safe=False,
        ),
        args.seconds,
    )
    report("one process, 60 fps, H.264", clips, 60)

    # 5. two processes
    clips = run_two_processes(sources, args.seconds, 60)
    report("two processes, 60 fps", clips, 60)

    # 6. composited into one file
    clips = run_plan(
        recorder.RecordingPlan(
            sources=sources, outputs=[OUT / "side_by_side.mp4"],
            layout=recorder.LAYOUT_SIDE_BY_SIDE, framerate=60, draw_mouse=False,
            crash_safe=False,
        ),
        args.seconds,
    )
    report("side by side, one file, 60 fps", clips, 60)

    print()
    print("A single screen on this machine measures 59.8 fps, for comparison.")
    print("'lengths differ by' is how far apart the two files end: with one process")
    print("they share a clock, with two they do not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
