# ScreenRec — handover

Started and built 2026-11-03. A Windows screen recorder, **capture only** — no editor, no
timeline, no export presets. Replaces OpenScreen 1.5.0, which would not install.

**Start here.** What is verified is marked so; what is not is marked too — this
document's job is to be honest about the difference rather than to look finished.

---

## 1. State

| | |
|---|---|
| Runs | `ScreenRec.cmd` (or `ScreenRec-console.cmd` to see tracebacks) |
| Python | 3.13.5 in the project's `.venv`, PySide6 6.12, pyaudiowpatch 0.2.12.9, comtypes |
| ffmpeg | bundled, `tools\ffmpeg` — BtbN gpl, N-128352-gec887b13a8-20261103 |
| Tests | 2105, counted 2026-11-09, `PYTHONPATH=app python -m unittest discover -s tests -q` — all pass, 2 skipped (they want the bundled ffmpeg/ffprobe) |
| Static sweep | `python tools\static_review.py` — clean |
| Translations | `python tools\check_translations.py` — every translatable string has Russian |
| Languages | English and Russian, switched in Settings, live |
| Git | **not a repository, nothing committed** |

### Works, and was measured working

* Capture of a whole monitor, a dragged region, or one window.
* The rotated (portrait) monitor, including cropping on it.
* Microphone and system sound, together or separately, mixed or as two tracks.
* The global hotkey, the click highlight, the floating bar.
* A 45 s recording with sound: **0.0 ms drift**, see §3.
* Pause and resume into one file — segment-and-join with `-c copy`, 0.03 s for three
  segments. This entry used to say pause was impossible; it was wrong.
* Every monitor recorded at once, one file per screen plus a `.screenrec.json` index,
  and a player that plays them in step — `docs/MULTIMONITOR.md`. Two ffmpeg processes
  beat one on both frame rate and sync; the screens start **17 ms apart**, one frame.
* Drawing over the screen while recording — `docs/ANNOTATE_CONTRACT.md`. The drawings
  are in the file and the toolbar is not.
* The thin red capture frame, on every recorded screen including a whole monitor, on
  screen and absent from the file: **15 984 outline pixels in the widget, 0 in the
  recording** — `tools\verify_capture_frame.py`.
* Five ways of showing a two-screen recording — `docs/PLAYER_LAYOUTS.md`.
  `tools\verify_layouts.py` opens a real two-screen take and checks the promises that
  matter: **23 of 23**, the position moving **0 ms** across every layout change and
  every swap, exactly one audio output, the duration covering the longest stream.
  Picture in picture was one of six and was **removed on 2026-11-04** at the user's
  word: «Рядом» does the same thing and has a splitter as well. With it gone, one
  monitor is advised `side_by_side` (0.530) and the real two-monitor desk
  `one_per_monitor` (1.000); the formula was not re-tuned to make that happen.
* Opening your own videos, and lining two cameras up by hand —
  `docs/PLAYER_SOURCES.md`. Any two files open as a pair, a lone video can be given a
  second one, and each stream has an offset in milliseconds.
  `tools\verify_sources.py` drives real `.mp4`s: **39 of 39**, including the offset
  moving the stream and **not** the timeline (4000 ms before and after, the stream at
  exactly `timeline + offset`), a stream whose content has not started reporting
  itself as waiting, a set saved with an absolute path and reopened with its files,
  its audio choice and its offsets, and the second video added to a lone one with the
  position carried over the rebuild (5000 ms, still paused).

* Choosing an encoder on a machine nobody has seen — `docs/ENCODERS.md`. Until
  2026-11-04 `_video_codec_args` knew exactly two encoder names and silently turned
  every other into `-c:v libx264` while the frames stayed on the GPU, so nine of the
  eleven probe candidates built a command ffmpeg could not run: every AMD machine, every
  Intel machine, and every RTX 40/50, where `av1_nvenc` passes the probe and sorted
  first. `app/screenrec/encoders.py` is now the one table both the probe and the recorder
  read; an unknown name raises. **Media Foundation** (`h264_mf`, `hevc_mf`) was added as
  a vendor-neutral hardware path: it takes `ddagrab`'s d3d11 surface directly and lets
  Windows dispatch to whatever MFT the machine has, measured at 59.3 fps and 5% of one
  core, the same as native NVENC. On this machine all five working families now record
  through the real builder. A review on 2026-11-04 rebuilt the real commands and
  reported the default profile's byte-identical to the one before the change, with two
  profiles moved - both of them the documented bug going away, because the old override
  wrote H.265 for "Most compatible — H.264" and NVENC for "No GPU — x264". That
  comparison was read, not re-run here.

  A second pass the same day moved the probe onto the real `ddagrab` pipeline, turned
  `Profile.encoder` into `Profile.codec`, and wrote the line that says in plain words
  which path is in use. **`docs/ENCODERS.md` §6 is the honest scoreboard for it** —
  including the two items that did not land (the cache key still ignores the GPU and the
  driver) and the parts that three reviewers found unreachable in the running program.
  The self-measuring size estimate (`storage.size_estimate`, `takes.measure`) is
  built and tested and **half wired**: the panel's estimate line and the free-space check
  ask `storage.size_estimate`, but `recorder.RecordingSession` does not tell its
  `takes.Take` which encoder wrote the file, so no recording is ever measured, nothing is
  ever stored, and what comes back is still `config.estimate_mb_per_minute`'s NVENC
  table.

### Not done, deliberately

* Any editing. Out of scope from the start.
* Joining the two screens into one file. A layout is a way of watching; the sources stay
  sources — `docs/PLAYER_LAYOUTS.md` §8, which also says plainly what an editor cannot
  read out of the sidecar.
* Fill-and-crop in the player. Letterboxing only, as §3 of that contract requires.

### Not verified

* **Almost nothing of the player's new interface has been seen on a real screen.** The
  chooser dialog has been opened and looked at. The stage, the arrangement menu, the
  library, the sync panel and the audio-source chip are covered by tests and by
  headless runs against real files; nobody has watched them yet.
* **Whether two clapperboards actually line up** once the offset says they do. The
  arithmetic is checked on real files; the eye is not.
* `tools\ui_probe.py` **is not reliable while someone is at the machine.** It drives
  the real cursor with `SendInput`, so the user's own mouse fights it: two runs minutes
  apart failed on different checks (8, then 5) and even counted different totals (35,
  then 32). Every check that failed lives in `ui/sources.py`, `ui/settings_panel.py`,
  `ui/annotations.py` or `ui/bar.py` — none of which has been touched since the run
  that reported 35 of 35. Run it on an idle machine or not at all.
* **Playback in the user's own player.** The default is H.265 4:2:0 and the HEVC Video
  Extensions package is installed on this machine, so Windows' player should open it —
  but that was read from the package list, not watched. VLC and mpv are **not installed
  here**, which is why 4:4:4 is not the default (see §4).
* The click highlight was proven click-through and proven to be captured by DDA, but
  nobody has yet recorded a real session and watched the rings back.
* Long recordings. The longest measured is 45 s.
* **Scaled displays.** Both screens here run at 100%; the 125/150/200% path has never
  executed.
* **More than two screens**, and a monitor physically unplugged mid-playback. The
  display-removal path is only ever driven by a simulated `screenRemoved`.

---

## 2. How it is put together

```
app\screenrec\
  paths.py      where ffmpeg, settings and output live
  winapi.py     DXGI monitors, window list, DPI awareness
  config.py     Settings + the measured encoding profiles
  audio.py      dshow mic list, WASAPI loopback feeder over a named pipe
  recorder.py   the ffmpeg command builder (N sources) and the process
  hotkeys.py    RegisterHotKey on its own thread
  clicks.py     WH_MOUSE_LL hook on its own thread
  overlay.py    region selector + click highlight
  ui\theme.py   the dark look, painted glyphs
  ui\island.py  the floating control bar
  ui\main_window.py
tools\
  measure_sync.py          A/V drift, by flashing and beeping together
  measure_audio_latency.py what the audio path alone costs
  benchmark_encoders.py    size vs quality, per encoder
  screen_content.py        the test content the benchmark encodes
  static_review.py         the sweep for silent failures and ctypes mistakes
```

Three design decisions worth not undoing:

1. **System sound goes down a named pipe, not stdin.** stdin is reserved for sending `q`,
   and `q` is what makes ffmpeg write the MP4 trailer. Kill the process instead and the
   file has no moov atom and nothing will play it. Both were measured.
2. **The click highlight is a real window on the real desktop.** DDA captures the composed
   desktop, so the ring lands in the recording without the ffmpeg side knowing anything
   about it. Measured: `WindowFromPoint` over the overlay returns the window underneath,
   and a captured frame contains the ring.
3. **The loopback feeder paces itself against a wall clock** and pads gaps with silence.
   Raw PCM has no timestamps, so a dropped block is not silence in the file — it does not
   exist, and everything after it slides earlier. Before this was added a 40 s recording
   lost 1.6 s of audio timeline.

---

## 3. Sound and picture: what was measured

`tools\measure_sync.py` flashes a square white and plays a tone at the same instants,
records both, and finds each in the finished file. 45 s, nine marks:

```
  #   flash(s)   beep(s)   beep-flash(ms)
  1      2.417     2.555          138.3
  ...        ...       ...           ...
  9     42.417    42.555          138.3

DRIFT first to last: +0.0 ms   spread 0.0 ms
```

Identical to the millisecond, nine times over 45 seconds. **There is no drift.**

The constant 138.3 ms is not an error in the file. `tools\measure_audio_latency.py`
measured the audio path on its own — write, WASAPI, virtual cable, loopback capture —
at **199.9 ms**. The recording's offset is *smaller* than the path it measured, so the
recorder adds no delay; if anything the sound sits slightly ahead of the picture, by
somewhere between 0 and 60 ms depending on how long the desktop takes to show a frame.
**That last number was not measured** and cannot be without a camera pointed at the
screen. It is inside the broadcast tolerance either way.

`Settings.audio_offset_ms` exists as a lever (applied with `-itsoffset`) and is **0**,
because the measurement said it was not needed. It has no control in the window on
purpose.

Other numbers from real recordings:

* 60 fps requested, **59.8–60.0 fps delivered**, 2 dropped frames in 2445.
* The audio track ends **0.1–0.4 s before** the video. That is the tail in flight when
  `q` arrives; it does not grow with length and it is not drift.
* An early sync run reported 28 fps. That was the measurement competing with five
  agents for the CPU, not the recorder — plain ffmpeg on the same region holds 59.9 fps.

---

## 4. Encoding: the measurements behind the four profiles

`tools\benchmark_encoders.py` puts a window of scrolling coloured text on screen
(`tools\screen_content.py`), captures it **losslessly in 4:4:4**, then encodes that same
clip with every candidate and compares SSIM per plane. 1280×720, 60 fps, busy content:

| family | cq | MB/min | SSIM Y | SSIM U | SSIM V |
|---|---|---|---|---|---|
| h264 4:2:0 | 16 | 67.2 | 0.99994 | **0.8886** | 0.8907 |
| h264 4:2:0 | 22 | 31.9 | 0.99979 | 0.8824 | 0.8844 |
| h264 4:4:4 | 22 | 22.3 | 0.99976 | 0.9969 | 0.9966 |
| hevc 4:2:0 | 22 | 27.8 | 0.99991 | 0.8845 | 0.8863 |
| **hevc 4:4:4** | **22** | **15.7** | 0.99990 | **0.9978** | 0.9976 |
| hevc 4:4:4 | 28 | 11.1 | 0.99965 | 0.9900 | 0.9891 |

The finding that matters: **4:2:0 caps chroma fidelity at about 0.888 no matter what you
spend.** 67 MB a minute buys nothing back — the subsampling threw the colour away before
the encoder saw it. That cap is what makes small red and blue text look fringed.
4:4:4 reaches 0.998 at *a quarter of the size*.

Three traps this benchmark fell into before it told the truth, all worth knowing:

* The first run captured an idle desktop: a 1 MB reference and every candidate within
  0.003 of every other. Screen content must be moving to discriminate.
* ffmpeg's `ssim` filter converts the reference into the candidate's format, so 4:2:0 was
  being compared against a 4:2:0 reference and its chroma loss vanished. Both sides are
  now forced to `yuv444p`.
* B-frames give the encoded file a presentation offset, the filter pairs frames by
  timestamp, and a one-frame slip against scrolling text swamped everything — SSIM Y came
  out as 0.94615 at *every* quality setting. Both sides are now re-indexed with
  `setpts=N/TB`.

### The tuning that was built, measured, and thrown away

The obvious "modern quality" set went in first: `-preset p6 -bf 3 -b_ref_mode middle
-rc-lookahead 32 -spatial-aq 1 -temporal-aq 1 -multipass fullres`. Then it was measured
on a **live** 2560x1440 capture at 60 fps, which the file-to-file benchmark above cannot
see because it has no real-time constraint:

| settings | fps | size, 20 s |
|---|---|---|
| plain `-preset p5 -tune hq -rc vbr` | **59.7** | 3.1 MB |
| tuned p6, `-multipass fullres` | 51.1 | 3.7 MB |
| tuned p6, `-multipass qres` | 54.1 | 4.2 MB |
| tuned p5, no multipass | 54.7 | 4.8 MB |

It cost **14% of the frames and produced a larger file**, and on the 4:2:0 benchmark its
quality was indistinguishable (SSIM Y 0.99991 against 0.99981; chroma marginally worse).
On the 4:4:4 path it was nearly free in frames (59.3 against 59.7) but still larger on a
quiet screen. A screen recorder must not drop frames to buy nothing, so it is gone.

`encoders.NVENC_SETTINGS` is now `-preset p5 -tune hq -rc vbr -b:v 0`, plus `-cq` from
the quality level and `-g` at two seconds. Through the whole app - island, click
highlight and system audio all on - that measures **59.8 fps at 2560x1440**, against
51.4 with the tuning.

The real gains are the codec and the chroma format, and those are large. They are what
`config.PROFILES` chooses.

### What the app ships with

Four profiles in `config.PROFILES`, each a named trade-off, with the measured MB/min
shown live in the window and scaled to the capture size:

* **Standard — H.265 4:2:0** (default). Plays here: the HEVC Video Extensions package is
  installed.
* **Sharp text — H.265 4:4:4.** Best colour *and* smallest. Not the default only because
  Windows' player cannot decode 4:4:4 and this machine has no VLC or mpv.
* **Most compatible — H.264 4:2:0.**
* **No GPU — x264.**

Two hardware facts, both measured, both easy to get wrong from memory:

* **`av1_nvenc` is listed by this ffmpeg but the RTX 3070 Ti cannot use it.** Ampere has
  no AV1 encoder; the run fails with "Nothing was written into output file".
* **4:4:4 cannot stay on the GPU.** `-pix_fmt yuv444p` straight off `ddagrab` fails with
  "Impossible to convert between the formats supported by the filter 'Parsed_null_0' and
  the filter 'auto_scale_0'" — there is no hardware path from the d3d11 surface to a
  4:4:4 plane. It goes through `hwdownload`, and that still holds full resolution:
  2560×1440 at 60 fps measured **58.9 fps** down the CPU path.

---

## 5. ffmpeg facts measured on this build

Do not "correct" these from general knowledge; each was run.

* `ddagrab` feeds `h264_nvenc`/`hevc_nvenc` **directly**, no filter between. Output is
  yuv420p.
* `hwmap=derive_device=cuda` **fails** ("Failed to created derived device context: -40").
  There is no GPU filtering path at all on this build.
* Any filter means `hwdownload,format=bgra,…` and must end with an explicit
  `format=yuv420p` — left alone, libx264 writes **yuv444p**, which many players refuse.
* A rotated screen arrives in the panel's own orientation. DXGI rotation 2 needs
  `transpose=1` — confirmed by capturing the portrait monitor and looking at it.
* `ddagrab`'s `offset_x`/`offset_y`/`video_size` are in **surface** space, not desktop
  space. `recorder.surface_crop` does the mapping; it was verified by capturing the
  desktop top-left 700×400 of the rotated screen and checking the right pixels came back.
* ffmpeg reads a Windows named pipe (`\\.\pipe\name`) as an input.
* `q` on stdin makes it write the trailer and exit 0.
* `-thread_queue_size` is an **output** option on this build and errors on an input.
* `metadata=print` writes at log level `info`; with `-loglevel error` the filter runs and
  prints nothing, and an analysis silently sees zero frames.

---

## 6. Defects found and fixed during the build

Kept because each has a test that would catch it coming back.

1. `surface_crop` **slid** a rectangle that hung off the left or top edge inwards, keeping
   its size — so dragging a region from off-screen recorded a *different* part of the
   screen, at the right size, silently. Now intersects.
2. `surface_crop`'s two-pixel floor was applied after the clamp, so a sliver at the far
   edge reached one pixel past the surface, which ddagrab rejects.
3. `LAYOUT_SEPARATE` with mixed audio mapped one `[aout]` into both files; ffmpeg refuses
   the whole command. Now split with `asplit`.
4. Picture-in-picture with three sources built a `[c2]` chain and never consumed it;
   ffmpeg refused. Now refused in words the UI can show.
5. An unrecognised layout string — including `LAYOUT_SINGLE` with two sources — built a
   clean command that captured both screens and wrote only the first. Now validated.
6. The loopback feeder lost audio to dropped blocks without padding (see §2.3).
7. `hotkeys.py` lost the handle to a worker that would not join, and an orphan cleared the
   live run's state, so a registered, firing hotkey reported `active == False` with an
   empty error. A queued `WM_HOTKEY` also still fired *after* `stop()` — "stop the
   listener" could start a recording.
8. `clicks.py` had the same class of bug, plus an orphaned dispatcher that adopted the
   next run's queue.
9. The theme drew spin-box arrows and the check-box tick as CSS border triangles. Qt's
   style-sheet engine does not draw those — the buttons came out as empty grey blocks and
   a checked box as a filled square with no mark. They are painted PNGs now.

---

## 6a. The bug the unit tests could not reach

Everything above passed, 188 tests were green, and then the app was driven end to end
through its own global hotkey - and wrote a **zero-byte file**.

System sound was on, set to the *default* playback device. On this machine PortAudio
calls that the wired headset, which is unplugged. The endpoint opened happily and then
delivered nothing, for ever. The feeder was draining it with a blocking `stream.read`
while waiting for ffmpeg to open the pipe, so that read never returned, the wait never
noticed the connection, the pump never started, and ffmpeg sat on an input that would
never deliver: no frames muxed, an empty file, and **not one word of error anywhere**.

Three changes, all with tests in `tests/test_audio_feeder.py`:

* `_wait_for_ffmpeg` reads only what is already waiting and gives up after
  `CONNECT_TIMEOUT`, reporting it.
* The pump's silence padding then keeps the timeline right even on a dead endpoint, so
  the recording simply succeeds with a silent track.
* `_report_silence` names the device when the *entire* track turned out to be invented:
  *No sound at all arrived from "Наушники - шнур (Temple) [Loopback]" - the whole track
  is silence. Is that playback device actually connected and in use?*

The lesson is the general one: a unit suite cannot see a thread that blocks on hardware.
`scratch/probe_endtoend.py` drives the real app through the real hotkey and is the test
that found this; it is worth keeping and running after any change to the audio path.

## 8. The interface, rebuilt (2026-11-04)

The first window was a settings form - four cards, twelve controls, all at one level of
importance, and you had to read all of them to press one button. Recording is an action,
so the interface is now built around the action. `ui/main_window.py` and `ui/island.py`
are **deleted**.

* **The bar is the application.** Source chip, three device toggles with their own
  chevron menus, a live level meter, two quick pills (frame rate and format), the record
  button, a gear. Under it a footer: the output folder, the last file, and the hotkey as
  a keycap. Recording swaps it for a stop button, the clock, size and frame rate, pause,
  discard and the folder.
* **Popovers**, not dialogs: the source picker (a card grid with live thumbnails), the
  device lists, the quality list.
* **The settings window** behind the gear: a sidebar of five pages - Recording,
  Sound, Camera, Control, General - with real toggle switches, a drawn diagram of where
  the camera sits, and shortcut boxes that read a key press rather than being typed
  into. Rebuilt on 2026-11-08 from the single 1375 px column it had been.
* `ui/theme.py` is a new palette: violet is *selection*, green is *live*, red is
  *recording*, and nothing else is ever red. Contrast is tested.

Four things came from the user's own mock-up (`docs/design/user-mockup-2026-11-03.webp`)
and are better than what was there: the live microphone meter, frame rate and format as
pills on the main row, the footer, and switches instead of check boxes.

**`WDA_EXCLUDEFROMCAPTURE` changed the design.** Measured: a window with this set stays
on screen and contributes **zero** pixels to a ddagrab frame, while an identical one
beside it contributed 3551. Every piece of chrome now sets it, so the bar can sit on the
screen being recorded without appearing in it - which retired the "move the bar to
another monitor" compromise entirely. It needs Windows 10 2004+; `FloatingSurface`
reports whether it took and the controller says so when it did not.

## 9. The eight features asked for on 2026-11-04

| | | |
|---|---|---|
| 1 | Pause and carry on | `takes.py`, `recorder.RecordingSession`. Measured below. |
| 2 | Level meters | `levels.py`, shown on the bar; warns when a device is open and silent |
| 3 | Crash safety | every segment is Matroska with `-flush_packets 1`, remuxed to MP4 |
| 4 | Free space | `storage.py` - says **time left**, not gigabytes, from the measured MB/min |
| 5 | Hardware encoding | `capabilities.py` probes by encoding a frame; "Automatic" picks from what works |
| 6 | Presets | `presets.py` - Lesson with voice, Silent demonstration, Maximum quality |
| 7 | Finish actions | `shellops.py` - open, reveal, or copy the file itself for pasting |
| 8 | Auto-stop | a single-shot timer; "Stop after" in the settings panel |

**Pause, measured.** Record 4 s, pause, wait 3 s, resume 4 s, pause, resume, stop:
one file of 11.24 s with its audio track, 59.7 fps, and the clock did not move while
paused. Three segments joined with `-c copy` in 0.03 s. Nothing is re-encoded.

**Crash safety, measured.** A 12-second recording with the process killed:

    plain mp4              2.10 MB on disk   UNREADABLE (no moov atom)
    fragmented mp4         3.41 MB           plays, 10.15 s
    plain mkv              1.31 MB           plays, 480 frames
    mkv, -flush_packets 1  1.23 MB           plays, 600 frames   <- chosen

`takes.orphans()` finds segments a dead session left behind and the app offers to
assemble them on the next launch.

**The encoder probe, measured.** Twelve candidates since 2026-11-04 (`libx265` was
dropped, Media Foundation added); on this machine **seven of them refuse to encode a
frame** - every AV1, every Intel QSV, every AMD AMF. `hevc_nvenc`, `h264_nvenc`,
`hevc_mf`, `h264_mf` and `libx264` work. Since the probe was moved onto the real
`ddagrab` pipeline it costs about 4 s cold: the cache this machine holds records
299-457 ms per candidate and **4.36 s** in all, plus two preamble runs it does not time.
It is cached and keyed on the ffmpeg build string - and on nothing else, so a new
graphics card or a new driver does **not** re-ask, whatever `capabilities.py`'s module
docstring says. `CACHE_VERSION` 2 means every existing install pays the 4 s once.

That cost used to be paid before the window appeared; it now runs from the event loop
after `bar.show()` (`ui/app.py`, a zero-delay `QTimer.singleShot`). The three
enumerations that still run before the bar is shown were timed here on 2026-11-04 and
cost **0.21 s** between them: `list_monitors` 0.011 s, `list_microphones` 0.135 s (it
starts ffmpeg) and `list_loopback_devices` 0.060 s.

**A crash found on the way.** Two `pyaudiowpatch.PyAudio()` instances alive at once
segfault the interpreter - exit 139, no traceback. One meter is fine; a second one in
another thread kills it. That would have landed mid-recording, because the loopback
feeder already holds an instance whenever system sound is on. Everything now goes
through `portaudio.py`: one reference-counted instance, opened under a lock.
Also: `audioop` is gone from Python 3.13, so the peak is computed directly.

## 10. Drawing over the screen

Epic Pen's job, in ScreenRec's architecture. `annotate/` holds the document
(`model.py`), the transparent canvas, the toolbar and the text box; `ui/annotations.py`
is the controller that owns the modes.

**The architecture question was settled by measurement before anything was written**: an
ordinary transparent always-on-top window **is** captured by the Desktop Duplication
API, and `WDA_EXCLUDEFROMCAPTURE` removes one from the capture while leaving it on
screen. So the drawings are an ordinary overlay and the toolbar is excluded - **the
encoding pipeline is not touched at all**. No compositing, no second render, no double
drawing, and the frame the encoder sees already has the stroke in it at the moment it
was drawn.

Verified against the real capture path by `tools/verify_annotations.py`, which records
while drawing and then looks in the file:

    whole monitor   361 frames, 59.5 fps, 5 dropped   drawing found (3423 px)
    region          337 frames, 60.4 fps, 0 dropped   drawing found (3379 px)
    window          338 frames, 60.2 fps, 0 dropped   drawing found (2827 px)

A still from the whole-monitor run was looked at: the green stroke is there, and the
toolbar - which was sitting in that exact corner of the screen - is not.

Coordinates are physical desktop pixels throughout, including negative origins for a
screen left of the primary. **Scaled displays (125%, 150%, 200%) are UNTESTED**: both
screens here run at 100%, the conversion is written explicitly and commented, and
nobody has seen it work at another scale.

Drawings are anchored to the **screen**, because the recording is too - a region or
window capture is a fixed rectangle and does not follow the window. Moving the recorded
window moves it out from under the recording and the drawing together. There is no
anchoring to anything inside the recorded application, and none is promised.

## 11. What using it found, and 475 passing tests had not (2026-11-04)

The user ran it and came back with seven things. Every one of them is an interaction -
a click, a drag, a window - and not one was visible to the offscreen suite, which grabs
widgets and calls handlers. `tools/ui_probe.py` exists because of this: it drives the
real windows with real `SendInput` clicks and reproduces them.

| | what they saw | what it really was |
|---|---|---|
| 1 | no countdown on screen | it counted down in a **tooltip**. A number nobody can see is not a countdown. |
| 2 | nothing drew | the canvas is a layered window, and Windows hit-tests those by **per-pixel alpha**. Qt clears the backing store to alpha 0 and an empty document paints nothing, so in draw mode the window owned no pixels and every click fell through it. |
| 3 | the brush popover vanished, sliders could not be grabbed | its event filter recognised only a `QWidget`; a press into a separate top-level window arrives on that window's **`QWindow`**, so the "is this click inside me?" test was skipped and the popover closed on its own click. |
| 4 | the collapsed panel could not be expanded | it could - by clicking the pencil - which is a guess, not an affordance. There was no chevron. |
| 5 | no tray icon, no way to quit | true, and worse: with no way to tell it was running, a second launch started a **rival copy**. Two were alive when this was found. |
| 6 | the panels would not drag | they never had drag handling. A plain omission. |
| 7 | (found while fixing) | the canvas raises itself entering draw mode and left the bar and the toolbar underneath. |

Fixes: a real countdown ring on the screen being recorded, excluded from capture and
click-through; an alpha-1 hit backing on the canvas; the `QWindow` case in the popover's
filter; an expand chevron on the collapsed pill; a tray icon with a menu, a close button
on the bar, and a named-mutex single-instance guard that tells the first copy to show
itself; drag-by-the-header on both panels; and a `Canvas.raised` signal the controller
uses to put the chrome back on top.

**The lesson, written down because it will happen again.** Every one of these lives in
the gap between "the widget behaves" and "a person can use it". A suite that calls
`button.click()` can never find a window that will not take the click. `ui_probe.py`
is cheap to extend - add to it rather than to the unit tests when the next one of these
turns up.

### Verified afterwards, by using it

`tools/ui_probe.py --only all` drives every surface with real `SendInput` clicks:
**35 of 35**. `tools/verify_end_to_end.py` then uses the whole application the way a
person does - press Record, watch the countdown ring, turn on drawing, draw, pause,
carry on, stop, quit - and judges the result from the file: **17 of 17**, one MP4 of
6.17 s at 2560x1440, 59.7 fps, with 4488 pixels of the drawing in it and no segments
left behind. A still from that recording was looked at: the stroke is there and not one
piece of ScreenRec's own interface is, although the bar, the drawing panel and the ring
were all on screen while it ran.

One adjacent defect the verifiers found and that is also fixed: the bar and the
annotation panel snapped to a screen edge but never *clamped*, so a drag could leave
either of them as a 20x25 sliver at the far corner of the desktop with nothing to grab.
`surfaces.SurfaceDrag` had it right; the other two now do the same.

## 12. Every screen at once (2026-11-04)

Built and verified. The full story and the numbers are in `docs/MULTIMONITOR.md`; the
short version is that **both conclusions of the first pass were wrong**, and each was
wrong in a way worth remembering.

* "One ffmpeg with several `ddagrab` inputs is the tidy answer" - it costs frames.
  Measured: 53.4 and 59.2 fps against 59.7 and 59.1 for a process per screen. The
  original 13.8/11.5 fps that made this look hopeless was measured with the encoder
  tuning that was later thrown out for costing 14% of the frames on one screen.
* "Two processes drift half a second apart" - that compared how *long* the files were,
  and they were stopped one after the other. Flashing a mark on both screens at the same
  instant and finding it again in each file gives 17 ms for two processes against 50 ms
  for one, and **0 ms** in the finished application.

So: **one ffmpeg per screen**, started in a tight loop, audio in exactly one file, and a
`.screenrec.json` sidecar carrying each screen's desktop rectangle so a player can lay
the windows out the way the screens really sat. `app/screenrec/multi.py`.

`tools/verify_two_screens.py` records both screens through the application and judges
the files: **12 of 12**, 59.8 and 59.3 fps, both at their own full size with the rotated
screen upright, 0 dropped frames, 0 ms apart.

The player is `app/screenrec/player/`. Before it was written, two `QMediaPlayer`
instances were measured against each other: a **constant 33 ms apart over 45 seconds,
worst 67 ms, no cumulative drift**. So it corrects a follower only when the gap is worth
correcting instead of nudging every tick, which would stutter for nothing.

## 13. The night of 2026-11-04 to 05

Two things were asked for: finish the encoder work, and go over both programs. What
follows is what changed, what was measured, and - at the end, and longer - what was
found and deliberately left alone.

### Fixed, each reproduced first and each with a test that fails against the old code

**The recorder**

* **The measured MB-per-minute was never measured.** Two halves were built and neither
  owned the join: `storage` files what a finished recording cost, `takes.finish` calls
  it, and `RecordingSession` built its `Take` with every field empty - so nothing was
  ever recorded and every estimate still came from the NVENC table. The plan now carries
  `quality_level`, and the session describes itself from the first segment's plan.
  `tools/verify_sizes.py` proves it end to end: **11.7 MB/min measured against 98.0 from
  the table**, which is the factor of eight this feature exists to remove.
* **A unit test was reading the user's own store**, and failed the first time anything
  was ever recorded on this machine; a harness was writing into it. Both now use a
  stand-in, and `verify_sizes.py` puts the real store back exactly as it found it.

**The player** - all three from the same two measured Qt facts, in bare Qt with no
ScreenRec code in it: a frame reaches a `QVideoSink` on `pause()` and **not** on
`setSource` or `setPosition`; and `pause()` on a player that has reached the end of its
file **rewinds it to zero and pushes frame zero**.

* **A recording nobody had played showed nothing at all.** Two black panes, a scrubber
  that moved while the picture did not, and §2.2 of `PLAYER_SOURCES.md` - nudge one
  stream a frame at a time until two clapperboards line up - impossible until somebody
  pressed play once. `SyncEngine._show_the_first_frame` pauses each player once at the
  end of `_build`.
* **Playing to the end threw the viewer back to 00:00 and showed frame 1 of both
  files.** `_on_finished` paused every player; now `_park` leaves alone any player Qt
  has already stopped at the end. Measured after: the timeline stays at 10267 of 10267.
* **A stream that had ended went black instead of holding its last frame**, against §5
  of `PLAYER_LAYOUTS.md`. Qt's end-of-file frame is *invalid*, and forwarding it is the
  same call `_blank()` makes on purpose. It is dropped on the way in from a decoder and
  still painted on the way in from `_show_held_frame`, where "nothing" is the truthful
  picture for a pane that has just changed stream.

### Measured

* **A ten-minute recording holds sync exactly.** `tools/measure_sync.py --seconds 600`:
  30 marks over 600.6 s, **drift first to last 0.0 ms**, spread 33.3 ms, which is one
  frame at 60 fps and therefore the detector's own resolution. The "longest measured is
  45 s" that stood in this document all week is gone.
* **The window now comes up before the probe, and by a long way.** Cold cache, measured
  from inside a real launch: a visible bar at **0.53 s**, the encoder probe answering at
  **5.18 s** - the window is on screen 4.65 s before the answer arrives.
* Every profile resolves to an encoder that can really be built, on this machine and on
  five pretend ones (NVIDIA-only, AMD-only, Intel-only, Media-Foundation-only,
  processor-only, nothing-works), and all five families that work here recorded a real
  file through the real `build_command`.

### Found and left alone, in the order a person would notice them

A seven-agent read-only audit of the player, with every finding reproduced by a second
agent and the worst of them re-reproduced here. **None of this is fixed.** They are
listed so that whoever picks the player up next starts from them rather than finding
them again.

1. **Press play on the last frame and it rewinds and does not play.** Bare Qt restarts
   such a player happily by all three routes tried, so this is in `engine.play`'s own
   path, not a Qt limitation - which is why it was not fixed blind at two in the
   morning.
2. **Pause while one stream has already ended and that stream rewinds to zero**; on
   resume it replays its first seconds against a timeline that is elsewhere, drift
   -7500 ms, and nothing is said to the viewer.
3. **"Save this set" loses the frame size and the frame rate**, so a portrait camera
   comes back landscape. `open.save_set` never passes `sizes`, though `multi.write_set`
   takes it and its docstring describes this very regression as fixed.
4. **Every "open another recording" from inside the player leaks a whole player** -
   windows, views, transport - linearly, ten opens leaving eleven of each. The clocks
   are released properly, so the MP4s are let go; the windows are not.
5. **The second window comes back on top of the first**, 100 % overlap, instead of into
   its share of the screen.
6. **A menu outlives the window it belongs to and still acts** - `show_view`,
   `hide_view`, `apply_tiling`, `restore_arrangement` and `set_stream_monitor` have no
   closing guard, so a frameless video window can return to the desk after the player
   has gone.
7. **`S` and `L` do nothing in the separate-windows half** while working on the stage.
8. **The audio chip and the volume button disagree**: choose "Звук выключен", then press
   the volume button, and the sound returns while the chip still says it is off.
9. No badge when scrubbing into the tail of an ended stream while paused; the black
   run-up before a negative offset runs about 10 % long; both offsets past the end give
   a timeline of zero length that still accepts a seek.
10. File handling: a network folder that is not there raises out of a function whose
    docstring says it never raises; `read_sidecar` raises on a `screens` that is not a
    list of objects; some messages reach a Russian interface in English; `save_set`'s
    `target=` can be pointed at somebody else's index.

### Still true from before

* **Not a git repository, nothing committed.** Line endings are mixed - 41 CRLF files
  against the rest - which wants a `.gitattributes` in the same breath as `git init`.
* Display scaling at 125/150/200 %, more than two screens, and a monitor physically
  unplugged mid-playback are all still unverified; this machine has two screens at
  100 %.
* Packaging is done: `packaging/ScreenRec.iss` builds the installer and
  `docs/RELEASE_PLAN.md` records the reasoning, including why the licence is GPL v3.

---

## 14. The rest of that night, to the morning of 2026-11-05

§13 ended with ten player defects found and not fixed, and with the recorder believed
to be in good order. Both halves of that turned out to need work, and the second half
is the more interesting one.

### The recorder was not in good order; nobody had looked

The player had been audited by seven agents who fixed nothing and only searched. The
recorder had not - its recent changes were reviewed, the program as a whole never was.
"Ten findings in the player and none in the recorder" was a difference in attention,
not in quality. The same five-lens audit on the recorder found **four defects a person
meets at once**, two of which lose work:

* **One failed recording and the system sound was dead until the program restarted.**
  The pipe name was built from `segment.stem[-6:]`, and the first segment of every
  recording is `…part00` - so the name was a constant. Worse, `audio.py` never let the
  handle go when ffmpeg had not connected, so the name stayed taken: the next recording
  got `error 231`, pipe busy, for the rest of the session. Unplugging a USB microphone
  was enough to trigger it. The tag is now `HHMMSS_<counter>` per recording, and the
  handle is released whether ffmpeg connected, connected and died, or never came.
* **Changing the format or the frame rate during a pause silently corrupted the file.**
  Settings are reachable while recording, the plan is rebuilt per segment, and
  `takes.finish` joined with `-c copy` without checking. Measured before: 122 frames
  that decoded against 353 decode errors, and the user was told it was saved. A take
  now has a shape, fixed by its first segment.
* **"Two tracks" lost one of them** - the system sound disappeared from every finished
  file, because the join had no `-map 0`.
* **A failed resume left a dead end** - `_paused` was cleared before the segment
  started - and recording by hotkey then abandoned the take as an orphan.

Six more, all fixed: a small rectangle could be chosen and not recorded; "stop after N
minutes" ignored an all-screens recording and counted the pause; quitting during one
asked nothing; the real reason for a failure was overwritten by the next status line
and left in English; a malformed `region` in `settings.json` killed the record button
silently.

### The player's ten

All ten of §13 are fixed. Four were re-reproduced here afterwards, from outside, on a
real recording rather than from the fixers' own tests:

* play on the last frame now plays;
* pausing while the short stream has ended leaves it at 5033 ms instead of rewinding it
  to zero, and it does not replay its beginning on resume;
* "Save this set" carries `(2560,1440)` and `(1440,2560)` through the round trip, with
  the offsets and the frame rate - a portrait camera comes back portrait;
* the leak is gone, and this one had to be measured twice because the first probe
  counted "0 of 0" and proved nothing. Honest count of live objects: **eleven
  `PlayerWindows`, eleven `ScreenView` and eleven `TransportBar` after ten opens before;
  two, three and two now, flat from the first open to the tenth.**

### One regression, found here and not by anyone's tests

`Recorder.start` grew a start grace so that a capture which dies of its own arguments
says so instead of reporting success - measured 0.355-0.363 s - and its own comment says
a caller starting several recorders must launch them all first and settle them
afterwards. `multi.py` belonged to a different agent and was never told, so the
all-screens start paid the grace **between** screens:

    verify_two_screens: "the screens start together (334 ms apart)"   334, 367, 334 ms

against the 17 ms this project measured and documents - twenty frames, and a viewer
would see it. `MultiSession.start` now launches every screen in the tight loop and
settles them in a second loop. After: 0, 0, 0, 17, 50 ms and three runs of 67-84 under
load, against a harness threshold of 50.

**The residual was then measured and is not this code's.** Re-run on an idle machine -
after stopping an orphan `ffmpeg` that an agent's test had left capturing the screen
for three hours - the harness gave 0, 16, 50, 0, 84, 33, 0, 83 ms over eight runs,
median 33, worst 84, two over the 50 ms threshold. So it was never load.

Timing both `Recorder.start` calls directly settles it: **the gap between the two
launches is 2.0-2.3 ms**, five runs out of five, while the whole of `MultiSession.start`
takes 355-365 ms - the start grace paid once at the end, as intended. The loop is as
tight as a loop can be. What the harness measures is when each `ddagrab` hands over its
first frame, which differs per monitor and is quantised to whole frames at 60 fps; every
value above is a multiple of 16.7 ms. The **17 ms** this document has quoted since the
beginning was one sample of that distribution, not a constant, and it is now written
down as a distribution.

The 50 ms threshold is kept: it is a statement about what a viewer should see, and two
runs in eight exceeding it is worth knowing rather than hiding. The lever for it is not
in this code.

### Where both programs stand

    1961 unit tests                     OK (2 skipped)
    tools/static_review.py              78 files, nothing found
    tools/check_translations.py         every translatable string has Russian
    tools/verify_layouts.py             23 of 23
    tools/verify_sources.py             39 of 39
    tools/verify_player.py              14 of 14
    tools/verify_sizes.py               8 of 8
    tools/verify_capture_frame.py       PASS
    tools/verify_end_to_end.py          17 of 17
    tools/verify_two_screens.py         12 of 12

### Still not verified, and why

* **AMD and Intel.** The code for them is written from their own option lists, measured
  against this build of ffmpeg, and guarded so that an untested path downloads the frame
  rather than crashing - but it has never run. This machine has one NVIDIA card.
* The start gap above, on a quiet machine.
* Display scaling at 125/150/200 %, more than two screens, a monitor physically
  unplugged mid-playback.
* **Not a git repository, nothing committed.** Line endings are still mixed, 41 CRLF
  files against the rest, which wants a `.gitattributes` in the same breath as
  `git init`. Packaging is in `docs/RELEASE_PLAN.md` and its first two steps are the
  author's: a licence decision and that `git init`.
* `scratch/twofinal` is **not the recording the earlier sections describe**: an agent
  recorded a fresh two-screen take over it during the night. The shape is the same
  (2560x1440 and a rotated 1440x2560) and every harness passes against it.

---

## 7. Next

Stage 2 — every monitor at once, and a player for it — **is done**; it was item 1 here
until 2026-11-04 and §12 is what came of it, including the two measurements in this list
that turned out to have been taken with the encoder tuning that was later thrown away.

1. **Finish the self-measuring size estimate: one line, in `recorder.py`.** The
   reading end is wired - the panel's estimate line and the free-space check both ask
   `storage.size_estimate`. The writing end is not: `RecordingSession` must tell its
   `takes.Take` the encoder, quality, pixel format, capture size and frame rate it is
   recording with, or `takes.measure` refuses every recording and `sizes.json` is never
   written. Until it does, every estimate on every machine is this machine's NVENC
   table, which `docs/ENCODERS.md` §3 measured at up to 4.9x wrong through Media
   Foundation. Check it with `grep -n "Take(" app/screenrec/recorder.py`.
2. **Watch a real recording back.** Everything here is measured; nobody has yet made a
   tutorial with it and looked at the result.
3. **Decide about git.** Done on 2026-11-05: the repository exists, the licence is
   GPL v3, and `THIRD-PARTY-NOTICES.md` says what else is in a release and why.
4. **Line endings are mixed**: counted 2026-11-04, **41** of the Python files are CRLF
   and **149** are LF, with no file mixing the two. This has been true since long before
   the player work and `tools/static_review.py` does not look at it. Nothing is broken by
   it, but the first `git add` would churn the whole tree, so normalise it with a
   `.gitattributes` in the same breath as deciding about git, not before.
