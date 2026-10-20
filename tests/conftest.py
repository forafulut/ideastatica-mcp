"""Shared fixtures: a fake IDEA StatiCa SDK client wired into the MCP server."""

from __future__ import annotations

import base64
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("IDEASTATICA_AUTOSTART", "false")
os.environ.setdefault("IDEASTATICA_API_URL", "http://127.0.0.1:59999")
os.environ.setdefault("IDEASTATICA_LOG_LEVEL", "WARNING")
os.environ.setdefault("IDEASTATICA_DISMISS_SIGNIN", "false")  # never touch real windows from tests

from ideastatica_connection_api.models.base_template_conversion import BaseTemplateConversion  # noqa: E402
from ideastatica_connection_api.models.check_res_bolt import CheckResBolt  # noqa: E402
from ideastatica_connection_api.models.check_res_plate import CheckResPlate  # noqa: E402
from ideastatica_connection_api.models.check_res_summary import CheckResSummary  # noqa: E402
from ideastatica_connection_api.models.check_res_weld import CheckResWeld  # noqa: E402
from ideastatica_connection_api.models.con_calculation_job import ConCalculationJob  # noqa: E402
from ideastatica_connection_api.models.con_calculation_job_status_enum import ConCalculationJobStatusEnum  # noqa: E402
from ideastatica_connection_api.models.con_connection import ConConnection  # noqa: E402
from ideastatica_connection_api.models.con_connection_template import ConConnectionTemplate  # noqa: E402
from ideastatica_connection_api.models.con_conversion_settings import ConConversionSettings  # noqa: E402
from ideastatica_connection_api.models.con_design_item import ConDesignItem  # noqa: E402
from ideastatica_connection_api.models.con_design_set import ConDesignSet  # noqa: E402
from ideastatica_connection_api.models.con_load_effect import ConLoadEffect  # noqa: E402
from ideastatica_connection_api.models.con_load_effect_member_load import ConLoadEffectMemberLoad  # noqa: E402
from ideastatica_connection_api.models.con_load_effect_position_enum import ConLoadEffectPositionEnum  # noqa: E402
from ideastatica_connection_api.models.con_load_effect_section_load import ConLoadEffectSectionLoad  # noqa: E402
from ideastatica_connection_api.models.con_load_settings import ConLoadSettings  # noqa: E402
from ideastatica_connection_api.models.con_member import ConMember  # noqa: E402
from ideastatica_connection_api.models.con_member_model import ConMemberModel  # noqa: E402
from ideastatica_connection_api.models.con_member_position import ConMemberPosition  # noqa: E402
from ideastatica_connection_api.models.con_operation import ConOperation  # noqa: E402
from ideastatica_connection_api.models.con_operation_common_properties import ConOperationCommonProperties  # noqa: E402
from ideastatica_connection_api.models.con_production_cost import ConProductionCost  # noqa: E402
from ideastatica_connection_api.models.con_project import ConProject  # noqa: E402
from ideastatica_connection_api.models.con_project_data import ConProjectData  # noqa: E402
from ideastatica_connection_api.models.con_result_summary import ConResultSummary  # noqa: E402
from ideastatica_connection_api.models.con_template_apply_result import ConTemplateApplyResult  # noqa: E402
from ideastatica_connection_api.models.con_template_create_result import ConTemplateCreateResult  # noqa: E402
from ideastatica_connection_api.models.connection_check_res import ConnectionCheckRes  # noqa: E402
from ideastatica_connection_api.models.connection_data import ConnectionData  # noqa: E402
from ideastatica_connection_api.models.conversion_mapping import ConversionMapping  # noqa: E402
from ideastatica_connection_api.models.country_code import CountryCode  # noqa: E402
from ideastatica_connection_api.models.idea_parameter import IdeaParameter  # noqa: E402
from ideastatica_connection_api.models.idea_parameter_validation_response import (  # noqa: E402
    IdeaParameterValidationResponse,
)
from ideastatica_connection_api.models.parameter_update_response import ParameterUpdateResponse  # noqa: E402
from ideastatica_connection_api.models.template_conversions import TemplateConversions  # noqa: E402
from ideastatica_connection_api.models.template_conversions_conversions_inner import (  # noqa: E402
    TemplateConversionsConversionsInner,
)

from ideastatica_mcp.config import Settings  # noqa: E402
from ideastatica_mcp.server import build_server  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATE_XML = '<?xml version="1.0" encoding="utf-16"?>\n<ConnectionTemplate><Name>T1</Name></ConnectionTemplate>'
STEEL_TYPE = "IdeaStatiCa.Api.Connection.Model.PlateMaterialTemplateConversion, IdeaStatiCa.Api"
CSS_TYPE = "IdeaStatiCa.Api.Connection.Model.CssTemplateConversion, IdeaStatiCa.Api"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Recorder:
    """Records every SDK call so tests can assert what the tools sent."""

    def __init__(self):
        self.calls: list[tuple[str, str, tuple, dict]] = []

    def record(self, group: str, method: str, *args, **kwargs):
        self.calls.append((group, method, args, kwargs))

    def find(self, group: str, method: str):
        return [c for c in self.calls if c[0] == group and c[1] == method]


def _connection(cid: int, name: str) -> ConConnection:
    return ConConnection(id=cid, name=name, identifier=f"CON{cid}", analysis_type="stress_Strain", is_calculated=False, include_buckling=False)


def _project(pid: str, name: str, connections: list[ConConnection]) -> ConProject:
    return ConProject(project_id=pid, project_info=ConProjectData(name=name, country_code=CountryCode.ECEN), connections=connections)


def _member(mid: int, name: str, bearing: bool) -> ConMember:
    return ConMember(
        id=mid,
        name=name,
        active=True,
        is_bearing=bearing,
        is_continuous=bearing,
        cross_section_id=1,
        connected_by="end",
        position=ConMemberPosition(defined_by="rotationsOfX", beta_direction=0.0, gama_pitch=0.0, alpha_rotation=0.0, offset_ex=0.0),
        model=ConMemberModel(model_type_enum="loadedInXYZ", forces_in="node", x=0.0),
    )


class FakeState:
    def __init__(self):
        self.projects: dict[str, ConProject] = {}
        self.members: dict[tuple[str, int], list[ConMember]] = {}
        self.parameters: dict[tuple[str, int], list[IdeaParameter]] = {}
        self.load_effects: dict[tuple[str, int], list[ConLoadEffect]] = {}
        self.jobs: dict[str, dict] = {}
        self.settings = {"calculationCommon/Checks/Shared/LimitPlasticStrainForHighStrengthSteel@01": 0.05, "other/Setting@01": 1}
        self.common = ConOperationCommonProperties(weld_material_id=1, plate_material_id=2, bolt_assembly_id=3)


class FakeGroup:
    def __init__(self, name: str, recorder: Recorder, state: FakeState):
        self._name = name
        self._rec = recorder
        self._state = state

    def _r(self, method: str, *args, **kwargs):
        self._rec.record(self._name, method, *args, **kwargs)


class FakeProject(FakeGroup):
    def _new(self, name: str, connection_names: list[str]) -> ConProject:
        pid = str(uuid.uuid4())
        connections = [_connection(i + 1, n) for i, n in enumerate(connection_names)]
        project = _project(pid, name, connections)
        self._state.projects[pid] = project
        for con in connections:
            self._state.members[(pid, con.id)] = [_member(1, "COL", True), _member(2, "BEAM", False)]
            self._state.parameters[(pid, con.id)] = [
                IdeaParameter(key="plateThickness", expression="0.015", value=0.015, unit="m", parameter_type="Float", is_visible=True),
                IdeaParameter(key="boltAssembly", expression="'M20 8.8'", value="M20 8.8", parameter_type="String", is_visible=True),
            ]
            self._state.load_effects[(pid, con.id)] = [
                ConLoadEffect(
                    id=1,
                    name="LE1",
                    active=True,
                    is_percentage=False,
                    member_loadings=[
                        ConLoadEffectMemberLoad(member_id=2, position=ConLoadEffectPositionEnum.END, section_load=ConLoadEffectSectionLoad(n=-10.0, vy=0.0, vz=20.0, mx=0.0, my=30.0, mz=0.0))
                    ],
                )
            ]
        return project

    def open_project(self, idea_con_file=None, **kwargs):
        self._r("open_project", idea_con_file=idea_con_file, **kwargs)
        assert isinstance(idea_con_file, bytes) and idea_con_file, "the project bytes must be uploaded"
        return self._new("Sample", ["CON1"])

    def import_iom(self, container_xml_file=None, connections_to_create=None, **kwargs):
        self._r("import_iom", container_xml_file=container_xml_file, connections_to_create=connections_to_create, **kwargs)
        return self._new("FromIOM", ["N1", "N2"])

    def create_empty_project(self, con_project_data=None, **kwargs):
        self._r("create_empty_project", con_project_data=con_project_data)
        project = self._new(con_project_data.name if con_project_data else "Empty", [])
        if con_project_data is not None:
            project.project_info = con_project_data
        return project

    def get_active_projects(self):
        self._r("get_active_projects")
        return list(self._state.projects.values())

    def get_project_data(self, pid):
        self._r("get_project_data", pid)
        return self._state.projects[pid]

    def update_project_data(self, pid, con_project_data=None):
        self._r("update_project_data", pid, con_project_data=con_project_data)
        self._state.projects[pid].project_info = con_project_data
        return self._state.projects[pid]

    def download_project_with_http_info(self, pid):
        self._r("download_project_with_http_info", pid)
        return SimpleNamespace(raw_data=b"IDEACON-BINARY-" + pid.encode(), status_code=200)

    def close_project(self, pid):
        self._r("close_project", pid)
        self._state.projects.pop(pid, None)

    def update_from_iom(self, pid, container_xml_file=None, **kwargs):
        self._r("update_from_iom", pid, container_xml_file=container_xml_file, **kwargs)
        return self._state.projects[pid]


class FakeConnection(FakeGroup):
    def get_connections(self, pid):
        self._r("get_connections", pid)
        return list(self._state.projects[pid].connections or [])

    def get_connection(self, pid, cid):
        self._r("get_connection", pid, cid)
        for con in self._state.projects[pid].connections or []:
            if con.id == cid:
                return con
        raise KeyError(cid)

    def update_connection(self, pid, cid, con_connection=None):
        self._r("update_connection", pid, cid, con_connection=con_connection)
        return con_connection

    def create_empty_connection(self, pid, name=None):
        self._r("create_empty_connection", pid, name=name)
        project = self._state.projects[pid]
        con = _connection(len(project.connections or []) + 1, name or "New")
        project.connections = list(project.connections or []) + [con]
        self._state.members[(pid, con.id)] = []
        return con

    def copy_connection(self, pid, cid, name=None):
        self._r("copy_connection", pid, cid, name=name)
        return _connection(99, name or "Copy")

    def delete_connection(self, pid, cid):
        self._r("delete_connection", pid, cid)
        project = self._state.projects[pid]
        project.connections = [c for c in project.connections or [] if c.id != cid]
        return project.connections

    def get_connection_topology(self, pid, cid):
        self._r("get_connection_topology", pid, cid)
        return json.dumps({"typology": "B-C", "members": 2})

    def get_production_cost(self, pid, cid):
        self._r("get_production_cost", pid, cid)
        return ConProductionCost(total_estimated_cost=123.5)


class FakeMember(FakeGroup):
    def get_members(self, pid, cid):
        self._r("get_members", pid, cid)
        return list(self._state.members[(pid, cid)])

    def get_member(self, pid, cid, mid):
        self._r("get_member", pid, cid, mid)
        for m in self._state.members[(pid, cid)]:
            if m.id == mid:
                return m
        raise KeyError(mid)

    def add_member(self, pid, cid, con_member=None):
        self._r("add_member", pid, cid, con_member=con_member)
        con_member.id = 10
        self._state.members[(pid, cid)].append(con_member)
        return con_member

    def update_member(self, pid, cid, con_member=None):
        self._r("update_member", pid, cid, con_member=con_member)
        members = self._state.members[(pid, cid)]
        self._state.members[(pid, cid)] = [con_member if m.id == con_member.id else m for m in members]
        return con_member

    def set_bearing_member(self, pid, cid, mid):
        self._r("set_bearing_member", pid, cid, mid)
        return self.get_member(pid, cid, mid)

    def delete_member(self, pid, cid, mid):
        self._r("delete_member", pid, cid, mid)
        return mid


class FakeOperation(FakeGroup):
    def get_operations(self, pid, cid):
        self._r("get_operations", pid, cid)
        return [ConOperation(id=1, name="EP1", operation_type="EndPlate", active=True, is_imported=False)]

    def delete_operations(self, pid, cid):
        self._r("delete_operations", pid, cid)

    def get_common_operation_properties(self, pid, cid):
        self._r("get_common_operation_properties", pid, cid)
        return self._state.common

    def update_common_operation_properties(self, pid, cid, con_operation_common_properties=None):
        self._r("update_common_operation_properties", pid, cid, con_operation_common_properties=con_operation_common_properties)
        self._state.common = con_operation_common_properties

    def pre_design_welds(self, pid, cid, design_type=None):
        self._r("pre_design_welds", pid, cid, design_type=design_type)
        return json.dumps({"welds": 4})


class FakeParameter(FakeGroup):
    def get_parameters(self, pid, cid, include_hidden=None):
        self._r("get_parameters", pid, cid, include_hidden=include_hidden)
        return list(self._state.parameters[(pid, cid)])

    def update(self, pid, cid, idea_parameter_update=None):
        self._r("update", pid, cid, idea_parameter_update=idea_parameter_update)
        failed = []
        params = self._state.parameters[(pid, cid)]
        for upd in idea_parameter_update or []:
            match = next((p for p in params if p.key == upd.key), None)
            if match is None:
                failed.append(IdeaParameterValidationResponse(key=upd.key, message="unknown parameter", validation_status="Error"))
            else:
                match.expression = upd.expression
        return ParameterUpdateResponse(set_to_model=not failed, parameters=params, failed_validations=failed)

    def evaluate_expression(self, pid, cid, body=None):
        self._r("evaluate_expression", pid, cid, body=body)
        return "42"

    def delete_parameters(self, pid, cid):
        self._r("delete_parameters", pid, cid)
        self._state.parameters[(pid, cid)] = []


class FakeLoadEffect(FakeGroup):
    def get_load_effects(self, pid, cid, is_percentage=None):
        self._r("get_load_effects", pid, cid, is_percentage=is_percentage)
        return list(self._state.load_effects[(pid, cid)])

    def get_load_effect(self, pid, cid, leid, is_percentage=None):
        self._r("get_load_effect", pid, cid, leid)
        return next(le for le in self._state.load_effects[(pid, cid)] if le.id == leid)

    def add_load_effect(self, pid, cid, con_load_effect=None):
        self._r("add_load_effect", pid, cid, con_load_effect=con_load_effect)
        con_load_effect.id = 2
        self._state.load_effects[(pid, cid)].append(con_load_effect)
        return con_load_effect

    def update_load_effect(self, pid, cid, con_load_effect=None):
        self._r("update_load_effect", pid, cid, con_load_effect=con_load_effect)
        return con_load_effect

    def delete_load_effect(self, pid, cid, leid):
        self._r("delete_load_effect", pid, cid, leid)
        return leid

    def get_load_settings(self, pid, cid):
        self._r("get_load_settings", pid, cid)
        return ConLoadSettings(loads_in_equilibrium=True, loads_in_percentage=False)

    def set_load_settings(self, pid, cid, con_load_settings=None):
        self._r("set_load_settings", pid, cid, con_load_settings=con_load_settings)
        return con_load_settings

    def calculate_load_extremes(self, pid, cid):
        self._r("calculate_load_extremes", pid, cid)
        return list(self._state.load_effects[(pid, cid)])


class FakeMaterial(FakeGroup):
    def __getattr__(self, name):
        if name.startswith("get_") and name != "get_material_library":
            def lister(pid):
                self._r(name, pid)
                return [{"id": 1, "name": "S 355", "kind": name}]
            return lister
        if name.startswith("add_"):
            def adder(pid, **kwargs):
                self._r(name, pid, **kwargs)
                return {"id": 7, "name": "added"}
            return adder
        raise AttributeError(name)

    def get_material_library(self, pid, material_type):
        self._r("get_material_library", pid, material_type)
        return ["HEB200", "HEB220", "IPE300", "S 355", "S 235"]


class FakeCalculation(FakeGroup):
    def calculate(self, pid, ids):
        self._r("calculate", pid, ids)
        return [self._summary(cid) for cid in ids]

    def calculate_connection(self, pid, cid, load_effect_ids=None):
        self._r("calculate_connection", pid, cid, load_effect_ids=load_effect_ids)
        return self._summary(cid)

    @staticmethod
    def _summary(cid: int) -> ConResultSummary:
        return ConResultSummary(
            id=cid,
            passed=True,
            result_summary=[
                CheckResSummary(name="Plates", check_value=45.2, check_status=True, skipped=False, load_case_id=1),
                CheckResSummary(name="Bolts", check_value=82.0, check_status=True, skipped=False, load_case_id=1),
                CheckResSummary(name="Anchors", check_value=0.0, check_status=True, skipped=True),
            ],
        )

    def get_results(self, pid, ids):
        self._r("get_results", pid, ids)
        return [
            ConnectionCheckRes(
                id=cid,
                name=f"CON{cid}",
                check_res_summary=self._summary(cid).result_summary,
                check_res_plate=[CheckResPlate(name="EP1", check_status=True, max_strain=0.012, max_stress=355e6, load_case_id=1)],
                check_res_weld=[CheckResWeld(name="W1", id=1, unity_check=0.6, check_status=True, is_full_strength=False)],
                check_res_bolt=[CheckResBolt(name="B1", unity_check=0.82, check_status=True)],
            )
            for cid in ids
        ]

    def get_connection_raw_json_results(self, pid, cid):
        self._r("get_connection_raw_json_results", pid, cid)
        return json.dumps({"Plates": [{"name": "EP1", "strain": 0.012}], "Bolts": [{"name": "B1"}], "Summary": {"passed": True}})


class FakeCalculationJobs(FakeGroup):
    def start_calculation(self, pid, ids):
        self._r("start_calculation", pid, ids)
        job_id = str(uuid.uuid4())
        self._state.jobs[job_id] = {"ids": list(ids), "polls": 0}
        return ConCalculationJob(job_id=job_id, project_id=pid, status=ConCalculationJobStatusEnum.QUEUED, connection_ids=list(ids), progress=0.0)

    def start_connection_calculation(self, pid, cid, load_effect_ids=None):
        self._r("start_connection_calculation", pid, cid, load_effect_ids=load_effect_ids)
        return self.start_calculation(pid, [cid])

    def get_calculation_job(self, pid, job_id):
        self._r("get_calculation_job", pid, job_id)
        job = self._state.jobs[job_id]
        job["polls"] += 1
        if job["polls"] < 2:
            return ConCalculationJob(job_id=job_id, status=ConCalculationJobStatusEnum.RUNNING, connection_ids=job["ids"], progress=50.0, connections_completed=0, current_connection_id=job["ids"][0])
        return ConCalculationJob(
            job_id=job_id,
            status=ConCalculationJobStatusEnum.FINISHED,
            connection_ids=job["ids"],
            progress=100.0,
            connections_completed=len(job["ids"]),
            results=[FakeCalculation._summary(cid) for cid in job["ids"]],
        )

    def cancel_calculation_job(self, pid, job_id):
        self._r("cancel_calculation_job", pid, job_id)
        return ConCalculationJob(job_id=job_id, status=ConCalculationJobStatusEnum.CANCELLED)


def _mapping() -> TemplateConversions:
    steel = BaseTemplateConversion.from_dict({"originalValue": "S 355", "newValue": "S 355", "description": "Steel", "$type": STEEL_TYPE})
    css = BaseTemplateConversion.from_dict({"originalValue": "HEB200", "newValue": "HEB200", "description": "COL", "$type": CSS_TYPE})
    return TemplateConversions(
        conversions=[TemplateConversionsConversionsInner.from_dict(steel.to_dict()), TemplateConversionsConversionsInner.from_dict(css.to_dict())],
        country_code="ECEN",
    )


class FakeTemplate(FakeGroup):
    def create_template_from_connection(self, pid, cid):
        self._r("create_template_from_connection", pid, cid)
        return ConTemplateCreateResult(template_id=str(uuid.uuid4()), name="CON1 template", design_code="ECEN", operation_count=3, parameter_count=2, template=TEMPLATE_XML)

    def create_con_template(self, pid, cid):
        self._r("create_con_template", pid, cid)
        return TEMPLATE_XML

    def get_default_template_mapping(self, pid, cid, con_template_mapping_get_param=None):
        self._r("get_default_template_mapping", pid, cid, con_template_mapping_get_param=con_template_mapping_get_param)
        return _mapping()

    def apply_template(self, pid, cid, con_template_apply_param=None):
        self._r("apply_template", pid, cid, con_template_apply_param=con_template_apply_param)
        return ConTemplateApplyResult(applied_without_issues=True, issues=[])

    def get_templates_in_connection(self, pid, cid):
        self._r("get_templates_in_connection", pid, cid)
        return [ConConnectionTemplate(library_template_id="11111111-2222-3333-4444-555555555555", template_id=1, parameter_keys=["plateThickness"])]

    def delete(self, pid, cid, tid):
        self._r("delete", pid, cid, tid)

    def delete_all(self, pid, cid):
        self._r("delete_all", pid, cid)

    def explode(self, pid, cid, tid):
        self._r("explode", pid, cid, tid)

    def explode_all(self, pid, cid):
        self._r("explode_all", pid, cid)

    def load_defaults(self, pid, cid, tid):
        self._r("load_defaults", pid, cid, tid)
        return ParameterUpdateResponse(set_to_model=True, parameters=[], failed_validations=[])

    def get_template_common_operation_properties(self, pid, cid, tid):
        self._r("get_template_common_operation_properties", pid, cid, tid)
        return self._state.common

    def update_template_common_operation_properties(self, pid, cid, tid, con_operation_common_properties=None):
        self._r("update_template_common_operation_properties", pid, cid, tid, con_operation_common_properties=con_operation_common_properties)
        self._state.common = con_operation_common_properties


class FakeConnectionLibrary(FakeGroup):
    def get_design_sets(self):
        self._r("get_design_sets")
        return [ConDesignSet(id="set-1", name="Predefined", type="predefined")]

    def propose(self, pid, cid, con_connection_library_search_parameters=None):
        self._r("propose", pid, cid, con_connection_library_search_parameters=con_connection_library_search_parameters)
        return [ConDesignItem(name="End plate", design_code="ECEN", version="1", con_design_set_id="set-1", con_design_item_id="item-1")]

    def get_template(self, design_set_id=None, design_item_id=None):
        self._r("get_template", design_set_id=design_set_id, design_item_id=design_item_id)
        return base64.b64encode(TEMPLATE_XML.encode("utf-8")).decode("ascii")

    def get_design_item_picture_with_http_info(self, design_set_id=None, design_item_id=None):
        self._r("get_design_item_picture_with_http_info", design_set_id=design_set_id, design_item_id=design_item_id)
        return SimpleNamespace(raw_data=b"\x89PNG\r\n\x1a\nfake", status_code=200)

    def publish_connection(self, pid, cid, con_template_publish_param=None):
        self._r("publish_connection", pid, cid, con_template_publish_param=con_template_publish_param)
        return True


class FakeConversion(FakeGroup):
    def get_conversion_mapping(self, pid, country_code=None):
        self._r("get_conversion_mapping", pid, country_code=country_code)
        return ConConversionSettings(
            target_design_code=country_code,
            steel=[ConversionMapping(source_value="S 355", target_value="A992")],
            cross_sections=[ConversionMapping(source_value="HEB200", target_value="W8X48")],
            bolt_grade=[ConversionMapping(source_value="8.8", target_value="A325")],
        )

    def change_code(self, pid, con_conversion_settings=None):
        self._r("change_code", pid, con_conversion_settings=con_conversion_settings)
        project = self._state.projects[pid]
        project.project_info.country_code = con_conversion_settings.target_design_code


class FakeReport(FakeGroup):
    def __getattr__(self, name):
        if name.endswith("_with_http_info"):
            def generator(pid, *args, **kwargs):
                self._r(name, pid, *args, **kwargs)
                return SimpleNamespace(raw_data=f"%{name}%".encode() + b"\x00" * 10, status_code=200)
            return generator
        raise AttributeError(name)


class FakeExport(FakeGroup):
    def export_ifc_with_http_info(self, pid, cid):
        self._r("export_ifc_with_http_info", pid, cid)
        return SimpleNamespace(raw_data=b"ISO-10303-21;", status_code=200)

    def export_dwg_with_http_info(self, pid, cid):
        self._r("export_dwg_with_http_info", pid, cid)
        return SimpleNamespace(raw_data=b"AC1027dwg", status_code=200)

    def export_iom(self, pid, cid, version=None):
        self._r("export_iom", pid, cid, version=version)
        return '<?xml version="1.0"?><OpenModelContainer/>'

    def export_iom_connection_data(self, pid, cid):
        self._r("export_iom_connection_data", pid, cid)
        return ConnectionData.from_dict({"beams": [{"name": "B1"}], "plates": [{"name": "P1"}, {"name": "P2"}], "welds": []})


class FakeSettings(FakeGroup):
    def get_settings(self, pid, search=None):
        self._r("get_settings", pid, search=search)
        if search:
            return {k: v for k, v in self._state.settings.items() if search.lower() in k.lower()}
        return dict(self._state.settings)

    def update_settings(self, pid, request_body=None):
        self._r("update_settings", pid, request_body=request_body)
        self._state.settings.update(request_body or {})
        return dict(self._state.settings)


class FakePresentation(FakeGroup):
    def get_data_scene3_d_text(self, pid, cid):
        self._r("get_data_scene3_d_text", pid, cid)
        return json.dumps({"vertices": [0, 0, 0, 1, 0, 0, 1, 1, 0], "normals": [], "groups": [{"faces": [0, 1, 2]}]})


class FakeClient:
    def __init__(self):
        self.recorder = Recorder()
        self.state = FakeState()
        self.client_id = "fake-client-id"
        rec, st = self.recorder, self.state
        self.project = FakeProject("project", rec, st)
        self.connection = FakeConnection("connection", rec, st)
        self.member = FakeMember("member", rec, st)
        self.operation = FakeOperation("operation", rec, st)
        self.parameter = FakeParameter("parameter", rec, st)
        self.load_effect = FakeLoadEffect("load_effect", rec, st)
        self.material = FakeMaterial("material", rec, st)
        self.calculation = FakeCalculation("calculation", rec, st)
        self.calculation_jobs = FakeCalculationJobs("calculation_jobs", rec, st)
        self.template = FakeTemplate("template", rec, st)
        self.connection_library = FakeConnectionLibrary("connection_library", rec, st)
        self.conversion = FakeConversion("conversion", rec, st)
        self.report = FakeReport("report", rec, st)
        self.export = FakeExport("export", rec, st)
        self.settings = FakeSettings("settings", rec, st)
        self.presentation = FakePresentation("presentation", rec, st)


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings.from_env()
    s.output_dir = tmp_path / "out"
    s.log_dir = tmp_path / "logs"
    s.autostart = False
    s.prewarm = False
    s.api_url = "http://127.0.0.1:59999"
    return s


@pytest.fixture
def fake() -> FakeClient:
    return FakeClient()


@pytest.fixture
def server(settings, fake):
    srv = build_server(settings)
    session = srv.ideastatica_session
    session._client = fake
    session._client_url = settings.api_url
    session.client = lambda: fake  # type: ignore[method-assign]
    session.service.ensure_running = lambda: settings.api_url  # type: ignore[method-assign]
    session.service.is_running = lambda: True  # type: ignore[method-assign]
    return srv


@pytest.fixture
def session(server):
    return server.ideastatica_session


@pytest.fixture
def sample_project() -> Path:
    return FIXTURES / "simple-project.ideaCon"
