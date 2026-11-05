# Giving it away: what has to be true before a stranger can run this

Written down 2026-11-04:

> нужна универсальная программа, которая сможет работать на любом ПК и любой видеокартой
> и давать оптимальный результат

That sentence decides almost everything below, including the licence. This document is the
plan, and the order is not arbitrary: **step 1 is a blocker, and the rest is packaging.**

---

## Built, 2026-11-05

**§0 of this document was overruled, and rightly:** it argued for a portable
zip first and an installer at 0.2, and the answer was "\u043d\u0443\u0436\u043d\u0430 \u0433\u043e\u0442\u043e\u0432\u0430\u044f
\u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0430 ... \u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u0434\u043e\u043b\u0436\u0435\u043d \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c \u043d\u0430\u0448 \u043f\u0430\u043a\u0435\u0442
\u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c". A folder of files that each have to be started separately is not a
product, and the argument for the zip was about what is cheapest to maintain, not about
what a person gets. So there is an installer.

What exists now, and what was measured rather than assumed:

| | |
|---|---|
| `ScreenRec.spec` | two executables, one `onedir` folder, WebEngine and the rest excluded |
| `tools/make_icons.py` | both `.ico` files rendered from the app's own drawing code |
| `tools/build_release.py` | icons, PyInstaller, ffmpeg, licences, and a report |
| `packaging/ScreenRec.iss` | the installer: per-user, no administrator |
| `dist/ScreenRec-0.1.0-setup.exe` | **131 MB**, from 462 MB installed |

**Proven on this machine, in this order:**

1. both executables start - the `QtMultimedia` risk this document warned about did not
   materialise, nothing on stderr;
2. the packaged player opened a real two-screen recording;
3. the installer put it in `%LOCALAPPDATA%\Programs\ScreenRec`, with Start-menu
   shortcuts for both programs, an entry in "Apps & features", and `.screenrec.json`
   registered to the player;
4. the **installed** recorder found its own bundled ffmpeg and probed twelve encoders
   in 5.1 s - which is what `paths.FROZEN` was written for;
5. the **installed** recorder made a real recording, started and stopped by the global
   hotkey: HEVC, 2560x1440, 368 frames;
6. the **installed** player opened that recording;
7. the uninstaller removed the program, the shortcuts, the registry entry and the file
   association - and left `settings.json` and the encoder cache alone, which is
   deliberate.

Still outstanding, and unchanged by any of the above: **the installer is not signed.**
What Windows does with an unsigned installer, and what the options cost, is in §5.

---

## 0. The two questions, answered short

**«Собрать в один пакет?»** — one **folder**, not one file, zipped as a release asset.
The repository itself is not packaged at all; it stays source. `onedir`, not `onefile`,
and the reason is concrete rather than stylistic: 324 MB of ffmpeg cannot sensibly live
inside a one-file bundle that unpacks into `%TEMP%` on every launch, so ffmpeg ends up
beside the exe regardless — and then the one file has bought nothing. `onedir` also
leaves Qt's DLLs as separate, replaceable files, which is exactly what PySide6's LGPL
asks for.

**«Переводить в exe?»** — yes, unavoidable: another PC has no Python, no PySide6 and no
`.venv`. But **an exe is not an installer, and the first release needs no installer.**
This application never writes outside `%APPDATA%\ScreenRec` and `%USERPROFILE%\Videos`,
touches no registry key and needs no administrator. A portable zip is not a cop-out here;
it is the technically correct shape. Inno Setup belongs to 0.2, with winget.

---

## 1. The blocker: "Automatic" builds a command that cannot run — **fixed 2026-11-04**

Fixed on the day this was written: `app/screenrec/encoders.py` now holds one
table that both the probe and the recorder read, an unknown encoder name raises
instead of quietly becoming libx264, and Media Foundation was added as a
vendor-neutral hardware path. `docs/ENCODERS.md` is the contract. What follows is
kept because it is the shape of the bug, and the tests are named after it.

This is not a packaging concern. It is a defect that is invisible on the machine it was written on
and fires on most other people's, and it must be fixed before anything is published.

The chain, all four links verified in the code on 2026-11-04:

1. `capabilities.CANDIDATES` (`capabilities.py:33`) lists `av1_nvenc` **first**, then the
   NVENC pair, then Intel QSV, then AMD AMF, then `libx264`/`libx265`.
2. `capabilities.best()` returns the first *working hardware* entry.
3. `ui/app.py:685` and `:718` substitute that name into the plan whenever the chosen
   profile is plain 4:2:0 — which all three default profiles are.
4. `recorder._video_codec_args` (`recorder.py:246`) emits NVENC arguments **only** for
   exactly `h264_nvenc` and `hevc_nvenc`; every other name silently falls through to
   `-c:v libx264`. And `recorder._filter_for` (`recorder.py:275`) decides whether the
   frames need `hwdownload` by `cpu_encoder = encoder == "libx264"` — a string
   comparison against the name that was *asked for*, not the one that was emitted.

So on a machine whose best encoder is `h264_amf`, `h264_qsv` or `av1_nvenc`, ffmpeg is
handed d3d11 surfaces and told to encode them with libx264, with no download step. It
dies on `Impossible to convert between the formats supported by the filter
'Parsed_null_0' and the filter 'auto_scale_0'` — the same message `_filter_for`'s own
docstring records for the 4:4:4 case. The user sees "Запись…", then "Запись
остановилась сама" about 200 ms later.

**Who this hits:** every AMD PC, every Intel-only PC, and — because `av1_nvenc` is first
in the list — every RTX 40-series and newer NVIDIA PC. It does not hit this
RTX 3070 Ti, because Ampere has no AV1 encoder and `av1_nvenc` fails the probe, so
`best()` returns `hevc_nvenc` and the override is a no-op. The README already records
that fact about Ampere; it is also what has hidden this bug.

**Two different fixes, and they are not the same ambition:**

- *Make it work everywhere* — one line, `cpu_encoder` becomes "this is not an encoder
  that eats d3d11 surfaces", plus a loud failure instead of a silent rewrite in
  `_video_codec_args`. After this, an AMD or Intel machine records correctly **on the
  processor**. Universal, not optimal.
- *Make it optimal everywhere* — dispatch per encoder family in `_video_codec_args`
  (`-global_quality` for QSV, `-quality`/`-rc` for AMF, AV1 for the newer NVENC), and
  `_filter_for` taught which families can take a GPU surface and which need system
  memory. This is what was actually asked for, and **none of it can be measured
  on this machine.** It needs an AMD box and an Intel box, or it is being written blind.

Until the second is measured, `best()` must not return a family the recorder cannot
build a correct command for. A probe that says "this encoder works" is about ffmpeg, not
about ScreenRec.

---

## 2. The licence, decided by the first sentence of this document

The bundled ffmpeg is a BtbN **gpl** build: GPL v3, because of libx264 and libx265. Both
of the other public repositories here are MIT.

It is tempting to escape by switching to BtbN's **lgpl** build — it has `ddagrab` and all
the NVENC encoders, and only loses the x264/x265 software path. That escape is closed by
the requirement. "Any PC, any graphics card" means there has to be a path with no
hardware encoder at all: a virtual machine, a remote desktop session, an old integrated
GPU, a machine whose video engine is off. The only encoder that always exists is
libx264 — and libx264 is precisely what makes the build GPL.

**So: keep a GPL build, and stop treating it as a problem to be escaped.** What follows
from that is paperwork, done once:

- ffmpeg stays a **separate program**, invoked as a subprocess over a command line and a
  named pipe. Nothing is linked. This project's own code could stay MIT; the release is an
  aggregate of an MIT program and an unmodified GPL program.
- GPL v3 §6 attaches to the binary that is *conveyed*. For a download, §6(d) is the only
  available route — the "written offer valid for three years" of §6(b) is for physical
  media and does not apply here. §6(d) does permit the Corresponding Source to sit on a
  third party's server, but the obligation to keep it *available* stays with whoever
  distributes. BtbN's autobuild tags rotate, so the cheap, safe form is: **attach the
  FFmpeg source archive for the exact build commit as a second asset of the same
  release.** It never rots, and it costs one upload.
- `THIRD-PARTY-NOTICES.md` in the repository root, naming: FFmpeg (GPL v3, the exact
  build string, a link to the commit and to BtbN/FFmpeg-Builds), PySide6 (LGPL v3, DLLs
  shipped as separate replaceable files), **PyAudioWPatch — Apache-2.0**, comtypes (MIT).
  The current `README.md` says PyAudioWPatch is MIT; its own `METADATA` says Apache-2.0,
  so the README is wrong and must be corrected.

A reasonable alternative, for anyone who would rather not think about any of this again:
license ScreenRec itself under GPL v3. For a program being given away free it costs
nothing and ends every argument. It is a preference, not a requirement.

---

## 3. Size, measured

| | zipped |
|---|---|
| `ffmpeg-master-latest-win64-gpl.zip` (static — what is bundled now) | 192.5 MiB |
| `ffmpeg-master-latest-win64-gpl-shared.zip` | 86.8 MiB |
| PySide6 in the venv | 647 MB, of which **197 MB is QtWebEngine**, which this app never imports |

Two easy subtractions, together worth more than everything else in this document:
**switch to the `gpl-shared` ffmpeg** (about 150 MB off every download, zero lines of
code), and **exclude WebEngine, QML and the add-ons** from the PyInstaller build.

One hazard nobody would find by reading: the shared ffmpeg's DLLs are
`avcodec-63.dll`, `avformat-63.dll`, `avutil-61.dll`, `swresample-7.dll`,
`swscale-10.dll` — and **PySide6 already ships files with exactly those names** for
QtMultimedia, at the same major versions. Keeping `tools\ffmpeg\` as its own directory is
safe, because Windows searches the executable's own directory first; flattening both sets
into one folder would silently overwrite one with the other, and Qt's build has no x264
at all. So: never `--add-binary` ffmpeg into `_internal`. Copy the folder.

---

## 4. The rest of the port, in order

1. **`paths.py` under freezing.** `PROJECT_ROOT = Path(__file__).resolve().parents[2]`
   happens to resolve correctly in a PyInstaller `onedir` layout — `<app>\tools\ffmpeg` —
   which is worse than breaking, because it works until the layout changes and then falls
   through to `shutil.which` and picks up a stranger's ffmpeg from `PATH`. Anchor it on
   `sys.frozen` / `Path(sys.executable).parent` explicitly. Five lines.
2. **Probe the encoders after the window is up.** `_probe_encoders()` runs at
   `ui/app.py:155`, *before* `self.bar.show()`, and `try_encoder` allows **40 seconds per
   candidate** across 11 candidates. The answer is cached afterwards, but the first run on
   a slow machine can be minutes of nothing visible at all. Show the window, probe behind
   it, report progress.
3. **PyInstaller, one `.spec`, two executables** (`ScreenRec.exe`, `ScreenRecPlayer.exe`)
   into one `onedir` folder: `--windowed`, `--hidden-import pyaudiowpatch`, the WebEngine
   and QML excludes from §3, version information. ffmpeg copied in afterwards, never
   analysed.
   The real risk lives here: PySide6 6.12 is newer than the PyInstaller hooks that know
   about it, and `QtMultimedia` — which the player needs — is the classic place that
   surfaces, as a black window and `could not load multimedia backend ""`. If it fights,
   pinning the venv back to PySide6 6.11.x is cheaper than debugging hooks.
4. **`git init`, `LICENSE`, the notices file.** `.gitignore` already excludes
   `tools/ffmpeg/*.exe`; leave it that way — binaries do not belong in the repository.
5. **A smoke test on somebody else's PC**, ideally Intel or AMD, ideally with a Cyrillic
   user name. Record with system sound, stop on the hotkey, open the result in the player.
   Three actions, and they cover every trap above. No unit test reaches them.

Release assets for `v0.1.0`: `ScreenRec-0.1.0-win64.zip`, the FFmpeg source archive for
the pinned commit, and `SHA256SUMS.txt`. The release text carries the hashes, the GPL
paragraph pointing at the source asset, and an honest paragraph about SmartScreen.

---

## 5. Deliberately not in the first release

* **Code signing.** Since 2023 an OV certificate needs a hardware token or a cloud HSM,
  and since 2024 an EV certificate no longer buys an instant SmartScreen pass, so the
  $400 buys very little. The right move is to apply to the SignPath Foundation *after*
  there is an OSI licence and a GitHub Actions build — those are its conditions — which
  means after the first release, not before. Until then: say so in the README, publish
  SHA-256 sums, and link a VirusTotal scan run before release rather than letting a
  frightened user discover one.
* **An installer, winget, auto-update.** All of them want the installer first, and the
  installer wants the signature first.
* **Measured QSV and AMF support** — §1 says why: it cannot be measured here.

---
