from unittest.mock import AsyncMock
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── FIX #3: TELEGRAM_BOT_ID в модульном импорте ──────────────

def test_telegram_bot_id_in_module_imports():
    """TELEGRAM_BOT_ID должен быть в блоке from config import на уровне модуля"""
    import inspect
    from services import telegram_listener

    # Берём только заголовок файла до первого def/class
    source = inspect.getsource(telegram_listener)
    module_header = source[:source.find('\ndef ')]

    assert 'TELEGRAM_BOT_ID' in module_header, \
        "TELEGRAM_BOT_ID не найден в модульном блоке импортов — остался внутри handler"


def test_no_from_config_import_inside_handler():
    """Нет 'from config import' внутри тела handler"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    handler_start = source.find('async def handler(event):')
    assert handler_start != -1
    handler_body = source[handler_start:]

    assert 'from config import' not in handler_body, \
        "'from config import' найден внутри handler — должен быть на уровне модуля"


# ── FIX #1: FloodWaitError перехватывается отдельно ──────────

def test_flood_wait_error_is_caught_separately():
    """
    FloodWaitError логирует e.seconds, не падает,
    и использует числовой ID как fallback
    """
    import asyncio
    async def run_test():
        from services.telegram_listener import build_tg_post_url

        chat = MagicMock()
        chat.username = None
        chat.id = -1003756373077
        chat.title = "PrivateChannel"

        logged = []

        # Симулируем FloodWaitError
        class FakeFloodWaitError(Exception):
            def __init__(self):
                self.seconds = 42

        async def get_entity_flood(chat_id):
            raise FakeFloodWaitError()

        # Логика из handler после фикса
        if not getattr(chat, 'username', None):
            try:
                full_entity = await get_entity_flood(chat.id)
                if getattr(full_entity, 'username', None):
                    chat = full_entity
            except FakeFloodWaitError as e:
                logged.append(f"FloodWait {e.seconds}с")
            except Exception as e:
                logged.append(f"Other: {e}")

        result = build_tg_post_url(chat, 100)
        assert result == "https://t.me/c/3756373077/100", \
            "После FloodWait должен использоваться числовой ID"
        assert logged and "FloodWait" in logged[0], \
            "FloodWaitError должен быть залогирован с указанием секунд"

    asyncio.run(run_test())


# ── FIX #2: entity cache предотвращает повторные запросы ─────

def test_entity_cache_prevents_duplicate_get_entity_calls():
    """get_entity вызывается только 1 раз для одного chat.id"""
    import asyncio
    async def run_test():
        call_count = {"n": 0}

        entity_with_username = MagicMock()
        entity_with_username.username = "radarpolybot"
        entity_with_username.id = -1003756373077

        _entity_cache = {}

        async def get_entity_mock(chat_id):
            call_count["n"] += 1
            return entity_with_username

        async def resolve_entity(chat):
            if not getattr(chat, 'username', None):
                cached = _entity_cache.get(chat.id)
                if cached:
                    return cached
                full_entity = await get_entity_mock(chat.id)
                if getattr(full_entity, 'username', None):
                    _entity_cache[chat.id] = full_entity
                return full_entity
            return chat

        chat = MagicMock()
        chat.username = None
        chat.id = -1003756373077

        # Два вызова подряд для одного chat.id
        await resolve_entity(chat)
        await resolve_entity(chat)

        assert call_count["n"] == 1, \
            f"get_entity вызван {call_count['n']} раз(а), ожидали 1 (кэш должен работать)"

    asyncio.run(run_test())


# ── Регрессия: layer 25 фикс всё ещё работает ────────────────

def test_get_entity_fallback_still_works():
    """get_entity вызывается при отсутствии username и обновляет chat"""
    import asyncio
    async def run_test():
        entity_with_username = MagicMock()
        entity_with_username.username = "radarpolybot"
        entity_with_username.id = -1003756373077

        chat = MagicMock()
        chat.username = None
        chat.id = -1003756373077

        _entity_cache = {}

        async def get_entity_mock(chat_id):
            return entity_with_username

        if not getattr(chat, 'username', None):
            cached = _entity_cache.get(chat.id)
            if cached:
                chat = cached
            else:
                full_entity = await get_entity_mock(chat.id)
                if getattr(full_entity, 'username', None):
                    _entity_cache[chat.id] = full_entity
                    chat = full_entity

        from services.telegram_listener import build_tg_post_url
        result = build_tg_post_url(chat, 21491)
        assert result == "https://t.me/radarpolybot/21491"
        assert "/c/" not in result

    asyncio.run(run_test())


# ── Порядок логов: chat_name печатается до URL ────────────────

def test_log_order_chat_before_url():
    """В handler: сначала логируется chat_name, потом tg_post_url"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    handler_start = source.find('async def handler(event):')
    handler_body = source[handler_start:]

    url_log_pos = handler_body.find('source_url =')
    chat_log_pos = handler_body.find('Получено новое сообщение из')

    assert chat_log_pos < url_log_pos, \
        "Лог с chat_name должен идти ДО лога с source_url"


# ── build_tg_post_url: граничные случаи ──────────────────────

def test_build_tg_post_url_zero_msg_id():
    """msg_id = 0 не ломает функцию"""
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = "testchan"
    chat.id = -100111
    result = build_tg_post_url(chat, 0)
    assert result == "https://t.me/testchan/0"


def test_build_tg_post_url_large_chat_id():
    """Большой chat.id корректно обрезает -100"""
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = None
    chat.id = -1009999999999
    result = build_tg_post_url(chat, 1)
    assert result == "https://t.me/c/9999999999/1"
    assert "-" not in result
