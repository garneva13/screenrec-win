"""Which playback device Windows is using, and which ones are making a sound.

Both questions are asked of the Windows audio endpoints directly, through COM, and
neither opens a capture stream. That is the whole reason this module exists rather than
more of `levels.py`: a recording in progress already holds a WASAPI capture on one
endpoint, and the answer to "is the *other* one playing?" must not need a second one.

Two facts, both measured on 2026-11-13:

* `IMMDeviceEnumerator::GetDefaultAudioEndpoint` costs **1.3 ms** with the enumerator
  held open, against 17 ms for the same answer through PortAudio - which also starts
  and stops PortAudio underneath a running capture, which is not something to do every
  second.
* `IAudioMeterInformation::GetPeakValue` reads the peak Windows is *already* computing
  for an endpoint. A sweep of all four endpoints on this machine costs **0.34 ms**, and
  a tone played into the virtual cable showed up on the cable's two endpoints at 0.366
  and on neither headphone endpoint - so it really does say which device the sound is
  coming out of.

**One thread.** A COM object belongs to the apartment that created it. Everything here
is created and used on the thread that constructs `Endpoints`, which in this program is
the Qt main thread; `close` must be called there too. Nothing in here is thread-safe and
nothing in here should be made thread-safe - the fix for wanting it elsewhere is a
second instance on that thread.

**Windows only, and failure is not fatal.** Every call reports rather than raises:
`problem` holds the reason the enumerator could not be built, and the two questions then
answer "" and {}. A program that cannot ask which device is default must still record.
"""
from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_float, c_void_p, c_wchar_p
from ctypes.wintypes import DWORD

__all__ = ["available", "Endpoints", "AUDIBLE_PEAK"]

#: Above this, an endpoint is carrying something worth calling sound. Measured on
#: 2026-11-13: a 440 Hz tone at a normal level peaked at 0.366, while an endpoint with
#: nothing playing sat at 0.000 with occasional residue around 0.0004. Two hundredths
#: is far above the residue and far below anything a person is listening to.
AUDIBLE_PEAK = 0.02

#: `ctypes.wintypes` has no HRESULT; it is a signed 32-bit result code.
_HRESULT = ctypes.c_long

try:
    _ole32 = ctypes.WinDLL("ole32")
except (OSError, AttributeError):  # not Windows
    _ole32 = None

_CLSCTX_ALL = 23
_COINIT_APARTMENTTHREADED = 0x2
#: CoInitializeEx answers: already initialised this way, and initialised the other way.
_S_FALSE = 1
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106 as a signed long
_STGM_READ = 0
_RENDER = 0  # EDataFlow::eRender - playback endpoints
_ACTIVE = 0x1  # DEVICE_STATE_ACTIVE
#: ERole::eConsole. The role a program gets when it does not ask for one, and the one
#: Windows moves first when a device disappears.
_CONSOLE = 0


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", DWORD),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text: str) -> None:
        super().__init__()
        if _ole32 is not None:
            _ole32.CLSIDFromString(c_wchar_p(text), byref(self))


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", DWORD)]


class _PROPVARIANT(ctypes.Structure):
    """Only the shape needed to read a string out of one, and to clear it again."""

    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("reserved1", ctypes.c_ushort),
        ("reserved2", ctypes.c_ushort),
        ("reserved3", ctypes.c_ushort),
        ("pwszVal", c_void_p),
        ("tail", ctypes.c_byte * 8),
    ]


_CLSID_MMDeviceEnumerator = _GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_IID_IMMDeviceEnumerator = _GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
_IID_IAudioMeterInformation = _GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
#: PKEY_Device_FriendlyName - "Speakers (Realtek High Definition Audio)".
_PKEY_FRIENDLY = _PROPERTYKEY()
_PKEY_FRIENDLY.fmtid = _GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}")
_PKEY_FRIENDLY.pid = 14


def available() -> bool:
    """Whether COM is here at all. False everywhere that is not Windows."""
    return _ole32 is not None


def _call(interface: c_void_p, slot: int, argtypes: tuple, *args) -> int:
    """One method off a COM vtable, by slot number.

    The argument types are passed in rather than derived from the arguments:
    `type(byref(x))` is `CArgObject`, which ctypes refuses in `_argtypes_`.
    """
    vtable = ctypes.cast(interface, POINTER(POINTER(c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(_HRESULT, c_void_p, *argtypes)
    return int(prototype(vtable[slot])(interface, *args))


def _release(interface: c_void_p | None) -> None:
    """IUnknown::Release, slot 2. Safe on None and on a pointer that is already gone."""
    if not interface:
        return
    try:
        _call(interface, 2, ())
    except Exception:  # noqa: BLE001 - teardown must not raise
        pass


class Endpoints:
    """The playback endpoints, their names, and what is coming out of them.

    Build one, keep it, ask it as often as you like, and `close` it when the window
    that owns it goes away. Rebuilding one per question would cost the COM object
    creation each time, which is the 17 ms this exists to avoid.
    """

    def __init__(self) -> None:
        self.problem: str = ""
        self._enumerator: c_void_p | None = None
        #: (friendly name, IMMDevice, IAudioMeterInformation) for each active endpoint,
        #: filled by `refresh`. Held open because activating a meter is the expensive
        #: part and the set of devices rarely changes.
        self._devices: list[tuple[str, c_void_p, c_void_p | None]] = []
        if _ole32 is None:
            self.problem = "COM is not available on this system."
            return
        started = _ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        # 0 and S_FALSE are both "COM is usable here". RPC_E_CHANGED_MODE means some
        # other code in this thread chose the other apartment, which is still usable -
        # it is only this call that was refused.
        if started not in (0, _S_FALSE, _RPC_E_CHANGED_MODE):
            self.problem = f"COM would not start (0x{started & 0xFFFFFFFF:08X})."
            return
        enumerator = c_void_p()
        created = _ole32.CoCreateInstance(
            byref(_CLSID_MMDeviceEnumerator), None, DWORD(_CLSCTX_ALL),
            byref(_IID_IMMDeviceEnumerator), byref(enumerator),
        )
        if created or not enumerator:
            self.problem = (
                f"The audio endpoint list could not be opened "
                f"(0x{created & 0xFFFFFFFF:08X})."
            )
            return
        self._enumerator = enumerator
        self.refresh()

    # ------------------------------------------------------------------ names

    def default_name(self) -> str:
        """The friendly name of the playback device Windows is using, or "".

        `eConsole`, which is the role a program gets when it does not ask for one.
        Windows moves this role first when the device behind it disappears - a
        wireless headset running out of battery, which is the case this is for.
        """
        if self._enumerator is None:
            return ""
        device = c_void_p()
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint, slot 4
        hr = _call(self._enumerator, 4, (DWORD, DWORD, POINTER(c_void_p)),
                   DWORD(_RENDER), DWORD(_CONSOLE), byref(device))
        if hr or not device:
            return ""
        try:
            return self._name_of(device)
        finally:
            _release(device)

    def _name_of(self, device: c_void_p) -> str:
        store = c_void_p()
        # IMMDevice::OpenPropertyStore, slot 4
        if _call(device, 4, (DWORD, POINTER(c_void_p)),
                 DWORD(_STGM_READ), byref(store)) or not store:
            return ""
        value = _PROPVARIANT()
        try:
            # IPropertyStore::GetValue, slot 5
            if _call(store, 5, (POINTER(_PROPERTYKEY), POINTER(_PROPVARIANT)),
                     byref(_PKEY_FRIENDLY), byref(value)):
                return ""
            text = ctypes.cast(value.pwszVal, c_wchar_p).value
            return text or ""
        finally:
            try:
                _ole32.PropVariantClear(byref(value))
            except Exception:  # noqa: BLE001 - teardown
                pass
            _release(store)

    # ------------------------------------------------------------------ peaks

    def refresh(self) -> None:
        """Re-read the list of endpoints. Call it when the set of devices may differ."""
        self._drop_devices()
        if self._enumerator is None:
            return
        collection = c_void_p()
        # IMMDeviceEnumerator::EnumAudioEndpoints, slot 3
        if _call(self._enumerator, 3, (DWORD, DWORD, POINTER(c_void_p)),
                 DWORD(_RENDER), DWORD(_ACTIVE), byref(collection)) or not collection:
            return
        try:
            count = ctypes.c_uint()
            # IMMDeviceCollection::GetCount, slot 3
            if _call(collection, 3, (POINTER(ctypes.c_uint),), byref(count)):
                return
            for index in range(int(count.value)):
                device = c_void_p()
                # IMMDeviceCollection::Item, slot 4
                if _call(collection, 4, (ctypes.c_uint, POINTER(c_void_p)),
                         ctypes.c_uint(index), byref(device)) or not device:
                    continue
                self._devices.append(
                    (self._name_of(device), device, self._meter_of(device))
                )
        finally:
            _release(collection)

    def _meter_of(self, device: c_void_p) -> c_void_p | None:
        meter = c_void_p()
        # IMMDevice::Activate, slot 3
        hr = _call(device, 3,
                   (POINTER(_GUID), DWORD, c_void_p, POINTER(c_void_p)),
                   byref(_IID_IAudioMeterInformation), DWORD(_CLSCTX_ALL), None,
                   byref(meter))
        return None if hr or not meter else meter

    def peaks(self) -> dict[str, float]:
        """Every endpoint's current peak, 0..1, by friendly name.

        What Windows sees leaving each device. An endpoint whose meter could not be
        opened is left out rather than reported as silent: "no answer" and "nothing
        playing" are different, and only one of them is worth telling a user about.
        """
        found: dict[str, float] = {}
        for name, _device, meter in self._devices:
            if meter is None or not name:
                continue
            value = c_float()
            # IAudioMeterInformation::GetPeakValue, slot 3
            if _call(meter, 3, (POINTER(c_float),), byref(value)):
                continue
            found[name] = float(value.value)
        return found

    def loudest(self, ignoring: str = "",
                floor: float = AUDIBLE_PEAK) -> tuple[str, float]:
        """The endpoint making a sound, as (name, peak), or ("", 0.0) if none is.

        `ignoring` leaves one out by name - the one already being recorded, when the
        question is "where is the sound going instead?". `floor` is what counts as a
        sound at all; below it an endpoint is reported as silent, because the residue
        an idle endpoint shows is not something to send a user looking for.
        """
        best_name, best_peak = "", 0.0
        for name, peak in self.peaks().items():
            if name == ignoring or peak < floor:
                continue
            if peak > best_peak:
                best_name, best_peak = name, peak
        return best_name, best_peak

    # ---------------------------------------------------------------- teardown

    def _drop_devices(self) -> None:
        for _name, device, meter in self._devices:
            _release(meter)
            _release(device)
        self._devices = []

    def close(self) -> None:
        """Let every interface go. Safe to call twice, and on one that never opened."""
        self._drop_devices()
        _release(self._enumerator)
        self._enumerator = None

    def __del__(self) -> None:  # pragma: no cover - interpreter teardown
        try:
            self.close()
        except Exception:
            # Interpreter shutdown: ctypes, the module globals and COM itself may all
            # be half gone by now. There is nobody left to report to and raising here
            # prints an unhandled exception from a destructor and changes nothing.
            pass
