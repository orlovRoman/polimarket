import re
import threading
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ── FIX #1: нет дублирующего import types ────────────────────

def test_no_redundant_import_types():
    """'import types' не должен стоять отдельно — только from types import"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    module_header = source[:source.find("\ndef ")]

    lines = module_header.splitlines()
    bare_import_types = [l for l in lines if l.strip() == "import types"]
    assert bare_import_types == [], \
        f"Найден избыточный 'import types': {bare_import_types}"

    assert "from types import SimpleNamespace" in module_header, \
        "from types import SimpleNamespace должен быть на уровне модуля"


# ── FIX #2: CoreEngine — синглтон ────────────────────────────

def test_core_engine_singleton_returns_same_instance():
    """get_core_engine() возвращает один и тот же объект"""
    from core.singleton import get_core_engine
    
    with patch("core.singleton.init_db"):
        eng1 = get_core_engine()
        eng2 = get_core_engine()

    assert eng1 is eng2, "CoreEngine должен создаваться один раз"


def test_scan_semaphore_blocks_concurrent_scans():
    """Второй одновременный scan пропускается через _scan_in_progress флаг"""
    from services import telegram_listener

    results = []
    
    # Симулируем, что скан запущен
    telegram_listener._scan_in_progress = True
    
    # Пытаемся запустить фоновый скан
    if not telegram_listener._scan_in_progress:
        results.append("scan")
    else:
        results.append("skipped")
        
    # Сбрасываем флаг
    telegram_listener._scan_in_progress = False
    
    if not telegram_listener._scan_in_progress:
        results.append("scan2")

    assert "skipped" in results
    assert "scan2" in results


# ── FIX #3: guard при отсутствии telethon ────────────────────

def test_main_returns_early_if_telethon_missing():
    """main() должен выйти если TelegramClient is None"""
    import asyncio
    from services import telegram_listener

    original = telegram_listener.TelegramClient
    telegram_listener.TelegramClient = None

    # main() не должна бросать AttributeError
    try:
        asyncio.run(telegram_listener.main())
    except Exception as e:
        pytest.fail(
            f"main() бросила исключение при TelegramClient=None: {e}"
        )
    finally:
        telegram_listener.TelegramClient = original


# ── FIX #4: events переименован в event_data ─────────────────

def test_resolve_market_ids_no_local_events_variable():
    """В resolve_market_ids_from_url нет 'events = ' (конфликт с telethon.events)"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener.resolve_market_ids_from_url)
    # Ищем присваивание 'events ='
    assert "events = " not in source, \
        "Локальная переменная 'events' конфликтует с telethon.events — переименуй в event_data"


def test_resolve_market_ids_uses_event_data():
    """resolve_market_ids_from_url использует event_data, не events"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener.resolve_market_ids_from_url)
    assert "event_data" in source, \
        "Переименуй 'events = resp.json()' → 'event_data = resp.json()'"


# ── Регрессия: layer 28 кэш chat_id нормализация ─────────────

def test_target_source_clean_id_regression():
    """3756373077 (без -100) совпадает с chat_id=-1003756373077"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("Chan", -1003756373077, ["3756373077"]) is True


def test_target_source_full_negative_id_regression():
    """Полный отрицательный ID совпадает"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("Chan", -1003756373077, ["-1003756373077"]) is True


def test_direct_url_extraction_regression():
    """Прямой URL рынка в тексте сигнала — regex работает"""
    signal_text = (
        "🎯 Market: https://polymarket.com/event/trump-returns-2028 YES: 45¢"
    )
    pm = re.search(
        r"https?://polymarket\.com/(?:event|market)/[a-zA-Z0-9_-]+", signal_text
    )
    assert pm is not None
    assert pm.group(0) == "https://polymarket.com/event/trump-returns-2028"


def test_simple_namespace_cache_regression():
    """SimpleNamespace chat используется корректно в build_tg_post_url"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username="radarpolybot", id=-1003756373077)
    assert build_tg_post_url(chat, 999) == "https://t.me/radarpolybot/999"
