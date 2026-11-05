"""The module that decides which encoder a recording uses, which had no tests at all.

It is the one place where a wrong answer is not a wrong pixel but a recording that never
happens, and until 2026-11-04 its answer and the recorder's command were built by
different code that nothing made agree. The invariant at the top of this file -
every candidate has arguments written for it - is the one that was broken, and it is the
reason eight of the eleven candidates produced a command ffmpeg could not run -
every name except `hevc_nvenc`, `h264_nvenc` and `libx264`.

Nothing here starts ffmpeg, touches a GPU or reads the user's settings folder.
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass

from screenrec import capabilities as caps
from screenrec import config
from screenrec import encoders


def _result(name: str, works: bool = True, **kwargs) -> caps.EncoderResult:
    """One probe row, with the codec and vendor taken from the real candidate table."""
    codec, vendor = "H.264", "CPU"
    for candidate, candidate_codec, candidate_vendor in caps.CANDIDATES:
        if candidate == name:
            codec, vendor = candidate_codec, candidate_vendor
            break
    fields = {
        "name": name, "codec": codec, "vendor": vendor,
        "listed": True, "works": works, "reason": "" if works else "no", "millis": 10,
    }
    fields.update(kwargs)
    return caps.EncoderResult(**fields)


class CandidateTableTest(unittest.TestCase):
    def test_every_candidate_has_arguments_written_for_it(self) -> None:
        """The invariant that was broken.

        `probe()` may report that any of these works, and `best()` may then return it,
        and the recorder must be able to build a correct command for whatever comes
        back. A name here that `encoders` does not know is a recording that dies after
        the user has pressed record.
        """
        for name, _codec, _vendor in caps.CANDIDATES:
            with self.subTest(name):
                self.assertTrue(
                    encoders.family_of(name),
                    f"{name} is probed but no arguments are written for it",
                )

    def test_the_table_has_no_duplicates(self) -> None:
        names = [name for name, _c, _v in caps.CANDIDATES]
        self.assertEqual(len(names), len(set(names)))

    def test_hardware_comes_before_the_processor(self) -> None:
        names = [name for name, _c, _v in caps.CANDIDATES]
        self.assertEqual(names[-1], "libx264")

    def test_nothing_but_the_processor_sorts_below_av1(self) -> None:
        """Measured ladders first; AV1 is a deliberate choice, never an automatic one.

        So it sorts below every measured family, and above only `libx264`, which has to
        stay the last resort.
        """
        names = [name for name, _c, _v in caps.CANDIDATES]
        first_av1 = next(i for i, name in enumerate(names) if encoders.is_av1(name))
        later = [name for name in names[first_av1:] if not encoders.is_av1(name)]
        self.assertEqual(later, ["libx264"])

    def test_media_foundation_sits_below_the_vendor_families(self) -> None:
        """A machine whose own family works keeps it; one whose family does not still
        gets hardware, because Windows will find whatever MFT is there."""
        names = [name for name, _c, _v in caps.CANDIDATES]
        vendors = [i for i, n in enumerate(names)
                   if encoders.family_of(n) in ("nvenc", "amf", "qsv")
                   and not encoders.is_av1(n)]
        mfs = [i for i, n in enumerate(names) if encoders.family_of(n) == "mf"]
        self.assertTrue(mfs)
        self.assertGreater(min(mfs), max(vendors))


class BestTest(unittest.TestCase):
    def test_hardware_wins_over_the_processor(self) -> None:
        results = [_result("hevc_nvenc"), _result("libx264")]
        self.assertEqual(caps.best(results), "hevc_nvenc")

    def test_the_first_working_hardware_entry_wins(self) -> None:
        results = [_result("hevc_nvenc", works=False), _result("h264_amf"),
                   _result("libx264")]
        self.assertEqual(caps.best(results), "h264_amf")

    def test_media_foundation_is_taken_when_no_vendor_family_works(self) -> None:
        results = [_result("hevc_nvenc", works=False), _result("hevc_amf", works=False),
                   _result("hevc_mf"), _result("libx264")]
        self.assertEqual(caps.best(results), "hevc_mf")

    def test_av1_is_never_chosen_even_when_it_is_the_only_hardware(self) -> None:
        """It probes, it works, and it still must not be picked for somebody.

        Its `cq` ladder and MB/minute were never measured, and `config.Profile` promises
        the viewer that those numbers are measured; a recording is also made to be sent
        to someone whose machine may not open AV1.
        """
        results = [_result("av1_nvenc"), _result("libx264")]
        self.assertEqual(caps.best(results), "libx264")

    def test_av1_is_not_chosen_by_the_software_route_either(self) -> None:
        results = [_result("av1_nvenc"), _result("libx264")]
        self.assertEqual(caps.best(results, prefer_hardware=False), "libx264")

    def test_nothing_working_still_answers(self) -> None:
        results = [_result("hevc_nvenc", works=False), _result("libx264", works=False)]
        self.assertEqual(caps.best(results), "libx264")

    def test_an_empty_probe_answers(self) -> None:
        self.assertEqual(caps.best([]), "libx264")

    def test_whatever_best_returns_can_be_turned_into_a_command(self) -> None:
        """Every reachable answer, end to end: `best()` into `codec_args` without a
        raise. This is the pair that disagreed before `encoders` existed."""
        for name, _codec, _vendor in caps.CANDIDATES:
            with self.subTest(name):
                chosen = caps.best([_result(name), _result("libx264")])
                encoders.codec_args(chosen, quality=23, framerate=60)


class ProbeCommandTest(unittest.TestCase):
    """What the probe actually runs - intercepted, not inspected second-hand.

    The first version of these asserted things about `encoders.codec_args` and never
    touched `try_encoder`, so they would have passed unchanged if the probe had stopped
    calling it. These catch the real command instead.
    """

    def _captured(self, name: str, **kwargs) -> list[str]:
        seen: list[list[str]] = []

        def fake_run(args, timeout=None):
            seen.append(list(args))
            raise OSError("not actually running ffmpeg")

        original = caps._run
        caps._run = fake_run
        try:
            caps.try_encoder("ffmpeg.exe", name, **kwargs)
        finally:
            caps._run = original
        self.assertTrue(seen, "try_encoder ran no command at all")
        return seen[0]

    def test_the_probe_runs_the_arguments_the_recorder_would_build(self) -> None:
        """A probe that tests other arguments than the ones that will run can pass
        while the recording fails. That was true until 2026-11-04."""
        for name in ("hevc_nvenc", "h264_amf", "hevc_qsv", "hevc_mf", "libx264"):
            with self.subTest(name):
                args = self._captured(name)
                for piece in encoders.codec_args(name, quality=23, framerate=30):
                    self.assertIn(piece, args)

    def test_the_probe_runs_the_filter_chain_the_recorder_would_build(self) -> None:
        """The half that was still missing after the first fix.

        Media Foundation accepts software nv12, refuses this build's `hwupload` d3d11
        frames, and accepts the real `ddagrab` surface. Three different answers from one
        encoder, so only the real source answers the real question.
        """
        for name in ("hevc_nvenc", "h264_qsv", "hevc_mf"):
            with self.subTest(name):
                args = " ".join(self._captured(name))
                self.assertIn("ddagrab", args)
                chain = encoders.frame_path(name)
                if chain:
                    self.assertIn(chain, args)

    def test_without_a_desktop_the_probe_says_so_rather_than_failing_everything(
        self,
    ) -> None:
        """On a machine that cannot duplicate a desktop, every hardware encoder would
        otherwise fail for a reason that is not about the encoder."""
        args = " ".join(self._captured("hevc_nvenc", from_screen=False))
        self.assertIn("testsrc", args)
        self.assertNotIn("ddagrab", args)

    def test_media_foundation_is_probed_as_hardware(self) -> None:
        """Without `-hw_encoding true` Windows quietly answers with its own software
        MFT, and the probe would file a CPU encoder under hardware - the single thing
        this module exists to prevent."""
        for name in ("h264_mf", "hevc_mf"):
            with self.subTest(name):
                args = self._captured(name)
                self.assertIn("-hw_encoding", args)
                self.assertEqual(args[args.index("-hw_encoding") + 1], "true")

    def test_media_foundation_gets_nv12_when_the_probe_has_no_desktop(self) -> None:
        args = " ".join(self._captured("hevc_mf", from_screen=False))
        self.assertIn("format=nv12", args)

    def test_an_unknown_name_is_refused_by_the_probe_without_running_ffmpeg(self) -> None:
        works, reason, millis = caps.try_encoder("ffmpeg-that-does-not-exist", "h266_x")
        self.assertFalse(works)
        self.assertIn("h266_x", reason)
        self.assertEqual(millis, 0)

    def test_a_refusal_keeps_the_most_specific_thing_ffmpeg_said(self) -> None:
        """Every refusal used to be filed under the one sentence ffmpeg prints for a
        missing encoder, for hardware that cannot do it, and for a pixel format it will
        not take. Three different answers, one recorded - which is how the Media
        Foundation defect hid for a day."""
        stderr = (
            "[hevc_qsv] Error creating a MFX session: -9.\n"
            "[out#0/null] Nothing was written into output file, because at least one "
            "of its streams received no packets.\n"
        )
        self.assertIn("MFX session", caps._reason(stderr, 1))

    def test_the_cause_is_kept_rather_than_the_cascade_after_it(self) -> None:
        """The real stderr of `hevc_qsv` on this machine, 2026-11-04, kept whole.

        ffmpeg prints the cause first and then six consequences. Keeping the last
        non-vague line recorded "Terminating thread with error: Invalid argument" for
        every one of the seven refusals in this machine's cache - the same sentence for
        a missing Intel device, a missing AMD runtime and an Ampere card with no AV1
        encoder. The settings panel now shows this string to the user as evidence.
        """
        stderr = (
            "[QSV @ 0] Error creating a MFX session: -9.\n"
            "[Parsed_hwmap_0 @ 0] Failed to created derived device context: -1313558101.\n"
            "[Parsed_hwmap_0 @ 0] Failed to configure output pad on Parsed_hwmap_0\n"
            "[vf#0:0 @ 0] Error reinitializing filters!\n"
            "[vf#0:0 @ 0] Task finished with error: Unknown error occurred\n"
            "[vf#0:0 @ 0] Terminating thread with error: Unknown error occurred\n"
            "[vost#0:0/hevc_qsv @ 0] Could not open encoder before EOF\n"
            "[vost#0:0/hevc_qsv @ 0] Task finished with error: Invalid argument\n"
            "[vost#0:0/hevc_qsv @ 0] Terminating thread with error: Invalid argument\n"
            "[out#0/null @ 0] Nothing was written into output file, because at least "
            "one of its streams received no packets.\n"
        )
        reason = caps._reason(stderr, 1)
        self.assertIn("MFX session", reason)
        self.assertNotIn("Terminating thread", reason)

    def test_the_cause_is_kept_for_the_two_other_refusals_measured_here(self) -> None:
        """`hevc_amf` and `av1_nvenc`, also read off this machine on 2026-11-04."""
        amf = (
            "[AMF @ 0] DLL amfrt64.dll failed to open\n"
            "[hevc_amf @ 0] Failed to create derived AMF device context: Unknown error\n"
            "[vost#0:0/hevc_amf @ 0] Error while opening encoder - maybe incorrect "
            "parameters such as bit_rate, rate, width or height.\n"
            "[vf#0:0 @ 0] Terminating thread with error: Unknown error occurred\n"
        )
        self.assertIn("amfrt64.dll", caps._reason(amf, 1))
        nvenc = (
            "[av1_nvenc @ 0] Provided device doesn't support required NVENC features\n"
            "[vost#0:0/av1_nvenc @ 0] Error while opening encoder - maybe incorrect "
            "parameters such as bit_rate, rate, width or height.\n"
            "[vost#0:0/av1_nvenc @ 0] Terminating thread with error: Invalid argument\n"
        )
        self.assertIn("NVENC features", caps._reason(nvenc, 1))

    def test_a_refusal_that_is_nothing_but_the_cascade_keeps_its_first_line(self) -> None:
        """`av1_mf` here says only "Could not open encoder before EOF"."""
        only = "[enc:av1_mf @ 0] Could not open encoder before EOF\n"
        self.assertIn("Could not open encoder", caps._reason(only, 1))

    def test_the_probe_passes_no_preset_of_its_own(self) -> None:
        """It used to append `-preset ultrafast` after `codec_args` had already said
        `-preset veryfast`, so the probe's command was not the recorder's for the one
        family where the preset is the whole cost. Measured on 2026-11-04, two
        2560x1440 frames, three runs each: ultrafast 472/562/481 ms against veryfast
        515/421/423 ms."""
        args = self._captured("libx264")
        self.assertEqual(args.count("-preset"), 1)
        self.assertEqual(args[args.index("-preset") + 1], "veryfast")
        for piece in encoders.codec_args("libx264", quality=23, framerate=30):
            self.assertIn(piece, args)

    def test_a_refusal_with_nothing_specific_still_says_something(self) -> None:
        vague = "[out#0/null] Nothing was written into output file.\n"
        self.assertIn("Nothing was written", caps._reason(vague, 1))
        self.assertIn("exit 7", caps._reason("", 7))


class ProbeBookkeepingTest(unittest.TestCase):
    """What `probe` writes down about how it got its answer, with no ffmpeg running."""

    def _probe(self, listed, answered, on_screen) -> list[caps.EncoderResult]:
        saved: list[list[caps.EncoderResult]] = []
        originals = (caps._listed, caps.screen_available, caps.try_encoder,
                     caps.save_cache)
        caps._listed = lambda ffmpeg: (set(listed), answered)
        caps.screen_available = lambda ffmpeg: on_screen
        caps.try_encoder = lambda ffmpeg, name, from_screen=True: (True, "", 7)
        caps.save_cache = lambda ffmpeg, results: saved.append(results) or ""
        try:
            return caps.probe("ffmpeg.exe", use_cache=False)
        finally:
            (caps._listed, caps.screen_available, caps.try_encoder,
             caps.save_cache) = originals

    def test_a_row_says_which_source_measured_it(self) -> None:
        """A `testsrc` answer is not the same measurement as a `ddagrab` one - for
        `qsv` and `amf` it is a different filter chain - and it is cached and believed
        until the ffmpeg build string changes. Two comments said this was recorded
        before anything recorded it."""
        rows = {r.name: r for r in self._probe(["hevc_nvenc"], True, on_screen=False)}
        self.assertEqual(rows["hevc_nvenc"].source, caps.PROBED_FROM_MEMORY)
        self.assertEqual(rows["hevc_amf"].source, caps.NOT_PROBED)
        rows = {r.name: r for r in self._probe(["hevc_nvenc"], True, on_screen=True)}
        self.assertEqual(rows["hevc_nvenc"].source, caps.PROBED_FROM_SCREEN)

    def test_an_ffmpeg_that_could_not_be_asked_is_not_called_a_missing_feature(
        self,
    ) -> None:
        """An ffmpeg that will not start gives the same empty set as a build with no
        encoders, and every candidate was filed under "this ffmpeg was not built with
        it" - a sentence about the build, for a machine whose trouble is the binary."""
        rows = self._probe([], answered=False, on_screen=True)
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(row.name):
                self.assertIn("could not be asked", row.reason)
        built = self._probe([], answered=True, on_screen=True)
        for row in built:
            with self.subTest(row.name):
                self.assertIn("not built with it", row.reason)

    def test_a_listing_that_exits_non_zero_is_not_an_answer(self) -> None:
        class _Done:
            returncode = 1
            stdout = " V..... h264_nvenc  NVIDIA\n"

        original = caps._run
        caps._run = lambda args, timeout=None: _Done()
        try:
            self.assertEqual(caps._listed("ffmpeg.exe"), (set(), False))
        finally:
            caps._run = original


class CacheTest(unittest.TestCase):
    def test_the_cache_version_moved_with_the_candidate_table(self) -> None:
        """An answer cached before Media Foundation was probed has nothing to say about
        it, one cached before `-hw_encoding true` would call software hardware, and one
        cached before `source` cannot say which pipeline measured it."""
        self.assertGreaterEqual(caps.CACHE_VERSION, 3)


@dataclass(frozen=True)
class _Profile:
    """Exactly the three fields `choose` reads - `capabilities.ProfileLike`."""

    codec: str = "hevc"
    pix_fmt: str = "yuv420p"
    software_only: bool = False


#: Five machines, as a probe would have answered on them. Only the first can be checked
#: against hardware: this machine has an RTX 3070 Ti and no AMD or Intel graphics, so
#: the other four are the shape of a probe result and not a claim about that silicon.
MACHINES: dict[str, tuple[str, ...]] = {
    "NVIDIA only": ("hevc_nvenc", "h264_nvenc", "av1_nvenc", "libx264"),
    "AMD only": ("hevc_amf", "h264_amf", "av1_amf", "libx264"),
    "Intel only": ("hevc_qsv", "h264_qsv", "libx264"),
    "Media Foundation only": ("hevc_mf", "h264_mf", "libx264"),
    "nothing but the processor": ("libx264",),
    # The five above all had `libx264` working, so no test ever entered the branch
    # where the last resort itself failed its probe - which is the branch that produced
    # the one sentence `choose` got wrong.
    "NVIDIA, and libx264 failed its probe": ("hevc_nvenc", "h264_nvenc"),
    "nothing at all": (),
    # AV1 probes clean and is never chosen: plausible on an Arc or a late AMD part.
    "AV1 only": ("av1_qsv", "av1_amf"),
    # The same, with a floor under it. This is the shape that made `choose` blame a
    # family that worked, so `test_no_reason_blames_a_family_that_worked` needs it.
    "AV1 and the processor": ("av1_nvenc", "libx264"),
}


def _machine(names: tuple[str, ...]) -> list[caps.EncoderResult]:
    """A full probe result in which exactly `names` encoded their two frames."""
    return [_result(name, works=name in names) for name, _c, _v in caps.CANDIDATES]


class ChooseTest(unittest.TestCase):
    """`choose` is what `Profile.codec` becomes on this particular machine.

    It replaces the override in `ui/app.py`, which substituted `best()` into any profile
    whose `pix_fmt` was `yuv420p` - so it moved the software profile onto hardware and
    left the 4:4:4 profile pointing at NVENC on machines with no NVENC.
    """

    def test_the_software_profile_stays_on_the_processor_where_nvenc_works(self) -> None:
        """The first of the two defects, stated as a test. `best()` on this machine is
        `hevc_nvenc`, and the profile must still be `libx264`."""
        results = _machine(MACHINES["NVIDIA only"])
        self.assertEqual(caps.best(results), "hevc_nvenc")
        name, reason = caps.choose(results, config.PROFILES["x264"])
        self.assertEqual(name, "libx264")
        self.assertIn("profile", reason)

    def test_the_software_profile_stays_on_the_processor_everywhere(self) -> None:
        for machine, names in MACHINES.items():
            with self.subTest(machine=machine):
                name, _reason = caps.choose(_machine(names), config.PROFILES["x264"])
                self.assertEqual(encoders.family_of(name), "cpu")

    def test_a_444_profile_is_never_given_an_encoder_that_cannot_do_444(self) -> None:
        """The second defect. Measured: only the three NVENC names and `libx264` list
        `yuv444p`; AMF, QSV and Media Foundation list no 4:4:4 format at all."""
        profile = config.PROFILES["hevc444"]
        self.assertEqual(profile.pix_fmt, "yuv444p")
        for machine, names in MACHINES.items():
            with self.subTest(machine=machine):
                name, _reason = caps.choose(_machine(names), profile)
                self.assertTrue(
                    encoders.supports_444(name),
                    f"{machine}: {name} cannot encode 4:4:4",
                )

    def test_a_444_profile_keeps_444_all_the_way_to_the_encoder(self) -> None:
        """Not the same assertion as the one above, and this is the one that catches a
        silent downgrade: `frame_path` rewrites `yuv444p` to `nv12` for the `mf` family,
        so a Media Foundation answer would build a command that runs, plays, and has
        thrown away three quarters of the colour samples."""
        for machine, names in MACHINES.items():
            with self.subTest(machine=machine):
                name, _reason = caps.choose(
                    _machine(names), config.PROFILES["hevc444"]
                )
                chain = encoders.frame_path(name, pix_fmt="yuv444p")
                self.assertIn("format=yuv444p", chain)

    def test_every_profile_resolves_to_a_name_the_recorder_can_build(self) -> None:
        """End to end on each of the five: `choose` into `codec_args` without a raise.
        This is the pair that disagreed before `encoders` existed."""
        for machine, names in MACHINES.items():
            for key, profile in config.PROFILES.items():
                with self.subTest(machine=machine, profile=key):
                    name, _reason = caps.choose(_machine(names), profile)
                    self.assertIn(name, encoders.FAMILIES)
                    encoders.codec_args(
                        name, quality=profile.cq["balanced"], framerate=60,
                        pix_fmt=profile.pix_fmt, extra=profile.extra,
                    )

    def test_the_codec_falls_back_inside_the_family_first(self) -> None:
        """H.265 failed its probe and H.264 did not: stay on the fast path rather than
        leaving the family for the sake of the codec."""
        results = _machine(("h264_nvenc", "hevc_amf", "libx264"))
        name, reason = caps.choose(results, _Profile(codec="hevc"))
        self.assertEqual(name, "h264_nvenc")
        self.assertIn("H.265", reason)
        self.assertIn("nvenc", reason)

    def test_the_family_falls_back_when_nothing_in_it_works(self) -> None:
        results = _machine(MACHINES["Media Foundation only"])
        name, reason = caps.choose(results, _Profile(codec="hevc"))
        self.assertEqual(name, "hevc_mf")
        for skipped in ("nvenc", "amf", "qsv"):
            self.assertIn(skipped, reason)

    def test_the_families_are_tried_in_the_order_of_the_candidate_table(self) -> None:
        self.assertEqual(caps.FAMILY_ORDER, ("nvenc", "amf", "qsv", "mf", "cpu"))
        for machine, names in MACHINES.items():
            with self.subTest(machine=machine):
                name, _reason = caps.choose(_machine(names), _Profile(codec="hevc"))
                chosen = caps.FAMILY_ORDER.index(encoders.family_of(name))
                earlier = [
                    # An AV1-only family is not a better family: `_ranked` never offers
                    # AV1, so passing it over is the rule and not a miss. Without this
                    # the "AV1 only" machine fails a test about family order.
                    f for f in caps.FAMILY_ORDER[:chosen]
                    if any(encoders.family_of(n) == f and not encoders.is_av1(n)
                           for n in names)
                ]
                self.assertEqual(earlier, [], "a better family was passed over")

    def test_av1_is_never_chosen_however_well_it_probes(self) -> None:
        results = _machine(("av1_nvenc", "av1_amf", "libx264"))
        name, _reason = caps.choose(results, _Profile(codec="hevc"))
        self.assertEqual(name, "libx264")

    def test_a_probe_in_which_nothing_worked_still_answers(self) -> None:
        name, reason = caps.choose(_machine(()), _Profile())
        self.assertEqual(name, caps.LAST_RESORT)
        self.assertIn("probe", reason)

    def test_an_empty_probe_answers(self) -> None:
        name, _reason = caps.choose([], config.PROFILES["hevc420"])
        self.assertEqual(name, caps.LAST_RESORT)

    def test_the_reason_is_one_short_line_that_names_the_encoder(self) -> None:
        for machine, names in MACHINES.items():
            for key, profile in config.PROFILES.items():
                with self.subTest(machine=machine, profile=key):
                    name, reason = caps.choose(_machine(names), profile)
                    self.assertTrue(reason.startswith(name + ":"))
                    self.assertNotIn("\n", reason)

    def test_the_last_resort_does_not_claim_the_probe_found_nothing(self) -> None:
        """It found NVENC. What it did not find is anything this profile may use.

        `choose` used to end with one fixed sentence - "nothing on this machine passed
        the encoder probe" - whatever had actually happened, and a status line carries
        it to the user through `_("Recording with {encoder} - {reason}")`.
        """
        results = _machine(MACHINES["NVIDIA, and libx264 failed its probe"])
        name, reason = caps.choose(results, config.PROFILES["x264"])
        self.assertEqual(name, caps.LAST_RESORT)
        self.assertNotIn("nothing on this machine passed", reason)
        # and the software profile's own note survives the fall-through
        self.assertIn("profile", reason)
        self.assertIn(caps.LAST_RESORT, reason.split(":", 1)[1])

    def test_the_last_resort_says_the_families_that_did_work(self) -> None:
        """A 4:4:4 profile on a machine whose hardware works and cannot do 4:4:4."""
        results = _machine(("hevc_amf", "h264_amf"))
        name, reason = caps.choose(results, config.PROFILES["hevc444"])
        self.assertEqual(name, caps.LAST_RESORT)
        self.assertIn("amf cannot do 4:4:4", reason)
        self.assertNotIn("nothing on this machine passed", reason)

    def test_a_family_whose_only_encoder_is_av1_is_not_called_broken(self) -> None:
        """It worked. It was passed over because this program never picks AV1, which is
        a decision made here and not a fault of the machine."""
        results = _machine(("av1_nvenc", "libx264"))
        _name, reason = caps.choose(results, config.PROFILES["hevc420"])
        self.assertIn("AV1", reason)
        self.assertNotIn("nvenc, amf, qsv, mf did not work here", reason)

    def test_the_processor_is_not_blamed_for_an_h265_encoder_it_never_had(self) -> None:
        """"no working H.265 encoder in cpu" read as "your processor's H.265 failed its
        probe". `libx265` is not a candidate of this program; nothing was tried."""
        _name, reason = caps.choose(
            _machine(MACHINES["nothing but the processor"]), config.PROFILES["hevc420"]
        )
        self.assertNotIn("no working H.265 encoder in cpu", reason)
        self.assertIn("H.265", reason)

    def test_a_444_recording_is_not_described_as_never_moving_the_frame(self) -> None:
        """`hevc_nvenc` is a tier-1 encoder and the 4:4:4 profile still copies every
        frame into system memory on the way to it, because nothing here takes 4:4:4 off
        a d3d11 surface. The reason has to describe the chain, not the family."""
        results = _machine(MACHINES["NVIDIA only"])
        name, reason = caps.choose(results, config.PROFILES["hevc444"])
        self.assertEqual(name, "hevc_nvenc")
        chain = encoders.frame_path(name, pix_fmt="yuv444p")
        self.assertTrue(chain.startswith("hwdownload"), chain)
        self.assertNotIn("without moving it", reason)
        self.assertIn("system memory", reason)

    def test_the_420_profile_still_says_the_frame_does_not_move(self) -> None:
        """The other half of the one above: the tier phrase is right when it is right."""
        name, reason = caps.choose(
            _machine(MACHINES["NVIDIA only"]), config.PROFILES["hevc420"]
        )
        self.assertEqual(name, "hevc_nvenc")
        self.assertEqual(encoders.frame_path(name), "")
        self.assertIn("without moving it", reason)

    def test_no_reason_blames_a_family_that_worked(self) -> None:
        """Across every fake machine and every profile: a family that encoded its two
        frames is never named in a "did not work here" clause."""
        for machine, names in MACHINES.items():
            for key, profile in config.PROFILES.items():
                with self.subTest(machine=machine, profile=key):
                    _name, reason = caps.choose(_machine(names), profile)
                    # The first clause carries the "<encoder>: " prefix; without
                    # dropping it the first family in the list reads "libx264: nvenc"
                    # and matches nothing, which is how this test passed against the
                    # behaviour it was written to catch.
                    clauses = reason.split(": ", 1)[1].split("; ")
                    broken = [
                        clause.split(" did not work here")[0]
                        for clause in clauses
                        if clause.endswith("did not work here")
                    ]
                    blamed = set(",".join(broken).replace(" ", "").split(","))
                    worked = {encoders.family_of(n) for n in names}
                    self.assertEqual(
                        blamed & worked, set(),
                        f"{reason!r} blames a family that worked",
                    )

    def test_the_probe_row_order_does_not_decide(self) -> None:
        """`choose` ranks by `CANDIDATES`, not by the order it was handed the rows, so a
        cache written by an older candidate table cannot reorder the choice."""
        results = _machine(MACHINES["NVIDIA only"])
        name, _reason = caps.choose(list(reversed(results)), config.PROFILES["hevc420"])
        self.assertEqual(name, "hevc_nvenc")


if __name__ == "__main__":
    unittest.main()
