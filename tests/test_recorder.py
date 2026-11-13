"""Geometry and the ffmpeg command builder - the two pure, high-risk parts of recorder.py.

The numbers for the rotated monitor are the ones measured on this machine (DXGI output 1,
``\\\\.\\DISPLAY1``, 1440x2560 of desktop at (2560, 0), DXGI rotation 2), so a change that
silently flips an axis fails here instead of in a recording.

Nothing in this file starts a process, touches a GPU or writes a video file. The one
thing it does touch is the disk: `Recorder.start` calls `mkdir(parents=True,
exist_ok=True)` on each output's parent, and every test here points its outputs into a
`TemporaryDirectory` that already exists, so the call finds its folder and makes
nothing.

Four defects were found by this file on 2026-11-03, all measured against the bundled
ffmpeg N-128352, and all four are fixed. The tests that found them are still here as the
guard, stated as what the builder must do rather than as what it did:

1. ``LAYOUT_SEPARATE`` with ``AUDIO_MIX`` emitted ``[aout]`` as the audio map of *both*
   output files. ffmpeg: "Output with label 'aout' does not exist in any defined filter
   graph, or was already used elsewhere." Fixed with ``asplit``.
2. ``LAYOUT_PIP`` with three or more sources built a ``[cN]`` chain per source and
   overlaid only the first two. ffmpeg: "Filter 'format:default' has output 0 (c2)
   unconnected". Fixed by refusing: one inset means two sources.
3. An unrecognised layout string - including ``LAYOUT_SINGLE`` with several sources, and
   a mis-cased ``"Side_By_Side"`` - captured every screen and mapped only the first.
   ffmpeg was perfectly happy and the user silently lost a monitor. Fixed by validating.
4. ``surface_crop`` slid a rectangle that hung off the left or top edge inwards, keeping
   its size, so the recording showed a different part of the screen than the one dragged
   out. Fixed by intersecting the selection with the monitor.

Since 2026-11-05 the file also covers the lifecycle of `Recorder` and
`RecordingSession` - three more defects, all of which reported success over something
that had not happened. Those tests still start nothing: `Recorder` is given a stand-in
for `subprocess.Popen`, and `RecordingSession` a stand-in for `Recorder`, so the branch
under test is exercised without a process, a GPU or a file. The measurements that
justify the numbers are in `recorder.py` beside the code they belong to.
"""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from screenrec import config
from screenrec import encoders
from screenrec import i18n
from screenrec import recorder as rec
from screenrec.winapi import Monitor

FFMPEG = r"B:\ScreenRec\tools\ffmpeg\ffmpeg.exe"

# DXGI 0: landscape, not rotated, primary.
LANDSCAPE = Monitor(
    index=0, device_name=r"\\.\DISPLAY2", x=0, y=0,
    width=2560, height=1440, rotation=1, adapter="NVIDIA GeForce RTX 3070 Ti",
)
# DXGI 1: the portrait screen. Desktop is 1440x2560; the duplicated surface is 2560x1440.
PORTRAIT = Monitor(
    index=1, device_name=r"\\.\DISPLAY1", x=2560, y=0,
    width=1440, height=2560, rotation=2, adapter="NVIDIA GeForce RTX 3070 Ti",
)
# Not on this machine - a hypothetical third screen, so the composed layouts can be built
# with more than the two sources they were originally written against.
THIRD = Monitor(
    index=2, device_name=r"\\.\DISPLAY4", x=-1920, y=0,
    width=1920, height=1080, rotation=1, adapter="NVIDIA GeForce RTX 3070 Ti",
)


def _value_after(args: list[str], flag: str) -> str:
    """The argument following `flag`; fails the lookup loudly if the flag is absent."""
    return args[args.index(flag) + 1]


def _maps(args: list[str]) -> list[str]:
    """Every value handed to -map, in command order."""
    return [args[i + 1] for i, a in enumerate(args) if a == "-map"]


def _filter_chain(args: list[str]) -> str:
    """The -filter_complex graph, or '' when the command has none."""
    return _value_after(args, "-filter_complex") if "-filter_complex" in args else ""


_LEADING_LABELS = re.compile(r"^(?:\[[^\]]+\])+")
_TRAILING_LABELS = re.compile(r"(?:\[[^\]]+\])+$")
_LABEL = re.compile(r"\[([^\]]+)\]")


def _graph_labels(chain: str) -> tuple[list[str], list[str]]:
    """Split a filtergraph into (labels it defines, labels it consumes).

    A chain is ``[in][in]filters[out][out]``, and the chains are joined with ';'. A
    leading label of the form ``0:v`` or ``2:a`` names an *input stream*, not something
    the graph produced, so it is not counted as consumption of a graph label.
    """
    defined: list[str] = []
    consumed: list[str] = []
    for segment in (s for s in chain.split(";") if s):
        tail = _TRAILING_LABELS.search(segment)
        head = _LEADING_LABELS.match(segment)
        if tail is not None:
            defined += _LABEL.findall(tail.group(0))
        if head is not None:
            consumed += [
                name for name in _LABEL.findall(head.group(0))
                if not re.fullmatch(r"\d+:[av]", name)
            ]
    return defined, consumed


def _mapped_labels(args: list[str]) -> list[str]:
    """The filtergraph labels the outputs consume, i.e. the bracketed -map values."""
    return [_LABEL.fullmatch(m).group(1) for m in _maps(args) if m.startswith("[")]


class EvenTest(unittest.TestCase):
    def test_odd_values_round_down(self) -> None:
        self.assertEqual(rec.even(1), 0)
        self.assertEqual(rec.even(3), 2)
        self.assertEqual(rec.even(1439), 1438)
        self.assertEqual(rec.even(2559), 2558)

    def test_even_values_are_untouched(self) -> None:
        for value in (0, 2, 4, 1440, 2560):
            with self.subTest(value=value):
                self.assertEqual(rec.even(value), value)

    def test_the_result_is_always_even(self) -> None:
        for value in range(0, 200):
            self.assertEqual(rec.even(value) % 2, 0)
            self.assertLessEqual(rec.even(value), value)


class RotationFilterTest(unittest.TestCase):
    def test_no_filter_for_an_unrotated_screen(self) -> None:
        self.assertEqual(rec.rotation_filter(0), "")  # DXGI "unspecified"
        self.assertEqual(rec.rotation_filter(1), "")  # "identity"

    def test_ninety_degrees_is_the_measured_transpose(self) -> None:
        self.assertEqual(rec.rotation_filter(2), "transpose=1")

    def test_one_hundred_and_eighty_degrees(self) -> None:
        self.assertEqual(rec.rotation_filter(3), "transpose=1,transpose=1")

    def test_two_hundred_and_seventy_degrees(self) -> None:
        self.assertEqual(rec.rotation_filter(4), "transpose=2")


class SurfaceCropTest(unittest.TestCase):
    def test_unrotated_monitor_at_the_origin_is_the_identity(self) -> None:
        self.assertEqual(rec.surface_crop(LANDSCAPE, 100, 200, 640, 480), (100, 200, 640, 480))

    def test_unrotated_monitor_is_only_a_translation(self) -> None:
        offset = Monitor(
            index=2, device_name=r"\\.\DISPLAY3", x=-1920, y=-200,
            width=1920, height=1080, rotation=1, adapter="x",
        )
        self.assertEqual(rec.surface_crop(offset, -1920 + 300, -200 + 50, 800, 600),
                         (300, 50, 800, 600))

    def test_rotated_monitor_matches_the_mapping_verified_by_capture(self) -> None:
        # Desktop rectangle at the portrait screen's top-left corner, 700 wide, 400 tall.
        self.assertEqual(rec.surface_crop(PORTRAIT, 2560, 0, 700, 400), (0, 740, 400, 700))

    def test_rotated_monitor_whole_screen(self) -> None:
        # The full desktop rectangle covers the whole surface, with the axes swapped.
        self.assertEqual(
            rec.surface_crop(PORTRAIT, 2560, 0, 1440, 2560), (0, 0, 2560, 1440)
        )

    def test_rotated_monitor_bottom_right_corner(self) -> None:
        # The desktop bottom-right corner is the surface's left edge, far along x.
        offset_x, offset_y, width, height = rec.surface_crop(
            PORTRAIT, 2560 + 1440 - 200, 2560 - 100, 200, 100
        )
        self.assertEqual((width, height), (100, 200))
        self.assertEqual(offset_x, 2560 - 100)  # local_y
        self.assertEqual(offset_y, 0)           # monitor.width - (local_x + local_w)

    def test_rotated_crop_stays_inside_the_surface(self) -> None:
        surface_w, surface_h = PORTRAIT.height, PORTRAIT.width  # 2560 x 1440
        for x, y, w, h in (
            (2560, 0, 1440, 2560),
            (2560, 0, 700, 400),
            (2560 + 700, 1200, 600, 900),
            (2560 + 1000, 2000, 400, 500),
        ):
            with self.subTest(rect=(x, y, w, h)):
                offset_x, offset_y, width, height = rec.surface_crop(PORTRAIT, x, y, w, h)
                self.assertGreaterEqual(offset_x, 0)
                self.assertGreaterEqual(offset_y, 0)
                self.assertLessEqual(offset_x + width, surface_w)
                self.assertLessEqual(offset_y + height, surface_h)

    def test_a_one_pixel_sliver_at_the_far_edge_does_not_overrun_the_surface(self) -> None:
        """Was a real bug: the two-pixel floor was applied after the clamp.

        One pixel in from the right or bottom edge the floor won and the rectangle
        reached one pixel past the duplicated surface - an offset/size pair ddagrab
        rejects. The origin is now clamped so two pixels always remain inside.
        """
        surface_w = PORTRAIT.height  # 2560
        offset_x, _, width, _ = rec.surface_crop(PORTRAIT, 2560 + 1439, 2559, 4, 4)
        self.assertLessEqual(offset_x + width, surface_w)

    def test_a_rectangle_running_off_the_right_edge_is_clamped(self) -> None:
        self.assertEqual(rec.surface_crop(LANDSCAPE, 2000, 1000, 1000, 1000),
                         (2000, 1000, 560, 440))

    def test_a_rectangle_starting_outside_the_monitor_keeps_only_the_visible_part(self):
        """DEFECT 4. Expected to fail until surface_crop intersects instead of sliding.

        A rectangle hanging off the *right* or *bottom* edge is handled correctly - see
        the test above, where 1000x1000 at (2000, 1000) becomes 560x440. Off the *left*
        or *top* edge the origin is clamped to 0 while the size is kept, so the rectangle
        slides inwards: measured today, selecting desktop x in [-500, 300) on the
        landscape screen records x in [0, 800) - an 800-pixel region 500 pixels to the
        right of the one that was dragged out, at full requested size, with nothing said
        about it. The recording simply shows the wrong part of the screen.

        The old version of this test asserted (0, 0, 800, 800) under the name "is
        clamped", which made the defect look like the specification. The expectation
        below is the intersection of the selection with the monitor, which is what
        "clamped" has to mean if the word is to be any use.

        If the sliding is ever decided to be deliberate, this is still not the fix:
        surface_crop would have to tell the caller it moved the rectangle, per the house
        rule that a thing which did not happen must be findable.
        """
        for monitor, (x, y, w, h), expected in (
            (LANDSCAPE, (-500, -500, 800, 800), (0, 0, 300, 300)),
            (LANDSCAPE, (-500, 100, 800, 200), (0, 100, 300, 200)),
            (LANDSCAPE, (100, -300, 400, 600), (100, 0, 400, 300)),
        ):
            with self.subTest(rect=(x, y, w, h)):
                self.assertEqual(rec.surface_crop(monitor, x, y, w, h), expected)

    def test_a_crop_never_covers_desktop_pixels_that_were_not_selected(self) -> None:
        """DEFECT 4 again, as the invariant rather than three worked examples.

        Stated in surface units so it holds for the rotated screen too: the returned
        size must be the overlap of the selection with the monitor, with the axes
        swapped where the panel is.
        """
        for monitor, x, y, w, h in (
            (LANDSCAPE, -500, -500, 800, 800),
            (LANDSCAPE, 100, -300, 400, 600),
            (PORTRAIT, 2560 - 400, 100, 900, 300),
            (PORTRAIT, 2560 + 100, -200, 300, 900),
        ):
            with self.subTest(monitor=monitor.index, rect=(x, y, w, h)):
                visible_w = min(monitor.x + monitor.width, x + w) - max(monitor.x, x)
                visible_h = min(monitor.y + monitor.height, y + h) - max(monitor.y, y)
                expected = ((rec.even(visible_h), rec.even(visible_w)) if monitor.rotated
                            else (rec.even(visible_w), rec.even(visible_h)))
                _, _, width, height = rec.surface_crop(monitor, x, y, w, h)
                self.assertEqual((width, height), expected)

    def test_the_result_is_always_even_and_never_leaves_the_monitor(self) -> None:
        for monitor in (LANDSCAPE, PORTRAIT):
            for x, y, w, h in (
                (monitor.x + 1, monitor.y + 3, 701, 403),
                (monitor.x, monitor.y, monitor.width, monitor.height),
                (monitor.x + 17, monitor.y + 19, 65, 33),
                (monitor.x + monitor.width - 10, monitor.y, 999, 999),
            ):
                with self.subTest(monitor=monitor.index, rect=(x, y, w, h)):
                    _, _, width, height = rec.surface_crop(monitor, x, y, w, h)
                    self.assertEqual(width % 2, 0, "ddagrab width must be even")
                    self.assertEqual(height % 2, 0, "ddagrab height must be even")
                    self.assertGreater(width, 0)
                    self.assertGreater(height, 0)


class VideoSourceTest(unittest.TestCase):
    def test_output_size_of_an_unrotated_screen(self) -> None:
        self.assertEqual(rec.VideoSource(LANDSCAPE).output_size(), (2560, 1440))

    def test_output_size_of_a_rotated_screen_is_display_orientation(self) -> None:
        # What the viewer must see is the portrait desktop, not the landscape panel.
        self.assertEqual(rec.VideoSource(PORTRAIT).output_size(), (1440, 2560))

    def test_output_size_of_a_crop_on_a_rotated_screen_is_display_orientation(self) -> None:
        source = rec.VideoSource(PORTRAIT, crop=(2560, 0, 700, 400))
        # surface_crop gives 400x700; after transpose the frame is 700x400 again.
        self.assertEqual(source.output_size(), (700, 400))

    def test_output_size_of_a_crop_on_an_unrotated_screen(self) -> None:
        source = rec.VideoSource(LANDSCAPE, crop=(10, 10, 641, 481))
        self.assertEqual(source.output_size(), (640, 480))

    def test_needs_cpu_follows_rotation(self) -> None:
        self.assertFalse(rec.VideoSource(LANDSCAPE).needs_cpu)
        self.assertTrue(rec.VideoSource(PORTRAIT).needs_cpu)

    def test_ddagrab_args_for_a_whole_screen(self) -> None:
        args = rec.VideoSource(LANDSCAPE).ddagrab_args(60, True)
        self.assertEqual(args, "ddagrab=output_idx=0:framerate=60:draw_mouse=1")

    def test_ddagrab_args_use_the_dxgi_index_not_the_list_position(self) -> None:
        args = rec.VideoSource(PORTRAIT).ddagrab_args(30, False)
        self.assertIn("output_idx=1", args)
        self.assertIn("framerate=30", args)
        self.assertIn("draw_mouse=0", args)

    def test_ddagrab_args_for_a_crop_are_in_surface_space(self) -> None:
        source = rec.VideoSource(PORTRAIT, crop=(2560, 0, 700, 400))
        args = source.ddagrab_args(60, True)
        self.assertIn("offset_x=0", args)
        self.assertIn("offset_y=740", args)
        self.assertIn("video_size=400x700", args)


def _plan(sources, outputs=None, **kwargs) -> rec.RecordingPlan:
    return rec.RecordingPlan(
        sources=list(sources),
        outputs=list(outputs if outputs is not None else [Path(r"C:\out\a.mp4")]),
        **kwargs,
    )


def _sources(count: int, rotated_first: bool = False) -> list[rec.VideoSource]:
    """`count` sources, mixing orientations so a chain for a rotated screen is included."""
    pool = [PORTRAIT, LANDSCAPE, THIRD] if rotated_first else [LANDSCAPE, PORTRAIT, THIRD]
    return [rec.VideoSource(pool[i % len(pool)]) for i in range(count)]


def _outputs(count: int) -> list[Path]:
    return [Path(rf"C:\out\screen{i}.mp4") for i in range(count)]


class GraphSoundnessMixin:
    """The rule ffmpeg enforces on a filtergraph, written down as an assertion.

    Every label the graph defines must be consumed exactly once - by a later filter or by
    exactly one output. Defining one and consuming it nowhere is "Filter ... has output 0
    (c2) unconnected"; consuming one twice is "Output with label 'aout' ... was already
    used elsewhere". Both abort ffmpeg before a single frame is written, so a command that
    breaks this rule is not a lesser recording, it is no recording at all.
    """

    def assert_graph_is_sound(self, args: list[str]) -> None:
        chain = _filter_chain(args)
        defined, consumed_in_graph = _graph_labels(chain)
        mapped = _mapped_labels(args)

        duplicates = sorted({name for name in defined if defined.count(name) > 1})
        self.assertEqual(duplicates, [], f"these labels are defined twice: {duplicates}")

        self.assertEqual(
            sorted({name for name in mapped if mapped.count(name) > 1}), [],
            "a filtergraph label is mapped to more than one output; ffmpeg refuses this "
            f"with \"already used elsewhere\". Maps: {_maps(args)}",
        )

        for name in sorted(set(defined)):
            uses = consumed_in_graph.count(name) + mapped.count(name)
            self.assertEqual(
                uses, 1,
                f"label [{name}] is defined once and consumed {uses} time(s); it must be "
                f"consumed exactly once. Graph: {chain} Maps: {_maps(args)}",
            )

        for name in sorted(set(consumed_in_graph) | set(mapped)):
            self.assertIn(
                name, defined,
                f"label [{name}] is used but nothing defines it. Graph: {chain}",
            )


def _is_known_defective(layout: str, source_count: int, mic: bool, system: bool,
                        audio_mode: str) -> bool:
    """The two builder defects that produce an unsound graph (see the module docstring).

    When one of these is repaired its ``@unittest.expectedFailure`` test starts failing as
    an unexpected success; delete the matching clause here at the same time, so the sweep
    picks the combination up for good.
    """
    # All four defects this sweep used to excuse were fixed on 2026-11-03: the mix is
    # now split with asplit, picture in picture refuses more than two sources, an unknown
    # layout raises, and surface_crop intersects instead of sliding. Nothing is excused
    # any more; the hook is kept so a future known-bad combination has somewhere to go.
    return False


class FilterGraphSoundnessTest(GraphSoundnessMixin, unittest.TestCase):
    """Sweep the combinations the UI can produce and check every graph binds.

    `LAYOUT_SINGLE` with several sources is left out on purpose - it builds a graph that
    ffmpeg accepts while quietly dropping a screen, which is defect 3 and has its own
    test rather than a soundness one.
    """

    AUDIO_CASES = (
        (False, False, config.AUDIO_MIX),
        (True, False, config.AUDIO_MIX),
        (False, True, config.AUDIO_MIX),
        (True, True, config.AUDIO_MIX),
        (True, True, config.AUDIO_SEPARATE),
    )
    MIC = r"@device_cm_{33D9A762}\wave_{113C1505}"
    PIPE = r"\\.\pipe\screenrec_1234"

    def test_every_buildable_combination_produces_a_graph_that_binds(self) -> None:
        checked = 0
        refused = 0
        for layout in (rec.LAYOUT_SINGLE, rec.LAYOUT_SEPARATE, rec.LAYOUT_SIDE_BY_SIDE,
                       rec.LAYOUT_STACKED, rec.LAYOUT_PIP):
            for count in (1, 2, 3):
                if layout == rec.LAYOUT_SINGLE and count > 1:
                    continue
                for mic, system, audio_mode in self.AUDIO_CASES:
                    if _is_known_defective(layout, count, mic, system, audio_mode):
                        continue
                    for rotated_first in (False, True):
                        for encoder in ("h264_nvenc", "libx264"):
                            with self.subTest(layout=layout, sources=count, mic=mic,
                                              system=system, audio_mode=audio_mode,
                                              rotated_first=rotated_first,
                                              encoder=encoder):
                                try:
                                    args = rec.build_command(FFMPEG, _plan(
                                        _sources(count, rotated_first),
                                        outputs=_outputs(
                                            count if layout == rec.LAYOUT_SEPARATE
                                            else 1),
                                        layout=layout, encoder=encoder,
                                        mic_device=self.MIC if mic else "",
                                        loopback_pipe=self.PIPE if system else "",
                                        audio_mode=audio_mode,
                                    ))
                                except ValueError as exc:
                                    # A refusal is a correct answer: the builder says
                                    # loudly that the combination makes no sense, which
                                    # is the opposite of the defect this sweep hunts -
                                    # a clean command that silently drops a screen. It
                                    # must still say which combination, though.
                                    self.assertTrue(
                                        str(exc).strip(),
                                        "build_command refused without saying why",
                                    )
                                    refused += 1
                                    continue
                                self.assert_graph_is_sound(args)
                                checked += 1
        self.assertGreater(checked, 100, "the sweep stopped covering combinations")
        self.assertGreater(
            refused, 0, "nothing was refused - the validation stopped working"
        )

    def test_no_filtergraph_label_is_ever_mapped_twice(self) -> None:
        """Stated on its own because it is the whole of defect 1."""
        for layout in (rec.LAYOUT_SEPARATE, rec.LAYOUT_SIDE_BY_SIDE, rec.LAYOUT_STACKED):
            for mic, system, audio_mode in self.AUDIO_CASES:
                if _is_known_defective(layout, 2, mic, system, audio_mode):
                    continue
                with self.subTest(layout=layout, audio_mode=audio_mode,
                                  mic=mic, system=system):
                    args = rec.build_command(FFMPEG, _plan(
                        _sources(2),
                        outputs=_outputs(2 if layout == rec.LAYOUT_SEPARATE else 1),
                        layout=layout,
                        mic_device=self.MIC if mic else "",
                        loopback_pipe=self.PIPE if system else "",
                        audio_mode=audio_mode,
                    ))
                    mapped = _mapped_labels(args)
                    self.assertEqual(sorted(mapped), sorted(set(mapped)), _maps(args))

    def test_an_input_stream_may_feed_several_output_files(self) -> None:
        """The counterpart: `2:a` is an input, not a label, so repeating it is legal.

        Verified against the bundled ffmpeg - two files each mapping `2:a` and `3:a`
        exit 0. The soundness rule applies to bracketed labels only.
        """
        args = rec.build_command(FFMPEG, _plan(
            _sources(2), outputs=_outputs(2), layout=rec.LAYOUT_SEPARATE,
            mic_device=self.MIC, loopback_pipe=self.PIPE,
            audio_mode=config.AUDIO_SEPARATE,
        ))
        self.assertEqual(_maps(args), ["0:v", "2:a", "3:a", "[v1]", "2:a", "3:a"])
        self.assert_graph_is_sound(args)


class BuildCommandVideoTest(unittest.TestCase):
    def test_plain_monitor_with_nvenc_has_no_filter_complex(self) -> None:
        """The measured direct GPU path: ddagrab feeds h264_nvenc with nothing between."""
        args = rec.build_command(FFMPEG, _plan([rec.VideoSource(LANDSCAPE)]))
        self.assertNotIn("-filter_complex", args)
        self.assertIn("-map", args)
        self.assertEqual(_value_after(args, "-map"), "0:v")
        self.assertEqual(_value_after(args, "-c:v"), "hevc_nvenc")

    def test_plain_monitor_with_hevc_nvenc_also_stays_on_the_gpu(self) -> None:
        args = rec.build_command(
            FFMPEG, _plan([rec.VideoSource(LANDSCAPE)], encoder="hevc_nvenc")
        )
        self.assertNotIn("-filter_complex", args)
        self.assertEqual(_value_after(args, "-c:v"), "hevc_nvenc")

    def test_libx264_forces_the_download_path(self) -> None:
        args = rec.build_command(FFMPEG, _plan([rec.VideoSource(LANDSCAPE)], encoder="libx264"))
        self.assertIn("-filter_complex", args)
        chain = _value_after(args, "-filter_complex")
        self.assertTrue(chain.startswith("[0:v]hwdownload,format=bgra"), chain)
        self.assertTrue(chain.endswith("format=yuv420p[v0]"), chain)
        self.assertEqual(_value_after(args, "-map"), "[v0]")

    def test_a_rotated_monitor_forces_the_download_path_even_on_nvenc(self) -> None:
        args = rec.build_command(FFMPEG, _plan([rec.VideoSource(PORTRAIT)]))
        self.assertIn("-filter_complex", args)
        chain = _value_after(args, "-filter_complex")
        self.assertIn("transpose=1", chain)
        self.assertTrue(chain.endswith("format=yuv420p[v0]"), chain)
        self.assertEqual(_value_after(args, "-c:v"), "hevc_nvenc")

    def test_every_filter_chain_ends_in_yuv420p(self) -> None:
        """Left alone this build writes yuv444p, which many players refuse."""
        for encoder in ("h264_nvenc", "libx264"):
            for monitor in (LANDSCAPE, PORTRAIT):
                with self.subTest(encoder=encoder, rotation=monitor.rotation):
                    args = rec.build_command(
                        FFMPEG, _plan([rec.VideoSource(monitor)], encoder=encoder)
                    )
                    if "-filter_complex" in args:
                        chain = _value_after(args, "-filter_complex")
                        self.assertTrue(chain.endswith("format=yuv420p[v0]"), chain)

    def test_stdin_is_left_open_so_q_can_stop_the_recording(self) -> None:
        args = rec.build_command(FFMPEG, _plan([rec.VideoSource(LANDSCAPE)]))
        self.assertNotIn("-nostdin", args)

    def test_the_output_file_is_last_and_overwrite_is_on(self) -> None:
        out = Path(r"C:\out\clip.mp4")
        args = rec.build_command(FFMPEG, _plan([rec.VideoSource(LANDSCAPE)], [out]))
        self.assertEqual(args[0], FFMPEG)
        self.assertEqual(args[-1], str(out))
        self.assertEqual(args[-2], "-y")

    def test_quality_and_framerate_reach_the_encoder(self) -> None:
        args = rec.build_command(
            FFMPEG, _plan([rec.VideoSource(LANDSCAPE)], quality=28, framerate=30)
        )
        self.assertEqual(_value_after(args, "-cq"), "28")
        self.assertEqual(_value_after(args, "-g"), "60")
        self.assertIn("ddagrab=output_idx=0:framerate=30:draw_mouse=1", args)

    def test_libx264_gets_crf_and_an_explicit_pixel_format(self) -> None:
        args = rec.build_command(
            FFMPEG, _plan([rec.VideoSource(LANDSCAPE)], encoder="libx264", quality=19)
        )
        self.assertEqual(_value_after(args, "-crf"), "19")
        self.assertEqual(_value_after(args, "-pix_fmt"), "yuv420p")


class RecordingSmallerThanTheScreenTest(unittest.TestCase):
    """Asking for a smaller recording.

    Measured on 2026-11-08 before any of this was written: **no GPU scaler in this
    build will take ddagrab's frames.** `scale_d3d11`, `scale_d3d12` and both behind
    `hwmap=derive_device=d3d11va` all refuse with "Unsupported pixel format: (null)".
    So a smaller picture has to come down through system memory, and the question the
    code has to get right is *when it is worth doing at all*.
    """

    def test_a_taller_screen_is_brought_down(self) -> None:
        source = rec.VideoSource(LANDSCAPE)          # 2560x1440
        self.assertEqual(rec.scale_for(source, 1080), 1080)
        chain = rec._filter_for(source, "hevc_nvenc", max_height=1080)
        self.assertIn("scale=-2:1080", chain)
        self.assertIn("hwdownload", chain)

    def test_a_screen_already_small_enough_keeps_the_direct_path(self) -> None:
        """The subtlety worth a test of its own: 1080 on a 1080p monitor must not
        download, scale by one and arrive at the same picture having paid for it."""
        small = Monitor(
            index=0, device_name=r"\.\DISPLAY9", x=0, y=0,
            width=1920, height=1080, rotation=1, adapter="NVIDIA",
        )
        source = rec.VideoSource(small)
        self.assertEqual(rec.scale_for(source, 1080), 0)
        self.assertEqual(rec._filter_for(source, "hevc_nvenc", max_height=1080), "")

    def test_exactly_equal_is_not_scaled(self) -> None:
        self.assertEqual(rec.scale_for(rec.VideoSource(LANDSCAPE), 1440), 0)

    def test_no_limit_changes_nothing(self) -> None:
        source = rec.VideoSource(LANDSCAPE)
        self.assertEqual(rec.scale_for(source, 0), 0)
        self.assertEqual(rec._filter_for(source, "hevc_nvenc", max_height=0), "")

    def test_a_portrait_screen_is_measured_after_its_rotation(self) -> None:
        """`output_size` is the finished picture, so a portrait 1440x2560 screen is
        2560 tall and does get scaled - and the scale goes after the transpose."""
        source = rec.VideoSource(PORTRAIT)
        self.assertEqual(source.output_size(), (1440, 2560))
        self.assertEqual(rec.scale_for(source, 1080), 1080)
        chain = rec._filter_for(source, "hevc_nvenc", max_height=1080)
        self.assertLess(chain.index("transpose"), chain.index("scale=-2:1080"))

    def test_the_height_reaches_the_command(self) -> None:
        plan = rec.RecordingPlan(
            sources=[rec.VideoSource(LANDSCAPE)],
            outputs=[Path("out.mp4")],
            max_height=720,
        )
        args = rec.build_command("ffmpeg", plan)
        self.assertTrue(
            any("scale=-2:720" in a for a in args),
            "the chosen height never reached ffmpeg",
        )

    def test_a_height_nobody_offers_is_refused_by_the_settings(self) -> None:
        """Through the real loading path, which is where a hand-edited settings file
        with a nonsense height arrives."""
        self.assertEqual(config._coerce({"max_height": 999}).max_height, 0)
        self.assertEqual(config._coerce({"max_height": "1080"}).max_height, 0)
        for height in config.MAX_HEIGHTS:
            self.assertEqual(config._coerce({"max_height": height}).max_height, height)


class EncoderFamilyTest(unittest.TestCase):
    """One per family, and the defect they exist for.

    Until 2026-11-04 `_video_codec_args` recognised exactly `h264_nvenc` and
    `hevc_nvenc` and fell through to libx264 arguments for every other name, while
    `_filter_for` decided whether to download by comparing against the name that was
    *asked for*. So eight of the eleven probe candidates built a command that fed d3d11
    surfaces to a CPU encoder, and ffmpeg died on "Impossible to convert between the
    formats". It never fired on the author's machine, where `best()` returns
    `hevc_nvenc`; it fired on every AMD box, every Intel box, and every RTX 40/50 -
    where `av1_nvenc` passes the probe and sorts first.

    These are string assertions. They need no GPU, which is the point: they cover the
    hardware the author cannot buy.
    """

    def _args(self, encoder: str, **kwargs) -> list[str]:
        return rec.build_command(
            FFMPEG, _plan([rec.VideoSource(LANDSCAPE)], encoder=encoder, **kwargs)
        )

    def test_every_family_encodes_with_the_encoder_it_was_given(self) -> None:
        for name in encoders.FAMILIES:
            with self.subTest(name):
                self.assertEqual(_value_after(self._args(name), "-c:v"), name)

    def test_no_family_is_quietly_turned_into_libx264(self) -> None:
        for name in encoders.FAMILIES:
            if name == "libx264":
                continue
            with self.subTest(name):
                self.assertNotIn("libx264", self._args(name))

    def test_the_hardware_families_stay_on_the_gpu(self) -> None:
        """nvenc, amf and mf take the d3d11 surface, so there must be no filter at all."""
        for name in ("hevc_nvenc", "h264_amf", "hevc_amf", "h264_mf", "hevc_mf"):
            with self.subTest(name):
                args = self._args(name)
                self.assertNotIn("-filter_complex", args)
                self.assertNotIn("hwdownload", " ".join(args))

    def test_qsv_is_remapped_and_converted_on_the_card(self) -> None:
        """ddagrab hands out bgra and only bgra, and h264_qsv will not take it.

        Measured: `hwdownload,format=nv12` straight after ddagrab is refused with
        "Invalid output format nv12 for hwframe download", which pins the frames
        context to one software format. `h264_qsv` lists `nv12 qsv` and no bgra at all,
        and nothing in the graph notices until the encoder opens - so the conversion
        has to be asked for, on the card.
        """
        for name in ("h264_qsv", "hevc_qsv", "av1_qsv"):
            with self.subTest(name):
                chain = _value_after(self._args(name), "-filter_complex")
                self.assertIn("hwmap=derive_device=qsv", chain)
                self.assertIn("vpp_qsv=format=nv12", chain)
                self.assertNotIn("hwdownload", chain)

    def test_media_foundation_is_always_told_to_use_hardware(self) -> None:
        """Without the flag Windows hands back its own software MFT and says nothing.

        That would have the probe record a CPU encoder as hardware - the single thing
        `capabilities` exists to prevent.
        """
        for name in ("h264_mf", "hevc_mf"):
            with self.subTest(name):
                args = self._args(name)
                self.assertEqual(_value_after(args, "-hw_encoding"), "true")

    def test_media_foundation_is_given_nv12_however_it_comes_down(self) -> None:
        """Whatever forces the download, MF gets nv12 from memory or it gets nothing.

        Measured: `yuv420p`, `yuv444p` and `bgra` all open `hevc_mf` and then write no
        packets at all, reporting "Nothing was written into output file" - the same
        sentence a machine with no hardware encoder produces, although `yuv420p` is in
        the encoder's own advertised format list. Only `nv12` exits 0.
        """
        rotated = rec.build_command(
            FFMPEG, _plan([rec.VideoSource(PORTRAIT)], encoder="hevc_mf")
        )
        self.assertTrue(
            _value_after(rotated, "-filter_complex").endswith("format=nv12[v0]")
        )
        for pix_fmt in ("yuv420p", "yuv444p", "bgra"):
            with self.subTest(pix_fmt):
                chain = encoders.frame_path(
                    "hevc_mf", pix_fmt=pix_fmt, force_download=True
                )
                self.assertTrue(chain.endswith("format=nv12"), chain)

    def test_nobody_else_is_given_nv12(self) -> None:
        for name in ("hevc_nvenc", "libx264", "h264_amf"):
            with self.subTest(name):
                args = rec.build_command(
                    FFMPEG, _plan([rec.VideoSource(PORTRAIT)], encoder=name)
                )
                self.assertTrue(
                    _value_after(args, "-filter_complex").endswith("format=yuv420p[v0]")
                )

    def test_an_unknown_encoder_is_refused_rather_than_guessed(self) -> None:
        with self.assertRaises(encoders.UnknownEncoder):
            self._args("h266_wishful")

    def test_an_unknown_encoder_would_at_least_take_the_slow_path(self) -> None:
        """Belt and braces: if a name ever reaches the filter chooser, it downloads.

        Not knowing must cost a copy, never a d3d11 surface handed to a CPU encoder.
        """
        self.assertIn("hwdownload", encoders.frame_path("h266_wishful"))

    def test_a_family_with_no_arguments_is_refused_too(self) -> None:
        """The back door into the original defect: a name filed under a family that has
        no branch would have fallen through to libx264 arguments."""
        encoders.FAMILIES["h266_future"] = "vvenc"
        try:
            with self.assertRaises(encoders.UnknownEncoder):
                encoders.codec_args("h266_future", quality=23, framerate=60)
        finally:
            del encoders.FAMILIES["h266_future"]

    def test_a_refused_encoder_does_not_escape_into_the_session(self) -> None:
        """`RecordingSession.start` returns sentences; it must not raise.

        The refusal lands after the loopback feeder has taken the named pipe, so an
        exception here would leave a half-started session and a thread holding a pipe.
        """
        recorder = rec.Recorder(FFMPEG)
        # A real folder: `Recorder.start` creates the output's parent before it builds
        # the command, and a test must not leave a directory behind on C:.
        with tempfile.TemporaryDirectory() as folder:
            problem = recorder.start(
                _plan([rec.VideoSource(LANDSCAPE)],
                      outputs=[Path(folder) / "a.mp4"], encoder="h266_wishful")
            )
        self.assertTrue(problem)
        self.assertIn("h266_wishful", problem)
        self.assertIsNone(recorder.process)
        self.assertIsNone(recorder.feeder)

    def test_each_family_is_given_quality_in_its_own_currency(self) -> None:
        knob = {
            "hevc_nvenc": "-cq", "hevc_amf": "-qvbr_quality_level",
            "hevc_qsv": "-global_quality", "libx264": "-crf",
        }
        for name, option in knob.items():
            with self.subTest(name):
                self.assertEqual(_value_after(self._args(name, quality=23), option), "23")

    def test_media_foundation_gets_its_own_ladder_not_the_cq_number(self) -> None:
        """MF's 0..100 is neither CQ nor CRF, and it is content-dependent enough that
        two measurements of the same pair of encoders disagreed by a factor of five.
        `encoders._MF_QUALITY` holds the number that was chosen and says why."""
        args = self._args("hevc_mf", quality=23)
        self.assertEqual(_value_after(args, "-quality"),
                         str(encoders.mf_quality(23)))
        self.assertNotIn("-cq", args)

    def test_the_media_foundation_ladder_runs_the_right_way(self) -> None:
        """A better `cq` is a smaller number; a better MF quality is a bigger one. It
        would be very easy to ship that inverted and never notice on one screen."""
        ladder = [encoders.mf_quality(q) for q in (16, 19, 23, 27)]
        self.assertEqual(ladder, sorted(ladder, reverse=True))
        self.assertTrue(all(0 <= value <= 100 for value in ladder), ladder)

    def test_the_nvenc_command_is_exactly_what_it_always_was(self) -> None:
        """The regression that matters: the path almost every user is on must not move.

        Spelled out rather than compared against the implementation, so that a change to
        the dispatch cannot quietly take the fast path with it.
        """
        args = self._args("hevc_nvenc", quality=23, framerate=60)
        start = args.index("-c:v")
        self.assertEqual(
            args[start:start + 13],
            ["-c:v", "hevc_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
             "-b:v", "0", "-cq", "23", "-g"],
        )
        self.assertEqual(args[start + 13], "120")


class CompositeLayoutEncoderTest(GraphSoundnessMixin, unittest.TestCase):
    def test_a_composite_downloads_without_lying_about_the_encoder(self) -> None:
        """Composition is a CPU filter, so the frames must come down - but the plan's
        own encoder is what encodes them. The old code passed the literal "libx264" to
        the filter chooser to force a download, which became a false statement the
        moment the chooser started dispatching on the name."""
        args = rec.build_command(
            FFMPEG,
            _plan(_sources(2), outputs=[Path(r"C:\out.mp4")],
                  encoder="hevc_amf", layout=rec.LAYOUT_SIDE_BY_SIDE),
        )
        chain = _value_after(args, "-filter_complex")
        self.assertIn("hwdownload", chain)
        self.assertEqual(_value_after(args, "-c:v"), "hevc_amf")
        self.assertNotIn("libx264", args)


class BuildCommandAudioTest(GraphSoundnessMixin, unittest.TestCase):
    MIC = r"@device_cm_{33D9A762}\wave_{113C1505}"
    PIPE = r"\\.\pipe\screenrec_1234"

    def test_no_audio_means_no_audio_arguments(self) -> None:
        args = rec.build_command(FFMPEG, _plan([rec.VideoSource(LANDSCAPE)]))
        self.assertNotIn("-c:a", args)
        self.assertEqual(args.count("-map"), 1)

    def test_mic_only_is_mapped_directly(self) -> None:
        args = rec.build_command(
            FFMPEG, _plan([rec.VideoSource(LANDSCAPE)], mic_device=self.MIC)
        )
        self.assertIn(f"audio={self.MIC}", args)
        self.assertIn("dshow", args)
        self.assertNotIn("-filter_complex", args)
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == "-map"],
                         ["0:v", "1:a"])
        self.assertEqual(_value_after(args, "-c:a"), "aac")

    def test_system_audio_only_comes_from_the_named_pipe(self) -> None:
        args = rec.build_command(
            FFMPEG, _plan([rec.VideoSource(LANDSCAPE)], loopback_pipe=self.PIPE)
        )
        self.assertIn(self.PIPE, args)
        self.assertIn("s16le", args)
        self.assertEqual(_value_after(args, "-ar"), "48000")
        self.assertEqual(_value_after(args, "-ac"), "2")
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == "-map"],
                         ["0:v", "1:a"])

    def test_mix_mode_emits_amix_without_normalisation(self) -> None:
        args = rec.build_command(FFMPEG, _plan(
            [rec.VideoSource(LANDSCAPE)],
            mic_device=self.MIC, loopback_pipe=self.PIPE, audio_mode=config.AUDIO_MIX,
        ))
        chain = _value_after(args, "-filter_complex")
        self.assertIn("amix=inputs=2", chain)
        self.assertIn("normalize=0", chain, "amix halves the volume without normalize=0")
        self.assertIn("[1:a][2:a]", chain)
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == "-map"],
                         ["0:v", "[aout]"])

    def test_separate_mode_emits_two_audio_maps_and_no_amix(self) -> None:
        args = rec.build_command(FFMPEG, _plan(
            [rec.VideoSource(LANDSCAPE)],
            mic_device=self.MIC, loopback_pipe=self.PIPE, audio_mode=config.AUDIO_SEPARATE,
        ))
        maps = [args[i + 1] for i, a in enumerate(args) if a == "-map"]
        self.assertEqual(maps, ["0:v", "1:a", "2:a"])
        if "-filter_complex" in args:
            self.assertNotIn("amix", _value_after(args, "-filter_complex"))
        self.assertIn("-metadata:s:a:0", args)
        self.assertIn("title=Microphone", args)
        self.assertIn("title=System", args)

    def test_the_mic_is_input_zero_of_the_audio_inputs_after_the_sources(self) -> None:
        args = rec.build_command(FFMPEG, _plan(
            [rec.VideoSource(LANDSCAPE), rec.VideoSource(PORTRAIT)],
            outputs=[Path(r"C:\out\a.mp4"), Path(r"C:\out\b.mp4")],
            layout=rec.LAYOUT_SEPARATE,
            mic_device=self.MIC, loopback_pipe=self.PIPE,
            audio_mode=config.AUDIO_SEPARATE,
        ))
        chain_inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
        self.assertEqual(len(chain_inputs), 4)
        self.assertEqual(chain_inputs[2], f"audio={self.MIC}")
        self.assertEqual(chain_inputs[3], self.PIPE)

    def test_separate_layout_with_mixed_audio_gives_each_file_its_own_branch(self) -> None:
        """DEFECT 1. Expected to fail until build_command splits the mixed audio.

        This is what the defaults produce: `Settings.audio_mode` is AUDIO_MIX, so a user
        who picks "one file per screen" and turns on both the microphone and system sound
        lands here. The builder mixes once into [aout] and then maps that same label into
        both files, which gives maps ['0:v', '[aout]', '[v1]', '[aout]'].

        Measured against the bundled ffmpeg: "[out#1/mp4] Output with label 'aout' does
        not exist in any defined filter graph, or was already used elsewhere. / Error
        opening output file bb.mp4." - exit 127. The headline feature of this program,
        two monitors at once, does not start at its own default settings.

        The shape that works, also measured (exit 0): end the mix with `asplit=2` and
        give each output its own branch, `[aout0]` and `[aout1]`.
        """
        args = rec.build_command(FFMPEG, _plan(
            _sources(2), outputs=_outputs(2), layout=rec.LAYOUT_SEPARATE,
            mic_device=self.MIC, loopback_pipe=self.PIPE, audio_mode=config.AUDIO_MIX,
        ))
        self.assert_graph_is_sound(args)
        # Each file must still end up with exactly one audio stream.
        self.assertEqual(len(_mapped_labels(args)), 3)  # [v1] plus one branch per output

    def test_a_single_file_may_of_course_map_the_mix_once(self) -> None:
        """The same mix is perfectly sound when there is only one output to feed."""
        args = rec.build_command(FFMPEG, _plan(
            [rec.VideoSource(LANDSCAPE)],
            mic_device=self.MIC, loopback_pipe=self.PIPE, audio_mode=config.AUDIO_MIX,
        ))
        self.assert_graph_is_sound(args)
        self.assertEqual(_maps(args), ["0:v", "[aout]"])


class BuildCommandLayoutTest(GraphSoundnessMixin, unittest.TestCase):
    def test_no_sources_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            rec.build_command(FFMPEG, _plan([]))

    def test_separate_layout_needs_one_output_per_source(self) -> None:
        sources = [rec.VideoSource(LANDSCAPE), rec.VideoSource(PORTRAIT)]
        with self.assertRaises(ValueError) as caught:
            rec.build_command(FFMPEG, _plan(sources, layout=rec.LAYOUT_SEPARATE))
        self.assertIn("one output file per source", str(caught.exception))

    def test_single_layout_refuses_more_than_one_output(self) -> None:
        with self.assertRaises(ValueError) as caught:
            rec.build_command(FFMPEG, _plan(
                [rec.VideoSource(LANDSCAPE)],
                outputs=[Path(r"C:\out\a.mp4"), Path(r"C:\out\b.mp4")],
            ))
        self.assertIn("exactly one output file", str(caught.exception))

    def test_side_by_side_refuses_two_outputs(self) -> None:
        with self.assertRaises(ValueError):
            rec.build_command(FFMPEG, _plan(
                [rec.VideoSource(LANDSCAPE), rec.VideoSource(PORTRAIT)],
                outputs=[Path(r"C:\out\a.mp4"), Path(r"C:\out\b.mp4")],
                layout=rec.LAYOUT_SIDE_BY_SIDE,
            ))

    def test_separate_layout_writes_each_source_to_its_own_file(self) -> None:
        outputs = [Path(r"C:\out\screen0.mp4"), Path(r"C:\out\screen1.mp4")]
        args = rec.build_command(FFMPEG, _plan(
            [rec.VideoSource(LANDSCAPE), rec.VideoSource(PORTRAIT)],
            outputs=outputs, layout=rec.LAYOUT_SEPARATE,
        ))
        for output in outputs:
            self.assertIn(str(output), args)
        maps = [args[i + 1] for i, a in enumerate(args) if a == "-map"]
        self.assertEqual(maps, ["0:v", "[v1]"])  # source 1 is rotated, so it is filtered

    def test_side_by_side_brings_both_sources_down_and_stacks_them(self) -> None:
        args = rec.build_command(FFMPEG, _plan(
            [rec.VideoSource(LANDSCAPE), rec.VideoSource(PORTRAIT)],
            layout=rec.LAYOUT_SIDE_BY_SIDE,
        ))
        chain = _value_after(args, "-filter_complex")
        self.assertIn("[0:v]hwdownload,format=bgra,format=yuv420p[c0]", chain)
        self.assertIn("[1:v]hwdownload,format=bgra,transpose=1,format=yuv420p[c1]", chain)
        self.assertIn("hstack=inputs=2[vout]", chain)
        self.assertEqual(_value_after(args, "-map"), "[vout]")
        # Both are scaled to the shorter of the two display heights.
        self.assertIn("scale=-2:1440", chain)

    def test_stacked_scales_to_the_narrower_width(self) -> None:
        args = rec.build_command(FFMPEG, _plan(
            [rec.VideoSource(LANDSCAPE), rec.VideoSource(PORTRAIT)],
            layout=rec.LAYOUT_STACKED,
        ))
        chain = _value_after(args, "-filter_complex")
        self.assertIn("vstack=inputs=2[vout]", chain)
        self.assertIn("scale=1440:-2", chain)

    def test_pip_overlays_the_second_source_on_the_first(self) -> None:
        args = rec.build_command(FFMPEG, _plan(
            [rec.VideoSource(LANDSCAPE), rec.VideoSource(PORTRAIT)],
            layout=rec.LAYOUT_PIP,
        ))
        chain = _value_after(args, "-filter_complex")
        self.assertIn("[c1]scale=640:-2[pip]", chain)
        self.assertIn("[c0][pip]overlay=W-w-24:H-h-24[vout]", chain)
        self.assertEqual(_value_after(args, "-map"), "[vout]")

    def test_side_by_side_with_three_sources_stacks_all_three(self) -> None:
        args = rec.build_command(FFMPEG, _plan(
            _sources(3), layout=rec.LAYOUT_SIDE_BY_SIDE,
        ))
        chain = _filter_chain(args)
        self.assertIn("hstack=inputs=3[vout]", chain)
        self.assertIn("[s0][s1][s2]", chain)
        self.assert_graph_is_sound(args)
        # The shortest of the three display heights: 1440, 2560 and 1080.
        self.assertIn("scale=-2:1080", chain)

    def test_stacked_with_three_sources_stacks_all_three(self) -> None:
        args = rec.build_command(FFMPEG, _plan(
            _sources(3), layout=rec.LAYOUT_STACKED,
        ))
        chain = _filter_chain(args)
        self.assertIn("vstack=inputs=3[vout]", chain)
        self.assert_graph_is_sound(args)
        self.assertIn("scale=1440:-2", chain)  # the narrowest of 2560, 1440 and 1920

    def test_a_composed_layout_with_one_source_is_refused(self) -> None:
        """Composing one screen with nothing is a mistake upstream; say so.

        This used to fall through the `len(plan.sources) > 1` guard and build a plain
        single-screen command. That reads as helpful and is not: whoever asked for two
        screens side by side got one, and nothing told them the second was missing.
        """
        for layout in (rec.LAYOUT_SIDE_BY_SIDE, rec.LAYOUT_STACKED, rec.LAYOUT_PIP):
            with self.subTest(layout=layout):
                with self.assertRaises(ValueError) as caught:
                    rec.build_command(FFMPEG, _plan(_sources(1), layout=layout))
                self.assertIn("source", str(caught.exception).lower())

    def test_pip_with_three_sources_is_refused(self) -> None:
        """Was DEFECT 2, fixed on 2026-11-03 by refusing rather than by composing.

        The builder used to prepare a [cN] chain per source and overlay only the first
        two, leaving [c2] dangling: "Filter 'format:default' has output 0 (c2)
        unconnected" - exit 127, nothing recorded. Picture in picture means one inset,
        so three sources is a question with no answer; it is refused in words the UI can
        show. If a layout with several insets is ever wanted it gets its own name.
        """
        with self.assertRaises(ValueError) as caught:
            rec.build_command(FFMPEG, _plan(_sources(3), layout=rec.LAYOUT_PIP))
        self.assertIn("two sources", str(caught.exception))

    def test_an_unrecognised_layout_is_refused_rather_than_dropping_screens(self) -> None:
        """DEFECT 3. Expected to fail until build_command validates plan.layout.

        Measured: every string below builds a clean command with one -i per source and
        maps only ['0:v']. ffmpeg accepts it, the user watches the recording back and the
        second monitor is simply absent - the capture ran and the frames were discarded.
        A mis-cased constant is the likely way in, since nothing checks the spelling.
        """
        for layout in ("Side_By_Side", "sidebyside", "nonsense", ""):
            with self.subTest(layout=layout):
                with self.assertRaises(ValueError):
                    rec.build_command(FFMPEG, _plan(_sources(2), layout=layout))

    def test_single_layout_with_several_sources_is_refused(self) -> None:
        """DEFECT 3, the reachable half: LAYOUT_SINGLE is what a plan defaults to.

        Two sources and the default layout capture both screens into one file that holds
        only the first. Nothing raises and nothing is logged.
        """
        with self.assertRaises(ValueError):
            rec.build_command(FFMPEG, _plan(_sources(2), layout=rec.LAYOUT_SINGLE))

    def test_a_composed_layout_leaves_no_orphan_source_chain(self) -> None:
        """The per-source [vN] chains must be replaced by the [cN] ones, not kept as well."""
        args = rec.build_command(FFMPEG, _plan(
            [rec.VideoSource(LANDSCAPE), rec.VideoSource(PORTRAIT)],
            layout=rec.LAYOUT_SIDE_BY_SIDE,
        ))
        chain = _value_after(args, "-filter_complex")
        self.assertNotIn("[v0]", chain)
        self.assertNotIn("[v1]", chain)
        self.assertEqual(chain.count("[0:v]"), 1)
        self.assertEqual(chain.count("[1:v]"), 1)


class OutputPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="screenrec-test-")
        self.addCleanup(self._dir.cleanup)
        self.settings = config.Settings(output_dir=self._dir.name)
        self.when = datetime(2026, 11, 3, 21, 42, 7)

    def test_the_pattern_is_expanded(self) -> None:
        path = rec.output_path(self.settings, self.when)
        self.assertEqual(path.name, "ScreenRec_2026-11-03_21-42-07.mp4")
        self.assertEqual(path.parent, Path(self._dir.name))

    def test_a_suffix_is_appended_before_the_extension(self) -> None:
        path = rec.output_path(self.settings, self.when, "screen1")
        self.assertEqual(path.name, "ScreenRec_2026-11-03_21-42-07_screen1.mp4")

    def test_characters_windows_forbids_are_replaced(self) -> None:
        self.settings.filename_pattern = 'a<b>c:d"e/f\\g|h?i*j'
        name = rec.output_path(self.settings, self.when).name
        self.assertEqual(name, "a-b-c-d-e-f-g-h-i-j.mp4")
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, name)

    def test_a_pattern_with_a_colon_from_strftime_is_cleaned(self) -> None:
        self.settings.filename_pattern = "Rec %H:%M:%S"
        name = rec.output_path(self.settings, self.when).name
        self.assertEqual(name, "Rec 21-42-07.mp4")

    def test_the_result_is_always_an_mp4_under_the_output_directory(self) -> None:
        for pattern in ("x", "%Y", "sub/dir/name"):
            with self.subTest(pattern=pattern):
                self.settings.filename_pattern = pattern
                path = rec.output_path(self.settings, self.when)
                self.assertEqual(path.suffix, ".mp4")
                self.assertEqual(path.parent, Path(self._dir.name))


class PipeNameTest(unittest.TestCase):
    def test_it_is_a_windows_named_pipe_unique_to_this_process(self) -> None:
        import os

        name = rec.pipe_name()
        self.assertTrue(name.startswith(r"\\.\pipe\screenrec_"), name)
        self.assertIn(str(os.getpid()), name)

    def test_a_tag_distinguishes_two_pipes_in_one_process(self) -> None:
        self.assertNotEqual(rec.pipe_name("mic"), rec.pipe_name("sys"))
        self.assertTrue(rec.pipe_name("sys").endswith("_sys"))


class RecorderStateTest(unittest.TestCase):
    """The parts of Recorder that need no process."""

    def test_a_fresh_recorder_is_not_running(self) -> None:
        recorder = rec.Recorder(FFMPEG)
        self.assertFalse(recorder.running)
        self.assertEqual(recorder.elapsed, 0.0)
        self.assertEqual(recorder.log_text(), "")
        self.assertEqual(recorder.command, [])

    def test_stopping_a_recorder_that_never_started_is_silent(self) -> None:
        self.assertEqual(rec.Recorder(FFMPEG).stop(), "")


# ---------------------------------------------------------------- lifecycle


class _Stdin:
    """ffmpeg's stdin, as much of it as `Recorder.stop` uses.

    Writing ``q`` is what ends a recording - measured, `docs/CONTRACT.md` - so the
    stand-in makes the process exit when it is written to, which is the behaviour
    `stop` is built on.
    """

    def __init__(self, process: "_FakeProcess", code: int) -> None:
        self.process = process
        self.code = code
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data
        self.process.returncode = self.code

    def flush(self) -> None:
        pass


class _FakeProcess:
    """A `subprocess.Popen` as much as `Recorder` uses one. Starts nothing."""

    def __init__(self, *, code: int | None = None, log: bytes = b"",
                 code_on_q: int = 0) -> None:
        #: None means "still running", which is what `Popen.returncode` means too.
        self.returncode = code
        self.stdin = _Stdin(self, code_on_q)
        self.stdout = io.BytesIO(b"")
        self.stderr = io.BytesIO(log)
        self.terminated = False
        self.killed = False
        #: Every timeout `wait` was given, so a test can see what it was asked for.
        self.waited: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.waited.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _segment_plan(folder: Path, **changes) -> rec.RecordingPlan:
    """A plan that builds, writing to `folder`. `changes` overrides any field."""
    fields = dict(
        sources=[rec.VideoSource(monitor=LANDSCAPE, crop=(0, 0, 640, 480))],
        outputs=[folder / "segment.mkv"],
        encoder="hevc_nvenc",
        pix_fmt="yuv420p",
        quality=23,
        quality_level="balanced",
        framerate=30,
        draw_mouse=False,
        layout=rec.LAYOUT_SINGLE,
    )
    fields.update(changes)
    return rec.RecordingPlan(**fields)


class RecorderTellsTheTruthAboutFfmpegTest(unittest.TestCase):
    """`start` and `stop` used to report success over a process that had died.

    Reproduced on 2026-11-05 with a real recording: a command naming DXGI output 9,
    which does not exist on this machine. ffmpeg exited after 0.20 s having written
    "Failed to enumerate DXGI output 9"; `start` returned '' and `stop` returned ''
    with the exit code and the whole log thrown away. Only `_tick`, 200 ms later,
    noticed anything was wrong.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="screenrec-life-")
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)

    def given(self, process: _FakeProcess) -> rec.Recorder:
        """A recorder whose next `Popen` hands back `process`.

        The attribute is put back by the cleanup; `unittest` runs these one at a
        time, so nothing else sees the stand-in.
        """
        real = subprocess.Popen
        subprocess.Popen = lambda *args, **kwargs: process
        self.addCleanup(setattr, subprocess, "Popen", real)
        return rec.Recorder(FFMPEG)

    def test_start_does_not_report_success_over_a_process_that_died(self) -> None:
        process = _FakeProcess(
            code=-5, log=b"Failed to enumerate DXGI output 9\nError opening input\n"
        )
        recorder = self.given(process)
        problem = recorder.start(_segment_plan(self.folder))
        self.assertNotEqual(problem, "")
        self.assertIn("-5", problem)
        self.assertIn("Failed to enumerate DXGI output 9", problem)

    def test_a_failed_start_leaves_nothing_running(self) -> None:
        recorder = self.given(_FakeProcess(code=-5))
        recorder.start(_segment_plan(self.folder))
        self.assertFalse(recorder.running)

    def test_start_is_silent_while_the_process_is_still_alive(self) -> None:
        """The whole point: a working start must not be turned into a problem."""
        recorder = self.given(_FakeProcess())
        self.assertEqual(recorder.start(_segment_plan(self.folder)), "")
        self.assertTrue(recorder.running)

    def test_start_gives_the_process_the_grace_window_and_no_more(self) -> None:
        """A working start pays this once; see `rec.START_GRACE`.

        What is left of the window, not the whole of it: `start` arms a deadline and
        `settle` waits for the rest of it, so the figure is a shade under.
        """
        process = _FakeProcess()
        recorder = self.given(process)
        recorder.start(_segment_plan(self.folder))
        self.assertEqual(len(process.waited), 1)
        self.assertLessEqual(process.waited[0], rec.START_GRACE)
        self.assertGreater(process.waited[0], rec.START_GRACE - 0.05)
        self.assertLess(rec.START_GRACE, 1.0)

    def test_a_start_that_does_not_wait_launches_and_returns(self) -> None:
        """The all-screens case: launching must not sit out a grace window, because
        in a loop that is what puts the captures a window apart. Measured on this
        build: 0.355-0.363 s per call waiting, 4 ms apart without."""
        process = _FakeProcess()
        recorder = self.given(process)
        self.assertEqual(recorder.start(_segment_plan(self.folder), wait=False), "")
        self.assertEqual(process.waited, [])
        self.assertTrue(recorder.running)

    def test_settle_answers_for_a_start_that_did_not_wait(self) -> None:
        """The verdict is deferred, not dropped: the exit code and the log still come."""
        process = _FakeProcess(code=-9, log=b"Error opening input file ddagrab=\n")
        recorder = self.given(process)
        self.assertEqual(recorder.start(_segment_plan(self.folder), wait=False), "")
        problem = recorder.settle()
        self.assertIn("-9", problem)
        self.assertIn("ddagrab", problem)

    def test_settle_is_silent_about_a_capture_that_is_running(self) -> None:
        recorder = self.given(_FakeProcess())
        recorder.start(_segment_plan(self.folder), wait=False)
        self.assertEqual(recorder.settle(), "")
        self.assertTrue(recorder.running)

    def test_settle_waits_only_what_is_left_of_the_window(self) -> None:
        process = _FakeProcess()
        recorder = self.given(process)
        recorder.start(_segment_plan(self.folder), wait=False)
        recorder.settle()
        self.assertEqual(len(process.waited), 1)
        self.assertLessEqual(process.waited[0], rec.START_GRACE)

    def test_settle_a_second_time_waits_for_nothing(self) -> None:
        """It is called from more than one place; a second call must not pay again."""
        process = _FakeProcess()
        recorder = self.given(process)
        recorder.start(_segment_plan(self.folder))
        self.assertEqual(recorder.settle(), "")
        self.assertEqual(len(process.waited), 1)

    def test_settle_on_a_recorder_that_never_started_is_silent(self) -> None:
        recorder = self.given(_FakeProcess())
        self.assertEqual(recorder.settle(), "")

    def test_stop_reports_a_process_that_had_already_died(self) -> None:
        """The `poll() is not None` branch, which used to return '' and nothing else."""
        process = _FakeProcess(code=-5, log=b"Error opening input file ddagrab=\n")
        recorder = self.given(process)
        recorder.start(_segment_plan(self.folder))  # reports it too, now
        problem = recorder.stop()
        self.assertIn("-5", problem)
        self.assertIn("ddagrab", problem)

    def test_stop_is_silent_when_ffmpeg_left_the_way_it_was_asked_to(self) -> None:
        """``q`` makes ffmpeg write the trailer and exit 0 - measured."""
        recorder = self.given(_FakeProcess(code_on_q=0))
        recorder.start(_segment_plan(self.folder))
        self.assertEqual(recorder.stop(), "")

    def test_stop_reports_a_bad_exit_code_after_the_stop_was_delivered(self) -> None:
        recorder = self.given(_FakeProcess(code_on_q=1, log=b"muxing overhead\n"))
        recorder.start(_segment_plan(self.folder))
        self.assertIn("1", recorder.stop())


class _FakeRecorder:
    """A `Recorder` as much as `RecordingSession` uses one. Starts nothing."""

    #: Every one made, newest last, so a test can reach the plan a segment was given.
    made: list["_FakeRecorder"] = []
    #: What the next `start`, `settle` and `stop` should say.
    next_start_problem = ""
    next_settle_problem = ""
    next_stop_problem = ""

    def __init__(self, ffmpeg: str) -> None:
        self.ffmpeg = ffmpeg
        self.plan: rec.RecordingPlan | None = None
        self.running = False
        self.progress = rec.Progress()
        self.feeder = None
        #: The real `Recorder` drops system sound rather than refusing to record when
        #: its device will not open, and says why here. A double without these two
        #: is not a `Recorder` as `RecordingSession` uses one.
        self.dropped_audio = ""
        self.dropped_reason = ""
        self.log = []
        #: Whether this one was launched with the start grace deferred, and how many
        #: times `settle` was asked for the verdict.
        self.waited: bool | None = None
        self.settles = 0
        _FakeRecorder.made.append(self)

    def start(
        self, plan, on_progress=None, loopback_device: int = -1, wait: bool = True
    ) -> str:
        self.plan = plan
        self.waited = wait
        if _FakeRecorder.next_start_problem:
            return _FakeRecorder.next_start_problem
        self.running = True
        return "" if wait else ""

    def settle(self) -> str:
        self.settles += 1
        if _FakeRecorder.next_settle_problem:
            self.running = False
            return _FakeRecorder.next_settle_problem
        return ""

    def stop(self, timeout: float = 12.0) -> str:
        self.running = False
        return _FakeRecorder.next_stop_problem

    def log_text(self) -> str:
        return ""


class SessionLifecycleMixin:
    """A `RecordingSession` whose segments are planned by the test, not recorded."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="screenrec-session-")
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)

        _FakeRecorder.made = []
        _FakeRecorder.next_start_problem = ""
        _FakeRecorder.next_settle_problem = ""
        _FakeRecorder.next_stop_problem = ""
        real_recorder = rec.Recorder
        rec.Recorder = _FakeRecorder
        self.addCleanup(setattr, rec, "Recorder", real_recorder)

        # `stop` would otherwise run the real join on segments nobody wrote.
        real_finish = rec.takes_mod.finish
        self.joined: list = []
        rec.takes_mod.finish = self._finish
        self.addCleanup(setattr, rec.takes_mod, "finish", real_finish)

        #: One entry per segment, in order; the last is reused once they run out.
        self.plans: list[dict] = [{}]

    def _finish(self, ffmpeg, take, keep_segments=False, size_store=None):
        self.joined.append(take)
        return take.final, ""

    def session(self) -> rec.RecordingSession:
        counter = {"n": 0}

        def plan_for(segment: Path):
            index = min(counter["n"], len(self.plans) - 1)
            counter["n"] += 1
            changes = self.plans[index]
            if changes is None:
                return None  # what `ui/app.py` returns when the source has gone
            return _segment_plan(self.folder, outputs=[segment], **changes)

        return rec.RecordingSession(FFMPEG, self.folder / "take.mp4", plan_for)


class ResumeThatFailsStaysPausedTest(SessionLifecycleMixin, unittest.TestCase):
    """`resume` cleared `_paused` before `_begin_segment` had succeeded.

    Reproduced on 2026-11-05 with a real recording: pause, make the source
    unresolvable, press Resume. The session went `active False, recording False,
    paused False` with 282929 bytes of segment on the disk, the bar still showing a
    recording, and the record hotkey built a *new* session - the take orphaned with
    no way back to it from the interface.
    """

    def test_a_resume_that_cannot_start_leaves_the_take_paused(self) -> None:
        self.plans = [{}, None]
        session = self.session()
        self.assertEqual(session.start(), "")
        self.assertEqual(session.pause(), "")
        problem = session.resume()
        self.assertNotEqual(problem, "")
        self.assertTrue(session.paused)
        self.assertTrue(session.active)
        self.assertFalse(session.recording)

    def test_the_failed_resume_adds_no_segment(self) -> None:
        self.plans = [{}, None]
        session = self.session()
        session.start()
        session.pause()
        session.resume()
        self.assertEqual(session.segments, 1)

    def test_a_take_left_by_a_failed_resume_cannot_be_orphaned(self) -> None:
        """`ui/app.py` starts a new recording when `active` is False. It must not be.

        The third plan is a workable one on purpose: the refusal has to come from the
        session still being active, not from the plan failing again.
        """
        self.plans = [{}, None, {}]
        session = self.session()
        session.start()
        session.pause()
        session.resume()
        self.assertNotEqual(session.start(), "")
        self.assertEqual(session.segments, 1)

    def test_a_take_left_by_a_failed_resume_can_still_be_resumed(self) -> None:
        self.plans = [{}, None]
        session = self.session()
        session.start()
        session.pause()
        session.resume()
        self.plans = [{}]  # the screen came back
        self.assertEqual(session.resume(), "")
        self.assertTrue(session.recording)
        self.assertFalse(session.paused)

    def test_a_resume_that_works_clears_the_pause(self) -> None:
        session = self.session()
        session.start()
        session.pause()
        self.assertEqual(session.resume(), "")
        self.assertFalse(session.paused)
        self.assertEqual(session.segments, 2)


class TakeKeepsTheShapeItStartedWithTest(SessionLifecycleMixin, unittest.TestCase):
    """A take is one file, so its segments must agree. See `takes.shape_problem`.

    Reproduced on 2026-11-05 with a real recording: 2 s of H.265, pause, profile
    changed to H.264, 2 s more. The user was told nothing, and the 4.10 s file
    decoded 122 of 244 frames with 353 errors.
    """

    def paused_session(self) -> rec.RecordingSession:
        session = self.session()
        session.start()
        session.pause()
        return session

    def test_a_changed_encoder_does_not_start_a_second_segment(self) -> None:
        self.plans = [{}, {"encoder": "h264_nvenc"}]
        session = self.paused_session()
        problem = session.resume()
        self.assertIn("hevc_nvenc", problem)
        self.assertIn("h264_nvenc", problem)
        self.assertEqual(session.segments, 1)
        self.assertTrue(session.paused)

    def test_a_changed_pixel_format_does_not_start_a_second_segment(self) -> None:
        self.plans = [{}, {"pix_fmt": "yuv444p"}]
        session = self.paused_session()
        self.assertIn("yuv444p", session.resume())

    def test_a_changed_frame_size_does_not_start_a_second_segment(self) -> None:
        self.plans = [{}, {"sources": [rec.VideoSource(
            monitor=LANDSCAPE, crop=(0, 0, 800, 600))]}]
        session = self.paused_session()
        self.assertIn("800x600", session.resume())

    def test_turning_the_system_sound_on_during_a_pause_is_refused(self) -> None:
        """One sound track becoming two is a take that cannot be joined."""
        self.plans = [
            {"mic_device": "USB Microphone"},
            {"mic_device": "USB Microphone",
             "loopback_pipe": r"\\.\pipe\screenrec_test",
             "audio_mode": config.AUDIO_SEPARATE},
        ]
        session = self.paused_session()
        problem = session.resume()
        self.assertIn("one sound track", problem)
        self.assertIn("2 sound tracks", problem)

    def test_the_refusal_is_russian_on_a_russian_bar(self) -> None:
        """All of it, including the advice.

        The refusal used to be the catalogue key "The recording could not be set up:
        {reason}" with the sentence written into `{reason}` in English, so a Russian
        user read "Не удалось настроить запись: this recording is hevc_nvenc ... Stop
        it to save what has been recorded ..." - the only thing in the message telling
        them what to do, in a language the setting says they do not read.
        """
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language(i18n.LANG_RU)
        self.plans = [{}, {"encoder": "h264_nvenc"}]
        session = self.paused_session()
        problem = session.resume()
        self.assertIn("Остановите", problem)
        self.assertIn("без звука", problem)
        self.assertNotIn("Stop it to save", problem)
        self.assertNotIn("with no sound", problem)
        self.assertIn("hevc_nvenc", problem)  # ffmpeg's own words stay its own

    def test_the_track_count_is_russian_too(self) -> None:
        """The clause filling {current} and {wanted} is a catalogue key of its own."""
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language(i18n.LANG_RU)
        self.plans = [
            {"mic_device": "USB Microphone"},
            {"mic_device": "USB Microphone",
             "loopback_pipe": r"\\.\pipe\screenrec_test",
             "audio_mode": config.AUDIO_SEPARATE},
        ]
        session = self.paused_session()
        problem = session.resume()
        self.assertIn("одна звуковая дорожка", problem)
        self.assertIn("звуковых дорожек: 2", problem)
        self.assertNotIn("sound track", problem)

    def test_a_changed_frame_rate_is_allowed(self) -> None:
        """Measured: 30 -> 60 fps joined with 138 frames and no decode errors, so
        this is not a reason to stop a recording that is working."""
        self.plans = [{}, {"framerate": 60}]
        session = self.paused_session()
        self.assertEqual(session.resume(), "")
        self.assertEqual(session.segments, 2)

    def test_a_different_microphone_is_allowed(self) -> None:
        """A different device is still one track, and one track joins."""
        self.plans = [{"mic_device": "USB Microphone"},
                      {"mic_device": "CABLE Output (VB-Audio Virtual Cable)"}]
        session = self.paused_session()
        self.assertEqual(session.resume(), "")

    def test_the_first_segment_is_the_contract_not_the_one_before(self) -> None:
        self.plans = [{}, {"framerate": 60}, {"encoder": "h264_nvenc"}]
        session = self.paused_session()
        self.assertEqual(session.resume(), "")
        session.pause()
        self.assertIn("hevc_nvenc", session.resume())

    def test_a_take_that_never_changes_is_never_refused(self) -> None:
        session = self.session()
        session.start()
        for _round in range(4):
            self.assertEqual(session.pause(), "")
            self.assertEqual(session.resume(), "")
        self.assertEqual(session.segments, 5)


class SessionStopDoesNotSkipADeadSegmentTest(
    SessionLifecycleMixin, unittest.TestCase
):
    """`stop` used to end the segment only `if self.recording`.

    An ffmpeg that died on its own leaves a recorder that is not running, so its
    feeder was left running, its threads unjoined and its exit code unread - and the
    take was assembled and reported as saved with nothing said about why it is short.
    """

    def test_stop_reports_what_the_dead_recorder_had_to_say(self) -> None:
        session = self.session()
        session.start()
        recorder = _FakeRecorder.made[-1]
        recorder.running = False  # ffmpeg died by itself
        _FakeRecorder.next_stop_problem = "ffmpeg exited with code -5."
        path, problem = session.stop()
        self.assertIn("-5", problem)

    def test_stop_still_joins_what_reached_the_disk(self) -> None:
        session = self.session()
        session.start()
        _FakeRecorder.made[-1].running = False
        path, _problem = session.stop()
        self.assertEqual(len(self.joined), 1)
        self.assertEqual(path, self.folder / "take.mp4")

    def test_a_normal_stop_is_still_silent(self) -> None:
        session = self.session()
        session.start()
        _path, problem = session.stop()
        self.assertEqual(problem, "")


class SessionCanLaunchWithoutWaitingTest(
    SessionLifecycleMixin, unittest.TestCase
):
    """Launching and settling are separable, so several screens can start together.

    `Recorder.start` watches the new process for `START_GRACE` before it reports the
    recording as begun - measured at 0.355-0.363 s per call on this build. Paid inside
    a loop over screens it is paid before the *next* screen is launched, which is what
    put the two files of an all-screens recording 355-367 ms apart (4 ms with the wait
    removed), against a 50 ms gate the project set itself. The wait is not removable -
    a doomed start needs 0.197-0.236 s to die and the first `-progress` line of a
    healthy one arrives at 0.73 s - so it is moved instead: launch every screen, then
    settle every screen.
    """

    def test_start_passes_the_wait_through_to_the_recorder(self) -> None:
        session = self.session()
        self.assertEqual(session.start(wait=False), "")
        self.assertIs(_FakeRecorder.made[-1].waited, False)

    def test_start_waits_by_itself_unless_told_not_to(self) -> None:
        """One screen keeps the answer with the button press that asked for it."""
        session = self.session()
        session.start()
        self.assertIs(_FakeRecorder.made[-1].waited, True)

    def test_settle_reports_a_launch_that_turned_out_to_be_doomed(self) -> None:
        _FakeRecorder.next_settle_problem = "ffmpeg exited with code -5."
        session = self.session()
        self.assertEqual(session.start(wait=False), "")
        self.assertIn("-5", session.settle())

    def test_a_failed_settle_leaves_no_recorder_and_no_segment(self) -> None:
        _FakeRecorder.next_settle_problem = "ffmpeg exited with code -5."
        session = self.session()
        session.start(wait=False)
        session.settle()
        self.assertFalse(session.recording)
        self.assertFalse(session.active)
        self.assertEqual(session.segments, 0)

    def test_a_failed_settle_on_a_resume_leaves_the_take_paused(self) -> None:
        """The same invariant `resume` holds: a take that did not resume is paused,
        with its segments on disk and a way back to them from the interface."""
        session = self.session()
        session.start()
        session.pause()
        _FakeRecorder.next_settle_problem = "ffmpeg exited with code -5."
        self.assertEqual(session.resume(wait=False), "")
        self.assertIn("-5", session.settle())
        self.assertTrue(session.paused)
        self.assertTrue(session.active)
        self.assertEqual(session.segments, 1)

    def test_the_take_left_by_a_failed_settle_can_still_be_resumed(self) -> None:
        session = self.session()
        session.start()
        session.pause()
        _FakeRecorder.next_settle_problem = "ffmpeg exited with code -5."
        session.resume(wait=False)
        session.settle()
        _FakeRecorder.next_settle_problem = ""
        self.assertEqual(session.resume(), "")
        self.assertTrue(session.recording)
        self.assertEqual(session.segments, 2)

    def test_a_settle_that_finds_nothing_wrong_keeps_the_segment(self) -> None:
        session = self.session()
        session.start(wait=False)
        self.assertEqual(session.settle(), "")
        self.assertTrue(session.recording)
        self.assertEqual(session.segments, 1)

    def test_settling_a_session_that_waited_asks_the_recorder_nothing(self) -> None:
        """`settle` is safe to call on every session, whichever way it was started."""
        session = self.session()
        session.start()
        self.assertEqual(session.settle(), "")
        self.assertEqual(_FakeRecorder.made[-1].settles, 0)

    def test_settle_before_anything_started_is_silent(self) -> None:
        self.assertEqual(self.session().settle(), "")

    def test_a_stop_cancels_an_outstanding_settle(self) -> None:
        session = self.session()
        session.start(wait=False)
        session.stop()
        _FakeRecorder.next_settle_problem = "ffmpeg exited with code -5."
        self.assertEqual(session.settle(), "")


class TheTakeCarriesItsOwnClockTest(SessionLifecycleMixin, unittest.TestCase):
    """`takes.measure` refuses a cost whose duration disagrees with the file's own.

    The guard was wired and never armed: `claimed_seconds` comes from `take.seconds`
    and nothing set it, so `storage.remember_recording` compared the file against 0.0
    and skipped the check every time. It is filled in `stop`, where the last segment
    has just been banked.
    """

    def test_stop_hands_the_join_what_the_recorder_counted(self) -> None:
        session = self.session()
        session.start()
        _FakeRecorder.made[-1].progress.seconds = 5.5
        session.stop()
        self.assertEqual(self.joined[-1].seconds, 5.5)

    def test_the_clock_covers_every_segment_of_a_paused_take(self) -> None:
        session = self.session()
        session.start()
        _FakeRecorder.made[-1].progress.seconds = 2.0
        session.pause()
        session.resume()
        _FakeRecorder.made[-1].progress.seconds = 3.0
        session.stop()
        self.assertEqual(self.joined[-1].seconds, 5.0)


if __name__ == "__main__":
    unittest.main()


class WhatTheSystemSoundTrackActuallyGotTest(unittest.TestCase):
    """`RecordingSession._feeder_note` is the only thing that reads the counters the
    feeder keeps, and it is what the bar says when a take ends."""

    class _Feeder:
        def __init__(
            self, error="", frames_written=0, audible_frames=0, reason="",
            fell_back=False,
        ) -> None:
            self.error = error
            self.reason = reason
            self.frames_written = frames_written
            self.audible_frames = audible_frames
            #: The chosen endpoint had gone and the default was recorded instead.
            self.fell_back = fell_back

    class _Recorder:
        def __init__(self, feeder, dropped="", reason="") -> None:
            self.feeder = feeder
            self.dropped_audio = dropped
            self.dropped_reason = reason

    def _note(self, feeder) -> str:
        return rec.RecordingSession._feeder_note(self._Recorder(feeder))

    def test_no_system_sound_at_all_means_no_note(self) -> None:
        self.assertEqual(self._note(None), "")

    def test_a_real_conversation_says_nothing(self) -> None:
        self.assertEqual(
            self._note(self._Feeder(frames_written=48000 * 600,
                                    audible_frames=48000 * 480)),
            "",
        )

    def test_an_endpoint_that_produced_nothing_is_reported(self) -> None:
        self.assertIn("no audio arrived", self._note(self._Feeder()))

    def test_a_track_that_is_all_silence_is_reported_too(self) -> None:
        """The case that cost a recorded call: the endpoint was alive and running and
        carrying nothing, because the call was playing out of a different one. Frames
        flowed, so the old check passed it as a good recording."""
        note = self._note(
            self._Feeder(frames_written=48000 * 600, audible_frames=0)
        )
        self.assertTrue(note)
        self.assertIn("silent", note)
        self.assertIn("playback device", note, "it has to say what to go and look at")

    def test_an_error_outranks_both(self) -> None:
        note = self._note(
            self._Feeder(error="the device vanished", frames_written=0)
        )
        self.assertEqual(note, "the device vanished")



class SystemSoundIsNotWorthARecordingTest(unittest.TestCase):
    """A system-sound device that will not open must not cost the whole take.

    On 2026-11-13 the user pressed record, watched the countdown, and got
    `No system-sound device available: [Errno -9996] Invalid device info` with no
    file at all - though the screen and the microphone were both ready. `start` now
    drops the track and records; these pin that down, because the wrong behaviour
    looked perfectly reasonable in the code that had it.
    """

    class _Feeder:
        """A `LoopbackFeeder` that fails on `start`, as a dead endpoint does."""

        def __init__(self, pipe_name, device_index=-1) -> None:
            self.pipe_name = pipe_name
            self.device_index = device_index
            self.error = ""
            self.reason = ""
            self.fell_back = False
            self.frames_written = 0
            self.audible_frames = 0
            self.stopped = False

        def start(self) -> None:
            self.error = "the endpoint is gone"
            self.reason = rec.audio_mod.REASON_NO_DEVICE

        def stop(self) -> None:
            self.stopped = True

    def setUp(self) -> None:
        self.made: list = []

        def feeder(pipe_name, device_index=-1):
            made = self._Feeder(pipe_name, device_index)
            self.made.append(made)
            return made

        self._real = rec.audio_mod.LoopbackFeeder
        rec.audio_mod.LoopbackFeeder = feeder
        self._real_popen = rec.subprocess.Popen

        class _Process:
            def __init__(self, *a, **k) -> None:
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO()
                self.stderr = io.BytesIO()

            def poll(self):
                return None

        rec.subprocess.Popen = _Process

    def tearDown(self) -> None:
        rec.audio_mod.LoopbackFeeder = self._real
        rec.subprocess.Popen = self._real_popen

    def _plan(self, tmp) -> rec.RecordingPlan:
        return rec.RecordingPlan(
            sources=[rec.VideoSource(monitor=LANDSCAPE)],
            outputs=[Path(tmp) / "take.mkv"],
            loopback_pipe=r"\.\pipe\screenrec_test",
        )

    def test_the_recording_still_happens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = rec.Recorder("ffmpeg.exe")
            problem = recorder.start(self._plan(tmp), wait=False)
        self.assertEqual(problem, "", "a dead sound device must not refuse the take")

    def test_the_track_is_dropped_from_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = rec.Recorder("ffmpeg.exe")
            recorder.start(self._plan(tmp), wait=False)
            self.assertFalse(
                recorder.plan.has_system_audio,
                "ffmpeg must not be told to read a pipe nobody is feeding",
            )

    def test_the_reason_is_kept_in_words_and_as_a_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = rec.Recorder("ffmpeg.exe")
            recorder.start(self._plan(tmp), wait=False)
        self.assertEqual(recorder.dropped_audio, "the endpoint is gone")
        self.assertEqual(recorder.dropped_reason, rec.audio_mod.REASON_NO_DEVICE)

    def test_the_feeder_is_let_go(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = rec.Recorder("ffmpeg.exe")
            recorder.start(self._plan(tmp), wait=False)
        self.assertTrue(self.made[0].stopped, "the pipe has to be given back")
        self.assertIsNone(recorder.feeder)

    def test_the_note_says_it_again_when_the_file_is_saved(self) -> None:
        """Said twice on purpose: once while the take can still be restarted, and
        once with the file, so a missing track is never a surprise on playback."""
        recorder = rec.Recorder("ffmpeg.exe")
        recorder.dropped_audio = "the endpoint is gone"
        recorder.dropped_reason = rec.audio_mod.REASON_NO_DEVICE
        self.assertEqual(
            rec.RecordingSession._feeder_note(recorder), "the endpoint is gone"
        )
        self.assertEqual(
            rec.RecordingSession._feeder_reason(recorder),
            rec.audio_mod.REASON_NO_DEVICE,
        )

    def test_a_second_take_starts_clean(self) -> None:
        """`dropped_audio` is per recording. A take that lost its sound must not make
        the next one say so too - the same bug `_frame_note` had."""
        recorder = rec.Recorder("ffmpeg.exe")
        recorder.dropped_audio = "the endpoint is gone"
        with tempfile.TemporaryDirectory() as tmp:
            plan = rec.RecordingPlan(
                sources=[rec.VideoSource(monitor=LANDSCAPE)],
                outputs=[Path(tmp) / "take.mkv"],
            )
            recorder.start(plan, wait=False)
        self.assertEqual(recorder.dropped_audio, "")
        self.assertEqual(recorder.dropped_reason, "")
