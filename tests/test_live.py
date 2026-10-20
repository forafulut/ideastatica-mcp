"""Live tests against a real IDEA StatiCa Connection REST API service.

Run with:  set IDEASTATICA_LIVE=1 && pytest tests/test_live.py -m live -s
The service is started automatically from the installed IDEA StatiCa (a licence seat is needed).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from mcp import Client

from ideastatica_mcp.config import Settings
from ideastatica_mcp.server import build_server

pytestmark = [pytest.mark.live, pytest.mark.anyio, pytest.mark.skipif(not os.environ.get("IDEASTATICA_LIVE"), reason="set IDEASTATICA_LIVE=1")]

FIXTURES = Path(__file__).parent / "fixtures"


def _result(result):
    text = result.content[0].text if result.content else ""
    assert not result.is_error, text
    return result.structured_content or json.loads(text)


async def test_full_workflow(tmp_path):
    os.environ.pop("IDEASTATICA_API_URL", None)
    os.environ["IDEASTATICA_AUTOSTART"] = "true"
    settings = Settings.from_env()
    settings.output_dir = tmp_path
    server = build_server(settings)
    async with Client(server) as client:
        status = _result(await client.call_tool("service_start", {}))
        print("service:", status)
        opened = _result(await client.call_tool("open_project", {"file_path": str(FIXTURES / "simple-project.ideaCon")}))
        cid = opened["project"]["connections"][0]["id"]
        members = _result(await client.call_tool("list_members", {"connection_id": cid}))
        assert members["count"] >= 2
        _result(await client.call_tool("list_operations", {"connection_id": cid}))
        _result(await client.call_tool("list_parameters", {"connection_id": cid}))
        loads = _result(await client.call_tool("list_load_effects", {"connection_id": cid}))
        assert loads["count"] >= 1
        calc = _result(await client.call_tool("calculate", {"connection_ids": [cid]}, read_timeout_seconds=1800))
        print("calculation:", json.dumps(calc, indent=1)[:2000])
        assert calc["results"][0]["checks"]
        details = _result(await client.call_tool("get_check_results", {"connection_ids": [cid]}))
        assert details["results"][0]["summary"]
        report = _result(await client.call_tool("generate_report", {"connection_ids": [cid]}))
        assert Path(report["file_path"]).stat().st_size > 1000
        saved = _result(await client.call_tool("save_project", {}))
        assert Path(saved["file_path"]).stat().st_size > 1000
        _result(await client.call_tool("close_project", {}))
        _result(await client.call_tool("service_stop", {}))
