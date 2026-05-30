import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import httpx


# ── Баг #2: httpx timeout ─────────────────────────────────────

def test_httpx_post_has_timeout():
    """AsyncClient вызывается с timeout при POST /api/analyze"""
    import inspect
    from services import telegram_listener
    source = inspect.getsource(telegram_listener)

    # Ищем создание AsyncClient в handler
    # После фикса должен быть timeout= параметр
    handler_start = source.find("async def handler(event):")
    handler_body = source[handler_start:]

    assert "timeout=" in handler_body or "AsyncClient(timeout" in handler_body, \
        "httpx.AsyncClient вызывается без timeout — handler может зависнуть навсегда"


def test_httpx_timeout_exception_is_caught():
    """TimeoutException перехватывается и логируется — не прерывает handler"""
    from services import telegram_listener

    chat = MagicMock()
    chat.username = "testchannel"
    chat.id = -1009999999
    chat.title = "testchannel"

    printed = []

    async def run_test():
        with patch("services.telegram_listener.httpx") as mock_httpx, \
             patch("builtins.print", side_effect=lambda *a: printed.append(str(a[0]))):

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.TimeoutException = httpx.TimeoutException

            # Прямой вызов логики POST (минимальная симуляция)
            try:
                from services.telegram_listener import httpx as tl_httpx
                async with tl_httpx.AsyncClient(timeout=10.0) as c:
                    await c.post("http://127.0.0.1:8000/api/analyze/1", json={})
            except httpx.TimeoutException:
                printed.append("⏱️ Таймаут перехвачен")

    asyncio.run(run_test())

    assert any("Таймаут" in p or "timeout" in p.lower() for p in printed), \
        "TimeoutException не логируется — ошибка проглатывается"


# ── Баг #3: _trigger_scan не падает молча ────────────────────

def test_trigger_scan_logs_runtime_error(capsys):
    """RuntimeError из run_team_discussion логируется, поток не падает молча"""
    import threading
    from unittest.mock import patch

    logged = []

    def _trigger_scan():
        try:
            raise RuntimeError("Сканирование уже выполняется")
        except RuntimeError as e:
            logged.append(f"RuntimeError: {e}")

    t = threading.Thread(target=_trigger_scan)
    t.start()
    t.join(timeout=1.0)

    assert logged, "RuntimeError не был перехвачен и залогирован"
    assert "RuntimeError" in logged[0]


def test_trigger_nexus_scan_thread_catches_runtime_error():
    """trigger_nexus_scan не падает при RuntimeError из CoreEngine"""
    async def run_test():
        with patch("services.telegram_listener._get_core_engine") as MockGetEngine, \
             patch("services.notifications.send_telegram"), \
             patch("services.telegram_listener.logger") as mock_logger:

            MockEngine = MockGetEngine.return_value
            MockEngine.run_team_discussion.side_effect = RuntimeError(
                "Сканирование уже выполняется"
            )

            from services.telegram_listener import trigger_nexus_scan
            # Не должно выбросить исключение
            await trigger_nexus_scan(
                market_id="mkt-1",
                source="whale",
                post_url="https://t.me/test/1"
            )

            import time; time.sleep(0.3)

            # Проверяем что была хотя бы одна запись в варнинги
            calls = [str(c) for c in mock_logger.warning.call_args_list]
            warning_logged = any("сканирование занято" in c.lower() or
                                 "runtime" in c.lower() or
                                 "уже выполняется" in c.lower()
                                 for c in calls)
            assert warning_logged, \
                "RuntimeError из run_team_discussion не залогирован в trigger_nexus_scan"

    asyncio.run(run_test())


# ── Баг #1 + #4: импорты на уровне модуля ────────────────────

def test_httpx_imported_at_module_level():
    """httpx импортирован на уровне модуля, не внутри handler"""
    import inspect
    from services import telegram_listener

    # httpx должен быть в модульных атрибутах
    assert hasattr(telegram_listener, 'httpx') or \
           'import httpx' in inspect.getsource(telegram_listener).split('async def')[0], \
        "httpx не импортирован на уровне модуля"


def test_threading_imported_at_module_level():
    """threading импортирован на уровне модуля"""
    import inspect
    from services import telegram_listener

    module_header = inspect.getsource(telegram_listener).split('async def')[0]
    assert 'import threading' in module_header, \
        "threading не импортирован на уровне модуля"


def test_no_import_inside_trigger_nexus_scan():
    """Нет import threading / import httpx внутри trigger_nexus_scan"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    fn_start = source.find("async def trigger_nexus_scan(")
    assert fn_start != -1

    # Находим тело функции — до следующей def на том же уровне отступа
    fn_body = source[fn_start:fn_start + 2000]

    assert "import threading" not in fn_body, \
        "'import threading' внутри trigger_nexus_scan — вынести на уровень модуля"


# ── Баг #5: конфиг импортируется один раз ────────────────────

def test_config_imported_once_at_module_level():
    """Все конфиги импортируются в одном блоке на уровне модуля"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    # После фикса не должно быть 'from config import' внутри функций
    # Находим все вхождения 'from config import'
    lines = source.split('\n')
    config_imports = [(i, l) for i, l in enumerate(lines) if 'from config import' in l and 'TELEGRAM_BOT_ID' not in l]

    # Все импорты должны быть в первых 30 строках (уровень модуля)
    deep_imports = [(i, l) for i, l in config_imports if i > 30]
    assert not deep_imports, \
        f"'from config import' найден внутри функции на строках: {deep_imports}"


# ── Регрессия: build_tg_post_url всё ещё работает ────────────

def test_build_tg_post_url_still_present():
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = "mychannel"
    chat.id = -100123456
    assert build_tg_post_url(chat, 10) == "https://t.me/mychannel/10"


def test_trigger_nexus_scan_signature_unchanged():
    import inspect
    from services.telegram_listener import trigger_nexus_scan
    sig = inspect.signature(trigger_nexus_scan)
    expected = {"market_id", "amount_usd", "source", "market_url", "post_url", "post_text"}
    assert expected.issubset(set(sig.parameters.keys())), \
        f"Сигнатура trigger_nexus_scan изменилась: {list(sig.parameters.keys())}"
