"""Tools: load effects (internal forces on members) and load settings."""

from __future__ import annotations

from typing import Any

from ideastatica_connection_api.models.con_load_effect import ConLoadEffect
from ideastatica_connection_api.models.con_load_effect_member_load import ConLoadEffectMemberLoad
from ideastatica_connection_api.models.con_load_effect_position_enum import ConLoadEffectPositionEnum
from ideastatica_connection_api.models.con_load_effect_section_load import ConLoadEffectSectionLoad
from ideastatica_connection_api.models.con_load_settings import ConLoadSettings

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import to_jsonable
from ideastatica_mcp.tools.common import DESTRUCTIVE, READ, WRITE, enum_value

FORCE_KEYS = ("n", "vy", "vz", "mx", "my", "mz")


def _member_loads(items: list[dict] | None) -> list[ConLoadEffectMemberLoad]:
    loads: list[ConLoadEffectMemberLoad] = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("member_id", item.get("memberId")) is None:
            raise IdeaError("each member load needs member_id plus forces n, vy, vz (N) and mx, my, mz (Nm)")
        member_id = int(item.get("member_id", item.get("memberId")))
        position = enum_value(ConLoadEffectPositionEnum, str(item.get("position", "End")), "position")
        section = item.get("section_load") or item.get("sectionLoad") or item
        values = {key: float(section.get(key, 0.0) or 0.0) for key in FORCE_KEYS}
        loads.append(
            ConLoadEffectMemberLoad(
                member_id=member_id,
                position=position,
                section_load=ConLoadEffectSectionLoad(**values),
            )
        )
    return loads


def compact_load_effect(effect: Any) -> dict:
    data = to_jsonable(effect) or {}
    loads = []
    for item in data.get("memberLoadings") or []:
        section = item.get("sectionLoad") or {}
        loads.append(
            {
                "member_id": item.get("memberId"),
                "position": item.get("position"),
                **{key: section.get(key) for key in FORCE_KEYS},
            }
        )
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "active": data.get("active"),
        "is_percentage": data.get("isPercentage"),
        "member_loads": loads,
    }


def register(server, session) -> None:
    @server.tool(annotations=READ)
    def list_load_effects(
        connection_id: int | None = None, project_id: str | None = None, is_percentage: bool | None = None
    ) -> dict:
        """List the load effects (load cases) of a connection with the forces per member.

        Forces n, vy, vz are in N and moments mx, my, mz in Nm (SI, e.g. -172300 = -172.3 kN) at the
        member End (or Begin).
        is_percentage=true returns loads as percentages of member resistance when the project uses
        that mode.
        """
        with tool_errors("list_load_effects"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            effects = session.invoke("load_effect", "get_load_effects", pid, cid, is_percentage=is_percentage) or []
            return {"connection_id": cid, "count": len(effects), "load_effects": [compact_load_effect(e) for e in effects]}

    @server.tool(annotations=READ)
    def get_load_effect(load_effect_id: int, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Get one load effect with its member loads (N, Nm)."""
        with tool_errors("get_load_effect"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            effect = session.invoke("load_effect", "get_load_effect", pid, cid, int(load_effect_id))
            return {"connection_id": cid, "load_effect": compact_load_effect(effect), "raw": to_jsonable(effect)}

    @server.tool(annotations=WRITE)
    def add_load_effect(
        name: str,
        member_loads: list[dict],
        connection_id: int | None = None,
        project_id: str | None = None,
        is_percentage: bool | None = None,
        active: bool = True,
    ) -> dict:
        """Add a load effect (load case) to a connection.

        member_loads: [{"member_id": 1, "position": "End", "n": -120000, "vy": 0, "vz": 45000,
        "mx": 0, "my": 80000, "mz": 0}, ...] with forces in N and moments in Nm - SI units, so
        120 kN = 120000 (sign convention of IDEA StatiCa: n positive = tension at the End position). Give one entry per loaded member; the
        bearing member is usually balanced automatically when loads are in equilibrium.
        """
        with tool_errors("add_load_effect"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            effect = ConLoadEffect(name=name, active=active, is_percentage=is_percentage, member_loadings=_member_loads(member_loads))
            session.snapshot(pid, f"add_load_effect {name}")
            created = session.invoke("load_effect", "add_load_effect", pid, cid, con_load_effect=effect)
            return {"connection_id": cid, "load_effect": compact_load_effect(created)}

    @server.tool(annotations=WRITE)
    def update_load_effect(
        load_effect_id: int,
        connection_id: int | None = None,
        project_id: str | None = None,
        name: str | None = None,
        active: bool | None = None,
        is_percentage: bool | None = None,
        member_loads: list[dict] | None = None,
    ) -> dict:
        """Change a load effect: rename, (de)activate, or replace its member loads (same format as add_load_effect).

        When member_loads is given it replaces all member loads of the load effect.
        """
        with tool_errors("update_load_effect"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            effect = session.invoke("load_effect", "get_load_effect", pid, cid, int(load_effect_id))
            if name is not None:
                effect.name = name
            if active is not None:
                effect.active = active
            if is_percentage is not None:
                effect.is_percentage = is_percentage
            if member_loads is not None:
                effect.member_loadings = _member_loads(member_loads)
            effect.id = int(load_effect_id)
            session.snapshot(pid, f"update_load_effect {load_effect_id}")
            updated = session.invoke("load_effect", "update_load_effect", pid, cid, con_load_effect=effect)
            return {"connection_id": cid, "load_effect": compact_load_effect(updated)}

    @server.tool(annotations=DESTRUCTIVE)
    def delete_load_effect(load_effect_id: int, connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Delete a load effect from a connection."""
        with tool_errors("delete_load_effect"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            session.snapshot(pid, f"delete_load_effect {load_effect_id}")
            result = session.invoke("load_effect", "delete_load_effect", pid, cid, int(load_effect_id))
            return {"connection_id": cid, "deleted_load_effect_id": int(load_effect_id), "result": to_jsonable(result)}

    @server.tool(annotations=READ)
    def get_load_settings(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Get the load settings of a connection: loads in equilibrium, loads in percentage."""
        with tool_errors("get_load_settings"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            settings = session.invoke("load_effect", "get_load_settings", pid, cid)
            return {"connection_id": cid, "load_settings": to_jsonable(settings)}

    @server.tool(annotations=WRITE)
    def set_load_settings(
        connection_id: int | None = None,
        project_id: str | None = None,
        loads_in_equilibrium: bool | None = None,
        loads_in_percentage: bool | None = None,
    ) -> dict:
        """Change the load settings of a connection (loads in equilibrium / loads in percentage)."""
        with tool_errors("set_load_settings"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            current = session.invoke("load_effect", "get_load_settings", pid, cid) or ConLoadSettings()
            if loads_in_equilibrium is not None:
                current.loads_in_equilibrium = loads_in_equilibrium
            if loads_in_percentage is not None:
                current.loads_in_percentage = loads_in_percentage
            updated = session.invoke("load_effect", "set_load_settings", pid, cid, con_load_settings=current)
            return {"connection_id": cid, "load_settings": to_jsonable(updated)}

    @server.tool(annotations=WRITE)
    def calculate_load_extremes(connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Let the service derive the extreme (envelope) load effects of a connection from the existing loads."""
        with tool_errors("calculate_load_extremes"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            effects = session.invoke("load_effect", "calculate_load_extremes", pid, cid) or []
            return {"connection_id": cid, "count": len(effects), "load_effects": [compact_load_effect(e) for e in effects]}
