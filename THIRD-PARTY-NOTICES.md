# Third party notices

ScreenRec itself is under the GNU General Public License v3 — see `LICENSE`. This file
says what else is in a release, under what terms, and where its source is. Everything
below was read from the thing itself rather than remembered: the ffmpeg line from
`ffmpeg -version`, the Python packages from their own installed metadata, on
2026-11-05.

---

## FFmpeg — GPL v3

    ffmpeg version N-128352-gec887b13a8-20261103
    configuration: … --enable-gpl --enable-version3 --enable-libx264 --enable-libx265 …

A Windows build by BtbN, shipped unmodified in `tools/ffmpeg/` with its own licence text
in `tools/ffmpeg/LICENSE.txt`. The `--enable-gpl --enable-version3` pair is what makes
it GPL v3: it carries **x264** and **x265**, both GPL, and this program needs x264 — it
is the only encoder that exists on every machine, and a recorder that must run on any PC
cannot do without it. That choice is explained in `docs/ENCODERS.md`.

ScreenRec **runs ffmpeg as a separate program**: a command line, a pipe for raw audio,
and `q` on its standard input. Nothing is linked and no ffmpeg code is compiled into
anything here.

**Corresponding source.** A release that contains the binary also carries the FFmpeg
source for that exact commit as a second asset, and the source of the build scripts that
produced it:

* FFmpeg at commit `ec887b13a8` — <https://git.ffmpeg.org/ffmpeg.git>
* the build that made this binary — <https://github.com/BtbN/FFmpeg-Builds>

* Project: <https://ffmpeg.org/>

---

## PySide6 and shiboken6 — 6.12.0, LGPL v3 or GPL v2 or GPL v3

Qt for Python, by the Qt Company. Offered under **LGPL-3.0-only OR GPL-2.0-only OR
GPL-3.0-only**; ScreenRec takes the GPL v3 option, which is the same licence as this
program, so the usual LGPL question of relinking does not arise. The DLLs are shipped as
separate replaceable files in any case.

* Project: <https://www.qt.io/qt-for-python>
* Source: <https://code.qt.io/cgit/pyside/pyside-setup.git/>

---

## PyAudioWPatch — 0.2.12.9, Apache License 2.0

A fork of PyAudio that adds WASAPI loopback, which is the only way to capture what the
machine is playing on Windows: mainline ffmpeg has no loopback input at all. It carries
PortAudio, which is under the PortAudio licence (MIT-style).

**`README.md` called this MIT until 2026-11-05. It is Apache-2.0**, which its own
installed metadata says, and the correction is noted here rather than quietly made.

* Project: <https://github.com/s0d3s/PyAudioWPatch>
* PortAudio: <https://www.portaudio.com/>

---

## comtypes — 1.4.17, MIT

Used for the Windows audio device interfaces.

* Project: <https://github.com/enthought/comtypes>

---

## What a release contains

| | |
|---|---|
| `ScreenRec-<version>-win64.zip` | the program, the Qt DLLs, and `tools/ffmpeg/` |
| `ffmpeg-source-<commit>.zip` | the corresponding source for the ffmpeg binary above |
| `SHA256SUMS.txt` | so a download can be checked |

The zip carries `LICENSE`, this file, and `tools/ffmpeg/LICENSE.txt` unchanged.
`docs/RELEASE_PLAN.md` has the rest of the recipe.
