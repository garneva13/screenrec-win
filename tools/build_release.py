"""Build the pair of programs, ready to be installed.

    .venv\\Scripts\\python.exe tools\\build_release.py

Four steps, each of which was done by hand once and is here so it is done the same way
every time:

1. the icons, rendered from the application's own drawing code (`make_icons.py`);
2. PyInstaller, from `ScreenRec.spec` - two executables sharing one folder;
3. ffmpeg copied in afterwards rather than analysed, and the licences beside it;
4. a report of what came out, because "it built" is not the same as "it is right".

It does **not** make the installer: that is `packaging/ScreenRec.iss` and Inno Setup,
and it is a separate step so that this one can be run on a machine that has no Inno
Setup to check that the programs themselves still build.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "ScreenRec"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

#: Shipped beside the programs. The first two are what the GPL asks of anyone who
#: passes the thing on; the third is how a person finds out what they have.
PAPERS = ("LICENSE", "THIRD-PARTY-NOTICES.md", "README.md")

CREATE_NO_WINDOW = 0x08000000


def run(label: str, args: list[str]) -> None:
    began = time.monotonic()
    print(f"  {label} ...", flush=True)
    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"{label} failed with exit code {result.returncode}")
    print(f"  {label}: {time.monotonic() - began:.0f} s")


def megabytes(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1e6


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-build", action="store_true",
                        help="leave PyInstaller's work folder for inspection")
    args = parser.parse_args()

    if not PYTHON.is_file():
        raise SystemExit(f"the project venv is not where it should be: {PYTHON}")

    print("building ScreenRec\n")
    run("icons", [str(PYTHON), str(ROOT / "tools" / "make_icons.py")])
    run("PyInstaller", [str(PYTHON), "-m", "PyInstaller", str(ROOT / "ScreenRec.spec"),
                        "--noconfirm", "--log-level", "WARN"])

    ffmpeg_from = ROOT / "tools" / "ffmpeg"
    ffmpeg_to = DIST / "tools" / "ffmpeg"
    ffmpeg_to.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in ffmpeg_from.iterdir():
        if item.is_file():
            shutil.copy2(item, ffmpeg_to / item.name)
            copied += 1
    print(f"  ffmpeg: {copied} files, {megabytes(ffmpeg_to):.0f} MB")

    for name in PAPERS:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, DIST / name)
    print(f"  papers: {', '.join(PAPERS)}")

    if not args.keep_build:
        shutil.rmtree(ROOT / "build" / "ScreenRec", ignore_errors=True)
        shutil.rmtree(ROOT / "build" / "ScreenRecPlayer", ignore_errors=True)

    print("\nwhat came out:")
    problems: list[str] = []
    for name in ("ScreenRec.exe", "ScreenRecPlayer.exe"):
        exe = DIST / name
        if exe.is_file():
            print(f"  {name:<22} {exe.stat().st_size / 1e6:>6.1f} MB")
        else:
            problems.append(f"{name} was not built")
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if not (ffmpeg_to / name).is_file():
            problems.append(f"tools/ffmpeg/{name} is missing")
    for name in PAPERS:
        if not (DIST / name).is_file():
            problems.append(f"{name} is missing")

    files = sum(1 for f in DIST.rglob("*") if f.is_file())
    print(f"  {'everything':<22} {megabytes(DIST):>6.0f} MB in {files} files")
    print(f"  {'in':<22} {DIST}")

    if problems:
        print("\nnot right yet:")
        for line in problems:
            print("  " + line)
        return 1
    print("\nNow run it before believing it: `dist\\ScreenRec\\ScreenRec.exe` and\n"
          "`dist\\ScreenRec\\ScreenRecPlayer.exe` - a build that starts is not the same\n"
          "as a build that records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
