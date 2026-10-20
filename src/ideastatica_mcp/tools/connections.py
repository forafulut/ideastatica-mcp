"""Tools: connections, members and manufacturing operations."""

from __future__ import annotations

import json
from typing import Any

from ideastatica_connection_api.models.con_analysis_type_enum import ConAnalysisTypeEnum
from ideastatica_connection_api.models.con_member import ConMember
from ideastatica_connection_api.models.con_operation_common_properties import ConOperationCommonProperties
from ideastatica_connection_api.models.con_weld_sizing_method_enum import ConWeldSizingMethodEnum

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import compact_connection, to_jsonable
from ideastatica_mcp.tools.common import DESTRUCTIVE, READ, WRITE, enum_value, merge_into_model, normalize_keys


def compact_member(member: Any) -> dict:
    data = to_jsonable(member) or {}
    position = data.get("position") or {}
    model = data.get("model") or {}
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "active": data.get("active"),
        "is_bearing": data.get("isBearing"),
        "is_continuous": data.get("isContinuous"),
        "cross_section_id": data.get("crossSectionId"),
        "connected_by": data.get("connectedBy"),
        "mirror_y": data.get("mirrorY"),
        "mirror_z": data.get("mirrorZ"),
        "position": {
            "defined_by": position.get("definedBy"),
            "beta_direction": position.get("betaDirection"),
            "gama_pitch": position.get("gamaPitch"),
            "alpha_rotation": position.get("alphaRotation"),
            "offset_ex": position.get("offsetEx"),
            "offset_ey": position.get("offsetEy"),
            "offset_ez": position.get("offsetEz"),
            "align": position.get("align"),
        },
        "model": {
            "model_type": model.get("modelTypeEnum"),
            "forces_in": model.get("forcesIn"),
            "x": model.get("x"),
            "connected_member_id": model.get("connectedMemberId"),
        },
        "stiffness_analysis": data.get("stiffnessAnalysis"),
    }


def _parse_json_text(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def register(server, session) -> None:
    # ---------------------------------------------------------------- connections
    @server.tool(annotations=READ)
    def list_connections(project_id: str | None = None) -> dict:
        """List the connections (design items) of a project with their integer ids, names and state."""
        with tool_errors("list_connections"):
            pid = session.resolve_project(project_id)
            connections = session.connections(pid, fresh=True)
            return {"project_id": pid, "count": len(connections), "connections": [compact_connection(c) for c in connections]}

    @server.tool(annotations=READ)
    def get_connection(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Get one connection (name, description, analysis type, calculated flag, buckling flag).

        connection_id may be omitted when the project has exactly one connection.
        """
        with tool_errors("get_connection"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            connection = session.invoke("connection", "get_connection", pid, cid)
            return {"project_id": pid, "connection": compact_connection(connection)}

    @server.tool(annotations=WRITE)
    def update_connection(
        connection_id: int | None = None,
        project_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        analysis_type: str | None = None,
        include_buckling: bool | None = None,
    ) -> dict:
        """Change a connection's name, description, analysis type or buckling flag (only given fields).

        analysis_type: stress_Strain, stiffness, capacity_Design, fatigues, total_Design,
        horizontalTying, capacityLoadLevels, fireRestance, buckling.
        """
        with tool_errors("update_connection"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            connection = session.invoke("connection", "get_connection", pid, cid)
            if name is not None:
                connection.name = name
            if description is not None:
                connection.description = description
            if analysis_type is not None:
                connection.analysis_type = enum_value(ConAnalysisTypeEnum, analysis_type, "analysis_type")
            if include_buckling is not None:
                connection.include_buckling = include_buckling
            updated = session.invoke("connection", "update_connection", pid, cid, con_connection=connection)
            session.invalidate_connections(pid)
            return {"connection": compact_connection(updated)}

    @server.tool(annotations=WRITE)
    def create_connection(name: str | None = None, project_id: str | None = None) -> dict:
        """Create a new empty connection (no members, no operations) in the project."""
        with tool_errors("create_connection"):
            pid = session.resolve_project(project_id)
            connection = session.invoke("connection", "create_empty_connection", pid, name=name)
            session.invalidate_connections(pid)
            return {"connection": compact_connection(connection)}

    @server.tool(annotations=WRITE)
    def copy_connection(connection_id: int, name: str | None = None, project_id: str | None = None) -> dict:
        """Duplicate a connection (members, operations, loads) under a new id, optionally renamed."""
        with tool_errors("copy_connection"):
            pid = session.resolve_project(project_id)
            connection = session.invoke("connection", "copy_connection", pid, int(connection_id), name=name)
            session.invalidate_connections(pid)
            return {"connection": compact_connection(connection)}

    @server.tool(annotations=DESTRUCTIVE)
    def delete_connection(connection_id: int, project_id: str | None = None) -> dict:
        """Delete a connection from the project. Returns the remaining connections."""
        with tool_errors("delete_connection"):
            pid = session.resolve_project(project_id)
            remaining = session.invoke("connection", "delete_connection", pid, int(connection_id)) or []
            session.invalidate_connections(pid)
            return {"deleted_connection_id": int(connection_id), "remaining": [compact_connection(c) for c in remaining]}

    @server.tool(annotations=READ)
    def get_connection_topology(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Get the topology description of a connection (member layout / typology code as JSON)."""
        with tool_errors("get_connection_topology"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            text = session.invoke("connection", "get_connection_topology", pid, cid)
            return {"connection_id": cid, "topology": _parse_json_text(text)}

    @server.tool(annotations=READ)
    def get_production_cost(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Estimated production cost of a connection (as computed by IDEA StatiCa's cost estimation)."""
        with tool_errors("get_production_cost"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            cost = session.invoke("connection", "get_production_cost", pid, cid)
            return {"connection_id": cid, "cost": to_jsonable(cost)}

    # ---------------------------------------------------------------- members
    @server.tool(annotations=READ)
    def list_members(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """List the members (beams/columns) of a connection: ids, names, bearing member, cross-section ids, placement."""
        with tool_errors("list_members"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            members = session.invoke("member", "get_members", pid, cid) or []
            return {"connection_id": cid, "count": len(members), "members": [compact_member(m) for m in members]}

    @server.tool(annotations=READ)
    def get_member(member_id: int, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Get the full definition of one member (position, model settings, stiffness analysis lengths)."""
        with tool_errors("get_member"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            member = session.invoke("member", "get_member", pid, cid, int(member_id))
            return {"connection_id": cid, "member": to_jsonable(member)}

    @server.tool(annotations=WRITE)
    def add_member(member: dict, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Add a member to a connection from a ConMember definition (JSON, snake_case or camelCase keys).

        Typical fields: name, crossSectionId (from list_project_materials cross_sections), isBearing,
        isContinuous, connectedBy (begin|end), mirrorY, mirrorZ, position {definedBy, betaDirection,
        gamaPitch, alphaRotation (radians), offsetEx/Ey/Ez (m), align}, model {modelTypeEnum,
        forcesIn, x, connectedMemberId}. Read an existing member with get_member for a full example.
        """
        with tool_errors("add_member"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            model = ConMember.from_dict(normalize_keys(ConMember, member or {}))
            created = session.invoke("member", "add_member", pid, cid, con_member=model)
            return {"connection_id": cid, "member": to_jsonable(created)}

    @server.tool(annotations=WRITE)
    def update_member(member_id: int, changes: dict, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Patch a member: pass only the fields to change (snake_case or camelCase, nested dicts allowed).

        Examples: {"is_continuous": true}, {"cross_section_id": 3}, {"position": {"gama_pitch": 0.1}},
        {"model": {"forces_in": "bolts"}}, {"stiffness_analysis": {"theoretical_length_y": 6.0}}.
        Angles are in radians and lengths in metres, as everywhere in the API.
        """
        with tool_errors("update_member"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            current = session.invoke("member", "get_member", pid, cid, int(member_id))
            patched = merge_into_model(ConMember, current, changes or {})
            patched.id = int(member_id)
            session.snapshot(pid, f"update_member {member_id}")
            updated = session.invoke("member", "update_member", pid, cid, con_member=patched)
            return {"connection_id": cid, "member": to_jsonable(updated)}

    @server.tool(annotations=WRITE)
    def set_bearing_member(member_id: int, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Make the given member the bearing member (the one the other members connect to)."""
        with tool_errors("set_bearing_member"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            member = session.invoke("member", "set_bearing_member", pid, cid, int(member_id))
            return {"connection_id": cid, "member": compact_member(member)}

    @server.tool(annotations=DESTRUCTIVE)
    def delete_member(member_id: int, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Remove a member from a connection."""
        with tool_errors("delete_member"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            result = session.invoke("member", "delete_member", pid, cid, int(member_id))
            return {"connection_id": cid, "deleted_member_id": int(member_id), "result": to_jsonable(result)}

    # ---------------------------------------------------------------- operations
    @server.tool(annotations=READ)
    def list_operations(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """List the manufacturing operations (plates, bolt grids, welds, cuts, stiffeners, ...) of a connection.

        The public API is read-only for individual operations: geometry is authored by applying
        templates (apply_template / apply_library_design), importing IOM, or using parameters.
        """
        with tool_errors("list_operations"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            operations = session.invoke("operation", "get_operations", pid, cid) or []
            return {"connection_id": cid, "count": len(operations), "operations": to_jsonable(operations)}

    @server.tool(annotations=DESTRUCTIVE)
    def delete_all_operations(connection_id: int, project_id: str | None = None) -> dict:
        """Delete ALL manufacturing operations of a connection (leaves the bare members)."""
        with tool_errors("delete_all_operations"):
            pid = session.resolve_project(project_id)
            session.snapshot(pid, f"delete_all_operations {connection_id}")
            session.invoke("operation", "delete_operations", pid, int(connection_id))
            remaining = session.invoke("operation", "get_operations", pid, int(connection_id)) or []
            return {"connection_id": int(connection_id), "remaining_operations": len(remaining)}

    @server.tool(annotations=READ)
    def get_common_operation_properties(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Get the material ids shared by all operations: weld material, plate material, bolt assembly."""
        with tool_errors("get_common_operation_properties"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            props = session.invoke("operation", "get_common_operation_properties", pid, cid)
            return {"connection_id": cid, "common_properties": to_jsonable(props)}

    @server.tool(annotations=WRITE)
    def update_common_operation_properties(
        connection_id: int | None = None,
        project_id: str | None = None,
        weld_material_id: int | None = None,
        plate_material_id: int | None = None,
        bolt_assembly_id: int | None = None,
    ) -> dict:
        """Set the weld material / plate material / bolt assembly used by all operations of a connection.

        Ids come from list_project_materials (welding, steel, bolt_assemblies). Only given ids change.
        """
        with tool_errors("update_common_operation_properties"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            current = session.invoke("operation", "get_common_operation_properties", pid, cid) or ConOperationCommonProperties()
            if weld_material_id is not None:
                current.weld_material_id = int(weld_material_id)
            if plate_material_id is not None:
                current.plate_material_id = int(plate_material_id)
            if bolt_assembly_id is not None:
                current.bolt_assembly_id = int(bolt_assembly_id)
            session.invoke("operation", "update_common_operation_properties", pid, cid, con_operation_common_properties=current)
            props = session.invoke("operation", "get_common_operation_properties", pid, cid)
            return {"connection_id": cid, "common_properties": to_jsonable(props)}

    @server.tool(annotations=WRITE)
    def predesign_welds(method: str = "fullStrength", connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Run weld sizing pre-design for a connection.

        method: fullStrength (default), minimumDuctility, overStrengthFactor, capacityEstimation.
        Returns the service's report of the sized welds.
        """
        with tool_errors("predesign_welds"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            design_type = enum_value(ConWeldSizingMethodEnum, method, "method")
            result = session.invoke("operation", "pre_design_welds", pid, cid, design_type=design_type)
            return {"connection_id": cid, "method": design_type.value, "result": _parse_json_text(result)}

    _ = IdeaError  # re-exported for type checkers; errors are raised through tool_errors
