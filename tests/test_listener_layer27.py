import pytest
import time
from unittest.mock import MagicMock


# ── FIX #1: FloodWaitError импортирован на уровне модуля ─────

def test_flood_wait_error_is_module_level_import():
    """FloodWaitError не должен импортироваться внутри handler"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    handler_start = source.find('async def handler(event):')
    assert handler_start != -1
    handler_body = source[handler_start:]

    assert 'from telethon.errors import FloodWaitError' not in handler_body, \
        "FloodWaitError импортируется внутри handler — должен быть на уровне модуля"

    module_header = source[:handler_start]
    assert 'FloodWaitError' in module_header, \
        "FloodWaitError должен быть импортирован на уровне модуля"


# ── FIX #2: entity cache хранит только username (str) ────────

def test_entity_cache_stores_only_string():
    """Кэш должен хранить строку (username), не полный entity-объект"""
    from services.telegram_listener import _set_cached_username, _get_cached_username

    heavy_entity = MagicMock()
    heavy_entity.username = "testchannel"
    heavy_entity.id = -100123

    # Кэшируем только username
    _set_cached_username(-100123, heavy_entity.username)

    cached = _get_cached_username(-100123)
    assert cached == "testchannel"
    assert isinstance(cached, str), "Кэш должен хранить str, не объект"


def test_entity_cache_ttl_expiry():
    """Записи кэша с истёкшим TTL не возвращаются"""
    from services import telegram_listener
    from services.telegram_listener import _set_cached_username, _get_cached_username

    _set_cached_username(-100999, "expiredchan")

    # Подменяем время записи на старое
    telegram_listener._entity_username_cache[-100999] = ("expiredchan", time.time() - 7200)

    result = _get_cached_username(-100999)
    assert result is None, "Истёкший кэш должен возвращать None"


def test_entity_cache_valid_within_ttl():
    """Свежие записи возвращаются корректно"""
    from services.telegram_listener import _set_cached_username, _get_cached_username

    _set_cached_username(-100777, "freshchan")
    result = _get_cached_username(-100777)
    assert result == "freshchan"


# ── FIX #3: save_telegram_post на уровне модуля ──────────────

def test_save_telegram_post_not_in_handler():
    """save_telegram_post не должен импортироваться внутри handler"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    handler_start = source.find('async def handler(event):')
    handler_body = source[handler_start:]

    assert 'from agents.shared.python.db import save_telegram_post' not in handler_body, \
        "save_telegram_post импортируется внутри handler — должен быть на уровне модуля"


# ── FIX #4: send_telegram не в trigger_nexus_scan ────────────

def test_send_telegram_not_in_trigger_nexus_scan():
    """send_telegram не должен импортироваться внутри trigger_nexus_scan"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    fn_start = source.find('async def trigger_nexus_scan(')
    assert fn_start != -1

    # Находим конец функции (следующий def на том же уровне)
    next_def = source.find('\nasync def ', fn_start + 1)
    if next_def == -1:
        next_def = source.find('\ndef ', fn_start + 1)
    fn_body = source[fn_start:next_def] if next_def != -1 else source[fn_start:]

    assert 'from services.notifications import' not in fn_body, \
        "send_telegram импортируется внутри trigger_nexus_scan — должен быть на уровне модуля"


# ── FIX #5: точное совпадение target_source ──────────────────

def test_is_target_source_exact_match():
    """Точное совпадение по username работает"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("radarpolybot", -100111, ["radarpolybot"]) is True


def test_is_target_source_no_substring_match():
    """Подстрока 'radar' НЕ матчит 'radarpolybot'"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("radarpolybot", -100111, ["radar"]) is False, \
        "Подстрочное совпадение не должно срабатывать"


def test_is_target_source_at_prefix_stripped():
    """@radarpolybot в config матчит 'radarpolybot'"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("radarpolybot", -100111, ["@radarpolybot"]) is True


def test_is_target_source_by_chat_id():
    """Совпадение по числовому chat_id"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("SomePrivateChannel", -100999, ["-100999"]) is True


def test_is_target_source_no_false_positive():
    """Случайный канал не матчит"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("randomchannel", -100222, ["radarpolybot", "polymarketalerthub"]) is False


# ── Регрессия: layer 26 кэш всё ещё работает ─────────────────

def test_get_entity_called_once_with_new_cache():
    """С новым кэшем (username string) get_entity вызывается один раз"""
    import asyncio
    async def run_test():
        from services.telegram_listener import _set_cached_username, _get_cached_username

        call_count = {"n": 0}
        entity = MagicMock()
        entity.username = "radarpolybot"
        entity.id = -1003756373077

        async def mock_get_entity(chat_id):
            call_count["n"] += 1
            return entity

        chat_id = -1003756373077

        # Первый вызов — кэша нет
        cached = _get_cached_username(chat_id)
        if not cached:
            full = await mock_get_entity(chat_id)
            uname = getattr(full, 'username', None)
            if uname:
                _set_cached_username(chat_id, uname)

        # Второй вызов — кэш есть
        cached = _get_cached_username(chat_id)
        if not cached:
            await mock_get_entity(chat_id)

        assert call_count["n"] == 1, \
            f"get_entity вызван {call_count['n']} раз — кэш должен предотвратить второй вызов"
            
    asyncio.run(run_test())
