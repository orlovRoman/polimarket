"""
Тесты для BUG-1, BUG-2, BUG-3 из аудита коммита 7a078fd.
"""
import pytest
import time
from unittest.mock import patch, MagicMock, call
import requests as requests_lib


# ══════════════════════════════════════════════════════════════
# BUG-1: import requests не должен быть внутри except-блока
# ══════════════════════════════════════════════════════════════

def test_no_import_inside_except_block():
    """
    В gemini_client.py не должно быть 'import requests' внутри except-блока.
    BUG: import внутри горячего пути создаёт путаницу и потенциально маскирует
         ошибки, если requests недоступен (хотя он уже импортирован вверху файла).
    FIX: убрать дублирующий import, использовать уже импортированный модуль.
    """
    import pathlib, re
    source = pathlib.Path("agents/shared/utils/gemini_client.py").read_text(encoding="utf-8")

    # Ищем паттерн: "import requests" внутри except-блока (с отступом >= 16 пробелов)
    # Верхнеуровневый "import requests" (без отступа) допустим
    inline_imports = re.findall(r"^ {8,}import requests\b", source, re.MULTILINE)
    assert not inline_imports, (
        f"Найден 'import requests' внутри функции/блока ({len(inline_imports)} раз).\n"
        f"BUG: дублирующий import в except-блоке — мёртвый код и source of confusion.\n"
        f"FIX: используйте верхнеуровневый 'import requests' (уже есть в файле)."
    )


def test_cerebras_http_error_check_uses_top_level_requests():
    """
    isinstance(e, requests.exceptions.HTTPError) должен использовать
    модуль requests, импортированный на уровне модуля, а не локально.
    Это косвенная проверка — убеждаемся, что HTTPError корректно распознаётся.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    rr_saves = []

    def mock_get_memory(key, default=None):
        return rr_saves[-1] if key == "cer_rr_index" and rr_saves else (default or 0)

    def mock_save_memory(key, value, *a, **kw):
        if key == "cer_rr_index":
            rr_saves.append(value)

    def cerebras_http_error(payload, model, key, timeout):
        # Бросаем чистый HTTPError (не ConnectionError)
        resp = MagicMock()
        resp.status_code = 429
        raise requests_lib.exceptions.HTTPError(response=resp)

    def gemini_ok(payload, model, key, timeout):
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3}},
            5, 3
        )

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": ["ck"], "models": ["model-a"], "send_func": cerebras_http_error},
        "gemini": {"keys": ["gk"], "models": ["gemini-2.5-flash"], "send_func": gemini_ok},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()}
    }), \
    patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
    patch("agents.shared.python.db.save_memory", side_effect=mock_save_memory), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):

        result, model = generate_content_with_fallback(
            api_key="gk",
            payload={"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
            agent_name="SCOUT"
        )

    # HTTPError должен был сдвинуть индекс
    assert len(rr_saves) >= 1, (
        "cer_rr_index не был обновлён при HTTPError от Cerebras.\n"
        "Проверьте, что isinstance(e, requests.exceptions.HTTPError) работает корректно."
    )
    assert result is not None, "Результат должен быть получен от Gemini после fallback"


# ══════════════════════════════════════════════════════════════
# BUG-2: _send_cerebras не должен бесполезно спать перед raise_for_status
# ══════════════════════════════════════════════════════════════

def test_send_cerebras_429_does_not_sleep_before_raising():
    """
    _send_cerebras при получении 429 не должна просто sleep(20) и затем
    бросать то же исключение — это бесполезная задержка.
    BUG: time.sleep(20) → response.raise_for_status() → HTTPError бросается в любом случае.
         Задержка 20с не несёт никакой пользы без повторной попытки.
    FIX: либо убрать sleep из _send_cerebras (делегировать retry в generate_content_with_fallback),
         либо после sleep повторить сам запрос.
    """
    from agents.shared.utils import gemini_client

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.raise_for_status.side_effect = requests_lib.exceptions.HTTPError(
        response=mock_response
    )

    sleep_calls = []

    with patch("requests.post", return_value=mock_response), \
         patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        with pytest.raises(requests_lib.exceptions.HTTPError):
            gemini_client._send_cerebras(
                payload={"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
                model="qwen-3-235b-a22b-instruct-2507",
                api_key="fake_key",
                timeout=30
            )

    assert len(sleep_calls) == 0, (
        f"_send_cerebras вызвала time.sleep({sleep_calls}) при 429, но всё равно бросила HTTPError.\n"
        f"BUG: sleep без повторной попытки — потеря {sum(sleep_calls)} секунд впустую.\n"
        f"FIX: убрать time.sleep из _send_cerebras. Retry-логика принадлежит generate_content_with_fallback."
    )


def test_send_cerebras_429_raises_http_error_immediately():
    """
    После удаления sleep: _send_cerebras при 429 должна бросать HTTPError мгновенно
    (не блокировать поток на 20+ секунд).
    """
    from agents.shared.utils import gemini_client
    import time as time_module

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.raise_for_status.side_effect = requests_lib.exceptions.HTTPError(
        response=mock_response
    )

    with patch("requests.post", return_value=mock_response):
        start = time_module.monotonic()
        with pytest.raises(requests_lib.exceptions.HTTPError):
            gemini_client._send_cerebras(
                payload={"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
                model="qwen-3-235b-a22b-instruct-2507",
                api_key="fake_key",
                timeout=30
            )
        elapsed = time_module.monotonic() - start

    assert elapsed < 1.0, (
        f"_send_cerebras заняла {elapsed:.1f}с при 429. Ожидается < 1с.\n"
        f"BUG: time.sleep(20) блокирует поток без пользы.\n"
        f"FIX: убрать time.sleep из _send_cerebras."
    )


def test_send_cerebras_200_does_not_sleep():
    """
    При успешном ответе (200) _send_cerebras не должна вызывать time.sleep.
    Регрессионный тест — убеждаемся, что фикс не сломал happy path.
    """
    from agents.shared.utils import gemini_client

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [{
            "message": {"content": "результат", "tool_calls": []},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}
    }

    sleep_calls = []

    with patch("requests.post", return_value=mock_response), \
         patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        result, in_tok, out_tok = gemini_client._send_cerebras(
            payload={"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
            model="llama3.1-8b",
            api_key="fake_key",
            timeout=30
        )

    assert len(sleep_calls) == 0, f"_send_cerebras спала при успешном ответе: {sleep_calls}"
    assert "candidates" in result


# ══════════════════════════════════════════════════════════════
# BUG-3: Gemini backoff не должен применяться при смене модели
# ══════════════════════════════════════════════════════════════

def test_gemini_backoff_not_applied_between_model_switches():
    """
    time.sleep(backoff) не должен вызываться перед переходом к следующей Gemini-модели.
    Backoff логичен при повторной попытке ОДНОЙ модели — не при смене.
    BUG: при 3 Gemini-моделях суммарная задержка = 0.5+1.0+2.0 = 3.5с даже при мгновенных 404.
    FIX: убрать backoff между моделями, применять только при retry той же модели.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    sleep_calls = []

    def gemini_instant_fail(payload, model, key, timeout):
        raise requests_lib.exceptions.HTTPError(
            response=MagicMock(status_code=404)
        )

    def mock_get_memory(key, default=None):
        return default or 0

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "gemini": {
            "keys": ["k1"],
            "models": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
            "send_func": gemini_instant_fail
        }
    }), \
    patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
    patch("agents.shared.python.db.save_memory"), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"), \
    patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        try:
            generate_content_with_fallback(
                api_key="k1",
                payload={"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
                agent_name="SCOUT"
            )
        except Exception:
            pass

    total_sleep = sum(sleep_calls)
    assert total_sleep == 0, (
        f"generate_content_with_fallback спал {total_sleep:.1f}с суммарно при смене Gemini-моделей.\n"
        f"sleep_calls: {sleep_calls}\n"
        f"BUG: backoff применяется между моделями — бессмысленная задержка.\n"
        f"FIX: убрать time.sleep(backoff) из цикла смены моделей Gemini."
    )


def test_gemini_total_latency_with_3_failing_models_under_threshold():
    """
    При трёх моделях Gemini, каждая из которых мгновенно возвращает ошибку,
    суммарное время выполнения не должно превышать 2 секунды.
    BUG: текущий backoff = 0.5+1.0+2.0 = 3.5с → медленный fallback.
    FIX: убрать sleep между моделями.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG
    import time as time_module

    def gemini_instant_fail(payload, model, key, timeout):
        raise ValueError("Model unavailable")

    def mock_get_memory(key, default=None):
        return default or 0

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "gemini": {
            "keys": ["k1"],
            "models": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
            "send_func": gemini_instant_fail
        }
    }), \
    patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
    patch("agents.shared.python.db.save_memory"), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):

        start = time_module.monotonic()
        try:
            generate_content_with_fallback(
                api_key="k1",
                payload={"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
                agent_name="SCOUT"
            )
        except Exception:
            pass
        elapsed = time_module.monotonic() - start

    assert elapsed < 2.0, (
        f"Полный цикл fallback через 3 Gemini-модели занял {elapsed:.2f}с (ожидается < 2с).\n"
        f"BUG: backoff-задержки между моделями замедляют failover.\n"
        f"FIX: убрать time.sleep из петли смены Gemini-моделей."
    )


# ══════════════════════════════════════════════════════════════
# Регрессия: ранее исправленные баги не регрессировали
# ══════════════════════════════════════════════════════════════

def test_rr_index_normalized_regression():
    """Регрессия BUG-2 из ревью #4: индекс нормализован через % len(models)."""
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    saved_idx = []

    def mock_get_memory(key, default=None):
        return saved_idx[-1] if key == "cer_rr_index" and saved_idx else 0

    def mock_save_memory(key, value, *a, **kw):
        if key == "cer_rr_index":
            saved_idx.append(value)

    def cerebras_ok(payload, model, key, timeout):
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}},
            1, 1
        )

    cer_models = ["a", "b", "c"]

    for _ in range(9):
        with patch.dict(PROVIDERS_CONFIG, {
            "cerebras": {"keys": ["k"], "models": cer_models, "send_func": cerebras_ok},
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

    assert all(v < len(cer_models) for v in saved_idx), (
        f"Регрессия BUG-2 (ревью #4): cer_rr_index вышел за пределы диапазона.\n"
        f"Значения: {saved_idx}"
    )


def test_empty_db_model_falls_back_to_rr_regression():
    """Регрессия BUG-3 из ревью #4: пустой db_model фоллбэчит в round-robin."""
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    used_models = []

    def mock_get_memory(key, default=None):
        if key == "cer_rr_index":
            return 0
        if key == "agent_config_SCOUT":
            return {"provider": "cerebras", "model": ""}
        return default

    def cerebras_send(payload, model, key, timeout):
        used_models.append(model)
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}},
            1, 1
        )

    cer_models = ["qwen-3-235b-a22b-instruct-2507", "gpt-oss-120b"]

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": ["k"], "models": cer_models, "send_func": cerebras_send},
        "gemini": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()}
    }), \
    patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
    patch("agents.shared.python.db.save_memory"), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
        generate_content_with_fallback("", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]}, agent_name="SCOUT")

    assert "" not in used_models, f"Регрессия BUG-3: пустая строка попала в запрос. Модели: {used_models}"
    assert used_models and used_models[0] in cer_models, f"Не использован round-robin. Модели: {used_models}"


# ══════════════════════════════════════════════════════════════
# Итерация 7: Разделение Rate Limit и валидация дополнительных ключей
# ══════════════════════════════════════════════════════════════

@patch("time.sleep")
def test_rate_limit_does_not_throttle_cerebras(mock_sleep):
    """
    Проверяет, что лимиты для Cerebras (60 RPM), OpenRouter (20 RPM)
    и Gemini (динамический: num_keys * 6 RPM) работают независимо.
    """
    from agents.shared.utils.gemini_client import _rate_limit_wait, _request_times
    import os

    # Временно убираем PYTEST_CURRENT_TEST из окружения
    old_env = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        # Сбрасываем счетчики
        _request_times["cerebras"] = []
        _request_times["gemini"] = []
        _request_times["openrouter"] = []

        # Cerebras лимит — 60 RPM. Первые 60 вызовов проходят без сна.
        for _ in range(60):
            _rate_limit_wait(provider="cerebras")
        assert mock_sleep.call_count == 0

        # 61-й вызов должен заснуть
        _rate_limit_wait(provider="cerebras")
        assert mock_sleep.call_count == 1

        mock_sleep.reset_mock()

        # Gemini с 1 ключом — 6 RPM
        for _ in range(6):
            _rate_limit_wait(provider="gemini", num_keys=1)
        assert mock_sleep.call_count == 0
        _rate_limit_wait(provider="gemini", num_keys=1)
        assert mock_sleep.call_count == 1

        mock_sleep.reset_mock()

        # Gemini с 3 ключами — 18 RPM
        _request_times["gemini"] = []
        for _ in range(18):
            _rate_limit_wait(provider="gemini", num_keys=3)
        assert mock_sleep.call_count == 0
        _rate_limit_wait(provider="gemini", num_keys=3)
        assert mock_sleep.call_count == 1

    finally:
        if old_env is not None:
            os.environ["PYTEST_CURRENT_TEST"] = old_env


def test_startup_check_validates_secondary_keys():
    """
    Проверяет валидацию первичного, вторичного и третичного ключей в startup_check.
    Невалидный первичный ключ вызывает RuntimeError.
    Невалидные вторичный/третичный вызывают warning, но не валят приложение.
    """
    import config
    from unittest.mock import patch, MagicMock

    with patch("config.GOOGLE_API_KEY", "primary_val"), \
         patch("config.GOOGLE_API_KEY_SECONDARY", "secondary_val"), \
         patch("config.GOOGLE_API_KEY_THIRD", "third_val"), \
         patch("config.TELEGRAM_BOT_TOKEN", "bot_token"), \
         patch("config.TELEGRAM_CHAT_ID", "chat_id"), \
         patch("config.TG_API_ID", "api_id"), \
         patch("config.TG_API_HASH", "api_hash"), \
         patch("config.setup_logger") as mock_setup_logger:

        mock_logger = MagicMock()
        mock_setup_logger.return_value = mock_logger

        # 1. Все ключи валидны
        def mock_get_success(url, timeout=5):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            return resp

        with patch("requests.get", side_effect=mock_get_success) as mock_get:
            config.startup_check()
            assert mock_get.call_count == 3

        # 2. Вторичный/третичный невалидны, первичный валиден -> предупреждения
        def mock_get_secondary_fail(url, timeout=5):
            resp = MagicMock()
            if "primary_val" in url:
                resp.raise_for_status.return_value = None
            else:
                resp.raise_for_status.side_effect = requests_lib.exceptions.HTTPError("Invalid Key")
            return resp

        with patch("requests.get", side_effect=mock_get_secondary_fail) as mock_get:
            config.startup_check()
            assert mock_get.call_count == 3
            assert mock_logger.warning.call_count == 2

        # 3. Первичный невалиден -> RuntimeError
        def mock_get_primary_fail(url, timeout=5):
            resp = MagicMock()
            if "primary_val" in url:
                resp.raise_for_status.side_effect = requests_lib.exceptions.HTTPError("Invalid Key")
            else:
                resp.raise_for_status.return_value = None
            return resp

        with patch("requests.get", side_effect=mock_get_primary_fail) as mock_get:
            with pytest.raises(RuntimeError) as exc_info:
                config.startup_check()
            assert "Первичный GOOGLE_API_KEY недействителен" in str(exc_info.value)
