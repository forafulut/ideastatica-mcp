"""Lifecycle management of the IdeaStatiCa.ConnectionRestApi.exe service.

The service ships with the IDEA StatiCa desktop installation. It listens on a local
port, answers ``GET /heartbeat`` with 200 once it is ready and serves the Connection REST
API under ``/api/4``. Starting it reserves a seat on the IDEA StatiCa licence, which can
take a while (the licence check talks to IDEA StatiCa's servers) - longer than an MCP
client is willing to wait for one tool call. Startup is therefore *bounded*: a call waits
at most ``start_wait`` seconds and otherwise reports "still starting"; the process keeps
running in the background and a later call picks it up. A small state file remembers the
running service so that a restarted MCP server re-adopts it instead of starting another.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ideastatica_mcp.config import API_BASE_PATH, Settings
from ideastatica_mcp.signin_guard import shared_guard

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = "/heartbeat"
VERSION_PATH = f"{API_BASE_PATH}/clients/idea-service-version"
LOCALHOST = "127.0.0.1"
STATE_FILE = "service-state.json"
NOISE_MARKERS = ("serilog", "Serilog", "System.Net", "at System.", "at Idea", "End of inner", "Threading")


class ServiceError(RuntimeError):
    """The REST API service could not be started or reached."""


class ServiceStarting(ServiceError):
    """The service process is running but not ready yet; call again later."""


def http_get(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Accept": "application/json, text/plain, */*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (local URL)
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() if exc.fp else b""


def is_reachable(base_url: str, timeout: float = 3.0) -> bool:
    try:
        status, _ = http_get(base_url + HEARTBEAT_PATH, timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return status == 200


def get_service_version(base_url: str, timeout: float = 5.0) -> str | None:
    try:
        status, body = http_get(base_url + VERSION_PATH, timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if status != 200:
        return None
    text = body.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, str):
            return parsed
        return json.dumps(parsed)
    except json.JSONDecodeError:
        return text.strip('"')


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOCALHOST, 0))
        return int(sock.getsockname()[1])


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # pragma: no cover - defensive
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def kill_pid(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:  # pragma: no cover
        os.kill(pid, signal.SIGTERM)


def _tail_lines(path: Path | None, limit: int = 25) -> list[str]:
    if path is None:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if not any(marker in line for marker in NOISE_MARKERS)]
    return lines[-limit:]


# --- Windows job object (optional: kills the service when this process dies) -----------

def _assign_to_kill_on_close_job(process: subprocess.Popen) -> object | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):  # type: ignore[attr-defined]
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception as exc:  # pragma: no cover
        logger.debug("Job object assignment failed: %s", exc)
        return None


class ServiceManager:
    """Starts, adopts, stops and monitors the Connection REST API service."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._process: subprocess.Popen | None = None
        self._adopted_pid: int | None = None
        self._job: object | None = None
        self._managed_url: str | None = None
        self._log_path: Path | None = None
        self._lock = threading.RLock()
        self._started_at: float | None = None
        self.signin_guard = shared_guard(settings.log_dir)
        self._adopt_from_state()

    def ensure_signin_guard(self) -> bool:
        """Start the licence sign-in auto-dismiss guard (once per process) unless disabled."""
        if not self.settings.dismiss_signin:
            return False
        return self.signin_guard.start()

    # -- state file --------------------------------------------------------------------

    @property
    def _state_path(self) -> Path:
        return self.settings.log_dir / STATE_FILE

    def _write_state(self) -> None:
        try:
            self.settings.log_dir.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"url": self._managed_url, "pid": self.pid, "started_at": self._started_at, "log_file": str(self._log_path) if self._log_path else None}),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            logger.debug("state file not written: %s", exc)

    def _clear_state(self) -> None:
        try:
            self._state_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass

    def _adopt_from_state(self) -> None:
        """Re-adopt a service started by a previous MCP server instance (e.g. after a client restart)."""
        if self.settings.api_url:
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        pid, url = data.get("pid"), data.get("url")
        if url and pid and pid_alive(pid):
            self._adopted_pid = int(pid)
            self._managed_url = url
            self._started_at = data.get("started_at")
            self._log_path = Path(data["log_file"]) if data.get("log_file") else None
            logger.info("Adopted running service pid %s at %s", pid, url)
        else:
            self._clear_state()

    # -- state -------------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return "attached" if self.settings.api_url else "managed"

    @property
    def base_url(self) -> str | None:
        return self.settings.api_url or self._managed_url

    @property
    def pid(self) -> int | None:
        if self._process is not None:
            return self._process.pid
        return self._adopted_pid

    def process_alive(self) -> bool:
        if self._process is not None:
            return self._process.poll() is None
        return pid_alive(self._adopted_pid)

    def is_running(self) -> bool:
        url = self.base_url
        return bool(url) and is_reachable(url, self.settings.heartbeat_timeout)

    def status(self) -> dict:
        url = self.base_url
        reachable = bool(url) and is_reachable(url, self.settings.heartbeat_timeout)
        alive = self.process_alive()
        state = "ready" if reachable else ("starting" if alive else "stopped")
        elapsed = round(time.time() - self._started_at, 1) if self._started_at and alive else None
        return {
            "mode": self.mode,
            "state": state,
            "url": url,
            "reachable": reachable,
            "service_version": get_service_version(url) if reachable and url else None,
            "managed_process": {
                "running": alive,
                "pid": self.pid if alive else None,
                "adopted": self._process is None and self._adopted_pid is not None,
                "seconds_since_start": elapsed,
                "log_file": str(self._log_path) if self._log_path else None,
                "last_log_lines": _tail_lines(self._log_path, 5) if alive and not reachable else [],
            },
            "setup_dir": str(self.settings.setup_dir) if self.settings.setup_dir else None,
            "service_exe_found": bool(self.settings.service_exe and self.settings.service_exe.is_file()),
            "autostart": self.settings.autostart,
            "signin_guard": self.signin_guard.status() if self.settings.dismiss_signin else {"enabled": False},
        }

    # -- lifecycle ---------------------------------------------------------------------

    def ensure_running(self, wait_seconds: float | None = None) -> str:
        """Return the base URL of a ready service, starting it when allowed (bounded wait)."""
        self.ensure_signin_guard()  # every connect path goes through here, including prewarm
        with self._lock:
            url = self.base_url
            if url and is_reachable(url, self.settings.heartbeat_timeout):
                return url
            if url and not self.settings.api_url and self.process_alive():
                return self._wait_ready(url, wait_seconds)

            if self.settings.api_url:
                if not self.settings.autostart:
                    raise ServiceError(
                        f"The IDEA StatiCa Connection REST API service at {self.settings.api_url} is not "
                        "reachable and IDEASTATICA_AUTOSTART is disabled. Start "
                        "IdeaStatiCa.ConnectionRestApi.exe from the IDEA StatiCa installation folder."
                    )
                return self.start(port=self._port_from_url(self.settings.api_url), wait_seconds=wait_seconds)

            if not self.settings.autostart:
                raise ServiceError(
                    "The IDEA StatiCa Connection REST API service is not running and IDEASTATICA_AUTOSTART "
                    "is disabled. Call service_start or set IDEASTATICA_API_URL to a running service."
                )
            return self.start(port=self.settings.port, wait_seconds=wait_seconds)

    def start(self, port: int | None = None, wait_seconds: float | None = None) -> str:
        """Spawn the service (unless one is already running) and wait a bounded time for readiness."""
        self.ensure_signin_guard()  # the licence check during start-up is where the window appears
        with self._lock:
            if self.process_alive() and self._managed_url:
                if port is None or self._port_from_url(self._managed_url) == port:
                    return self._wait_ready(self._managed_url, wait_seconds)
                self.stop()

            exe = self.settings.service_exe
            if exe is None or not exe.is_file():
                raise ServiceError(
                    "IdeaStatiCa.ConnectionRestApi.exe was not found. Install IDEA StatiCa 24.1 or newer, "
                    "or set IDEASTATICA_SETUP_DIR to the installation folder "
                    f"(looked in: {self.settings.setup_dir})."
                )

            # Another MCP instance (Claude Desktop + Claude Code run their own servers) may have
            # started a service meanwhile: share it instead of spawning a second licence seat.
            if port is None:
                self._adopt_from_state()
                if self._adopted_pid and self._managed_url:
                    return self._wait_ready(self._managed_url, wait_seconds)

            chosen_port = port or self.settings.port or find_free_port()
            url = f"http://{LOCALHOST}:{chosen_port}"
            if is_reachable(url, 1.0):
                logger.info("A service already answers on %s; attaching to it.", url)
                self._managed_url = url
                return url

            self.settings.log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = self.settings.log_dir / f"service-{chosen_port}.log"
            log_file = open(self._log_path, "ab")  # noqa: SIM115 - handed to the subprocess

            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

            logger.info("Starting %s on port %s", exe, chosen_port)
            try:
                self._process = subprocess.Popen(
                    [str(exe), f"-port={chosen_port}"],
                    cwd=str(exe.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            finally:
                log_file.close()
            self._adopted_pid = None
            self._started_at = time.time()
            self._managed_url = url
            if self.settings.kill_on_exit:
                self._job = _assign_to_kill_on_close_job(self._process)
            self._write_state()
            return self._wait_ready(url, wait_seconds)

    def _wait_ready(self, url: str, wait_seconds: float | None) -> str:
        budget = self.settings.start_wait if wait_seconds is None else wait_seconds
        started = self._started_at or time.time()
        call_deadline = time.monotonic() + max(0.0, budget)
        while True:
            if not self.process_alive():
                code = self._process.returncode if self._process is not None else None
                tail = "\n".join(_tail_lines(self._log_path))
                self._forget_process()
                raise ServiceError(
                    f"The service exited (code {code}) before becoming ready. Common causes: a Basic licence "
                    "(the API is disabled for it), no free licence seat, or a missing ASP.NET Core runtime. "
                    f"Service output:\n{tail}"
                )
            if is_reachable(url, 2.0):
                logger.info("Service ready at %s after %.1fs", url, time.time() - started)
                return url
            elapsed = time.time() - started
            if elapsed > self.settings.start_timeout:
                tail = "\n".join(_tail_lines(self._log_path))
                self.stop()
                raise ServiceError(
                    f"The service did not answer on {url}{HEARTBEAT_PATH} within {self.settings.start_timeout:.0f}s "
                    "and was stopped. It usually hangs in the licence check when IDEA StatiCa's licence servers "
                    "cannot be reached, or when no seat is available. Check that IDEA StatiCa itself can start and "
                    f"is signed in, then retry (IDEASTATICA_START_TIMEOUT raises the limit). Service output:\n{tail}"
                )
            if time.monotonic() >= call_deadline:
                raise ServiceStarting(
                    f"The service is still starting ({elapsed:.0f}s so far; licence check in progress, "
                    f"limit {self.settings.start_timeout:.0f}s). It keeps starting in the background - call "
                    "service_status or retry the tool in about a minute."
                )
            time.sleep(1.0)

    def _forget_process(self) -> None:
        self._process = None
        self._adopted_pid = None
        self._managed_url = None
        self._job = None
        self._clear_state()

    def stop(self, timeout: float = 15.0) -> bool:
        with self._lock:
            stopped = False
            if self._process is not None and self._process.poll() is None:
                logger.info("Stopping service pid %s", self._process.pid)
                try:
                    self._process.terminate()
                    self._process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
                except OSError:
                    pass
                stopped = True
            elif self._adopted_pid and pid_alive(self._adopted_pid):
                logger.info("Stopping adopted service pid %s", self._adopted_pid)
                kill_pid(self._adopted_pid)
                stopped = True
            self._forget_process()
            return stopped

    @staticmethod
    def _port_from_url(url: str) -> int | None:
        parsed = urlparse(url)
        if parsed.hostname not in (LOCALHOST, "localhost", "::1"):
            return None
        return parsed.port


def describe_environment() -> dict:
    return {"platform": sys.platform, "python": sys.version.split()[0], "cwd": os.getcwd()}
