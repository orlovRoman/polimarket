"""
Тесты для BUG-1, BUG-2, BUG-3 из аудита коммита 9fc27b4.
"""
import pytest
from unittest.mock import patch, MagicMock, call
import requests


# ══════════════════════════════════════════════════════════════
# BUG-1: round-robin не должен двигаться при сетевом таймауте
# ══════════════════════════════════════════════════════════════

def test_cerebras_rr_index_not_incremented_on_network_timeout():
    """
    При ConnectionError (сетевой таймаут) round-robin индекс Cerebras
    НЕ должен инкрементироваться — это не вина модели, нет смысла её пропускать.
    BUG: любой except инкрементирует cer_rr_index, поэтому при нестабильной
         сети все модели Cerebras пропускаются за один цикл.
    FIX: инкрементировать только при ответах 429/503/400 (HTTP-ошибки модели),
         но не при ConnectionError/Timeout.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    rr_index_saves = []

    def mock_get_memory(key, default=None):
        if key == "cer_rr_index":
            return 0
        return default

    def mock_save_memory(key, value, *args, **kwargs):
        if key == "cer_rr_index":
            rr_index_saves.append(value)

    # Cerebras выбрасывает ConnectionError (сеть недоступна)
    def cerebras_network_error(payload, model, key, timeout):
        raise requests.exceptions.ConnectionError("Network unreachable")

    # Gemini возвращает успех
    def gemini_ok(payload, model, key, timeout):
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}},
            10, 5
        )

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {
            "keys": ["fake_cerebras_key"],
            "models": ["model-a", "model-b"],
            "send_func": cerebras_network_error
        },
        "gemini": {
            "keys": ["fake_gemini_key"],
            "models": ["gemini-2.5-flash"],
            "send_func": gemini_ok
        },
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()}
    }), \
    patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
    patch("agents.shared.python.db.save_memory", side_effect=mock_save_memory), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="test"):

        result, model = generate_content_with_fallback(
            api_key="fake_gemini_key",
            payload={"contents": [{"parts": [{"text": "test"}], "role": "user"}]},
            agent_name="SCOUT"
        )

    # При ConnectionError round-robin индекс не должен меняться
    network_error_increments = [v for v in rr_index_saves]
    assert len(network_error_increments) == 0, (
        f"cer_rr_index был изменён {len(network_error_increments)} раз при ConnectionError: {network_error_increments}\n"
        f"BUG: инкремент при сетевой ошибке пропускает все модели Cerebras.\n"
        f"FIX: инкрементировать только при HTTP 4xx/5xx (requests.exceptions.HTTPError)."
    )


def test_cerebras_rr_index_incremented_on_429():
    """
    При HTTP 429 (rate limit) round-robin индекс Cerebras ДОЛЖЕН инкрементироваться —
    это значит, что данная модель временно недоступна, переключаемся на следующую.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    rr_index_saves = []

    def mock_get_memory(key, default=None):
        if key == "cer_rr_index":
            return rr_index_saves[-1] if rr_index_saves else 0
        return default

    def mock_save_memory(key, value, *args, **kwargs):
        if key == "cer_rr_index":
            rr_index_saves.append(value)

    call_count = [0]

    def cerebras_rate_limited(payload, model, key, timeout):
        call_count[0] += 1
        # Имитируем 429 как HTTPError
        resp = MagicMock()
        resp.status_code = 429
        resp.text = "Too Many Requests"
        raise requests.exceptions.HTTPError(response=resp)

    def gemini_ok(payload, model, key, timeout):
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}},
            10, 5
        )

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {
            "keys": ["fake_key"],
            "models": ["model-a"],
            "send_func": cerebras_rate_limited
        },
        "gemini": {
            "keys": ["fake_gemini"],
            "models": ["gemini-2.5-flash"],
            "send_func": gemini_ok
        },
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()}
    }), \
    patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
    patch("agents.shared.python.db.save_memory", side_effect=mock_save_memory), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="test"):

        result, model = generate_content_with_fallback(
            api_key="fake_gemini",
            payload={"contents": [{"parts": [{"text": "test"}], "role": "user"}]},
            agent_name="SCOUT"
        )

    # При 429 индекс ДОЛЖЕН сдвинуться
    assert len(rr_index_saves) >= 1, (
        "cer_rr_index не был инкрементирован при HTTP 429. "
        "FIX: инкрементировать при requests.exceptions.HTTPError."
    )


# ══════════════════════════════════════════════════════════════
# BUG-2: cer_rr_index должен нормализоваться, а не расти бесконечно
# ══════════════════════════════════════════════════════════════

def test_cerebras_rr_index_normalized_on_write():
    """
    cer_rr_index при сохранении должен нормализоваться по len(models),
    чтобы не накапливать бесконечно растущий счётчик в БД.
    BUG: save_memory("cer_rr_index", 10001) — бесполезная запись.
    FIX: save_memory("cer_rr_index", (idx + 1) % len(cer_models))
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    saved_values = []
    call_count = [0]

    def mock_get_memory(key, default=None):
        if key == "cer_rr_index":
            return saved_values[-1] if saved_values else 0
        return default

    def mock_save_memory(key, value, *args, **kwargs):
        if key == "cer_rr_index":
            saved_values.append(value)

    def cerebras_ok(payload, model, key, timeout):
        call_count[0] += 1
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3}},
            5, 3
        )

    cer_models = ["model-a", "model-b", "model-c"]

    for _ in range(10):  # Делаем 10 успешных запросов
        with patch.dict(PROVIDERS_CONFIG, {
            "cerebras": {
                "keys": ["fake_key"],
                "models": cer_models,
                "send_func": cerebras_ok
            },
            "gemini": {"keys": [], "models": [], "send_func": MagicMock()},
            "openrouter": {"keys": [], "models": [], "send_func": MagicMock()}
        }), \
        patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
        patch("agents.shared.python.db.save_memory", side_effect=mock_save_memory), \
        patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
        patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
            try:
                generate_content_with_fallback(
                    api_key="",
                    payload={"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
                    agent_name="SCOUT"
                )
            except Exception:
                pass

    # После 10 запросов значения должны быть в диапазоне [0, len(models)-1]
    large_values = [v for v in saved_values if v >= len(cer_models)]
    assert not large_values, (
        f"cer_rr_index сохранял значения >= len(models)={len(cer_models)}: {large_values}\n"
        f"BUG: счётчик растёт бесконечно, создавая мусор в БД.\n"
        f"FIX: save_memory('cer_rr_index', (cer_idx + 1) % len(cer_models))"
    )


def test_cerebras_rr_cycles_through_all_models():
    """
    Проверяет что round-robin проходит по ВСЕМ моделям по циклу,
    а не застревает на одной из-за неправильного индекса.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    current_idx = [0]
    used_models = []
    cer_models = ["model-a", "model-b", "model-c", "model-d"]

    def mock_get_memory(key, default=None):
        if key == "cer_rr_index":
            return current_idx[0]
        return default

    def mock_save_memory(key, value, *args, **kwargs):
        if key == "cer_rr_index":
            current_idx[0] = value % len(cer_models)  # После фикса

    def cerebras_ok(payload, model, key, timeout):
        used_models.append(model)
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}},
            1, 1
        )

    for _ in range(len(cer_models)):
        with patch.dict(PROVIDERS_CONFIG, {
            "cerebras": {"keys": ["key"], "models": cer_models, "send_func": cerebras_ok},
            "gemini": {"keys": [], "models": [], "send_func": MagicMock()},
            "openrouter": {"keys": [], "models": [], "send_func": MagicMock()}
        }), \
        patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
        patch("agents.shared.python.db.save_memory", side_effect=mock_save_memory), \
        patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
        patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
            try:
                generate_content_with_fallback("", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]}, agent_name="SCOUT")
            except Exception:
                pass

    assert len(set(used_models)) == len(cer_models), (
        f"Round-robin не прошёл по всем {len(cer_models)} моделям.\n"
        f"Использованные модели: {used_models}\n"
        f"Уникальные: {set(used_models)}"
    )


# ══════════════════════════════════════════════════════════════
# BUG-3: db_model = None/"" должен fallback на cerebras_round_robin
# ══════════════════════════════════════════════════════════════

def test_cerebras_empty_db_model_falls_back_to_round_robin():
    """
    Если в БД сохранён agent_config с provider=cerebras, model="" (пустая строка),
    система должна использовать round-robin, а не отправить запрос с model="".
    BUG: plans.insert(0, ("cerebras", "")) → Cerebras API вернёт 400 Bad Request.
    FIX: if not db_model: db_model = "cerebras_round_robin" перед проверкой.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    actual_models_used = []

    def mock_get_memory(key, default=None):
        if key == "cer_rr_index":
            return 0
        if key == "agent_config_SCOUT":
            return {"provider": "cerebras", "model": ""}  # пустая модель
        return default

    def cerebras_send(payload, model, key, timeout):
        actual_models_used.append(model)
        if not model:
            raise ValueError(f"Model name is empty — API would return 400")
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}},
            1, 1
        )

    cer_models = ["qwen-3-235b-a22b-instruct-2507", "gpt-oss-120b"]

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": ["key"], "models": cer_models, "send_func": cerebras_send},
        "gemini": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()}
    }), \
    patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
    patch("agents.shared.python.db.save_memory"), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):

        try:
            generate_content_with_fallback("", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]}, agent_name="SCOUT")
        except Exception:
            pass

    # Пустая строка не должна попасть в запрос
    assert "" not in actual_models_used, (
        f"Cerebras получил запрос с model='' (пустая строка).\n"
        f"Использованные модели: {actual_models_used}\n"
        f"BUG: if not db_model: пропускается, и plans.insert(0, ('cerebras', '')) выполняется.\n"
        f"FIX: if not db_model: db_model = 'cerebras_round_robin'"
    )
    if actual_models_used:
        assert actual_models_used[0] in cer_models, (
            f"Первая использованная модель '{actual_models_used[0]}' не из списка моделей Cerebras.\n"
            f"Ожидался round-robin из: {cer_models}"
        )


def test_cerebras_none_db_model_falls_back_to_round_robin():
    """
    Если agent_config содержит model=None — должен использоваться round-robin.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    actual_models_used = []

    def mock_get_memory(key, default=None):
        if key == "cer_rr_index":
            return 1  # Второй индекс
        if key == "agent_config_SWING":
            return {"provider": "cerebras", "model": None}  # None модель
        return default

    def cerebras_send(payload, model, key, timeout):
        actual_models_used.append(model)
        if model is None:
            raise TypeError(f"model cannot be None")
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}},
            1, 1
        )

    cer_models = ["qwen-3-235b-a22b-instruct-2507", "gpt-oss-120b", "zai-glm-4.7"]

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": ["key"], "models": cer_models, "send_func": cerebras_send},
        "gemini": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()}
    }), \
    patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
    patch("agents.shared.python.db.save_memory"), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):

        try:
            generate_content_with_fallback("", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]}, agent_name="SWING")
        except Exception:
            pass

    assert None not in actual_models_used, (
        f"Cerebras получил запрос с model=None.\n"
        f"FIX: if not db_model: db_model = 'cerebras_round_robin'"
    )


# ══════════════════════════════════════════════════════════════
# Интеграционный тест: полный fallback-цикл
# ══════════════════════════════════════════════════════════════

def test_full_fallback_chain_cerebras_to_gemini():
    """
    Интеграционный тест: если Cerebras недоступен (все модели 429),
    система должна перейти к Gemini и вернуть успешный результат.
    Проверяет, что после BUG-1/BUG-2 фикса цепочка работает корректно.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    current_idx = [0]

    def mock_get_memory(key, default=None):
        if key == "cer_rr_index":
            return current_idx[0]
        return default

    def mock_save_memory(key, value, *args, **kwargs):
        if key == "cer_rr_index":
            current_idx[0] = value

    cerebras_call_count = [0]

    def cerebras_all_429(payload, model, key, timeout):
        cerebras_call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 429
        raise requests.exceptions.HTTPError(response=resp)

    gemini_call_count = [0]

    def gemini_ok(payload, model, key, timeout):
        gemini_call_count[0] += 1
        return (
            {"candidates": [{"content": {"parts": [{"text": "success from gemini"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10}},
            20, 10
        )

    cer_models = ["model-a", "model-b"]

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": ["cer_key"], "models": cer_models, "send_func": cerebras_all_429},
        "gemini": {"keys": ["gem_key"], "models": ["gemini-2.5-flash"], "send_func": gemini_ok},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()}
    }), \
    patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
    patch("agents.shared.python.db.save_memory", side_effect=mock_save_memory), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="test"), \
    patch("time.sleep"):  # Ускоряем тест — не ждём backoff

        result, model_used = generate_content_with_fallback(
            api_key="gem_key",
            payload={"contents": [{"parts": [{"text": "test"}], "role": "user"}]},
            agent_name="SHADOW"
        )

    assert result is not None, "Результат не должен быть None после fallback на Gemini"
    assert gemini_call_count[0] >= 1, "Gemini не был вызван после отказа Cerebras"
    assert "gemini" in model_used.lower() or model_used == "gemini-2.5-flash", (
        f"Ожидался Gemini после отказа Cerebras, получен: {model_used}"
    )
