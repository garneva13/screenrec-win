"""The tray icon: the only honest way to see that this is running, and to stop it.

The first version had neither, and the consequence was exactly what you would predict -
the user could not tell the program was running, could not close it, and ended up with
**two copies alive at once**, because launching it again did nothing visible.

So: an icon that says what the program is doing, a menu that can start, stop, pause,
show the bar and quit, and a single-instance guard so a second launch raises the first
one instead of starting a rival.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n
from ..i18n import _
from . import icons, theme

#: Any name will do as long as it is the same in every copy. The GUID-looking tail is
#: there so another program cannot own it by accident.
MUTEX_NAME = "Global\\ScreenRec.SingleInstance.6f1c0b2a"
BROADCAST_MESSAGE = "ScreenRec.ShowYourself"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
ERROR_ALREADY_EXISTS = 183
HWND_BROADCAST = 0xFFFF


class SingleInstance:
    """A named mutex, held for the life of the process.

    The handle is kept on the instance on purpose: letting it be collected frees the
    mutex and the guard silently stops guarding.
    """

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self.first = False
        self.message = 0
        self._handle = None

    def acquire(self) -> bool:
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                          wintypes.LPCWSTR]
        self._handle = kernel32.CreateMutexW(None, False, self.name)
        self.first = ctypes.get_last_error() != ERROR_ALREADY_EXISTS
        self.message = user32.RegisterWindowMessageW(BROADCAST_MESSAGE)
        return self.first

    def wake_the_other_one(self) -> None:
        """Ask the copy that is already running to show itself, then let this one die."""
        if self.message:
            user32.PostMessageW(HWND_BROADCAST, self.message, 0, 0)

    def release(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None


def _icon(colour_key: str, kind: str = "record") -> QtGui.QIcon:
    """A tray icon drawn here, so there is no asset to ship or to lose."""
    icon = QtGui.QIcon()
    for size in (16, 24, 32, 48):
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(theme.COLOURS["panel_raised"]))
            painter.drawRoundedRect(
                QtCore.QRectF(0.5, 0.5, size - 1, size - 1), size * 0.26, size * 0.26
            )
            inset = size * 0.24
            icons.draw(
                painter, kind,
                QtCore.QRectF(inset, inset, size - inset * 2, size - inset * 2),
                theme.COLOURS[colour_key],
                weight=max(1.4, size / 12),
            )
        finally:
            painter.end()
        icon.addPixmap(pixmap)
    return icon


class Tray(QtCore.QObject):
    """The tray icon and its menu."""

    record_requested = QtCore.Signal()
    stop_requested = QtCore.Signal()
    pause_requested = QtCore.Signal()
    show_requested = QtCore.Signal()
    settings_requested = QtCore.Signal()
    folder_requested = QtCore.Signal()
    watch_requested = QtCore.Signal()
    quit_requested = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.available = QtWidgets.QSystemTrayIcon.isSystemTrayAvailable()
        self.icon = QtWidgets.QSystemTrayIcon(self)
        self._recording = False
        self._paused = False
        #: What `set_elapsed` was last told, so the tooltip can be written again in
        #: another language without waiting for the next tick of the clock.
        self._seconds = 0.0
        self._megabytes = 0.0

        self.menu = QtWidgets.QMenu()
        self.menu.setStyleSheet(theme.style_sheet())
        # Every action is kept, not only the two that change with the state: a
        # language change has to reach all of them, and this menu cannot be rebuilt to
        # rename them - the tray icon holds it for the life of the program.
        self.action_record = self.menu.addAction(_("Start recording"))
        self.action_record.triggered.connect(self._record_or_stop)
        self.action_pause = self.menu.addAction(_("Pause"))
        self.action_pause.triggered.connect(self.pause_requested.emit)
        self.action_pause.setEnabled(False)
        self.menu.addSeparator()
        self.action_show = self.menu.addAction(_("Show the bar"))
        self.action_show.triggered.connect(self.show_requested.emit)
        self.action_settings = self.menu.addAction(_("Settings…"))
        self.action_settings.triggered.connect(self.settings_requested.emit)
        self.action_watch = self.menu.addAction(_("Watch the last recording"))
        self.action_watch.triggered.connect(self.watch_requested.emit)
        self.action_folder = self.menu.addAction(_("Open the output folder"))
        self.action_folder.triggered.connect(self.folder_requested.emit)
        self.menu.addSeparator()
        # The one thing the first version had no way at all to do.
        self.action_quit = self.menu.addAction(_("Quit ScreenRec"))
        self.action_quit.triggered.connect(self.quit_requested.emit)

        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        i18n.notifier.changed.connect(self.retranslate)
        self.set_recording(False)

    def show(self) -> str:
        """Put the icon in the tray. Returns '' or why it could not."""
        if not self.available:
            return _(
                "There is no system tray on this desktop, so ScreenRec has no icon "
                "there; quit it from the bar's right-click menu."
            )
        self.icon.show()
        return ""

    def hide(self) -> None:
        self.icon.hide()

    def _activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_requested.emit()

    def _record_or_stop(self) -> None:
        if self._recording:
            self.stop_requested.emit()
        else:
            self.record_requested.emit()

    # -- state -------------------------------------------------------------

    def set_recording(self, recording: bool, paused: bool = False) -> None:
        self._recording = recording
        self._paused = paused
        if recording and paused:
            self.icon.setIcon(_icon("warn", "pause"))
            self.icon.setToolTip(_("ScreenRec — paused"))
            self.action_record.setText(_("Stop recording"))
            self.action_pause.setText(_("Carry on recording"))
        elif recording:
            self.icon.setIcon(_icon("record", "record"))
            self.icon.setToolTip(_("ScreenRec — recording"))
            self.action_record.setText(_("Stop recording"))
            self.action_pause.setText(_("Pause"))
        else:
            self.icon.setIcon(_icon("text_dim", "record"))
            self.icon.setToolTip(_("ScreenRec — ready"))
            self.action_record.setText(_("Start recording"))
            self.action_pause.setText(_("Pause"))
        self.action_pause.setEnabled(recording)

    def set_elapsed(self, seconds: float, megabytes: float) -> None:
        if not self._recording:
            return
        self._seconds = float(seconds)
        self._megabytes = float(megabytes)
        minutes, rest = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        clock = (f"{hours}:{minutes:02d}:{rest:02d}" if hours
                 else f"{minutes:02d}:{rest:02d}")
        # Two whole sentences rather than one with the state dropped into it: Russian
        # will not take "recording" and "paused" in the same slot without reading as
        # translated English.
        template = (
            _("ScreenRec — paused {clock}, {size} MB") if self._paused
            else _("ScreenRec — recording {clock}, {size} MB")
        )
        self.icon.setToolTip(
            template.format(clock=clock, size=f"{megabytes:.0f}")
        )

    # -- language ----------------------------------------------------------

    def retranslate(self, code: str = "") -> None:
        """Re-read the menu and the tooltip. Connected to `i18n.notifier.changed`."""
        self.action_show.setText(_("Show the bar"))
        self.action_settings.setText(_("Settings…"))
        self.action_watch.setText(_("Watch the last recording"))
        self.action_folder.setText(_("Open the output folder"))
        self.action_quit.setText(_("Quit ScreenRec"))
        # Rewrites the record and pause items and the resting tooltip.
        self.set_recording(self._recording, self._paused)
        if self._recording:
            self.set_elapsed(self._seconds, self._megabytes)

    def notify(self, title: str, text: str, warn: bool = False) -> None:
        if not self.available or not self.icon.isVisible():
            return
        kind = (
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning if warn
            else QtWidgets.QSystemTrayIcon.MessageIcon.Information
        )
        self.icon.showMessage(title, text, kind, 4000)
