"""Tools: read, add and edit individual manufacturing operations (welds, bolt grids, cuts, plates...).

These use operation endpoints that ship with the IDEA StatiCa 26.0 service but are not yet part of
the published OpenAPI document / SDK ("hidden from Swagger until the operation API set is released").
They are called through the session's raw REST helper with the same client id.
"""

from __future__ import annotations

from typing import Any

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.tools.common import DESTRUCTIVE, READ, WRITE, deep_merge

KINDS = {
    "weld": "weld",
    "welds": "weld",
    "cut": "cut",
    "cuts": "cut",
    "plate_cut": "plate-cut",
    "platecut": "plate-cut",
    "bolt_grid": "bolt-grid",
    "boltgrid": "bolt-grid",
    "bolts": "bolt-grid",
    "anchor_grid": "anchor-grid",
    "anchors": "anchor-grid",
    "pin_grid": "pin-grid",
    "pins": "pin-grid",
    "contact": "contact",
    "contact_grid": "contact-grid",
    "stiffening_plate": "stiffening-plate",
    "plate": "stiffening-plate",
    "stiffening_member": "stiffening-member",
    "stiffener": "stiffening-member",
    "negative_plate": "negative-plate",
    "negative_member": "negative-member",
    "work_plane": "work-plane",
    "workplane": "work-plane",
}
KIND_LIST = sorted(set(KINDS.values()))


def normalize_kind(value: str) -> str:
    key = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    kind = KINDS.get(key) or KINDS.get(key.rstrip("s"))
    if kind is None:
        if key.replace("_", "-") in KIND_LIST:
            return key.replace("_", "-")
        raise IdeaError(f"unknown operation kind '{value}'. Allowed: {', '.join(KIND_LIST)}")
    return kind


def _lower_first(key: str) -> str:
    return key[:1].lower() + key[1:] if key else key


def normalize_json_keys(data: Any) -> Any:
    """Accept PascalCase / snake_case keys and emit the camelCase the service uses."""
    if isinstance(data, list):
        return [normalize_json_keys(v) for v in data]
    if not isinstance(data, dict):
        return data
    out: dict = {}
    for key, value in data.items():
        if "_" in key:
            parts = key.split("_")
            key = parts[0].lower() + "".join(p[:1].upper() + p[1:] for p in parts[1:])
        out[_lower_first(key)] = normalize_json_keys(value)
    return out


NOT_ENABLED = (
    "the per-operation endpoints (operations/{kind}) are not enabled in this IDEA StatiCa service build "
    "(26.0.5 answers 404 for them; IDEA StatiCa plans to release the operation API set later). Edit geometry "
    "through parameters (update_parameters), templates (apply_template / apply_library_design) or IOM import, "
    "and size welds with auto_weld_sizing / predesign_welds."
)


def _rest(session, method: str, path: str, body=None):
    from ideastatica_mcp.errors import RestError

    try:
        return session.rest(method, path, body)
    except RestError as exc:
        if exc.status == 404 and not (exc.body or "").strip():
            raise IdeaError(NOT_ENABLED) from exc
        raise


def register(server, session) -> None:
    def _path(pid: str, cid: int, kind: str, op_id: int | None = None) -> str:
        base = f"/projects/{pid}/connections/{cid}/operations/{kind}"
        return f"{base}/{op_id}" if op_id is not None else base

    @server.tool(annotations=READ)
    def list_operation_details(kind: str, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Full definitions of all operations of one kind in a connection (editable fields included).

        kind: weld, cut, plate_cut, bolt_grid, anchor_grid, pin_grid, contact, contact_grid,
        stiffening_plate, stiffening_member, negative_plate, negative_member, work_plane.
        For welds the result shows id, name, type (fillet/double fillet/...), size (m), placement
        and connected items - what update_operation / update_weld change. Dimensions are SI (m).
        """
        with tool_errors("list_operation_details"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            k = normalize_kind(kind)
            items = session.rest("GET", _path(pid, cid, k)) or []
            return {"connection_id": cid, "kind": k, "count": len(items), "operations": items}

    @server.tool(annotations=READ)
    def get_operation_details(kind: str, operation_id: int, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Full definition of one operation (see list_operation_details for kinds). operation_id from list_operations."""
        with tool_errors("get_operation_details"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            k = normalize_kind(kind)
            return {"connection_id": cid, "kind": k, "operation": session.rest("GET", _path(pid, cid, k, int(operation_id)))}

    @server.tool(annotations=WRITE)
    def update_operation(
        kind: str, operation_id: int, changes: dict, connection_id: int | None = None, project_id: str | None = None
    ) -> dict:
        """Edit an existing operation: the current definition is fetched, `changes` is deep-merged into it and the result is sent back (PUT).

        Example weld: kind="weld", changes={"size": 0.006, "type": "doubleFillet"}. Example bolt
        grid: {"boltAssembly": "M24 8.8"}. Keys may be camelCase or snake_case; dimensions in m.
        Returns the stored operation plus any non-conformity issues from the model rebuild.
        Recalculate afterwards.
        """
        with tool_errors("update_operation"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            k = normalize_kind(kind)
            current = session.rest("GET", _path(pid, cid, k, int(operation_id)))
            if not isinstance(current, dict):
                raise IdeaError(f"operation {operation_id} of kind {k} not found")
            merged = deep_merge(current, normalize_json_keys(changes or {}))
            merged["id"] = int(operation_id)
            result = session.rest("PUT", _path(pid, cid, k), merged)
            return {"connection_id": cid, "kind": k, "sent": merged, "result": result}

    @server.tool(annotations=WRITE)
    def add_operation(kind: str, operation: dict, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Add a new operation of the given kind from a full definition (POST).

        Copy the structure from list_operation_details of a similar operation and change the
        fields; omit "id". Returns the created operation and any non-conformity issues.
        """
        with tool_errors("add_operation"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            k = normalize_kind(kind)
            payload = normalize_json_keys(operation or {})
            payload.pop("id", None)
            result = session.rest("POST", _path(pid, cid, k), payload)
            return {"connection_id": cid, "kind": k, "result": result}

    @server.tool(annotations=DESTRUCTIVE)
    def delete_operation(kind: str, operation_id: int, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Delete one operation (DELETE on the operation endpoint; not every service build supports it - delete_all_operations always works)."""
        with tool_errors("delete_operation"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            k = normalize_kind(kind)
            result = session.rest("DELETE", _path(pid, cid, k, int(operation_id)))
            return {"connection_id": cid, "kind": k, "deleted_operation_id": int(operation_id), "result": result}
