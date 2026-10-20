"""Tools: design-code conversion of a whole project."""

from __future__ import annotations

from ideastatica_connection_api.models.country_code import CountryCode

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import compact_project, to_jsonable
from ideastatica_mcp.tools.common import READ, WRITE, normalize_design_code

CATEGORIES = ("steel", "concrete", "cross_sections", "fasteners", "welds", "bolt_grade")
CATEGORY_ALIASES = {"crosssections": "cross_sections", "css": "cross_sections", "boltgrade": "bolt_grade", "bolts": "fasteners", "weld": "welds"}


def _category(name: str) -> str:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    key = CATEGORY_ALIASES.get(key.replace("_", ""), key)
    if key not in CATEGORIES:
        raise IdeaError(f"unknown mapping category '{name}'. Allowed: {', '.join(CATEGORIES)}")
    return key


def register(server, session) -> None:
    @server.tool(annotations=READ)
    def get_design_code_conversion_mapping(target_code: str, project_id: str | None = None) -> dict:
        """Get the default mapping used to convert the project to another design code.

        target_code: american (AISC), ecen, canada, australia, india, sia, rus, chn, hkg. The
        result lists per category (steel, concrete, cross_sections, fasteners, welds, bolt_grade)
        the source value and the proposed target value. Only Eurocode (ecen) projects can be
        converted. Review it, then call convert_project_design_code with overrides if needed.
        """
        with tool_errors("get_design_code_conversion_mapping"):
            pid = session.resolve_project(project_id)
            code = CountryCode(normalize_design_code(target_code))
            mapping = session.invoke("conversion", "get_conversion_mapping", pid, country_code=code)
            return {"project_id": pid, "target_code": code.value, "mapping": to_jsonable(mapping)}

    @server.tool(annotations=WRITE)
    def convert_project_design_code(
        target_code: str,
        project_id: str | None = None,
        overrides: dict[str, dict[str, str]] | None = None,
        keep_cross_sections: bool = False,
    ) -> dict:
        """Convert the whole project to another design code using the default mapping plus overrides.

        overrides: {"steel": {"S 355": "A992"}, "bolt_grade": {"8.8": "A325"}} maps a source value
        to a target value per category (steel, concrete, cross_sections, fasteners, welds,
        bolt_grade). keep_cross_sections=true keeps the original cross-section names instead of the
        proposed equivalents. Save the project afterwards with save_project.
        """
        with tool_errors("convert_project_design_code"):
            pid = session.resolve_project(project_id)
            code = CountryCode(normalize_design_code(target_code))
            mapping = session.invoke("conversion", "get_conversion_mapping", pid, country_code=code)
            applied: list[dict] = []
            for raw_category, pairs in (overrides or {}).items():
                category = _category(raw_category)
                items = getattr(mapping, category, None) or []
                for item in items:
                    if item.source_value in pairs:
                        item.target_value = str(pairs[item.source_value])
                        applied.append({"category": category, "source": item.source_value, "target": item.target_value})
            if keep_cross_sections:
                for item in mapping.cross_sections or []:
                    item.target_value = item.source_value
            session.snapshot(pid, f"convert_project_design_code {code.value}")
            session.invoke("conversion", "change_code", pid, con_conversion_settings=mapping)
            project = session.invoke("project", "get_project_data", pid)
            if pid in session.projects:
                session.projects[pid].design_code = code.value
            return {
                "project_id": pid,
                "target_code": code.value,
                "overrides_applied": applied,
                "mapping_used": to_jsonable(mapping),
                "project": compact_project(project),
            }
