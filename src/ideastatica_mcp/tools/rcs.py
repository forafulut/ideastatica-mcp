"""Tools: IDEA StatiCa RCS (reinforced concrete section checks) through IdeaStatiCa.RcsRestApi.exe.

The RCS service ships next to the Connection service and exposes REST API 1 (``/api/1``).
No matching Python SDK exists for 26.0, so the endpoints are called directly.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from ideastatica_mcp.errors import IdeaError, RestError, tool_errors
from ideastatica_mcp.service import LOCALHOST, find_free_port, is_reachable, pid_alive
from ideastatica_mcp.tools.common import DESTRUCTIVE, READ, WRITE, resolve_output_path, sanitize_filename

logger = logging.getLogger(__name__)
RCS_EXE = "IdeaStatiCa.RcsRestApi.exe"
RCS_STATE = "rcs-state.json"


class RcsSession:
    """Lifecycle + raw REST for the RCS service (one project at a time is typical)."""

    def __init__(self, settings):
        self.settings = settings
        self.url: str | None = settings_env_url()
        self.client_id: str | None = None
        self.process: subprocess.Popen | None = None
        self.pid: int | None = None
        self.started_at: float | None = None
        self.active_project_id: str | None = None
        self.projects: dict[str, dict] = {}
        self._adopt()

    # -- service ---------------------------------------------------------------------------
    @property
    def _state_path(self) -> Path:
        return self.settings.log_dir / RCS_STATE

    def _adopt(self) -> None:
        if self.url:
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if data.get("pid") and pid_alive(data["pid"]) and data.get("url"):
            self.pid, self.url, self.started_at = int(data["pid"]), data["url"], data.get("started_at")

    def exe(self) -> Path | None:
        if self.settings.setup_dir is None:
            return None
        return self.settings.setup_dir / RCS_EXE

    def ensure(self, wait_seconds: float | None = None) -> str:
        if self.url and is_reachable(self.url, 3.0):
            return self.url
        if self.url and (self.process and self.process.poll() is None or pid_alive(self.pid)):
            return self._wait(wait_seconds)
        exe = self.exe()
        if exe is None or not exe.is_file():
            raise IdeaError(f"{RCS_EXE} not found in {self.settings.setup_dir}")
        port = find_free_port()
        self.url = f"http://{LOCALHOST}:{port}"
        self.settings.log_dir.mkdir(parents=True, exist_ok=True)
        log = open(self.settings.log_dir / f"rcs-{port}.log", "ab")  # noqa: SIM115
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0  # type: ignore[attr-defined]
        try:
            self.process = subprocess.Popen([str(exe), f"-port={port}"], cwd=str(exe.parent), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        finally:
            log.close()
        self.pid = self.process.pid
        self.started_at = time.time()
        try:
            self._state_path.write_text(json.dumps({"url": self.url, "pid": self.pid, "started_at": self.started_at}), encoding="utf-8")
        except OSError:
            pass
        return self._wait(wait_seconds)

    def _wait(self, wait_seconds: float | None) -> str:
        budget = self.settings.start_wait if wait_seconds is None else wait_seconds
        deadline = time.monotonic() + budget
        while True:
            if is_reachable(self.url or "", 2.0):
                return self.url or ""
            alive = (self.process.poll() is None) if self.process else pid_alive(self.pid)
            if not alive:
                self.url = None
                raise IdeaError("the RCS service exited before becoming ready (licence?)")
            if time.time() - (self.started_at or time.time()) > self.settings.start_timeout:
                self.stop()
                raise IdeaError(f"the RCS service did not answer within {self.settings.start_timeout:.0f}s")
            if time.monotonic() >= deadline:
                raise IdeaError("the RCS service is still starting (licence check) - retry in a minute")
            time.sleep(1.0)

    def stop(self) -> bool:
        stopped = False
        if self.process and self.process.poll() is None:
            self.process.terminate()
            stopped = True
        elif self.pid and pid_alive(self.pid):
            subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"], capture_output=True, check=False)
            stopped = True
        self.process = None
        self.pid = None
        self.url = None
        self.client_id = None
        self.projects.clear()
        self.active_project_id = None
        try:
            self._state_path.unlink(missing_ok=True)
        except OSError:
            pass
        return stopped

    # -- REST ------------------------------------------------------------------------------
    def connect(self) -> str:
        url = self.ensure()
        if self.client_id is None:
            self.client_id = str(self.request("GET", "/clients/connect-client", auth=False))
        return url

    def request(self, method: str, path: str, body: Any = None, raw: bytes | None = None, content_type: str | None = None, auth: bool = True, decode: bool = True) -> Any:
        url = f"{self.ensure()}/api/1{path}"
        headers = {"Accept": "application/json"}
        if auth:
            if self.client_id is None:
                self.connect()
            headers["ClientId"] = self.client_id or ""
        data = None
        if raw is not None:
            data = raw
            headers["Content-Type"] = content_type or "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:  # noqa: S310
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            raise RestError(exc.code, exc.reason, exc.read().decode("utf-8", "replace") if exc.fp else "") from exc
        if not decode:
            return payload
        text = payload.decode("utf-8", "replace")
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def upload(self, path: str, field: str, filename: str, data: bytes) -> Any:
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        return self.request("POST", path, raw=body, content_type=f"multipart/form-data; boundary={boundary}")

    def resolve_project(self, project_id: str | None) -> str:
        if project_id:
            return project_id
        if self.active_project_id:
            return self.active_project_id
        raise IdeaError("no RCS project is open - call rcs_open_project first")


def settings_env_url() -> str | None:
    import os

    value = os.environ.get("IDEASTATICA_RCS_API_URL", "").strip()
    return value.rstrip("/") or None


def register(server, session) -> None:
    rcs = RcsSession(session.settings)
    session.rcs = rcs  # type: ignore[attr-defined]

    @server.tool(annotations=READ)
    def rcs_service_status() -> dict:
        """State of the IDEA StatiCa RCS (reinforced concrete sections) service used by the rcs_* tools."""
        with tool_errors("rcs_service_status"):
            reachable = bool(rcs.url) and is_reachable(rcs.url, 3.0)
            return {
                "url": rcs.url,
                "reachable": reachable,
                "pid": rcs.pid,
                "exe_found": bool(rcs.exe() and rcs.exe().is_file()),
                "active_project_id": rcs.active_project_id,
                "projects": rcs.projects,
            }

    @server.tool(annotations=WRITE)
    def rcs_open_project(file_path: str) -> dict:
        """Open an .ideaRcs project (or import an IOM .xml) in the RCS service; returns sections, members and reinforced cross-sections."""
        with tool_errors("rcs_open_project"):
            path = Path(file_path).expanduser()
            if not path.is_file():
                raise IdeaError(f"file not found: {path}")
            rcs.connect()
            if path.suffix.lower() == ".idearcs":
                project = rcs.upload("/projects/open", "rcsFile", path.name, path.read_bytes())
            elif path.suffix.lower() == ".xml":
                project = rcs.upload("/projects/import-iom-file", "iomFile", path.name, path.read_bytes())
            else:
                raise IdeaError("expected an .ideaRcs project or an IOM .xml file")
            if isinstance(project, str):  # /projects/open returns the project id only
                project = rcs.request("GET", "/projects/active-project")
            pid = str(project.get("projectId"))
            rcs.active_project_id = pid
            rcs.projects[pid] = {"source_path": str(path), "sections": project.get("sections")}
            return {"project_id": pid, "project": project}

    @server.tool(annotations=READ)
    def rcs_get_project(project_id: str | None = None) -> dict:
        """Project data of the open RCS project: sections, check members, reinforced cross-sections."""
        with tool_errors("rcs_get_project"):
            rcs.resolve_project(project_id)
            return {"project": rcs.request("GET", "/projects/active-project")}

    @server.tool(annotations=READ)
    def rcs_get_section_loading(section_id: int, project_id: str | None = None) -> dict:
        """Internal forces (loading) defined for a section of the RCS project."""
        with tool_errors("rcs_get_section_loading"):
            pid = rcs.resolve_project(project_id)
            return {"section_id": section_id, "loading": rcs.request("GET", f"/projects/{pid}/sections/{section_id}/internal-forces")}

    @server.tool(annotations=WRITE)
    def rcs_set_section_loading(section_id: int, loading: dict, project_id: str | None = None) -> dict:
        """Replace the internal forces of a section (pass the structure returned by rcs_get_section_loading with edited values, SI units: N, Nm)."""
        with tool_errors("rcs_set_section_loading"):
            pid = rcs.resolve_project(project_id)
            result = rcs.request("POST", f"/projects/{pid}/sections/{section_id}/internal-forces", body=loading)
            return {"section_id": section_id, "result": result}

    @server.tool(annotations=WRITE)
    def rcs_update_section(section: dict, project_id: str | None = None) -> dict:
        """Update a section (e.g. assign another reinforced cross-section: {"id": 1, "rcsId": 2})."""
        with tool_errors("rcs_update_section"):
            pid = rcs.resolve_project(project_id)
            return {"section": rcs.request("PUT", f"/projects/{pid}/sections", body=section)}

    @server.tool(annotations=WRITE)
    def rcs_calculate(section_ids: list[int] | None = None, project_id: str | None = None) -> dict:
        """Run the RCS checks for the given sections (default: all) and return the overall results per section."""
        with tool_errors("rcs_calculate"):
            pid = rcs.resolve_project(project_id)
            if not section_ids:
                project = rcs.request("GET", "/projects/active-project")
                section_ids = [int(s["id"]) for s in project.get("sections") or []]
            results = rcs.request("POST", f"/projects/{pid}/calculate", body={"sections": section_ids}) or []
            return {"project_id": pid, "sections": section_ids, "results": results}

    @server.tool(annotations=READ)
    def rcs_get_results(section_ids: list[int] | None = None, project_id: str | None = None, detailed: bool = False) -> dict:
        """Results of the last RCS calculation (overall per section; detailed=true returns the full result objects)."""
        with tool_errors("rcs_get_results"):
            pid = rcs.resolve_project(project_id)
            body = {"sections": section_ids} if section_ids else {}
            path = "get-raw-results" if detailed else "get-results"
            return {"project_id": pid, "results": rcs.request("POST", f"/projects/{pid}/{path}", body=body)}

    @server.tool(annotations=READ)
    def rcs_list_materials(project_id: str | None = None) -> dict:
        """Materials (concrete, reinforcement, prestress) used in the RCS project."""
        with tool_errors("rcs_list_materials"):
            pid = rcs.resolve_project(project_id)
            return {"materials": rcs.request("GET", f"/projects/{pid}/materials")}

    @server.tool(annotations=READ)
    def rcs_get_code_settings(project_id: str | None = None) -> dict:
        """Code setup values of the RCS project (JSON)."""
        with tool_errors("rcs_get_code_settings"):
            pid = rcs.resolve_project(project_id)
            return {"settings": rcs.request("GET", f"/projects/{pid}/code-settings-json")}

    @server.tool(annotations=WRITE)
    def rcs_save_project(file_path: str | None = None, project_id: str | None = None, overwrite: bool = False) -> dict:
        """Download the RCS project (with all API changes) and save it as .ideaRcs."""
        with tool_errors("rcs_save_project"):
            pid = rcs.resolve_project(project_id)
            data = rcs.request("GET", f"/projects/{pid}/download", decode=False)
            record = rcs.projects.get(pid) or {}
            stem = Path(record["source_path"]).stem if record.get("source_path") else f"rcs-{pid[:8]}"
            target = resolve_output_path(session.settings, file_path, sanitize_filename(stem) + ".ideaRcs", overwrite)
            target.write_bytes(data)
            return {"project_id": pid, "file_path": str(target), "size_bytes": len(data)}

    @server.tool(annotations=DESTRUCTIVE)
    def rcs_close_project(project_id: str | None = None) -> dict:
        """Close the RCS project in the service (unsaved changes are lost)."""
        with tool_errors("rcs_close_project"):
            pid = rcs.resolve_project(project_id)
            rcs.request("GET", f"/projects/{pid}/close")
            rcs.projects.pop(pid, None)
            if rcs.active_project_id == pid:
                rcs.active_project_id = None
            return {"closed_project_id": pid}

    @server.tool(annotations=DESTRUCTIVE)
    def rcs_service_stop() -> dict:
        """Stop the RCS service started by this MCP server."""
        with tool_errors("rcs_service_stop"):
            return {"stopped": rcs.stop()}
