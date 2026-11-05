"""A system-wide hotkey, so start/stop works while another application has focus.

``RegisterHotKey`` is a thread-affine call: the registration belongs to the thread that made
it, and ``WM_HOTKEY`` is posted to *that thread's* message queue - not to a window, not to
the process. A listener therefore cannot be a passive object; it owns a thread that runs a
``GetMessageW`` loop for as long as the hotkey is live, registers inside that thread, and is
woken by ``PostThreadMessageW(WM_QUIT)`` when it is time to stop.

Three failure modes are designed against here, because all three are silent by nature:

* **Another program already owns the combination.** ``RegisterHotKey`` then fails with
  ``ERROR_HOTKEY_ALREADY_REGISTERED`` (1409) and nothing else happens - no exception, no
  message, just a hotkey that never fires. `start` waits for the worker thread to report
  back before it returns, so the caller gets the failure as text naming the combination
  rather than a cheerful empty string.
* **An exception inside the callback.** It would otherwise kill the loop and leave an
  object that still calls itself active. The loop catches it, keeps running, and records it
  in `callback_error`.
* **A worker that outlives its own `stop`.** ``on_pressed`` runs *inside* the message loop,
  so a callback that takes longer than the join timeout (stopping a recording can take
  twelve seconds) leaves a thread that is still alive, still holding the registration, and
  will keep running until the callback returns. Every piece of state a worker writes
  therefore lives on a per-run object (`_Run`), never on the listener, and a worker that
  could not be joined is kept in `_orphans` rather than dropped: a later `start` can still
  reap it, and until it dies `start` refuses rather than silently replacing it. Losing the
  handle to such a thread would strand the combination for the life of the process.

`on_pressed` is called **on the listener thread**, not on the Qt main thread. A Qt caller
must marshal - emit a signal, or ``QtCore.QMetaObject.invokeMethod`` with
``Qt.ConnectionType.QueuedConnection`` - and must not touch widgets from inside it.
"""
from __future__ import annotations

import ctypes
import threading
import traceback
from ctypes import byref, wintypes
from dataclasses import dataclass, field
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
PM_NOREMOVE = 0x0000
QS_ALLPOSTMESSAGE = 0x0100

ERROR_HOTKEY_ALREADY_REGISTERED = 1409
ERROR_INVALID_PARAMETER = 87

_HOTKEY_ID = 0xA17C  # arbitrary; ids are per-thread and this thread registers exactly one

# Spelled the way the UI will show them, in the order `format_hotkey` emits.
_MODIFIER_ORDER: tuple[tuple[int, str], ...] = (
    (MOD_CONTROL, "Ctrl"),
    (MOD_ALT, "Alt"),
    (MOD_SHIFT, "Shift"),
    (MOD_WIN, "Win"),
)

# Everything a user might type for a modifier, lower case.
_MODIFIER_ALIASES: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "ctl": MOD_CONTROL,
    "alt": MOD_ALT,
    "menu": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
    "cmd": MOD_WIN,
}

_ALL_MODIFIERS = MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_WIN


def _key_tables() -> tuple[dict[str, int], dict[int, str]]:
    """The supported keys: A-Z, 0-9, F1-F24 (what the contract asks for, nothing more)."""
    names_to_vk: dict[str, int] = {}
    for code in range(ord("A"), ord("Z") + 1):
        names_to_vk[chr(code).lower()] = code
    for digit in range(10):
        names_to_vk[str(digit)] = 0x30 + digit
    for number in range(1, 25):
        names_to_vk[f"f{number}"] = 0x6F + number  # VK_F1 == 0x70
    vk_to_name = {vk: name.upper() for name, vk in names_to_vk.items()}
    return names_to_vk, vk_to_name


_KEY_TO_VK, _VK_TO_KEY = _key_tables()


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """``"Ctrl+Alt+R"`` -> ``(MOD_CONTROL | MOD_ALT, ord("R"))``, or None if it is not one.

    Case and spacing are free ("  ctrl + alt + r  " is the same hotkey). The last token is
    the key and every token before it must be a modifier, so "Ctrl+" , "Ctrl+Alt" (no key)
    and "Banana" all return None rather than a half-understood combination.

    ``MOD_NOREPEAT`` is deliberately *not* part of the returned modifiers: it is a
    registration flag, not something a user types, and including it would make the value
    round-trip badly through `format_hotkey`. `HotkeyListener` adds it when registering.
    """
    if not isinstance(text, str):
        return None
    tokens = [part.strip() for part in text.split("+")]
    if len(tokens) < 1 or any(not token for token in tokens):
        return None

    *modifier_tokens, key_token = tokens
    modifiers = 0
    for token in modifier_tokens:
        bit = _MODIFIER_ALIASES.get(token.lower())
        if bit is None:
            return None
        modifiers |= bit

    vk = _KEY_TO_VK.get(key_token.lower())
    if vk is None:
        return None
    return modifiers, vk


def format_hotkey(modifiers: int, vk: int) -> str:
    """The inverse of `parse_hotkey`, in a fixed order: Ctrl, Alt, Shift, Win, key.

    ``MOD_NOREPEAT`` (or any other stray bit) is ignored, so a value taken straight back
    out of a registration still formats. An unsupported virtual key gives "" - there is no
    name to show and inventing one would hide the problem.
    """
    name = _VK_TO_KEY.get(int(vk))
    if name is None:
        return ""
    parts = [label for bit, label in _MODIFIER_ORDER if int(modifiers) & bit]
    parts.append(name)
    return "+".join(parts)


@dataclass
class _Run:
    """Everything one `start`/`stop` cycle owns, including its worker thread.

    The worker writes *only* to its own `_Run`. That is the whole point of this class: a
    worker still finishing a slow callback after its `stop` gave up joining it must not be
    able to flip `registered` or overwrite `thread_id` for the run that replaced it. The
    listener mirrors a field onto itself only after checking the run is still the current
    one.
    """

    modifiers: int
    vk: int
    canonical: str
    ready: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    thread_id: int = 0
    registered: bool = False
    registered_ok: bool = False  # latches True once RegisterHotKey succeeded
    cancelled: bool = False
    error: str = ""
    callback_error: str = ""
    presses: int = 0

    @property
    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class HotkeyListener:
    """One system-wide hotkey, registered on a thread of its own.

    Usage is `start` then `stop`; the object may be restarted. `start` returns "" on success
    or a sentence explaining the failure, and never returns "" for a hotkey that will not
    actually fire - it waits for the worker thread's registration result before returning.

    `on_pressed` runs on the listener thread. If it raises, the exception is recorded in
    `callback_error` (and printed) and the listener keeps going.

    If a callback outlives `stop`'s join - which is the normal case when the callback is
    "stop the recording" - the worker is remembered rather than abandoned, and `start`
    refuses with an explanation until that thread has exited. It cannot do anything else
    honestly: the dying worker still holds the combination, so re-registering it would fail
    anyway, and replacing the handle would strand the thread for good.
    """

    def __init__(self, on_pressed: Callable[[], None]) -> None:
        self._on_pressed = on_pressed
        self._run: _Run | None = None
        self._orphans: list[_Run] = []
        self._error: str = ""
        self._lock = threading.Lock()
        self.presses = 0
        self.callback_error: str = ""

    # -- public API --------------------------------------------------------

    @property
    def active(self) -> bool:
        """True only while a successful registration is being pumped by a live thread."""
        run = self._run
        return run is not None and run.registered and run.alive

    @property
    def error(self) -> str:
        """The last failure, or "" when the last `start` succeeded.

        A loop that dies on its own after a successful `start` (``GetMessageW`` returning
        -1) reports through here too, so a listener that quietly stopped working is not
        indistinguishable from a healthy one.
        """
        run = self._run
        if run is not None and run.error:
            return run.error
        return self._error

    @property
    def hotkey_text(self) -> str:
        """The combination currently registered, canonically spelled, or ""."""
        run = self._run
        if run is None or not (run.registered and run.alive):
            return ""
        return run.canonical

    def start(self, hotkey_text: str) -> str:
        """Register `hotkey_text` system-wide. Returns "" or why it failed.

        Synchronous: by the time this returns, the worker thread has already called
        ``RegisterHotKey`` and reported the result back.
        """
        with self._lock:
            current = self._run
            if current is not None and current.registered and current.alive:
                return (
                    f"A hotkey is already registered ({current.canonical}). "
                    "Stop the listener before starting it again."
                )

            # Cleared before the reaping below, so anything a previous worker reported on
            # its way out survives into this run instead of being wiped by it.
            self.callback_error = ""

            if current is not None:
                self._run = None
                self._stop_run(current, timeout=1.0)
            self._reap_orphans()

            stale = self._stale_worker_problem()
            if stale:
                self._error = stale
                return stale

            parsed = parse_hotkey(hotkey_text)
            if parsed is None:
                self._error = (
                    f"{hotkey_text!r} is not a usable hotkey. Expected something like "
                    "Ctrl+Alt+R: Ctrl/Alt/Shift/Win plus one of A-Z, 0-9 or F1-F24."
                )
                return self._error

            modifiers, vk = parsed
            run = _Run(modifiers=modifiers, vk=vk, canonical=format_hotkey(modifiers, vk))
            self._error = ""

            run.thread = threading.Thread(
                target=self._worker,
                args=(run,),
                name=f"hotkey-{run.canonical}",
                daemon=True,
            )
            # Published before the thread starts, so the worker's own `self._run is run`
            # checks see it as current from its very first message.
            self._run = run
            run.thread.start()

            # The worker only has a RegisterHotKey call to make, but a hung thread must not
            # hang the UI; a timeout is itself a reportable failure.
            if not run.ready.wait(timeout=5.0):
                self._error = (
                    f"The hotkey thread for {run.canonical} did not report back within 5 s; "
                    "the hotkey is not registered."
                )
                self._run = None
                self._stop_run(run, timeout=3.0)
                return self._error

            if not run.registered:
                self._error = (
                    run.error or f"Registering {run.canonical} failed for an unknown reason."
                )
                self._run = None
                self._stop_run(run, timeout=1.0)  # already returning; this just joins it
                return self._error
            return ""

    def stop(self) -> None:
        """Unregister and join. Safe to call twice, and safe when `start` failed.

        The listener counts as stopped the moment this is called - `active` is False and no
        further callback fires - even if the worker needs a little longer to leave its own
        message loop.
        """
        with self._lock:
            run = self._run
            self._run = None
            if run is not None:
                self._stop_run(run, timeout=3.0)
            self._reap_orphans()

    # -- internals ---------------------------------------------------------

    def _note(self, text: str) -> None:
        """Keep the first problem reported; later ones must not overwrite the cause."""
        if text and not self.callback_error:
            self.callback_error = text

    def _stop_run(self, run: _Run, timeout: float) -> None:
        """Ask one run's worker to leave its loop and join it. Caller holds `_lock`.

        A worker that does not come back in time is moved to `_orphans`, not dropped: it
        still holds the registration and a later `start` or `stop` must be able to find it.
        """
        run.cancelled = True
        thread = run.thread
        if thread is None:
            return
        if thread.is_alive() and run.thread_id:
            if not user32.PostThreadMessageW(
                wintypes.DWORD(run.thread_id), WM_QUIT, wintypes.WPARAM(0), wintypes.LPARAM(0)
            ):
                # The thread is already on its way out, or the id is stale. Either way the
                # join below decides; record it so a hang is explainable.
                code = ctypes.get_last_error()
                self._note(
                    f"Could not post WM_QUIT to the hotkey thread for {run.canonical} "
                    f"(error {code})."
                )
        thread.join(timeout=timeout)
        if thread.is_alive():
            self._note(
                f"The hotkey thread for {run.canonical} did not exit within {timeout:g} s; "
                f"it still holds {run.canonical} until its callback returns."
            )
            if run not in self._orphans:
                self._orphans.append(run)
            return
        self._collect(run)

    def _reap_orphans(self) -> None:
        """Drop the workers that have since exited, keeping what they reported."""
        still_running: list[_Run] = []
        for run in self._orphans:
            if run.alive:
                still_running.append(run)
            else:
                self._collect(run)
        self._orphans = still_running

    def _collect(self, run: _Run) -> None:
        """Fold a finished run's problems into the listener, so they are not lost.

        A registration failure is left out on purpose: `start` already returned it to the
        caller and stored it in `_error`, and repeating it as a callback problem would
        describe one failure twice. A loop that died *after* registering did not get
        reported anywhere else, so that one is kept.
        """
        self._note(run.callback_error)
        if run.registered_ok:
            self._note(run.error)

    def _stale_worker_problem(self) -> str:
        if not self._orphans:
            return ""
        names = ", ".join(sorted({run.canonical for run in self._orphans}))
        return (
            f"The previous hotkey thread ({names}) has not exited yet - its callback is "
            "still running and it still holds that combination. Try again once it has "
            "finished."
        )

    def _worker(self, run: _Run) -> None:
        """Own the registration and pump messages until WM_QUIT. Writes only to `run`."""
        run.thread_id = int(kernel32.GetCurrentThreadId())

        # Force the thread's message queue into existence before anyone can post to it.
        # Without this a PostThreadMessageW that races with startup is simply dropped.
        message = wintypes.MSG()
        user32.PeekMessageW(byref(message), None, 0x0400, 0x0400, PM_NOREMOVE)

        if not user32.RegisterHotKey(None, _HOTKEY_ID, run.modifiers | MOD_NOREPEAT, run.vk):
            code = ctypes.get_last_error()
            run.error = self._describe_failure(run.canonical, code)
            run.thread_id = 0
            run.ready.set()
            return

        run.registered = True
        run.registered_ok = True
        run.ready.set()
        try:
            self._pump(run)
        finally:
            run.registered = False
            if not user32.UnregisterHotKey(None, _HOTKEY_ID):
                code = ctypes.get_last_error()
                run.callback_error = (
                    run.callback_error or f"Unregistering {run.canonical} failed (error {code})."
                )

    def _pump(self, run: _Run) -> None:
        message = wintypes.MSG()
        while True:
            result = user32.GetMessageW(byref(message), None, 0, 0)
            if result == 0:  # WM_QUIT
                return
            if result == -1:
                code = ctypes.get_last_error()
                run.error = f"The hotkey message loop failed (error {code})."
                return
            if message.message == WM_HOTKEY and message.wParam == _HOTKEY_ID:
                run.presses += 1
                if run.cancelled or self._run is not run:
                    # Stopped (or replaced) while this press was already in the queue.
                    # The owner has asked for no more callbacks; count it and move on.
                    continue
                self.presses += 1
                try:
                    self._on_pressed()
                except Exception as exc:
                    # Never let a caller's bug silently stop the hotkey working.
                    run.callback_error = f"The hotkey callback raised: {exc}"
                    if self._run is run:
                        self.callback_error = run.callback_error
                    traceback.print_exc()

    @staticmethod
    def _describe_failure(canonical: str, code: int) -> str:
        if code == ERROR_HOTKEY_ALREADY_REGISTERED:
            return (
                f"{canonical} is already registered - another program (or another part "
                "of this one) holds it. Choose a different combination."
            )
        if code == ERROR_INVALID_PARAMETER:
            return f"Windows rejected {canonical} as a hotkey (error 87)."
        return f"Registering {canonical} failed (error {code})."
