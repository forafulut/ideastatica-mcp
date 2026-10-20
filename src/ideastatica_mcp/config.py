"""Configuration: environment variables and IDEA StatiCa installation discovery."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "IDEASTATICA_"
SERVICE_EXE = "IdeaStatiCa.ConnectionRestApi.exe"
API_BASE_PATH = "/api/4"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(ENV_PREFIX + name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int | None) -> int | None:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def program_files_dirs() -> list[Path]:
    dirs: list[Path] = []
    for key in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        value = os.environ.get(key)
        if value:
            p = Path(value)
            if p not in dirs:
                dirs.append(p)
    if not dirs:
        dirs.append(Path(r"C:\Program Files"))
    return dirs


def _version_key(path: Path) -> tuple[int, int, int]:
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", path.name)
    if not match:
        return (0, 0, 0)
    return tuple(int(g) if g else 0 for g in match.groups())  # type: ignore[return-value]


def detect_setup_dirs() -> list[Path]:
    """All IDEA StatiCa installation directories that contain the REST API service, newest first."""
    candidates: list[Path] = []
    for pf in program_files_dirs():
        base = pf / "IDEA StatiCa"
        if not base.is_dir():
            continue
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and (entry / SERVICE_EXE).is_file():
                candidates.append(entry)
    return sorted(candidates, key=_version_key, reverse=True)


def detect_setup_dir() -> Path | None:
    dirs = detect_setup_dirs()
    return dirs[0] if dirs else None


@dataclass
class Settings:
    setup_dir: Path | None
    api_url: str | None
    port: int | None
    autostart: bool
    start_timeout: float
    output_dir: Path
    log_dir: Path
    log_level: str
    client_app_name: str
    client_app_version: str
    max_text_chars: int
    heartbeat_timeout: float
    start_wait: float = 40.0  # max seconds one tool call blocks while the service starts
    kill_on_exit: bool = False  # tie the service to this process with a Windows job object
    prewarm: bool = True  # start the service in the background as soon as the MCP server is up
    dismiss_signin: bool = True  # auto-cancel the licence "Sign in" window while connected

    @classmethod
    def from_env(cls) -> "Settings":
        from ideastatica_mcp import __version__

        setup_dir_env = _env("SETUP_DIR")
        setup_dir = Path(setup_dir_env) if setup_dir_env else detect_setup_dir()

        api_url = _env("API_URL")
        if api_url:
            api_url = api_url.rstrip("/")

        default_output = Path.home() / "Documents" / "IdeaStatiCa-MCP"
        output_dir = Path(_env("OUTPUT_DIR", str(default_output)) or default_output)

        local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        log_dir = Path(_env("LOG_DIR", str(Path(local_app_data) / "ideastatica-mcp" / "logs")) or "")

        return cls(
            setup_dir=setup_dir,
            api_url=api_url,
            port=_env_int("PORT", None),
            autostart=_env_bool("AUTOSTART", True),
            start_timeout=_env_float("START_TIMEOUT", 240.0),
            output_dir=output_dir,
            log_dir=log_dir,
            log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
            client_app_name=_env("CLIENT_APP_NAME", "ideastatica-mcp") or "ideastatica-mcp",
            client_app_version=_env("CLIENT_APP_VERSION", __version__) or __version__,
            max_text_chars=_env_int("MAX_TEXT_CHARS", 60000) or 60000,
            heartbeat_timeout=_env_float("HEARTBEAT_TIMEOUT", 3.0),
            start_wait=_env_float("START_WAIT", 40.0),
            kill_on_exit=_env_bool("KILL_ON_EXIT", False),
            prewarm=_env_bool("PREWARM", True),
            dismiss_signin=_env_bool("DISMISS_SIGNIN", True),
        )

    @property
    def service_exe(self) -> Path | None:
        if self.setup_dir is None:
            return None
        return self.setup_dir / SERVICE_EXE

    def as_dict(self) -> dict:
        return {
            "setup_dir": str(self.setup_dir) if self.setup_dir else None,
            "service_exe_found": bool(self.service_exe and self.service_exe.is_file()),
            "api_url": self.api_url,
            "port": self.port,
            "autostart": self.autostart,
            "start_timeout_seconds": self.start_timeout,
            "start_wait_seconds": self.start_wait,
            "kill_on_exit": self.kill_on_exit,
            "prewarm": self.prewarm,
            "dismiss_signin": self.dismiss_signin,
            "output_dir": str(self.output_dir),
            "log_dir": str(self.log_dir),
            "log_level": self.log_level,
            "client_app": f"{self.client_app_name}/{self.client_app_version}",
            "max_text_chars": self.max_text_chars,
        }
