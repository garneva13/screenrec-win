"""Record something real and check that the machine's own MB/min is written down.

`config.PROFILES`' per-minute figures were measured on NVENC, on this author's card, on
deliberately busy content. On anyone else's machine they are somebody else's numbers, and
for Media Foundation they were measured to be out by up to five times. So a finished
recording is supposed to file what it really cost, and the estimate is supposed to prefer
that.

All of that was built on 2026-11-04 in two halves by two people, and the join between
them was missing: nothing ever filled the fields, so every estimate still came from the
table and `measured` was always False. Nothing in the unit tests could see it, because
each half was tested against the other half's stub. This records for real and reads the
answer back out.

It writes into a temporary store, never the user's own.

Usage:  .venv\\Scripts\\python.exe tools\\verify_sizes.py [--seconds 8]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from screenrec import capabilities, config, paths, recorder, storage, winapi  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    ffmpeg = str(paths.ffmpeg_exe())
    monitors = winapi.list_monitors()
    flat = next((m for m in monitors if not m.rotated), monitors[0])
    profile = config.PROFILES[config.DEFAULT_PROFILE]
    results = capabilities.probe(ffmpeg)
    encoder, reason = capabilities.choose(results, profile)
    print(f"recording with {encoder} ({reason})")

    folder = Path(tempfile.mkdtemp(prefix="screenrec_sizes_"))
    store = folder / "sizes.json"
    # `takes.finish` files its measurement into the real store, because that is its
    # job. This is a harness and must leave the user's own figures exactly as it found
    # them, so the real one is moved aside for the duration and put back at the end.
    real = storage.sizes_path()
    kept = None
    if real.is_file():
        kept = real.with_suffix(".json.verifying")
        shutil.move(str(real), str(kept))
        print(f"  the real store is held aside as {kept.name} while this runs")
    final = folder / "take.mp4"
    level = "balanced"

    def plan_for(segment: Path) -> recorder.RecordingPlan:
        return recorder.RecordingPlan(
            sources=[recorder.VideoSource(monitor=flat)],
            outputs=[segment],
            encoder=encoder,
            pix_fmt=profile.pix_fmt,
            encoder_extra=profile.extra,
            quality=profile.cq[level],
            quality_level=level,
            framerate=60,
            draw_mouse=True,
            layout=recorder.LAYOUT_SINGLE,
        )

    before = storage.size_estimate(
        profile.key, level, flat.width, flat.height, 60,
        encoder=encoder, store=store,
    )
    check("with nothing recorded yet the estimate comes from the table",
          not before.measured and before.mb_per_minute > 0,
          f"{before.mb_per_minute:.1f} MB/min, measured={before.measured}")

    session = recorder.RecordingSession(ffmpeg, final, plan_for)
    problem = session.start()
    if problem:
        print(f"  the recording would not start: {problem}")
        return 2
    began = time.monotonic()
    while time.monotonic() - began < args.seconds:
        time.sleep(0.2)
    check("the take knows what is writing it", session.take.encoder == encoder,
          f"take.encoder={session.take.encoder!r}, quality={session.take.quality!r}, "
          f"{session.take.width}x{session.take.height}@{session.take.framerate}")

    path, problem = session.stop()
    check("it produced a file", path is not None and path.is_file(),
          f"{path}" + (f"; {problem}" if problem else ""))
    if path is None or not path.is_file():
        shutil.rmtree(folder, ignore_errors=True)
        return 1
    megabytes = path.stat().st_size / 1e6
    print(f"  {megabytes:.1f} MB in about {args.seconds:.0f} s")

    # `takes.finish` files the measurement into the real store, so this run is asked to
    # file it into the temporary one as well - the same call, the same arguments.
    note = storage.remember_recording(
        encoder=session.take.encoder,
        quality=session.take.quality,
        pix_fmt=session.take.pix_fmt,
        size_bytes=path.stat().st_size,
        seconds=args.seconds,
        width=session.take.width,
        height=session.take.height,
        framerate=session.take.framerate,
        store=store,
    )
    check("the measurement was accepted", not note, note or "")
    check("the store exists", store.is_file(), str(store))
    if store.is_file():
        body = json.loads(store.read_text(encoding="utf-8"))
        check("it is keyed on the encoder, not the profile",
              any(encoder in key for key in body.get("samples", body)),
              f"keys: {list(body.get('samples', body))[:3]}")

    after = storage.size_estimate(
        profile.key, level, flat.width, flat.height, 60,
        encoder=encoder, store=store,
    )
    check("the estimate now prefers this machine's own figure", after.measured,
          f"{after.mb_per_minute:.1f} MB/min from {after.samples} recording(s), "
          f"was {before.mb_per_minute:.1f} from the table")
    check("and the figure is the one just measured",
          abs(after.mb_per_minute - megabytes / args.seconds * 60) < 1.0,
          f"{after.mb_per_minute:.1f} against {megabytes / args.seconds * 60:.1f}")

    shutil.rmtree(folder, ignore_errors=True)
    if kept is not None:
        if real.is_file():
            real.unlink()
        shutil.move(str(kept), str(real))
        print("  the real store is back as it was")
    elif real.is_file():
        # Nothing was there before and `takes.finish` has just made one: this harness's
        # own recording is not the user's, so it does not get to teach their estimate.
        real.unlink()
        print("  removed the store this harness's own recording created")
    print(f"\n{len(PASSED)} of {len(PASSED) + len(FAILED)} checks passed")
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
