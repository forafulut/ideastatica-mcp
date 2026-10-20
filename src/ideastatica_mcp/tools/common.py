"""Helpers shared by the tool groups."""

from __future__ import annotations

import re
import typing
from pathlib import Path
from typing import Any

from mcp_types import ToolAnnotations
from pydantic import BaseModel

from ideastatica_mcp.config import Settings
from ideastatica_mcp.errors import IdeaError

READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)

DESIGN_CODE_ALIASES = {
    "ecen": "ecen", "en": "ecen", "eurocode": "ecen", "eu": "ecen", "ec": "ecen", "ec3": "ecen",
    "american": "american", "aisc": "american", "us": "american", "usa": "american", "aisc360": "american",
    "canada": "canada", "csa": "canada", "canadian": "canada",
    "australia": "australia", "as": "australia", "australian": "australia", "aus": "australia",
    "india": "india", "is": "india", "indian": "india",
    "sia": "sia", "swiss": "sia",
    "rus": "rus", "russia": "rus", "russian": "rus", "sp": "rus",
    "chn": "chn", "china": "chn", "chinese": "chn", "gb": "chn",
    "hkg": "hkg", "hongkong": "hkg", "hong kong": "hkg", "hk": "hkg",
    "none": "none",
}


def normalize_design_code(value: str) -> str:
    key = (value or "").strip().lower().replace("_", "").replace("-", "")
    if key in DESIGN_CODE_ALIASES:
        return DESIGN_CODE_ALIASES[key]
    raise IdeaError(
        f"unknown design code '{value}'. Use one of: ecen, american, canada, australia, india, sia, rus, chn, hkg"
    )


def enum_value(enum_cls: Any, value: str, label: str) -> Any:
    """Case-insensitive lookup of an SDK enum member by its value or name."""
    if isinstance(value, enum_cls):
        return value
    text = str(value).strip()
    for member in enum_cls:
        if member.value.lower() == text.lower() or member.name.lower() == text.lower():
            return member
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    for member in enum_cls:
        if re.sub(r"[^a-z0-9]", "", member.value.lower()) == compact:
            return member
    options = ", ".join(m.value for m in enum_cls)
    raise IdeaError(f"invalid {label} '{value}'. Allowed values: {options}")


def to_expression(value: Any) -> str:
    """Parameter expressions are always strings on the wire."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        if "e" in text or "E" in text:  # NCalc-friendly plain decimal instead of 1e-05
            text = f"{value:.12f}".rstrip("0").rstrip(".") or "0"
        return text
    if value is None:
        raise IdeaError("a parameter expression cannot be null")
    return str(value)


def sanitize_filename(name: str, fallback: str = "output") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name or "").strip(" .")
    return cleaned or fallback


def resolve_output_path(settings: Settings, file_path: str | None, default_name: str, overwrite: bool) -> Path:
    """Where a generated file goes: an explicit path, or ``output_dir/default_name``."""
    if file_path:
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = settings.output_dir / path  # relative names land in the output folder, not the CWD
        if path.is_dir() or file_path.endswith(("/", "\\")):
            path = path / default_name
    else:
        path = settings.output_dir / default_name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise IdeaError(f"{path} already exists - pass overwrite=true to replace it or choose another file_path")
    return path


def read_text_auto(path: Path) -> str:
    """Read a text file whose encoding is unknown (IDEA templates are usually UTF-16)."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if b"\x00" in raw[:400]:
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def write_xml_text(path: Path, text: str) -> int:
    """Write XML honouring a declared utf-16 encoding (what IDEA StatiCa writes itself)."""
    head = text[:200].lower()
    encoding = "utf-16" if 'encoding="utf-16"' in head or "encoding='utf-16'" in head else "utf-8"
    data = text.encode(encoding)
    path.write_bytes(data)
    return len(data)


def _unwrap_model(annotation: Any) -> type[BaseModel] | None:
    """The pydantic model class hidden in ``Optional[Model]`` / ``List[Model]`` annotations."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        found = _unwrap_model(arg)
        if found is not None:
            return found
    return None


def normalize_keys(model_cls: type[BaseModel], data: Any) -> Any:
    """Accept snake_case or camelCase keys and return data keyed by the wire aliases."""
    if isinstance(data, list):
        return [normalize_keys(model_cls, item) for item in data]
    if not isinstance(data, dict):
        return data
    fields = model_cls.model_fields
    by_alias = {(f.alias or name): (name, f) for name, f in fields.items()}
    by_lower = {k.lower(): k for k in by_alias}
    by_name = {name: (f.alias or name) for name, f in fields.items()}
    out: dict[str, Any] = {}
    for key, value in data.items():
        alias = key
        if key in by_name:
            alias = by_name[key]
        elif key not in by_alias and key.lower() in by_lower:
            alias = by_lower[key.lower()]
        elif key not in by_alias:
            camel = re.sub(r"_([a-z0-9])", lambda m: m.group(1).upper(), key)
            alias = camel if camel in by_alias else key
        if alias in by_alias:
            nested = _unwrap_model(by_alias[alias][1].annotation)
            if nested is not None and isinstance(value, (dict, list)):
                value = normalize_keys(nested, value)
        out[alias] = value
    return out


def deep_merge(base: Any, changes: Any) -> Any:
    if isinstance(base, dict) and isinstance(changes, dict):
        merged = dict(base)
        for key, value in changes.items():
            merged[key] = deep_merge(base.get(key), value) if key in base else value
        return merged
    return changes


def merge_into_model(model_cls: type[BaseModel], current: BaseModel | None, changes: dict) -> BaseModel:
    """Apply a (possibly partial, snake_case or camelCase) patch to an SDK model instance."""
    base = current.to_dict() if current is not None and hasattr(current, "to_dict") else {}
    merged = deep_merge(base, normalize_keys(model_cls, changes or {}))
    return model_cls.from_dict(merged)  # type: ignore[attr-defined]


def clip(items: list, limit: int) -> tuple[list, bool]:
    if limit and len(items) > limit:
        return items[:limit], True
    return items, False
