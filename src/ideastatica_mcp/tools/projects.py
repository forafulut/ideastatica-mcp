"""Tools: projects (open, create, inspect, save, close, IOM import)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ideastatica_connection_api.models.con_project_data import ConProjectData
from ideastatica_connection_api.models.country_code import CountryCode

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import compact_project, to_jsonable
from ideastatica_mcp.tools.common import (
    DESTRUCTIVE,
    READ,
    WRITE,
    normalize_design_code,
    resolve_output_path,
    sanitize_filename,
)

logger_warning = logging.getLogger(__name__).warning
IDEACON_EXTENSIONS = {".ideacon"}
IOM_EXTENSIONS = {".xml", ".iom"}


def _read_project_file(file_path: str) -> tuple[Path, bytes]:
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise IdeaError(f"file not found: {path}")
    return path, path.read_bytes()


def register(server, session) -> None:
    @server.tool(annotations=WRITE)
    def open_project(file_path: str, connections_to_create: list[int] | None = None) -> dict:
        """Open an IDEA StatiCa Connection project in the service and make it the active project.

        Accepts a .ideaCon project file, or an IDEA Open Model container XML (.xml / .iom) which
        is imported into a new project (connections_to_create optionally restricts which IOM
        connection points become connections). Returns the project id (a GUID needed by other
        tools when several projects are open), project info and the connections with their
        integer ids. The project stays open in the service memory until close_project.
        """
        with tool_errors("open_project"):
            path, data = _read_project_file(file_path)
            ext = path.suffix.lower()
            if ext in IDEACON_EXTENSIONS:
                project = session.invoke("project", "open_project", idea_con_file=data, _content_type="multipart/form-data")
            elif ext in IOM_EXTENSIONS:
                project = session.invoke(
                    "project",
                    "import_iom",
                    container_xml_file=data,
                    connections_to_create=connections_to_create or None,
                    _content_type="multipart/form-data",
                )
            else:
                raise IdeaError(f"unsupported file type '{ext}'; expected .ideaCon or an IOM container .xml/.iom")
            session.register_project(project, source_path=str(path))
            return {"source_path": str(path), "active": True, "project": compact_project(project)}

    @server.tool(annotations=WRITE)
    def create_project(
        design_code: str = "ecen",
        name: str | None = None,
        description: str | None = None,
        project_number: str | None = None,
        author: str | None = None,
    ) -> dict:
        """Create a new, empty project (no connections yet) and make it the active project.

        design_code selects the code: ecen (Eurocode, default), american (AISC), canada, australia,
        india, sia, rus, chn, hkg. Add connections with create_connection, members with add_member,
        or bring in a full model through open_project with an IOM file instead.
        """
        with tool_errors("create_project"):
            code = normalize_design_code(design_code)
            info = ConProjectData(
                name=name,
                description=description,
                project_number=project_number,
                author=author,
                country_code=CountryCode(code),
            )
            project = session.invoke("project", "create_empty_project", con_project_data=info)
            session.register_project(project)
            return {"active": True, "project": compact_project(project)}

    @server.tool(annotations=READ)
    def list_open_projects() -> dict:
        """List the projects currently open in the service for this session, with the active one.

        Other tools default to the active project when project_id is omitted.
        """
        with tool_errors("list_open_projects"):
            records = session.sync_projects()
            return {
                "active_project_id": session.active_project_id,
                "projects": [record.as_dict() for record in records],
            }

    @server.tool(annotations=READ)
    def get_project(project_id: str | None = None) -> dict:
        """Get project information (name, design code, author, ...) and its connections."""
        with tool_errors("get_project"):
            pid = session.resolve_project(project_id)
            project = session.invoke("project", "get_project_data", pid)
            return {"project": compact_project(project), "raw": to_jsonable(project)}

    @server.tool(annotations=WRITE)
    def update_project_info(
        project_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        project_number: str | None = None,
        author: str | None = None,
    ) -> dict:
        """Change project metadata (name, description, project number, author). Only given fields change."""
        with tool_errors("update_project_info"):
            pid = session.resolve_project(project_id)
            current = session.invoke("project", "get_project_data", pid)
            info = current.project_info or ConProjectData()
            if name is not None:
                info.name = name
            if description is not None:
                info.description = description
            if project_number is not None:
                info.project_number = project_number
            if author is not None:
                info.author = author
            project = session.invoke("project", "update_project_data", pid, con_project_data=info)
            if pid in session.projects and info.name:
                session.projects[pid].name = info.name
            return {"project": compact_project(project)}

    @server.tool(annotations=WRITE)
    def save_project(
        file_path: str | None = None, project_id: str | None = None, overwrite: bool = False, versioned: bool = False, keep_versions: int = 10
    ) -> dict:
        """Download the project from the service and save it as a .ideaCon file (includes all API changes).

        Without file_path the file goes to the MCP output folder, named after the source file or
        the project. Existing files are only replaced when overwrite=true. versioned=true writes
        name_v1, name_v2 ... instead (never overwrites) and keeps the last keep_versions files.
        The project stays open.
        """
        with tool_errors("save_project"):
            pid = session.resolve_project(project_id)
            record = session.projects.get(pid)
            stem = None
            if record and record.source_path:
                stem = Path(record.source_path).stem
            elif record and record.name:
                stem = record.name
            base = sanitize_filename(stem or f"project-{pid[:8]}")
            if versioned:
                folder = Path(file_path).expanduser() if file_path and Path(file_path).expanduser().is_dir() else session.settings.output_dir
                folder.mkdir(parents=True, exist_ok=True)
                existing = sorted(folder.glob(f"{base}_v*.ideaCon"), key=lambda p: int(p.stem.rsplit("_v", 1)[1]) if p.stem.rsplit("_v", 1)[1].isdigit() else 0)
                last = int(existing[-1].stem.rsplit("_v", 1)[1]) if existing and existing[-1].stem.rsplit("_v", 1)[1].isdigit() else 0
                target = folder / f"{base}_v{last + 1}.ideaCon"
                for old in existing[: max(0, len(existing) + 1 - max(1, int(keep_versions)))]:
                    old.unlink(missing_ok=True)
            else:
                target = resolve_output_path(session.settings, file_path, base + ".ideaCon", overwrite)
            response = session.invoke("project", "download_project_with_http_info", pid)
            data = response.raw_data
            if not data:
                raise IdeaError("the service returned an empty project file")
            target.write_bytes(data)
            return {"project_id": pid, "file_path": str(target), "size_bytes": len(data)}

    @server.tool(annotations=READ)
    def list_snapshots(project_id: str | None = None) -> dict:
        """Automatic snapshots taken before modifying tools ran (undo points for undo_last_change)."""
        with tool_errors("list_snapshots"):
            pid = session.resolve_project(project_id)
            stack = session.snapshots.get(pid, [])
            return {
                "project_id": pid,
                "count": len(stack),
                "snapshots": [{"index": i, "label": s["label"], "taken_at": time.strftime("%H:%M:%S", time.localtime(s["taken_at"])), "size_bytes": s["size"]} for i, s in enumerate(stack)],
            }

    @server.tool(annotations=DESTRUCTIVE)
    def undo_last_change(project_id: str | None = None, snapshot_index: int | None = None) -> dict:
        """Restore the project state captured before the last modifying tool (or a chosen snapshot).

        Modifying tools (update_parameters, apply_template, load effects, members, settings,
        conversion, optimisation sweeps) snapshot the project first. Restoring re-opens that
        snapshot as the active project with a new project id; the current state is closed.
        """
        with tool_errors("undo_last_change"):
            pid = session.resolve_project(project_id)
            stack = session.snapshots.get(pid, [])
            if not stack:
                raise IdeaError("no snapshot available for this project")
            index = len(stack) - 1 if snapshot_index is None else int(snapshot_index)
            if index < 0 or index >= len(stack):
                raise IdeaError(f"snapshot_index must be between 0 and {len(stack) - 1}")
            entry = stack[index]
            record = session.projects.get(pid)
            project = session.invoke("project", "open_project", idea_con_file=entry["data"], _content_type="multipart/form-data")
            new_record = session.register_project(project, source_path=record.source_path if record else None)
            remaining = stack[:index]
            try:
                session.invoke("project", "close_project", pid)
            except Exception as exc:  # noqa: BLE001
                logger_warning(f"closing replaced project {pid} failed: {exc}")
            session.projects.pop(pid, None)
            session.snapshots.pop(pid, None)
            session.invalidate_connections(pid)
            if remaining:
                session.snapshots[new_record.project_id] = remaining
            return {"restored_label": entry["label"], "old_project_id": pid, "project": compact_project(project), "remaining_snapshots": len(remaining)}

    @server.tool(annotations=DESTRUCTIVE)
    def close_project(project_id: str | None = None) -> dict:
        """Close a project in the service (unsaved changes are lost - save_project first if needed)."""
        with tool_errors("close_project"):
            pid = session.resolve_project(project_id)
            session.invoke("project", "close_project", pid)
            session.forget_project(pid)
            return {"closed_project_id": pid, "active_project_id": session.active_project_id}

    @server.tool(annotations=WRITE)
    def update_project_from_iom(file_path: str, project_id: str | None = None) -> dict:
        """Update an open project from a newer IDEA Open Model container XML (geometry, loads, results).

        Use it when the upstream model (FEA/CAD export) changed and the connection designs should
        be kept.
        """
        with tool_errors("update_project_from_iom"):
            pid = session.resolve_project(project_id)
            path, data = _read_project_file(file_path)
            if path.suffix.lower() not in IOM_EXTENSIONS:
                raise IdeaError("expected an IOM container .xml/.iom file")
            project = session.invoke(
                "project", "update_from_iom", pid, container_xml_file=data, _content_type="multipart/form-data"
            )
            session.invalidate_connections(pid)
            return {"project": compact_project(project)}
