"""Tools: reports (PDF / Word / HTML), exports (IFC / IOM / DWG) and 3D scene data."""

from __future__ import annotations

import json
from pathlib import Path

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import to_jsonable
from ideastatica_mcp.tools.common import READ, resolve_output_path, sanitize_filename

REPORT_FORMATS = {"pdf": "pdf", "word": "docx", "docx": "docx", "doc": "docx", "html": "zip", "htmlzip": "zip", "zip": "zip"}
EXPORT_FORMATS = {"ifc", "iom", "dwg", "iom_connection_data"}


def _project_stem(session, pid: str) -> str:
    record = session.projects.get(pid)
    if record and record.source_path:
        return Path(record.source_path).stem
    if record and record.name:
        return record.name
    return f"project-{pid[:8]}"


def _write(target: Path, data: bytes | str) -> int:
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not data:
        raise IdeaError("the service returned an empty file")
    target.write_bytes(data)
    return len(data)


def register(server, session) -> None:
    @server.tool(annotations=READ)
    def generate_report(
        format: str = "pdf",
        connection_ids: list[int] | None = None,
        project_id: str | None = None,
        file_path: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Generate the check report of one or more connections and save it to disk.

        format: pdf (default), word (docx) or html (a zip with the HTML report; single connection
        only). connection_ids defaults to all connections; several ids produce one document with a
        section per connection. Calculate first, otherwise the report has no results. Returns the
        saved file path.
        """
        with tool_errors("generate_report"):
            key = (format or "pdf").strip().lower()
            if key not in REPORT_FORMATS:
                raise IdeaError("format must be pdf, word or html")
            ext = REPORT_FORMATS[key]
            pid = session.resolve_project(project_id)
            if connection_ids:
                ids = [int(i) for i in connection_ids]
            else:
                ids = [int(c.id) for c in session.connections(pid)]
            if not ids:
                raise IdeaError("the project has no connections")
            stem = sanitize_filename(_project_stem(session, pid))
            suffix = f"con{ids[0]}" if len(ids) == 1 else "all-connections"
            if ext == "zip":
                if len(ids) != 1:
                    raise IdeaError("the html report supports exactly one connection id")
                response = session.invoke("report", "generate_html_zip_with_http_info", pid, ids[0])
                default_name = f"{stem}-{suffix}-report-html.zip"
            elif ext == "pdf":
                if len(ids) == 1:
                    response = session.invoke("report", "generate_pdf_with_http_info", pid, ids[0])
                else:
                    response = session.invoke("report", "generate_pdf_for_mutliple_with_http_info", pid, ids)
                default_name = f"{stem}-{suffix}-report.pdf"
            else:
                if len(ids) == 1:
                    response = session.invoke("report", "generate_word_with_http_info", pid, ids[0])
                else:
                    response = session.invoke("report", "generate_word_for_multiple_with_http_info", pid, ids)
                default_name = f"{stem}-{suffix}-report.docx"
            target = resolve_output_path(session.settings, file_path, default_name, overwrite)
            size = _write(target, response.raw_data)
            return {"project_id": pid, "connection_ids": ids, "format": ext, "file_path": str(target), "size_bytes": size}

    @server.tool(annotations=READ)
    def export_connection(
        format: str = "ifc",
        connection_id: int | None = None,
        project_id: str | None = None,
        file_path: str | None = None,
        iom_version: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Export one connection: ifc (IFC model), iom (IDEA Open Model XML incl. results), dwg (2D drawing) or iom_connection_data (connection geometry as JSON).

        The file is written to disk (output folder unless file_path is given). iom_version
        optionally selects an IOM schema version. For iom_connection_data a short inventory
        (beams, plates, bolt grids, welds, ...) is returned besides the file.
        """
        with tool_errors("export_connection"):
            key = (format or "ifc").strip().lower().replace("-", "_")
            if key not in EXPORT_FORMATS:
                raise IdeaError("format must be ifc, iom, dwg or iom_connection_data")
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            stem = sanitize_filename(f"{_project_stem(session, pid)}-con{cid}")
            inventory = None
            if key == "ifc":
                response = session.invoke("export", "export_ifc_with_http_info", pid, cid)
                data = response.raw_data
                default_name = f"{stem}.ifc"
            elif key == "dwg":
                response = session.invoke("export", "export_dwg_with_http_info", pid, cid)
                data = response.raw_data
                default_name = f"{stem}.dwg"
            elif key == "iom":
                data = session.invoke("export", "export_iom", pid, cid, version=iom_version)
                default_name = f"{stem}-iom.xml"
            else:
                model = session.invoke("export", "export_iom_connection_data", pid, cid)
                payload = to_jsonable(model) or {}
                data = json.dumps(payload, ensure_ascii=False, indent=2)
                default_name = f"{stem}-connection-data.json"
                inventory = {
                    name: len(payload.get(key_name) or [])
                    for name, key_name in (
                        ("beams", "beams"),
                        ("plates", "plates"),
                        ("folded_plates", "foldedPlates"),
                        ("bolt_grids", "boltGrids"),
                        ("anchor_grids", "anchorGrids"),
                        ("pin_grids", "pinGrids"),
                        ("welds", "welds"),
                        ("concrete_blocks", "concreteBlocks"),
                        ("cut_beam_by_beams", "cutBeamByBeams"),
                    )
                }
            target = resolve_output_path(session.settings, file_path, default_name, overwrite)
            size = _write(target, data)
            out = {"project_id": pid, "connection_id": cid, "format": key, "file_path": str(target), "size_bytes": size}
            if inventory is not None:
                out["inventory"] = inventory
            return out

    @server.tool(annotations=READ)
    def get_scene_3d(
        connection_id: int | None = None, project_id: str | None = None, file_path: str | None = None, overwrite: bool = False
    ) -> dict:
        """Save the 3D scene data (vertices, normals, groups) of a connection as JSON for a custom viewer and return its statistics."""
        with tool_errors("get_scene_3d"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            text = session.invoke("presentation", "get_data_scene3_d_text", pid, cid)
            if not text:
                raise IdeaError("the service returned no scene data")
            stats: dict = {}
            try:
                scene = json.loads(text)
                vertices = scene.get("vertices") or scene.get("Vertices") or []
                groups = scene.get("groups") or scene.get("Groups") or []
                stats = {"vertex_count": len(vertices) // 3, "group_count": len(groups)}
            except (json.JSONDecodeError, AttributeError):
                pass
            stem = sanitize_filename(f"{_project_stem(session, pid)}-con{cid}-scene3d")
            target = resolve_output_path(session.settings, file_path, f"{stem}.json", overwrite)
            size = _write(target, text)
            return {"project_id": pid, "connection_id": cid, "file_path": str(target), "size_bytes": size, **stats}
