"""Tools: images for Claude - 3D scene rendering and report page previews."""

from __future__ import annotations

import base64
import json
import math
import time
from pathlib import Path

from mcp.server.mcpserver import Image
from mcp_types import ImageContent, TextContent

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import to_jsonable
from ideastatica_mcp.tools.common import READ, WRITE, resolve_output_path, sanitize_filename

PALETTE = [(70, 120, 200), (200, 120, 70), (90, 170, 110), (170, 90, 170), (200, 170, 60), (90, 160, 190), (150, 150, 150)]


def _project(x: float, y: float, z: float, azimuth: float, elevation: float) -> tuple[float, float, float]:
    """Rotate a point for an isometric-like view; returns (screen_x, screen_y, depth)."""
    ca, sa = math.cos(azimuth), math.sin(azimuth)
    ce, se = math.cos(elevation), math.sin(elevation)
    x1 = ca * x - sa * y
    y1 = sa * x + ca * y
    y2 = ce * y1 - se * z
    z2 = se * y1 + ce * z
    return x1, -z2, y2


def heat_color(ratio: float | None) -> tuple[int, int, int]:
    """0 -> green, 0.5 -> yellow, 1 -> red, >1 -> dark red; None -> grey."""
    if ratio is None:
        return (170, 170, 170)
    r = max(0.0, min(ratio, 1.2))
    if r <= 0.5:
        return (int(60 + 390 * r), 200, 60)
    if r <= 1.0:
        return (255, int(200 - 300 * (r - 0.5)), 60)
    return (150, 20, 20)


def render_scene_png(
    scene: dict,
    width: int = 1200,
    height: int = 900,
    azimuth_deg: float = 35.0,
    elevation_deg: float = 25.0,
    color_for_group=None,
    legend: list[tuple[str, tuple[int, int, int]]] | None = None,
) -> bytes:
    from PIL import Image as PILImage, ImageDraw

    vertices = scene.get("vertices") or scene.get("Vertices") or []
    groups = scene.get("groups") or scene.get("Groups") or []
    if len(vertices) < 9:
        raise IdeaError("the scene has no geometry")
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    points = [_project(vertices[i], vertices[i + 1], vertices[i + 2], az, el) for i in range(0, len(vertices) - 2, 3)]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y, 1e-9)
    margin = 40
    scale = (min(width, height) - 2 * margin) / span
    off_x = (width - (max_x - min_x) * scale) / 2 - min_x * scale
    off_y = (height - (max_y - min_y) * scale) / 2 - min_y * scale

    def screen(index: int) -> tuple[float, float]:
        p = points[index]
        return p[0] * scale + off_x, p[1] * scale + off_y

    triangles: list[tuple[float, tuple, tuple]] = []
    for gi, group in enumerate(groups):
        faces = group.get("faces") or group.get("Faces") or []
        color = PALETTE[gi % len(PALETTE)]
        if color_for_group is not None:
            custom = color_for_group(group)
            if custom is not None:
                color = custom
        for i in range(0, len(faces) - 2, 3):
            a, b, c = faces[i], faces[i + 1], faces[i + 2]
            if max(a, b, c) >= len(points):
                continue
            depth = (points[a][2] + points[b][2] + points[c][2]) / 3
            # simple shading from the triangle normal
            ax, ay = screen(a)
            bx, by = screen(b)
            cx, cy = screen(c)
            n = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            shade = 0.75 + 0.25 * (1 if n > 0 else -1) * 0.5
            shaded = tuple(max(0, min(255, int(ch * shade))) for ch in color)
            triangles.append((depth, ((ax, ay), (bx, by), (cx, cy)), shaded))
    triangles.sort(key=lambda t: t[0], reverse=True)
    image = PILImage.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for _, poly, color in triangles:
        draw.polygon(poly, fill=color, outline=(60, 60, 60))
    for gi, group in enumerate(groups):
        for line in group.get("lines") or group.get("Lines") or []:
            pairs = line.get("pairs") or []
            for i in range(0, len(pairs) - 1, 2):
                if max(pairs[i], pairs[i + 1]) < len(points):
                    draw.line([screen(pairs[i]), screen(pairs[i + 1])], fill=(20, 20, 20), width=1)
    if legend:
        y = 10
        for label, color in legend:
            draw.rectangle([10, y, 30, y + 14], fill=color, outline=(40, 40, 40))
            draw.text((36, y), label, fill=(20, 20, 20))
            y += 20
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def utilization_lookup(check_res: dict, raw: dict | None, strain_limit: float) -> tuple[dict, list[str]]:
    """Map scene selection (kind, id) -> utilization ratio from the detailed check results."""
    lookup: dict[tuple[str, str], float] = {}
    notes: list[str] = []
    for weld in check_res.get("checkResWeld") or []:
        if weld.get("id") is not None and weld.get("unityCheck") is not None:
            lookup[("weld", str(weld["id"]))] = float(weld["unityCheck"])
    for plate in check_res.get("checkResPlate") or []:
        strain = plate.get("maxStrain")
        ratio = float(strain) / strain_limit if strain is not None and strain_limit else None
        for item in plate.get("items") or []:
            if ratio is not None:
                lookup[("plate", str(item))] = max(lookup.get(("plate", str(item)), 0.0), ratio)
    bolts = [b.get("unityCheck") for b in (check_res.get("checkResBolt") or []) if b.get("unityCheck") is not None]
    if bolts:
        lookup[("boltGrid", "*")] = max(float(b) for b in bolts)
        notes.append(f"bolt grids coloured by the maximum bolt utilization {max(bolts):.0%}")
    if raw:
        for bolt in raw.get("bolts") or []:
            grid = bolt.get("boltGridId", bolt.get("gridId"))
            value = bolt.get("unityCheck", bolt.get("checkValue"))
            if grid is not None and value is not None:
                key = ("boltGrid", str(grid))
                lookup[key] = max(lookup.get(key, 0.0), float(value) / (100.0 if float(value) > 2 else 1.0))
    return lookup, notes


def register(server, session) -> None:
    @server.tool(annotations=READ)
    def render_connection_image(
        connection_id: int | None = None,
        project_id: str | None = None,
        azimuth_deg: float = 35.0,
        elevation_deg: float = 25.0,
        width: int = 1200,
        height: int = 900,
        file_path: str | None = None,
        overwrite: bool = True,
    ) -> list:
        """Render the connection's 3D model (plates, members, bolts, welds) to a PNG image and return it.

        The image is also saved to the output folder. azimuth/elevation rotate the view; call
        twice with different angles for a second viewpoint.
        """
        with tool_errors("render_connection_image"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            text = session.invoke("presentation", "get_data_scene3_d_text", pid, cid)
            scene = json.loads(text)
            png = render_scene_png(scene, int(width), int(height), float(azimuth_deg), float(elevation_deg))
            target = resolve_output_path(session.settings, file_path, sanitize_filename(f"connection-{cid}-view") + ".png", overwrite)
            target.write_bytes(png)
            return [
                TextContent(type="text", text=json.dumps({"connection_id": cid, "file_path": str(target), "size_bytes": len(png)})),
                ImageContent(type="image", data=base64.b64encode(png).decode("ascii"), mime_type="image/png"),
            ]

    @server.tool(annotations=READ)
    def render_utilization_map(
        connection_id: int | None = None,
        project_id: str | None = None,
        azimuth_deg: float = 35.0,
        elevation_deg: float = 25.0,
        width: int = 1200,
        height: int = 900,
        strain_limit: float = 0.05,
        file_path: str | None = None,
    ) -> list:
        """Render the connection with parts coloured by their check utilization (green < 50 %, yellow, red >= 100 %).

        Welds use their unity check, plates their plastic strain relative to strain_limit (5 % by
        default), bolt grids the maximum bolt utilization; members stay grey. Requires a previous
        calculate. Returns the image and the utilization table used for colouring.
        """
        with tool_errors("render_utilization_map"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            results = session.invoke("calculation", "get_results", pid, [cid]) or []
            if not results:
                raise IdeaError("no results - run calculate first")
            check_res = to_jsonable(results[0]) or {}
            lookup, notes = utilization_lookup(check_res, None, float(strain_limit))
            scene = json.loads(session.invoke("presentation", "get_data_scene3_d_text", pid, cid))

            def color_for(group: dict):
                sel = group.get("selected") or {}
                kind = ((sel.get("type") or {}).get("kind") or "").strip()
                ident = str(sel.get("id"))
                if kind == "member":
                    return (190, 190, 200)
                ratio = lookup.get((kind, ident))
                if ratio is None and kind == "boltGrid":
                    ratio = lookup.get(("boltGrid", "*"))
                return heat_color(ratio) if ratio is not None else None

            legend = [("< 50 %", heat_color(0.2)), ("50-90 %", heat_color(0.7)), ("90-100 %", heat_color(0.95)), ("> 100 %", heat_color(1.1)), ("member / n.a.", (190, 190, 200))]
            png = render_scene_png(scene, int(width), int(height), float(azimuth_deg), float(elevation_deg), color_for, legend)
            target = resolve_output_path(session.settings, file_path, sanitize_filename(f"connection-{cid}-utilization") + ".png", True)
            target.write_bytes(png)
            table = sorted(({"kind": k, "id": i, "utilization_percent": round(v * 100, 1)} for (k, i), v in lookup.items() if i != "*"), key=lambda r: -r["utilization_percent"])
            return [
                TextContent(type="text", text=json.dumps({"connection_id": cid, "file_path": str(target), "notes": notes, "parts": table[:40]})),
                ImageContent(type="image", data=base64.b64encode(png).decode("ascii"), mime_type="image/png"),
            ]

    @server.tool(annotations=READ)
    def get_result_tables(connection_id: int | None = None, project_id: str | None = None, max_rows: int = 40) -> dict:
        """Compact tables from the detailed results: plates (max strain/stress), welds (utilization, full strength), bolts, anchors, plus the bolt force rows of the raw results when present."""
        with tool_errors("get_result_tables"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            results = session.invoke("calculation", "get_results", pid, [cid]) or []
            if not results:
                raise IdeaError("no results - run calculate first")
            data = to_jsonable(results[0]) or {}
            plates = [{"name": p.get("name"), "max_strain_percent": round(100 * (p.get("maxStrain") or 0), 2), "max_stress_MPa": round((p.get("maxStress") or 0) / 1e6, 1), "passed": p.get("checkStatus"), "load_case_id": p.get("loadCaseId")} for p in data.get("checkResPlate") or []]
            welds = [{"name": w.get("name"), "id": w.get("id"), "utilization_percent": round(100 * (w.get("unityCheck") or 0), 1), "passed": w.get("checkStatus"), "full_strength": w.get("isFullStrength"), "load_case_id": w.get("loadCaseId")} for w in data.get("checkResWeld") or []]
            bolts = [{"name": b.get("name"), "utilization_percent": round(100 * (b.get("unityCheck") or 0), 1), "passed": b.get("checkStatus")} for b in data.get("checkResBolt") or []]
            anchors = [{"name": a.get("name"), "utilization_percent": round(100 * (a.get("unityCheck") or 0), 1), "passed": a.get("checkStatus")} for a in data.get("checkResAnchor") or []]
            raw_bolts = []
            try:
                raw = json.loads(session.invoke("calculation", "get_connection_raw_json_results", pid, cid) or "{}")
                for b in (raw.get("bolts") or [])[: max(1, int(max_rows))]:
                    if isinstance(b, dict):
                        raw_bolts.append({k: b[k] for k in list(b)[:12] if not isinstance(b[k], (dict, list))})
            except Exception:  # noqa: BLE001
                pass
            plates.sort(key=lambda r: -r["max_strain_percent"])
            welds.sort(key=lambda r: -r["utilization_percent"])
            bolts.sort(key=lambda r: -r["utilization_percent"])
            return {"connection_id": cid, "plates": plates[:max_rows], "welds": welds[:max_rows], "bolts": bolts[:max_rows], "anchors": anchors[:max_rows], "raw_bolt_rows": raw_bolts}

    @server.tool(annotations=WRITE)
    def compile_summary_report(
        title: str,
        findings_markdown: str,
        connection_ids: list[int] | None = None,
        project_id: str | None = None,
        include_images: bool = True,
        file_path: str | None = None,
    ) -> dict:
        """Compile an HTML summary report: title, Claude's findings (markdown-ish text), check tables per connection, rendered model and utilization images, project metadata.

        Use at the end of a study to hand over one self-contained file (images embedded).
        """
        with tool_errors("compile_summary_report"):
            import html as html_mod

            pid = session.resolve_project(project_id)
            ids = [int(i) for i in connection_ids] if connection_ids else [int(c.id) for c in session.connections(pid)]
            project = to_jsonable(session.invoke("project", "get_project_data", pid)) or {}
            sections = []
            for cid in ids:
                rows_html = ""
                try:
                    res = to_jsonable((session.invoke("calculation", "get_results", pid, [cid]) or [None])[0]) or {}
                    checks = res.get("checkResSummary") or []
                    rows_html = "".join(
                        f"<tr><td>{html_mod.escape(str(c.get('name')))}</td><td>{c.get('checkValue'):.1f}</td><td>{'OK' if c.get('checkStatus') else 'FAIL'}</td></tr>"
                        for c in checks if not c.get("skipped") and c.get("checkValue") is not None
                    )
                except Exception as exc:  # noqa: BLE001
                    rows_html = f"<tr><td colspan='3'>no results: {html_mod.escape(str(exc)[:120])}</td></tr>"
                images = ""
                if include_images:
                    try:
                        scene = json.loads(session.invoke("presentation", "get_data_scene3_d_text", pid, cid))
                        png = render_scene_png(scene, 900, 650)
                        images += f"<img src='data:image/png;base64,{base64.b64encode(png).decode()}' style='max-width:100%'>"
                    except Exception:  # noqa: BLE001
                        pass
                sections.append(f"<h2>Connection {cid}</h2><table border='1' cellpadding='4'><tr><th>Check</th><th>Utilization %</th><th>Status</th></tr>{rows_html}</table>{images}")
            body = html_mod.escape(findings_markdown).replace("\n", "<br>")
            info = project.get("projectInfo") or {}
            html = (
                "<!doctype html><html><head><meta charset='utf-8'><title>" + html_mod.escape(title) + "</title>"
                "<style>body{font-family:Segoe UI,Arial;margin:32px;max-width:1000px}table{border-collapse:collapse}th{background:#eee}</style></head><body>"
                f"<h1>{html_mod.escape(title)}</h1><p><b>Project:</b> {html_mod.escape(str(info.get('name') or ''))} &nbsp; <b>Code:</b> {html_mod.escape(str(info.get('countryCode') or ''))} &nbsp; <b>Generated:</b> {time.strftime('%Y-%m-%d %H:%M')}</p>"
                f"<h2>Findings</h2><p>{body}</p>{''.join(sections)}</body></html>"
            )
            target = resolve_output_path(session.settings, file_path, sanitize_filename(title) + ".html", True)
            target.write_text(html, encoding="utf-8")
            return {"file_path": str(target), "connections": ids, "size_bytes": len(html)}

    @server.tool(annotations=READ)
    def render_report_pages(
        connection_id: int | None = None,
        project_id: str | None = None,
        pages: list[int] | None = None,
        dpi: int = 90,
        pdf_path: str | None = None,
    ) -> list:
        """Generate (or reuse) the PDF report of a connection and return the requested pages as images.

        pages defaults to [1, 2]; page numbers are 1-based. pdf_path reuses an existing report
        instead of generating a new one. Images are saved next to the PDF.
        """
        with tool_errors("render_report_pages"):
            try:
                import fitz  # PyMuPDF
            except ImportError as exc:  # pragma: no cover
                raise IdeaError("PyMuPDF is not installed (pip install pymupdf)") from exc
            if pdf_path:
                pdf = Path(pdf_path).expanduser()
                if not pdf.is_file():
                    raise IdeaError(f"PDF not found: {pdf}")
                cid = connection_id
            else:
                pid = session.resolve_project(project_id)
                cid = session.resolve_connection(pid, connection_id)
                response = session.invoke("report", "generate_pdf_with_http_info", pid, cid)
                pdf = resolve_output_path(session.settings, None, sanitize_filename(f"connection-{cid}-report") + ".pdf", True)
                pdf.write_bytes(response.raw_data)
            doc = fitz.open(str(pdf))
            wanted = pages or [1, 2]
            contents: list = [TextContent(type="text", text=json.dumps({"pdf": str(pdf), "page_count": doc.page_count, "pages": wanted}))]
            for number in wanted:
                if number < 1 or number > doc.page_count:
                    continue
                pix = doc[number - 1].get_pixmap(dpi=int(dpi))
                png = pix.tobytes("png")
                out = pdf.with_name(f"{pdf.stem}-page{number}.png")
                out.write_bytes(png)
                contents.append(ImageContent(type="image", data=base64.b64encode(png).decode("ascii"), mime_type="image/png"))
            doc.close()
            return contents

    _ = Image
