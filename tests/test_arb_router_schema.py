import json
import pytest

def test_schema_all_types_lowercase():
    """Gemini API требует строго lowercase типы в responseSchema."""
    from core.arb_router import _SCHEMA

    def collect_types(obj, path=""):
        if isinstance(obj, dict):
            if "type" in obj:
                yield path, obj["type"]
            for k, v in obj.items():
                yield from collect_types(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                yield from collect_types(item, f"{path}[{i}]")

    errors = []
    for path, t in collect_types(_SCHEMA):
        if t != t.lower():
            errors.append(f"[{path}] type='{t}' → должно быть '{t.lower()}'")
    
    assert not errors, (
        "Обнаружены uppercase типы в _SCHEMA (Gemini требует lowercase):\n"
        + "\n".join(errors)
    )

def test_schema_required_fields_present():
    from core.arb_router import _SCHEMA
    props = set(_SCHEMA.get("properties", {}).keys())
    required = set(_SCHEMA.get("required", []))
    assert required <= props, f"Отсутствуют required поля: {required - props}"

def test_schema_json_serializable():
    from core.arb_router import _SCHEMA
    assert json.loads(json.dumps(_SCHEMA))["type"] == "object"
