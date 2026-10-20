"""Tools: materials, cross-sections, bolt assemblies (project items and the MPRL library)."""

from __future__ import annotations

from ideastatica_connection_api.models.con_mprl_cross_section import ConMprlCrossSection
from ideastatica_connection_api.models.con_mprl_element import ConMprlElement

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import to_jsonable
from ideastatica_mcp.tools.common import READ, WRITE, clip

LIST_METHODS = {
    "all": "get_all_materials",
    "steel": "get_steel_materials",
    "concrete": "get_concrete_materials",
    "bolt_grade": "get_bolt_grade_materials",
    "welding": "get_welding_materials",
    "headed_stud_grade": "get_headed_stud_grade_materials",
    "reinforcement": "get_reinforcement_materials",
    "cross_sections": "get_cross_sections",
    "bolt_assemblies": "get_bolt_assemblies",
    "pins": "get_pins",
}

ADD_METHODS = {
    "steel": "add_material_steel",
    "concrete": "add_material_concrete",
    "bolt_grade": "add_material_bolt_grade",
    "welding": "add_material_weld",
    "headed_stud_grade": "add_material_headed_stud_grade",
    "reinforcement": "add_material_reinforcement",
    "bolt_assemblies": "add_bolt_assembly",
    "pins": "add_pin",
}

LIBRARY_SLUGS = {
    "steel": "steel",
    "concrete": "concrete",
    "bolt_grade": "bolt-grade",
    "welding": "welding",
    "headed_stud_grade": "headed-stud-grade",
    "reinforcement": "reinforcement",
    "pins": "pin",
    "cross_sections": "cross-sections",
    "bolt_assemblies": "bolt-assemblies",
}

ALIASES = {
    "weld": "welding",
    "welds": "welding",
    "electrode": "welding",
    "boltgrade": "bolt_grade",
    "bolt_grades": "bolt_grade",
    "bolts": "bolt_grade",
    "bolt_assembly": "bolt_assemblies",
    "boltassembly": "bolt_assemblies",
    "boltassemblies": "bolt_assemblies",
    "assemblies": "bolt_assemblies",
    "cross_section": "cross_sections",
    "crosssection": "cross_sections",
    "crosssections": "cross_sections",
    "css": "cross_sections",
    "sections": "cross_sections",
    "pin": "pins",
    "stud": "headed_stud_grade",
    "studs": "headed_stud_grade",
    "headed_stud": "headed_stud_grade",
    "headedstudgrade": "headed_stud_grade",
    "rebar": "reinforcement",
    "reinforcements": "reinforcement",
}


COMPACT_KEYS = ("fy", "fu", "fck", "fcm", "e", "unitMass", "diameter", "shearArea", "tensileStressArea", "crossSectionType", "crossSectionRotation", "boltGrade", "boltAssembly", "thickness")


def compact_material(item) -> dict:
    """Short view of an IOM material / cross-section / bolt assembly object."""
    if not isinstance(item, dict):
        return {"value": item}
    out: dict = {"id": item.get("id"), "name": item.get("name")}
    type_name = item.get("$type") or item.get("typeName")
    if type_name:
        out["type"] = str(type_name).split(",")[0].rsplit(".", 1)[-1]
    material = item.get("material")
    if isinstance(material, dict):
        element = material.get("element") if isinstance(material.get("element"), dict) else {}
        out["material"] = element.get("name") or material.get("name") or material.get("id")
    for key in COMPACT_KEYS:
        value = item.get(key)
        if value is None and isinstance(item.get("element"), dict):
            value = item["element"].get(key)
        if value is not None and not isinstance(value, (dict, list)):
            out[key] = value
    params = item.get("parameters")
    if isinstance(params, list):
        for p in params:
            if isinstance(p, dict) and p.get("name") and not isinstance(p.get("value"), (dict, list)):
                out.setdefault("parameters", {})[p["name"]] = p.get("value")
    return out


def normalize_material_type(value: str, allowed: dict) -> str:
    key = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    key = ALIASES.get(key, key)
    if key in allowed:
        return key
    raise IdeaError(f"unknown material type '{value}'. Allowed: {', '.join(allowed)}")


def register(server, session) -> None:
    @server.tool(annotations=READ)
    def list_project_materials(material_type: str = "all", project_id: str | None = None, verbose: bool = False) -> dict:
        """List materials / cross-sections / bolt assemblies already present in the project (with their ids).

        material_type: all, steel, concrete, bolt_grade, welding, headed_stud_grade, reinforcement,
        cross_sections, bolt_assemblies, pins. The ids are what members (crossSectionId) and
        common operation properties (plate/weld material id, bolt assembly id) refer to. The
        default output is compact (id, name, type, key properties); verbose=true returns the
        full IOM objects.
        """
        with tool_errors("list_project_materials"):
            pid = session.resolve_project(project_id)
            kind = normalize_material_type(material_type, LIST_METHODS)
            items = to_jsonable(session.invoke("material", LIST_METHODS[kind], pid) or [])
            if verbose:
                return {"project_id": pid, "material_type": kind, "count": len(items), "items": items}
            return {"project_id": pid, "material_type": kind, "count": len(items), "items": [compact_material(i) for i in items]}

    @server.tool(annotations=WRITE)
    def add_project_material(material_type: str, mprl_name: str, project_id: str | None = None) -> dict:
        """Add an item from the IDEA StatiCa material & product range library (MPRL) to the project.

        material_type: steel, concrete, bolt_grade, welding, headed_stud_grade, reinforcement,
        bolt_assemblies, pins. mprl_name is the library name, e.g. 'S 355', 'C30/37', '8.8',
        'M20 8.8'. Use search_material_library to find valid names. For cross-sections use
        add_cross_section.
        """
        with tool_errors("add_project_material"):
            pid = session.resolve_project(project_id)
            kind = normalize_material_type(material_type, ADD_METHODS)
            element = ConMprlElement(mprl_name=mprl_name)
            result = session.invoke("material", ADD_METHODS[kind], pid, con_mprl_element=element)
            return {"project_id": pid, "material_type": kind, "added": to_jsonable(result)}

    @server.tool(annotations=WRITE)
    def add_cross_section(mprl_name: str, material_name: str, project_id: str | None = None) -> dict:
        """Add a library cross-section (e.g. mprl_name 'HEB200', 'IPE300') with a steel material (e.g. 'S 355') to the project.

        The material must exist in the project or the MPRL. The returned item id can be used as a
        member's crossSectionId.
        """
        with tool_errors("add_cross_section"):
            pid = session.resolve_project(project_id)
            css = ConMprlCrossSection(mprl_name=mprl_name, material_name=material_name)
            result = session.invoke("material", "add_cross_section", pid, con_mprl_cross_section=css)
            return {"project_id": pid, "added": to_jsonable(result)}

    @server.tool(annotations=READ)
    def search_material_library(
        material_type: str, query: str | None = None, project_id: str | None = None, limit: int = 100
    ) -> dict:
        """Search the MPRL library names available for a material type (steel, concrete, bolt_grade, welding, headed_stud_grade, reinforcement, pins, cross_sections, bolt_assemblies).

        query filters names case-insensitively (e.g. 'HEB', 'S 3', 'M20'). Returns up to `limit`
        names; use them with add_project_material / add_cross_section.
        """
        with tool_errors("search_material_library"):
            pid = session.resolve_project(project_id)
            kind = normalize_material_type(material_type, LIBRARY_SLUGS)
            names = session.invoke("material", "get_material_library", pid, LIBRARY_SLUGS[kind]) or []
            names = [str(n) for n in names]
            if query:
                needle = query.lower()
                names = [n for n in names if needle in n.lower()]
            shown, truncated = clip(names, max(1, int(limit)))
            return {
                "project_id": pid,
                "material_type": kind,
                "total_matches": len(names),
                "truncated": truncated,
                "names": shown,
            }
