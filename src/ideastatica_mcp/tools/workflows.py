"""Tools: engineering workflows that bundle several API calls into one step."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

import anyio
from ideastatica_connection_api.models.con_weld_sizing_method_enum import ConWeldSizingMethodEnum
from ideastatica_connection_api.models.idea_parameter_update import IdeaParameterUpdate
from mcp.server.mcpserver import Context

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import compact_parameter, summarize_result_summary, to_jsonable
from ideastatica_mcp.tools.common import READ, WRITE, enum_value, resolve_output_path, sanitize_filename, to_expression

TERMINAL = {"finished", "failed", "cancelled"}


def _progress(ctx: Context | None, value: float, message: str) -> None:
    if ctx is None:
        return
    try:
        anyio.from_thread.run(ctx.report_progress, float(value), 100.0, message)
    except Exception:  # noqa: BLE001
        pass


def run_calculation(session, pid: str, ids: list[int], timeout_s: float = 1800) -> list[dict]:
    """Calculate connections (async job, synchronous fallback) and return compact summaries."""
    from ideastatica_connection_api.exceptions import ApiException, NotFoundException

    started = time.monotonic()
    try:
        job = session.invoke("calculation_jobs", "start_calculation", pid, ids)
    except ApiException as exc:
        if not isinstance(exc, NotFoundException) or (exc.body or "").strip():
            raise
        summaries = session.invoke("calculation", "calculate", pid, ids) or []
        return [summarize_result_summary(s) for s in summaries]
    interval = 0.25
    while str(getattr(job.status, "value", job.status) or "").lower() not in TERMINAL:
        if time.monotonic() - started > timeout_s:
            session.invoke("calculation_jobs", "cancel_calculation_job", pid, job.job_id)
            raise IdeaError(f"calculation exceeded {timeout_s:.0f}s and was cancelled")
        time.sleep(interval)
        interval = min(interval * 1.4, 2.0)
        job = session.invoke("calculation_jobs", "get_calculation_job", pid, job.job_id)
    status = str(getattr(job.status, "value", job.status) or "").lower()
    if status != "finished":
        raise IdeaError(f"calculation {status}: {job.error or job.message or 'no details'}")
    return [summarize_result_summary(r) for r in (job.results or [])]


def governing(checks: list[dict]) -> dict | None:
    valid = [c for c in checks if not c.get("skipped") and c.get("utilization_percent") is not None and c.get("name") != "Analysis"]
    if not valid:
        return None
    return max(valid, key=lambda c: c["utilization_percent"])


def summarize_connection_result(result: dict) -> dict:
    checks = result.get("checks") or []
    gov = governing(checks)
    failed = [c["name"] for c in checks if not c.get("skipped") and c.get("passed") is False]
    return {
        "connection_id": result.get("connection_id"),
        "passed": result.get("passed"),
        "governing_check": gov.get("name") if gov else None,
        "max_utilization_percent": gov.get("utilization_percent") if gov else None,
        "governing_load_case_id": gov.get("load_case_id") if gov else None,
        "failed_checks": failed,
        "utilizations": {c["name"]: c.get("utilization_percent") for c in checks if not c.get("skipped")},
    }


def register(server, session) -> None:
    @server.tool(annotations=WRITE)
    def optimize_parameter(
        ctx: Context,
        parameter_key: str,
        values: list[str | float | int],
        connection_id: int | None = None,
        project_id: str | None = None,
        restore: bool = True,
        stop_at_first_pass: bool = False,
        save_csv: str | None = None,
    ) -> dict:
        """Parametric sweep in one call: set the parameter to each value, recalculate, collect pass/fail and utilizations.

        values are expressions in SI units (e.g. [0.01, 0.012, 0.015] for plate thickness in m,
        or ["'M20 8.8'", "'M24 8.8'"] for strings). restore=true puts the original expression
        back at the end; stop_at_first_pass=true stops when a value passes (values should then be
        ordered from cheapest to strongest). Returns a comparison table and the most economical
        passing value. save_csv optionally writes the table.
        """
        with tool_errors("optimize_parameter"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            params = session.invoke("parameter", "get_parameters", pid, cid, include_hidden=True) or []
            current = next((p for p in params if p.key == parameter_key), None)
            if current is None:
                keys = ", ".join(p.key for p in params) or "none"
                raise IdeaError(f"parameter '{parameter_key}' not found (available: {keys})")
            original = current.expression
            rows: list[dict] = []
            best = None
            try:
                for i, value in enumerate(values):
                    expression = to_expression(value)
                    _progress(ctx, 100.0 * i / max(len(values), 1), f"{parameter_key} = {expression}")
                    response = session.invoke("parameter", "update", pid, cid, idea_parameter_update=[IdeaParameterUpdate(key=parameter_key, expression=expression)])
                    failed = to_jsonable(getattr(response, "failed_validations", None)) or []
                    if failed:
                        rows.append({"value": expression, "error": failed[0].get("message") or "validation failed"})
                        continue
                    result = run_calculation(session, pid, [cid])[0]
                    row = {"value": expression, **summarize_connection_result(result)}
                    rows.append(row)
                    if row["passed"] and best is None:
                        best = row
                        if stop_at_first_pass:
                            break
            finally:
                if restore and original is not None:
                    session.invoke("parameter", "update", pid, cid, idea_parameter_update=[IdeaParameterUpdate(key=parameter_key, expression=original)])
            saved = None
            if save_csv:
                target = resolve_output_path(session.settings, save_csv, sanitize_filename(f"sweep-{parameter_key}") + ".csv", True)
                fields = ["value", "passed", "governing_check", "max_utilization_percent", "failed_checks", "error"]
                with target.open("w", newline="", encoding="utf-8") as fh:
                    writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({**row, "failed_checks": ";".join(row.get("failed_checks") or [])})
                saved = str(target)
            return {
                "connection_id": cid,
                "parameter_key": parameter_key,
                "original_expression": original,
                "restored": restore,
                "rows": rows,
                "most_economical_passing_value": best["value"] if best else None,
                "csv": saved,
            }

    @server.tool(annotations=READ)
    def design_check_summary(connection_ids: list[int] | None = None, project_id: str | None = None, recalculate: bool = False) -> dict:
        """One-glance status of the connections: pass/fail, governing check, max utilization, failed checks.

        Uses the last results (recalculate=true runs the analysis first). Ideal as the final
        answer after modifications.
        """
        with tool_errors("design_check_summary"):
            pid = session.resolve_project(project_id)
            ids = [int(i) for i in connection_ids] if connection_ids else [int(c.id) for c in session.connections(pid)]
            if recalculate:
                results = run_calculation(session, pid, ids)
            else:
                raw = session.invoke("calculation", "get_results", pid, ids) or []
                results = []
                for item in raw:
                    data = to_jsonable(item) or {}
                    checks = [
                        {"name": c.get("name"), "utilization_percent": c.get("checkValue"), "passed": c.get("checkStatus"), "skipped": c.get("skipped"), "load_case_id": c.get("loadCaseId")}
                        for c in data.get("checkResSummary") or []
                    ]
                    passed = all(c["passed"] for c in checks if not c.get("skipped")) if checks else None
                    results.append({"connection_id": data.get("id"), "passed": passed, "checks": checks})
            rows = [summarize_connection_result(r) for r in results]
            return {"project_id": pid, "all_passed": all(r["passed"] for r in rows) if rows else None, "connections": rows}

    @server.tool(annotations=WRITE)
    def auto_weld_sizing(ctx: Context, method: str = "fullStrength", connection_id: int | None = None, project_id: str | None = None) -> dict:
        """Size the welds (weld pre-design), recalculate and report the resulting utilizations in one step.

        method: fullStrength (default), minimumDuctility, overStrengthFactor, capacityEstimation.
        """
        with tool_errors("auto_weld_sizing"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            design_type = enum_value(ConWeldSizingMethodEnum, method, "method")
            _progress(ctx, 10, "sizing welds")
            sizing = session.invoke("operation", "pre_design_welds", pid, cid, design_type=design_type)
            try:
                sizing = json.loads(sizing) if isinstance(sizing, str) else sizing
            except json.JSONDecodeError:
                pass
            _progress(ctx, 50, "recalculating")
            result = run_calculation(session, pid, [cid])[0]
            return {"connection_id": cid, "method": design_type.value, "sizing": sizing, "result": summarize_connection_result(result)}

    @server.tool(annotations=WRITE)
    def batch_calculate(
        ctx: Context,
        folder: str,
        pattern: str = "*.ideaCon",
        report_csv: str | None = None,
        save_calculated: bool = False,
        timeout_per_project_seconds: float = 1800,
    ) -> dict:
        """Calculate every project file in a folder and return one table (file, connection, pass/fail, governing check).

        Each project is opened, calculated, summarized and closed; save_calculated=true also
        writes the calculated copies to the output folder. report_csv writes the table.
        """
        with tool_errors("batch_calculate"):
            root = Path(folder).expanduser()
            if not root.is_dir():
                raise IdeaError(f"folder not found: {root}")
            files = sorted(root.glob(pattern))
            if not files:
                raise IdeaError(f"no files matching {pattern} in {root}")
            rows: list[dict] = []
            for index, file in enumerate(files):
                _progress(ctx, 100.0 * index / len(files), f"{file.name}")
                pid = None
                try:
                    project = session.invoke("project", "open_project", idea_con_file=file.read_bytes(), _content_type="multipart/form-data")
                    pid = str(project.project_id)
                    ids = [int(c.id) for c in (project.connections or [])]
                    results = run_calculation(session, pid, ids, timeout_per_project_seconds) if ids else []
                    for r in results:
                        rows.append({"file": file.name, **summarize_connection_result(r)})
                    if save_calculated:
                        target = resolve_output_path(session.settings, None, f"{file.stem}-calculated.ideaCon", True)
                        target.write_bytes(session.invoke("project", "download_project_with_http_info", pid).raw_data)
                except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
                    rows.append({"file": file.name, "error": str(exc)[:300]})
                finally:
                    if pid:
                        try:
                            session.invoke("project", "close_project", pid)
                        except Exception:  # noqa: BLE001
                            pass
            saved = None
            if report_csv:
                target = resolve_output_path(session.settings, report_csv, "batch-results.csv", True)
                fields = ["file", "connection_id", "passed", "governing_check", "max_utilization_percent", "failed_checks", "error"]
                with target.open("w", newline="", encoding="utf-8") as fh:
                    writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({**row, "failed_checks": ";".join(row.get("failed_checks") or [])})
                saved = str(target)
            return {"folder": str(root), "files": len(files), "rows": rows, "csv": saved}

    @server.tool(annotations=READ)
    def score_library_designs(
        connection_id: int | None = None,
        project_id: str | None = None,
        prefer_bolted: bool | None = None,
        prefer_welded: bool | None = None,
        moment_connection: bool | None = None,
        max_results: int = 10,
    ) -> dict:
        """Propose Connection Library designs for the connection and rank them by the stated preferences.

        Scoring: +2 when the design matches the requested fastening (bolted/welded) or behaviour
        (moment vs shear), +1 for the predefined set, +1 for parametric designs. Returns the
        ranked list with ids for apply_library_design.
        """
        with tool_errors("score_library_designs"):
            from ideastatica_connection_api.models.con_connection_library_search_parameters import ConConnectionLibrarySearchParameters

            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            params = ConConnectionLibrarySearchParameters(in_predefined_set=True, in_company_set=True, in_personal_set=True)
            items = session.invoke("connection_library", "propose", pid, cid, con_connection_library_search_parameters=params) or []
            sets = {s.id: (s.name, s.type) for s in (session.invoke("connection_library", "get_design_sets") or [])}
            ranked = []
            for item in items:
                data = to_jsonable(item) or {}
                name = (data.get("name") or "").lower()
                score = 0
                reasons = []
                if prefer_bolted and ("bolt" in name or "end plate" in name or "fin plate" in name):
                    score += 2; reasons.append("bolted")
                if prefer_welded and "weld" in name:
                    score += 2; reasons.append("welded")
                if moment_connection is True and ("moment" in name or "end plate" in name or "haunch" in name):
                    score += 2; reasons.append("moment")
                if moment_connection is False and ("shear" in name or "fin plate" in name or "cleat" in name):
                    score += 2; reasons.append("shear")
                set_name, set_type = sets.get(data.get("conDesignSetId"), (None, None))
                if set_type and "predef" in str(set_type).lower():
                    score += 1; reasons.append("predefined set")
                if "param" in name:
                    score += 1; reasons.append("parametric")
                ranked.append({**data, "designSetName": set_name, "score": score, "reasons": reasons})
            ranked.sort(key=lambda r: r["score"], reverse=True)
            return {"connection_id": cid, "count": len(ranked), "designs": ranked[: max(1, int(max_results))]}

    _ = compact_parameter
