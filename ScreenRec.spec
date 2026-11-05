# PyInstaller recipe: two programs, one folder.
#
#     .venv\Scripts\python.exe -m PyInstaller ScreenRec.spec --noconfirm
#
# or, with the icons and the extras done for you, `tools\build_release.py`.
#
# **onedir, not onefile**, and that is a decision rather than a default: the bundled
# ffmpeg is 324 MB, which cannot sensibly be unpacked into %TEMP% on every launch, so it
# sits beside the executables either way - and then one file has bought nothing. A folder
# also leaves the Qt DLLs as separate replaceable files, which is what PySide6's LGPL
# option asks for, and lets the recorder's own ffmpeg be swapped by hand.
#
# **Two EXEs, one COLLECT.** They share every byte of Python and Qt they both use, so
# the pair costs barely more than one, and the user gets two programs rather than one
# program with a switch.
#
# ffmpeg is **not** listed in `datas`: `tools\build_release.py` copies it afterwards.
# Running 324 MB of unrelated binaries through PyInstaller's analyser gains nothing and
# costs minutes, and keeping it in its own folder keeps its DLL names away from the ones
# QtMultimedia ships under the same names.

from pathlib import Path

PROJECT = Path(SPECPATH)
APP = PROJECT / "app"
ICONS = PROJECT / "build" / "icons"

# Qt is modular and PyInstaller takes it all unless told otherwise. WebEngine alone is
# 197 MB in this venv and nothing here imports it; the rest are the same story.
EXCLUDED = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtTest",
    "PySide6.QtSql", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSerialPort",
    "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtHelp", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    # Nothing here is a notebook or a plotting script.
    "tkinter", "matplotlib", "numpy", "PIL", "pytest", "setuptools", "pip",
]

# Found by import only when something asks for them by name at run time.
HIDDEN = [
    "pyaudiowpatch",
    "comtypes",
    "comtypes.stream",
]


def analysis(script: str) -> Analysis:
    return Analysis(
        [str(APP / script)],
        pathex=[str(APP)],
        binaries=[],
        datas=[],
        hiddenimports=HIDDEN,
        hookspath=[],
        runtime_hooks=[],
        excludes=EXCLUDED,
        noarchive=False,
    )


recorder = analysis("run.pyw")
player = analysis("run_player.pyw")

MERGE((recorder, "ScreenRec", "ScreenRec"),
      (player, "ScreenRecPlayer", "ScreenRecPlayer"))

recorder_pyz = PYZ(recorder.pure)
player_pyz = PYZ(player.pure)

recorder_exe = EXE(
    recorder_pyz,
    recorder.scripts,
    [],
    exclude_binaries=True,
    name="ScreenRec",
    icon=str(ICONS / "screenrec.ico"),
    console=False,          # a recorder that opens a console window is not a product
    disable_windowed_traceback=False,
)

player_exe = EXE(
    player_pyz,
    player.scripts,
    [],
    exclude_binaries=True,
    name="ScreenRecPlayer",
    icon=str(ICONS / "screenrecplayer.ico"),
    console=False,
    disable_windowed_traceback=False,
)

COLLECT(
    recorder_exe, recorder.binaries, recorder.datas,
    player_exe, player.binaries, player.datas,
    strip=False,
    upx=False,              # UPX is what makes an unsigned exe look like malware
    name="ScreenRec",
)
