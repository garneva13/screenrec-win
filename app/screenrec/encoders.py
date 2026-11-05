"""Which encoder family a name belongs to, what to say to it, and how far the frame moves.

One table, two readers. Before this module, `capabilities.try_encoder` built one command
and `recorder.build_command` built another, and nothing made them agree - so the probe
could report that `h264_qsv` works while the recorder quietly built `-c:v libx264` and
handed it d3d11 surfaces. Eight of the eleven probe candidates produced a command that
could not run - every name except `hevc_nvenc`, `h264_nvenc` and `libx264`. The cure is
not another `if`: both the probe and the recorder build their command from the two
functions here, `codec_args` and `frame_path`, so that what was tested is what runs.

No Qt, no settings, no I/O - like `config`, so that anything may import it.

**The ranking idea this serves** is in `docs/ENCODERS.md`: rank by how far the frame has
to travel, not by who made the card. Measured on the author's machine at 2560x1440 and
60 fps with moving content: `ddagrab -> hevc_nvenc` with no filter costs 0.6 s of CPU in
12 s, and `ddagrab -> hwdownload -> libx264` costs 21.9 s. A factor of thirty-six, and it
is the journey, not the codec.
"""
from __future__ import annotations

from typing import Sequence

#: Every encoder name the recorder can build a correct command for, and its family.
#: A name that is not here is one nobody has written arguments for, and the callers
#: below treat that as a reason to refuse or to take the slow path - never as a reason
#: to guess.
FAMILIES: dict[str, str] = {
    "h264_nvenc": "nvenc",
    "hevc_nvenc": "nvenc",
    "av1_nvenc": "nvenc",
    "h264_amf": "amf",
    "hevc_amf": "amf",
    "av1_amf": "amf",
    "h264_qsv": "qsv",
    "hevc_qsv": "qsv",
    "av1_qsv": "qsv",
    "h264_mf": "mf",
    "hevc_mf": "mf",
    "libx264": "cpu",
}

#: Families that take `ddagrab`'s d3d11 surface as it is, with no filter at all.
#: `mf` is in here on measurement, not on hope: `ddagrab -> hevc_mf -hw_encoding true`
#: encoded at 59.3 fps for 0.6 s of CPU on 2026-11-04, the same as native NVENC, and the
#: log said `MFT name: 'NVIDIA HEVC Encoder MFT'`.
DIRECT = frozenset({"nvenc", "amf", "mf"})

#: Families that can be reached by remapping the same D3D11 texture - no copy, but a
#: filter. Unverified on real Intel hardware; see `docs/ENCODERS.md` §5.
MAPPED = frozenset({"qsv"})

#: What Media Foundation must always be told. The default is false, and at the default
#: Windows hands back Microsoft's *software* MFT - a CPU encoder wearing a hardware
#: name, which is the one lie `capabilities` exists to prevent.
MF_HARDWARE = ("-hw_encoding", "true")

#: And the one pixel format it will take from system memory. Measured 2026-11-04, and it
#: is the kind of thing that is found only by trying it:
#:
#:     testsrc -> format=nv12    -> hevc_mf -hw_encoding true   exit 0
#:     testsrc -> format=yuv420p -> hevc_mf -hw_encoding true   "Nothing was written
#:                                                               into output file"
#:
#: `ffmpeg -h encoder=hevc_mf` lists `nv12 yuv420p d3d11`, so yuv420p is advertised and
#: silently produces no packets at all - the same failure text that a missing hardware
#: encoder produces. The probe rejected Media Foundation on this machine for exactly
#: this reason, which would have quietly disabled the vendor-neutral path on **every**
#: machine while looking like an honest "your hardware cannot do it".
MF_PIXEL_FORMAT = "nv12"

#: The `cq` ladder translated into Media Foundation's own quality scale.
#:
#: MF has no CQ: `-rate_control quality -quality N` takes 0..100 and the scale is
#: neither CRF nor CQ. It was measured rather than guessed, twice, and **the two
#: measurements disagree by a factor of five** - which is the finding, not a failure of
#: the measuring. 2560x1440 at 60 fps, seven seconds each, `hevc_nvenc` at cq 23 as the
#: reference:
#:
#:     scrolling text (tools/screen_content.py)   a busy desktop
#:     nvenc cq 23    71.5 MB/min                 nvenc cq 23   146.1 MB/min
#:     mf quality 87  74.0   (1.03x)              mf quality 87 709.4   (4.9x)
#:     mf quality 78  57.7   (0.81x)              mf quality 78 529.4   (3.6x)
#:     mf quality 68  41.0   (0.57x)              mf quality 68 262.3   (1.8x)
#:
#: So no single number reproduces NVENC's size, and any claim that one does would be
#: true of one screen and wrong about the next. **68 is chosen for `balanced` because
#: its two ratios, 0.57 and 1.80, have a geometric mean of 1.01** - it is as close to
#: even-handed as one number gets. The others are spaced by hand rather than fitted,
#: because the scale saturates near 100 and a fit through saturation is a fit through
#: nothing.
#:
#: This is a starting point and it is written down as one. The real answer is §5 of
#: `docs/ENCODERS.md`: measure the viewer's own first recording and use that.
#:
#: The same numbers serve `h264_mf`, which will write a larger file for the same
#: setting - as `config.PROFILES` already accepts for `h264_nvenc` against `hevc_nvenc`
#: at the same named level (27.7 against 24.5 MB/min at `balanced`).
_MF_QUALITY: tuple[tuple[int, int], ...] = (
    (16, 80),
    (19, 75),
    (23, 68),
    (27, 50),
)


#: What NVENC is actually asked for. Deliberately short, and that is a measured decision,
#: not laziness.
#:
#: The obvious "modern quality" set - ``-preset p6 -bf 3 -b_ref_mode middle
#: -rc-lookahead 32 -spatial-aq 1 -temporal-aq 1 -multipass fullres`` - was built, shipped
#: for an afternoon, and then measured on a live 2560x1440 capture at 60 fps:
#:
#:     plain p5                 59.7 fps   3.1 MB / 20 s
#:     tuned p6, multipass      51.1 fps   3.7 MB / 20 s
#:     tuned p6, multipass qres 54.1 fps   4.2 MB / 20 s
#:     tuned p5, no multipass   54.7 fps   4.8 MB / 20 s
#:
#: It cost 14% of the frames and produced a *larger* file, and on the 4:2:0 benchmark its
#: quality was indistinguishable (SSIM Y 0.99991 against 0.99981, chroma marginally
#: worse). A screen recorder must not drop frames to buy nothing. The real gains are the
#: codec and the chroma format, which `config.PROFILES` chooses; those were measured too
#: and they are large.
NVENC_SETTINGS = [
    "-preset", "p5",
    "-tune", "hq",
    "-rc", "vbr",
    "-b:v", "0",
]


class UnknownEncoder(ValueError):
    """A name no one has written arguments for. Never raised for a name in FAMILIES."""


def family_of(name: str) -> str:
    """`''` for a name nobody has measured. Callers must treat that as "take the slow
    path" or "refuse", depending on whether being wrong costs a recording."""
    return FAMILIES.get(name, "")


def is_av1(name: str) -> bool:
    return name.startswith("av1")


def codec_of(name: str) -> str:
    """"hevc", "h264", "av1", or "" - the codec, with the family stripped off."""
    if name == "libx264":
        return "h264"
    for codec in ("hevc", "h264", "av1"):
        if name.startswith(codec):
            return codec
    return ""


#: codec -> family -> the ffmpeg name, derived from `FAMILIES` so that adding a name to
#: one table can never leave the other disagreeing. A pair that is absent is a pair this
#: program has no name for, and `name_for` returns `''` rather than inventing one. The
#: absence that matters is `("hevc", "cpu")`: `libx265` is not a candidate, so a machine
#: with no working hardware encoder records H.264 on the processor, not H.265.
_NAME_FOR: dict[tuple[str, str], str] = {
    (codec_of(name), family): name for name, family in FAMILIES.items()
}


def name_for(codec: str, family: str) -> str:
    """The ffmpeg encoder name for one codec in one family, or `''` when there is none.

    `("hevc", "nvenc")` is `"hevc_nvenc"`, `("h264", "cpu")` is `"libx264"`, and
    `("hevc", "cpu")` is `''`. A `config.Profile` names a codec and nothing more; this
    is what turns that into a name `codec_args` will accept, once the probe has said
    which family this machine can actually run.
    """
    return _NAME_FOR.get((codec, family), "")


#: Which encoders really take `yuv444p` - the 4:4:4 profile's whole reason to exist.
#:
#: Measured on 2026-11-04 by asking this build itself, `ffmpeg -h encoder=NAME`, for all
#: twelve names in `FAMILIES`:
#:
#:     h264_nvenc hevc_nvenc av1_nvenc   yuv420p nv12 p010le YUV444P nv24 p016le ...
#:     libx264                           yuv420p yuvj420p yuv422p yuvj422p YUV444P ...
#:     h264_amf hevc_amf av1_amf         nv12 yuv420p d3d11 dxva2_vld p010le amf bgr0 ...
#:     h264_qsv                          nv12 qsv
#:     av1_qsv                           nv12 p010le qsv
#:     hevc_qsv                          nv12 p010le p012le yuyv422 y210le qsv bgra ...
#:     h264_mf hevc_mf                   nv12 yuv420p d3d11
#:
#: So it is **not** "NVENC only", which is what the override in `ui/app.py` assumed when
#: it restricted itself to `yuv420p`: `libx264` does 4:4:4 as well, and that is what
#: keeps the 4:4:4 profile usable on a machine with no hardware encoder at all.
#:
#: **What AMF, QSV and Media Foundation do not list is `yuv444p`** - and that is the
#: claim, narrower than the one this comment used to make. They are not chroma-starved
#: across the board: `*_amf` advertises `bgr0 rgb0 bgra argb rgba x2bgr10le rgbaf16le`,
#: and `hevc_qsv` advertises `bgra x2rgb10le vuyx xv30le`, of which `vuyx` and `xv30le`
#: are packed 4:4:4. None of that is reachable from here, because `frame_path` builds
#: `hwdownload,format=bgra,format=yuv444p` and `yuv444p` is the format those encoders
#: will not take; whether they would encode a packed 4:4:4 frame instead is untested and
#: untestable on this desk - RTX 3070 Ti, no AMD and no Intel graphics. Saying "no 4:4:4
#: format of any kind" claimed to have ruled that out.
#:
#: `av1_qsv` shared `h264_qsv`'s row when this table was first taken down; asked again
#: on 2026-11-04 it answers `nv12 p010le qsv`, which is not the same list. It changes
#: nothing here - neither lists `yuv444p` - and it is corrected because a measurement
#: comment with a wrong number in it is worth less than none.
#: `tests/test_encoders.py` now asks this build the same question, for all twelve names.
#:
#: The advertised list is not taken on trust - `MF_PIXEL_FORMAT` above is this project's
#: standing proof that the build advertises a format it will not encode. So two frames
#: of the real `ddagrab` pipeline were encoded at `yuv444p` here on 2026-11-04:
#: `hevc_nvenc`, `h264_nvenc` and `libx264` all exit 0, and `hevc_mf` handed a real
#: `yuv444p` frame says `Task finished with error: Invalid argument` and writes no
#: packets. AMF and QSV could not be tried at all - this machine has an RTX 3070 Ti and
#: no AMD or Intel graphics - so their rows above are what the build advertises and
#: nothing more.
#:
#: Why a caller has to ask instead of letting the filter chain sort it out: `frame_path`
#: quietly rewrites `yuv444p` to `nv12` for the `mf` family, because from system memory
#: that is the only thing Media Foundation takes. The command then runs, the file plays,
#: and three quarters of the colour samples the profile was chosen for are gone. An
#: unavailable profile is better than a silent downgrade.
YUV444_ENCODERS = frozenset({"h264_nvenc", "hevc_nvenc", "av1_nvenc", "libx264"})


def supports_444(name: str) -> bool:
    """Whether this encoder really takes `yuv444p`. See `YUV444_ENCODERS` for how the
    list was measured and which two families nobody here could measure."""
    return name in YUV444_ENCODERS


def mf_quality(quality: int) -> int:
    """A `cq` number on the Media Foundation scale, interpolated between `_MF_QUALITY`.

    Only this family needs it. Everything else is given the ladder itself, because
    everything else has a constant-quality mode that means what it says.
    """
    points = _MF_QUALITY
    if quality <= points[0][0]:
        return points[0][1]
    if quality >= points[-1][0]:
        return points[-1][1]
    for (low_q, low_mf), (high_q, high_mf) in zip(points, points[1:]):
        if low_q <= quality <= high_q:
            span = high_q - low_q
            share = (quality - low_q) / span if span else 0.0
            return int(round(low_mf + (high_mf - low_mf) * share))
    return points[-1][1]


def codec_args(
    encoder: str,
    *,
    quality: int,
    framerate: int,
    pix_fmt: str = "yuv420p",
    extra: Sequence[str] = (),
) -> list[str]:
    """The `-c:v …` arguments for one encoder. Raises `UnknownEncoder` for a stranger.

    Refusing is the point. The old code returned `libx264` arguments for every name it
    did not recognise, which is how a probe that said "QSV works here" became a
    recording that died on `Impossible to convert between the formats`. A loud failure
    on a name nobody wrote arguments for is a bug report; a quiet substitution is a
    recording somebody has already lost.

    `quality` is always the `cq` ladder's own number, and what becomes of it differs per
    family. Worth stating plainly, because this paragraph used to claim that "no number
    is ever carried across a family boundary" four lines above the code that carries it
    across three of them - `codec_args("hevc_amf", quality=23, ...)` really does emit
    `-qvbr_quality_level 23`:

    * `nvenc` `-cq` and `cpu` `-crf` get the number as it stands. `docs/ENCODERS.md` §5
      puts that as "NVENC CQ and x264 CRF happen to be close", and the ladders in
      `config.PROFILES` were measured on exactly those two.
    * `amf` `-qvbr_quality_level` and `qsv` `-global_quality` get it as it stands too,
      into scales nobody here has measured - this machine has an RTX 3070 Ti and no AMD
      or Intel graphics. Both are 0..51 like CQ, which is the only reason the same
      ladder can drive them at all. §5's refusal - "do not map quality scales between
      families" - is honoured in the sense that nothing is *converted*; it is not
      honoured in the sense that the curves are assumed to be alike.
    * `mf` is translated, by `mf_quality`: its scale is 0..100 and runs the other way,
      so passing the number through would ask for near-lossless. That translation was
      measured twice and the two measurements disagree by a factor of five -
      `_MF_QUALITY` says what was chosen and why.

    What is never done is carrying a number between *codecs* inside the AMF family: the
    integers behind `-quality` differ per codec and are not even monotonic, and `-qp_i`
    reaches 255 on `av1_amf` against 51 on the others. See the AMF branch.
    """
    family = family_of(encoder)
    if not family:
        known = ", ".join(sorted(FAMILIES))
        raise UnknownEncoder(f"No arguments are written for {encoder!r}. Known: {known}.")
    gop = max(1, int(framerate) * 2)
    tail = list(extra)

    if family == "nvenc":
        # Unchanged, and measured: the p6/multipass tuning was built, measured and
        # thrown away for costing 14% of the frames - see docs/HANDOVER.md.
        return [
            "-c:v", encoder,
            *NVENC_SETTINGS,
            "-cq", str(quality),
            *tail,
            "-g", str(gop),
        ]

    if family == "amf":
        # The option *names* are the same across the three AMF encoders and the
        # *numbers* behind them are not, and are not even monotonic: `-quality` runs
        # -1..3 on h264_amf, -1..15 on hevc_amf and -1..100 on av1_amf, and `-qp_i`
        # reaches 255 on av1_amf against 51 on the others. So a name is passed here and
        # a number never is, and no number is ever carried between codecs.
        # `-qvbr_quality_level` is the one scale that is 0..51 on all three, which is
        # why the cq ladder can drive it.
        return [
            "-c:v", encoder,
            "-usage", "transcoding", "-quality", "balanced",
            "-rc", "qvbr", "-qvbr_quality_level", str(quality),
            *tail,
            "-g", str(gop),
        ]

    if family == "qsv":
        return [
            "-c:v", encoder,
            "-preset", "veryfast", "-global_quality", str(quality),
            *tail,
            "-g", str(gop),
        ]

    if family == "mf":
        # `-rate_control quality` is the one mode that behaves like the rest of this
        # program: it varies with the content instead of spending a fixed bitrate on a
        # still screen. Measured on 2026-11-04, same seconds of the same near-static
        # screen: 14.0 MB/min in quality mode against 89 MB/min at a fixed `-b:v 12M`.
        # `_MF_QUALITY` says what the number means and how far it can be trusted.
        return [
            "-c:v", encoder,
            *MF_HARDWARE,
            "-rate_control", "quality", "-quality", str(mf_quality(quality)),
            *tail,
            "-g", str(gop),
        ]

    if family == "cpu":
        return [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(quality),
            "-pix_fmt", pix_fmt,
            *tail,
            "-g", str(gop),
        ]

    # A name that is in FAMILIES under a family with no branch above. Falling through to
    # libx264 is exactly how the original defect worked, so it is refused instead.
    raise UnknownEncoder(
        f"{encoder!r} is filed under the family {family!r}, for which no arguments are "
        f"written."
    )


def frame_path(
    encoder: str,
    *,
    rotate: str = "",
    pix_fmt: str = "yuv420p",
    force_download: bool = False,
) -> str:
    """The filter chain between `ddagrab` and the encoder, or `''` to stay on the GPU.

    `rotate` is the transpose filter for a portrait monitor, already worked out by the
    caller (this module must not import `recorder`). A rotated screen always costs the
    trip through system memory: `vpp_qsv` can transpose but `vpp_amf` has no rotation
    option at all, and neither can be tested here.

    An **unknown** family downloads. That is the whole difference between this and the
    code it replaces: not knowing now means taking the slow, certain path, instead of
    handing a d3d11 surface to a CPU encoder and dying.
    """
    family = family_of(encoder)
    if not force_download and not rotate and pix_fmt == "yuv420p":
        if family in DIRECT:
            return ""
        if family in MAPPED:
            # `vpp_qsv=format=nv12` is not decoration. `ddagrab`'s frames context has
            # **one** sw_format and it is bgra - measured, because
            # `hwdownload,format=nv12` after it is refused outright ("Invalid output
            # format nv12 for hwframe download") while `format=bgra` succeeds. `hwmap`
            # cannot change it: its entire option list is `mode`, `derive_device`,
            # `reverse`. And of the three QSV encoders only `hevc_qsv` lists `bgra`
            # among its formats - `h264_qsv` lists `nv12 qsv` and nothing else. The
            # graph's output format is `AV_PIX_FMT_QSV`, which all three accept, so no
            # scaler is inserted and nothing notices the sw_format until the encoder
            # opens and fails. The conversion is therefore done on the GPU by QSV's own
            # filter.
            #
            # Untestable here: this machine stops a step earlier, at "Error creating a
            # MFX session: -9", which is "no Intel device", not "no such path".
            return "hwmap=derive_device=qsv,format=qsv,vpp_qsv=format=nv12"
    chain = ["hwdownload", "format=bgra"]
    if rotate:
        chain.append(rotate)
    # Media Foundation takes the d3d11 surface directly, so it reaches here only when
    # something forced the download: a rotated screen, a composite layout, or a chroma
    # format it cannot do anyway. Whatever the reason, from system memory it takes nv12
    # and nothing else - `yuv420p`, `yuv444p` and `bgra` all open the encoder and then
    # write no packets at all. See `MF_PIXEL_FORMAT`.
    wanted = MF_PIXEL_FORMAT if family == "mf" and pix_fmt != MF_PIXEL_FORMAT else pix_fmt
    chain.append("format=" + wanted)
    return ",".join(chain)
