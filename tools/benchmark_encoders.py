"""Choose the encoder settings by measuring them, not by repeating folklore.

Screen content is not film. It is mostly still, it has hard edges, and it is full of
small coloured text on flat backgrounds. What ruins it is not blockiness but chroma
subsampling smearing red and blue glyphs - so the settings worth having are not a film's,
and the only way to know is to measure quality against size on content like the real
thing, from this machine, with this ffmpeg.

Method:

* A window of representative content is put on screen (`tools/screen_content.py`:
  scrolling code-like text in several colours, a sliding panel, a gradient) and captured
  **losslessly in 4:4:4** as the reference. An idle desktop is useless here - the first
  run of this benchmark captured a still screen, produced a 1 MB reference, and scored
  every candidate within 0.003 of every other.
* Each candidate then encodes *that same file*, so every one of them sees identical
  frames. Re-capturing per candidate would compare different content.
* SSIM is reported **per plane**. "All" is dominated by luma, and luma is not where 4:2:0
  hurts; the damage is in U and V, which is exactly what smears small red text.
* `-cq` is swept, so the families can be compared at equal quality rather than at one
  arbitrary setting each - which is the mistake the second run of this made.

Usage:
    .venv\\Scripts\\python.exe tools\\benchmark_encoders.py [--seconds 8] [--live]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from screenrec import paths, winapi  # noqa: E402

CREATE_NO_WINDOW = 0x08000000
SCRATCH = ROOT / "scratch" / "bench"


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )


#: Common to every NVENC candidate: look ahead so rate control can plan, adaptive
#: quantisation so flat areas do not steal bits from text, B-frames as references, and a
#: real two-pass. All cheap on a GPU encoder, all off by default in ffmpeg.
NVENC_TUNING = [
    "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-b:v", "0",
    "-bf", "3", "-b_ref_mode", "middle", "-rc-lookahead", "32",
    "-spatial-aq", "1", "-aq-strength", "8", "-temporal-aq", "1",
    "-multipass", "fullres", "-g", "120",
]

#: The cheap settings: whatever the preset decides, and nothing else. Measured to be the
#: only ones that hold 60 fps on a live 2560x1440 capture.
NVENC_PLAIN = ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-b:v", "0", "-g", "120"]

FAMILIES: list[tuple[str, str, str, list[str], list[str]]] = [
    ("hevc 420 plain", "hevc_nvenc", "yuv420p", [], NVENC_PLAIN),
    ("hevc 420 tuned", "hevc_nvenc", "yuv420p", [], NVENC_TUNING),
    ("hevc 444 plain", "hevc_nvenc", "yuv444p", ["-profile:v", "rext"], NVENC_PLAIN),
    ("hevc 444 tuned", "hevc_nvenc", "yuv444p", ["-profile:v", "rext"], NVENC_TUNING),
    ("h264 420 plain", "h264_nvenc", "yuv420p", [], NVENC_PLAIN),
    ("h264 444 plain", "h264_nvenc", "yuv444p", ["-profile:v", "high444p"], NVENC_PLAIN),
]

CQ_SWEEP = [16, 19, 22, 25, 28, 31]


def capture_reference(ffmpeg: str, seconds: float, live: bool) -> Path:
    """Lossless 4:4:4 capture of representative content."""
    reference = SCRATCH / "reference.mkv"
    monitor = winapi.list_monitors()[0]
    content = None

    if live:
        width, height = 1280, 720
        x = monitor.x + max(0, (monitor.width - width) // 2)
        y = monitor.y + max(0, (monitor.height - height) // 2)
        print(f"capturing the live desktop, {width}x{height} for {seconds:.0f} s")
    else:
        content = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "screen_content.py"),
             "--seconds", str(seconds + 6), "--monitor", str(monitor.index)],
            stdout=subprocess.PIPE, text=True, creationflags=CREATE_NO_WINDOW,
        )
        geometry = (content.stdout.readline() if content.stdout else "").split()
        if len(geometry) != 4:
            print("  the content window did not report its geometry")
            content.terminate()
            sys.exit(1)
        x, y, width, height = (int(value) for value in geometry)
        time.sleep(1.2)  # let it start animating before the clock starts
        print(f"capturing the content window, {width}x{height} for {seconds:.0f} s")

    args = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"ddagrab=output_idx={monitor.index}:framerate=60:draw_mouse=0:"
              f"offset_x={x - monitor.x}:offset_y={y - monitor.y}:"
              f"video_size={width}x{height}",
        "-t", str(seconds),
        "-vf", "hwdownload,format=bgra,format=yuv444p",
        "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast",
        "-y", str(reference),
    ]
    result = run(args)
    if content is not None:
        content.terminate()
        try:
            content.wait(timeout=5)
        except subprocess.TimeoutExpired:
            content.kill()
    if result.returncode != 0 or not reference.is_file():
        print("  reference capture failed:", result.stderr.strip()[:400])
        sys.exit(1)
    megabytes = reference.stat().st_size / 1e6
    print(f"  reference: {megabytes:.0f} MB "
          f"({megabytes * 60 / seconds:.0f} MB/min lossless)")
    return reference


def ssim_of(ffmpeg: str, candidate: Path, reference: Path) -> dict[str, float]:
    """SSIM per plane against the 4:4:4 reference.

    Two things have to be forced, and leaving either out makes the whole table lie:

    * **Both sides to yuv444p explicitly.** Left alone, ffmpeg converts the reference
      into the candidate's own format, so a 4:2:0 candidate is compared against a 4:2:0
      reference and its chroma loss disappears - which is why an earlier run had 4:2:0
      scoring *better* on U and V than 4:4:4.
    * **Both sides re-indexed by frame number** (`setpts=N/TB`). B-frames give the
      encoded file a presentation offset, the ssim filter pairs frames by timestamp, and
      a one-frame slip against scrolling text swamps every difference being measured -
      which is why Y came out as 0.94615 at every single quality setting.
    """
    chain = (
        "[0:v]format=yuv444p,setpts=N/TB[a];"
        "[1:v]format=yuv444p,setpts=N/TB[b];"
        "[a][b]ssim"
    )
    result = run([
        ffmpeg, "-hide_banner", "-loglevel", "info",
        "-i", str(candidate), "-i", str(reference),
        "-lavfi", chain, "-f", "null", "-",
    ])
    text = result.stdout + result.stderr
    found: dict[str, float] = {}
    for plane in ("Y", "U", "V", "All"):
        match = re.search(plane + r":([0-9.]+)", text)
        if match:
            found[plane] = float(match.group(1))
    return found


def encode(ffmpeg: str, reference: Path, name: str, encoder: str, pix_fmt: str,
           extra: list[str], tuning: list[str], cq: int) -> tuple[Path, float, float] | None:
    stem = name.replace(" ", "_").replace(":", "")
    out = SCRATCH / f"{stem}_cq{cq}.mp4"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(reference),
        "-vf", "format=" + pix_fmt, "-c:v", encoder, *tuning, *extra,
        "-cq", str(cq), "-an", "-y", str(out),
    ]
    began = time.monotonic()
    result = run(command)
    took = time.monotonic() - began
    if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        reason = (result.stderr.strip().splitlines() or ["no output"])[-1]
        print(f"{name:<12} {cq:>4}   FAILED - {reason[:60]}")
        return None
    return out, out.stat().st_size / 1e6, took


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--live", action="store_true",
                        help="capture the real desktop instead of the test content")
    args = parser.parse_args()

    winapi.enable_dpi_awareness()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    ffmpeg = str(paths.ffmpeg_exe())

    reference = capture_reference(ffmpeg, args.seconds, args.live)
    per_minute = 60 / args.seconds

    header = (
        f"{'family':<12} {'cq':>4} {'MB':>7} {'MB/min':>8} "
        f"{'SSIM Y':>9} {'SSIM U':>9} {'SSIM V':>9} {'enc s':>6}"
    )
    print()
    print(header)
    print("-" * len(header))

    results: dict[str, list[dict]] = {}
    for name, encoder, pix_fmt, extra, tuning in FAMILIES:
        rows: list[dict] = []
        for cq in CQ_SWEEP:
            made = encode(ffmpeg, reference, name, encoder, pix_fmt, extra,
                          tuning, cq)
            if made is None:
                continue
            out, size_mb, took = made
            ssim = ssim_of(ffmpeg, out, reference)
            rows.append({"cq": cq, "mb": size_mb, "ssim": ssim})
            print(
                f"{name:<12} {cq:>4} {size_mb:>7.2f} {size_mb * per_minute:>8.1f} "
                f"{ssim.get('Y', 0):>9.5f} {ssim.get('U', 0):>9.5f} "
                f"{ssim.get('V', 0):>9.5f} {took:>6.1f}"
            )
            out.unlink(missing_ok=True)
        results[name] = rows
        print()

    target = 0.9990
    print(f"At the first setting of each family reaching SSIM Y >= {target}:")
    print(f"  {'family':<12} {'cq':>4} {'MB/min':>8} {'SSIM U':>9} {'SSIM V':>9}")
    for name, rows in results.items():
        hit = next((row for row in rows if row["ssim"].get("Y", 0) >= target), None)
        if hit is None:
            best = max(rows, key=lambda r: r["ssim"].get("Y", 0)) if rows else None
            if best is None:
                print(f"  {name:<12}  never built")
            else:
                print(f"  {name:<12}  never reached it; best Y "
                      f"{best['ssim'].get('Y', 0):.5f} at cq {best['cq']}")
            continue
        print(f"  {name:<12} {hit['cq']:>4} {hit['mb'] * per_minute:>8.1f} "
              f"{hit['ssim'].get('U', 0):>9.5f} {hit['ssim'].get('V', 0):>9.5f}")

    print()
    print("These clips are 1280x720; multiply by about 4 for 2560x1440.")
    print("av1_nvenc is listed by this ffmpeg, but Ampere (RTX 3070 Ti) has no AV1")
    print("encoder - measured, it fails with 'Nothing was written into output file'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
