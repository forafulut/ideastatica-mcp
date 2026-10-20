"""Tools: project settings (code setup values)."""

from __future__ import annotations

from typing import Any

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import to_jsonable
from ideastatica_mcp.tools.common import READ, WRITE


def register(server, session) -> None:
    @server.tool(annotations=READ)
    def get_project_settings(project_id: str | None = None, search: str | None = None) -> dict:
        """Read project settings (code setup) as a key/value dictionary.

        search filters keys by substring (e.g. 'PlasticStrain', 'Bolts', 'Welds'). Keys look like
        'calculationCommon/Checks/Shared/LimitPlasticStrainForHighStrengthSteel@01'.
        """
        with tool_errors("get_project_settings"):
            pid = session.resolve_project(project_id)
            values = session.invoke("settings", "get_settings", pid, search=search) or {}
            return {"project_id": pid, "count": len(values), "settings": to_jsonable(values)}

    @server.tool(annotations=WRITE)
    def update_project_settings(values: dict[str, Any], project_id: str | None = None) -> dict:
        """Change project settings: pass {setting_key: new_value} using keys from get_project_settings.

        Example: {"calculationCommon/Checks/Shared/LimitPlasticStrainForHighStrengthSteel@01": 0.02}.
        Recalculate afterwards for the new setup to take effect on results.
        """
        with tool_errors("update_project_settings"):
            if not values:
                raise IdeaError("values must contain at least one setting")
            pid = session.resolve_project(project_id)
            session.snapshot(pid, "update_project_settings")
            updated = session.invoke("settings", "update_settings", pid, request_body=dict(values))
            return {"project_id": pid, "updated_keys": list(values), "settings": to_jsonable(updated)}
