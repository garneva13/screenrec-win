"""The whole product, driven the way the user drives it.

Launch the app, press the hotkey to start, wait, press it again to stop, then probe the
file. Nothing here reaches into the app's internals - it goes through the global hotkey,
which is the path that matters and the one a unit test cannot reach.
"""
import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, r"B:\ScreenRec\app")
from screenrec import paths, winapi  # noqa: E402

PY = r"B:\ScreenRec\.venv\Scripts\python.exe"
APP = r"B:\ScreenRec\app\run.pyw"
HOTKEY_VKS = (0x11, 0x12, 0x10, 0x86)  # Ctrl, Alt, Shift, F23
RECORD_SECONDS = 25

user32 = ctypes.WinDLL("user32", use_last_error=True)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class INPUT(ctypes.Structure):
    # MOUSEINPUT is the largest member and must be present, or sizeof(INPUT) is 32
    # instead of 40 and SendInput rejects the cbSize without doing anything.
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def press_hotkey() -> None:
    items = [INPUT() for _ in range(len(HOTKEY_VKS) * 2)]
    for index, vk in enumerate(HOTKEY_VKS):
        items[index].type = 1
        items[index].ki = KEYBDINPUT(vk, 0, 0, 0, None)
    for index, vk in enumerate(reversed(HOTKEY_VKS)):
        slot = items[len(HOTKEY_VKS) + index]
        slot.type = 1
        slot.ki = KEYBDINPUT(vk, 0, 2, 0, None)  # KEYEVENTF_KEYUP
    down = (INPUT * len(HOTKEY_VKS))(*items[: len(HOTKEY_VKS)])
    up = (INPUT * len(HOTKEY_VKS))(*items[len(HOTKEY_VKS):])
    user32.SendInput(len(HOTKEY_VKS), down, ctypes.sizeof(INPUT))
    time.sleep(0.05)
    user32.SendInput(len(HOTKEY_VKS), up, ctypes.sizeof(INPUT))


winapi.enable_dpi_awareness()

# Known settings, so the run is reproducible and uses an obscure hotkey.
config_path = Path(ctypes.create_unicode_buffer(260).value or "")
from screenrec import paths as paths_mod  # noqa: E402

config_file = paths_mod.config_path()
config_file.parent.mkdir(parents=True, exist_ok=True)
previous = config_file.read_text(encoding="utf-8") if config_file.is_file() else None
config_file.write_text(json.dumps({
    "source_kind": "monitor",
    "monitor_index": 0,
    "framerate": 60,
    "profile": "hevc420",
    "quality_level": "balanced",
    "draw_mouse": True,
    "click_highlight": "--no-clicks" not in sys.argv,
    "system_audio_enabled": "--no-audio" not in sys.argv,
    "system_audio_device": -1,
    "hotkey": "Ctrl+Alt+Shift+F23",
    "show_island": "--no-island" not in sys.argv,
    "countdown": 0,
}, indent=2), encoding="utf-8")

out_dir = paths_mod.default_output_dir()
before = set(out_dir.glob("*.mp4")) if out_dir.is_dir() else set()

proc = subprocess.Popen([PY, APP], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
print("app pid", proc.pid, "| click highlight:",
      "--no-clicks" not in sys.argv)
time.sleep(5.0)
if proc.poll() is not None:
    _, err = proc.communicate()
    print("the app exited early:", err.decode("utf-8", "replace")[-2000:])
    sys.exit(1)

print("pressing the hotkey to start")
press_hotkey()
time.sleep(RECORD_SECONDS)
print("pressing the hotkey to stop")
press_hotkey()
time.sleep(4.0)

proc.terminate()
try:
    proc.wait(timeout=8)
except subprocess.TimeoutExpired:
    proc.kill()
out, err = proc.communicate()
tail = err.decode("utf-8", "replace").strip()
if tail:
    print("app stderr:", tail[-1500:])

after = set(out_dir.glob("*.mp4")) if out_dir.is_dir() else set()
fresh = sorted(after - before)
if not fresh:
    print("NO FILE WAS WRITTEN to", out_dir)
    if previous is not None:
        config_file.write_text(previous, encoding="utf-8")
    sys.exit(1)

clip = fresh[-1]
print("\nwrote:", clip, f"{clip.stat().st_size / 1e6:.1f} MB")
probe = subprocess.run(
    [str(paths.ffprobe_exe()), "-v", "error", "-show_entries",
     "stream=codec_type,codec_name,profile,pix_fmt,width,height,avg_frame_rate,"
     "nb_frames,duration,start_time,channels,sample_rate:format=duration",
     "-of", "default=noprint_wrappers=1", str(clip)],
    capture_output=True, text=True,
)
print(probe.stdout)

if previous is not None:
    config_file.write_text(previous, encoding="utf-8")
    print("(settings file put back)")
