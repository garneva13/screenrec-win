"""What happens the moment a recording finishes: open it, show it, or copy it.

Named `shellops` and not `shell` on purpose - a module called `shell.py` on the import
path is the kind of name that collides with something one day.

The one interesting part is **copying the file itself**, not its path. Putting the text
``B:\\REC\\clip.mp4`` on the clipboard is useless in a chat window; what is wanted is the
file, so that Ctrl+V in Discord or Telegram attaches the video. That means a clipboard
payload carrying a `text/uri-list` and the Windows ``CF_HDROP`` shape, which Qt produces
from `QMimeData.setUrls` with a local file URL. Both are set, plus the path as plain
text, so a target that only understands one of the three still gets something sensible.

Every function returns a problem string - empty when it worked - because the caller is a
status line, not an exception handler.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

ACTIONS: tuple[tuple[str, str], ...] = (
    ("", "Do nothing"),
    ("open", "Open the video"),
    ("reveal", "Show it in the folder"),
    ("copy", "Copy the file, ready to paste"),
)


def open_file(path: Path) -> str:
    """Hand the file to whatever Windows opens it with."""
    target = Path(path)
    if not target.is_file():
        return f"{target.name} is not there any more."
    try:
        os.startfile(str(target))  # noqa: S606 - the point of the function
    except OSError as exc:
        return f"{target.name} could not be opened: {exc}"
    return ""


def reveal(path: Path) -> str:
    """Open Explorer with the file selected."""
    target = Path(path)
    if not target.exists():
        parent = target.parent
        if not parent.is_dir():
            return f"{target.parent} is not there any more."
        try:
            subprocess.Popen(["explorer", str(parent)])
        except OSError as exc:
            return f"The folder could not be opened: {exc}"
        return f"{target.name} is gone; opened the folder instead."
    try:
        # explorer wants /select, and the comma is part of the switch, not a separator.
        subprocess.Popen(["explorer", "/select,", str(target)])
    except OSError as exc:
        return f"Explorer could not be started: {exc}"
    return ""


def open_folder(folder: Path) -> str:
    target = Path(folder)
    try:
        target.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(target)])
    except OSError as exc:
        return f"The folder could not be opened: {exc}"
    return ""


def copy_file(path: Path) -> str:
    """Put the file on the clipboard so Ctrl+V attaches it somewhere else.

    Needs a running QApplication; without one there is no clipboard to write to, and
    saying so is better than appearing to succeed.
    """
    target = Path(path)
    if not target.is_file():
        return f"{target.name} is not there any more."
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError as exc:  # pragma: no cover - PySide6 is a hard dependency
        return f"The clipboard is unavailable: {exc}"
    if QtWidgets.QApplication.instance() is None:
        return "The clipboard is only available while the application is running."

    data = QtCore.QMimeData()
    url = QtCore.QUrl.fromLocalFile(str(target.resolve()))
    data.setUrls([url])  # becomes CF_HDROP on Windows, which is what a chat window takes
    data.setText(str(target.resolve()))
    QtGui.QGuiApplication.clipboard().setMimeData(data)
    return ""


def run_action(action: str, path: Path) -> str:
    """Do whatever the "after recording" setting asks for."""
    if not action:
        return ""
    if action == "open":
        return open_file(path)
    if action == "reveal":
        return reveal(path)
    if action == "copy":
        problem = copy_file(path)
        return problem or ""
    return f"Unknown after-recording action {action!r}; nothing was done."
