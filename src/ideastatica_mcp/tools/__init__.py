"""Tool groups registered on the MCP server."""

from __future__ import annotations

from ideastatica_mcp.tools import (
    advisor,
    calculation,
    connections,
    conversion,
    iom,
    loads,
    materials,
    operations,
    parameters,
    projects,
    rcs,
    reports,
    service,
    settings,
    template_edit,
    templates,
    visual,
    workflows,
)

ALL_GROUPS = (
    service,
    projects,
    connections,
    operations,
    parameters,
    loads,
    materials,
    calculation,
    templates,
    conversion,
    reports,
    settings,
    workflows,
    advisor,
    template_edit,
    visual,
    iom,
    rcs,
)


def register_all(server, session) -> None:
    for group in ALL_GROUPS:
        group.register(server, session)
