"""The DirectShow device-list parser, and the one-PortAudio rule the meters depend on.

Nothing here touches a real device or a real pipe. `parse_dshow_devices` is deliberately
split off from the subprocess call precisely so it can be tested from captured text, and
the PortAudio half is driven against a stand-in module that counts its own instances -
which is the only way to test "two of these crash the interpreter" without crashing the
interpreter.
"""
from __future__ import annotations

import array
import math
import sys
import threading
import time
import types
import unittest

from screenrec import audio, levels, portaudio
from screenrec.audio import CHANNELS, SAMPLE_RATE, MicDevice, parse_dshow_devices

# Shaped exactly like a real capture -
#   ffmpeg -hide_banner -list_devices true -f dshow -i dummy
# - taken on 2026-11-03, with the makes, models and per-device GUIDs replaced by
# invented ones before this became a public repository. Everything the parser can trip
# over is kept: the first device is a camera, each "Alternative name" line follows the
# device it belongs to, one name is Cyrillic, and the run ends with the error that
# listing devices always produces.
REAL_OUTPUT = r'''[in#0 @ 00000229cb501b80] "HD Webcam C-100" (video)
[in#0 @ 00000229cb501b80]   Alternative name "@device_pnp_\\?\usb#vid_1234&pid_5678&mi_00#7&1a2b3c4d&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"
[in#0 @ 00000229cb501b80] "Микрофон (USB Microphone)" (audio)
[in#0 @ 00000229cb501b80]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{1A2B3C4D-5E6F-4071-8293-A4B5C6D7E8F9}"
[in#0 @ 00000229cb501b80] "CABLE Output (VB-Audio Virtual Cable)" (audio)
[in#0 @ 00000229cb501b80]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{2B3C4D5E-6F70-4182-93A4-B5C6D7E8F901}"
Error opening input file dummy.
'''

MIC_ALT = (
    r"@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}"
    r"\wave_{1A2B3C4D-5E6F-4071-8293-A4B5C6D7E8F9}"
)
CABLE_ALT = (
    r"@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}"
    r"\wave_{2B3C4D5E-6F70-4182-93A4-B5C6D7E8F901}"
)


class ParseRealOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.devices = parse_dshow_devices(REAL_OUTPUT)

    def test_two_audio_devices_are_found(self) -> None:
        self.assertEqual(len(self.devices), 2)

    def test_the_video_device_is_ignored(self) -> None:
        names = [device.name for device in self.devices]
        self.assertNotIn("HD Webcam C-100", names)
        for device in self.devices:
            self.assertNotIn("device_pnp_", device.alt_name)

    def test_friendly_names_survive_cyrillic_and_brackets(self) -> None:
        self.assertEqual(self.devices[0].name, "Микрофон (USB Microphone)")
        self.assertEqual(self.devices[1].name, "CABLE Output (VB-Audio Virtual Cable)")

    def test_each_device_is_paired_with_its_own_alternative_name(self) -> None:
        self.assertEqual(self.devices[0].alt_name, MIC_ALT)
        self.assertEqual(self.devices[1].alt_name, CABLE_ALT)

    def test_the_trailing_error_line_is_not_a_device(self) -> None:
        self.assertNotIn("Error opening input file dummy.",
                         [device.name for device in self.devices])


class ParseEdgeCaseTest(unittest.TestCase):
    def test_empty_input(self) -> None:
        self.assertEqual(parse_dshow_devices(""), [])

    def test_output_with_no_devices_at_all(self) -> None:
        text = "[in#0 @ 0] DirectShow video devices\n[in#0 @ 0] Could not enumerate\n"
        self.assertEqual(parse_dshow_devices(text), [])

    def test_audio_device_with_no_alternative_name(self) -> None:
        text = (
            '[in#0 @ 0] "Line In (Realtek Audio)" (audio)\n'
            '[in#0 @ 0] "Микрофон (USB Microphone)" (audio)\n'
            '[in#0 @ 0]   Alternative name "' + MIC_ALT + '"\n'
        )
        devices = parse_dshow_devices(text)
        self.assertEqual(devices, [
            MicDevice(name="Line In (Realtek Audio)", alt_name=""),
            MicDevice(name="Микрофон (USB Microphone)", alt_name=MIC_ALT),
        ])

    def test_last_device_with_no_alternative_name_is_still_emitted(self) -> None:
        text = '[in#0 @ 0] "Line In (Realtek Audio)" (audio)\n'
        self.assertEqual(parse_dshow_devices(text),
                         [MicDevice(name="Line In (Realtek Audio)", alt_name="")])

    def test_a_cameras_alternative_name_is_not_stolen_by_the_audio_device_before_it(self) -> None:
        text = (
            '[in#0 @ 0] "Line In (Realtek Audio)" (audio)\n'
            '[in#0 @ 0] "HD Webcam C-100" (video)\n'
            '[in#0 @ 0]   Alternative name "@device_pnp_\\\\?\\usb#vid_1234"\n'
        )
        devices = parse_dshow_devices(text)
        self.assertEqual(devices, [MicDevice(name="Line In (Realtek Audio)", alt_name="")])

    def test_video_only_output_yields_nothing(self) -> None:
        text = (
            '[in#0 @ 0] "HD Webcam C-100" (video)\n'
            '[in#0 @ 0]   Alternative name "@device_pnp_x"\n'
        )
        self.assertEqual(parse_dshow_devices(text), [])


class MicDeviceTest(unittest.TestCase):
    def test_ffmpeg_id_prefers_the_alternative_name(self) -> None:
        device = MicDevice(name="Микрофон (USB Microphone)", alt_name=MIC_ALT)
        self.assertEqual(device.ffmpeg_id, MIC_ALT)

    def test_ffmpeg_id_falls_back_to_the_friendly_name(self) -> None:
        device = MicDevice(name="Line In (Realtek Audio)", alt_name="")
        self.assertEqual(device.ffmpeg_id, "Line In (Realtek Audio)")

    def test_mic_device_is_hashable_and_comparable(self) -> None:
        # Frozen dataclass: the UI keeps these in sets and compares a remembered device
        # against a freshly enumerated one.
        a = MicDevice(name="X", alt_name="@y")
        b = MicDevice(name="X", alt_name="@y")
        self.assertEqual(a, b)
        self.assertEqual(len({a, b}), 1)


class AudioConstantsTest(unittest.TestCase):
    def test_pipe_format_matches_what_the_recorder_tells_ffmpeg(self) -> None:
        self.assertEqual(SAMPLE_RATE, 48000)
        self.assertEqual(CHANNELS, 2)


# ------------------------------------------------- the one-PortAudio rule and the meters


class FakeStream:
    """A capture stream that always has a quiet block waiting, unless it is wedged."""

    def __init__(self, wedge: threading.Event | None = None) -> None:
        self.wedge = wedge
        self.reads = 0
        self.closed = False

    def get_read_available(self) -> int:
        if self.wedge is not None:
            # What a device that has stopped answering does: the call never returns.
            # Released in the test's cleanup so no thread is left behind.
            self.wedge.wait(30)
        return levels.BLOCK_FRAMES

    def read(self, frames: int, exception_on_overflow: bool = True) -> bytes:
        self.reads += 1
        return bytes(frames * 2 * 2)

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakePyAudio:
    """Stands in for `pyaudiowpatch.PyAudio`, and counts how many of itself exist.

    Two real ones alive at once segfault the interpreter - exit 139, no traceback, see
    `portaudio.py`. That cannot be asserted against the real thing, so this counts
    instead: `peak` is the most that were ever alive together, and it must stay at 1.
    """

    made = 0
    live = 0
    peak = 0
    terminated = 0
    #: How many streams were opened on it, for "two WASAPI streams on one device".
    opened = 0
    wedge: threading.Event | None = None

    def __init__(self) -> None:
        type(self).made += 1
        type(self).live += 1
        type(self).peak = max(type(self).peak, type(self).live)
        self.streams: list[FakeStream] = []

    @classmethod
    def reset(cls) -> None:
        cls.made = cls.live = cls.peak = cls.terminated = cls.opened = 0
        cls.wedge = None

    def terminate(self) -> None:
        type(self).live -= 1
        type(self).terminated += 1

    def open(self, **kwargs) -> FakeStream:
        stream = FakeStream(type(self).wedge)
        self.streams.append(stream)
        type(self).opened += 1
        return stream

    def get_device_info_by_index(self, index: int) -> dict:
        return {"index": index, "name": f"Fake device {index}",
                "maxInputChannels": 2, "defaultSampleRate": 48000}

    def get_default_wasapi_loopback(self) -> dict:
        return self.get_device_info_by_index(7)


class PortAudioTestCase(unittest.TestCase):
    """Every test here runs against the stand-in module, with the shared state reset."""

    def setUp(self) -> None:
        FakePyAudio.reset()
        fake = types.ModuleType("pyaudiowpatch")
        fake.PyAudio = FakePyAudio
        fake.paInt16 = 8
        previous = sys.modules.get("pyaudiowpatch")
        sys.modules["pyaudiowpatch"] = fake
        self.addCleanup(self._restore_module, previous)
        self.addCleanup(self._restore_portaudio,
                        portaudio._instance, portaudio._users)
        portaudio._instance = None
        portaudio._users = 0

    @staticmethod
    def _restore_module(previous) -> None:
        if previous is None:
            sys.modules.pop("pyaudiowpatch", None)
        else:
            sys.modules["pyaudiowpatch"] = previous

    @staticmethod
    def _restore_portaudio(instance, users) -> None:
        portaudio._instance = instance
        portaudio._users = users

    @staticmethod
    def meter_threads() -> list[str]:
        return sorted(t.name for t in threading.enumerate()
                      if t.name.startswith("meter-"))


class SharedPortAudioTest(PortAudioTestCase):
    def test_many_callers_at_once_still_make_exactly_one_instance(self) -> None:
        """The race the shared instance exists for: two constructions from two threads."""
        barrier = threading.Barrier(12)

        def hold() -> None:
            barrier.wait(10)
            with portaudio.session():
                time.sleep(0.05)

        workers = [threading.Thread(target=hold, daemon=True) for _ in range(12)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        self.assertEqual(FakePyAudio.made, 1, "a second PortAudio was constructed")
        self.assertEqual(FakePyAudio.peak, 1, "two were alive at the same moment")
        self.assertEqual(portaudio.users(), 0, "a caller was never counted out")

    def test_the_last_caller_out_terminates_it(self) -> None:
        with portaudio.session():
            with portaudio.session():
                self.assertEqual(portaudio.users(), 2)
            self.assertEqual(FakePyAudio.terminated, 0, "terminated with a caller left")
        self.assertEqual(FakePyAudio.terminated, 1)
        self.assertEqual(FakePyAudio.live, 0)

    def test_a_meter_during_a_recording_adds_no_second_instance(self) -> None:
        """The crash this guards: the feeder holds one, the settings panel opens a meter.

        `audio.LoopbackFeeder` keeps a session for the whole recording, so every meter
        started while the panel is open during one is the second PortAudio - which is
        exactly the pair that segfaults.
        """
        with portaudio.session():  # stands in for the running feeder
            meter = levels.LevelMeter(3)
            self.assertEqual(meter.start(), "")
            self.addCleanup(meter.stop)
            self.assertTrue(_eventually(lambda: meter.frames_read > 0, 5.0),
                            "the meter never read anything: " + meter.error)
            # The recording, the meter's session and the meter's open stream: three
            # callers, counted separately, holding one instance between them.
            self.assertEqual(portaudio.users(), 3)
            meter.stop()

        self.assertEqual(FakePyAudio.made, 1, "the meter built its own PortAudio")
        self.assertEqual(FakePyAudio.peak, 1)


class MeterCannotBeOpenedTwiceTest(PortAudioTestCase):
    def test_a_running_meter_refuses_a_second_start(self) -> None:
        meter = levels.LevelMeter(3)
        self.assertEqual(meter.start(), "")
        self.addCleanup(meter.stop)
        self.assertTrue(_eventually(lambda: meter.frames_read > 0, 5.0))

        self.assertNotEqual(meter.start(), "", "it let a second thread in")
        self.assertEqual(self.meter_threads(), ["meter-3"])

    def test_a_wedged_meter_still_refuses_a_second_start(self) -> None:
        """The hole the old `stop` left open.

        It cleared `_thread` *before* joining, so a meter whose device had stopped
        answering reported itself stopped while its thread was still inside the device
        call - and the next `start` saw a free slot. Measured against the old body: two
        `meter-3` threads alive at once on one `LevelMeter`, and `start` returned "".
        """
        wedge = threading.Event()
        FakePyAudio.wedge = wedge
        # Let the stuck thread out when the test ends and wait for it, or it is still
        # named meter-3 while the next test counts meter threads.
        self.addCleanup(lambda: _eventually(lambda: self.meter_threads() == [], 5.0))
        self.addCleanup(wedge.set)

        meter = levels.LevelMeter(3)
        self.assertEqual(meter.start(), "")
        self.assertTrue(_eventually(lambda: self.meter_threads() == ["meter-3"], 5.0))

        meter.stop(timeout=0.2)  # the device will not answer, so the join times out
        self.assertTrue(meter.active, "a meter stuck in a device call is not stopped")
        self.assertNotEqual(meter.start(), "",
                            "a second stream was opened on a wedged device")
        self.assertEqual(self.meter_threads(), ["meter-3"], "two threads on one meter")

    def test_a_stopped_meter_leaves_no_thread_behind(self) -> None:
        meter = levels.LevelMeter(3)
        self.assertEqual(meter.start(), "")
        self.assertTrue(_eventually(lambda: meter.frames_read > 0, 5.0))
        meter.stop()

        self.assertEqual(self.meter_threads(), [])
        self.assertFalse(meter.active)

    def test_a_wedged_device_gets_no_second_meter_from_the_pair(self) -> None:
        """`LevelPair.stop` used to throw away the guard `LevelMeter.stop` hands it.

        A meter whose device call will not come back is deliberately left "running", so
        that `start`'s "already running" guard refuses a second WASAPI stream on that
        device. `LevelPair.stop` dropped the object anyway, and the next
        `LevelPair.start` built a *fresh* `LevelMeter` on the same index - which a
        per-object guard has never met. `LevelPair.start` runs every time the settings
        panel closes.

        Measured on 2026-11-05 against the old body, with a stand-in device whose read
        never returns: three restarts left four `meter-9` threads and four open streams
        on one device, `pair.notes` empty, and `portaudio.users()` at 8 instead of 2 -
        so `release()` never reached zero and PortAudio was never terminated.
        """
        wedge = threading.Event()
        FakePyAudio.wedge = wedge
        self.addCleanup(lambda: _eventually(lambda: self.meter_threads() == [], 5.0))
        self.addCleanup(wedge.set)

        pair = levels.LevelPair()
        self.addCleanup(pair.stop)
        self.assertEqual(pair.start(loopback_index=9), [])
        self.assertTrue(_eventually(lambda: self.meter_threads() == ["meter-9"], 5.0))
        held = portaudio.users()

        for attempt in range(3):
            notes = pair.start(loopback_index=9)
            self.assertEqual(self.meter_threads(), ["meter-9"],
                             f"restart {attempt}: a second thread on a wedged device")
            self.assertEqual(FakePyAudio.opened, 1,
                             f"restart {attempt}: a second stream on a wedged device")
            self.assertEqual(portaudio.users(), held,
                             f"restart {attempt}: another PortAudio reference kept")
            self.assertTrue(notes, "the user was told nothing at all")
            self.assertIn("Fake device 9", notes[0], "the note does not name the device")
            self.assertIsNone(pair.system, "a meter that was never opened is held out")

    def test_a_device_that_answers_again_may_be_metered_again(self) -> None:
        """The slot is held while the stuck thread is alive, not for the whole session."""
        wedge = threading.Event()
        FakePyAudio.wedge = wedge
        self.addCleanup(lambda: _eventually(lambda: self.meter_threads() == [], 5.0))
        self.addCleanup(wedge.set)

        pair = levels.LevelPair()
        self.addCleanup(pair.stop)
        self.assertEqual(pair.start(loopback_index=9), [])
        self.assertTrue(_eventually(lambda: self.meter_threads() == ["meter-9"], 5.0))
        self.assertTrue(pair.start(loopback_index=9), "the wedged meter was not kept")

        wedge.set()  # the device answers again and the stuck thread unwinds
        self.assertTrue(_eventually(lambda: self.meter_threads() == [], 5.0))

        self.assertEqual(pair.start(loopback_index=9), [],
                         "the device was held against a thread that had finished")
        self.assertIsNotNone(pair.system)

    def test_restarting_the_pair_does_not_leave_the_old_meter_running(self) -> None:
        """The bar restarts the pair on every settings change, recording or not."""
        pair = levels.LevelPair()
        self.addCleanup(pair.stop)
        self.assertEqual(pair.start(loopback_index=5), [])
        first = pair.system
        self.assertTrue(_eventually(lambda: first.frames_read > 0, 5.0))

        self.assertEqual(pair.start(loopback_index=5), [])
        self.assertIsNot(pair.system, first)
        self.assertFalse(first.active, "the old meter is still metering")
        self.assertEqual(self.meter_threads(), ["meter-5"], "two meters on one device")
        self.assertEqual(FakePyAudio.peak, 1)


def _eventually(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


if __name__ == "__main__":
    unittest.main()


class SilenceIsNotTheSameAsNothingTest(unittest.TestCase):
    """A track can be full of real, flowing, perfectly silent audio.

    That is what recording the wrong playback endpoint sounds like, and it cost a whole
    recorded call on 2026-11-13: frames arrived, so the only check there was -
    `frames_written == 0` - said the recording was fine.

    What counts as sound is **swing, not level**. Measured on the author's machine the
    same day: with nothing playing, the headphone endpoint's loopback delivers a steady
    -8 to -10, because the driver never writes a true zero, while Windows' own meter for
    it reads 0.000. The first version of this used a level threshold of 8 and called
    that silence sound - on the one device the user actually records.
    """

    @staticmethod
    def _flat(left: int, right: int, frames: int = 480) -> bytes:
        """A block that does not move: digital silence, or a driver's DC offset."""
        block = array.array("h")
        for _ in range(frames):
            block.append(left)
            block.append(right)
        return block.tobytes()

    @staticmethod
    def _tone(amplitude: int, frames: int = 480, left: bool = True,
              right: bool = True) -> bytes:
        block = array.array("h")
        for index in range(frames):
            value = int(amplitude * math.sin(2 * math.pi * 440 * index / 48000))
            block.append(value if left else 0)
            block.append(value if right else 0)
        return block.tobytes()

    def test_digital_silence_carries_nothing(self) -> None:
        self.assertFalse(audio._has_sound(self._flat(0, 0)))

    def test_a_driver_that_never_writes_zero_is_still_silent(self) -> None:
        """The measured case: a steady -9 on both channels, nothing playing."""
        self.assertFalse(audio._has_sound(self._flat(-9, -9)))
        self.assertFalse(audio._has_sound(self._flat(-10, -8)))

    def test_a_loud_constant_is_not_sound_either(self) -> None:
        """A level that never changes is an offset, whatever its size."""
        self.assertFalse(audio._has_sound(self._flat(9000, 9000)))

    def test_quiet_speech_is_sound(self) -> None:
        self.assertTrue(audio._has_sound(self._tone(300)))

    def test_something_barely_above_the_floor_is_sound(self) -> None:
        self.assertTrue(audio._has_sound(self._tone(audio.AUDIBLE_SPAN)))

    def test_one_channel_is_enough(self) -> None:
        """The stride is odd for this: an even one visits a single channel for ever,
        and audio in the other would read as silence."""
        self.assertTrue(audio._has_sound(self._tone(9000, left=True, right=False)))
        self.assertTrue(audio._has_sound(self._tone(9000, left=False, right=True)))

    def test_it_never_raises_on_odd_input(self) -> None:
        self.assertFalse(audio._has_sound(b""))
        self.assertFalse(audio._has_sound(b"\x01"))
        self.assertTrue(audio._has_sound(self._tone(9000) + b"\x01"))

    def test_a_fresh_feeder_has_counted_nothing(self) -> None:
        feeder = audio.LoopbackFeeder("pipe", device_index=-1)
        self.assertEqual(feeder.frames_written, 0)
        self.assertEqual(feeder.audible_frames, 0)


class TheLoopbackDeviceLabelTest(unittest.TestCase):
    def test_portaudio_s_suffix_is_dropped(self) -> None:
        device = audio.LoopbackDevice(
            index=24, name="Headphones (Temple) [Loopback]", channels=2, rate=48000
        )
        self.assertEqual(device.label, "Headphones (Temple)")

    def test_a_name_without_it_is_left_alone(self) -> None:
        device = audio.LoopbackDevice(
            index=24, name="Headphones (Temple)", channels=2, rate=48000
        )
        self.assertEqual(device.label, "Headphones (Temple)")

    def test_a_name_that_is_only_the_suffix_keeps_something(self) -> None:
        """Better a strange name than an empty row in the list."""
        device = audio.LoopbackDevice(
            index=24, name=" [Loopback]", channels=2, rate=48000
        )
        self.assertTrue(device.label.strip())



class ResolvingAnEndpointByNameTest(unittest.TestCase):
    """A PortAudio index is a position in a list, not an identity.

    PortAudio enumerates its devices once, when it starts, in the order it finds
    them - so plugging a speaker in renumbers everything after it. A user's settings
    pointed at index 21, a speaker had been added since, and the Source list showed
    `Device 21 (not found)` with nothing recorded until they picked a device by hand.
    """

    class _Device:
        def __init__(self, index, label) -> None:
            self.index = index
            self.label = label

    def setUp(self) -> None:
        self.devices = [
            self._Device(25, "CABLE Input (VB-Audio Virtual Cable)"),
            self._Device(27, "Speakers (Desk)"),
            self._Device(29, "Headset (Bluetooth)"),
        ]

    def test_a_name_finds_its_device(self) -> None:
        self.assertEqual(
            audio.index_for_label("Headset (Bluetooth)", self.devices), 29
        )

    def test_a_name_that_is_not_there_is_minus_one(self) -> None:
        self.assertEqual(audio.index_for_label("Headset (gone)", self.devices), -1)

    def test_no_name_is_minus_one(self) -> None:
        self.assertEqual(audio.index_for_label("", self.devices), -1)

    def test_a_near_match_is_not_a_match(self) -> None:
        """Recording the wrong endpoint is silent, and is exactly the failure that
        cost a recorded call. Never guess."""
        self.assertEqual(audio.index_for_label("Headset", self.devices), -1)
        self.assertEqual(audio.index_for_label("headset (bluetooth)", self.devices), -1)

    def test_the_reverse_names_a_device(self) -> None:
        self.assertEqual(audio.label_for_index(27, self.devices), "Speakers (Desk)")

    def test_the_default_is_not_a_device_and_has_no_name(self) -> None:
        self.assertEqual(audio.label_for_index(-1, self.devices), "")

    def test_an_index_nothing_sits_at_has_no_name(self) -> None:
        self.assertEqual(audio.label_for_index(21, self.devices), "")

    def test_a_sound_stack_that_raises_is_not_an_error_of_its_own(self) -> None:
        class _Explodes(list):
            def __iter__(self):
                raise OSError("the audio stack is unhappy")

        self.assertEqual(audio.index_for_label("anything", _Explodes()), -1)
        self.assertEqual(audio.label_for_index(1, _Explodes()), "")
