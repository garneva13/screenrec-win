"""The Windows calls the recorder needs, through ctypes.

Three jobs live here:

* **Monitors** are enumerated through DXGI, not through Qt or EnumDisplayMonitors, because
  ffmpeg's ``ddagrab`` addresses a screen by its *DXGI output index* on the default adapter.
  Any other enumeration order is a guess that happens to be right on a one-GPU machine.
* **Windows** are enumerated for the "record this window" mode. The rectangle comes from
  ``DWMWA_EXTENDED_FRAME_BOUNDS``, not ``GetWindowRect``: since Vista the latter includes an
  invisible resize border, so cropping to it records a few pixels of whatever is behind.
* **DPI awareness** so those rectangles are physical pixels, which is what DDA hands back.

Everything here is read-only inspection of the desktop; nothing in this module changes any
system state.
"""
from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_int, c_uint, c_void_p, wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DXGI_ERROR_NOT_FOUND = 0x887A0002

# DXGI_MODE_ROTATION
ROTATION_NAMES = {0: "unspecified", 1: "none", 2: "90", 3: "180", 4: "270"}

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def enable_dpi_awareness() -> bool:
    """Ask for per-monitor-v2 DPI awareness. Harmless if Qt already did it."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return True
    except AttributeError:
        # Windows 10 before 1703 has no SetProcessDpiAwarenessContext. Nothing to
        # do about it, and the caller is told by the False below.
        pass
    # Qt having set it already is the common case here, and is not an error.
    return False


# --------------------------------------------------------------------------- monitors


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [
        ("DeviceName", ctypes.c_wchar * 32),
        ("DesktopCoordinates", wintypes.RECT),
        ("AttachedToDesktop", wintypes.BOOL),
        ("Rotation", ctypes.c_uint),
        ("Monitor", ctypes.c_void_p),
    ]


class _DXGI_ADAPTER_DESC(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint),
        ("DeviceId", ctypes.c_uint),
        ("SubSysId", ctypes.c_uint),
        ("Revision", ctypes.c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", ctypes.c_longlong),
    ]


def _method(ptr: c_void_p, index: int, restype, *argtypes):
    """Bind vtable slot `index` of the COM object at `ptr`."""
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtable[index])


def _release(ptr: c_void_p) -> None:
    if ptr:
        _method(ptr, 2, ctypes.c_ulong)(ptr)


@dataclass(frozen=True)
class Monitor:
    """One DXGI output. `index` is exactly ddagrab's ``output_idx``."""

    index: int
    device_name: str
    x: int
    y: int
    width: int
    height: int
    rotation: int
    adapter: str

    @property
    def rotated(self) -> bool:
        """True when the desktop is turned 90 or 270 degrees on this output.

        DDA duplicates the surface in the panel's native orientation, so a portrait screen
        arrives sideways and has to be transposed by the filter graph.
        """
        return self.rotation in (2, 4)

    @property
    def label(self) -> str:
        tail = "" if self.rotation in (0, 1) else f", rotated {ROTATION_NAMES[self.rotation]}°"
        return f"{self.index}: {self.width}×{self.height} at {self.x},{self.y}{tail}"

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def list_monitors() -> list[Monitor]:
    """DXGI outputs of the default adapter, in ddagrab's own order.

    Outputs on any further adapter are appended after a gap in the index space only if the
    default adapter has none, because ddagrab counts within one adapter; on a machine where
    that matters the caller gets what DDA would get, not a renumbered list.
    """
    dxgi = ctypes.WinDLL("dxgi")
    # IID_IDXGIFactory1 {770aae78-f26f-4dba-a829-253c83d1b387}
    iid = _GUID(
        0x770AAE78, 0xF26F, 0x4DBA,
        (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87),
    )
    factory = c_void_p()
    dxgi.CreateDXGIFactory1.argtypes = [POINTER(_GUID), POINTER(c_void_p)]
    dxgi.CreateDXGIFactory1.restype = ctypes.HRESULT
    dxgi.CreateDXGIFactory1(byref(iid), byref(factory))

    monitors: list[Monitor] = []
    try:
        enum_adapters = _method(factory, 7, ctypes.c_long, c_uint, POINTER(c_void_p))
        adapter_index = 0
        while True:
            adapter = c_void_p()
            if enum_adapters(factory, adapter_index, byref(adapter)) != 0:
                break
            try:
                desc = _DXGI_ADAPTER_DESC()
                _method(adapter, 8, ctypes.c_long, POINTER(_DXGI_ADAPTER_DESC))(adapter, byref(desc))
                enum_outputs = _method(adapter, 7, ctypes.c_long, c_uint, POINTER(c_void_p))
                output_index = 0
                while True:
                    output = c_void_p()
                    if enum_outputs(adapter, output_index, byref(output)) != 0:
                        break
                    try:
                        odesc = _DXGI_OUTPUT_DESC()
                        _method(output, 7, ctypes.c_long, POINTER(_DXGI_OUTPUT_DESC))(
                            output, byref(odesc)
                        )
                        rect = odesc.DesktopCoordinates
                        if odesc.AttachedToDesktop:
                            monitors.append(
                                Monitor(
                                    index=output_index,
                                    device_name=odesc.DeviceName,
                                    x=rect.left,
                                    y=rect.top,
                                    width=rect.right - rect.left,
                                    height=rect.bottom - rect.top,
                                    rotation=int(odesc.Rotation),
                                    adapter=desc.Description,
                                )
                            )
                    finally:
                        _release(output)
                    output_index += 1
            finally:
                _release(adapter)
            if monitors:
                # ddagrab counts outputs on one adapter; stop at the first that has any.
                break
            adapter_index += 1
    finally:
        _release(factory)
    return monitors


def monitor_at(monitors: list[Monitor], x: int, y: int) -> Monitor | None:
    for monitor in monitors:
        if monitor.contains(x, y):
            return monitor
    return None


# ---------------------------------------------------------------------------- windows


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str
    x: int
    y: int
    width: int
    height: int

    @property
    def label(self) -> str:
        return f"{self.title}  —  {self.process}  ({self.width}×{self.height})"


def _is_cloaked(hwnd: int) -> bool:
    """A UWP window parked off-screen, or one on another virtual desktop."""
    cloaked = wintypes.DWORD()
    result = dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), DWMWA_CLOAKED, byref(cloaked), ctypes.sizeof(cloaked)
    )
    return result == 0 and cloaked.value != 0


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) of what the user actually sees, in physical pixels."""
    rect = wintypes.RECT()
    result = dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, byref(rect), ctypes.sizeof(rect)
    )
    if result != 0:
        # Pre-DWM or an odd window: fall back and say nothing was adjusted.
        if not user32.GetWindowRect(wintypes.HWND(hwnd), byref(rect)):
            return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return rect.left, rect.top, width, height


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, byref(size)):
            return buffer.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)
    return ""


def list_windows(min_size: int = 80) -> list[WindowInfo]:
    """Top-level windows a user would recognise, largest area first.

    Filtered out: invisible, cloaked, tool windows, owned windows, untitled ones and
    anything smaller than `min_size` in either direction.
    """
    found: list[WindowInfo] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER: a dialog of another window
            return True
        styles = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if styles & WS_EX_TOOLWINDOW:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        if _is_cloaked(hwnd):
            return True
        rect = window_rect(hwnd)
        if rect is None:
            return True
        x, y, width, height = rect
        if width < min_size or height < min_size:
            return True
        found.append(
            WindowInfo(
                hwnd=int(hwnd),
                title=title,
                process=_process_name(hwnd),
                x=x,
                y=y,
                width=width,
                height=height,
            )
        )
        return True

    user32.EnumWindows(proto(callback), 0)
    found.sort(key=lambda w: w.width * w.height, reverse=True)
    return found


def window_is_alive(hwnd: int) -> bool:
    return bool(user32.IsWindow(wintypes.HWND(hwnd)))


def cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(byref(point))
    return point.x, point.y
