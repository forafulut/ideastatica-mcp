"""Auto-dismiss the IDEA StatiCa licence "Sign in" window while the service is being used.

When the licence session has to be refreshed, IDEA StatiCa's License.exe opens a
"Sign in - IDEA StatiCa" window. The REST service (and its solver) block until that window
is answered, which looks like a hung start-up or a calculation that never finishes.
Cancelling the window lets the service continue with the locally validated licence and
local design sets, so this guard closes it (WM_CLOSE, the same as pressing Cancel) as soon
as it appears.

The guard is a single daemon thread per MCP server process. It is started whenever the
service manager starts, adopts or attaches to a service, and runs for the lifetime of the
process. Disable it with IDEASTATICA_DISMISS_SIGNIN=false.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SIGNIN_TITLE_PREFIX = "Sign in"
SIGNIN_TITLE_MARKER = "IDEA StatiCa"
LICENSE_PROCESS = "license.exe"
WM_CLOSE = 0x0010


def is_signin_window(title: str, process_name: str) -> bool:
    """True for the licence sign-in window ("Sign in - IDEA StatiCa" owned by License.exe)."""
    return (
        title.startswith(SIGNIN_TITLE_PREFIX)
        and SIGNIN_TITLE_MARKER in title
        and Path(process_name).name.lower() == LICENSE_PROCESS
    )


def _list_top_windows() -> list[tuple[int, str, str]]:
    """Visible top-level windows as (hwnd, title, process image path). Windows only."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    windows: list[tuple[int, str, str]] = []

    def process_path(pid: int) -> str:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return ""
        finally:
            kernel32.CloseHandle(handle)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if not title.startswith(SIGNIN_TITLE_PREFIX):
            return True  # cheap filter before touching the owning process
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        windows.append((int(hwnd), title, process_path(pid.value)))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def _close_window(hwnd: int) -> bool:
    import ctypes

    return bool(ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))  # type: ignore[attr-defined]


class SignInGuard:
    """Background thread that closes the licence sign-in window whenever it appears."""

    def __init__(self, interval: float = 2.0, log_file: Path | None = None):
        self.interval = interval
        self.log_file = log_file
        self.closed_count = 0
        self.last_closed_at: float | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start the guard (idempotent). Returns True when it is running afterwards."""
        if not self.supported:
            return False
        with self._lock:
            if self.running:
                return True
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="ideastatica-signin-guard", daemon=True)
            self._thread.start()
            logger.info("Sign-in guard started (checks every %.1fs)", self.interval)
            return True

    def stop(self) -> None:
        self._stop.set()

    def sweep(self) -> int:
        """Close every sign-in window that is open right now; returns how many were closed."""
        closed = 0
        for hwnd, title, process in _list_top_windows():
            if is_signin_window(title, process) and _close_window(hwnd):
                closed += 1
                self.closed_count += 1
                self.last_closed_at = time.time()
                self._record(f"closed '{title}' (hwnd {hwnd}, {Path(process).name})")
        return closed

    def status(self) -> dict:
        return {
            "enabled": self.supported,
            "running": self.running,
            "closed_windows": self.closed_count,
            "last_closed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.last_closed_at)) if self.last_closed_at else None,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sweep()
            except Exception as exc:  # noqa: BLE001 - the guard must never die on a bad window
                logger.debug("Sign-in guard sweep failed: %s", exc)
            self._stop.wait(self.interval)

    def _record(self, message: str) -> None:
        logger.info("Sign-in guard: %s", message)
        if self.log_file is None:
            return
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
        except OSError:  # pragma: no cover
            pass


_shared_guard: SignInGuard | None = None
_shared_lock = threading.Lock()


def shared_guard(log_dir: Path | None = None) -> SignInGuard:
    """The one guard of this process (several ServiceManagers share it)."""
    global _shared_guard
    with _shared_lock:
        if _shared_guard is None:
            _shared_guard = SignInGuard(log_file=(log_dir / "signin-guard.log") if log_dir else None)
        return _shared_guard
