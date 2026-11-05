# Choosing an encoder on a machine nobody has seen

The author's requirement, 2026-11-04:

> нужна универсальная программа, которая сможет работать на любом ПК и любой
> видеокартой и давать оптимальный результат

This is the contract for how that choice is made. It replaces nothing in
`docs/CONTRACT.md`; it fills the hole that `recorder._video_codec_args` and
`recorder._filter_for` currently leave open.

---

## 1. What everybody else does, and the one thing they all agree on

**Nobody decides from the name of the graphics card.** The same chip reports different
abilities depending on the driver, the Windows build, whether it is in a virtual machine
and whether the display engine is switched off in firmware. Every recorder that works on
other people's computers does the same three things in the same order: **enumerate the
candidates, find out for real which ones run, remember the answer.**

OBS Studio is the reference, and the detail worth stealing is *where* the knowledge
lives: there is no probing in its front end at all. Each encoder plug-in decides at load
time whether it can register itself, and the UI simply lists what registered. Its
automatic configuration never picks AV1 or HEVC — not an oversight, ten years of support
load. The thing **not** to copy is its preference rule
(`preferHardware = nvencAvailable || appleAvailable || physical_cores <= 4`): that is
tuned for streaming at a fixed bitrate, where a weak encoder costs picture quality. This
program writes to disk at constant quality, so a weak encoder costs **file size**, while
the CPU it burns is taken from the program being recorded. Nobody notices a file 20 %
larger. Everybody notices the game stuttering while they record it.

So: **hardware wins whenever it actually works, whatever the vendor and however many
cores the machine has.**

This project already has the hard half. `capabilities.py` encodes a real test frame with
each candidate and keeps "ffmpeg lists this name" apart from "this machine did it",
caching the answer against the ffmpeg build string. That is better than most. What is
missing is that `recorder.py` cannot act on the answer — see §4.

---

## 2. Rank by how far the frame has to travel, not by vendor

The ranking rule has to be defensible without three vendors' hardware on the desk, so it
is built on the one thing that was measured here (2560×1440 at 60 fps, with
`tools/screen_content.py` scrolling on screen, so not an idle desktop):

| path | fps | ffmpeg CPU in 12 s | of one core |
|---|---|---|---|
| `ddagrab → hevc_nvenc`, no filter | 59.9 | 0.6 s | 5 % |
| `ddagrab → hwdownload → libx264 veryfast` | 60.0 | 21.9 s | 173 % |

(Both over a 12.7 s run, which is where 173 % comes from rather than 182 %.)

A factor of thirty-six. That is not a difference between codecs; it is the price of
moving the frame out of video memory and converting its colours. So the tiers are:

| tier | what happens to the frame | who | filter |
|---|---|---|---|
| **1 — it does not move** | the encoder takes the d3d11 surface as it is | `*_nvenc`, `*_mf`, `*_amf` | *(none)* |
| **2 — it is remapped on the same adapter** | zero-copy `hwmap`, both sides one D3D11 texture | `*_qsv` | `hwmap=derive_device=qsv,format=qsv` |
| **3 — it is copied into RAM** | an honest copy plus a colour conversion | `libx264` | `hwdownload,format=bgra,…` |

Within a tier: **H.265 before H.264**, which the project has already measured
(`config.PROFILES`: 24.5 against 27.7 MB/min at `balanced`).

**AV1 never wins automatically.** Its `cq` ladder and MB/minute are not measured, so
choosing it would break `config.Profile`'s own promise that those numbers are measured
rather than guessed; it exists on a narrow band of hardware; and a screen recording is
made to be sent to somebody. It stays an explicit choice, like 4:4:4.

**When two families both work** — the usual laptop with an Intel iGPU and an NVIDIA
card — the winner is the family belonging to **the adapter the frame came from**. This is
not a preference, it is a condition: Desktop Duplication duplicates an output on the
adapter that drives it, `ddagrab` makes its D3D11 device there, and the frame is in that
adapter's memory. `winapi.Monitor` already carries `adapter`; it needs the DXGI vendor id
beside it.

---

## 3. Media Foundation is a tier-1 path for every vendor, and it was nearly missed

Measured on this machine, 2026-11-04, straight from `ddagrab` with no filter at all:

```
hevc_nvenc   59.3 fps   0.7 s CPU   5% of a core   45.6 MB/min  (cq 23)
hevc_mf      59.3 fps   0.6 s CPU   5% of a core   89.3 MB/min  (-b:v 12M)
h264_mf      58.1 fps   0.6 s CPU   5% of a core   88.7 MB/min  (-b:v 12M)
```

**On a near-static desktop**, and that is why the MB/min column must not be read against
the ladder further down, which was measured with `tools/screen_content.py` scrolling:
`hevc_nvenc` at cq 23 is 45.6 MB/min here and 71.5 MB/min there, both true, because the
content is what a constant-quality encoder responds to. The bitrate figures are from a
fixed `-b:v 12M` passed by the measurement, not by this program - nothing here sets a
bitrate for MF any more, which is the point of the ladder below.

`[hevc_mf] MFT name: 'NVIDIA HEVC Encoder MFT'` — Windows found the hardware encoder
itself. **The file sizes are not comparable**: NVENC ran at constant quality and the MF
runs at a fixed bitrate, which is the real difference between them (see below). The
numbers that matter here are the first three columns, and they are identical.

What this means: `h264_mf` and `hevc_mf` are **one code path that uses whatever hardware
encoder the machine has**, because Media Foundation dispatches to the vendor's own MFT.
For a program that cannot test AMD or Intel, that is worth more than three hand-written
vendor paths that nobody can run.

**And one thing that is found only by trying it.** Media Foundation takes `nv12` from
system memory and nothing else. It advertises `nv12 yuv420p d3d11`, and given `yuv420p`
it writes **no packets at all** and reports `Nothing was written into output file` — word
for word the sentence a machine with no hardware encoder produces. The probe fed it
`yuv420p`, concluded that this machine has no Media Foundation encoder, and was believed.
That would have quietly switched off the vendor-neutral path on **every** machine while
looking like an honest hardware answer. `encoders.MF_PIXEL_FORMAT` and the probe's own
`-vf format=nv12` are what close it, with a named test as the guard.

Its quality ladder was measured rather than guessed — twice, and **the two
measurements disagree by a factor of five.** `-quality` is a 0..100 scale that is neither
CQ nor CRF, and what it spends depends on the content far more than NVENC's `cq` does.
2560×1440 at 60 fps, seven seconds each, `hevc_nvenc` at cq 23 as the reference:

```
                     scrolling text        a busy desktop
hevc_nvenc cq 23       71.5 MB/min          146.1 MB/min
hevc_mf quality 87     74.0   (1.03x)       709.4   (4.9x)
hevc_mf quality 78     57.7   (0.81x)       529.4   (3.6x)
hevc_mf quality 68     41.0   (0.57x)       262.3   (1.8x)
```

So no single number reproduces NVENC's size, and the first version of this document
claimed one did, on the strength of the left-hand column alone. **68 is chosen for
`balanced` because its two ratios, 0.57 and 1.80, have a geometric mean of 1.01** — as
even-handed as one number gets. The rest of the ladder is spaced by hand, because the
scale saturates near 100 and fitting through saturation fits nothing.

This is a starting point, and §5's fourth device — measure the viewer's own first
recording — is the real answer. Until then, a machine recording through MF should be
expected to write a larger file than this program's own table predicts.
Constant-quality mode is worth having on its own, and on the near-static desktop above
the difference is the whole argument: `-rate_control quality -quality 60` cost
14.0 MB/min where a fixed `-b:v 12M` cost 89 MB/min for the same seconds of the same
screen. A fixed bitrate spends the same on a still screen as on a moving one.

Four more things that must be got right, all verified here:

* **`-hw_encoding true` is not optional.** The default is false, and at the default
  Media Foundation hands back Microsoft's software MFT — a CPU encoder wearing a hardware
  name. A probe that did not set the flag would record it as hardware and lie.
* **`av1_mf` does not open here** (`Could not open encoder before EOF`), consistent with
  Ampere having no AV1 encoder. It is not special-cased; it simply fails its probe, which
  is what the probe is for.
* **Rate control is a different animal.** MF has no CQ: `-rate_control quality` takes a
  0..100 number that is neither CQ nor CRF. The ladder below is what it was matched
  against, measured; and `config.PROFILES`' `mb_per_minute` was measured for NVENC, so
  on a machine that records through MF the size estimate is somebody else's number until
  §5's fourth device replaces it with the first real recording's own.
* **Only NVIDIA's MFT has been seen.** That Media Foundation picks Intel's or AMD's on
  their hardware is what the API is for and is not in doubt as a design; it is still
  unverified as a fact. §5.

---

## 4. The dispatch, concretely

The defect this replaces: `_video_codec_args` (`recorder.py:246`) emits NVENC arguments
for exactly two names and silently falls through to `-c:v libx264` for every other,
while `_filter_for` (`recorder.py:275`) decides on `hwdownload` by comparing against the
name that was *asked for*. Eight of the eleven probe candidates therefore produced a
command that hands d3d11 surfaces to a CPU encoder, and ffmpeg dies on
`Impossible to convert between the formats`.

Checked by building the real commands, because one review claimed a second silent failure
that does not exist: **`hevc444` is not affected.** It names `hevc_nvenc`, which *is* one
of the two known names, so it emits `-c:v hevc_nvenc` and fails cleanly on a machine
without NVENC rather than recording the wrong thing quietly.

A new module **`app/screenrec/encoders.py`**, with no Qt, read by both the probe and the
recorder, so that the pipeline that was tested is the pipeline that runs:

```python
FAMILIES = {"h264_nvenc": "nvenc", ..., "h264_mf": "mf", "libx264": "cpu"}
DIRECT = frozenset({"nvenc", "mf", "amf"})
MAPPED = frozenset({"qsv"})

def family_of(name) -> str: ...      # '' for a name nobody has measured
def codec_args(encoder, quality, framerate, extra=()) -> list[str]: ...
def frame_path(encoder, rotate, pix_fmt) -> str: ...
```

* `_video_codec_args` becomes a dispatch per family and **raises** on an unknown name,
  exactly as `build_command` already refuses an unknown layout.
* `_filter_for` becomes the table in §2. An **unknown** family means *download* — not
  guessing, so ignorance degrades instead of crashing.
* `capabilities.try_encoder` stops encoding `testsrc` through a software path and starts
  exercising the real one, using the same two functions. Today it answers "does this
  encoder initialise"; it must answer "does this **pipeline** run", because those differ
  precisely on the hardware the author does not own.
* `config.Profile.encoder` becomes `codec` (`"hevc" | "h264"`). A profile naming
  `hevc_nvenc` on an AMD machine is a lie in the data; the family is chosen once, at
  record time, and the override hack in `ui/app.py:685` and `:718` disappears with it.

Per-family argument notes that cost a day if guessed (verified against this build):

* **AMF**: always pass the *name* (`balanced`, `quality`, …), never the number. The
  integers behind those names differ per codec and are not even monotonic — `-quality`
  is −1…3 on `h264_amf`, −1…15 on `hevc_amf`, −1…100 on `av1_amf`. And `av1_amf`'s
  `-qp_i` goes to **255**, not 51, so a quality number carried over from H.264 asks for
  near-lossless.
* **QSV**: `ddagrab` produces BGRA, and only `hevc_qsv` lists `bgra` among its formats;
  `h264_qsv` and `av1_qsv` will need `vpp_qsv=format=nv12` after the `hwmap`. Untestable
  here.
* **Rotation stays on the CPU path for every family.** `vpp_qsv` can transpose;
  `vpp_amf` has no rotation option at all. A portrait screen is rare enough to leave
  alone until there is hardware to measure it on.

---

## 5. What cannot be measured here, and how to ship it honestly

Not verifiable on this machine: **AMD entirely, Intel QSV entirely, Media Foundation on
a non-NVIDIA MFT, two graphics cards in one machine, GPU rotation**, and any claim that
quality numbers mean the same thing across families.

Four devices turn "untested" into "tested on the machine that matters, before the user
presses record":

1. **The probe exercises the pipeline**, through `encoders.frame_path()` and
   `codec_args()` — the same strings the recorder will use. Costs what it costs today.
2. **The cache key grows** beyond the ffmpeg build string to include the GPU name and
   driver version. The answer depends on the driver, so a driver update must re-ask.
3. **One step down instead of a crash.** If ffmpeg exits non-zero before the first frame,
   write the refusal into the probe cache with its reason, drop one tier, restart, and
   **say so in one line**. Only before the first frame, and only once per session — any
   more and it is dancing under the user. Nobody else in the field falls back silently;
   the current code is the only one that does, and it then dies.
4. **`mb_per_minute` is marked unmeasured for families nobody measured**, and the real
   figure is computed from the first finished recording's size and duration, which
   `Progress` already carries.

And one refusal: **do not map quality scales between families.** NVENC CQ and x264 CRF
happen to be close; AMF QVBR and QSV ICQ are different curves on different silicon.
Keep the ladder abstract (`best/high/balanced/small`) and show what the first recording
actually cost.

The refusal holds between *scales* and is broken between *codecs*, which a review on
2026-11-04 found and which is written down here rather than fixed from a guess. The
ladder lives on the profile, and `choose` may keep the profile while dropping its codec:
a CPU-only machine records the `hevc420` profile as `libx264 -crf 23`, where
`config.PROFILES` puts H.264 at 22, and an H.264-only Media Foundation machine takes
`hevc420`'s ladder into `h264_mf`. The number stays inside one scale, so nothing
mistranslates, but it is the other codec's number. **Nothing here is measured either
way** - what one step of `cq` is worth across codecs on the same encoder has not been
measured on this machine - and §5's fourth device makes the question smaller rather than
answering it, because after one recording the figure shown is that recording's own.

---

## 6. Order of work

**Done on 2026-11-04.** The unit suite, `static_review` and `check_translations` were
clean when it landed - 1477 tests then, and the count moves with every pass, so
`docs/HANDOVER.md` §1 is where the current one lives rather than here. On this machine
the probe now reports `hevc_mf` and `h264_mf` working while
every working family records through the real `build_command`: `hevc_nvenc` 59.7 fps,
`h264_nvenc` 56.9, `hevc_mf` 55.6, `h264_mf` 59.5, `libx264` 56.8, and the 4:4:4 profile
unchanged at 58.7. `best()` still returns `hevc_nvenc` here, so nothing moved for the
author's own machine — which is the regression that mattered. It was about
an hour, and it is all string-building, so the tests cover the hardware nobody here
owns:

1. `encoders.py` with the tables above.
2. `_video_codec_args` → dispatch, raising on an unknown name.
3. `_filter_for` → the tier table; the composite layouts pass `force_download=True`
   instead of lying about the encoder's name.
4. `CANDIDATES` reordered by §2, with `h264_mf`/`hevc_mf` added at tier 1 and AV1 last.
5. `best()` never returns AV1 while preferring hardware.
6. One test per family in `tests/test_recorder.py`.

**Half a day, also done on 2026-11-04** — with two of the four honest only as far as
the list below says. Three reviewers went over it the same day; what they found about
the UI is in the last column, and a reader should re-run the checks named there rather
than believe this paragraph, because the files it describes are not this document's.

| §6 item | state | what can be checked |
|---|---|---|
| the pipeline probe | **landed** | `capabilities.try_encoder` builds its command from `encoders.frame_path` and `codec_args` - the recorder's own strings - and falls back to `testsrc` only when `screen_available` says there is no desktop |
| the cache key | **not done** | `encoders.json` on this machine holds `version` and the ffmpeg build string and nothing else; the GPU name and the driver version are still not in it |
| `Profile.codec` | **landed** | `config.Profile` carries `codec` and `software_only`; `encoder` and `gpu` are gone, and so is the `pix_fmt == "yuv420p"` override in `ui/app.py` |
| the UI line | **landed, after a repair** | `capabilities.choose` returns a reason and both surfaces render one. As first written neither reached a user - the settings panel's `set_encoders` had no caller and the recorder's line was replaced by the next status line one statement later; re-checked the same day, `ui/app.py` now calls `set_encoders` and flushes the note after the status line |

Two things the probe still does not do, both worth knowing before trusting a cached
answer on somebody else's machine:

* **It measures one of the four chains `frame_path` can build.** `try_encoder` asks for
  the defaults - no rotation, `yuv420p`, no forced download - so a portrait monitor and
  the composite layouts run a chain nothing probed. ffmpeg inserts a converter when the
  graph's terminal format is not in the encoder's list, which was watched happening here
  for `h264_nvenc` and `libx264`, so this is expected to open rather than fail; it is
  **unverified on AMD and Intel**, which is where it would matter.
* **A cached answer does not say which pipeline produced it.** `screen_available`'s
  fallback to `testsrc` is not recorded in `EncoderResult` or in the cache file, and for
  `qsv` and `amf` the fallback is a different pipeline - no d3d11 surface, no `hwmap`. A
  probe that ran under RDP without a console, or in session 0, therefore caches a
  `testsrc` answer as though the screen had been there. `capabilities.py`'s own module
  docstring claims the record is kept; it is not.

The probe's cost, read off the cache this machine is holding (2026-11-04, twelve
candidates, `CACHE_VERSION` 2): 299-457 ms each, **4.36 s** of encoder attempts in all,
plus the two preamble runs (`-encoders` and the screen check) which the cache does not
time. It is paid once per ffmpeg build.

**Half wired** — §5's fourth device. `storage.remember_recording`,
`storage.size_estimate` and `takes.measure` are built and tested, and the reading end
landed on 2026-11-04: the settings panel's estimate line and the free-space check both
ask `storage.size_estimate` now instead of `config.estimate_mb_per_minute`. **The
writing end did not.** `recorder.RecordingSession` builds its `takes.Take` from the
output path alone, so every recording takes `measure`'s "The take does not say which
encoder wrote it" branch, `sizes.json` is never written, and `size_estimate` therefore
falls back to the table on every machine - which through Media Foundation §3 measured at
up to 4.9x wrong on a busy desktop, and which is the whole reason the device exists. The
check is one line: `grep -n "Take(" app/screenrec/recorder.py`.

**Not started** — §5's second and third devices: the cache key that includes the GPU and
the driver, and the one-step-down-with-a-sentence instead of a crash.

**Days** — measuring the software path and choosing its preset from what the machine can
actually do; the automatic drop to 30 fps when it cannot keep up; bitrate-based rate
control for the MF family; GPU rotation when there is hardware to measure it on.
