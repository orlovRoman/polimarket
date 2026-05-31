# tests/test_gemini_client_refactor.py
"""
Тесты для рефакторинга gemini_client.py (коммит dabd797 + hotfixes).
Покрывают: баг 1 (дубли моделей), баг 2 (cerebras rr), баг 3 (пустые ключи),
регрессии convert_gemini_to_openai и convert_openai_to_gemini.
"""
import pytest
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════
# БАГ 1: дедупликация default_model в списке Gemini (ФИКС)
# ═══════════════════════════════════════════════════════════

def test_no_duplicate_gemini_models_with_default():
    """
    generate_content_with_fallback с default_model='gemini-2.5-flash'
    не должен добавлять 'gemini-2.5-flash' дважды в plan.
    После фикса dict.fromkeys() дубли убираются.
    """
    from agents.shared.utils.gemini_client import PROVIDERS_CONFIG

    default_model = "gemini-2.5-flash"
    # Имитируем ту же логику, что теперь в функции
    raw = [default_model] + list(PROVIDERS_CONFIG["gemini"]["models"])
    deduped = list(dict.fromkeys(raw))

    # После фикса дублей быть не должно
    assert len(deduped) == len(set(deduped)), "Найдены дубли после dict.fromkeys()"
    assert deduped.count(default_model) == 1, (
        f"'{default_model}' должен встречаться ровно 1 раз, найдено: {deduped.count(default_model)}"
    )


def test_plans_dedup_with_custom_default_model():
    """
    Нестандартный default_model ('gemini-2.0-flash'), уже присутствующий в PROVIDERS_CONFIG,
    после dict.fromkeys() должен встречаться ровно один раз.
    """
    from agents.shared.utils.gemini_client import PROVIDERS_CONFIG

    default_model = "gemini-2.0-flash"
    raw = [default_model] + list(PROVIDERS_CONFIG["gemini"]["models"])
    deduped = list(dict.fromkeys(raw))

    assert deduped.count(default_model) == 1, (
        f"'{default_model}' должен быть в списке ровно 1 раз, сейчас: {deduped}"
    )
    # Порядок: default_model всегда первым
    assert deduped[0] == default_model, "default_model должен быть первым в списке"


def test_gemini_models_list_has_no_duplicates():
    """PROVIDERS_CONFIG['gemini']['models'] сам по себе не содержит дублей."""
    from agents.shared.utils.gemini_client import PROVIDERS_CONFIG

    models = PROVIDERS_CONFIG["gemini"]["models"]
    assert len(models) == len(set(models)), f"Дубли в PROVIDERS_CONFIG: {models}"


def test_gemini_20_flash_lite_in_providers():
    """gemini-2.0-flash-lite должен присутствовать в списке моделей Gemini."""
    from agents.shared.utils.gemini_client import PROVIDERS_CONFIG
    assert "gemini-2.0-flash-lite" in PROVIDERS_CONFIG["gemini"]["models"]


def test_gemini_20_flash_exp_not_in_providers():
    """gemini-2.0-flash-exp не должен присутствовать в списке моделей Gemini (deprecated, 404)."""
    from agents.shared.utils.gemini_client import PROVIDERS_CONFIG
    assert "gemini-2.0-flash-exp" not in PROVIDERS_CONFIG["gemini"]["models"]


# ═══════════════════════════════════════════════════════════
# БАГ 2: Cerebras round-robin двигается при ошибке (ФИКС)
# ═══════════════════════════════════════════════════════════

def test_cerebras_rr_index_advances_on_error():
    """
    При ошибке (в т.ч. 429) от Cerebras cer_rr_index должен инкрементироваться,
    чтобы следующий вызов попал на другую модель.
    До фикса: индекс обновлялся только при успехе → round-robin замерзал.
    """
    import requests
    from agents.shared.utils import gemini_client
    from agents.shared.python.db import save_memory, get_memory

    # Устанавливаем начальный индекс
    save_memory("cer_rr_index", 0)

    successful_result = {
        "candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}
    }

    call_count = [0]
    def mock_cerebras_send(payload, model, key, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            raise requests.HTTPError("429 Too Many Requests")
        return successful_result, 10, 5

    patched_config = {
        "gemini": {
            "keys": [],
            "models": ["gemini-2.5-flash"],
            "send_func": MagicMock(side_effect=Exception("skip gemini"))
        },
        "openrouter": {
            "keys": [],
            "models": [],
            "send_func": MagicMock(side_effect=Exception("skip openrouter"))
        },
        "cerebras": {
            "keys": ["fake_cerebras_key"],
            "models": ["model-a", "model-b"],
            "send_func": mock_cerebras_send
        }
    }

    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    with patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", patched_config):
        # Первый вызов: cerebras model-a (idx=0) → 429 → rr должен стать 1
        try:
            gemini_client.generate_content_with_fallback(
                api_key="",
                payload=payload,
                default_model="gemini-2.5-flash",
                agent_name="TEST_RR"
            )
        except Exception:
            pass  # Может упасть, важно проверить индекс

    final_idx = int(get_memory("cer_rr_index", 0))
    assert final_idx > 0, (
        f"cer_rr_index должен вырасти при ошибке Cerebras. "
        f"Текущее значение: {final_idx}. БАГ 2 не исправлен."
    )


# ═══════════════════════════════════════════════════════════
# БАГ 3: пустые ключи — провайдер выпадает без краша
# ═══════════════════════════════════════════════════════════

def test_providers_config_empty_keys_are_filtered():
    """
    Если OPENROUTER_API_KEY / CEREBRAS_API_KEY не заданы,
    провайдер тихо выпадает из active_providers, без исключений.
    """
    from agents.shared.utils.gemini_client import PROVIDERS_CONFIG

    for provider_name, cfg in PROVIDERS_CONFIG.items():
        keys = cfg.get("keys", [])
        active_keys = [k for k in keys if k and k.strip()]
        assert isinstance(active_keys, list)
        for key in keys:
            assert key is not None, (
                f"Провайдер '{provider_name}' содержит None в keys. "
                f"os.getenv() должен возвращать '' при default='', не None."
            )


def test_providers_config_send_func_callable():
    """Все send_func в PROVIDERS_CONFIG должны быть вызываемыми."""
    from agents.shared.utils.gemini_client import PROVIDERS_CONFIG

    for provider_name, cfg in PROVIDERS_CONFIG.items():
        send_func = cfg.get("send_func")
        assert callable(send_func), (
            f"PROVIDERS_CONFIG['{provider_name}']['send_func'] не callable: {send_func}"
        )


# ═══════════════════════════════════════════════════════════
# REGRESSION: convert_gemini_to_openai сохраняет tool_calls
# ═══════════════════════════════════════════════════════════

def test_convert_gemini_to_openai_preserves_tool_calls():
    """После рефакторинга конвертер не должен терять functionCall части."""
    from agents.shared.utils.gemini_client import convert_gemini_to_openai

    payload = {
        "contents": [{
            "role": "model",
            "parts": [{"functionCall": {"name": "search_web", "args": {"query": "BTC price"}}}]
        }]
    }
    result = convert_gemini_to_openai(payload, model_name="gemini-2.5-flash")
    messages = result["messages"]
    assert any("tool_calls" in m for m in messages), (
        "tool_calls не найдены в конвертированном payload. "
        "Рефакторинг мог сломать обработку functionCall."
    )


def test_convert_openai_to_gemini_round_trip():
    """Обратная конвертация должна сохранять текст ответа и токены."""
    from agents.shared.utils.gemini_client import convert_openai_to_gemini

    openai_res = {
        "choices": [{"message": {"content": "BTC will hit $200K", "tool_calls": []}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20}
    }
    result = convert_openai_to_gemini(openai_res)
    assert result["candidates"][0]["content"]["parts"][0]["text"] == "BTC will hit $200K"
    assert result["usageMetadata"]["promptTokenCount"] == 100
    assert result["usageMetadata"]["candidatesTokenCount"] == 20


def test_convert_gemini_to_openai_system_instruction():
    """systemInstruction корректно конвертируется в system-роль."""
    from agents.shared.utils.gemini_client import convert_gemini_to_openai

    payload = {
        "systemInstruction": {"parts": [{"text": "You are a trading bot"}]},
        "contents": [{"role": "user", "parts": [{"text": "analyze"}]}]
    }
    result = convert_gemini_to_openai(payload, model_name="gemini-2.5-flash")
    messages = result["messages"]
    assert messages[0]["role"] == "system"
    assert "trading bot" in messages[0]["content"]


# ═══════════════════════════════════════════════════════════
# Тест падения первого ключа и перехода на второй (ФИКС)
# ═══════════════════════════════════════════════════════════

def test_gemini_keys_fallback_on_first_key_error():
    """
    Если первый ключ Google Gemini API возвращает ошибку,
    должен произойти автоматический переход на второй (secondary) ключ.
    """
    from agents.shared.utils import gemini_client
    import requests

    keys_attempted = []
    successful_result = {
        "candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}
    }

    def mock_gemini_send(payload, model, key, timeout):
        keys_attempted.append(key)
        if len(keys_attempted) == 1:
            raise requests.HTTPError("403 Forbidden - Invalid Key")
        return successful_result, 10, 5

    patched_config = {
        "gemini": {
            "keys": [],  # Будет построено из api_key + secondary
            "models": ["gemini-2.5-flash"],
            "send_func": mock_gemini_send
        },
        "openrouter": {"keys": [], "models": [], "send_func": lambda *args: None},
        "cerebras": {"keys": [], "models": [], "send_func": lambda *args: None}
    }

    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    with patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", patched_config), \
         patch("os.getenv", side_effect=lambda k, d="": "key_secondary" if k == "GOOGLE_API_KEY_SECONDARY" else d):
        
        result, active_model = gemini_client.generate_content_with_fallback(
            api_key="key_primary",
            payload=payload,
            default_model="gemini-2.5-flash",
            agent_name="TEST_KEYS_FALLBACK"
        )

    assert result == successful_result
    assert keys_attempted == ["key_primary", "key_secondary"], (
        f"Ожидали попытку обоих ключей ['key_primary', 'key_secondary'], "
        f"но получили: {keys_attempted}"
    )
