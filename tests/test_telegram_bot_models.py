"""
Тесты для динамического маппинга моделей в Telegram-боте.
Проверяют get_nice_model_name и get_dynamic_models_mapping.
"""
import pytest
from unittest.mock import MagicMock, patch


def test_get_nice_model_name_emoji_mapping():
    """get_nice_model_name должен корректно возвращать имена моделей с эмодзи."""
    from telegram.bot import get_nice_model_name

    # Стандартные Gemini
    assert get_nice_model_name("gemini-2.5-flash") == "✨ Gemini 2.5 Flash"
    assert get_nice_model_name("gemini-2.5-pro") == "🧠 Gemini 2.5 Pro"
    assert get_nice_model_name("gemini-2.0-flash-lite") == "⚡ Gemini 2.0 Flash Lite"
    assert get_nice_model_name("gemini-2.0-flash-exp") == "🧪 Gemini 2.0 Flash Exp"
    assert get_nice_model_name("gemini-2.0-flash-thinking-exp-01-21") == "🤔 Gemini Thinking"

    # OpenRouter
    assert get_nice_model_name("meta-llama/llama-3.3-70b-instruct:free") == "🦙 Llama 3.3"
    assert get_nice_model_name("nvidia/nemotron-3-super-120b-a12b:free") == "🟢 Nemotron 3 (Free)"
    assert get_nice_model_name("z-ai/glm-4.5-air:free") == "🟣 GLM 4.5 Air (Free)"

    # Cerebras
    assert get_nice_model_name("cerebras_round_robin") == "⚡ Cerebras (Round Robin)"
    assert get_nice_model_name("cerebras-model-x") == "⚡ Cerebras (cerebras-model-x)"


def test_get_dynamic_models_mapping_synced_with_config():
    """get_dynamic_models_mapping должен динамически строить маппинг на базе PROVIDERS_CONFIG."""
    from telegram.bot import get_dynamic_models_mapping

    fake_config = {
        "gemini": {
            "models": ["gemini-2.5-flash", "gemini-custom-model"]
        },
        "openrouter": {
            "models": ["openrouter-custom"]
        },
        "cerebras": {
            "models": ["cerebras-custom"]
        }
    }

    with patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", fake_config):
        mapping = get_dynamic_models_mapping()

    # Проверяем, что добавлены модели из конфига
    gemini_custom_key = "gemini_gemini_custom_model"
    assert gemini_custom_key in mapping
    assert mapping[gemini_custom_key] == ("gemini", "gemini-custom-model", "✨ Gemini-Custom-Model")

    # Проверяем, что Thinking добавляется принудительно
    assert "geminithink" in mapping
    assert mapping["geminithink"][1] == "gemini-2.0-flash-thinking-exp-01-21"

    # Проверяем OpenRouter
    or_custom_key = "or_openrouter_custom"
    assert or_custom_key in mapping
    assert mapping[or_custom_key][1] == "openrouter-custom"

    # Проверяем Cerebras
    assert "cerebras" in mapping
    assert mapping["cerebras"][1] == "cerebras_round_robin"
    
    cerebras_custom_key = "cerebras_cerebras_custom"
    assert cerebras_custom_key in mapping
    assert mapping[cerebras_custom_key][1] == "cerebras-custom"
