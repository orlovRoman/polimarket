from unittest.mock import AsyncMock
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio


# ── Баг #1: build_tg_post_url helper ─────────────────────────

def test_build_tg_post_url_public_channel():
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = "polymarketalerthub"
    chat.id = -1001234567890
    result = build_tg_post_url(chat, msg_id=42)
    assert result == "https://t.me/polymarketalerthub/42"


def test_build_tg_post_url_private_channel():
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = None
    chat.id = -1001234567890
    result = build_tg_post_url(chat, msg_id=99)
    assert result == "https://t.me/c/1234567890/99"


def test_build_tg_post_url_no_minus100_prefix():
    """ID без -100 не ломает clean_id"""
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = None
    chat.id = 1234567890   # без -100
    result = build_tg_post_url(chat, msg_id=1)
    assert result == "https://t.me/c/1234567890/1"


def test_build_tg_post_url_empty_username_string():
    """Пустая строка username — считается как приватный"""
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = ""    # falsy
    chat.id = -1009999999
    result = build_tg_post_url(chat, msg_id=5)
    assert result.startswith("https://t.me/c/")


# ── Баг #2: нет дублирующего import asyncio ──────────────────

def test_no_asyncio_reimport_in_handler():
    """В исходнике handler не должно быть 'import asyncio' внутри тела функции"""
    import inspect
    from services import telegram_listener
    source = inspect.getsource(telegram_listener)

    # Находим тело async def handler
    handler_start = source.find("async def handler(event):")
    assert handler_start != -1, "handler не найден"
    handler_body = source[handler_start:]

    # В теле handler не должно быть "import asyncio"
    # (asyncio импортируется на уровне модуля)
    reimport_pos = handler_body.find("import asyncio")
    assert reimport_pos == -1, \
        f"'import asyncio' найден внутри handler на позиции {handler_start + reimport_pos} — shadowing!"


# ── Баг #3: нет create_task без await ────────────────────────

def test_no_create_task_without_await_in_handler():
    """asyncio.create_task вызывается без await — потенциальный ресурс-лик"""
    import inspect
    from services import telegram_listener
    source = inspect.getsource(telegram_listener)

    handler_start = source.find("async def handler(event):")
    handler_body = source[handler_start:]

    # create_task без await — антипаттерн
    assert "asyncio.create_task" not in handler_body, \
        "asyncio.create_task найден в handler — замените на await"


# ── build_tg_post_url вызывается ровно 1 раз на сообщение ────

def test_tg_post_url_built_once_per_message():
    """
    build_tg_post_url должен вызываться ровно 1 раз в handler —
    не 3 раза как раньше.
    """
    from services import telegram_listener

    # Проверяем через исходник — считаем упоминания build_tg_post_url
    import inspect
    source = inspect.getsource(telegram_listener)
    handler_start = source.find("async def handler(event):")
    handler_body = source[handler_start:]

    # Количество вызовов build_tg_post_url в теле handler
    occurrences = handler_body.count("build_tg_post_url(")
    # В теле функции handler вызов один
    # В исходнике оно может быть ещё в самом объявлении перед handler
    assert occurrences == 1, \
        f"build_tg_post_url вызывается {occurrences} раз(а) в handler, ожидали 1"


# ── trigger_nexus_scan получает post_url ─────────────────────

def test_trigger_nexus_scan_has_post_url_param():
    """trigger_nexus_scan принимает post_url и передаёт его в source_url"""
    import inspect
    from services.telegram_listener import trigger_nexus_scan

    sig = inspect.signature(trigger_nexus_scan)
    assert "post_url" in sig.parameters, "Параметр post_url отсутствует в trigger_nexus_scan"
    assert "post_text" in sig.parameters, "Параметр post_text отсутствует в trigger_nexus_scan"


def test_trigger_nexus_scan_post_url_priority_over_market_url():
    """post_url имеет приоритет над market_url в source_url"""
    from services.telegram_listener import trigger_nexus_scan
    import threading, time

    captured = {}

    def mock_run(**kwargs):
        captured.update(kwargs)

    async def run_test():
        with patch("services.telegram_listener._get_core_engine") as MockGetEngine, \
             patch("services.notifications.send_telegram"):
            MockEngine = MockGetEngine.return_value
            MockEngine.run_team_discussion.side_effect = mock_run
            await trigger_nexus_scan(
                market_id="mkt-test",
                amount_usd=3000,
                source="whale",
                market_url="https://polymarket.com/event/test",
                post_url="https://t.me/polymarketalerthub/555",
                post_text="Whale alert!"
            )
            await asyncio.sleep(0.3)
            
    asyncio.run(run_test())

    assert captured.get("source_url") == "https://t.me/polymarketalerthub/555", \
        f"post_url должен иметь приоритет, получили: {captured.get('source_url')}"
    assert captured.get("source_text") == "Whale alert!"
    assert captured.get("trigger_type") == "event_driven"
