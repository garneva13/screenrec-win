"""The low-level mouse hook that tells the overlay when to draw a ring.

A click highlight has to know about clicks that land in *other* applications, so there is
no Qt event to listen to: the only way is a system-wide ``WH_MOUSE_LL`` hook.

Three Windows facts shape everything in this module:

* A low-level hook is **not** called on the thread that is clicking. Windows posts the
  event to the message queue of the thread that installed the hook and runs the callback
  there. A thread with no message loop therefore never sees a single event, so the watcher
  owns a thread whose whole job is ``GetMessageW``.
* The callback runs **inside other processes' input path**. If it takes longer than
  ``HKEY_CURRENT_USER\\Control Panel\\Desktop\\LowLevelHooksTimeout`` (300 ms by default)
  Windows quietly drops the hook out of the chain and the feature stops working with no
  error anywhere. So the callback reads the point, drops it in a queue and returns; a
  second thread does the calling back.
* The ``CFUNCTYPE`` object wrapping the callback must stay alive for as long as the hook
  does - see ``_proc`` below.

Coordinates arrive in physical desktop pixels, the same space ``winapi.list_monitors()``
reports and the same space the overlay draws in, so nothing is converted here.
"""
from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import POINTER, byref, c_int, c_uint, wintypes
from dataclasses import dataclass
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
HC_ACTION = 0

WM_QUIT = 0x0012
WM_USER = 0x0400
PM_NOREMOVE = 0x0000

WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207

#: Only the three buttons the contract names produce a `ClickEvent`. The X buttons
#: (``WM_XBUTTONDOWN``) are deliberately ignored: "back" and "forward" are navigation, not
#: something a viewer of the recording needs pointed out, and inventing extra `button`
#: strings would hand the overlay a value it has no colour for.
BUTTON_NAMES = {
    WM_LBUTTONDOWN: "left",
    WM_RBUTTONDOWN: "right",
    WM_MBUTTONDOWN: "middle",
}

ERROR_INVALID_THREAD_ID = 1444

LRESULT = ctypes.c_ssize_t
ULONG_PTR = wintypes.WPARAM
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    """What ``lParam`` points at for a ``WH_MOUSE_LL`` event."""

    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


user32.SetWindowsHookExW.argtypes = [c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [POINTER(wintypes.MSG), wintypes.HWND, c_uint, c_uint]
user32.GetMessageW.restype = c_int
user32.PeekMessageW.argtypes = [POINTER(wintypes.MSG), wintypes.HWND, c_uint, c_uint, c_uint]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [POINTER(wintypes.MSG)]
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, c_uint, wintypes.WPARAM, wintypes.LPARAM
]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


@dataclass(frozen=True)
class ClickEvent:
    """One mouse button going down, in physical desktop pixels."""

    x: int
    y: int
    button: str  # "left" | "right" | "middle"


class ClickWatcher:
    """Report every mouse button press anywhere on the desktop.

    `on_click` is called **on a worker thread owned by this watcher**, never on the thread
    that called `start` and never on the Qt GUI thread. A Qt caller must marshal - emit a
    signal, or `QtCore.QMetaObject.invokeMethod(..., Qt.ConnectionType.QueuedConnection)` -
    and must not touch widgets from inside the callback.

    `on_click` is also not allowed to be slow in spirit, even though it does not run in the
    hook itself: events queue up behind it and the rings will lag the clicks.

    Restartable: `stop` then `start` installs a fresh hook. If `stop` could not retire the
    previous run's threads it says so in `error` and keeps them, and `start` then refuses
    with text rather than stacking a second hook on the first.
    """

    def __init__(self, on_click: Callable[[ClickEvent], None]) -> None:
        self._on_click = on_click
        self._error: str = ""
        self._hook: int | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._dispatcher: threading.Thread | None = None
        #: The queue the live dispatcher is looping on, kept so `stop` can post the
        #: sentinel to *that* thread's queue even after `start` has built a new one.
        self._dispatcher_queue: queue.SimpleQueue[ClickEvent | None] | None = None
        self._dispatcher_stop = threading.Event()
        self._queue: queue.SimpleQueue[ClickEvent | None] = queue.SimpleQueue()
        self._running = False
        self._ready = threading.Event()
        self._clicks_seen = 0
        self._lock = threading.Lock()

        # THE REFERENCE THAT MUST NOT DIE.
        #
        # `HOOKPROC(self._hook_proc)` builds a small trampoline and hands Windows its raw
        # address. ctypes owns that trampoline; nothing on the Windows side keeps it alive.
        # If the only Python reference went out of scope the object would be freed and the
        # address Windows holds would point at released memory - and the crash would not
        # come at `stop()` time, it would come the next time anyone anywhere moves the
        # mouse, as an access violation with no traceback. So it is built once, in
        # __init__, and lives exactly as long as the watcher does.
        self._proc = HOOKPROC(self._hook_proc)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> str:
        """Install the hook. Returns "" on success, or why it failed.

        If a previous run would not stop, this **refuses** rather than installing a second
        system-wide hook on top of the first one. Two hooks in the chain mean `on_click`
        fires twice for every press - two rings per click - and the extra hook belongs to a
        thread nothing can reach any more. See `stop`.
        """
        if self.active:
            return ""
        if self._running or self._thread is not None or self._dispatcher is not None:
            # A previous run that has not finished unwinding; let it go first.
            self.stop()

        stale = self._stale_problem()
        if stale:
            # Whatever `stop` already recorded about the old hook outranks this: it is the
            # same event, and that wording is the one that names the cause. Clearing
            # `_error` here - as this used to - erased the only warning that a hook was
            # still live.
            self._error = self._error or stale
            return self._error

        self._error = ""
        self._ready.clear()
        self._dispatcher_stop = threading.Event()
        work: queue.SimpleQueue[ClickEvent | None] = queue.SimpleQueue()
        self._queue = work
        self._dispatcher_queue = work

        self._dispatcher = threading.Thread(
            target=self._dispatch_loop,
            args=(work, self._dispatcher_stop),
            name="clicks-dispatch",
            daemon=True,
        )
        self._dispatcher.start()

        self._thread = threading.Thread(target=self._run, name="clicks-hook", daemon=True)
        self._running = True
        self._thread.start()

        if not self._ready.wait(timeout=5.0):
            self._error = self._error or "The mouse hook thread did not start within 5 s."
        if self._error:
            self.stop()
            return self._error
        return ""

    def stop(self) -> None:
        """Remove the hook and join both threads. Safe to call twice, or never started.

        A thread that refuses to exit is **kept**, not forgotten. Dropping the handle would
        throw away the thread id that is the only way to post it another ``WM_QUIT``,
        leaving a system-wide hook installed that nothing in this process could ever
        remove - and every later `stop` returning instantly having done nothing. Instead
        `error` says what happened, `start` refuses while it is true, and a later `stop`
        retries both the post and the join.
        """
        self._running = False

        thread = self._thread
        if thread is not None:
            if thread.is_alive():
                self._post_quit()
                thread.join(timeout=3.0)
            if thread.is_alive():
                self._error = (
                    self._error
                    or "The mouse hook thread did not exit; the hook may still be installed."
                )
            else:
                # `_run` unhooks in its `finally`, so only a joined thread proves the hook
                # is gone and the id is safe to discard.
                self._thread = None
                self._thread_id = 0

        dispatcher = self._dispatcher
        if dispatcher is not None:
            # The flag before the sentinel: a dispatcher stuck inside a slow `on_click`
            # must not call it a second time once `stop` has returned, because by then the
            # caller is entitled to have destroyed the overlay it calls into.
            self._dispatcher_stop.set()
            work = self._dispatcher_queue
            if work is not None:
                work.put(None)
            if dispatcher.is_alive():
                dispatcher.join(timeout=3.0)
            if dispatcher.is_alive():
                self._error = self._error or "The click dispatch thread did not exit."
            else:
                self._dispatcher = None
                self._dispatcher_queue = None

    def _stale_problem(self) -> str:
        """Why a fresh `start` would be unsafe right now, or "" when the watcher is clean."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            return (
                "The mouse hook thread from the previous run is still alive, so its hook "
                "may still be installed; starting now would put a second hook in the "
                "chain and report every click twice. Call stop() again."
            )
        if self._hook is not None:
            return (
                "A mouse hook from the previous run is still installed. Call stop() again "
                "before starting a new one."
            )
        dispatcher = self._dispatcher
        if dispatcher is not None and dispatcher.is_alive():
            return (
                "The click dispatch thread from the previous run is still alive - most "
                "likely on_click has not returned. Call stop() again once it has."
            )
        return ""

    @property
    def active(self) -> bool:
        """True while *this* run's hook is installed and its thread is pumping messages.

        False after a `stop` that timed out, even though the hook may well still be live
        then: the watcher is wedged, not working, and saying "active" would invite a caller
        to keep using it. `error` is what reports that state, and `start` refuses to begin
        a new run until a later `stop` has retired the old thread.
        """
        thread = self._thread
        return (
            self._running
            and self._hook is not None
            and thread is not None
            and thread.is_alive()
        )

    @property
    def error(self) -> str:
        """The first thing that went wrong, or "". Also set by a failure after `start`."""
        return self._error

    @property
    def clicks_seen(self) -> int:
        """Presses the hook reported. Zero after a while of clicking means Windows dropped
        the hook (too slow a callback, or a UAC-elevated window has focus)."""
        return self._clicks_seen

    # -- the hook thread ---------------------------------------------------

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())

        # Force the message queue into existence before anyone may PostThreadMessageW to
        # it: a thread has no queue until it first asks for a message, and a post to a
        # thread without one fails with ERROR_INVALID_THREAD_ID and is lost.
        msg = wintypes.MSG()
        user32.PeekMessageW(byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)

        module = kernel32.GetModuleHandleW(None)
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, module, 0)
        if not hook:
            code = ctypes.get_last_error()
            self._error = f"Could not install the mouse hook (error {code})."
            self._ready.set()
            return
        self._hook = hook
        self._ready.set()

        try:
            while True:
                result = user32.GetMessageW(byref(msg), None, 0, 0)
                if result == 0:  # WM_QUIT
                    break
                if result == -1:
                    code = ctypes.get_last_error()
                    self._error = self._error or f"The hook message loop failed (error {code})."
                    break
                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))
        finally:
            if not user32.UnhookWindowsHookEx(wintypes.HANDLE(self._hook)):
                code = ctypes.get_last_error()
                self._error = self._error or f"Could not remove the mouse hook (error {code})."
            self._hook = None

    def _post_quit(self) -> None:
        if not self._thread_id:
            return
        if user32.PostThreadMessageW(wintypes.DWORD(self._thread_id), WM_QUIT, 0, 0):
            return
        code = ctypes.get_last_error()
        thread = self._thread
        if code == ERROR_INVALID_THREAD_ID and (thread is None or not thread.is_alive()):
            return  # the thread is already gone; nothing left to wake
        # A live thread whose id Windows will not accept is a real problem, not a benign
        # race: it cannot be woken, so the join below is going to time out.
        self._error = self._error or f"Could not stop the mouse hook thread (error {code})."

    # -- the callback ------------------------------------------------------

    def _hook_proc(self, n_code: int, wparam: int, lparam: int) -> int:
        """Runs on the hook thread, inside somebody else's input path. Do almost nothing.

        Note what is *not* here: no Qt, no logging, no allocation beyond one small object,
        no lock held across anything. `queue.SimpleQueue.put` is lock-free in CPython and
        never blocks, which is exactly the property this needs.
        """
        try:
            if n_code == HC_ACTION:
                name = BUTTON_NAMES.get(int(wparam))
                if name is not None:  # everything else, WM_MOUSEMOVE above all, is dropped
                    data = ctypes.cast(lparam, POINTER(MSLLHOOKSTRUCT)).contents
                    self._clicks_seen += 1
                    self._queue.put(ClickEvent(x=int(data.pt.x), y=int(data.pt.y), button=name))
        except Exception as exc:  # never let an exception unwind into Windows
            with self._lock:
                self._error = self._error or f"The mouse hook callback failed: {exc}"
        return user32.CallNextHookEx(None, n_code, wparam, lparam)

    # -- the dispatch thread -----------------------------------------------

    def _dispatch_loop(
        self,
        work: queue.SimpleQueue[ClickEvent | None],
        stop_flag: threading.Event,
    ) -> None:
        """Call `on_click` off the hook thread, so a slow callback cannot kill the hook.

        The queue arrives as an **argument** rather than being re-read from ``self`` each
        time round: a dispatcher that outlived its own `stop` - because `on_click` blocked
        past the join timeout - would otherwise drift onto the next run's queue and start
        eating events meant for the new dispatcher, including its sentinel. `stop_flag`
        then ends such a thread at the next event instead of letting it call back into
        whatever the caller tore down after `stop` returned.
        """
        while True:
            event = work.get()
            if event is None or stop_flag.is_set():
                return
            try:
                self._on_click(event)
            except Exception as exc:
                with self._lock:
                    self._error = self._error or f"A click callback raised: {exc}"
