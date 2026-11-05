"""Where everything lives: the bundled ffmpeg, the settings file, the output folder.

The ffmpeg binaries ship inside the project (tools/ffmpeg) so the app never depends on a
system install. A settings override wins over the bundle, and PATH is the last resort; the
lookup is explicit rather than silent so a missing binary is reported with the paths tried.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "ScreenRec"

#: True when this is running out of a PyInstaller build rather than the source tree.
FROZEN = bool(getattr(sys, "frozen", False))


def _project_root() -> Path:
    """Where `tools/ffmpeg` is, source tree or installed program alike.

    Counting parents off `__file__` is right in the source tree and an accident
    everywhere else. In a PyInstaller `onedir` build the modules live under
    `<app>/_internal/screenrec/`, so three parents up happens to land on `<app>` and
    the old expression worked by luck; in a `onefile` build it lands in the unpacked
    ``%TEMP%/_MEI...`` folder, where there is no `tools` at all, and the lookup then falls
    through to `shutil.which` and picks up whatever ffmpeg a stranger has on PATH -
    a different build, with different encoders, chosen silently.

    So when frozen the anchor is the executable's own folder, which is what the
    installer lays `tools/ffmpeg` beside, and nothing depends on how PyInstaller
    arranges its internals.
    """
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _project_root()
TOOLS_DIR = PROJECT_ROOT / "tools" / "ffmpeg"


#: Where the installer leaves the folder its wizard asked for. Not a settings file,
#: because an elevated installer runs as the administrator and `%APPDATA%` there is the
#: administrator's: the installer *proposes* and the program adopts, once, into the
#: settings of whoever actually runs it. See `packaging/ScreenRec.iss`.
INSTALLER_KEY = r"Software\ScreenRec"
INSTALLER_VALUE = "DefaultOutputDir"


def proposed_output_dir() -> str:
    """The folder the installer's wizard asked for, or '' if nobody installed this.

    Looked for under the current user first and the machine second, which is the order
    `HKA` writes them in - per-user install, then per-machine. Never raises: a missing
    key, a missing value, a value of the wrong type and a Windows without the registry
    at all are every one of them just "nobody proposed anything".
    """
    try:
        import winreg
    except ImportError:          # not Windows; this program does not run there anyway
        return ""
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, INSTALLER_KEY) as key:
                value, kind = winreg.QueryValueEx(key, INSTALLER_VALUE)
        except (OSError, ValueError):
            continue
        if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and str(value).strip():
            return str(value).strip()
    return ""


class BinaryNotFound(RuntimeError):
    """Raised with every location that was tried, so the message is actionable."""


def _find(name: str, override: str | os.PathLike[str] | None = None) -> Path:
    tried: list[str] = []

    if override:
        candidate = Path(override)
        if candidate.is_dir():
            candidate = candidate / name
        tried.append(str(candidate))
        if candidate.is_file():
            return candidate

    bundled = TOOLS_DIR / name
    tried.append(str(bundled))
    if bundled.is_file():
        return bundled

    on_path = shutil.which(Path(name).stem)
    tried.append(f"PATH ({on_path or 'not found'})")
    if on_path:
        return Path(on_path)

    raise BinaryNotFound(f"{name} not found. Tried:\n  " + "\n  ".join(tried))


def ffmpeg_exe(override: str | os.PathLike[str] | None = None) -> Path:
    return _find("ffmpeg.exe", override)


def ffprobe_exe(override: str | os.PathLike[str] | None = None) -> Path:
    return _find("ffprobe.exe", override)


def config_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / APP_NAME



def own_settings_folder(prefix: str = "screenrec_harness_"):
    """A context manager giving this run its own settings folder.

    `paths.config_dir()` reads `%APPDATA%`, so moving that for the life of the process
    moves the settings, the encoder probe cache and the measured-size store with it.

    Harnesses that drive the real application must use this. Without it a ten-second
    capture of an idle desktop is filed as this machine's measured MB per minute, and
    the settings panel then quotes a harness's recording back at the person as their
    own - which is exactly what happened on 2026-11-05, five times over.

    The cost is that the run starts with no probe cache and pays for a cold encoder
    probe. That is the right price: a harness should not be reading the answer a
    previous run left lying about either.
    """
    import contextlib
    import shutil
    import tempfile

    import json

    @contextlib.contextmanager
    def scope():
        folder = tempfile.mkdtemp(prefix=prefix)
        was = os.environ.get("APPDATA")
        os.environ["APPDATA"] = folder
        # Seeded, not empty. An empty settings folder is a *first run*, and a first run
        # opens the "where should recordings go?" dialog and waits - which a harness
        # cannot answer, so both recording harnesses went from 12/12 and 17/17 to 2/5
        # and 12/13 the moment this was introduced without it.
        settings = Path(folder) / APP_NAME
        settings.mkdir(parents=True, exist_ok=True)
        (settings / "settings.json").write_text(
            json.dumps({"output_dir_asked": True}), encoding="utf-8")
        try:
            yield Path(folder)
        finally:
            if was is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = was
            shutil.rmtree(folder, ignore_errors=True)

    return scope()

def config_path() -> Path:
    return config_dir() / "settings.json"


def default_output_dir() -> Path:
    """%USERPROFILE%\\Videos\\ScreenRec, falling back to the home folder."""
    videos = Path.home() / "Videos"
    return (videos if videos.is_dir() else Path.home()) / APP_NAME
