from pathlib import Path

import pytest
from ideastatica_connection_api.models.con_member import ConMember
from ideastatica_connection_api.models.con_member_position import ConMemberPosition

from ideastatica_mcp.config import Settings, detect_setup_dirs
from ideastatica_mcp.errors import IdeaError, describe_exception
from ideastatica_mcp.tools.common import (
    enum_value,
    merge_into_model,
    normalize_design_code,
    normalize_keys,
    read_text_auto,
    resolve_output_path,
    sanitize_filename,
    to_expression,
    write_xml_text,
)


def test_to_expression_converts_scalars():
    assert to_expression(15) == "15"
    assert to_expression(0.02) == "0.02"
    assert to_expression(True) == "true"
    assert to_expression("'M20 8.8'") == "'M20 8.8'"
    with pytest.raises(IdeaError):
        to_expression(None)


def test_normalize_keys_accepts_snake_and_camel_case():
    data = {"is_continuous": True, "crossSectionId": 2, "position": {"gama_pitch": 0.1, "offsetEx": 0.2}}
    out = normalize_keys(ConMember, data)
    assert out == {"isContinuous": True, "crossSectionId": 2, "position": {"gamaPitch": 0.1, "offsetEx": 0.2}}


def test_merge_into_model_keeps_untouched_fields():
    current = ConMember(id=2, name="BEAM", is_continuous=False, cross_section_id=1, position=ConMemberPosition(beta_direction=0.5, gama_pitch=0.0))
    patched = merge_into_model(ConMember, current, {"is_continuous": True, "position": {"gama_pitch": 0.3}})
    assert patched.name == "BEAM"
    assert patched.is_continuous is True
    assert patched.cross_section_id == 1
    assert patched.position.beta_direction == 0.5
    assert patched.position.gama_pitch == 0.3


def test_design_code_aliases():
    assert normalize_design_code("AISC") == "american"
    assert normalize_design_code("Eurocode") == "ecen"
    with pytest.raises(IdeaError):
        normalize_design_code("martian")


def test_enum_value_is_case_insensitive():
    from ideastatica_connection_api.models.con_weld_sizing_method_enum import ConWeldSizingMethodEnum

    assert enum_value(ConWeldSizingMethodEnum, "fullstrength", "method") is ConWeldSizingMethodEnum.FULLSTRENGTH
    assert enum_value(ConWeldSizingMethodEnum, "over_strength_factor", "method") is ConWeldSizingMethodEnum.OVERSTRENGTHFACTOR
    with pytest.raises(IdeaError):
        enum_value(ConWeldSizingMethodEnum, "bogus", "method")


def test_xml_roundtrip_honours_utf16(tmp_path: Path):
    text = '<?xml version="1.0" encoding="utf-16"?><T/>'
    path = tmp_path / "t.contemp"
    write_xml_text(path, text)
    assert path.read_bytes().startswith(b"\xff\xfe")
    assert read_text_auto(path) == text
    path8 = tmp_path / "u.xml"
    write_xml_text(path8, "<a/>")
    assert read_text_auto(path8) == "<a/>"


def test_resolve_output_path_guards_overwrite(tmp_path: Path):
    settings = Settings.from_env()
    settings.output_dir = tmp_path / "out"
    target = resolve_output_path(settings, None, "x.pdf", False)
    assert target == tmp_path / "out" / "x.pdf"
    target.write_bytes(b"1")
    with pytest.raises(IdeaError):
        resolve_output_path(settings, None, "x.pdf", False)
    assert resolve_output_path(settings, None, "x.pdf", True) == target
    sub = resolve_output_path(settings, str(tmp_path / "sub") + "\\", "y.pdf", False)
    assert sub == tmp_path / "sub" / "y.pdf"


def test_sanitize_filename():
    assert sanitize_filename('a<b>:"c"/d') == "a_b_c_d"
    assert sanitize_filename("   ") == "output"


def test_describe_api_exception_adds_hint():
    from ideastatica_connection_api.exceptions import NotFoundException

    exc = NotFoundException(status=404, reason="Not Found", body='{"title":"Project not found","status":404}')
    text = describe_exception(exc)
    assert "HTTP 404" in text and "Project not found" in text and "list_open_projects" in text


def test_detect_setup_dirs_returns_paths_or_empty():
    dirs = detect_setup_dirs()
    assert all(d.is_dir() for d in dirs)
