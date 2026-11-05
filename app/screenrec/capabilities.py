"""What this machine's ffmpeg and GPU can really do - probed, not read off a list.

``ffmpeg -encoders`` lists what the binary was *built* with, which is not what the
hardware will *run*. Measured here on 2026-11-03: this build lists ``av1_nvenc``, and the
RTX 3070 Ti cannot use it - Ampere has no AV1 encoder, and a recording started with it
fails with "Nothing was written into output file" after the user has already pressed
record. Choosing an encoder from the list is therefore choosing one that may not work.

So every candidate is given one frame to encode. It takes a few hundred milliseconds per
encoder, happens once, and the answer is cached in the settings folder with the ffmpeg
build it was measured against - a new ffmpeg, or a new GPU, re-probes.

The probe encodes **through the real capture pipeline**: ``ddagrab`` into the same
filter chain and the same encoder arguments ``recorder.build_command`` would build, both
taken from ``encoders``. So it does need Desktop Duplication, and it is deliberately not
the first thing the program does - ``ui/app.py`` runs it once the window is on screen.
``testsrc`` is the fallback for a machine with no desktop to duplicate, and each row
records which of the two measured it: see ``EncoderResult.source``.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from . import encoders, paths

CREATE_NO_WINDOW = 0x08000000
#: Bumped whenever CANDIDATES changes, the probe's own arguments change, or a row
#: gains a field: a cached answer from an older list has nothing to say about an encoder
#: that was not in it, and - worse - a Media Foundation entry probed before
#: `-hw_encoding true` would be a software encoder recorded as hardware.
#:
#: 3: rows carry `source`. A version 2 row cannot say whether it was measured through
#: `ddagrab` or through the `testsrc` fallback, and defaulting it to "screen" would put
#: a claim into the cache that nothing measured. Old caches are dropped instead; the
#: whole probe costs about five seconds once.
CACHE_VERSION = 3

#: Every encoder worth trying, in the order "whatever is best here" should prefer them.
#:
#: The order is **how far the frame has to travel**, not who made the card - see
#: `docs/ENCODERS.md`. Measured at 2560x1440 and 60 fps with moving content on screen:
#: `ddagrab -> hevc_nvenc` with no filter costs 0.6 s of CPU in 12 s, and
#: `ddagrab -> hwdownload -> libx264` costs 21.9 s. A factor of thirty-six, and it is the
#: journey rather than the codec. Within a tier, H.265 before H.264, which this project
#: measured too (`config.PROFILES`: 24.5 against 27.7 MB/min at `balanced`).
#:
#: `*_mf` is Media Foundation, and it earns its place on the same measurement: it takes
#: ddagrab's d3d11 surface directly and let Windows dispatch to whatever hardware the
#: machine has - `MFT name: 'NVIDIA HEVC Encoder MFT'`, 59.3 fps, 0.6 s of CPU. It sits
#: BELOW the vendor families, so a machine whose own family works keeps it, and above
#: libx264, so a machine whose family does not work still gets hardware. That is the
#: whole answer to "any PC, any graphics card" from a desk with one NVIDIA card on it.
#:
#: AV1 is last on purpose and `best()` never chooses it: its `cq` ladder and MB/minute
#: are not measured, so picking it would break `config.Profile`'s own promise, and a
#: screen recording is made to be sent to somebody.
CANDIDATES: tuple[tuple[str, str, str], ...] = (
    ("hevc_nvenc", "H.265", "NVIDIA"),
    ("h264_nvenc", "H.264", "NVIDIA"),
    ("hevc_amf", "H.265", "AMD"),
    ("h264_amf", "H.264", "AMD"),
    ("hevc_qsv", "H.265", "Intel"),
    ("h264_qsv", "H.264", "Intel"),
    ("hevc_mf", "H.265", "Windows"),
    ("h264_mf", "H.264", "Windows"),
    ("av1_nvenc", "AV1", "NVIDIA"),
    ("av1_amf", "AV1", "AMD"),
    ("av1_qsv", "AV1", "Intel"),
    ("libx264", "H.264", "CPU"),
)

#: The families in the order §2 of `docs/ENCODERS.md` ranks them, read straight off
#: `CANDIDATES` rather than written out again, so that reordering that table reorders the
#: choice and there is no second list to keep in step. Today: nvenc, amf, qsv, mf, cpu.
FAMILY_ORDER: tuple[str, ...] = tuple(
    dict.fromkeys(encoders.family_of(name) for name, _codec, _vendor in CANDIDATES)
)

#: Where each name sits in `CANDIDATES`, so a list of probe rows handed in in any order
#: is ranked by the table instead of by the order of the list.
_RANK: dict[str, int] = {
    name: index for index, (name, _codec, _vendor) in enumerate(CANDIDATES)
}

#: The one encoder every ffmpeg build has, and the answer when nothing else worked.
LAST_RESORT = "libx264"

_CODEC_LABEL: dict[str, str] = {"hevc": "H.265", "h264": "H.264", "av1": "AV1"}

#: Why each family sits where it does, in words a log line or a status line can carry.
#: These are §2 of `docs/ENCODERS.md` in one clause each.
_TIER_PHRASE: dict[str, str] = {
    "nvenc": "encodes the captured frame without moving it",
    "amf": "encodes the captured frame without moving it",
    "qsv": "remaps the captured frame on the same adapter",
    "mf": "lets Windows hand the frame to whatever hardware encoder this machine has",
    "cpu": "copies every frame into system memory and encodes it on the processor",
}

#: What a hardware encoder's tier phrase becomes when the chain brings the frame back
#: anyway. The encoder is still the hardware one; what costs is the copy.
_DOWNLOAD_PHRASE = "copies every frame into system memory before the encoder takes it"


def _path_phrase(name: str, pix_fmt: str) -> str:
    """What really happens to the frame on the way to this encoder, for this profile.

    Asked of `encoders.frame_path`, not of the family, because the family only says what
    *can* happen. `hevc_nvenc` takes the d3d11 surface as it is for a 4:2:0 profile and
    is handed `hwdownload,format=bgra,format=yuv444p` for the 4:4:4 one - the same
    encoder, with every frame copied into system memory on the way, because no hardware
    encoder here takes 4:4:4 off a d3d11 surface. `choose` used to answer "encodes the
    captured frame without moving it" for the second, which is a sentence this program
    can check against the chain it is about to build, and was getting wrong.
    """
    family = encoders.family_of(name)
    if encoders.frame_path(name, pix_fmt=pix_fmt).startswith("hwdownload"):
        # The cpu phrase already says both halves - the copy and the processor.
        return _TIER_PHRASE["cpu"] if family == "cpu" else _DOWNLOAD_PHRASE
    return _TIER_PHRASE.get(family, family)


def _skipped(missing: list[str], av1_only: list[str], no_444: list[str]) -> list[str]:
    """The families that were passed over, each with the true reason it was.

    `av1_only` is its own answer and not a kind of `missing`: a family whose only
    working encoder is AV1 did work, and was passed over because `_ranked` never offers
    AV1 automatically. Reporting it as "did not work here" blames the machine for a
    decision this program made.
    """
    notes: list[str] = []
    if missing:
        notes.append(", ".join(missing) + " did not work here")
    if av1_only:
        works = "works" if len(av1_only) == 1 else "work"
        notes.append(
            ", ".join(av1_only)
            + f" {works} only in AV1, which is never chosen automatically"
        )
    if no_444:
        notes.append(", ".join(no_444) + " cannot do 4:4:4")
    return notes


def _codec_note(wanted: str, family: str) -> str:
    """Why the family's own codec was not the one taken.

    Two different sentences, because there are two different reasons and the old one
    said the wrong one for the `cpu` family: "no working H.265 encoder in cpu" reads as
    "your processor's H.265 failed its probe", when `libx265` is not a candidate of this
    program at all and nothing of the sort was ever tried.
    """
    label = _CODEC_LABEL.get(wanted, wanted)
    if not encoders.name_for(wanted, family):
        return f"this program has no {label} encoder for the {family} family"
    return f"{family}'s {label} encoder did not work here"


#: How a row was arrived at, kept because the two are not the same measurement.
#: `ddagrab` hands the encoder a d3d11 surface; `testsrc` hands it system memory, and for
#: `qsv` and `amf` that is a different filter chain altogether - no `hwmap`, no surface.
#: A probe that ran under a remote session with no console, in session 0, or with Desktop
#: Duplication blocked falls back silently, and the answer is then cached and believed
#: until the ffmpeg build string changes. Which source it was has to travel with it.
PROBED_FROM_SCREEN = "screen"
PROBED_FROM_MEMORY = "memory"
#: Nothing was run at all: ffmpeg does not list the name, or could not be asked.
NOT_PROBED = ""


@dataclass(frozen=True)
class EncoderResult:
    name: str
    codec: str
    vendor: str
    listed: bool  # the binary knows the name
    works: bool  # it encoded a frame
    reason: str  # why not, when it did not
    millis: int
    #: One of PROBED_FROM_SCREEN / PROBED_FROM_MEMORY / NOT_PROBED. Defaulted so that a
    #: caller building a row by hand - every test that fakes a machine - need not know
    #: about it; `probe` always sets it.
    source: str = PROBED_FROM_SCREEN

    @property
    def hardware(self) -> bool:
        return self.vendor != "CPU"


def _run(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )


def _listed(ffmpeg: str) -> tuple[set[str], bool]:
    """(the video encoder names this build has, whether ffmpeg answered at all).

    The second half is not decoration. An ffmpeg that will not start, or that exits
    non-zero, yields the same empty set as a build with no encoders in it, and `probe`
    then wrote "this ffmpeg was not built with it" against all twelve candidates - a
    sentence about the ffmpeg build for a machine whose real trouble is that no ffmpeg
    ran. The return code was not looked at at all.
    """
    try:
        result = _run([ffmpeg, "-hide_banner", "-encoders"])
    except (OSError, subprocess.SubprocessError):
        return set(), False
    if result.returncode != 0:
        return set(), False
    found: set[str] = set()
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            found.add(parts[1])
    return found, True


def listed_encoders(ffmpeg: str) -> set[str]:
    """What this build says it has. Empty also when ffmpeg could not be asked, which is
    why `probe` uses `_listed` and can tell the two apart."""
    return _listed(ffmpeg)[0]


#: The capture the recorder really uses, as a probe source. `output_idx=0` because the
#: probe is asking what this machine's encoders can do, not what one monitor can.
SCREEN_SOURCE = ("-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=30")

#: The stand-in for a machine with no desktop to duplicate - a service, session 0, a
#: remote session with no console. Software frames, which is not the same pipeline, so
#: every row says which of the two measured it in `EncoderResult.source`.
MEMORY_SOURCE = ("-f", "lavfi", "-i", "testsrc=size=640x360:rate=30")

#: Said by ffmpeg whether the encoder is absent, unsupported by the hardware, or simply
#: given a pixel format it will not take. Useless on its own, which is why a more
#: specific line is preferred when there is one.
_VAGUE = "nothing was written into output file"


def screen_available(ffmpeg: str) -> bool:
    """Whether `ddagrab` can duplicate this desktop at all.

    Asked once, before the candidates, because if the answer is no then every hardware
    encoder would fail the probe for a reason that has nothing to do with the encoder,
    and the machine would be told it must record on the processor.
    """
    try:
        result = _run([ffmpeg, "-hide_banner", "-loglevel", "error",
                       *SCREEN_SOURCE, "-frames:v", "2", "-f", "null", "-"],
                      timeout=20.0)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


#: The lines ffmpeg prints *after* the thing that actually went wrong. Read off this
#: machine's own refusals on 2026-11-04 - `ddagrab` into each failing candidate, stderr
#: kept whole. `hevc_qsv`:
#:
#:     [QSV] Error creating a MFX session: -9.                      <- the cause
#:     [Parsed_hwmap_0] Failed to created derived device context: -1313558101.
#:     [Parsed_hwmap_0] Failed to configure output pad on Parsed_hwmap_0
#:     [vf#0:0] Error reinitializing filters!
#:     [vf#0:0] Task finished with error: Unknown error occurred
#:     [vf#0:0] Terminating thread with error: Unknown error occurred
#:     [vost#0:0/hevc_qsv] Could not open encoder before EOF
#:     [vost#0:0/hevc_qsv] Task finished with error: Invalid argument
#:     [vost#0:0/hevc_qsv] Terminating thread with error: Invalid argument  <- was kept
#:     [out#0/null] Nothing was written into output file, because ...
#:
#: `hevc_amf` answers "DLL amfrt64.dll failed to open" and `av1_nvenc` "Provided device
#: doesn't support required NVENC features", both on the first line, both followed by the
#: same cascade. The cause is first and the noise is last, which is why all seven
#: refusals in this machine's cache read "Terminating thread with error: Invalid
#: argument" and said nothing about AMF, QSV or NVENC.
_CASCADE: tuple[str, ...] = (
    "terminating thread with error",
    "task finished with error",
    "error sending frames to consumers",
    "error reinitializing filters",
    "failed to configure output pad",
    "error while opening encoder",
    "could not open encoder before eof",
    _VAGUE,
)


def _reason(stderr: str, returncode: int) -> str:
    """What ffmpeg said went wrong, which is the **first** line it printed, not the last.

    Every refusal used to be filed under "Nothing was written into output file", which
    is the sentence ffmpeg prints for a missing encoder, for hardware that cannot do it,
    and for a pixel format the encoder silently will not take. Those are three different
    answers and the cache recorded one. The Media Foundation defect hid behind exactly
    this for a day. Dropping that one sentence and keeping the last of the rest only
    moved the problem along the cascade: see `_CASCADE` for the measured shape of a real
    refusal. This now matters to a user and not only to a log, because the settings
    panel puts this string in front of them as evidence.
    """
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    if not lines:
        return f"exit {returncode}"
    for line in lines:
        low = line.lower()
        if not any(piece in low for piece in _CASCADE):
            return line[:200]
    # Nothing but the cascade. `av1_mf` here prints only "Could not open encoder before
    # EOF", and a Media Foundation encoder handed yuv420p prints only "Nothing was
    # written into output file" - the first of those is still worth more than the last.
    specific = [line for line in lines if _VAGUE not in line.lower()]
    return (specific[0] if specific else lines[-1])[:200]


def try_encoder(ffmpeg: str, name: str, from_screen: bool = True) -> tuple[bool, str, int]:
    """Encode two frames **through the pipeline the recorder would build**.

    Not through a convenient one. Until this was done the probe encoded `testsrc` from
    system memory while the recorder handed the encoder a d3d11 surface off `ddagrab` -
    two different pipelines, so a pass here meant very little there. It is not
    hypothetical in either direction: Media Foundation accepts software nv12 and this
    build's `hwupload` d3d11 frames are refused by it, while the real `ddagrab` surface
    is accepted. Only the real source answers the real question.

    `from_screen=False` is the fallback for a machine with no desktop to duplicate. It
    tests what it can, and `probe` records that it had to in `EncoderResult.source`.

    **One chain out of four is what gets tested.** The question here is profile-blind,
    so it always builds `encoders.frame_path(name)` - no rotation, `yuv420p`, no forced
    download. A portrait screen, a composite layout and a 4:4:4 profile each take the
    `hwdownload` path instead, and nothing probes that. Run by hand here on 2026-11-04,
    two frames each off the real `ddagrab`:
    `hwdownload,format=bgra,format=yuv420p -> hevc_nvenc` exits 0, and the same chain at
    `format=yuv444p` with `-profile:v rext` exits 0. For `amf` and `qsv` that path is
    unmeasured and not measurable here - this machine has an RTX 3070 Ti and no AMD or
    Intel graphics.
    """
    try:
        codec = encoders.codec_args(
            name, quality=23, framerate=30, pix_fmt="yuv420p"
        )
    except encoders.UnknownEncoder as exc:
        return False, str(exc)[:200], 0

    family = encoders.family_of(name)
    args = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if from_screen:
        args += [*SCREEN_SOURCE, "-frames:v", "2"]
        chain = encoders.frame_path(name)
    else:
        args += [*MEMORY_SOURCE, "-frames:v", "2"]
        # From system memory there is no d3d11 surface to hand anybody, so every family
        # is on the same footing - except Media Foundation, which takes nv12 and only
        # nv12 from there. See `encoders.MF_PIXEL_FORMAT`.
        chain = "format=" + encoders.MF_PIXEL_FORMAT if family == "mf" else ""
    if chain:
        args += ["-vf", chain]
    args += codec
    # No preset of the probe's own. `codec_args` has already chosen the recorder's
    # (`-preset veryfast` for the cpu family), and appending `-preset ultrafast` after it
    # made the probe's command differ from the recorder's for the one family where the
    # preset is the whole cost - last wins, so it also silently overrode it. The reason
    # given for it does not survive measurement: two 2560x1440 frames through the real
    # chain, three runs each, here on 2026-11-04, ultrafast 472 / 562 / 481 ms against
    # veryfast 515 / 421 / 423 ms. It bought nothing.
    args += ["-f", "null", "-"]

    began = time.monotonic()
    try:
        result = _run(args, timeout=40.0)
    except subprocess.TimeoutExpired:
        return False, "the encoder did not answer within 40 s", 40000
    except OSError as exc:
        return False, f"ffmpeg could not be run: {exc}", 0
    millis = int((time.monotonic() - began) * 1000)
    if result.returncode == 0:
        return True, "", millis
    return False, _reason(result.stderr, result.returncode), millis


def ffmpeg_build(ffmpeg: str) -> str:
    """The build string, so a cached probe can be invalidated when ffmpeg changes."""
    try:
        result = _run([ffmpeg, "-hide_banner", "-version"], timeout=20.0)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    first = (result.stdout or "").strip().splitlines()
    return first[0][:120] if first else "unknown"


def cache_path() -> Path:
    return paths.config_dir() / "encoders.json"


def load_cached(ffmpeg: str) -> list[EncoderResult] | None:
    target = cache_path()
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION:
        return None
    if raw.get("build") != ffmpeg_build(ffmpeg):
        return None  # a different ffmpeg; the answer may differ
    try:
        return [EncoderResult(**row) for row in raw.get("encoders", [])]
    except TypeError:
        return None


def save_cache(ffmpeg: str, results: list[EncoderResult]) -> str:
    target = cache_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "version": CACHE_VERSION,
                    "build": ffmpeg_build(ffmpeg),
                    "encoders": [asdict(r) for r in results],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        return f"The encoder probe could not be saved: {exc}"
    return ""


def probe(ffmpeg: str, use_cache: bool = True, on_step=None) -> list[EncoderResult]:
    """Every candidate, with whether it actually encoded a frame.

    `on_step(name, index, total)` is called before each attempt, so a caller can say
    what is happening rather than freezing for a couple of seconds.
    """
    if use_cache:
        cached = load_cached(ffmpeg)
        if cached is not None:
            return cached

    listed, answered = _listed(ffmpeg)
    # Asked once rather than per candidate: without a desktop every hardware encoder
    # would fail for a reason that is not about the encoder.
    on_screen = screen_available(ffmpeg)
    source = PROBED_FROM_SCREEN if on_screen else PROBED_FROM_MEMORY
    results: list[EncoderResult] = []
    for index, (name, codec, vendor) in enumerate(CANDIDATES):
        if on_step is not None:
            try:
                on_step(name, index, len(CANDIDATES))
            except Exception:
                # A caller's progress hook must never be able to abandon the probe.
                pass
        if name not in listed:
            # Two different answers, and giving the first when the second is true tells a
            # machine whose ffmpeg will not start that its hardware is at fault.
            results.append(
                EncoderResult(
                    name, codec, vendor, False, False,
                    "this ffmpeg was not built with it" if answered
                    else "this ffmpeg could not be asked what it was built with",
                    0, NOT_PROBED,
                )
            )
            continue
        works, reason, millis = try_encoder(ffmpeg, name, from_screen=on_screen)
        results.append(
            EncoderResult(name, codec, vendor, True, works, reason, millis, source)
        )
    save_cache(ffmpeg, results)
    return results


def working(results: list[EncoderResult]) -> list[EncoderResult]:
    return [r for r in results if r.works]


class ProfileLike(Protocol):
    """The three fields of `config.Profile` that choosing an encoder depends on.

    Spelled out as a protocol rather than imported, so this module goes on knowing
    nothing about settings or the interface, and so a test can hand `choose` a one-off
    profile without building a real one.
    """

    codec: str
    pix_fmt: str
    software_only: bool


def _ranked(results: list[EncoderResult]) -> list[EncoderResult]:
    """Everything that works, never AV1, in `CANDIDATES` order.

    **AV1 is never chosen automatically**, however well it probes. Its `cq` ladder and
    its MB/minute were never measured, and `config.Profile` promises the viewer that
    those numbers are measured rather than guessed; a recording is also made to be sent
    to somebody, and AV1 is the one codec their machine may not open. It stays available
    as a deliberate choice, exactly like 4:4:4.
    """
    usable = [r for r in working(results) if not encoders.is_av1(r.name)]
    usable.sort(key=lambda r: _RANK.get(r.name, len(_RANK)))
    return usable


def choose(results: list[EncoderResult], profile: ProfileLike) -> tuple[str, str]:
    """The encoder name for one profile on this machine, and a short English reason.

    This is what a profile's `codec` becomes. The ranking is §2 of
    `docs/ENCODERS.md` - rank by how far the frame has to travel, not by who made the
    card - and it is taken from `FAMILY_ORDER`, which is `CANDIDATES` itself.

    Three rules, in this order:

    * a `software_only` profile gets the `cpu` family and nothing else. It exists
      *because* the processor was wanted; substituting hardware into it is the bug this
      function was written to remove, and it was a real one - "No GPU - x264" recorded
      on NVENC.
    * a `yuv444p` profile is only ever given an encoder that really takes `yuv444p`
      (`encoders.supports_444`, measured). Nothing else is offered a downgrade in
      silence.
    * otherwise the first family that has anything working wins, and inside it the
      profile's own codec if there is a name for it, else whatever else that family
      has - within the family first, then on to the next one.

    The reason is a short English phrase for a log line or a status line, naming the
    fallbacks that were taken. It is **not** a translated interface string: whoever puts
    it in front of a user owns that, because it is assembled from family names here.

    **Every clause in it has to be true on a machine nobody here owns**, which is the
    whole point of the module. The one that was not: reaching the end of the loop used
    to produce "nothing on this machine passed the encoder probe" whatever had happened,
    so a machine whose NVENC works but whose `libx264` failed its probe, and a machine
    whose only working encoder is AV1, were both told their probe found nothing.
    """
    if not results:
        return LAST_RESORT, (
            f"{LAST_RESORT}: there is no probe result for this machine, and this is the "
            f"one encoder every ffmpeg build has"
        )

    ranked = _ranked(results)
    #: Working, and dropped by `_ranked` only for being AV1 - which is this program's
    #: decision, not the machine's, and must not be reported as a failure.
    av1_working = {r.name for r in working(results) if encoders.is_av1(r.name)}
    wanted = profile.codec
    needs_444 = profile.pix_fmt == "yuv444p"

    notes: list[str] = []
    if profile.software_only:
        families: tuple[str, ...] = ("cpu",)
        notes.append("the profile asks for it, not the probe")
    else:
        families = FAMILY_ORDER

    missing: list[str] = []  # families with nothing working at all
    av1_only: list[str] = []  # families whose only working encoder is AV1
    no_444: list[str] = []  # families that work and cannot do 4:4:4
    for family in families:
        here = [r.name for r in ranked if encoders.family_of(r.name) == family]
        if not here:
            if any(encoders.family_of(n) == family for n in av1_working):
                av1_only.append(family)
            else:
                missing.append(family)
            continue
        if needs_444:
            able = [name for name in here if encoders.supports_444(name)]
            if not able:
                no_444.append(family)
                continue
            here = able

        notes += _skipped(missing, av1_only, no_444)
        name = encoders.name_for(wanted, family)
        if name not in here:
            # Within the family first: a machine whose H.265 encoder failed its probe
            # but whose H.264 one passed stays on the fast path.
            name = here[0]
            notes.append(_codec_note(wanted, family))
        notes.append(_path_phrase(name, profile.pix_fmt))
        return name, f"{name}: " + "; ".join(notes)

    # Nothing eligible anywhere. That includes `libx264` itself: the cpu family is the
    # last one tried and it takes 4:4:4, so reaching here means `libx264` did not pass
    # its own probe. It is still returned, because there is nothing else in every ffmpeg
    # build - but the reason now says which of those two things happened, instead of one
    # fixed sentence for all of them. `cpu` is left out of the list below because the
    # last clause says it in words.
    notes += _skipped([f for f in missing if f != "cpu"], av1_only, no_444)
    probed = any(r.name == LAST_RESORT for r in results)
    notes.append(
        (f"{LAST_RESORT} did not pass the probe here either" if probed
         else f"{LAST_RESORT} was not probed here")
        + ", and it is the last resort because every ffmpeg build has it"
    )
    return LAST_RESORT, f"{LAST_RESORT}: " + "; ".join(notes)


def best(results: list[EncoderResult], prefer_hardware: bool = True) -> str:
    """The encoder "Automatic" should use when no profile is in hand.

    `choose` is the one to call when there is a profile: it knows about the codec, about
    4:4:4 and about `software_only`, none of which this can see, and it is what both the
    recorder and the settings panel ask. **Nothing in `app/` or `tools/` calls this one**
    - checked on 2026-11-04, after the override in `ui/app.py` that used to was removed.
    It is kept for the codec-blind question, "what would this machine record on at all",
    and because `tests/test_app.py` pins that the override is gone. There is no probe
    window; an earlier version of this paragraph said there was.

    AV1 is never returned and `libx264` is the floor; `_ranked` says why.
    """
    ranked = _ranked(results)
    if prefer_hardware:
        hardware = [r for r in ranked if r.hardware]
        if hardware:
            return hardware[0].name
    return ranked[0].name if ranked else LAST_RESORT


def summary(results: list[EncoderResult]) -> str:
    """One line naming what is usable rather than what exists.

    Like `best`, it has no caller in `app/` or `tools/` as of 2026-11-04: the settings
    panel asks `choose` once per profile instead, which can answer for 4:4:4 and for the
    software profile, and this cannot.
    """
    usable = working(results)
    if not usable:
        return "No encoder on this machine could encode a test frame."
    hardware = [r for r in usable if r.hardware]
    if hardware:
        vendor = hardware[0].vendor
        codecs = ", ".join(dict.fromkeys(r.codec for r in hardware))
        return f"{vendor} hardware encoding available: {codecs}."
    return "No hardware encoder works here; recording will use the processor."


def rejected(results: list[EncoderResult]) -> list[EncoderResult]:
    """Listed by ffmpeg and refused by the machine - the trap this module exists for."""
    return [r for r in results if r.listed and not r.works]
