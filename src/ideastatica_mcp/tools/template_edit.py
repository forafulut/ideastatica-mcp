"""Tools: inspect/edit connection template XML and keep a personal template library.

Per-operation endpoints are not enabled in the 26.0 service, so geometry details (weld sizes,
bolt assemblies, plate thicknesses) are changed by exporting the connection as a template,
editing values in the XML and applying it back.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.tools.common import DESTRUCTIVE, READ, WRITE, read_text_auto, sanitize_filename, write_xml_text


def _library_dir(settings) -> Path:
    path = settings.output_dir / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse(text: str) -> ET.Element:
    body = re.sub(r"^<\?xml[^>]*\?>", "", text.strip(), count=1)
    return ET.fromstring(body)


def _serialize(root: ET.Element, original: str) -> str:
    declaration = re.match(r"^<\?xml[^>]*\?>", original.strip())
    header = declaration.group(0) if declaration else '<?xml version="1.0" encoding="utf-16"?>'
    return header + "\n" + ET.tostring(root, encoding="unicode")


def _path_of(element_path: list[ET.Element]) -> str:
    return "/".join(e.tag for e in element_path)


def _walk(root: ET.Element):
    stack = [(root, [root])]
    while stack:
        element, path = stack.pop()
        yield element, path
        for child in reversed(list(element)):
            stack.append((child, path + [child]))


def register(server, session) -> None:
    @server.tool(annotations=READ)
    def find_template_values(template_file: str, query: str, max_results: int = 60) -> dict:
        """Search a .contemp template XML for elements whose tag or text matches query (regex, case-insensitive) and show their paths, values and parent context.

        Typical queries: "Weld", "Thickness", "BoltAssembly", "Diameter", "Material". The
        returned paths are what edit_template_xml needs.
        """
        with tool_errors("find_template_values"):
            path = Path(template_file).expanduser()
            if not path.is_file():
                raise IdeaError(f"template not found: {path}")
            root = _parse(read_text_auto(path))
            pattern = re.compile(query, re.IGNORECASE)
            hits = []
            for element, chain in _walk(root):
                text = (element.text or "").strip()
                if pattern.search(element.tag) or (text and pattern.search(text)):
                    parent = chain[-2] if len(chain) > 1 else None
                    hits.append(
                        {
                            "path": _path_of(chain),
                            "tag": element.tag,
                            "value": text if len(text) < 200 else text[:200] + "...",
                            "parent": parent.tag if parent is not None else None,
                            "parent_name": next((c.text for c in parent if c.tag in ("Name", "name")), None) if parent is not None else None,
                        }
                    )
                if len(hits) >= max_results:
                    break
            return {"template_file": str(path), "count": len(hits), "matches": hits}

    @server.tool(annotations=WRITE)
    def edit_template_xml(template_file: str, edits: list[dict], output_file: str | None = None) -> dict:
        """Change element values in a template XML and write a new file (the original is kept).

        edits: [{"path": "ConnectionTemplate/Operations/.../Thickness", "value": "0.02"},
        {"tag": "Thickness", "parent_name": "EP1", "value": "0.02"}, {"xpath": ".//Weld/Size", "value": "0.006", "all": true}].
        A path from find_template_values targets one element; tag+parent_name targets the
        element under the operation with that Name; xpath uses ElementTree syntax (all=true
        edits every match). Apply the result with apply_template(template_file=output).
        """
        with tool_errors("edit_template_xml"):
            path = Path(template_file).expanduser()
            if not path.is_file():
                raise IdeaError(f"template not found: {path}")
            original = read_text_auto(path)
            root = _parse(original)
            changed = []
            for edit in edits or []:
                value = edit.get("value")
                if value is None:
                    raise IdeaError("each edit needs a value")
                targets: list[ET.Element] = []
                if edit.get("path"):
                    parts = edit["path"].split("/")
                    for element, chain in _walk(root):
                        if [e.tag for e in chain] == parts:
                            targets.append(element)
                            break
                elif edit.get("xpath"):
                    found = root.findall(edit["xpath"])
                    targets = found if edit.get("all") else found[:1]
                elif edit.get("tag"):
                    for element, chain in _walk(root):
                        if element.tag != edit["tag"]:
                            continue
                        if edit.get("parent_name"):
                            parent = chain[-2] if len(chain) > 1 else None
                            name = next((c.text for c in parent if c.tag in ("Name", "name")), None) if parent is not None else None
                            if name != edit["parent_name"]:
                                continue
                        targets.append(element)
                        if not edit.get("all"):
                            break
                else:
                    raise IdeaError("each edit needs path, xpath or tag")
                if not targets:
                    raise IdeaError(f"no element matched {edit}")
                for target in targets:
                    changed.append({"path": target.tag, "from": (target.text or "").strip(), "to": str(value)})
                    target.text = str(value)
            out = Path(output_file).expanduser() if output_file else path.with_name(f"{path.stem}-edited{path.suffix}")
            if not out.is_absolute():
                out = session.settings.output_dir / out
            out.parent.mkdir(parents=True, exist_ok=True)
            write_xml_text(out, _serialize(root, original))
            return {"output_file": str(out), "changes": changed}

    @server.tool(annotations=WRITE)
    def save_template_to_library(name: str, connection_id: int | None = None, project_id: str | None = None, description: str = "", tags: list[str] | None = None) -> dict:
        """Export the current connection design as a template and store it in the personal library (output folder / templates) under a name, with metadata."""
        with tool_errors("save_template_to_library"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            result = session.invoke("template", "create_template_from_connection", pid, cid)
            template = result.template or session.invoke("template", "create_con_template", pid, cid)
            safe = sanitize_filename(name)
            folder = _library_dir(session.settings)
            file = folder / f"{safe}.contemp"
            write_xml_text(file, template)
            meta = {
                "name": name,
                "file": str(file),
                "description": description,
                "tags": tags or [],
                "design_code": result.design_code,
                "typology_code": result.typology_code,
                "operations": result.operation_count,
                "parameters": result.parameter_count,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source_project": session.projects.get(pid).source_path if pid in session.projects else None,
            }
            (folder / f"{safe}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            return meta

    @server.tool(annotations=READ)
    def list_saved_templates(query: str | None = None) -> dict:
        """List the templates saved with save_template_to_library (name, typology, tags, file)."""
        with tool_errors("list_saved_templates"):
            folder = _library_dir(session.settings)
            items = []
            for meta_file in sorted(folder.glob("*.json")):
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                if query and query.lower() not in json.dumps(meta, ensure_ascii=False).lower():
                    continue
                items.append(meta)
            return {"folder": str(folder), "count": len(items), "templates": items}

    @server.tool(annotations=WRITE)
    def apply_saved_template(name: str, connection_id: int | None = None, project_id: str | None = None, remap: dict[str, str] | None = None) -> dict:
        """Apply a template from the personal library to a connection (default mapping, optional remap of materials/sections)."""
        with tool_errors("apply_saved_template"):
            folder = _library_dir(session.settings)
            file = folder / f"{sanitize_filename(name)}.contemp"
            if not file.is_file():
                raise IdeaError(f"no saved template named '{name}' (see list_saved_templates)")
            from ideastatica_mcp.tools.templates import _apply_remap, _mapping_view  # reuse helpers
            from ideastatica_connection_api.models.con_template_apply_param import ConTemplateApplyParam
            from ideastatica_connection_api.models.con_template_mapping_get_param import ConTemplateMappingGetParam

            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            template = read_text_auto(file)
            mapping = session.invoke("template", "get_default_template_mapping", pid, cid, con_template_mapping_get_param=ConTemplateMappingGetParam(template=template))
            applied = _apply_remap(mapping, remap)
            result = session.invoke("template", "apply_template", pid, cid, con_template_apply_param=ConTemplateApplyParam(connection_template=template, mapping=mapping))
            data = result.to_dict() if hasattr(result, "to_dict") else {}
            return {"connection_id": cid, "template": name, "applied_without_issues": data.get("appliedWithoutIssues"), "issues": data.get("issues") or [], "remap_applied": applied, "mapping_used": _mapping_view(mapping)}

    @server.tool(annotations=DESTRUCTIVE)
    def delete_saved_template(name: str) -> dict:
        """Remove a template from the personal library."""
        with tool_errors("delete_saved_template"):
            folder = _library_dir(session.settings)
            safe = sanitize_filename(name)
            removed = []
            for ext in (".contemp", ".json"):
                file = folder / f"{safe}{ext}"
                if file.exists():
                    file.unlink()
                    removed.append(str(file))
            if not removed:
                raise IdeaError(f"no saved template named '{name}'")
            return {"removed": removed}
