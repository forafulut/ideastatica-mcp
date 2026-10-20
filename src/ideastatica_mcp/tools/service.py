"""Tools: service lifecycle and diagnostics."""

from __future__ import annotations

import re
from importlib import metadata

from ideastatica_mcp.config import detect_setup_dirs
from ideastatica_mcp.errors import tool_errors
from ideastatica_mcp.service import get_service_version
from ideastatica_mcp.tools.common import DESTRUCTIVE, READ, WRITE


def sdk_version() -> str | None:
    try:
        return metadata.version("ideastatica-connection-api")
    except metadata.PackageNotFoundError:  # pragma: no cover
        return None


def same_major_minor(a: str | None, b: str | None) -> bool | None:
    if not a or not b:
        return None

    def key(text: str) -> tuple[int, ...]:
        return tuple(int(p) for p in re.findall(r"\d+", text)[:2])

    return key(a) == key(b)


def register(server, session) -> None:
    @server.tool(annotations=READ)
    def service_status() -> dict:
        """Report the state of the IDEA StatiCa Connection REST API service and this MCP session.

        Returns whether the service is reachable, its URL and version, the installed Python
        SDK version (must match the service's major.minor), the connected client id, the
        projects opened in this session, detected IDEA StatiCa installations and the active
        configuration. Call this first when a call fails or before starting work.
        """
        with tool_errors("service_status"):
            status = session.service.status()
            sdk = sdk_version()
            status["sdk_version"] = sdk
            status["sdk_matches_service"] = same_major_minor(status.get("service_version"), sdk)
            status["client_connected"] = session.connected
            status["client_id"] = session.client_id
            status["active_project_id"] = session.active_project_id
            status["open_projects"] = [record.as_dict() for record in session.projects.values()]
            status["available_installations"] = [str(p) for p in detect_setup_dirs()]
            status["settings"] = session.settings.as_dict()
            return status

    @server.tool(annotations=WRITE)
    def service_start(port: int | None = None, wait_seconds: float | None = None) -> dict:
        """Start IdeaStatiCa.ConnectionRestApi.exe (or attach to an already running service) and connect.

        Normally not needed: every other tool starts the service on demand. Use it to warm the
        service up, to pick a specific port, or to retry after a failed start. Starting reserves a
        seat on the IDEA StatiCa licence. The call waits at most wait_seconds (default 40) - the
        licence check often takes longer; if the result says the service is still starting, it
        keeps starting in the background: call service_status (or any tool) again in a minute.
        """
        with tool_errors("service_start"):
            if port and not session.settings.api_url:
                url = session.service.start(port=port, wait_seconds=wait_seconds)
            else:
                url = session.service.ensure_running(wait_seconds=wait_seconds)
            client = session.client()
            return {
                "url": url,
                "service_version": get_service_version(url),
                "sdk_version": sdk_version(),
                "client_id": client.client_id,
                "mode": session.service.mode,
            }

    @server.tool(annotations=DESTRUCTIVE)
    def service_stop() -> dict:
        """Close all projects opened here and stop the service process started by this MCP server.

        Projects opened through the API live in the service's memory, so unsaved changes are lost
        (call save_project first). A service started outside of this MCP server (attach mode) is
        left running; only the client session is dropped.
        """
        with tool_errors("service_stop"):
            closed = list(session.projects)
            session.close()
            stopped = session.service.stop()
            return {"stopped": stopped, "closed_projects": closed}
