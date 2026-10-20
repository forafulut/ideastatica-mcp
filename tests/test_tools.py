"""Tool behaviour against the fake SDK client, driven through the real MCP client in-process."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client

pytestmark = pytest.mark.anyio


async def call(client: Client, tool_name: str, **args):
    result = await client.call_tool(tool_name, args)
    text = result.content[0].text if result.content else ""
    if result.is_error:
        return None, text
    return result.structured_content or json.loads(text), None


async def open_sample(client: Client, sample_project: Path) -> dict:
    data, err = await call(client, "open_project", file_path=str(sample_project))
    assert err is None, err
    return data


async def test_tool_listing_has_descriptions_and_annotations(server):
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) >= 70
        for tool in tools:
            assert tool.description, tool.name
            assert tool.annotations is not None, tool.name
        names = {t.name for t in tools}
        assert {"open_project", "calculate", "update_parameters", "apply_template", "generate_report", "save_project"} <= names


async def test_open_project_and_defaults(server, fake, sample_project):
    async with Client(server) as client:
        data = await open_sample(client, sample_project)
        pid = data["project"]["project_id"]
        assert data["project"]["connections"][0]["id"] == 1
        upload = fake.recorder.find("project", "open_project")[0]
        assert upload[3]["idea_con_file"] == sample_project.read_bytes()
        assert upload[3]["_content_type"] == "multipart/form-data"

        # project_id and connection_id default to the active project / only connection
        data, err = await call(client, "list_members")
        assert err is None and data["connection_id"] == 1 and len(data["members"]) == 2
        listing, _ = await call(client, "list_open_projects")
        assert listing["active_project_id"] == pid


async def test_missing_project_gives_actionable_error(server):
    async with Client(server) as client:
        _, err = await call(client, "list_connections")
        assert "open_project" in err


async def test_connection_id_required_when_ambiguous(server, sample_project, tmp_path):
    iom = tmp_path / "model.xml"
    iom.write_text("<OpenModelContainer/>")
    async with Client(server) as client:
        data, err = await call(client, "open_project", file_path=str(iom))
        assert err is None and len(data["project"]["connections"]) == 2
        _, err = await call(client, "list_parameters")
        assert "2 connections" in err and "1: N1" in err


async def test_update_parameters_sends_string_expressions(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "update_parameters", updates={"plateThickness": 0.02, "boltAssembly": "'M24 8.8'", "missing": True})
        assert err is None
        sent = fake.recorder.find("parameter", "update")[0][3]["idea_parameter_update"]
        assert [(u.key, u.expression) for u in sent] == [("plateThickness", "0.02"), ("boltAssembly", "'M24 8.8'"), ("missing", "true")]
        assert data["failed_validations"][0]["key"] == "missing"
        assert data["applied_to_model"] is False
        data, err = await call(client, "update_parameters", updates=[{"key": "plateThickness", "expression": "0.012"}])
        assert err is None and data["applied_to_model"] is True
        listing, _ = await call(client, "list_parameters")
        assert listing["parameters"][0]["expression"] == "0.012"
        evaluated, _ = await call(client, "evaluate_expression", expression="[plateThickness]*2")
        assert evaluated["result"] == "42"


async def test_calculate_polls_job_and_summarizes(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "calculate")
        assert err is None, err
        assert data["mode"] == "job" and data["status"] == "finished" and data["all_passed"] is True
        checks = data["results"][0]["checks"]
        assert checks[1] == {"name": "Bolts", "utilization_percent": 82.0, "passed": True, "skipped": False, "load_case_id": 1, "message": None}
        assert fake.recorder.find("calculation_jobs", "start_calculation")[0][2][1] == [1]
        assert len(fake.recorder.find("calculation_jobs", "get_calculation_job")) >= 2

        data, err = await call(client, "calculate", wait=False, connection_ids=[1])
        assert err is None and data["job"]["status"] == "queued"
        polled, _ = await call(client, "get_calculation_job", job_id=data["job"]["job_id"])
        assert polled["job"]["status"] in {"running", "finished"}

        # load effect subsets need exactly one connection
        _, err = await call(client, "calculate", connection_ids=[1, 2], load_effect_ids=[1])
        assert err is not None and "exactly one" in err


async def test_calculate_falls_back_to_sync_when_async_missing(server, fake, sample_project):
    from ideastatica_connection_api.exceptions import NotFoundException

    def missing(*args, **kwargs):
        raise NotFoundException(status=404, reason="Not Found", body="")

    fake.calculation_jobs.start_calculation = missing
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "calculate")
        assert err is None and data["mode"] == "synchronous" and data["results"][0]["passed"] is True


async def test_results_and_raw_results(server, fake, sample_project, settings):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "get_check_results")
        assert err is None
        res = data["results"][0]
        assert res["plates"][0]["maxStrain"] == 0.012 and res["welds"][0]["utilization_percent"] == 0.6
        summary, _ = await call(client, "get_check_results", summary_only=True)
        assert "plates" not in summary["results"][0]
        raw, err = await call(client, "get_raw_results", keys=["Plates", "Nope"], save_to_file="raw.json")
        assert err is None
        assert raw["top_level_keys"] == ["Bolts", "Plates", "Summary"] and raw["missing_keys"] == ["Nope"]
        assert json.loads(raw["data"]) == {"Plates": [{"name": "EP1", "strain": 0.012}]}
        assert Path(raw["saved_to"]) == settings.output_dir / "raw.json"


async def test_load_effects_roundtrip(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "add_load_effect", name="LE2", member_loads=[{"member_id": 2, "position": "Begin", "n": -50, "my": 12.5}])
        assert err is None
        sent = fake.recorder.find("load_effect", "add_load_effect")[0][3]["con_load_effect"]
        load = sent.member_loadings[0]
        assert load.member_id == 2 and load.position.value == "Begin" and load.section_load.n == -50.0 and load.section_load.my == 12.5 and load.section_load.vz == 0.0
        assert data["load_effect"]["member_loads"][0]["my"] == 12.5
        upd, err = await call(client, "update_load_effect", load_effect_id=1, active=False)
        assert err is None and upd["load_effect"]["active"] is False
        ls, _ = await call(client, "set_load_settings", loads_in_percentage=True)
        assert ls["load_settings"]["loadsInPercentage"] is True and ls["load_settings"]["loadsInEquilibrium"] is True


async def test_member_patch_merges(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "update_member", member_id=2, changes={"is_continuous": True, "position": {"gama_pitch": 0.25}})
        assert err is None
        sent = fake.recorder.find("member", "update_member")[0][3]["con_member"]
        assert sent.id == 2 and sent.name == "BEAM" and sent.is_continuous is True and sent.position.gama_pitch == 0.25
        assert sent.model.forces_in.value == "node"
        added, err = await call(client, "add_member", member={"name": "BRACE", "cross_section_id": 1, "connected_by": "begin"})
        assert err is None and added["member"]["name"] == "BRACE" and added["member"]["id"] == 10


async def test_templates_and_library(server, fake, sample_project, settings):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        exported, err = await call(client, "export_connection_template", include_content=True)
        assert err is None and Path(exported["file_path"]).exists() and exported["metadata"]["operationCount"] == 3
        assert Path(exported["file_path"]).read_bytes().startswith(b"\xff\xfe")

        mapping, err = await call(client, "get_template_mapping", template_file=exported["file_path"])
        assert err is None and mapping["mapping"]["conversions"][0]["description"] == "Steel"

        applied, err = await call(client, "apply_template", template_file=exported["file_path"], remap={"S 355": "S 450", "COL": "HEB240"})
        assert err is None and applied["applied_without_issues"] is True
        assert {(a["original"], a["new"]) for a in applied["remap_applied"]} == {("S 355", "S 450"), ("HEB200", "HEB240")}
        param = fake.recorder.find("template", "apply_template")[0][3]["con_template_apply_param"]
        assert param.connection_template.startswith("<?xml") and param.mapping.conversions[0].actual_instance.new_value == "S 450"

        # a mapping edited by the model can be passed back verbatim
        edited = mapping["raw_mapping"]
        edited["conversions"][1]["newValue"] = "IPE400"
        applied, err = await call(client, "apply_template", template_file=exported["file_path"], mapping=edited)
        assert err is None and applied["mapping_used"]["conversions"][1]["newValue"] == "IPE400"

        proposals, err = await call(client, "propose_library_designs", has_bolts="must")
        assert err is None and proposals["proposals"][0]["designSetName"] == "Predefined"
        search = fake.recorder.find("connection_library", "propose")[0][3]["con_connection_library_search_parameters"]
        assert search.has_bolts.value == "must" and search.in_predefined_set is True

        lib, err = await call(client, "apply_library_design", design_set_id="set-1", design_item_id="item-1")
        assert err is None and lib["applied_without_issues"] is True

        tpl, err = await call(client, "get_library_template", design_set_id="set-1", design_item_id="item-1", include_content=True)
        assert err is None and tpl["template_xml"].startswith("<?xml")

        deleted, err = await call(client, "delete_template", template_id="1")
        assert err is None and deleted["deleted"] == "11111111-2222-3333-4444-555555555555"
        pic, err = await call(client, "save_library_design_picture", design_set_id="set-1", design_item_id="item-1")
        assert err is None and Path(pic["file_path"]).suffix == ".png"


async def test_materials(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "list_project_materials", material_type="bolt assemblies")
        assert err is None and data["material_type"] == "bolt_assemblies"
        assert fake.recorder.find("material", "get_bolt_assemblies")
        data, err = await call(client, "search_material_library", material_type="cross-sections", query="heb", limit=1)
        assert err is None and data["total_matches"] == 2 and data["names"] == ["HEB200"] and data["truncated"] is True
        assert fake.recorder.find("material", "get_material_library")[0][2][1] == "cross-sections"
        data, err = await call(client, "add_cross_section", mprl_name="IPE300", material_name="S 355")
        assert err is None and data["added"]["id"] == 7
        data, err = await call(client, "add_project_material", material_type="weld", mprl_name="E 42")
        assert err is None and fake.recorder.find("material", "add_material_weld")
        _, err = await call(client, "add_project_material", material_type="cross_sections", mprl_name="x")
        assert "unknown material type" in err


async def test_reports_exports_and_save(server, fake, sample_project, settings):
    async with Client(server) as client:
        opened = await open_sample(client, sample_project)
        pid = opened["project"]["project_id"]
        pdf, err = await call(client, "generate_report")
        assert err is None and pdf["file_path"].endswith("simple-project-con1-report.pdf")
        assert fake.recorder.find("report", "generate_pdf_with_http_info")
        multi, err = await call(client, "generate_report", format="word", connection_ids=[1, 2])
        assert err is None and multi["file_path"].endswith("all-connections-report.docx")
        assert fake.recorder.find("report", "generate_word_for_multiple_with_http_info")[0][2] == (pid, [1, 2])
        _, err = await call(client, "generate_report", format="html", connection_ids=[1, 2])
        assert "exactly one" in err
        _, err = await call(client, "generate_report")
        assert "already exists" in err
        again, err = await call(client, "generate_report", overwrite=True)
        assert err is None

        ifc, err = await call(client, "export_connection", format="ifc")
        assert err is None and Path(ifc["file_path"]).read_bytes() == b"ISO-10303-21;"
        cd, err = await call(client, "export_connection", format="iom_connection_data")
        assert err is None and cd["inventory"]["plates"] == 2
        scene, err = await call(client, "get_scene_3d")
        assert err is None and scene["vertex_count"] == 3 and scene["group_count"] == 1

        saved, err = await call(client, "save_project")
        assert err is None and saved["file_path"] == str(settings.output_dir / "simple-project.ideaCon")
        assert Path(saved["file_path"]).read_bytes().startswith(b"IDEACON-BINARY-")


async def test_settings_and_conversion(server, fake, sample_project):
    async with Client(server) as client:
        await open_sample(client, sample_project)
        data, err = await call(client, "get_project_settings", search="plasticstrain")
        assert err is None and data["count"] == 1
        upd, err = await call(client, "update_project_settings", values={"other/Setting@01": 2})
        assert err is None and upd["settings"]["other/Setting@01"] == 2
        mapping, err = await call(client, "get_design_code_conversion_mapping", target_code="AISC")
        assert err is None and mapping["target_code"] == "american"
        conv, err = await call(client, "convert_project_design_code", target_code="american", overrides={"steel": {"S 355": "A572"}}, keep_cross_sections=True)
        assert err is None
        sent = fake.recorder.find("conversion", "change_code")[0][3]["con_conversion_settings"]
        assert sent.steel[0].target_value == "A572" and sent.cross_sections[0].target_value == "HEB200"
        assert conv["project"]["design_code"] == "american"


async def test_close_and_service_stop(server, fake, sample_project):
    async with Client(server) as client:
        data = await open_sample(client, sample_project)
        closed, err = await call(client, "close_project")
        assert err is None and closed["closed_project_id"] == data["project"]["project_id"]
        assert fake.recorder.find("project", "close_project")
        listing, _ = await call(client, "list_open_projects")
        assert listing["projects"] == []


async def test_resources_and_prompts(server):
    async with Client(server) as client:
        resources = (await client.list_resources()).resources
        uris = {str(r.uri) for r in resources}
        assert "ideastatica://docs/parameter-reference" in uris
        content = await client.read_resource("ideastatica://docs/parameter-reference")
        assert "RoundWithOffset" in content.contents[0].text
        prompts = (await client.list_prompts()).prompts
        assert {p.name for p in prompts} == {"calculate_and_report", "parametric_study", "design_from_library"}
        prompt = await client.get_prompt("calculate_and_report", {"project_file": "C:/x.ideaCon"})
        assert "open_project" in prompt.messages[0].content.text
