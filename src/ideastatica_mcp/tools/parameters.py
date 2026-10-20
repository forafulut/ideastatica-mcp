"""Tools: design parameters (Developer tab) and expression evaluation."""

from __future__ import annotations

from ideastatica_connection_api.models.idea_parameter_update import IdeaParameterUpdate

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import compact_parameter, to_jsonable
from ideastatica_mcp.tools.common import DESTRUCTIVE, READ, WRITE, to_expression


def _build_updates(updates: dict | list) -> list[IdeaParameterUpdate]:
    items: list[IdeaParameterUpdate] = []
    if isinstance(updates, dict):
        for key, value in updates.items():
            items.append(IdeaParameterUpdate(key=str(key), expression=to_expression(value)))
    elif isinstance(updates, list):
        for entry in updates:
            if not isinstance(entry, dict) or "key" not in entry:
                raise IdeaError("each update must be an object with 'key' and 'expression' (or 'value')")
            expression = entry.get("expression", entry.get("value"))
            items.append(IdeaParameterUpdate(key=str(entry["key"]), expression=to_expression(expression)))
    else:
        raise IdeaError("updates must be an object {key: expression} or a list of {key, expression}")
    if not items:
        raise IdeaError("no parameter updates given")
    return items


def register(server, session) -> None:
    @server.tool(annotations=READ)
    def list_parameters(
        connection_id: int | None = None, project_id: str | None = None, include_hidden: bool = False
    ) -> dict:
        """List the design parameters of a connection: key, expression, evaluated value, unit, bounds, validation.

        Parameters exist only in projects/templates set up with the Developer tab (parametric
        designs). Values are SI (m, N, Pa, rad). Always read the keys here before update_parameters.
        """
        with tool_errors("list_parameters"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            params = session.invoke("parameter", "get_parameters", pid, cid, include_hidden=include_hidden or None) or []
            return {"connection_id": cid, "count": len(params), "parameters": [compact_parameter(p) for p in params]}

    @server.tool(annotations=WRITE)
    def update_parameters(
        updates: dict[str, str | int | float | bool] | list[dict],
        connection_id: int | None = None,
        project_id: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Set parameter expressions and apply them to the model (the connection geometry updates).

        updates: {"key": expression, ...} or [{"key": ..., "expression": ...}]. Expressions follow the
        NCalc syntax of the parameter reference (resource ideastatica://docs/parameter-reference):
        numbers in SI units with a decimal point (plate thickness 0.015 = 15 mm), strings in single
        quotes ('M20 8.8'), references to other parameters as [key], functions such as
        Round([t]*1.5, 3). Numbers given as JSON numbers are converted to expression strings.
        dry_run=true only evaluates the expressions and checks them against the parameter bounds
        without touching the model. A snapshot is taken first (undo_last_change restores it).
        Returns which validations failed and the resulting parameter list.
        """
        with tool_errors("update_parameters"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            items = _build_updates(updates)
            if dry_run:
                existing = {p.key: p for p in (session.invoke("parameter", "get_parameters", pid, cid, include_hidden=True) or [])}
                checks = []
                for item in items:
                    row: dict = {"key": item.key, "expression": item.expression}
                    param = existing.get(item.key)
                    if param is None:
                        row["error"] = "unknown parameter key"
                    else:
                        try:
                            row["evaluated"] = session.invoke("parameter", "evaluate_expression", pid, cid, body=item.expression)
                        except Exception as exc:  # noqa: BLE001
                            row["error"] = str(exc)[:200]
                        validation = to_jsonable(getattr(param, "parameter_validation", None)) or {}
                        try:
                            value = float(row.get("evaluated")) if row.get("evaluated") not in (None, "") else None
                        except (TypeError, ValueError):
                            value = None
                        lower, upper = validation.get("lowerBoundEvaluated"), validation.get("upperBoundEvaluated")
                        if value is not None and lower is not None and value < lower:
                            row["error"] = f"{value} is below the lower bound {lower}"
                        if value is not None and upper is not None and value > upper:
                            row["error"] = f"{value} is above the upper bound {upper}"
                    checks.append(row)
                return {"connection_id": cid, "dry_run": True, "would_apply": all("error" not in c for c in checks), "checks": checks}
            snapshot = session.snapshot(pid, f"update_parameters {', '.join(i.key for i in items)}")
            response = session.invoke("parameter", "update", pid, cid, idea_parameter_update=items)
            data = to_jsonable(response) or {}
            failed = data.get("failedValidations") or []
            return {
                "connection_id": cid,
                "applied_to_model": data.get("setToModel"),
                "snapshot": snapshot,
                "updated_keys": [item.key for item in items],
                "failed_validations": failed,
                "parameters": [compact_parameter(p) for p in (getattr(response, "parameters", None) or [])],
            }

    @server.tool(annotations=READ)
    def evaluate_expression(expression: str, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Evaluate a parameter expression in the context of a connection without changing anything.

        Useful to test formulas, unit conversions (Length(20,'mm')) or model queries such as
        GetValue('B','CrossSection.Bounds.Height') before assigning them to a parameter.
        """
        with tool_errors("evaluate_expression"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            result = session.invoke("parameter", "evaluate_expression", pid, cid, body=expression)
            return {"connection_id": cid, "expression": expression, "result": result}

    @server.tool(annotations=DESTRUCTIVE)
    def delete_all_parameters(connection_id: int, project_id: str | None = None) -> dict:
        """Delete all parameters and parameter-to-model links of a connection (geometry stays as is)."""
        with tool_errors("delete_all_parameters"):
            pid = session.resolve_project(project_id)
            session.invoke("parameter", "delete_parameters", pid, int(connection_id))
            return {"connection_id": int(connection_id), "deleted": True}
