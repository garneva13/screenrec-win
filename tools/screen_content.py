"""A window of representative screen-recording content, for benchmarking against.

An idle desktop compresses to nothing and every candidate encoder scores the same on it,
which tells you nothing. What a screen recorder actually has to carry is small text that
moves, hard edges, and large flat areas - and the thing that wrecks it is chroma
subsampling smearing saturated text, so the content includes red and blue on white on
purpose.

Run on its own to look at it:
    .venv\\Scripts\\python.exe tools\\screen_content.py --seconds 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from screenrec import winapi  # noqa: E402

WIDTH, HEIGHT = 1280, 720

LINES = [
    ("def surface_crop(monitor, x, y, width, height):", "#000000"),
    ("    left = max(monitor.x, x)        # intersect, do not slide", "#008000"),
    ("    right = min(monitor.x + monitor.width, x + width)", "#000000"),
    ("    if local_w <= 0 or local_h <= 0:", "#0000CC"),
    ("        raise ValueError('does not overlap monitor %d' % monitor.index)", "#CC0000"),
    ("", "#000000"),
    ("WARNING  chroma subsampling smears small red and blue text", "#CC0000"),
    ("INFO     4:4:4 keeps every colour sample; 4:2:0 keeps one in four", "#0000CC"),
    ("OK       flat backgrounds cost almost nothing either way", "#008000"),
    ("", "#000000"),
    ("  1  ddagrab feeds h264_nvenc directly, with no filter at all", "#000000"),
    ("  2  hwmap=derive_device=cuda fails on this build", "#000000"),
    ("  3  a CPU path must end format=yuv420p or libx264 writes yuv444p", "#000000"),
    ("  4  a rotated screen arrives in the panel's own orientation", "#000000"),
]


class ContentWindow(QtWidgets.QWidget):
    """Scrolling code-like text, a moving panel and a gradient, at a fixed size."""

    def __init__(self, x: int, y: int) -> None:
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setGeometry(x, y, WIDTH, HEIGHT)
        self.offset = 0.0
        self.frame = 0
        self.mono = QtGui.QFont("Consolas", 10)
        self.ui_font = QtGui.QFont("Segoe UI", 9)

        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._advance)
        self.timer.start()

    def _advance(self) -> None:
        self.offset += 1.4
        self.frame += 1
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QtGui.QPainter) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        rect = self.rect()

        # A light editor pane: the worst case for 4:2:0, and the common case in a tutorial.
        painter.fillRect(rect, QtGui.QColor("#FFFFFF"))
        painter.fillRect(0, 0, rect.width(), 30, QtGui.QColor("#F0F0F0"))
        painter.setPen(QtGui.QColor("#202020"))
        painter.setFont(self.ui_font)
        painter.drawText(12, 20, f"recorder.py  -  frame {self.frame}")
        painter.setPen(QtGui.QColor("#C8C8C8"))
        painter.drawLine(0, 30, rect.width(), 30)

        painter.setFont(self.mono)
        line_height = 17
        total = len(LINES) * line_height
        start = 40 - (self.offset % total)
        y = start
        index = 0
        while y < rect.height():
            text, colour = LINES[index % len(LINES)]
            painter.setPen(QtGui.QColor("#A0A0A0"))
            painter.drawText(12, int(y), f"{index % 200 + 1:>4}")
            painter.setPen(QtGui.QColor(colour))
            painter.drawText(56, int(y), text)
            y += line_height
            index += 1

        # A panel that slides, so there is real motion and not just a scroll.
        slide = int((self.frame * 2) % (rect.width() - 320))
        panel = QtCore.QRect(slide, rect.height() - 190, 300, 160)
        painter.fillRect(panel, QtGui.QColor("#1E232B"))
        painter.setPen(QtGui.QColor("#4AA3FF"))
        painter.drawRect(panel)
        painter.setFont(self.ui_font)
        painter.setPen(QtGui.QColor("#E6E9EF"))
        painter.drawText(panel.adjusted(10, 8, -10, 0), 0, "Recording")
        painter.setPen(QtGui.QColor("#FF5A5A"))
        painter.drawText(panel.adjusted(10, 28, -10, 0), 0, "00:0%d  59.8 fps" % (self.frame // 60 % 10))

        # A smooth gradient: banding shows up here before anywhere else.
        gradient = QtGui.QLinearGradient(0, 0, 360, 0)
        gradient.setColorAt(0.0, QtGui.QColor("#102030"))
        gradient.setColorAt(0.5, QtGui.QColor("#3080C0"))
        gradient.setColorAt(1.0, QtGui.QColor("#F0F4F8"))
        painter.fillRect(rect.width() - 380, rect.height() - 190, 360, 160, gradient)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--monitor", type=int, default=0)
    args = parser.parse_args()

    winapi.enable_dpi_awareness()
    app = QtWidgets.QApplication(sys.argv)
    monitors = winapi.list_monitors()
    monitor = next((m for m in monitors if m.index == args.monitor), monitors[0])
    x = monitor.x + max(0, (monitor.width - WIDTH) // 2)
    y = monitor.y + max(0, (monitor.height - HEIGHT) // 2)

    window = ContentWindow(x, y)
    window.show()
    print(f"{x} {y} {WIDTH} {HEIGHT}", flush=True)

    quit_timer = QtCore.QTimer(window)
    quit_timer.setSingleShot(True)
    quit_timer.timeout.connect(app.quit)
    quit_timer.start(int(args.seconds * 1000))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
