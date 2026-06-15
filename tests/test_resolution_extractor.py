import pytest

# Симулируем логику парсинга ответа LLM
def parse_llm_response(data: dict) -> dict | None:
    """Зеркало исправленной логики из resolution_extractor.py."""
    import json

    candidates = data.get("candidates")
    if not candidates:
        return None
    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        return None
    text = parts[0].get("text")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def make_response(text: str | None) -> dict:
    if text is None:
        return {"candidates": [{"content": {"parts": [{}]}}]}
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


@pytest.mark.parametrize("data,expected", [
    # Нормальный ответ
    (make_response('{"source": "AP", "confidence": 0.9}'), {"source": "AP", "confidence": 0.9}),
    # Пустые candidates (safety block)
    ({"candidates": []}, None),
    # Нет ключа candidates
    ({}, None),
    # Пустой parts
    ({"candidates": [{"content": {"parts": []}}]}, None),
    # text = None
    (make_response(None), None),
    # Невалидный JSON — БАГ без try/except роняет задачу
    (make_response("not a json at all"), None),
    # Частичный JSON
    (make_response('{"source": "AP"'), None),
    # Пустая строка
    (make_response(""), None),
])
def test_parse_llm_response(data, expected):
    result = parse_llm_response(data)
    assert result == expected


def test_invalid_json_does_not_raise():
    """json.JSONDecodeError должен перехватываться, не падать наружу."""
    data = make_response("Извини, я не могу ответить на этот вопрос.")
    try:
        result = parse_llm_response(data)
        assert result is None
    except Exception as e:
        pytest.fail(f"Исключение не должно выбрасываться: {e}")
