"""Render the two programs' icons into .ico files, from the app's own drawing code.

There is no artwork in this project and that was deliberate: the tray icon and every
glyph are painted at run time, so there is no asset to ship or to lose. A Windows
executable, though, wants an `.ico` baked into it at build time - the file a person sees
in the Start menu, on the desktop and in the task bar.

So the icons are generated here from the same `icons.draw` the running program uses,
into `build/icons/`, which the build then hands to PyInstaller. Nothing is hand-drawn
and nothing can drift from what the application itself shows.

**The .ico is assembled here rather than by Qt**, because `QImageWriter`'s ico support
writes one image per file, and a Windows icon wants every size in one: 16 for the task
bar, 256 for Explorer's largest view, and the ones between because a 256 scaled down to
20 looks worse than a glyph drawn at 20. The format is a six-field header, one directory
entry per size and the PNG bytes after them, which is less code than working around the
writer.

Usage:  .venv\\Scripts\\python.exe tools\\make_icons.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec.player import window as player_window  # noqa: E402
from screenrec.ui import icons, theme  # noqa: E402

SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
OUT = ROOT / "build" / "icons"


def _tile(size: int, background: QtGui.QColor, kind: str,
          ink: QtGui.QColor) -> QtGui.QImage:
    """One square: a rounded tile with the program's own glyph on it."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        # A bare glyph on the desktop reads as a shortcut to something; a tile reads
        # as a program.
        inset = max(1.0, size * 0.06)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(
            QtCore.QRectF(inset, inset, size - 2 * inset, size - 2 * inset),
            size * 0.22, size * 0.22,
        )
    finally:
        painter.end()

    # The recorder's glyphs live in `ui.icons`, the player's in `player.window`, and
    # neither knows the other's names: asking `icons` for "play" gets the fallback
    # square, which is what the first run of this script drew.
    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        side = size * 0.52
        where = (size - side) / 2
        box = QtCore.QRectF(where, where, side, side)
        weight = max(1.4, size * 0.055)
        if not player_window.draw_glyph(painter, kind, box, ink, weight):
            icons.draw(painter, kind, box, ink, weight)
    finally:
        painter.end()
    return pixmap.toImage()


def _png_bytes(image: QtGui.QImage) -> bytes:
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise SystemExit("a frame could not be encoded as PNG")
    return bytes(buffer.data())


def write(name: str, kind: str, background: str, ink: str) -> Path:
    frames = [
        _png_bytes(_tile(size, QtGui.QColor(background), kind, QtGui.QColor(ink)))
        for size in SIZES
    ]
    # ICONDIR: reserved, type 1 (icon), count.
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    directory, body = b"", b""
    for size, data in zip(SIZES, frames):
        # 256 is written as 0 - the field is one byte, and that is the convention.
        directory += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
        )
        body += data
        offset += len(data)
    target = OUT / f"{name}.ico"
    target.write_bytes(header + directory + body)
    print(f"  {target.name:<22} {target.stat().st_size:>7,} bytes  "
          f"({len(frames)} sizes, {SIZES[0]}-{SIZES[-1]})")
    return target


def main() -> int:
    QtWidgets.QApplication(sys.argv[:1])
    theme.apply(QtWidgets.QApplication.instance())
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"writing into {OUT}")
    write("screenrec", "record", "#C2334D", "#FFFFFF")
    write("screenrecplayer", "play", "#5A4BD6", "#FFFFFF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
