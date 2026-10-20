"""The SDK client session: connection to the service and the registry of open projects."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from ideastatica_connection_api.connection_api_client import ConnectionApiClient
from ideastatica_connection_api.exceptions import UnauthorizedException

from ideastatica_mcp.config import API_BASE_PATH, Settings
from ideastatica_mcp.errors import IdeaError, RestError
from ideastatica_mcp.service import ServiceManager, ServiceStarting

logger = logging.getLogger(__name__)
CONNECTION_CACHE_TTL = 60.0  # seconds; mutations invalidate explicitly
SNAPSHOT_LIMIT = 5  # undo depth per project (each snapshot is a full .ideaCon in memory)

# Exceptions raised by urllib3 when the service is gone.
try:  # pragma: no cover - import guard
    from urllib3.exceptions import HTTPError as _Urllib3HTTPError
except ImportError:  # pragma: no cover
    _Urllib3HTTPError = OSError  # type: ignore[misc,assignment]

TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (_Urllib3HTTPError, ConnectionError)


@dataclass
class ProjectRecord:
    project_id: str
    name: str | None
    source_path: str | None
    opened_at: float
    design_code: str | None = None
    connection_count: int | None = None

    def as_dict(self) -> dict:
        data = asdict(self)
        data["opened_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.opened_at))
        return data


class IdeaSession:
    """Owns one ``ConnectionApiClient`` and remembers which projects were opened through it."""

    def __init__(self, settings: Settings, service: ServiceManager):
        self.settings = settings
        self.service = service
        self._client: ConnectionApiClient | None = None
        self._client_url: str | None = None
        self._lock = threading.RLock()
        self.projects: dict[str, ProjectRecord] = {}
        self.active_project_id: str | None = None
        self._connections: dict[str, tuple[float, list]] = {}
        self.snapshots: dict[str, list[dict]] = {}

    # -- connection --------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._client is not None

    @property
    def client_id(self) -> str | None:
        return self._client.client_id if self._client else None

    def client(self) -> ConnectionApiClient:
        """A connected SDK client (starts the service and registers the client when needed).

        Fast path: an existing client is returned without any network round-trip; a dead
        service shows up as a transport error in ``invoke`` which then reconnects.
        """
        client = self._client
        if client is not None:
            return client
        with self._lock:
            if self._client is not None:
                return self._client
            url = self.service.ensure_running()
            return self._connect(url)

    def prewarm(self) -> None:
        """Start the service and register the client in the background (called at server start)."""
        if self.settings.api_url is None and not self.settings.autostart:
            return
        try:
            self.service.start(wait_seconds=0) if self.settings.api_url is None else self.service.ensure_running(wait_seconds=0)
        except ServiceStarting:
            pass
        except Exception as exc:  # noqa: BLE001 - prewarm is best effort
            logger.info("Prewarm did not start the service: %s", exc)
            return
        deadline = time.monotonic() + self.settings.start_timeout
        url = self.service.base_url
        while url and time.monotonic() < deadline and self._client is None:
            if self.service.is_running():
                try:
                    self.client()
                except Exception as exc:  # noqa: BLE001
                    logger.info("Prewarm connect failed: %s", exc)
                return
            if not self.service.process_alive() and self.settings.api_url is None:
                return
            time.sleep(1.0)

    def _connect(self, url: str) -> ConnectionApiClient:
        try:  # SDK >= 26.0.6 accepts an application identity; 26.0.5 takes only the URL
            client = ConnectionApiClient(url, self.settings.client_app_name, self.settings.client_app_version)
        except TypeError:
            client = ConnectionApiClient(url)
        client.__enter__()  # registers the client (GET /clients/connect-client)
        self._client = client
        self._client_url = url
        logger.info("Connected to %s as client %s", url, client.client_id)
        return client

    def reconnect(self) -> ConnectionApiClient:
        """Drop the current client and register a new one; open projects are forgotten."""
        with self._lock:
            self._client = None
            self._client_url = None
            if self.projects:
                logger.warning("Forgetting %d open project(s): the service session was lost", len(self.projects))
            self.projects.clear()
            self._connections.clear()
            self.active_project_id = None
            url = self.service.ensure_running()
            return self._connect(url)

    def invoke(self, group: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call ``client.<group>.<method>(*args, **kwargs)`` with one automatic reconnect."""
        client = self.client()
        try:
            return getattr(getattr(client, group), method)(*args, **kwargs)
        except UnauthorizedException:
            logger.warning("Client id rejected by the service; reconnecting")
            client = self.reconnect()
            return getattr(getattr(client, group), method)(*args, **kwargs)
        except TRANSPORT_ERRORS as exc:
            if self.service.is_running():
                raise
            logger.warning("Service unreachable (%s); restarting and reconnecting", exc)
            client = self.reconnect()
            return getattr(getattr(client, group), method)(*args, **kwargs)

    # -- raw REST (endpoints the generated SDK does not expose yet) ----------------------

    def rest(self, method: str, path: str, body: Any | None = None, timeout: float = 600.0) -> Any:
        """Call ``{base_url}/api/4{path}`` with this session's ClientId; returns decoded JSON.

        Used for the operation endpoints (welds, bolt grids, cuts, ...) that exist in the 26.0
        service but are not part of the published OpenAPI document / SDK yet.
        """
        client = self.client()
        url = f"{self._client_url}{API_BASE_PATH}{path}"
        headers = {"Accept": "application/json", "ClientId": client.client_id or ""}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local service
                status, text = response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace") if exc.fp else ""
            if exc.code == 401:
                self.reconnect()
                raise IdeaError("the service rejected the client id (service restarted?) - re-open the project and retry") from exc
            raise RestError(exc.code, exc.reason, text) from exc
        except urllib.error.URLError as exc:
            if not self.service.is_running():
                self.reconnect()
            raise IdeaError(f"cannot reach the service: {exc.reason}") from exc
        if status == 204 or not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # -- snapshots (undo) ---------------------------------------------------------------

    def snapshot(self, project_id: str, label: str) -> dict | None:
        """Download the project bytes before a modification so undo_last_change can restore them."""
        try:
            response = self.invoke("project", "download_project_with_http_info", project_id)
            data = response.raw_data
        except Exception as exc:  # noqa: BLE001 - never block the edit because the snapshot failed
            logger.warning("snapshot before %s failed: %s", label, exc)
            return None
        if not data:
            return None
        entry = {"label": label, "taken_at": time.time(), "size": len(data), "data": data}
        stack = self.snapshots.setdefault(project_id, [])
        stack.append(entry)
        del stack[:-SNAPSHOT_LIMIT]
        return {"label": label, "size_bytes": len(data), "snapshots": len(stack)}

    # -- connection cache (saves a round-trip when connection_id is omitted) ---------------

    def connections(self, project_id: str, fresh: bool = False) -> list:
        entry = self._connections.get(project_id)
        if not fresh and entry and time.monotonic() - entry[0] < CONNECTION_CACHE_TTL:
            return entry[1]
        items = self.invoke("connection", "get_connections", project_id) or []
        self._connections[project_id] = (time.monotonic(), items)
        return items

    def invalidate_connections(self, project_id: str | None = None) -> None:
        if project_id is None:
            self._connections.clear()
        else:
            self._connections.pop(project_id, None)

    # -- project registry --------------------------------------------------------------

    def register_project(self, project: Any, source_path: str | None = None) -> ProjectRecord:
        project_id = str(project.project_id)
        info = getattr(project, "project_info", None)
        record = ProjectRecord(
            project_id=project_id,
            name=getattr(info, "name", None) if info else None,
            source_path=source_path,
            opened_at=time.time(),
            design_code=str(getattr(info, "country_code", None).value) if info and getattr(info, "country_code", None) else None,
            connection_count=len(getattr(project, "connections", None) or []),
        )
        self.projects[project_id] = record
        self.active_project_id = project_id
        return record

    def forget_project(self, project_id: str) -> None:
        self.projects.pop(project_id, None)
        self._connections.pop(project_id, None)
        self.snapshots.pop(project_id, None)
        if self.active_project_id == project_id:
            self.active_project_id = next(iter(self.projects), None)

    def sync_projects(self) -> list[ProjectRecord]:
        """Reconcile the registry with the projects the service reports for this client."""
        projects = self.invoke("project", "get_active_projects") or []
        seen: set[str] = set()
        for project in projects:
            pid = str(project.project_id)
            seen.add(pid)
            if pid not in self.projects:
                self.register_project(project)
                self.active_project_id = self.active_project_id or pid
            else:
                record = self.projects[pid]
                record.connection_count = len(getattr(project, "connections", None) or [])
                info = getattr(project, "project_info", None)
                if info and getattr(info, "name", None):
                    record.name = info.name
        for pid in list(self.projects):
            if pid not in seen:
                self.forget_project(pid)
        if self.active_project_id not in self.projects:
            self.active_project_id = next(iter(self.projects), None)
        return list(self.projects.values())

    def resolve_project(self, project_id: str | None) -> str:
        if project_id:
            project_id = project_id.strip()
            if project_id in self.projects:
                return project_id
            matches = [pid for pid in self.projects if pid.lower().startswith(project_id.lower())]
            if len(matches) == 1:
                return matches[0]
            # Unknown to the registry: let the service decide (it may have been opened elsewhere).
            return project_id
        if self.active_project_id:
            return self.active_project_id
        raise IdeaError("no project is open - call open_project(file_path) first, or pass project_id")

    def resolve_connection(self, project_id: str, connection_id: int | None) -> int:
        if connection_id is not None:
            return int(connection_id)
        connections = self.connections(project_id)
        if len(connections) == 1:
            return int(connections[0].id)
        if not connections:
            raise IdeaError("the project has no connections")
        listing = ", ".join(f"{c.id}: {c.name}" for c in connections)
        raise IdeaError(f"the project has {len(connections)} connections, pass connection_id (available: {listing})")

    # -- shutdown ----------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            client = self._client
            if client is not None:
                for pid in list(self.projects):
                    try:
                        client.project.close_project(pid)
                    except Exception as exc:  # noqa: BLE001 - best effort during shutdown
                        logger.debug("Closing project %s failed: %s", pid, exc)
                self.projects.clear()
                self.active_project_id = None
                self._client = None
                self._client_url = None
            if self.settings.kill_on_exit:
                self.service.stop()
            else:
                # Leave the service running: a restarted MCP server re-adopts it (state file) and the
                # slow licence start-up is not paid again. service_stop ends it explicitly.
                logger.info("Leaving the service running for re-adoption (IDEASTATICA_KILL_ON_EXIT=false)")
