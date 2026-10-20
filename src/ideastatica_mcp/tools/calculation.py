"""Tools: CBFEM calculation (async jobs with progress) and results."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import anyio
from ideastatica_connection_api.exceptions import ApiException, NotFoundException
from mcp.server.mcpserver import Context

from ideastatica_mcp.errors import IdeaError, tool_errors
from ideastatica_mcp.serialize import summarize_check_results, summarize_result_summary, to_jsonable, truncate_text
from ideastatica_mcp.tools.common import DESTRUCTIVE, READ, WRITE, resolve_output_path, sanitize_filename

logger = logging.getLogger(__name__)

TERMINAL_STATES = {"finished", "failed", "cancelled"}


def _status_value(job: Any) -> str:
    status = getattr(job, "status", None)
    return str(getattr(status, "value", status) or "").lower()


def _job_view(job: Any) -> dict:
    data = to_jsonable(job) or {}
    results = getattr(job, "results", None) or []
    return {
        "job_id": data.get("jobId"),
        "status": data.get("status"),
        "progress_percent": data.get("progress"),
        "connections_completed": data.get("connectionsCompleted"),
        "connection_ids": data.get("connectionIds"),
        "current_connection_id": data.get("currentConnectionId"),
        "message": data.get("message"),
        "error": data.get("error"),
        "created_at": data.get("createdAt"),
        "finished_at": data.get("finishedAt"),
        "results": [summarize_result_summary(r) for r in results],
    }


def _report_progress(ctx: Context | None, progress: float, message: str) -> None:
    if ctx is None:
        return
    try:
        anyio.from_thread.run(ctx.report_progress, float(progress), 100.0, message)
    except Exception as exc:  # noqa: BLE001 - progress is best effort
        logger.debug("progress report skipped: %s", exc)


def _is_route_missing(exc: ApiException) -> bool:
    """A bare 404 without ProblemDetails means the async endpoints do not exist on this service."""
    if not isinstance(exc, NotFoundException):
        return False
    body = (exc.body or "").strip()
    return not body or "calculation-jobs" in body or "No route" in body


def select_path(data: Any, path: str) -> Any:
    """Tiny JSON path: dotted keys, [index], and * wildcards; case-insensitive key matching."""
    import re

    tokens = [t for t in re.split(r"\.|(?=\[)", path.strip()) if t]
    current: list[Any] = [data]
    for token in tokens:
        nxt: list[Any] = []
        for item in current:
            if token == "*":
                if isinstance(item, dict):
                    nxt.extend(item.values())
                elif isinstance(item, list):
                    nxt.extend(item)
            elif token.startswith("[") and token.endswith("]"):
                inner = token[1:-1]
                if isinstance(item, list):
                    if inner == "*":
                        nxt.extend(item)
                    else:
                        try:
                            nxt.append(item[int(inner)])
                        except (ValueError, IndexError):
                            pass
            elif isinstance(item, dict):
                if token in item:
                    nxt.append(item[token])
                else:
                    match = next((k for k in item if k.lower() == token.lower()), None)
                    if match is not None:
                        nxt.append(item[match])
        current = nxt
    if not current:
        raise IdeaError(f"path '{path}' matched nothing")
    return current[0] if len(current) == 1 and "*" not in path else current


def _all_connection_ids(session, pid: str) -> list[int]:
    connections = session.connections(pid)
    ids = [int(c.id) for c in connections]
    if not ids:
        raise IdeaError("the project has no connections to calculate")
    return ids


def register(server, session) -> None:
    @server.tool(annotations=WRITE)
    def calculate(
        ctx: Context,
        connection_ids: list[int] | None = None,
        project_id: str | None = None,
        load_effect_ids: list[int] | None = None,
        wait: bool = True,
        timeout_seconds: float = 1800,
    ) -> dict:
        """Run the CBFEM analysis (the same solver as the desktop app) and return pass/fail summaries.

        connection_ids defaults to all connections of the project. load_effect_ids (single
        connection only) restricts the analysis to those load effects. The calculation runs as a
        service job; with wait=true (default) the tool polls it, reports progress and returns
        the per-connection summaries: passed flag plus checks with utilization in percent
        (85.0 means 85 %; ignore entries with skipped=true). With wait=false the job id is
        returned immediately for get_calculation_job. Typical durations: seconds to minutes per
        connection; timeout_seconds cancels the job when exceeded. Detailed numbers:
        get_check_results / get_raw_results.
        """
        with tool_errors("calculate"):
            pid = session.resolve_project(project_id)
            ids = [int(i) for i in connection_ids] if connection_ids else _all_connection_ids(session, pid)
            started = time.monotonic()
            job = None
            try:
                if load_effect_ids:
                    if len(ids) != 1:
                        raise IdeaError("load_effect_ids can only be used with exactly one connection id")
                    job = session.invoke(
                        "calculation_jobs", "start_connection_calculation", pid, ids[0],
                        load_effect_ids=[int(i) for i in load_effect_ids],
                    )
                else:
                    job = session.invoke("calculation_jobs", "start_calculation", pid, ids)
            except ApiException as exc:
                if not _is_route_missing(exc):
                    raise
                logger.info("Async calculation endpoints unavailable; using synchronous calculate")

            if job is None:
                _report_progress(ctx, 0, f"Calculating {len(ids)} connection(s) (synchronous)")
                if load_effect_ids:
                    summaries = [session.invoke("calculation", "calculate_connection", pid, ids[0], load_effect_ids=[int(i) for i in load_effect_ids])]
                else:
                    summaries = session.invoke("calculation", "calculate", pid, ids) or []
                results = [summarize_result_summary(s) for s in summaries]
                return {
                    "project_id": pid,
                    "connection_ids": ids,
                    "mode": "synchronous",
                    "duration_seconds": round(time.monotonic() - started, 1),
                    "all_passed": all(r.get("passed") for r in results) if results else None,
                    "results": results,
                }

            job_id = job.job_id
            if not wait:
                return {"project_id": pid, "connection_ids": ids, "mode": "job", "job": _job_view(job)}

            _report_progress(ctx, 0, f"Job {job_id} queued for {len(ids)} connection(s)")
            last_key: tuple = ()
            interval = 0.25  # fast first polls: small connections finish in a few seconds
            while _status_value(job) not in TERMINAL_STATES:
                if time.monotonic() - started > timeout_seconds:
                    try:
                        session.invoke("calculation_jobs", "cancel_calculation_job", pid, job_id)
                    finally:
                        raise IdeaError(f"calculation job {job_id} exceeded {timeout_seconds:.0f}s and was cancelled")
                time.sleep(interval)
                interval = min(interval * 1.4, 2.0)
                job = session.invoke("calculation_jobs", "get_calculation_job", pid, job_id)
                key = (_status_value(job), job.progress, job.connections_completed, job.current_connection_id)
                if key != last_key:
                    last_key = key
                    done = job.connections_completed or 0
                    message = f"{_status_value(job)}: {done}/{len(ids)} connections"
                    if job.current_connection_id is not None:
                        message += f", working on connection {job.current_connection_id}"
                    _report_progress(ctx, job.progress or (100.0 * done / max(len(ids), 1)), message)

            view = _job_view(job)
            view["duration_seconds"] = round(time.monotonic() - started, 1)
            results = view["results"]
            view["all_passed"] = all(r.get("passed") for r in results) if results else None
            view["project_id"] = pid
            view["mode"] = "job"
            status = _status_value(job)
            if status == "failed":
                view["warning"] = f"calculation job failed: {job.error or job.message or 'no details'}"
            elif status == "cancelled":
                view["warning"] = "calculation job was cancelled"
            return view

    @server.tool(annotations=READ)
    def get_calculation_job(job_id: str, project_id: str | None = None) -> dict:
        """Poll an asynchronous calculation job started with calculate(wait=false): status, progress, results."""
        with tool_errors("get_calculation_job"):
            pid = session.resolve_project(project_id)
            job = session.invoke("calculation_jobs", "get_calculation_job", pid, job_id)
            return {"project_id": pid, "job": _job_view(job)}

    @server.tool(annotations=DESTRUCTIVE)
    def cancel_calculation_job(job_id: str, project_id: str | None = None) -> dict:
        """Cancel a running calculation job (finished connections keep their results)."""
        with tool_errors("cancel_calculation_job"):
            pid = session.resolve_project(project_id)
            job = session.invoke("calculation_jobs", "cancel_calculation_job", pid, job_id)
            return {"project_id": pid, "job": _job_view(job)}

    @server.tool(annotations=READ)
    def get_check_results(
        connection_ids: list[int] | None = None, project_id: str | None = None, summary_only: bool = False
    ) -> dict:
        """Detailed check results of calculated connections: summary checks plus per-plate, weld, bolt, anchor, concrete block and buckling results.

        Requires a previous calculate. Utilizations are percentages; plates report max strain
        (fraction, e.g. 0.05 = 5 %) and max stress (Pa). connection_ids defaults to all
        connections. summary_only=true returns just the summary rows.
        """
        with tool_errors("get_check_results"):
            pid = session.resolve_project(project_id)
            ids = [int(i) for i in connection_ids] if connection_ids else _all_connection_ids(session, pid)
            results = session.invoke("calculation", "get_results", pid, ids) or []
            out = []
            for item in results:
                summary = summarize_check_results(item)
                if summary_only:
                    summary = {k: summary[k] for k in ("connection_id", "name", "summary", "messages")}
                out.append(summary)
            return {"project_id": pid, "connection_ids": ids, "results": out}

    @server.tool(annotations=READ)
    def get_raw_results(
        connection_id: int | None = None,
        project_id: str | None = None,
        keys: list[str] | None = None,
        path: str | None = None,
        save_to_file: str | None = None,
        max_chars: int | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Raw CBFEM results of one connection (the full CheckResultsData JSON of the solver).

        The document is large; the response is truncated to max_chars (default from settings).
        keys selects top-level sections (call once without keys to see the section names);
        path drills into the document with a dotted path such as "plates[0].items" or
        "welds.*.name" (wildcard * over lists/dicts). save_to_file writes the complete JSON.
        """
        with tool_errors("get_raw_results"):
            pid = session.resolve_project(project_id)
            cid = session.resolve_connection(pid, connection_id)
            text = session.invoke("calculation", "get_connection_raw_json_results", pid, cid)
            if not text:
                raise IdeaError("the service returned no raw results - calculate the connection first")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            saved_to = None
            if save_to_file:
                target = resolve_output_path(
                    session.settings, save_to_file, sanitize_filename(f"raw-results-{cid}") + ".json", overwrite
                )
                target.write_text(text, encoding="utf-8")
                saved_to = str(target)
            top_level = sorted(data.keys()) if isinstance(data, dict) else None
            selected: Any = data
            missing: list[str] = []
            if keys and isinstance(data, dict):
                selected = {}
                for key in keys:
                    if key in data:
                        selected[key] = data[key]
                    else:
                        missing.append(key)
            if path and data is not None:
                selected = select_path(selected if keys else data, path)
            limit = max_chars or session.settings.max_text_chars
            payload = json.dumps(selected, ensure_ascii=False) if selected is not None else text
            shown, truncated = truncate_text(payload, limit)
            return {
                "connection_id": cid,
                "total_chars": len(text),
                "top_level_keys": top_level,
                "missing_keys": missing,
                "saved_to": saved_to,
                "truncated": truncated,
                "data": shown,
            }

    _ = Path  # keep import for type checkers
