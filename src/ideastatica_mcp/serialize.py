"""Conversion of SDK models to JSON friendly structures and result summaries."""

from __future__ import annotations

import json
from typing import Any

import pydantic_core
from pydantic import BaseModel


def to_jsonable(obj: Any) -> Any:
    """Turn SDK (pydantic / OpenAPI generated) objects into plain JSON data.

    Generated models expose ``to_dict()`` which uses the wire aliases (camelCase), drops
    unset ``None`` fields and unwraps ``oneOf`` wrappers - exactly what we want to show.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, bytes):
        return f"<{len(obj)} bytes>"
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return pydantic_core.to_jsonable_python(to_dict(), fallback=str)
        except Exception:  # noqa: BLE001 - fall back to pydantic dump below
            pass
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", by_alias=True, exclude_none=True)
    return pydantic_core.to_jsonable_python(obj, fallback=str)


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    return head + f"\n... [truncated {len(text) - max_chars} characters]", True


def dumps(data: Any) -> str:
    return json.dumps(to_jsonable(data), ensure_ascii=False, indent=2)


def compact_checks(result_summary: list | None) -> list[dict]:
    """Compact view of ``CheckResSummary`` items (from calculate / job results)."""
    checks: list[dict] = []
    for item in result_summary or []:
        data = to_jsonable(item) or {}
        checks.append(
            {
                "name": data.get("name"),
                "utilization_percent": data.get("checkValue"),
                "passed": data.get("checkStatus"),
                "skipped": data.get("skipped"),
                "load_case_id": data.get("loadCaseId"),
                "message": data.get("unityCheckMessage"),
            }
        )
    return checks


def summarize_result_summary(summary: Any) -> dict:
    """``ConResultSummary`` -> {connection_id, passed, checks[]}."""
    data = to_jsonable(summary) or {}
    return {
        "connection_id": data.get("id"),
        "passed": data.get("passed"),
        "checks": compact_checks(getattr(summary, "result_summary", None) or data.get("resultSummary")),
    }


def _named_checks(items: list | None, value_key: str = "unityCheck") -> list[dict]:
    out: list[dict] = []
    for item in items or []:
        data = to_jsonable(item) or {}
        entry = {"name": data.get("name"), "passed": data.get("checkStatus")}
        if value_key in data:
            entry["utilization_percent"] = data.get(value_key)
        for key in ("id", "loadCaseId", "maxStrain", "maxStress", "isFullStrength", "items"):
            if key in data:
                entry[key] = data[key]
        out.append(entry)
    return out


def summarize_check_results(check_res: Any) -> dict:
    """``ConnectionCheckRes`` (detailed results) -> grouped compact dict."""
    data = to_jsonable(check_res) or {}
    messages = []
    raw_messages = (data.get("messages") or {}).get("messages") or []
    for message in raw_messages:
        messages.append({"number": message.get("number"), "description": message.get("description")})
    return {
        "connection_id": data.get("id"),
        "connection_guid": data.get("connectionID"),
        "name": data.get("name"),
        "summary": compact_checks(getattr(check_res, "check_res_summary", None)),
        "plates": _named_checks(getattr(check_res, "check_res_plate", None), value_key="__none__"),
        "welds": _named_checks(getattr(check_res, "check_res_weld", None)),
        "bolts": _named_checks(getattr(check_res, "check_res_bolt", None)),
        "anchors": _named_checks(getattr(check_res, "check_res_anchor", None)),
        "concrete_blocks": _named_checks(getattr(check_res, "check_res_concrete_block", None)),
        "buckling": to_jsonable(getattr(check_res, "buckling_results", None)) or [],
        "messages": messages,
    }


def compact_parameter(param: Any) -> dict:
    data = to_jsonable(param) or {}
    validation = data.get("parameterValidation") or {}
    return {
        "key": data.get("key"),
        "expression": data.get("expression"),
        "value": data.get("value"),
        "unit": data.get("unit"),
        "type": data.get("parameterType"),
        "default": data.get("default"),
        "description": data.get("description"),
        "validation_status": data.get("validationStatus") or validation.get("validationStatus"),
        "validation_message": validation.get("message"),
        "lower_bound": data.get("lowerBound"),
        "upper_bound": data.get("upperBound"),
        "visible": data.get("isVisible"),
    }


def compact_connection(con: Any) -> dict:
    data = to_jsonable(con) or {}
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "identifier": data.get("identifier"),
        "description": data.get("description"),
        "analysis_type": data.get("analysisType"),
        "is_calculated": data.get("isCalculated"),
        "include_buckling": data.get("includeBuckling"),
    }


def compact_project(project: Any) -> dict:
    data = to_jsonable(project) or {}
    info = data.get("projectInfo") or {}
    return {
        "project_id": data.get("projectId"),
        "name": info.get("name"),
        "description": info.get("description"),
        "project_number": info.get("projectNumber"),
        "author": info.get("author"),
        "design_code": info.get("countryCode"),
        "date": info.get("date"),
        "connections": [compact_connection(c) for c in (getattr(project, "connections", None) or [])],
    }
