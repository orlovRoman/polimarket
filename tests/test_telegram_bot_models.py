"""
Тесты для динамического маппинга моделей в Telegram-боте.
Проверяют get_nice_model_name, get_dynamic_models_mapping и исправления 4 багов.
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

    # Проверяем, что добавлены модели из конфига (без дублирования префикса)
    gemini_custom_key = "gemini_custom_model"
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


# ══════════════════════════════════════════════════════════
# BUG-2: get_dynamic_models_mapping — нет дублирующего gemini_ префикса
# ══════════════════════════════════════════════════════════

def test_dynamic_models_mapping_no_duplicate_gemini_prefix():
    """
    Ключи маппинга для Gemini-моделей не должны начинаться с 'gemini_gemini_'.
    BUG: key = 'gemini_' + m.replace(...) → 'gemini_gemini_2_5_flash'
    FIX: key = m.replace(...) (без повторного префикса)
    """
    from telegram.bot import get_dynamic_models_mapping
    mapping = get_dynamic_models_mapping()

    bad_keys = [k for k in mapping if k.startswith("gemini_gemini_")]
    assert not bad_keys, (
        f"Найдены ключи с дублирующимся префиксом 'gemini_gemini_': {bad_keys}. "
        f"Исправьте: key = m.replace('-','_').replace('.','_') без добавления 'gemini_'."
    )


def test_dynamic_models_mapping_keys_are_unique():
    """Все ключи маппинга уникальны."""
    from telegram.bot import get_dynamic_models_mapping
    mapping = get_dynamic_models_mapping()
    keys = list(mapping.keys())
    assert len(keys) == len(set(keys)), f"Дублирующиеся ключи в маппинге: {[k for k in keys if keys.count(k) > 1]}"


def test_dynamic_models_mapping_values_have_correct_structure():
    """Каждое значение маппинга — кортеж (provider, model_id, display_name)."""
    from telegram.bot import get_dynamic_models_mapping
    mapping = get_dynamic_models_mapping()
    for key, val in mapping.items():
        assert isinstance(val, tuple) and len(val) == 3, \
            f"Значение маппинга для ключа '{key}' должно быть tuple из 3 элементов, получено: {val}"
        provider, model_id, display_name = val
        assert isinstance(provider, str) and provider in ("gemini", "openrouter", "cerebras"), \
            f"Неизвестный провайдер '{provider}' для ключа '{key}'"
        assert isinstance(model_id, str) and model_id, \
            f"Пустой model_id для ключа '{key}'"
        assert isinstance(display_name, str) and display_name, \
            f"Пустой display_name для ключа '{key}'"


# ══════════════════════════════════════════════════════════
# BUG-1: callback_data длина ≤ 64 байта
# ══════════════════════════════════════════════════════════

def test_model_callback_data_length():
    """
    Все callback_data для выбора модели (sm_{agent}_{key}) должны быть ≤ 64 байта.
    aiogram/Telegram BotAPI жёстко ограничивает callback_data 64 символами.
    """
    from telegram.bot import get_dynamic_models_mapping

    agents = ["NEXUS", "SCOUT", "SWING", "SHADOW", "ARBITRAGE"]
    mapping = get_dynamic_models_mapping()

    too_long = []
    for agent in agents:
        for key in mapping:
            cb = f"sm_{agent}_{key}"
            if len(cb.encode("utf-8")) > 64:
                too_long.append((agent, key, len(cb.encode("utf-8")), cb))

    assert not too_long, (
        f"callback_data превышает 64 байта для {len(too_long)} комбинаций:\n"
        + "\n".join(f"  [{size}b] {cb}" for _, _, size, cb in too_long[:5])
        + "\nРешение: хэшируйте ключ модели или сократите имена."
    )


# ══════════════════════════════════════════════════════════
# BUG-3: estimate_llm_cost — "pro" не даёт false-positive для не-Gemini моделей
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("model,expected_tier", [
    ("gemini-2.5-flash-preview-05-20", "flash"),
    ("gemini-2.0-flash", "flash"),
    ("gemini-2.5-pro", "pro"),
    ("gemini-1.5-pro", "pro"),
    ("meta-llama/llama-3.3-70b-instruct:free", "free"),
    # Эти НЕ должны попасть в тариф pro:
    ("openrouter/prometheus-eval-7b", "fallback"),  # содержит "pro" в имени
    ("nvidia/nemotron-3-super-120b:free", "free"),
    ("z-ai/glm-4.5-air:free", "free"),
])
def test_estimate_llm_cost_correct_tier(model, expected_tier):
    """
    Тариф должен определяться корректно:
    - 'prometheus' (содержит 'pro') → fallback, не pro-тариф
    - free-модели → $0.00
    """
    from telegram.bot import estimate_llm_cost

    cost = estimate_llm_cost(model, input_tokens=1_000_000, output_tokens=1_000_000)

    if expected_tier == "free":
        assert cost == 0.0, f"Модель '{model}' должна быть бесплатной, получено: ${cost}"
    elif expected_tier == "flash":
        # Flash: $0.075 + $0.30 = $0.375 за 1M+1M токенов
        assert abs(cost - 0.375) < 0.001, f"Неверный тариф flash для '{model}': ${cost}"
    elif expected_tier == "pro":
        # Pro: $1.25 + $5.00 = $6.25 за 1M+1M токенов
        assert abs(cost - 6.25) < 0.001, f"Неверный тариф pro для '{model}': ${cost}"
    elif expected_tier == "fallback":
        # Fallback: $0.50 + $1.50 = $2.00 за 1M+1M токенов
        assert abs(cost - 2.00) < 0.001, (
            f"Модель '{model}' содержит 'pro' в имени, но НЕ является Gemini Pro. "
            f"Должен применяться fallback тариф ($2.00), получено: ${cost}. "
            f"Исправьте: 'gemini' in model and 'pro' in model"
        )


def test_estimate_llm_cost_zero_tokens():
    """Нулевые токены → нулевая стоимость."""
    from telegram.bot import estimate_llm_cost
    assert estimate_llm_cost("gemini-2.5-pro", 0, 0) == 0.0
    assert estimate_llm_cost("gemini-2.5-flash", 0, 0) == 0.0


# ══════════════════════════════════════════════════════════
# BUG-4: send_models_menu — параллельность DB-запросов
# ══════════════════════════════════════════════════════════

def test_send_models_menu_uses_gather(monkeypatch):
    """
    send_models_menu должен использовать asyncio.gather для параллельных DB-запросов,
    а не последовательные await asyncio.to_thread.
    Этот тест проверяет количество последовательных to_thread вызовов — их не должно быть 5+.
    """
    import pathlib

    source = pathlib.Path("telegram/bot.py").read_text(encoding="utf-8")
    # Ищем паттерн: несколько await asyncio.to_thread внутри for-цикла по agents
    # Простая текстовая эвристика: в функции send_models_menu должно быть gather или
    # не более 1 to_thread внутри цикла
    
    # Считаем to_thread в секции send_models_menu
    in_func = False
    to_thread_in_loop = 0
    in_for_loop = False
    
    for line in source.splitlines():
        if "async def send_models_menu" in line:
            in_func = True
        if in_func and "async def " in line and "send_models_menu" not in line:
            in_func = False
        if in_func and "for agent in agents" in line:
            in_for_loop = True
        if in_func and in_for_loop and "asyncio.to_thread" in line:
            to_thread_in_loop += 1

    assert to_thread_in_loop <= 1 or "gather" in source, (
        f"send_models_menu содержит {to_thread_in_loop} последовательных asyncio.to_thread "
        f"внутри цикла for agent in agents. "
        f"Используйте asyncio.gather для параллельного выполнения."
    )


# ══════════════════════════════════════════════════════════
# Smoke-тест: get_nice_model_name покрывает все популярные модели
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("model_id,expected_contains", [
    ("gemini-2.5-flash", "Flash"),
    ("gemini-2.5-pro", "Pro"),
    ("gemini-2.0-flash-lite", "Lite"),
    ("meta-llama/llama-3.3-70b-instruct:free", "Llama"),
    ("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron"),
    ("z-ai/glm-4.5-air:free", "GLM"),
    ("cerebras_round_robin", "Cerebras"),
])
def test_get_nice_model_name_smoke(model_id, expected_contains):
    """Все популярные модели получают красивое читаемое имя."""
    from telegram.bot import get_nice_model_name
    result = get_nice_model_name(model_id)
    assert expected_contains in result, (
        f"get_nice_model_name('{model_id}') = '{result}', "
        f"ожидалось вхождение '{expected_contains}'"
    )
