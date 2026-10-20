"""MCP server assembly: logging, session, tools, resources, prompts."""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from mcp.server.mcpserver import MCPServer

from ideastatica_mcp import __version__
from ideastatica_mcp.config import Settings
from ideastatica_mcp.prompts import register as register_prompts
from ideastatica_mcp.service import ServiceManager
from ideastatica_mcp.session import IdeaSession
from ideastatica_mcp.tools import register_all

RESOURCE_DIR = Path(__file__).parent / "resources"

INSTRUCTIONS = """\
You are connected to IDEA StatiCa Connection (steel connection design, CBFEM analysis) through its
local REST API service. Version: IDEA StatiCa 26.0, REST API 4.0.

Workflow: service_status (optional) -> open_project(file_path) -> list_connections -> inspect
(list_members, list_operations, list_parameters, list_load_effects, list_project_materials) ->
modify (update_parameters, add/update_load_effect, apply_template / apply_library_design,
update_member, update_project_settings) -> calculate -> get_check_results / get_raw_results ->
generate_report / export_connection -> save_project. Projects are server-side state: they stay
open until close_project, and nothing is written to disk until save_project.

Conventions:
- project_id defaults to the last opened project; connection_id defaults to the only connection.
- Parameter expressions are strings in SI units (m, N, Pa, rad): 15 mm -> "0.015". Strings in
  single quotes ('M20 8.8'), references as [otherKey]. Read ideastatica://docs/parameter-reference
  for the expression functions. Always list_parameters before update_parameters.
- Load effects are in SI too: forces in N, moments in Nm (120 kN -> 120000). Check
  utilizations are already percentages (85.0 = 85 %); skip checks flagged skipped.
- Geometry (plates, bolts, welds, cuts) cannot be created one by one through the API: use
  templates, the Connection Library (propose -> apply) or IOM import, then drive dimensions via
  parameters.
- Generated files go to the output folder returned in each result; existing files are not
  overwritten unless overwrite=true.
- The service needs an IDEA StatiCa licence seat and is pre-started in the background when this
  server launches; if a tool says it is "still starting", retry after a short while. If a tool
  reports the service unreachable, call service_status and service_start.
- Speed: independent read tools (list_members, list_operations, list_parameters,
  list_load_effects, get_project_settings ...) can be called in parallel in one turn; omit
  project_id / connection_id when they default; prefer compact outputs (verbose=false) and
  keys= filters on get_raw_results.
"""


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not any(getattr(h, "_ideastatica_stderr", False) for h in root.handlers):
        stderr_handler = logging.StreamHandler(sys.stderr)  # never stdout: it carries the MCP protocol
        stderr_handler.setFormatter(fmt)
        stderr_handler._ideastatica_stderr = True  # type: ignore[attr-defined]
        root.addHandler(stderr_handler)

    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        if not any(getattr(h, "_ideastatica_file", False) for h in root.handlers):
            file_handler = logging.handlers.RotatingFileHandler(
                settings.log_dir / "ideastatica-mcp.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(fmt)
            file_handler._ideastatica_file = True  # type: ignore[attr-defined]
            root.addHandler(file_handler)
    except OSError:  # pragma: no cover - read-only locations
        pass

    logging.getLogger("urllib3").setLevel(max(level, logging.WARNING))


def _read_resource_file(name: str) -> str:
    path = RESOURCE_DIR / name
    return path.read_text(encoding="utf-8")


def register_resources(server: MCPServer, session: IdeaSession) -> None:
    @server.resource(
        "ideastatica://docs/parameter-reference",
        name="parameter-reference",
        title="IDEA StatiCa expression parameter reference",
        description="Operators, functions, unit conversions, model queries (GetValue, GetBoltDiameter...), bolt/anchor layout and parametric cross-section functions usable in parameter expressions.",
        mime_type="text/markdown",
    )
    def parameter_reference() -> str:
        return _read_resource_file("parameter_reference.md")

    @server.resource(
        "ideastatica://docs/parameters-getting-started",
        name="parameters-getting-started",
        title="Getting started with parameters",
        description="How parameters, model properties and validation work in IDEA StatiCa Connection (Developer tab).",
        mime_type="text/markdown",
    )
    def parameters_getting_started() -> str:
        return _read_resource_file("parameters_getting_started.md")

    @server.resource(
        "ideastatica://docs/api-reference",
        name="connection-api-reference",
        title="Connection API reference (SDK surface, data models, workflows)",
        description="Single-file reference of the Connection API: method catalog, key data models, canonical workflows and common mistakes.",
        mime_type="text/markdown",
    )
    def api_reference() -> str:
        return _read_resource_file("connection_api_reference.md")

    @server.resource(
        "ideastatica://docs/api-concepts",
        name="connection-api-concepts",
        title="Connection API concepts",
        description="Service lifecycle, projects, connections, members, operations, templates, Connection Library, parameters, loads, materials, results, reports and exports.",
        mime_type="text/markdown",
    )
    def api_concepts() -> str:
        return _read_resource_file("connection_api_concepts.md")

    @server.resource(
        "ideastatica://status",
        name="service-status",
        title="Live service status",
        description="Current state of the IDEA StatiCa REST API service and the MCP session (JSON).",
        mime_type="application/json",
    )
    def status_resource() -> str:
        status = session.service.status()
        status["open_projects"] = [r.as_dict() for r in session.projects.values()]
        status["active_project_id"] = session.active_project_id
        return json.dumps(status, indent=2, default=str)

    @server.resource(
        "ideastatica://jobs/{job_id}",
        name="calculation-job",
        title="Calculation job status",
        description="Status/progress/results of a calculation job started with calculate(wait=false) (JSON).",
        mime_type="application/json",
    )
    def job_resource(job_id: str) -> str:
        from ideastatica_mcp.tools.calculation import _job_view

        pid = session.resolve_project(None)
        job = session.invoke("calculation_jobs", "get_calculation_job", pid, job_id)
        return json.dumps(_job_view(job), indent=2, default=str)

    @server.resource(
        "ideastatica://projects",
        name="open-projects",
        title="Projects open in this session",
        description="Projects opened through this MCP session with their ids and source files (JSON).",
        mime_type="application/json",
    )
    def projects_resource() -> str:
        return json.dumps(
            {"active_project_id": session.active_project_id, "projects": [r.as_dict() for r in session.projects.values()]},
            indent=2,
        )


def build_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or Settings.from_env()
    configure_logging(settings)
    logger = logging.getLogger(__name__)
    logger.info("ideastatica-mcp %s starting; setup_dir=%s api_url=%s", __version__, settings.setup_dir, settings.api_url)

    service = ServiceManager(settings)
    session = IdeaSession(settings, service)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[IdeaSession]:
        # Close the licence "Sign in" window for the whole life of the server: it blocks both the
        # service start-up and running calculations until someone answers it.
        service.ensure_signin_guard()
        if settings.prewarm:
            # Start the (slow, licence-bound) service as soon as the MCP server is up so the first
            # tool call usually finds it ready; runs in a daemon thread, never blocks the protocol.
            threading.Thread(target=session.prewarm, name="ideastatica-prewarm", daemon=True).start()
        try:
            yield session
        finally:
            logger.info("Shutting down: closing projects and stopping the managed service")
            try:
                await anyio.to_thread.run_sync(session.close)
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                logger.warning("Shutdown cleanup failed: %s", exc)

    server = MCPServer(
        "ideastatica",
        title="IDEA StatiCa Connection",
        description="Drive IDEA StatiCa Connection (steel connection design and CBFEM checks) through its REST API.",
        instructions=INSTRUCTIONS,
        website_url="https://developer.ideastatica.com/docs/api/connection-api/connection_api_overview.html",
        version=__version__,
        lifespan=lifespan,
        log_level=settings.log_level if settings.log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO",  # type: ignore[arg-type]
    )
    server.ideastatica_session = session  # type: ignore[attr-defined]  # handy for tests
    register_all(server, session)
    register_resources(server, session)
    register_prompts(server)
    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
