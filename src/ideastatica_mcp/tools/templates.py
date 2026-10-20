"""Tools: connection templates and the Connection Library (propose / apply / publish)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from ideastatica_connection_api.models.con_connection_library_search_parameters import (
    ConConnectionLibrarySearchParameters,
)
from ideastatica_connection_api.models.con_design_set_type import ConDesignSetType
from ideastatica_connection_api.models.con_operation_common_properties import ConOperationCommonProperties
from ideastatica_connection_api.models.con_template_apply_param import ConTemplateApplyParam
from ideastatica_connection_api.models.con_template_mapping_get_param import ConTemplateMappingGetParam
from ideastatica_connection_api.models.con_template_publish_param import ConTemplatePublishParam
from ideastatica_connection_api.models.search_option import SearchOption
from ideastatica_connection_api.models.template_conversions import TemplateConversions

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import to_jsonable, truncate_text
from ideastatica_mcp.tools.common import (
    DESTRUCTIVE,
    READ,
    WRITE,
    enum_value,
    read_text_auto,
    resolve_output_path,
    sanitize_filename,
    write_xml_text,
)


def _template_text(template_file: str | None, template_xml: str | None) -> str:
    if template_xml and template_xml.strip():
        return template_xml
    if template_file:
        path = Path(template_file).expanduser()
        if not path.is_file():
            raise IdeaError(f"template file not found: {path}")
        return read_text_auto(path)
    raise IdeaError("pass template_file (a .contemp file) or template_xml (the template content)")


def _decode_library_template(payload: str) -> str:
    text = (payload or "").strip()
    if text.startswith("<"):
        return text
    try:
        return base64.b64decode(text).decode("utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        raise IdeaError(f"the library returned an unreadable template: {exc}") from exc


def _conversion_items(mapping: TemplateConversions) -> list[Any]:
    items = []
    for wrapper in mapping.conversions or []:
        inst = getattr(wrapper, "actual_instance", wrapper)
        if inst is not None:
            items.append(inst)
    return items


def _apply_remap(mapping: TemplateConversions, remap: dict[str, str] | None) -> list[dict]:
    applied: list[dict] = []
    if not remap:
        return applied
    for inst in _conversion_items(mapping):
        for key, new_value in remap.items():
            if key in {inst.original_value, inst.description, inst.original_template_id}:
                inst.new_value = str(new_value)
                applied.append({"description": inst.description, "original": inst.original_value, "new": inst.new_value})
    return applied


def _mapping_view(mapping: TemplateConversions) -> dict:
    return {"countryCode": mapping.country_code, "conversions": [to_jsonable(i) for i in _conversion_items(mapping)]}


def _search_option(value: str | None) -> SearchOption | None:
    if value is None:
        return None
    return enum_value(SearchOption, value, "search option")


def register(server, session) -> None:
    def _default_mapping(pid: str, cid: int, template: str, member_ids: list[int] | None) -> TemplateConversions:
        param = ConTemplateMappingGetParam(template=template, member_ids=[int(m) for m in member_ids] if member_ids else None)
        return session.invoke("template", "get_default_template_mapping", pid, cid, con_template_mapping_get_param=param)

    def _apply(pid: str, cid: int, template: str, mapping: TemplateConversions) -> dict:
        param = ConTemplateApplyParam(connection_template=template, mapping=mapping)
        result = session.invoke("template", "apply_template", pid, cid, con_template_apply_param=param)
        data = to_jsonable(result) or {}
        return {
            "applied_without_issues": data.get("appliedWithoutIssues"),
            "issues": data.get("issues") or [],
            "template_model": data.get("templateModel"),
        }

    def _resolve_template_id(pid: str, cid: int, template_id: str | int) -> str:
        text = str(template_id).strip()
        if text.isdigit():
            for item in session.invoke("template", "get_templates_in_connection", pid, cid) or []:
                if item.template_id is not None and int(item.template_id) == int(text) and item.library_template_id:
                    return str(item.library_template_id)
            raise IdeaError(f"no applied template with instance id {text}; see list_applied_templates")
        return text

    # ---------------------------------------------------------------- templates
    @server.tool(annotations=READ)
    def export_connection_template(
        connection_id: int | None = None,
        project_id: str | None = None,
        file_path: str | None = None,
        include_content: bool = False,
        overwrite: bool = False,
    ) -> dict:
        """Create a connection template (.contemp) from the current design of a connection and save it.

        The template captures operations, parameters and links, and can be applied to another
        connection with compatible topology (apply_template). Returns metadata (design code,
        typology, operation/parameter counts) and the saved file path; include_content=true also
        returns the (possibly truncated) template XML.
        """
        with tool_errors("export_connection_template"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            result = session.invoke("template", "create_template_from_connection", pid, cid)
            template = result.template
            if not template:
                template = session.invoke("template", "create_con_template", pid, cid)
            name = sanitize_filename(result.name or f"connection-{cid}") + ".contemp"
            target = resolve_output_path(session.settings, file_path, name, overwrite)
            size = write_xml_text(target, template)
            data = to_jsonable(result) or {}
            data.pop("template", None)
            out = {"connection_id": cid, "file_path": str(target), "size_bytes": size, "metadata": data}
            if include_content:
                shown, truncated = truncate_text(template, session.settings.max_text_chars)
                out["template_xml"] = shown
                out["truncated"] = truncated
            return out

    @server.tool(annotations=READ)
    def get_template_mapping(
        connection_id: int | None = None,
        project_id: str | None = None,
        template_file: str | None = None,
        template_xml: str | None = None,
        member_ids: list[int] | None = None,
    ) -> dict:
        """Get the default mapping of a template onto a connection (which member/material/cross-section maps to what).

        The conversions list is flat; material items carry a category in description (Steel,
        Concrete, Weld, Bolt Assembly, Bolt grade), member/cross-section items carry the template
        item name. Edit newValue entries and pass the whole mapping back to apply_template, or use
        its simpler `remap` argument.
        """
        with tool_errors("get_template_mapping"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            template = _template_text(template_file, template_xml)
            mapping = _default_mapping(pid, cid, template, member_ids)
            return {"connection_id": cid, "mapping": _mapping_view(mapping), "raw_mapping": to_jsonable(mapping)}

    @server.tool(annotations=WRITE)
    def apply_template(
        connection_id: int | None = None,
        project_id: str | None = None,
        template_file: str | None = None,
        template_xml: str | None = None,
        mapping: dict | None = None,
        remap: dict[str, str] | None = None,
        member_ids: list[int] | None = None,
    ) -> dict:
        """Apply a connection template (.contemp file or XML) to a connection - the main way to author geometry.

        Without mapping the default mapping is computed automatically. remap overrides mapping
        entries by original value, description or template id, e.g. {"S 355": "S 450",
        "Bolt Assembly": "M24 8.8"}. mapping accepts the raw_mapping object returned by
        get_template_mapping (edited newValue fields). Returns whether it applied without issues
        and the non-conformity issues otherwise. Recalculate afterwards.
        """
        with tool_errors("apply_template"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            template = _template_text(template_file, template_xml)
            if mapping:
                conversions = TemplateConversions.from_dict(mapping)
            else:
                conversions = _default_mapping(pid, cid, template, member_ids)
            applied = _apply_remap(conversions, remap)
            session.snapshot(pid, "apply_template")
            result = _apply(pid, cid, template, conversions)
            result.update({"connection_id": cid, "remap_applied": applied, "mapping_used": _mapping_view(conversions)})
            return result

    @server.tool(annotations=READ)
    def list_applied_templates(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """List templates applied to a connection: instance id, library template id (GUID), members, operations, parameter keys."""
        with tool_errors("list_applied_templates"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            items = session.invoke("template", "get_templates_in_connection", pid, cid) or []
            return {"connection_id": cid, "count": len(items), "templates": to_jsonable(items)}

    @server.tool(annotations=DESTRUCTIVE)
    def delete_template(
        template_id: str | None = None,
        connection_id: int | None = None,
        project_id: str | None = None,
        all_templates: bool = False,
    ) -> dict:
        """Delete an applied template including the operations it created (all_templates=true removes every template).

        template_id is the library template GUID from list_applied_templates (an instance id
        number is accepted too).
        """
        with tool_errors("delete_template"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            session.snapshot(pid, "delete_template")
            if all_templates:
                session.invoke("template", "delete_all", pid, cid)
                return {"connection_id": cid, "deleted": "all"}
            if not template_id:
                raise IdeaError("pass template_id or all_templates=true")
            tid = _resolve_template_id(pid, cid, template_id)
            session.invoke("template", "delete", pid, cid, tid)
            return {"connection_id": cid, "deleted": tid}

    @server.tool(annotations=WRITE)
    def explode_template(
        template_id: str | None = None,
        connection_id: int | None = None,
        project_id: str | None = None,
        all_templates: bool = False,
    ) -> dict:
        """Explode an applied template: keep its operations but drop the template's parameters and parametric links."""
        with tool_errors("explode_template"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            if all_templates:
                session.invoke("template", "explode_all", pid, cid)
                return {"connection_id": cid, "exploded": "all"}
            if not template_id:
                raise IdeaError("pass template_id or all_templates=true")
            tid = _resolve_template_id(pid, cid, template_id)
            session.invoke("template", "explode", pid, cid, tid)
            return {"connection_id": cid, "exploded": tid}

    @server.tool(annotations=WRITE)
    def load_template_defaults(template_id: str, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Reset the parameters of an applied template to the template's default expressions."""
        with tool_errors("load_template_defaults"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            tid = _resolve_template_id(pid, cid, template_id)
            response = session.invoke("template", "load_defaults", pid, cid, tid)
            return {"connection_id": cid, "template_id": tid, "response": to_jsonable(response)}

    @server.tool(annotations=READ)
    def get_template_common_properties(template_id: str, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Material ids (weld, plate, bolt assembly) used by the operations of one applied template."""
        with tool_errors("get_template_common_properties"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            tid = _resolve_template_id(pid, cid, template_id)
            props = session.invoke("template", "get_template_common_operation_properties", pid, cid, tid)
            return {"connection_id": cid, "template_id": tid, "common_properties": to_jsonable(props)}

    @server.tool(annotations=WRITE)
    def update_template_common_properties(
        template_id: str,
        connection_id: int | None = None,
        project_id: str | None = None,
        weld_material_id: int | None = None,
        plate_material_id: int | None = None,
        bolt_assembly_id: int | None = None,
    ) -> dict:
        """Set the weld material / plate material / bolt assembly ids for the operations of one applied template."""
        with tool_errors("update_template_common_properties"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            tid = _resolve_template_id(pid, cid, template_id)
            current = session.invoke("template", "get_template_common_operation_properties", pid, cid, tid) or ConOperationCommonProperties()
            if weld_material_id is not None:
                current.weld_material_id = int(weld_material_id)
            if plate_material_id is not None:
                current.plate_material_id = int(plate_material_id)
            if bolt_assembly_id is not None:
                current.bolt_assembly_id = int(bolt_assembly_id)
            session.invoke("template", "update_template_common_operation_properties", pid, cid, tid, con_operation_common_properties=current)
            props = session.invoke("template", "get_template_common_operation_properties", pid, cid, tid)
            return {"connection_id": cid, "template_id": tid, "common_properties": to_jsonable(props)}

    # ---------------------------------------------------------------- connection library
    @server.tool(annotations=READ)
    def list_library_design_sets() -> dict:
        """List the Connection Library design sets (predefined, company, personal) available to the signed-in user."""
        with tool_errors("list_library_design_sets"):
            sets = session.invoke("connection_library", "get_design_sets") or []
            return {"count": len(sets), "design_sets": to_jsonable(sets)}

    @server.tool(annotations=READ)
    def propose_library_designs(
        connection_id: int | None = None,
        project_id: str | None = None,
        in_predefined_set: bool | None = True,
        in_company_set: bool | None = None,
        in_personal_set: bool | None = None,
        has_bolts: str | None = None,
        has_welds: str | None = None,
        has_anchor: str | None = None,
        has_clip_angles: str | None = None,
        is_moment: str | None = None,
        is_shear: str | None = None,
        is_truss: str | None = None,
        is_parametric: str | None = None,
        member_ids: list[int] | None = None,
    ) -> dict:
        """Ask the Connection Library to propose design items that fit the topology of a connection.

        Filters has_*/is_* take 'ignore', 'must' or 'mustNot'. Returns design items with their
        design set id and design item id, to be used with apply_library_design or
        get_library_template.
        """
        with tool_errors("propose_library_designs"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            params = ConConnectionLibrarySearchParameters(
                members=[int(m) for m in member_ids] if member_ids else None,
                in_predefined_set=in_predefined_set,
                in_company_set=in_company_set,
                in_personal_set=in_personal_set,
                has_bolts=_search_option(has_bolts),
                has_welds=_search_option(has_welds),
                has_anchor=_search_option(has_anchor),
                has_clip_angles=_search_option(has_clip_angles),
                is_moment=_search_option(is_moment),
                is_shear=_search_option(is_shear),
                is_truss=_search_option(is_truss),
                is_parametric=_search_option(is_parametric),
            )
            items = session.invoke("connection_library", "propose", pid, cid, con_connection_library_search_parameters=params) or []
            sets = {}
            try:
                sets = {s.id: s.name for s in (session.invoke("connection_library", "get_design_sets") or [])}
            except Exception:  # noqa: BLE001 - names are a nicety
                pass
            proposals = []
            for item in items:
                data = to_jsonable(item) or {}
                data["designSetName"] = sets.get(data.get("conDesignSetId"))
                proposals.append(data)
            return {"connection_id": cid, "count": len(proposals), "proposals": proposals}

    @server.tool(annotations=READ)
    def get_library_template(
        design_set_id: str, design_item_id: str, file_path: str | None = None, include_content: bool = False, overwrite: bool = False
    ) -> dict:
        """Download a Connection Library design item as a template (decoded from base64) and save it as .contemp."""
        with tool_errors("get_library_template"):
            payload = session.invoke("connection_library", "get_template", design_set_id=design_set_id, design_item_id=design_item_id)
            template = _decode_library_template(payload)
            name = sanitize_filename(f"library-{design_item_id[:8]}") + ".contemp"
            target = resolve_output_path(session.settings, file_path, name, overwrite)
            size = write_xml_text(target, template)
            out = {"design_set_id": design_set_id, "design_item_id": design_item_id, "file_path": str(target), "size_bytes": size}
            if include_content:
                shown, truncated = truncate_text(template, session.settings.max_text_chars)
                out["template_xml"] = shown
                out["truncated"] = truncated
            return out

    @server.tool(annotations=WRITE)
    def apply_library_design(
        design_set_id: str,
        design_item_id: str,
        connection_id: int | None = None,
        project_id: str | None = None,
        remap: dict[str, str] | None = None,
        member_ids: list[int] | None = None,
    ) -> dict:
        """One step: fetch a Connection Library design item, compute the default mapping and apply it to a connection.

        remap optionally overrides mapping entries ({"Steel": "S 450"}). Recalculate afterwards.
        """
        with tool_errors("apply_library_design"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            payload = session.invoke("connection_library", "get_template", design_set_id=design_set_id, design_item_id=design_item_id)
            template = _decode_library_template(payload)
            conversions = _default_mapping(pid, cid, template, member_ids)
            applied = _apply_remap(conversions, remap)
            session.snapshot(pid, f"apply_library_design {design_item_id[:8]}")
            result = _apply(pid, cid, template, conversions)
            result.update({"connection_id": cid, "design_item_id": design_item_id, "remap_applied": applied, "mapping_used": _mapping_view(conversions)})
            return result

    @server.tool(annotations=READ)
    def save_library_design_picture(design_set_id: str, design_item_id: str, file_path: str | None = None, overwrite: bool = False) -> dict:
        """Save the preview picture (PNG) of a Connection Library design item to disk."""
        with tool_errors("save_library_design_picture"):
            response = session.invoke(
                "connection_library", "get_design_item_picture_with_http_info", design_set_id=design_set_id, design_item_id=design_item_id
            )
            data = response.raw_data
            if not data or not data.startswith(b"\x89PNG"):
                raise IdeaError("the library returned no PNG picture for this design item")
            name = sanitize_filename(f"library-{design_item_id[:8]}") + ".png"
            target = resolve_output_path(session.settings, file_path, name, overwrite)
            target.write_bytes(data)
            return {"file_path": str(target), "size_bytes": len(data)}

    @server.tool(annotations=WRITE)
    def publish_connection_to_library(
        name: str,
        connection_id: int | None = None,
        project_id: str | None = None,
        author: str | None = None,
        company_name: str | None = None,
        design_set_type: str = "private",
    ) -> dict:
        """Publish the current design of a connection to the Connection Library (private or company set)."""
        with tool_errors("publish_connection_to_library"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            param = ConTemplatePublishParam(
                name=name,
                author=author,
                company_name=company_name,
                design_set_type=enum_value(ConDesignSetType, design_set_type, "design_set_type"),
            )
            ok = session.invoke("connection_library", "publish_connection", pid, cid, con_template_publish_param=param)
            return {"connection_id": cid, "published": bool(ok), "name": name, "design_set_type": param.design_set_type.value}
