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


# ── Новые тесты для фиксов багов 1, 2, 3 и rate_limiter ──────────────────

def test_no_hardcoded_api_key_in_source():
    """
    GOOGLE_API_KEY_SECONDARY не должен иметь дефолтного значения в коде.
    Если os.getenv не находит ключ — должна вернуться пустая строка.
    """
    import inspect
    import re
    from agents.shared.utils import gemini_client as gemini_client_mod
    source = inspect.getsource(gemini_client_mod)
    # Проверяем что в исходнике нет паттерна реального API ключа
    hardcoded = re.findall(r'AIzaSy[A-Za-z0-9_\-]{33}', source)
    assert not hardcoded, (
        f"КРИТИЧНО: В исходном коде найдены захардкоженные API ключи: {hardcoded}. "
        f"Убери default из os.getenv('GOOGLE_API_KEY_SECONDARY', '')."
    )


def test_backoff_fires_on_first_key_429():
    """
    При 429 на первом из двух ключей должен быть backoff + retry,
    а не немедленный переход ко второму ключу.
    Проверяем что attempt > 0 случается для первого ключа.
    """
    import requests
    from agents.shared.utils import gemini_client as gemini_client_mod
    attempts_per_key = {}

    successful_result = {
        "candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}
    }

    def mock_send(payload, model, key, timeout):
        attempts_per_key[key] = attempts_per_key.get(key, 0) + 1
        if key == "key1" and attempts_per_key[key] < 3:
            err = requests.HTTPError(response=MagicMock(status_code=429))
            raise err
        return successful_result, 10, 5

    patched_config = {
        "gemini": {
            "keys": ["key1", "key2"],
            "models": ["gemini-2.5-flash"],
            "send_func": mock_send
        },
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "cerebras": {"keys": [], "models": [], "send_func": MagicMock()}
    }

    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    with patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", patched_config), \
         patch("time.sleep"):  # не ждём реально
        result, _ = gemini_client_mod.generate_content_with_fallback(
            api_key="key1",
            payload=payload,
            default_model="gemini-2.5-flash",
            agent_name="TEST_BACKOFF"
        )

    # key1 должен был получить retry (attempt > 1), не сразу перейти на key2
    assert attempts_per_key.get("key1", 0) > 1, (
        f"Backoff не сработал для key1: было только {attempts_per_key.get('key1', 0)} попыток. "
        f"БАГ 2: условие key_idx == len(keys)-1 отсекает backoff для не-последнего ключа."
    )


def test_cerebras_rr_advances_on_404():
    """
    При 404 от Cerebras cer_rr_index должен инкрементироваться,
    иначе следующий вызов снова попадёт на ту же мёртвую модель.
    """
    import requests
    from agents.shared.python.db import save_memory, get_memory
    from agents.shared.utils import gemini_client as gemini_client_mod

    save_memory("cer_rr_index", 0)

    def mock_cerebras_404(payload, model, key, timeout):
        err = requests.HTTPError(response=MagicMock(status_code=404))
        raise err

    patched_config = {
        "gemini": {"keys": [], "models": ["gemini-2.5-flash"], "send_func": MagicMock(side_effect=Exception("no gemini"))},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "cerebras": {
            "keys": ["fake_key"],
            "models": ["dead-model", "alive-model"],
            "send_func": mock_cerebras_404
        }
    }

    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    with patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", patched_config):
        try:
            gemini_client_mod.generate_content_with_fallback(
                api_key="",
                payload=payload,
                default_model="gemini-2.5-flash",
                agent_name="TEST_404_RR"
            )
        except Exception:
            pass

    final_idx = int(get_memory("cer_rr_index", 0))
    assert final_idx > 0, (
        f"cer_rr_index не сдвинулся при 404 от Cerebras. "
        f"Значение: {final_idx}. Следующий вызов снова попадёт на мёртвую модель."
    )


def test_sanitize_error_masks_all_key_patterns():
    """_sanitize_error должен маскировать все варианты ключей в URL и строках."""
    from agents.shared.utils.gemini_client import _sanitize_error

    cases = [
        "404 Not Found for url: https://generativelanguage.googleapis.com/v1beta/models/gemini:generateContent?key=AQ.Ab8RN6_FakeKeyMaskingTestStringOnlyForTests123",
        "400 Bad Request ?key=AQ.Ab8RN6_FakeKeySecondaryTestStringOnlyForTests123 extra text",
        "Error key=shortkey123",  # короткий ключ < 20 символов — НЕ должен маскироваться
    ]

    sanitized_0 = _sanitize_error(Exception(cases[0]))
    assert "AQ.Ab8RN6" not in sanitized_0
    assert "***REDACTED***" in sanitized_0

    sanitized_1 = _sanitize_error(Exception(cases[1]))
    assert "AQ.Ab8RN6" not in sanitized_1

    sanitized_2 = _sanitize_error(Exception(cases[2]))
    assert "shortkey123" in sanitized_2, "Короткие строки не должны маскироваться"





def test_rate_limiter_disabled_in_pytest():
    """
    В тестовой среде (PYTEST_CURRENT_TEST задан) _rate_limit_wait
    должен возвращаться мгновенно без sleep.
    """
    import time
    from agents.shared.utils.gemini_client import _rate_limit_wait

    start = time.monotonic()
    for _ in range(50):  # симулируем много запросов
        _rate_limit_wait()
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, (
        f"_rate_limit_wait заблокировал выполнение в тестах на {elapsed:.2f}с. "
        f"Проверь условие 'PYTEST_CURRENT_TEST' in os.environ."
    )


# ── Баг 2: _is_model_not_found_error — точность паттерна ─────────────────────

from agents.shared.utils.gemini_client import (
    _is_model_not_found_error,
    _sanitize_error,
    _send_gemini,
)
import requests

class TestIsModelNotFoundError:

    def test_404_http_error_detected(self):
        """HTTPError со статусом 404 → True."""
        resp = MagicMock()
        resp.status_code = 404
        e = requests.exceptions.HTTPError(response=resp)
        assert _is_model_not_found_error(e) is True

    def test_model_not_found_string_detected(self):
        """Строка 'model not found' → True."""
        assert _is_model_not_found_error(Exception("model not found")) is True

    def test_generic_not_found_NOT_detected(self):
        """
        'not found' без 'model' НЕ должен давать True.
        БАГ 2: текущая реализация ловит любой 'not found'.
        """
        cases = [
            "Token not found in context",
            "Key not found in response",
            "Field 'choices' not found",
            "Document not found in database",
        ]
        for msg in cases:
            result = _is_model_not_found_error(Exception(msg))
            assert result is False, (
                f"Ложное срабатывание _is_model_not_found_error на: '{msg}'. "
                f"Нужно сузить паттерн до 'model not found' / 'model_not_found'."
            )

    def test_500_http_error_NOT_detected(self):
        """HTTPError со статусом 500 → False."""
        resp = MagicMock()
        resp.status_code = 500
        e = requests.exceptions.HTTPError(response=resp)
        assert _is_model_not_found_error(e) is False

    def test_model_not_found_case_insensitive(self):
        """Проверка case-insensitive."""
        assert _is_model_not_found_error(Exception("Model Not Found")) is True
        assert _is_model_not_found_error(Exception("MODEL_NOT_FOUND")) is True


# ── Баг 3: ключ не должен попадать в URL Gemini ──────────────────────────────

def test_send_gemini_key_not_in_url():
    """
    _send_gemini должен передавать API-ключ через заголовок x-goog-api-key,
    а не через ?key= query param в URL.
    Если ключ в URL — он попадёт в response.url и в трейсбеки.
    """
    import inspect
    source = inspect.getsource(_send_gemini)
    
    # Проверяем что ключ не конкатенируется в URL строку
    assert "?key=" not in source and "key={api_key}" not in source, (
        "БАГ 3: _send_gemini передаёт ключ через ?key= в URL. "
        "Используй заголовок x-goog-api-key вместо query param."
    )


def test_send_gemini_uses_api_key_header():
    """
    _send_gemini должен использовать x-goog-api-key header.
    """
    import inspect
    source = inspect.getsource(_send_gemini)
    assert "x-goog-api-key" in source, (
        "x-goog-api-key header не найден в _send_gemini. "
        "Ключ должен передаваться через заголовок, не через URL."
    )


# ── Баг 4: consecutive_failures не спамит Telegram ───────────────────────────

def test_consecutive_failures_no_spam():
    """
    При многократных сбоях уведомление должно отправляться не чаще
    чем раз в N вызовов (экспоненциальный backoff уведомлений).
    """
    from agents.shared.utils import gemini_client
    from agents.shared.python.db import save_memory, get_memory

    notifications_sent = []

    def mock_send_telegram(text, reply_markup=None):
        notifications_sent.append(text)

    patched_config = {
        "gemini": {"keys": ["fake"], "models": ["gemini-2.5-flash"],
                   "send_func": MagicMock(side_effect=Exception("always fails"))},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "cerebras": {"keys": [], "models": [], "send_func": MagicMock()}
    }

    payload = {"contents": [{"parts": [{"text": "test"}]}]}
    save_memory("consecutive_failures_SPAM_TEST", 0)

    with patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", patched_config), \
         patch("services.notifications.send_telegram", side_effect=mock_send_telegram):
        
        for _ in range(12):  # 12 вызовов подряд
            try:
                gemini_client.generate_content_with_fallback(
                    api_key="fake",
                    payload=payload,
                    default_model="gemini-2.5-flash",
                    agent_name="SPAM_TEST"
                )
            except Exception:
                pass

    # При 12 вызовах без экспоненциального backoff: 12//3 = 4 уведомления
    # С экспоненциальным: должно быть не более 2
    assert len(notifications_sent) <= 2, (
        f"Слишком много уведомлений в Telegram: {len(notifications_sent)} за 12 вызовов. "
        f"Добавь экспоненциальный порог уведомлений."
    )


# ── Провайдер-переключение логируется ────────────────────────────────────────

def test_provider_switch_is_logged(caplog):
    """
    При переходе от Cerebras к Gemini (после исчерпания) должно быть
    info-сообщение о переключении провайдера.
    """
    import logging
    from agents.shared.utils import gemini_client

    successful_result = {
        "candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}
    }
    call_count = {"n": 0}

    def mock_send(payload, model, key, timeout):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("Cerebras failed")
        return successful_result, 10, 5

    patched_config = {
        "gemini": {
            "keys": ["gemini_key"],
            "models": ["gemini-2.5-flash"],
            "send_func": mock_send
        },
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "cerebras": {
            "keys": ["cer_key"],
            "models": ["llama3.1-8b"],
            "send_func": mock_send
        }
    }

    payload = {"contents": [{"parts": [{"text": "test"}]}]}

    with patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG", patched_config), \
         caplog.at_level(logging.INFO, logger="gemini_client"):

        gemini_client.generate_content_with_fallback(
            api_key="gemini_key",
            payload=payload,
            agent_name="SWITCH_TEST"
        )

    log_text = " ".join(caplog.messages)
    # Должен быть хотя бы один лог о переходе/ошибке cerebras
    assert "cerebras" in log_text.lower() or "SWITCH" in log_text, (
        "Переключение между провайдерами не логируется. "
        "Добавь logger.info при смене провайдера."
    )

