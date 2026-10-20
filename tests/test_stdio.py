"""End-to-end over stdio: the server process started exactly like Claude Desktop starts it."""

from __future__ import annotations

import json
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.anyio


async def test_stdio_handshake_and_status():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ideastatica_mcp"],
        env={
            "IDEASTATICA_AUTOSTART": "false",
            "IDEASTATICA_API_URL": "http://127.0.0.1:59999",
            "IDEASTATICA_LOG_LEVEL": "WARNING",
            "IDEASTATICA_PREWARM": "false",
        },
    )
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        assert any(t.name == "service_status" for t in tools)
        result = await client.call_tool("service_status", {})
        assert not result.is_error
        payload = result.structured_content or json.loads(result.content[0].text)
        assert payload["reachable"] is False and payload["sdk_version"].startswith("26.0")
        failure = await client.call_tool("list_connections", {})
        assert failure.is_error and "open_project" in failure.content[0].text
