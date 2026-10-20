"""Advisor, optimisation, load combinations, template editing, snapshots/undo, IOM portal frame."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client

from ideastatica_mcp.tools.iom import portal_frame_iom_xml
from ideastatica_mcp.tools.visual import heat_color, utilization_lookup
from tests.conftest import TEMPLATE_XML
from tests.test_tools import call, open_sample

pytestmark = pytest.mark.anyio


def test_heat_color_and_lookup():
    assert heat_color(0.0) == (60, 200, 60) and heat_color(1.1) == (150, 20, 20) and heat_color(None) == (170, 170, 170)
    check_res = {"checkResWeld": [{"id": 3, "unityCheck": 0.8}], "checkResPlate": [{"maxStrain": 0.025, "items": [7, 8]}], "checkResBolt": [{"unityCheck": 0.9}]}
    lookup, notes = utilization_lookup(check_res, None, 0.05)
    assert lookup[("weld", "3")] == 0.8 and lookup[("plate", "7")] == 0.5 and lookup[("boltGrid", "*")] == 0.9 and notes


def test_portal_frame_xml_connection_points():
    xml, cps = portal_frame_iom_xml(2, 2, 6.0, 4.0, "HEB300", "IPE360", "S 355")
    assert xml.count("<ConnectionPoint><Id>") == 6 and len(cps) == 6
    assert cps[0]["beam_member_ids"] and "<Member1DType>Column</Member1DType>" in xml


async def test_design_advisor_and_apply(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "design_advisor", target_utilization_percent=80)
        assert err is None, err
        assert data["problems"][0]["check"] == "Bolts" and data["problems"][0]["advice"]
        data, err = await call(client, "design_advisor", target_utilization_percent=80, apply=True)
        assert err is None
        assert data["applied"]["check"] == "Bolts" and data["applied"]["result"]["passed"] is True


async def test_optimize_design_grid(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "optimize_design", parameters={"plateThickness": [0.01, 0.012], "boltAssembly": ["'M20 8.8'"]}, objective="margin", max_runs=10)
        assert err is None, err
        assert data["runs"] == 2 and data["best"]["values"]["plateThickness"] in ("0.01", "0.012")
        last = fake.recorder.find("parameter", "update")[-1][3]["idea_parameter_update"]
        assert {u.key: u.expression for u in last} == {"plateThickness": "0.015", "boltAssembly": "'M20 8.8'"}  # restored
        _, err = await call(client, "optimize_design", parameters={"nope": [1]})
        assert "unknown parameters" in err


async def test_generate_load_combinations(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        loads = [
            {"name": "G", "category": "permanent", "member_loads": [{"member_id": 2, "vz": -10000, "my": 5000}]},
            {"name": "Q", "category": "variable", "member_loads": [{"member_id": 2, "vz": -20000}]},
            {"name": "W", "category": "wind", "member_loads": [{"member_id": 2, "n": 3000}]},
        ]
        data, err = await call(client, "generate_load_combinations", characteristic_loads=loads, code="ec")
        assert err is None, err
        names = [c["name"] for c in data["created"]]
        assert names == ["1.35G+1.5Q", "1.35G+1.5W"]
        q_lead = data["created"][0]["member_loads"][0]
        assert q_lead["vz"] == pytest.approx(1.35 * -10000 + 1.5 * -20000) and q_lead["n"] == pytest.approx(1.5 * 0.6 * 3000)
        data, err = await call(client, "generate_load_combinations", characteristic_loads=loads, code="aisc")
        assert err is None and data["created"][0]["name"] == "1.4D"


async def test_diagnose_and_dry_run(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "diagnose_parameters")
        assert err is None and data["count"] == 2 and data["parameters"][0]["evaluated"] == "42"
        dry, err = await call(client, "update_parameters", updates={"plateThickness": 0.02, "missing": 1}, dry_run=True)
        assert err is None and dry["dry_run"] is True and dry["would_apply"] is False
        assert dry["checks"][1]["error"] == "unknown parameter key"
        assert not fake.recorder.find("project", "download_project_with_http_info")  # dry run takes no snapshot


async def test_snapshot_and_undo(server, fake, sample_project):
    async with Client(server) as client:
        opened = await open_sample(client, sample_project)
        old_pid = opened["project"]["project_id"]
        upd, err = await call(client, "update_parameters", updates={"plateThickness": 0.02})
        assert err is None and upd["snapshot"]["snapshots"] == 1
        snaps, _ = await call(client, "list_snapshots")
        assert snaps["count"] == 1 and "update_parameters" in snaps["snapshots"][0]["label"]
        undo, err = await call(client, "undo_last_change")
        assert err is None and undo["old_project_id"] == old_pid and undo["project"]["project_id"] != old_pid
        assert fake.recorder.find("project", "close_project")[-1][2] == (old_pid,)
        assert fake.recorder.find("project", "open_project")[-1][3]["idea_con_file"].startswith(b"IDEACON-BINARY-")
        _, err = await call(client, "undo_last_change")
        assert "no snapshot" in err


async def test_versioned_save(server, fake, sample_project, settings):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        first, err = await call(client, "save_project", versioned=True)
        second, err2 = await call(client, "save_project", versioned=True, keep_versions=1)
        assert err is None and err2 is None
        assert first["file_path"].endswith("simple-project_v1.ideaCon") and second["file_path"].endswith("simple-project_v2.ideaCon")
        assert not Path(first["file_path"]).exists() and Path(second["file_path"]).exists()


async def test_template_edit_and_library(server, fake, sample_project, tmp_path):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        saved, err = await call(client, "save_template_to_library", name="Splice EP", tags=["splice"])
        assert err is None and Path(saved["file"]).exists()
        listing, _ = await call(client, "list_saved_templates", query="splice")
        assert listing["count"] == 1
        found, err = await call(client, "find_template_values", template_file=saved["file"], query="Name")
        assert err is None and found["matches"][0]["value"] == "T1"
        edited, err = await call(client, "edit_template_xml", template_file=saved["file"], edits=[{"tag": "Name", "value": "T2"}])
        assert err is None and edited["changes"] == [{"path": "Name", "from": "T1", "to": "T2"}]
        assert "<Name>T2</Name>" in Path(edited["output_file"]).read_bytes().decode("utf-16")
        applied, err = await call(client, "apply_saved_template", name="Splice EP", remap={"S 355": "S 450"})
        assert err is None and applied["applied_without_issues"] is True and applied["remap_applied"]
        removed, err = await call(client, "delete_saved_template", name="Splice EP")
        assert err is None and len(removed["removed"]) == 2


async def test_utilization_map_and_tables(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        result = await client.call_tool("render_utilization_map", {"width": 160, "height": 120})
        assert not result.is_error, result.content[0].text
        meta = json.loads(result.content[0].text)
        assert meta["parts"][0]["kind"] == "weld" and any(c.type == "image" for c in result.content)
        tables, err = await call(client, "get_result_tables")
        assert err is None and tables["welds"][0]["utilization_percent"] == 60.0 and tables["plates"][0]["max_strain_percent"] == 1.2
        report, err = await call(client, "compile_summary_report", title="Study 1", findings_markdown="all good\nline 2")
        assert err is None and Path(report["file_path"]).read_text(encoding="utf-8").count("<h2>Connection") == 1


async def test_portal_frame_tool(server):
    async with Client(server) as client:
        data, err = await call(client, "build_iom_portal_frame", bays=1, storeys=1)
        assert err is None and len(data["connection_points"]) == 2
        info, err = await call(client, "inspect_iom_file", file_path=data["file_path"])
        assert err is None and info["counts"]["connection_points"] == 2 and info["counts"]["members"] == 3


_ = TEMPLATE_XML
