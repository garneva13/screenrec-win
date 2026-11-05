# ScreenRec

A small Windows screen recorder. Recording only — no editor, no timeline, no export
presets. It captures a monitor, a dragged-out region or a single window to MP4, with the
microphone and the system sound, and stops on a global hotkey.

Written because the free recorder it replaces would not install, and because the half of
that program that mattered was the capture.

## Running it

```
ScreenRec.cmd              start it
ScreenRec-console.cmd      same, but keeps a console so tracebacks are visible
```

Both use the project's own `.venv`. Nothing is installed system-wide and `ffmpeg` is
bundled under `tools\ffmpeg`.

## What it records

| | |
|---|---|
| Source | whole monitor, a region you drag out, or one window |
| Audio | microphone (DirectShow) and/or system sound (WASAPI loopback), mixed or as two tracks |
| Cursor | drawn or not; clicks optionally get a fading ring |
| Encoder | four measured profiles: H.265, H.265 4:4:4 for sharp text, H.264, x264 |
| Output | MP4 in `%USERPROFILE%\Videos\ScreenRec`, named by a strftime pattern |
| Source | …or **every screen at once**: one file per monitor, 0 ms apart |
| Hotkey | system-wide, default `Ctrl+Alt+R` |
| Floating bar | a small always-on-top control that moves off the recorded screen |

## How it works

`ffmpeg` is driven as a subprocess. Video comes from the `ddagrab` filter, which is the
Desktop Duplication API. In the common case — an unrotated screen in 4:2:0 — frames go
straight from `ddagrab` into NVENC without ever being copied to system memory. A rotated
screen or a 4:4:4 recording has to come down through `hwdownload`, because this build has
no GPU filtering path at all; measured, that still holds 58.9 fps at 2560×1440. Cropping
to a region or a window is done by `ddagrab` itself either way.

System sound is the part ffmpeg cannot do on Windows: mainline ffmpeg has no WASAPI
loopback input. ScreenRec captures it with PortAudio (`pyaudiowpatch`) and feeds raw PCM
to ffmpeg down a **named pipe** — which leaves ffmpeg's stdin free to receive `q`, and `q`
is what makes ffmpeg write the MP4 trailer instead of leaving an unplayable file behind.

The click highlight is drawn in a transparent, click-through window on the real desktop.
The Desktop Duplication API captures the composed desktop, so the ring appears in the
recording without the capture pipeline knowing anything about it.

## Quality and size

The four formats are not guesses. `tools/benchmark_encoders.py` puts a window of
scrolling coloured text on screen, captures it losslessly in 4:4:4, encodes that same
clip with each candidate and compares SSIM **per plane**. The headline:

> **4:2:0 caps colour fidelity at about 0.888 whatever you spend on it.** Sixty-seven
> megabytes a minute buys none of it back, because the subsampling discarded the colour
> before the encoder ever saw it. H.265 4:4:4 reaches 0.998 at a quarter of the size.

So the window offers the trade-off by name rather than a codec list, and shows what a
minute will cost for the screen you are about to capture. The full table, and the three
ways this benchmark lied before it was fixed, are in `docs/HANDOVER.md`.

Two hardware facts, both measured: `av1_nvenc` is listed by this ffmpeg but the RTX 3070
Ti cannot use it (Ampere has no AV1 encoder), and 4:4:4 cannot stay on the GPU - there is
no d3d11-to-4:4:4 conversion, so it goes through system memory. It still holds 58.9 fps
at 2560x1440.

## Measuring, not hoping

`tools\measure_sync.py` flashes a square and plays a tone at the same instants, records
both, and then finds in the finished file when each actually happened. The number that
matters is the **drift** — the offset must be the same at the end of a long recording as
at the start. The tone is played into VB-Audio Virtual Cable, so running the test is
silent.

```
.venv\Scripts\python.exe tools\measure_sync.py --seconds 40
```

## Tests

```
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

No GPU, no recording and no visible window is needed for any of them.

## Layout

```
app\screenrec\      paths, winapi, config, audio, recorder, hotkeys, clicks, overlay
app\screenrec\ui\   theme, island, main_window
app\run.pyw         entry point
tools\ffmpeg\       bundled ffmpeg.exe and ffprobe.exe
tools\measure_sync.py           audio/video drift
tools\measure_audio_latency.py  what the audio path alone costs
tools\benchmark_encoders.py     size against quality, per encoder
tools\static_review.py          sweep for silent failures and ctypes mistakes
docs\CONTRACT.md    the module contract and the measured facts behind the design
docs\HANDOVER.md    state, what is verified, what is not
```

## Licence

**GNU General Public License v3** - see `LICENSE`.

Not a preference but a consequence, and it is worth saying which: this recorder has to
work on a PC with no hardware encoder at all, and the only encoder that always exists is
x264 - which is GPL, and which makes the bundled ffmpeg GPL with it. `docs/ENCODERS.md`
has the measurements behind that.

## Third party

Exact versions, licences and where the source lives are in `THIRD-PARTY-NOTICES.md`.

* **FFmpeg** - BtbN Windows build, **GPL v3** (`--enable-gpl --enable-version3`).
  Bundled unmodified under `tools\ffmpeg` with its own licence, and run as a separate
  program: a command line and a pipe, nothing linked.
* **PySide6** and **shiboken6** - LGPL v3 or GPL v2 or GPL v3; taken here under GPL v3.
* **PyAudioWPatch** - **Apache-2.0**, a PortAudio fork that adds WASAPI loopback.
  This line said MIT until 2026-11-05 and was wrong.
* **comtypes** - MIT.
