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
    Проверяет, что лимиты для Cerebras (500 RPM = минимум из zai-glm-4.7:500, gpt-oss-120b:1000),
    OpenRouter (20 RPM) и Gemini (динамический: num_keys * 6 RPM) работают независимо.
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

        # Cerebras лимит — 500 RPM. Первые 500 вызовов проходят без сна.
        for _ in range(500):
            _rate_limit_wait(provider="cerebras")
        assert mock_sleep.call_count == 0

        # 501-й вызов должен заснуть
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

    mock_logger = MagicMock()
    with patch("config.GOOGLE_API_KEY", "primary_val"), \
         patch("config.GOOGLE_API_KEY_SECONDARY", "secondary_val"), \
         patch("config.GOOGLE_API_KEY_THIRD", "third_val"), \
         patch("config.TELEGRAM_BOT_TOKEN", "bot_token"), \
         patch("config.TELEGRAM_CHAT_ID", "chat_id"), \
         patch("config.TG_API_ID", "api_id"), \
         patch("config.TG_API_HASH", "api_hash"), \
         patch("config.logger", mock_logger):

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


def test_cerebras_rpm_independent_of_num_keys():
    """RPM Cerebras не зависит от числа ключей — у них нет per-key лимитов."""
    from agents.shared.utils.gemini_client import _rate_limit_wait, _request_times
    import os

    old_env = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        _request_times["cerebras"] = []
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            for _ in range(60):
                _rate_limit_wait(provider="cerebras", num_keys=3)  # 3 ключа, не меняет RPM
        assert not sleep_calls, "Cerebras RPM не должен зависеть от num_keys"
    finally:
        if old_env:
            os.environ["PYTEST_CURRENT_TEST"] = old_env


def test_gemini_keys_order_is_deterministic():
    """Порядок ключей Gemini должен быть стабильным, не зависеть от порядка env vars."""
    from agents.shared.utils import gemini_client as gc
    import os

    with patch.dict(os.environ, {
        "GOOGLE_API_KEY_THIRD": "key_c",
        "GOOGLE_API_KEY_SECONDARY": "key_b",
        "GOOGLE_API_KEY_EXTRA": "key_x",  # неизвестная переменная
    }):
        keys = gc._collect_gemini_keys("key_a")

    assert keys[0] == "key_a"          # первичный всегда первый
    assert keys[1] == "key_b"          # SECONDARY второй
    assert keys[2] == "key_c"          # THIRD третий
    assert "key_x" not in keys         # EXTRA игнорируется


def test_attempt_loop_does_not_retry_on_generic_error():
    """При ValueError цикл attempt не делает повторных попыток."""
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    call_count = [0]

    def gemini_value_error(payload, model, key, timeout):
        call_count[0] += 1
        raise ValueError("generic error")

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "gemini": {
            "keys": ["k1"],
            "models": ["gemini-2.5-flash"],
            "send_func": gemini_value_error,
        }
    }), patch("agents.shared.python.db.get_memory", return_value=0), \
       patch("agents.shared.python.db.save_memory"), \
       patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
       patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
        try:
            generate_content_with_fallback("k1", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]}, agent_name="SCOUT")
        except Exception:
            pass

    assert call_count[0] == 1, f"Ожидалась 1 попытка, сделано {call_count[0]}. Мёртвый retry-loop не должен вызывать send_func несколько раз."


def test_gemini_429_advances_key_rr_index():
    """При 429 от Gemini gem_key_rr_index должен инкрементироваться."""
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG
    import requests as req

    saved = {}
    def mock_save(key, value, *a, **kw):
        saved[key] = value

    call_count = [0]
    def gemini_first_fails_second_ok(payload, model, key, timeout):
        call_count[0] += 1
        if key == "key_1":
            r = MagicMock(); r.status_code = 429
            raise req.exceptions.HTTPError(response=r)
        return ({"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
                 "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}}, 1, 1)

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "gemini": {"keys": ["key_1", "key_2"], "models": ["gemini-2.5-flash"],
                   "send_func": gemini_first_fails_second_ok},
    }), patch("agents.shared.python.db.get_memory", return_value=0), \
       patch("agents.shared.python.db.save_memory", side_effect=mock_save), \
       patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
       patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
        result, model = generate_content_with_fallback(
            "key_1", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]}, agent_name="SCOUT")

    assert "gem_key_rr_index" in saved, \
        "BUG-01: gem_key_rr_index не обновлён при 429 от Gemini. Следующий вызов снова попадёт на rate-limited ключ."


def test_db_model_override_is_tried_first():
    """Модель из БД должна быть первой в списке планов."""
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    tried_models = []

    def gemini_track(payload, model, key, timeout):
        tried_models.append(model)
        raise ValueError("always fail")

    def mock_get_memory(key, default=None):
        if key == "agent_config_SCOUT":
            return {"provider": "gemini", "model": "gemini-2.0-flash-lite"}
        return default or 0

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "gemini": {"keys": ["k1"], "models": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
                   "send_func": gemini_track},
    }), patch("agents.shared.python.db.get_memory", side_effect=mock_get_memory), \
       patch("agents.shared.python.db.save_memory"), \
       patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
       patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
        try:
            generate_content_with_fallback("k1", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
                                            agent_name="SCOUT")
        except Exception:
            pass

    assert tried_models[0] == "gemini-2.0-flash-lite", \
        f"BUG-02: db_model должна быть первой. Реальный порядок: {tried_models}"


def test_collect_gemini_keys_skips_empty_primary():
    from agents.shared.utils.gemini_client import _collect_gemini_keys
    import os

    with patch.dict(os.environ, {
        "GOOGLE_API_KEY_SECONDARY": "key_b",
        "GOOGLE_API_KEY_THIRD": "key_c",
    }):
        keys = _collect_gemini_keys("")  # пустой первичный ключ

    assert "" not in keys, f"BUG-03: пустой primary_key попал в список: {keys}"
    assert keys == ["key_b", "key_c"]


def test_gem_key_rr_uses_active_keys_not_env():
    """gem_key_rr_index должен считаться по реальным активным ключам, а не по env-списку."""
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG
    import requests as req

    saved = {}
    def mock_save(key, value, *a, **kw): saved[key] = value
    def mock_get(key, default=None): return saved.get(key, default or 0)

    call_n = [0]
    def gemini_first_429_second_ok(payload, model, key, timeout):
        call_n[0] += 1
        if call_n[0] == 1:
            r = MagicMock(); r.status_code = 429
            raise req.exceptions.HTTPError(response=r)
        return ({"candidates": [{"content": {"parts": [{"text":"ok"}],"role":"model"}}],
                 "usageMetadata": {"promptTokenCount":1,"candidatesTokenCount":1}}, 1, 1)

    # 3 override-ключа (не из env!)
    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "gemini": {"keys": ["ov_k1", "ov_k2", "ov_k3"],
                   "models": ["gemini-2.5-flash"], "send_func": gemini_first_429_second_ok},
    }), patch("agents.shared.python.db.get_memory", side_effect=mock_get), \
       patch("agents.shared.python.db.save_memory", side_effect=mock_save), \
       patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
       patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
        generate_content_with_fallback(
            "unrelated_env_key",
            {"contents": [{"parts": [{"text":"t"}], "role":"user"}]},
            agent_name="TEST"
        )

    # Ожидаем: модуль 3 (3 override-ключа), а не модуль 1 (1 env-ключ)
    assert saved.get("gem_key_rr_index") == 2, \
        f"BUG-01: RR считается по env-ключам, а не по active-ключам. Сохранено: {saved.get('gem_key_rr_index')}"


def test_timeout_map_applied_only_to_default_model():
    """TIMEOUT_MAP применяется только к default_model, не к fallback-моделям в PROVIDERS_CONFIG."""
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    used_timeouts = {}
    def track_timeout(payload, model, key, timeout):
        used_timeouts[model] = timeout
        raise ValueError("stop")

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": [], "models": [], "send_func": MagicMock()},
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "gemini": {
            "keys": ["k1"],
            "models": ["gemini-2.5-flash", "gemini-2.0-flash"],
            "send_func": track_timeout
        },
    }), patch("agents.shared.python.db.get_memory", return_value=0), \
       patch("agents.shared.python.db.save_memory"), \
       patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
       patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
        try:
            generate_content_with_fallback("k1",
                {"contents": [{"parts": [{"text":"t"}], "role":"user"}]},
                default_model="gemini-2.5-flash", agent_name="TEST")
        except Exception:
            pass

    # Только первая модель (default) получает таймаут из TIMEOUT_MAP
    assert used_timeouts.get("gemini-2.5-flash") == 45, f"Expected 45s for 2.5-flash, got {used_timeouts}"


# ══════════════════════════════════════════════════════════════
# BUG-01 (Итерация 8): is_cerebras_model охватывает все префиксы Cerebras
# BUG-02 (Итерация 8): Cerebras добавляет ВСЕ модели в plans (аналогично Gemini)
# ══════════════════════════════════════════════════════════════

def test_gpt_oss_model_routed_to_cerebras():
    """
    BUG-01: default_model='gpt-oss-120b' должен быть определён как Cerebras-модель
    и добавлен в providers['cerebras']['models'], не попасть в OpenRouter.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    tried = []

    def cerebras_track(payload, model, key, timeout):
        tried.append(("cerebras", model))
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}},
            1, 1
        )

    def openrouter_track(payload, model, key, timeout):
        tried.append(("openrouter", model))
        raise ValueError("should not be called")

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": ["ck"], "models": ["gpt-oss-120b", "llama3.1-8b"], "send_func": cerebras_track},
        "openrouter": {"keys": ["ok"], "models": ["llama-3.3-70b"], "send_func": openrouter_track},
        "gemini": {"keys": [], "models": [], "send_func": MagicMock()},
    }), \
    patch("agents.shared.python.db.get_memory", return_value=0), \
    patch("agents.shared.python.db.save_memory"), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
        result, model = generate_content_with_fallback(
            "", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
            default_model="gpt-oss-120b", agent_name="TEST"
        )

    # gpt-oss-120b должен был пойти в Cerebras первым
    assert tried[0] == ("cerebras", "gpt-oss-120b"), \
        f"BUG-01: gpt-oss-120b не определён как Cerebras-модель. tried={tried}"
    assert all(p == "cerebras" for p, _ in tried), \
        f"BUG-01: модель gpt-oss-120b попала в OpenRouter. tried={tried}"


def test_cerebras_fallback_tries_all_models():
    """
    BUG-02: при ошибке первой Cerebras-модели должны пробоваться остальные,
    а не немедленный переход к следующему провайдеру.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    tried_models = []

    def cerebras_fail_first_ok_rest(payload, model, key, timeout):
        tried_models.append(model)
        if model == "qwen-3-235b-a22b-instruct-2507":
            raise ValueError("model temporarily unavailable")
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}},
            1, 1
        )

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {
            "keys": ["ck"],
            "models": ["qwen-3-235b-a22b-instruct-2507", "gpt-oss-120b", "zai-glm-4.7"],
            "send_func": cerebras_fail_first_ok_rest
        },
        "openrouter": {"keys": [], "models": [], "send_func": MagicMock()},
        "gemini": {"keys": [], "models": [], "send_func": MagicMock()},
    }), \
    patch("agents.shared.python.db.get_memory", return_value=0), \
    patch("agents.shared.python.db.save_memory"), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
        result, model = generate_content_with_fallback(
            "", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
            agent_name="TEST"
        )

    assert tried_models[0] == "qwen-3-235b-a22b-instruct-2507", \
        f"BUG-02: ожидалась первая модель Cerebras первой. tried={tried_models}"
    assert "gpt-oss-120b" in tried_models, \
        f"BUG-02: вторая Cerebras-модель не была попробована. tried={tried_models}"
    assert model == "gpt-oss-120b", \
        f"BUG-02: успешный ответ должен быть от gpt-oss-120b. model={model}"


def test_zai_model_routed_to_cerebras():
    """
    BUG-01: default_model='zai-glm-4.7' (prefix 'zai-') должен быть определён как Cerebras.
    """
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG

    tried = {"cerebras": 0, "openrouter": 0}

    def cerebras_ok(payload, model, key, timeout):
        tried["cerebras"] += 1
        return (
            {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
             "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}},
            1, 1
        )

    def openrouter_fail(payload, model, key, timeout):
        tried["openrouter"] += 1
        raise ValueError("should not reach openrouter")

    with patch.dict(PROVIDERS_CONFIG, {
        "cerebras": {"keys": ["ck"], "models": ["zai-glm-4.7", "llama3.1-8b"], "send_func": cerebras_ok},
        "openrouter": {"keys": ["ok"], "models": ["some-model"], "send_func": openrouter_fail},
        "gemini": {"keys": [], "models": [], "send_func": MagicMock()},
    }), \
    patch("agents.shared.python.db.get_memory", return_value=0), \
    patch("agents.shared.python.db.save_memory"), \
    patch("agents.shared.utils.gemini_client.LLMLogger.log_call"), \
    patch("agents.shared.utils.gemini_client.extract_prompt_from_payload", return_value="t"):
        generate_content_with_fallback(
            "", {"contents": [{"parts": [{"text": "t"}], "role": "user"}]},
            default_model="zai-glm-4.7", agent_name="TEST"
        )

    assert tried["cerebras"] >= 1, "BUG-01: zai-glm-4.7 не попал в Cerebras"
    assert tried["openrouter"] == 0, "BUG-01: zai-glm-4.7 ошибочно попал в OpenRouter"
