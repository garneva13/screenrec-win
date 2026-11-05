"""Is sound actually arriving? Measured, before and during a recording.

The failure this exists to prevent has already happened once here: a recording was made
with system sound switched on, the chosen playback device was a headset that was listed
but unplugged, and the result was a silent track with no warning until afterwards. A
meter answers that question *before* the recording starts, in the only way that is
convincing - by showing the needle move.

Two devices, two mechanisms, for the same reason as in `audio.py`:

* **System sound** is metered from the WASAPI loopback of a playback endpoint, exactly
  as `audio.LoopbackFeeder` records it.
* **The microphone** is metered through WASAPI as well, even though ffmpeg records it
  through DirectShow. That is deliberate: WASAPI shared mode allows several clients on
  one device, so the meter can run while ffmpeg is recording. Opening the same device
  twice through DirectShow would not be safe.

The cost of that choice is a name match: ffmpeg names a DirectShow device and WASAPI
names the same hardware slightly differently, so `match_input` has to pair them. It is
reported when it cannot, rather than silently metering the wrong microphone.

**The sentences in `notes` and `warnings` are English only, and that is a known hole.**
The house rule is that anything a user reads goes through `i18n._()` and has a Russian
pair. It cannot here: `i18n` imports `QtCore`, and this module is deliberately Qt-free -
its only import outside the standard library is `portaudio`, and its code runs on a
metering thread that must not touch Qt. These strings reach the bar through `ui/app.py`,
which is where the translation would have to be done. Same hole, same reason, as
`audio.py`.
"""
from __future__ import annotations

import array
import re
import threading
import time
from dataclasses import dataclass

from . import portaudio

#: How fast the displayed level falls when the sound stops. A meter that drops instantly
#: flickers; one that falls slowly lies about the present.
DECAY_PER_SECOND = 2.6
BLOCK_FRAMES = 480  # 10 ms at 48 kHz
#: Below this, over this long, the device is treated as producing nothing at all.
SILENCE_LEVEL = 0.0015
SILENCE_SECONDS = 3.0


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    channels: int
    rate: int


def _peak(data: bytes) -> float:
    """Loudest sample in a block of signed 16-bit PCM, as 0..1.

    Written out rather than taken from `audioop`, which Python 3.13 removed. A block is
    960 samples at 10 ms, so the scan costs nothing at the rate this runs.
    """
    samples = array.array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if not samples:
        return 0.0
    loudest = max(max(samples), -min(samples))
    return min(1.0, loudest / 32768.0)


def _normalise(name: str) -> str:
    """Strip the decoration each API adds, so two names for one device can be compared.

    DirectShow says "Микрофон (USB Microphone)" where WASAPI says
    "Микрофон (USB Microphone)" or "USB Microphone" - and either may carry an index
    prefix like "2- ". What is left after this is the part that identifies the hardware.
    """
    text = name.lower()
    text = re.sub(r"\(\s*\d+\s*-\s*", "(", text)
    text = re.sub(r"^\s*\d+\s*-\s*", "", text)
    text = re.sub(r"\[(loopback|вход|output)\]", " ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def list_inputs() -> list[InputDevice]:
    """WASAPI capture endpoints - microphones and line inputs, not loopbacks."""
    if not portaudio.available():
        return []
    found: list[InputDevice] = []
    try:
        import pyaudiowpatch as pyaudio

        with portaudio.session() as handle:
            api = handle.get_host_api_info_by_type(pyaudio.paWASAPI)
            for position in range(int(api["deviceCount"])):
                info = handle.get_device_info_by_host_api_device_index(
                    int(api["index"]), position
                )
                if int(info["maxInputChannels"]) <= 0:
                    continue
                if "[loopback]" in str(info["name"]).lower():
                    continue
                found.append(
                    InputDevice(
                        index=int(info["index"]),
                        name=str(info["name"]),
                        channels=int(info["maxInputChannels"]),
                        rate=int(info["defaultSampleRate"]),
                    )
                )
    except Exception:
        # Whatever is listed so far is still useful; an empty list is the honest
        # answer when nothing could be read at all.
        return found
    return found


def match_input(dshow_name: str, devices: list[InputDevice] | None = None) -> int:
    """The WASAPI index for the microphone ffmpeg knows by its DirectShow name.

    Returns -1 when no device matches well enough, which the caller must report rather
    than quietly meter whichever device happened to be first.
    """
    if not dshow_name:
        return -1
    pool = devices if devices is not None else list_inputs()
    if not pool:
        return -1
    wanted = set(_normalise(dshow_name).split())
    if not wanted:
        return -1

    best_index, best_score = -1, 0.0
    for device in pool:
        words = set(_normalise(device.name).split())
        if not words:
            continue
        shared = wanted & words
        score = len(shared) / max(len(wanted), len(words))
        if score > best_score:
            best_index, best_score = device.index, score
    # Half the words in common is a real match; less is a coincidence.
    return best_index if best_score >= 0.5 else -1


class LevelMeter:
    """Peak level of one capture device, 0..1, with a decay.

    Single use: `start`, read `level` as often as you like from any thread, `stop`.
    Reading is a plain float load, so a 20 Hz repaint costs nothing.
    """

    def __init__(self, device_index: int, loopback: bool = False) -> None:
        self.device_index = device_index
        self.loopback = loopback
        #: Filled in by `_run` once the device has been read, so a message about a
        #: meter that will not stop can name the device rather than its index.
        self.device_name = ""
        self.level: float = 0.0
        self.peak: float = 0.0
        self.error: str = ""
        self.frames_read = 0
        self._silent_since: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> str:
        if self._thread is not None:
            return "That meter is already running."
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"meter-{self.device_index}", daemon=True
        )
        self._thread.start()
        return ""

    def stop(self, timeout: float = 1.5) -> None:
        """Stop metering, and wait for the thread to really finish before saying so.

        The join comes **before** `_thread` is cleared, and the clearing is skipped
        altogether when the thread outlives the timeout. Clearing first made `active`
        report a stopped meter while its thread was still inside a device call, and
        `start`'s "already running" guard - the only thing between this object and two
        WASAPI streams on one device - then saw a free slot and let a second thread in.
        Measured with a stand-in device whose read never returns: the old order left two
        meter threads alive on one `LevelMeter`; this one leaves one, and `start` says
        the meter is already running.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                # Parked in a device call that is not coming back. The object stays
                # "running" on purpose: a wedged meter is a reason to open no second one
                # on that device, not a reason to pretend this one has gone.
                return
        self._thread = None
        self.level = 0.0

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def label(self) -> str:
        """What to call this meter in front of a user."""
        return self.device_name or f"device {self.device_index}"

    @property
    def silent(self) -> bool:
        """Nothing above the noise floor for a few seconds - worth saying out loud."""
        if self._silent_since is None:
            return False
        return (time.monotonic() - self._silent_since) >= SILENCE_SECONDS

    # -- internals ---------------------------------------------------------

    def _run(self) -> None:
        """Meter one device on the SHARED PortAudio instance.

        Never construct a PyAudio here. Two instances alive at once segfault the
        interpreter - measured - and a meter running during a recording would be the
        second, because the loopback feeder already holds one.
        """
        if not portaudio.available():
            self.error = "pyaudiowpatch is not installed; levels cannot be shown."
            return
        import pyaudiowpatch as pyaudio

        name = f"device {self.device_index}"
        try:
            with portaudio.session() as handle:
                info = handle.get_device_info_by_index(self.device_index)
                name = str(info.get("name", name))
                self.device_name = name
                channels = min(2, max(1, int(info["maxInputChannels"])))
                rate = int(info["defaultSampleRate"])
                with portaudio.opened(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    frames_per_buffer=BLOCK_FRAMES,
                    input=True,
                    input_device_index=self.device_index,
                ) as stream:
                    self._pump(stream, channels)
        except Exception as exc:
            if not self.error:
                self.error = f"\u201c{name}\u201d could not be metered: {exc}"

    def _pump(self, stream, channels: int) -> None:
        last = time.monotonic()
        self._silent_since = last
        while not self._stop.is_set():
            try:
                available = stream.get_read_available()
            except Exception as exc:
                self.error = self.error or f"The device stopped answering: {exc}"
                break
            now = time.monotonic()
            if available >= BLOCK_FRAMES:
                try:
                    data = stream.read(BLOCK_FRAMES, exception_on_overflow=False)
                except Exception as exc:
                    self.error = self.error or f"The device stopped answering: {exc}"
                    break
                self.frames_read += BLOCK_FRAMES
                value = _peak(data)
                if value > self.level:
                    self.level = min(1.0, value)
                self.peak = max(self.peak, self.level)
                if value >= SILENCE_LEVEL:
                    self._silent_since = None
                elif self._silent_since is None:
                    self._silent_since = now
            else:
                time.sleep(0.004)

            # Decay towards zero so the meter tracks the present, not the loudest moment.
            elapsed, last = now - last, now
            if self.level > 0:
                self.level = max(0.0, self.level - DECAY_PER_SECOND * elapsed)


class LevelPair:
    """The two meters the bar shows, started and stopped together."""

    def __init__(self) -> None:
        self.mic = None
        self.system = None
        self.notes: list[str] = []
        #: Meters whose thread would not come back, by device index. At most one per
        #: index, because `_open` refuses to build a second on an index that is in
        #: here. See `stop`.
        self._wedged: dict[int, LevelMeter] = {}

    def start(self, mic_dshow_name: str = "", loopback_index: int = -1) -> list[str]:
        """Returns anything the user should be told; an empty list means all is well."""
        self.stop()
        self.notes = []

        if mic_dshow_name:
            index = match_input(mic_dshow_name)
            if index < 0:
                self.notes.append(
                    f"The level of “{mic_dshow_name}” cannot be shown: no "
                    f"matching Windows input device. Recording is unaffected."
                )
            else:
                self.mic = self._open(index, loopback=False)

        if loopback_index >= 0:
            self.system = self._open(loopback_index, loopback=True)

        return list(self.notes)

    def _open(self, index: int, loopback: bool) -> LevelMeter | None:
        """A started meter on `index`, or None with the reason put in `notes`.

        A device that is already being metered by a thread that will not come back gets
        no second meter and no second WASAPI stream - that is the whole point of
        `stop` keeping hold of it.
        """
        stuck = self._wedged.get(index)
        if stuck is not None:
            if stuck.active:
                self.notes.append(
                    f"“{stuck.label}” has stopped answering, so its level "
                    f"cannot be shown and no second meter will be opened on it. "
                    f"Recording is unaffected."
                )
                return None
            # It came back after all, late. The slot is free again.
            del self._wedged[index]
        meter = LevelMeter(index, loopback=loopback)
        problem = meter.start()
        if problem:
            self.notes.append(problem)
            return None
        return meter

    def stop(self) -> None:
        """Stop both meters, and keep hold of one that would not stop.

        `LevelMeter.stop` deliberately leaves a meter "running" when its thread is
        parked in a device call that is not coming back, so that `start`'s
        "already running" guard refuses a second WASAPI stream on that device. Dropping
        the object here threw that guard away: the next `start` built a *fresh*
        `LevelMeter` on the same index, and a per-object guard cannot see a meter it has
        never met.

        Measured on 2026-11-05 with a stand-in device whose read never returns, against
        the old body: four `LevelPair.start` calls left four `meter-9` threads and four
        open streams on one device, with `notes` empty - nothing said at all - and
        `portaudio.users()` at 8 rather than 0, so PortAudio was never terminated.
        `LevelPair.start` runs every time the settings panel closes.
        """
        for name in ("mic", "system"):
            meter = getattr(self, name)
            setattr(self, name, None)
            if meter is None:
                continue
            meter.stop()
            if meter.active:
                self._wedged[meter.device_index] = meter

    def mic_level(self) -> float:
        return self.mic.level if self.mic is not None else 0.0

    def system_level(self) -> float:
        return self.system.level if self.system is not None else 0.0

    def warnings(self) -> list[str]:
        """Devices that are open but have produced nothing for a few seconds."""
        found: list[str] = []
        if self.mic is not None and self.mic.error:
            found.append(self.mic.error)
        elif self.mic is not None and self.mic.silent:
            found.append("The microphone is open but silent.")
        if self.system is not None and self.system.error:
            found.append(self.system.error)
        elif self.system is not None and self.system.silent:
            found.append("Nothing is playing, so there is no system sound to record.")
        return found
