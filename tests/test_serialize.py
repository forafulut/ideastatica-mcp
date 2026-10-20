from ideastatica_connection_api.models.check_res_summary import CheckResSummary
from ideastatica_connection_api.models.con_result_summary import ConResultSummary
from ideastatica_connection_api.models.idea_parameter import IdeaParameter
from ideastatica_connection_api.models.template_conversions import TemplateConversions

from ideastatica_mcp.serialize import compact_parameter, summarize_result_summary, to_jsonable, truncate_text
from tests.conftest import _mapping


def test_summarize_result_summary_uses_wire_names():
    summary = ConResultSummary(id=3, passed=False, result_summary=[CheckResSummary(name="Bolts", check_value=112.5, check_status=False, skipped=False, load_case_id=2)])
    out = summarize_result_summary(summary)
    assert out == {
        "connection_id": 3,
        "passed": False,
        "checks": [{"name": "Bolts", "utilization_percent": 112.5, "passed": False, "skipped": False, "load_case_id": 2, "message": None}],
    }


def test_compact_parameter():
    param = IdeaParameter(key="t", expression="0.015", value=0.015, unit="m", parameter_type="Float", is_visible=True)
    out = compact_parameter(param)
    assert out["key"] == "t" and out["expression"] == "0.015" and out["unit"] == "m" and out["visible"] is True


def test_to_jsonable_unwraps_oneof_mapping_and_keeps_type_discriminator():
    data = to_jsonable(_mapping())
    assert data["countryCode"] == "ECEN"
    assert data["conversions"][0]["$type"].endswith("PlateMaterialTemplateConversion, IdeaStatiCa.Api")
    # the wire form round-trips through the SDK model again (what apply_template needs)
    rebuilt = TemplateConversions.from_dict(data)
    assert rebuilt.conversions[1].actual_instance.original_value == "HEB200"


def test_truncate_text():
    text, truncated = truncate_text("abcdef", 3)
    assert truncated and text.startswith("abc") and "truncated 3" in text
    assert truncate_text("abc", 10) == ("abc", False)
