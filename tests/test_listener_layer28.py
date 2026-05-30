import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock


# ── FIX #1: CachedChat через SimpleNamespace ─────────────────

def test_cached_chat_simple_namespace_has_id():
    """SimpleNamespace корректно хранит id и username"""
    chat = SimpleNamespace(username="radarpolybot", id=-1003756373077, title="Radar")
    assert chat.id == -1003756373077
    assert chat.username == "radarpolybot"
    assert chat.title == "Radar"


def test_cached_chat_simple_namespace_used_in_build_url():
    """build_tg_post_url корректно работает с SimpleNamespace"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username="radarpolybot", id=-1003756373077, title="")
    result = build_tg_post_url(chat, 21491)
    assert result == "https://t.me/radarpolybot/21491"


def test_cached_chat_simple_namespace_no_username():
    """SimpleNamespace без username использует числовой ID"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username=None, id=-1003756373077, title="Private")
    result = build_tg_post_url(chat, 100)
    assert result == "https://t.me/c/3756373077/100"


# ── FIX #2: CoreEngine не импортируется внутри trigger_nexus_scan ─

def test_core_engine_not_imported_inside_trigger_nexus_scan():
    """from core.engine import CoreEngine не должен быть внутри trigger_nexus_scan"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    fn_start = source.find('async def trigger_nexus_scan(')
    assert fn_start != -1
    next_fn = source.find('\nasync def ', fn_start + 1)
    fn_body = source[fn_start:next_fn] if next_fn != -1 else source[fn_start:]

    assert 'from core.engine import CoreEngine' not in fn_body, \
        "CoreEngine импортируется внутри trigger_nexus_scan — должен быть на уровне модуля"


# ── FIX #3: Telethon импортируется на уровне модуля ──────────

def test_telethon_client_module_level_import():
    """TelegramClient и events импортированы на уровне модуля"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    # Находим всё до первого def
    module_header = source[:source.find('\ndef ')]

    assert 'from telethon import' in module_header, \
        "telethon импортируется внутри main() — должен быть на уровне модуля"

    # Внутри main() не должно быть повторного импорта
    main_start = source.find('async def main():')
    main_body = source[main_start:]
    assert 'from telethon import TelegramClient' not in main_body, \
        "from telethon import TelegramClient найден внутри main()"


# ── FIX #4: _is_target_source_match нормализует chat_id ──────

def test_is_target_source_match_with_clean_id():
    """Пользователь пишет 3756373077 (без -100) — должно совпасть"""
    from services.telegram_listener import _is_target_source_match
    # chat_id как в Telethon: -1003756373077
    assert _is_target_source_match("SomeChannel", -1003756373077, ["3756373077"]) is True


def test_is_target_source_match_with_full_negative_id():
    """Пользователь пишет -1003756373077 — тоже должно совпасть"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("SomeChannel", -1003756373077, ["-1003756373077"]) is True


def test_is_target_source_match_username_still_works():
    """username совпадение не сломано"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("radarpolybot", -100111, ["radarpolybot"]) is True


def test_is_target_source_match_no_false_positive_partial():
    """Частичный ID не матчит"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("SomeChannel", -1003756373077, ["375637"]) is False


# ── FIX #5: прямой URL в сигнале из radarpolybot ─────────────

def test_polymarket_url_extracted_from_signal_text():
    """Прямая ссылка на Polymarket извлекается из текста без LLM"""
    import re
    signal_text = (
        "🎯 Рынок: [New Playboi Carti Album before GTA VI?] "
        "(https://polymarket.com/event/new-playboi-carti-album-before-gta-vi-421) "
        "YES: 54¢ | NO: 46¢"
    )
    pm_url_match = re.search(
        r'https?://polymarket\.com/(?:event|market)/[a-zA-Z0-9_-]+', signal_text
    )
    assert pm_url_match is not None
    assert pm_url_match.group(0) == \
        "https://polymarket.com/event/new-playboi-carti-album-before-gta-vi-421"


def test_no_polymarket_url_in_plain_news():
    """Обычная новость без URL → None (используется LLM)"""
    import re
    news_text = "Федрезерв поднял ставку на 25 базисных пунктов."
    pm_url_match = re.search(
        r'https?://polymarket\.com/(?:event|market)/[a-zA-Z0-9_-]+', news_text
    )
    assert pm_url_match is None


# ── Регрессия: layer 27 всё ещё работает ────────────────────

def test_entity_cache_ttl_still_works():
    """TTL кэш не сломан после фиксов"""
    import time
    from services import telegram_listener
    from services.telegram_listener import _set_cached_username, _get_cached_username

    _set_cached_username(-100555, "testchan")
    assert _get_cached_username(-100555) == "testchan"

    # Истекаем TTL вручную
    telegram_listener._entity_username_cache[-100555] = ("testchan", time.time() - 7200)
    assert _get_cached_username(-100555) is None


def test_is_target_source_no_substring_regression():
    """Подстрочное совпадение НЕ работает (регрессия layer 27)"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("radarpolybot", -100111, ["radar"]) is False
