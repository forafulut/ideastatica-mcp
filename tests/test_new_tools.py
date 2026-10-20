"""Workflow, visual, IOM and operation tools against the fake SDK client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client

from ideastatica_mcp.tools.calculation import select_path
from ideastatica_mcp.tools.visual import render_scene_png
from tests.test_tools import call, open_sample

pytestmark = pytest.mark.anyio


def test_select_path():
    data = {"Plates": [{"name": "P1", "items": [1, 2]}, {"name": "P2"}], "Welds": {"w1": {"name": "W1"}}}
    assert select_path(data, "plates[0].items") == [1, 2]
    assert select_path(data, "Plates.*.name") == ["P1", "P2"]
    assert select_path(data, "welds.*.name") == ["W1"]
    with pytest.raises(Exception):
        select_path(data, "nothing.here")


def test_render_scene_png_produces_png():
    scene = {"vertices": [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1], "groups": [{"faces": [0, 1, 2, 0, 2, 3], "lines": [{"pairs": [4, 5]}]}]}
    png = render_scene_png(scene, 200, 150)
    assert png.startswith(b"\x89PNG") and len(png) > 200


async def test_optimize_parameter_sweeps_and_restores(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "optimize_parameter", parameter_key="plateThickness", values=[0.01, 0.012], save_csv="sweep.csv")
        assert err is None, err
        assert [r["value"] for r in data["rows"]] == ["0.01", "0.012"]
        assert data["rows"][0]["governing_check"] == "Bolts" and data["rows"][0]["max_utilization_percent"] == 82.0
        assert data["most_economical_passing_value"] == "0.01"
        updates = [c[3]["idea_parameter_update"][0].expression for c in fake.recorder.find("parameter", "update")]
        assert updates == ["0.01", "0.012", "0.015"]  # restored at the end
        assert Path(data["csv"]).read_text().startswith("value,passed")
        _, err = await call(client, "optimize_parameter", parameter_key="nope", values=[1])
        assert "not found" in err


async def test_design_check_summary_and_weld_sizing(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "design_check_summary")
        assert err is None and data["all_passed"] is True and data["connections"][0]["governing_check"] == "Bolts"
        data, err = await call(client, "auto_weld_sizing", method="minimum_ductility")
        assert err is None and data["method"] == "minimumDuctility" and data["result"]["passed"] is True
        assert fake.recorder.find("operation", "pre_design_welds")


async def test_batch_calculate(server, fake, sample_project, tmp_path):
    folder = tmp_path / "batch"
    folder.mkdir()
    (folder / "a.ideaCon").write_bytes(sample_project.read_bytes())
    (folder / "b.ideaCon").write_bytes(sample_project.read_bytes())
    async with Client(server) as client:
        data, err = await call(client, "batch_calculate", folder=str(folder), report_csv="batch.csv")
        assert err is None, err
        assert [r["file"] for r in data["rows"]] == ["a.ideaCon", "b.ideaCon"] and all(r["passed"] for r in data["rows"])
        assert len(fake.recorder.find("project", "close_project")) == 2
        assert Path(data["csv"]).exists()


async def test_score_library_designs(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "score_library_designs", prefer_bolted=True, moment_connection=True)
        assert err is None and data["designs"][0]["score"] >= 4 and "bolted" in data["designs"][0]["reasons"]


async def test_operation_tools_use_raw_rest(server, session, sample_project):
    store = {5: {"id": 5, "name": "W1", "type": "fillet", "size": 0.004, "placement": {"kind": "edge"}}}
    calls = []

    def fake_rest(method, path, body=None, timeout=600.0):
        calls.append((method, path, body))
        if method == "GET" and path.endswith("/operations/weld"):
            return list(store.values())
        if method == "GET" and "/operations/weld/" in path:
            return store[int(path.rsplit("/", 1)[1])]
        if method == "PUT":
            store[body["id"]] = body
            return {"operation": body, "issues": []}
        if method == "POST":
            body["id"] = 9
            store[9] = body
            return {"operation": body, "addedWithoutIssues": True}
        if method == "DELETE":
            return store.pop(int(path.rsplit("/", 1)[1]), None) and {"deleted": True}
        raise AssertionError(path)

    session.rest = fake_rest
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "list_operation_details", kind="welds")
        assert err is None and data["kind"] == "weld" and data["count"] == 1
        data, err = await call(client, "update_operation", kind="weld", operation_id=5, changes={"size": 0.006, "Type": "doubleFillet", "placement": {"kind": "all"}})
        assert err is None
        assert data["sent"] == {"id": 5, "name": "W1", "type": "doubleFillet", "size": 0.006, "placement": {"kind": "all"}}
        data, err = await call(client, "add_operation", kind="weld", operation={"name": "W2", "size": 0.005, "id": 77})
        assert err is None and data["result"]["operation"]["id"] == 9 and "id" not in calls[-1][2] or calls[-1][2].get("id") == 9
        data, err = await call(client, "delete_operation", kind="weld", operation_id=9)
        assert err is None and 9 not in store
        _, err = await call(client, "list_operation_details", kind="laser")
        assert "unknown operation kind" in err


async def test_iom_build_and_inspect(server, settings):
    async with Client(server) as client:
        built, err = await call(client, "build_iom_frame", column_section="HEB240")
        assert err is None and Path(built["file_path"]).exists()
        info, err = await call(client, "inspect_iom_file", file_path=built["file_path"])
        assert err is None
        assert info["counts"]["members"] == 2 and info["counts"]["connection_points"] == 1 and info["has_results"] is False
        assert info["cross_sections"][0]["name"] == "HEB240"
        assert info["connection_points"][0]["id"] == "1"


async def test_render_connection_image_returns_image(server, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        result = await client.call_tool("render_connection_image", {"width": 160, "height": 120})
        assert not result.is_error, result.content[0].text
        types = [c.type for c in result.content]
        assert "image" in types
        meta = json.loads(result.content[0].text)
        assert Path(meta["file_path"]).exists()
