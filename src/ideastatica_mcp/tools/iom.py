"""Tools: IDEA Open Model (IOM) files - inspection and a minimal steel-frame builder."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.tools.common import READ, WRITE, resolve_output_path, sanitize_filename

SUMMARY_TAGS = {
    "Point3D": "points",
    "LineSegment3D": "line_segments",
    "Member1D": "members",
    "Element1D": "elements",
    "ConnectionPoint": "connection_points",
    "CrossSection": "cross_sections",
    "CrossSectionParameter": "cross_sections",
    "CrossSectionGeneralSteel": "cross_sections",
    "MatSteel": "steel_materials",
    "MatSteelEc2": "steel_materials",
    "MatConcreteEc2": "concrete_materials",
    "LoadCase": "load_cases",
    "LoadGroup": "load_groups",
    "CombiInput": "combinations",
    "LoadOnPoint": "point_loads",
    "LoadOnLine": "line_loads",
}


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _text(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    child = next((c for c in element if _local(c.tag) == name), None)
    return child.text.strip() if child is not None and child.text else None


def _ref(type_name: str, ref_id: int) -> str:
    return f"<TypeName>{type_name}</TypeName><Id>{ref_id}</Id>"


def _steel(mat_id: int, name: str) -> str:
    return (
        f'<MatSteel xsi:type="MatSteelEc2"><Id>{mat_id}</Id><Name>{name}</Name><LoadFromLibrary>true</LoadFromLibrary>'
        "<E>0</E><G>0</G><Poisson>0</Poisson><UnitMass>0</UnitMass><SpecificHeat>0</SpecificHeat><ThermalExpansion>0</ThermalExpansion>"
        "<ThermalConductivity>0</ThermalConductivity><IsDefaultMaterial>false</IsDefaultMaterial><OrderInCode>0</OrderInCode>"
        "<StateOfThermalExpansion>None</StateOfThermalExpansion><StateOfThermalConductivity>None</StateOfThermalConductivity>"
        "<StateOfThermalSpecificHeat>None</StateOfThermalSpecificHeat><StateOfThermalStressStrain>None</StateOfThermalStressStrain>"
        "<StateOfThermalStrain>None</StateOfThermalStrain><UserThermalStressStrainCurvature /><fy>0</fy><fu>0</fu><fy40>0</fy40><fu40>0</fu40>"
        "<DiagramType>Bilinear</DiagramType></MatSteel>"
    )


def _css(css_id: int, name: str, mat_id: int) -> str:
    return (
        f'<CrossSection xsi:type="CrossSectionParameter"><Id>{css_id}</Id><Name>{name}</Name><CrossSectionRotation>0</CrossSectionRotation>'
        "<IsInPrincipal>false</IsInPrincipal><CrossSectionType>RolledI</CrossSectionType>"
        f'<Parameters><Parameter xsi:type="ParameterString"><Name>UniqueName</Name><Value>{name}</Value></Parameter></Parameters>'
        f"<Material>{_ref('MatSteel', mat_id)}</Material></CrossSection>"
    )


def _element(el_id: int, segment_id: int) -> str:
    return (
        f"<Element1D><Id>{el_id}</Id><Name>E{el_id}</Name><Segment>{_ref('LineSegment3D', segment_id)}</Segment><RotationRx>0</RotationRx>"
        "<EccentricityBeginX>0</EccentricityBeginX><EccentricityBeginY>0</EccentricityBeginY><EccentricityBeginZ>0</EccentricityBeginZ>"
        "<EccentricityEndX>0</EccentricityEndX><EccentricityEndY>0</EccentricityEndY><EccentricityEndZ>0</EccentricityEndZ></Element1D>"
    )


def _member(m_id: int, name: str, kind: str, element_ids: list[int], css_id: int) -> str:
    refs = "".join(f"<ReferenceElement>{_ref('Element1D', e)}</ReferenceElement>" for e in element_ids)
    return (
        f"<Member1D><Id>{m_id}</Id><Name>{name}</Name><Elements1D>{refs}</Elements1D><Member1DType>{kind}</Member1DType>"
        f"<CrossSection>{_ref('CrossSection', css_id)}</CrossSection><Alignment>Center</Alignment><MirrorY>false</MirrorY><MirrorZ>false</MirrorZ></Member1D>"
    )


def frame_iom_xml(column_section: str, beam_section: str, material: str, column_height_m: float, beam_length_m: float, continuous_column: bool = True) -> str:
    """IOM container for one column (two elements, continuous) and one beam framing into its middle node."""
    h, L = float(column_height_m), float(beam_length_m)
    points = [(1, 0, 0, 0), (2, 0, 0, h), (3, 0, 0, 2 * h), (4, L, 0, h)]
    segments = [(1, 1, 2), (2, 2, 3), (3, 2, 4)]
    pts = "".join(f"<Point3D><Id>{i}</Id><Name>N{i}</Name><X>{x}</X><Y>{y}</Y><Z>{z}</Z></Point3D>" for i, x, y, z in points)
    segs = "".join(
        f"<LineSegment3D><Id>{i}</Id><StartPoint>{_ref('Point3D', a)}</StartPoint><EndPoint>{_ref('Point3D', b)}</EndPoint></LineSegment3D>"
        for i, a, b in segments
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<OpenModelContainer xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        "<OpenModel><Version>2</Version>"
        "<OriginSettings><ProjectName>MCP frame</ProjectName><DateOfCreate>2026-01-01T00:00:00</DateOfCreate>"
        "<CrossSectionConversionTable>NoUsed</CrossSectionConversionTable><CountryCode>ECEN</CountryCode>"
        "<ImportRecommendedWelds>false</ImportRecommendedWelds><CheckEquilibrium>true</CheckEquilibrium></OriginSettings>"
        f"<Point3D>{pts}</Point3D><LineSegment3D>{segs}</LineSegment3D>"
        f"<MatSteel>{_steel(1, material)}</MatSteel>"
        f"<CrossSection>{_css(1, column_section, 1)}{_css(2, beam_section, 1)}</CrossSection>"
        f"<Element1D>{_element(1, 1)}{_element(2, 2)}{_element(3, 3)}</Element1D>"
        f"<Member1D>{_member(1, 'COLUMN', 'Column', [1, 2], 1)}{_member(2, 'BEAM', 'Beam', [3], 2)}</Member1D>"
        "<ConnectionPoint><ConnectionPoint><Id>1</Id><Name>CON1</Name>"
        f"<Node>{_ref('Point3D', 2)}</Node><ConnectedMembers>"
        f"<ConnectedMember><Id>1</Id><MemberId>{_ref('Member1D', 1)}</MemberId><IsContinuous>{'true' if continuous_column else 'false'}</IsContinuous><Length>0</Length></ConnectedMember>"
        f"<ConnectedMember><Id>2</Id><MemberId>{_ref('Member1D', 2)}</MemberId><IsContinuous>false</IsContinuous><Length>0</Length></ConnectedMember>"
        "</ConnectedMembers><NodeId>0</NodeId></ConnectionPoint></ConnectionPoint>"
        "</OpenModel><OpenModelResult><ResultOnMembers><ResultOnMembers></ResultOnMembers></ResultOnMembers></OpenModelResult>"
        "</OpenModelContainer>\n"
    )


def portal_frame_iom_xml(bays: int, storeys: int, bay_width_m: float, storey_height_m: float, column_section: str, beam_section: str, material: str) -> tuple[str, list[dict]]:
    """Multi-bay / multi-storey 2D frame: every beam-column node becomes a connection point."""
    bays, storeys = max(1, int(bays)), max(1, int(storeys))
    points: dict[tuple[int, int], int] = {}
    pts = []
    pid = 0
    for i in range(bays + 1):
        for j in range(storeys + 1):
            pid += 1
            points[(i, j)] = pid
            pts.append(f"<Point3D><Id>{pid}</Id><Name>N{pid}</Name><X>{i * bay_width_m}</X><Y>0</Y><Z>{j * storey_height_m}</Z></Point3D>")
    segs, elements, members = [], [], []
    seg_id = el_id = mem_id = 0
    column_elements: dict[int, list[int]] = {}
    for i in range(bays + 1):
        els = []
        for j in range(storeys):
            seg_id += 1
            el_id += 1
            segs.append(f"<LineSegment3D><Id>{seg_id}</Id><StartPoint>{_ref('Point3D', points[(i, j)])}</StartPoint><EndPoint>{_ref('Point3D', points[(i, j + 1)])}</EndPoint></LineSegment3D>")
            elements.append(_element(el_id, seg_id))
            els.append(el_id)
        mem_id += 1
        members.append(_member(mem_id, f"COL{i + 1}", "Column", els, 1))
        column_elements[i] = els
    column_member_ids = {i: i + 1 for i in range(bays + 1)}
    beam_member_ids: dict[tuple[int, int], int] = {}
    for j in range(1, storeys + 1):
        for i in range(bays):
            seg_id += 1
            el_id += 1
            segs.append(f"<LineSegment3D><Id>{seg_id}</Id><StartPoint>{_ref('Point3D', points[(i, j)])}</StartPoint><EndPoint>{_ref('Point3D', points[(i + 1, j)])}</EndPoint></LineSegment3D>")
            elements.append(_element(el_id, seg_id))
            mem_id += 1
            members.append(_member(mem_id, f"B{j}-{i + 1}", "Beam", [el_id], 2))
            beam_member_ids[(i, j)] = mem_id
    cps = []
    cp_info = []
    cp_id = 0
    cm_id = 0
    for i in range(bays + 1):
        for j in range(1, storeys + 1):
            cp_id += 1
            connected = []
            cm_id += 1
            continuous = j < storeys
            connected.append(f"<ConnectedMember><Id>{cm_id}</Id><MemberId>{_ref('Member1D', column_member_ids[i])}</MemberId><IsContinuous>{'true' if continuous else 'false'}</IsContinuous><Length>0</Length></ConnectedMember>")
            beams = []
            for (bi, bj), bm in beam_member_ids.items():
                if bj == j and (bi == i or bi + 1 == i):
                    cm_id += 1
                    connected.append(f"<ConnectedMember><Id>{cm_id}</Id><MemberId>{_ref('Member1D', bm)}</MemberId><IsContinuous>false</IsContinuous><Length>0</Length></ConnectedMember>")
                    beams.append(bm)
            cps.append(f"<ConnectionPoint><Id>{cp_id}</Id><Name>CON{cp_id}</Name><Node>{_ref('Point3D', points[(i, j)])}</Node><ConnectedMembers>{''.join(connected)}</ConnectedMembers><NodeId>0</NodeId></ConnectionPoint>")
            cp_info.append({"id": cp_id, "name": f"CON{cp_id}", "column_member_id": column_member_ids[i], "beam_member_ids": beams, "x": i * bay_width_m, "z": j * storey_height_m})
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<OpenModelContainer xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        "<OpenModel><Version>2</Version>"
        "<OriginSettings><ProjectName>MCP portal frame</ProjectName><DateOfCreate>2026-01-01T00:00:00</DateOfCreate>"
        "<CrossSectionConversionTable>NoUsed</CrossSectionConversionTable><CountryCode>ECEN</CountryCode>"
        "<ImportRecommendedWelds>false</ImportRecommendedWelds><CheckEquilibrium>true</CheckEquilibrium></OriginSettings>"
        f"<Point3D>{''.join(pts)}</Point3D><LineSegment3D>{''.join(segs)}</LineSegment3D>"
        f"<MatSteel>{_steel(1, material)}</MatSteel>"
        f"<CrossSection>{_css(1, column_section, 1)}{_css(2, beam_section, 1)}</CrossSection>"
        f"<Element1D>{''.join(elements)}</Element1D><Member1D>{''.join(members)}</Member1D>"
        f"<ConnectionPoint>{''.join(cps)}</ConnectionPoint>"
        "</OpenModel><OpenModelResult><ResultOnMembers><ResultOnMembers></ResultOnMembers></ResultOnMembers></OpenModelResult></OpenModelContainer>\n"
    )
    return xml, cp_info


def register(server, session) -> None:
    @server.tool(annotations=WRITE)
    def build_iom_portal_frame(
        bays: int = 2,
        storeys: int = 1,
        bay_width_m: float = 6.0,
        storey_height_m: float = 4.0,
        column_section: str = "HEB300",
        beam_section: str = "IPE360",
        material: str = "S 355",
        file_path: str | None = None,
        overwrite: bool = True,
    ) -> dict:
        """Write an IOM container for a plane frame with `bays` x `storeys`; every beam-column node is a connection point.

        Import with open_project(file_path, connections_to_create=[ids]) to create several
        connections at once, then add loads per connection and apply library designs.
        """
        with tool_errors("build_iom_portal_frame"):
            xml, cps = portal_frame_iom_xml(bays, storeys, bay_width_m, storey_height_m, column_section, beam_section, material)
            target = resolve_output_path(session.settings, file_path, sanitize_filename(f"frame-{bays}x{storeys}-{column_section}-{beam_section}") + ".xml", overwrite)
            target.write_text(xml, encoding="utf-8")
            return {"file_path": str(target), "connection_points": cps, "members": {"columns": bays + 1, "beams": bays * storeys}}

    @server.tool(annotations=READ)
    def inspect_iom_file(file_path: str) -> dict:
        """Summarize an IDEA Open Model container XML: counts of points, members, connection points, cross-sections, materials, loads, and whether results are included.

        Use before open_project to see which connection point ids exist (connections_to_create).
        """
        with tool_errors("inspect_iom_file"):
            path = Path(file_path).expanduser()
            if not path.is_file():
                raise IdeaError(f"file not found: {path}")
            root = ET.parse(path).getroot()
            counts: dict[str, int] = {}
            connection_points = []
            members = []
            cross_sections = []
            for element in root.iter():
                tag = _local(element.tag)
                key = SUMMARY_TAGS.get(tag)
                if key and _text(element, "Id") is not None:  # skip the list container elements
                    counts[key] = counts.get(key, 0) + 1
                elif key:
                    continue
                if tag == "ConnectionPoint":
                    connection_points.append({"id": _text(element, "Id"), "name": _text(element, "Name")})
                elif tag == "Member1D":
                    members.append({"id": _text(element, "Id"), "name": _text(element, "Name"), "type": _text(element, "Member1DType")})
                elif tag in ("CrossSection", "CrossSectionParameter", "CrossSectionGeneralSteel"):
                    cross_sections.append({"id": _text(element, "Id"), "name": _text(element, "Name")})
            has_results = any(_local(e.tag) == "ResultOnMember" for e in root.iter())
            return {
                "file_path": str(path),
                "root": _local(root.tag),
                "counts": counts,
                "has_results": has_results,
                "connection_points": connection_points[:50],
                "members": members[:50],
                "cross_sections": cross_sections[:50],
            }

    @server.tool(annotations=WRITE)
    def build_iom_frame(
        file_path: str | None = None,
        column_section: str = "HEB300",
        beam_section: str = "IPE300",
        material: str = "S 355",
        column_height_m: float = 4.0,
        beam_length_m: float = 6.0,
        continuous_column: bool = True,
        overwrite: bool = True,
    ) -> dict:
        """Write a minimal IOM container (a continuous column with one beam framing into it, one connection point) to import with open_project.

        Sections/materials are MPRL library names (Eurocode, e.g. HEB300, IPE300, S 355). The
        model carries no loads: after open_project add them with add_load_effect (N, Nm), then
        apply a template / library design and calculate. Edit the XML for other topologies.
        """
        with tool_errors("build_iom_frame"):
            xml = frame_iom_xml(column_section, beam_section, material, column_height_m, beam_length_m, continuous_column)
            target = resolve_output_path(session.settings, file_path, sanitize_filename(f"frame-{column_section}-{beam_section}") + ".xml", overwrite)
            target.write_text(xml, encoding="utf-8")
            return {
                "file_path": str(target),
                "connection_point_ids": [1],
                "members": {"1": "COLUMN (bearing, continuous)", "2": "BEAM"},
                "next_steps": ["open_project(file_path)", "add_load_effect(member_loads=[{member_id: 2, ...}])", "apply_library_design / apply_template", "calculate"],
            }
