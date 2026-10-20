"""Every SDK method the tools call must exist in the installed ideastatica-connection-api package."""

from __future__ import annotations

import re
from pathlib import Path

import ideastatica_connection_api.api_ext.connection_library_ext_api as cle
import ideastatica_connection_api.api_ext.export_ext_api as ee
import ideastatica_connection_api.api_ext.project_ext_api as pe
import ideastatica_connection_api.api_ext.report_ext_api as re_
from ideastatica_connection_api import (
    CalculationApi,
    CalculationJobsApi,
    ConnectionApi,
    ConversionApi,
    LoadEffectApi,
    MaterialApi,
    MemberApi,
    OperationApi,
    ParameterApi,
    PresentationApi,
    SettingsApi,
    TemplateApi,
)

GROUPS = {
    "project": pe.ProjectExtApi,
    "connection": ConnectionApi,
    "member": MemberApi,
    "operation": OperationApi,
    "parameter": ParameterApi,
    "load_effect": LoadEffectApi,
    "material": MaterialApi,
    "calculation": CalculationApi,
    "calculation_jobs": CalculationJobsApi,
    "template": TemplateApi,
    "connection_library": cle.ConnectionLibraryExtApi,
    "conversion": ConversionApi,
    "report": re_.ReportExtApi,
    "export": ee.ExportExtApi,
    "settings": SettingsApi,
    "presentation": PresentationApi,
}

TOOLS_DIR = Path(__file__).resolve().parents[1] / "src" / "ideastatica_mcp" / "tools"
INVOKE = re.compile(r'invoke\(\s*"(?P<group>\w+)"\s*,\s*"(?P<method>\w+)"')
METHOD_DICTS = re.compile(r'"(?P<key>\w+)":\s*"(?P<method>(get|add)_\w+)"')


def collect_calls() -> set[tuple[str, str]]:
    calls: set[tuple[str, str]] = set()
    for path in TOOLS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in INVOKE.finditer(text):
            calls.add((match.group("group"), match.group("method")))
        if path.name == "materials.py":
            for match in METHOD_DICTS.finditer(text):
                calls.add(("material", match.group("method")))
    return calls


def test_all_invoked_methods_exist():
    calls = collect_calls()
    assert len(calls) > 80, "the source scan should find the SDK calls"
    missing = sorted(f"{g}.{m}" for g, m in calls if not callable(getattr(GROUPS[g], m, None)))
    assert not missing, f"methods missing in the installed SDK: {missing}"


def test_base_path_is_api_4():
    import ideastatica_connection_api.api.client_api as client_api

    source = Path(client_api.__file__).read_text(encoding="utf-8")
    assert "/api/4/clients/connect-client" in source
