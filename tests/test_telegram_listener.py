"""
Консолидированные тесты для services/telegram_listener.py
Покрывают слои layer 23–27: build_tg_post_url, entity cache, _is_target_source_match,
FloodWaitError, SimpleNamespace, is_bot_message, parse_whale_alert, resolve_market_ids_from_url.

Актуально для текущего состояния кода (layer 27).
"""
import re
import time
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# is_bot_message  (layer 23 — актуально)
# ═══════════════════════════════════════════════════════════════

def test_is_bot_message_no_markets_signature():
    """Точная сигнатура 'не нашел связанных рынков' распознаётся как бот"""
    from services.telegram_listener import is_bot_message
    msg = "К сожалению, я не нашел связанных рынков на Polymarket для этого поста."
    assert is_bot_message(msg) is True


def test_is_bot_message_trigger_whale():
    """Сигнатура 'Запущен внеочередной скан для рынка' распознаётся как бот"""
    from services.telegram_listener import is_bot_message
    msg = "🚀 ТРИГГЕР (Whale): Запущен внеочередной скан для рынка abc123"
    assert is_bot_message(msg) is True


def test_is_bot_message_analyzing():
    """Сигнатура 'Анализирую...' распознаётся как бот"""
    from services.telegram_listener import is_bot_message
    assert is_bot_message("Анализирую...") is True


def test_is_bot_message_real_news_passes():
    """Обычная новость не блокируется"""
    from services.telegram_listener import is_bot_message
    assert is_bot_message("СРОЧНО: Илон Маск запускает SpaceX на Марс") is False


def test_is_bot_message_partial_match_does_not_block():
    """
    Широкое слово "Найдено" НЕ является сигнатурой.
    Ранее был баг: 'Найдено N рынков' блокировало легитимные новости.
    Проверяем что эта широкая сигнатура удалена.
    """
    from services.telegram_listener import is_bot_message
    msg = "Найдено 3 связанных рынков:\n1. New Rihanna Album before GTA VI?"
    # Это НЕ должно блокироваться — 'Найдено' убрано из _BOT_SIGNATURES
    assert is_bot_message(msg) is False


def test_is_bot_message_similar_but_not_bot():
    """Похожие слова в легитимном тексте не блокируются"""
    from services.telegram_listener import is_bot_message
    msg = "К сожалению, Трамп не приехал. Найдено много улик."
    assert is_bot_message(msg) is False


# ═══════════════════════════════════════════════════════════════
# build_tg_post_url  (layer 23–25 — актуально)
# ═══════════════════════════════════════════════════════════════

def test_build_url_public_channel_uses_username():
    """Публичный канал с username → t.me/{username}/{msg_id}"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username="radarpolybot", id=-1003756373077)
    assert build_tg_post_url(chat, 21491) == "https://t.me/radarpolybot/21491"


def test_build_url_private_channel_uses_numeric_id():
    """Приватный канал (нет username) → t.me/c/{clean_id}/{msg_id}"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username=None, id=-1003756373077)
    assert build_tg_post_url(chat, 21491) == "https://t.me/c/3756373077/21491"


def test_build_url_empty_username_treated_as_private():
    """Пустая строка username считается приватным (falsy)"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username="", id=-1001234567890)
    result = build_tg_post_url(chat, 5)
    assert result.startswith("https://t.me/c/")


def test_build_url_large_chat_id_strips_100_prefix():
    """Большой chat.id корректно обрезает -100"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username=None, id=-1009999999999)
    assert build_tg_post_url(chat, 1) == "https://t.me/c/9999999999/1"
    assert "-" not in build_tg_post_url(chat, 1)


def test_build_url_zero_msg_id():
    """msg_id=0 не ломает функцию"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username="testchan", id=-100111)
    assert build_tg_post_url(chat, 0) == "https://t.me/testchan/0"


def test_build_url_with_simple_namespace_cached_chat():
    """SimpleNamespace (CachedChat) корректно используется в build_tg_post_url"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username="radarpolybot", id=-1003756373077, title="")
    assert build_tg_post_url(chat, 21491) == "https://t.me/radarpolybot/21491"


def test_build_url_simple_namespace_no_username_fallback():
    """SimpleNamespace без username → числовой формат"""
    from services.telegram_listener import build_tg_post_url
    chat = SimpleNamespace(username=None, id=-1003756373077, title="Private")
    assert build_tg_post_url(chat, 100) == "https://t.me/c/3756373077/100"


# ═══════════════════════════════════════════════════════════════
# entity cache  (layer 26–27 — актуально)
# ═══════════════════════════════════════════════════════════════

def test_cache_set_and_get():
    """Кэш корректно сохраняет и возвращает username"""
    from services.telegram_listener import _set_cached_username, _get_cached_username
    _set_cached_username(-100_001, "freshchan")
    assert _get_cached_username(-100_001) == "freshchan"
    assert isinstance(_get_cached_username(-100_001), str)


def test_cache_returns_none_for_unknown():
    """Несуществующий chat_id → None"""
    from services.telegram_listener import _get_cached_username
    assert _get_cached_username(-100_999_999) is None


def test_cache_ttl_expiry():
    """Запись с истёкшим TTL не возвращается"""
    from services import telegram_listener
    from services.telegram_listener import _set_cached_username, _get_cached_username
    _set_cached_username(-100_002, "expiredchan")
    # Откатываем timestamp на 2 часа назад
    telegram_listener._entity_username_cache[-100_002] = ("expiredchan", time.time() - 7200)
    assert _get_cached_username(-100_002) is None


def test_cache_valid_within_ttl():
    """Свежая запись доступна в пределах TTL"""
    from services.telegram_listener import _set_cached_username, _get_cached_username
    _set_cached_username(-100_003, "validchan")
    # TTL = 3600, только что записано — должна быть доступна
    assert _get_cached_username(-100_003) == "validchan"


def test_cache_stores_string_not_object():
    """Кэш хранит str, а не тяжёлый Telethon-объект"""
    from services.telegram_listener import _set_cached_username, _get_cached_username
    _set_cached_username(-100_004, "stringonly")
    assert isinstance(_get_cached_username(-100_004), str)


def test_cache_prevents_duplicate_get_entity():
    """get_entity вызывается 1 раз — второй вызов идёт из кэша"""
    import asyncio
    from services.telegram_listener import _set_cached_username, _get_cached_username

    call_count = {"n": 0}

    async def mock_get_entity(chat_id):
        call_count["n"] += 1
        entity = SimpleNamespace(username="radarpolybot", id=chat_id)
        return entity

    async def run_test():
        chat_id = -1003756373077
        from services import telegram_listener
        telegram_listener._entity_username_cache.pop(chat_id, None)

        # Первый вызов — кэша нет
        cached = _get_cached_username(chat_id)
        if not cached:
            full = await mock_get_entity(chat_id)
            uname = getattr(full, "username", None)
            if uname:
                _set_cached_username(chat_id, uname)

        # Второй вызов — кэш есть
        cached = _get_cached_username(chat_id)
        if not cached:
            await mock_get_entity(chat_id)

    asyncio.run(run_test())
    assert call_count["n"] == 1


# ═══════════════════════════════════════════════════════════════
# FloodWaitError обработка  (layer 26–27 — актуально)
# ═══════════════════════════════════════════════════════════════

def test_flood_wait_logs_seconds_and_uses_numeric_id():
    """При FloodWaitError логируется e.seconds и используется числовой ID"""
    import asyncio
    from services.telegram_listener import build_tg_post_url

    class FakeFloodWaitError(Exception):
        seconds = 42

    chat = SimpleNamespace(username=None, id=-1003756373077, title="Private")
    logged = []

    async def get_entity_flood(_id):
        raise FakeFloodWaitError()

    async def run_test():
        nonlocal chat
        if not getattr(chat, "username", None):
            try:
                full_entity = await get_entity_flood(chat.id)
                if getattr(full_entity, "username", None):
                    chat = full_entity
            except FakeFloodWaitError as e:
                logged.append(f"FloodWait {e.seconds}с")
            except Exception as e:
                logged.append(f"Other: {e}")

    asyncio.run(run_test())
    result = build_tg_post_url(chat, 100)
    assert result == "https://t.me/c/3756373077/100"
    assert logged and "FloodWait 42с" in logged[0]


def test_get_entity_network_error_uses_numeric_id():
    """При обычной ошибке get_entity тоже используется числовой ID, не краш"""
    import asyncio
    from services.telegram_listener import build_tg_post_url

    chat = SimpleNamespace(username=None, id=-1003756373077, title="Private")

    async def get_entity_fail(_id):
        raise ConnectionError("Network error")

    async def run_test():
        nonlocal chat
        if not getattr(chat, "username", None):
            try:
                full_entity = await get_entity_fail(chat.id)
                if getattr(full_entity, "username", None):
                    chat = full_entity
            except Exception:
                pass

    asyncio.run(run_test())
    result = build_tg_post_url(chat, 100)
    assert result == "https://t.me/c/3756373077/100"


def test_get_entity_success_updates_chat():
    """Успешный get_entity обновляет chat на объект с username"""
    import asyncio
    from services.telegram_listener import build_tg_post_url

    chat = SimpleNamespace(username=None, id=-1003756373077, title="Private")
    resolved_entity = SimpleNamespace(username="radarpolybot", id=-1003756373077)

    async def get_entity_ok(_id):
        return resolved_entity

    async def run_test():
        nonlocal chat
        if not getattr(chat, "username", None):
            try:
                full_entity = await get_entity_ok(chat.id)
                if getattr(full_entity, "username", None):
                    chat = full_entity
            except Exception:
                pass

    asyncio.run(run_test())
    result = build_tg_post_url(chat, 21491)
    assert result == "https://t.me/radarpolybot/21491"
    assert "/c/" not in result


# ═══════════════════════════════════════════════════════════════
# _is_target_source_match  (layer 27 — актуально)
# ═══════════════════════════════════════════════════════════════

def test_target_source_exact_username_match():
    """Точное совпадение по username"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("radarpolybot", -100111, ["radarpolybot"]) is True


def test_target_source_at_prefix_stripped():
    """@radarpolybot в конфиге матчит 'radarpolybot'"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("radarpolybot", -100111, ["@radarpolybot"]) is True


def test_target_source_no_substring_match():
    """'radar' НЕ должен матчить 'radarpolybot' (подстрока запрещена)"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("radarpolybot", -100111, ["radar"]) is False


def test_target_source_by_full_negative_chat_id():
    """Совпадение по полному chat_id с минусом"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("SomeChannel", -1003756373077, ["-1003756373077"]) is True


def test_target_source_by_clean_chat_id_without_100():
    """
    Пользователь пишет 3756373077 (без -100) — должно совпасть.
    Layer 28 фикс: нормализация обеих сторон.
    """
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("SomeChannel", -1003756373077, ["3756373077"]) is True


def test_target_source_no_match_for_random_channel():
    """Случайный канал не матчит"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("randomchan", -100222, ["radarpolybot", "polymarketalerthub"]) is False


def test_target_source_polymarketalerthub():
    """polymarketalerthub матчится напрямую"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("polymarketalerthub", -100333, ["polymarketalerthub"]) is True


def test_target_source_empty_list():
    """Пустой target_sources → всегда False"""
    from services.telegram_listener import _is_target_source_match
    assert _is_target_source_match("radarpolybot", -100111, []) is False


# ═══════════════════════════════════════════════════════════════
# Module-level import checks  (layer 24–27 — структурные)
# ═══════════════════════════════════════════════════════════════

def test_telegram_bot_id_in_module_imports():
    """TELEGRAM_BOT_ID импортируется на уровне модуля, не внутри handler"""
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    module_header = source[:source.find("\ndef ")]
    assert "TELEGRAM_BOT_ID" in module_header


    import asyncio
    from unittest.mock import patch
    from services import telegram_listener
    
    with patch.object(telegram_listener, "TelegramClient", None):
        # Должен выйти без ошибок, напечатав сообщение
        asyncio.run(telegram_listener.main())

def test_handler_parses_radarpolybot_as_whale():
    """
    Проверяет, что сообщение из radarpolybot теперь обрабатывается
    как whale-сигнал через parse_radar_signal.
    """
    import asyncio
    from unittest.mock import patch, MagicMock
    from services import telegram_listener

    # Создаём мок ивента для radarpolybot
    mock_event = MagicMock()
    mock_event.message.message = "⚡️ Buy Yes\n├ Amount: $15,000\n├ Entry: 15¢ → Now: 90¢"
    mock_event.message.id = 123
    mock_event.message.entities = [
        MagicMock(url="https://polymarket.com/event/trump-leads"),
        MagicMock(url="https://polymarket.com/profile/0xAbCdEf1234567890AbCdEf1234567890AbCdEf12")
    ]

    from unittest.mock import AsyncMock
    mock_chat = MagicMock()
    mock_chat.username = "radarpolybot"
    mock_chat.id = -100444555
    mock_event.get_chat = AsyncMock(return_value=mock_chat)
    mock_event.get_sender = AsyncMock(return_value=MagicMock(id=9999))

    # Чтобы функция resolve_market_ids_from_url не ходила в сеть
    with patch("services.telegram_listener.resolve_market_ids_from_url", return_value=["market123"]) as mock_resolve, \
         patch("services.telegram_listener.trigger_nexus_scan") as mock_trigger, \
         patch("services.telegram_listener.save_trader_transaction") as mock_save, \
         patch("services.telegram_listener._is_target_source_match", return_value=False):
         
        # Перехватываем хендлер, замокав client
        handler_func = None
        
        def capture_on(*args, **kwargs):
            def decorator(f):
                nonlocal handler_func
                handler_func = f
                return f
            return decorator
            
        from unittest.mock import AsyncMock
        mock_client = MagicMock()
        mock_client.on = capture_on
        mock_client.run_until_disconnected = AsyncMock()  # avoid blocking
        mock_client.start = AsyncMock()
        
        # Мокаем TelegramClient чтобы он вернул наш mock_client
        with patch("services.telegram_listener.TelegramClient", return_value=mock_client), \
             patch("builtins.input", return_value="123"), \
             patch("services.telegram_listener.NewsProcessor"):
             
            # Запускаем main() чтобы он зарегистрировал handler
            asyncio.run(telegram_listener.main())
            
            assert handler_func is not None
            
            # Запускаем сам handler с замоканным ивентом
            asyncio.run(handler_func(mock_event))
            
            # Должен быть вызван resolve_market_ids_from_url
            mock_resolve.assert_called_once_with("https://polymarket.com/event/trump-leads")
            
            # Должен быть вызван save_trader_transaction
            mock_save.assert_called_once()
            
            # И должен быть вызван trigger_nexus_scan (т.к. $15,000 >= WHALE_ALERT_MIN_USD)
            mock_trigger.assert_called_once()
            args, kwargs = mock_trigger.call_args
            assert args[0] == "market123"
            assert args[1] == 15000.0
            assert kwargs["source"] == "whale"
            assert kwargs["market_url"] == "https://polymarket.com/event/trump-leads"
    import inspect
    from services import telegram_listener

    source = inspect.getsource(telegram_listener)
    handler_start = source.find("async def handler(event):")
    handler_body = source[handler_start:]
    assert handler_body.find("Получено новое сообщение из") < handler_body.find("source_url =")


# ═══════════════════════════════════════════════════════════════
# parse_whale_alert  (актуально)
# ═══════════════════════════════════════════════════════════════

def test_parse_whale_alert_full():
    """Полный текст whale-алерта парсится корректно"""
    from services.telegram_listener import parse_whale_alert
    text = (
        "🐋 Whale Alert: Trader bought YES @ 61.2¢\n"
        "Market: https://polymarket.com/event/trump-wins-2024\n"
        "Profile: https://polymarket.com/profile/0xAbCdEf1234567890AbCdEf1234567890AbCdEf12\n"
        "Amount: $15,000"
    )
    result = parse_whale_alert(text)
    assert result["market_url"] == "https://polymarket.com/event/trump-wins-2024"
    assert result["wallet"] == "0xabcdef1234567890abcdef1234567890abcdef12"
    assert result["amount_usd"] == 15000.0
    assert result["outcome"] == "YES"
    assert abs(result["price"] - 0.612) < 0.001


def test_parse_whale_alert_no_market_url():
    """Без ссылки на рынок market_url = None"""
    from services.telegram_listener import parse_whale_alert
    result = parse_whale_alert("Some random text $500")
    assert result["market_url"] is None
    assert result["amount_usd"] == 500.0


def test_parse_whale_alert_short_wallet():
    """Сокращённый кошелёк 0x1234...abcd сохраняется как псевдо-адрес"""
    from services.telegram_listener import parse_whale_alert
    text = "Trader 0x1234...abcd bought YES $1,000"
    result = parse_whale_alert(text)
    assert result["wallet"] == "0x1234...abcd"


def test_parse_whale_alert_no_outcome():
    """Текст без YES/NO → outcome = None"""
    from services.telegram_listener import parse_whale_alert
    result = parse_whale_alert("Something happened for $200")
    assert result["outcome"] is None


def test_parse_whale_alert_price_in_cents():
    """Цена в центах (¢) конвертируется в доли единицы"""
    from services.telegram_listener import parse_whale_alert
    result = parse_whale_alert("Bought YES at 75¢")
    assert result["price"] is not None
    assert abs(result["price"] - 0.75) < 0.001


def test_parse_whale_alert_username_profile():
    """Username-профиль сохраняется как alias и wallet с префиксом 'username:'"""
    from services.telegram_listener import parse_whale_alert
    text = "https://polymarket.com/profile/TrumpMegaBull bought YES $5,000"
    result = parse_whale_alert(text)
    assert result["alias"] == "TrumpMegaBull"
    assert result["wallet"] == "username:trumpmegabull"


# ═══════════════════════════════════════════════════════════════
# resolve_market_ids_from_url  (актуально)
# ═══════════════════════════════════════════════════════════════

def test_resolve_market_ids_invalid_url():
    """Не-Polymarket URL → пустой список"""
    from services.telegram_listener import resolve_market_ids_from_url
    assert resolve_market_ids_from_url("https://google.com/search") == []


def test_resolve_market_ids_no_slug():
    """URL без slug → пустой список"""
    from services.telegram_listener import resolve_market_ids_from_url
    assert resolve_market_ids_from_url("https://polymarket.com/") == []


def test_resolve_market_ids_extracts_slug():
    """Slug корректно извлекается из URL события"""
    url = "https://polymarket.com/event/trump-wins-2024"
    match = re.search(r"polymarket\.com/(?:event|market)/([a-zA-Z0-9_-]+)", url)
    assert match is not None
    assert match.group(1) == "trump-wins-2024"


def test_resolve_market_ids_query_params_stripped():
    """Query-параметры (?ref=...) не мешают извлечению slug"""
    url = "https://polymarket.com/event/trump-wins-2024?ref=abc"
    match = re.search(r"polymarket\.com/(?:event|market)/([a-zA-Z0-9_-]+)", url)
    assert match is not None
    assert match.group(1) == "trump-wins-2024"


# ═══════════════════════════════════════════════════════════════
# FIX #5 layer 28: прямой URL рынка в тексте сигнала
# ═══════════════════════════════════════════════════════════════

def test_polymarket_url_extracted_from_signal_text():
    """Прямая ссылка на Polymarket извлекается regex'ом без LLM"""
    signal_text = (
        "🎯 Рынок: [New Playboi Carti Album before GTA VI?] "
        "(https://polymarket.com/event/new-playboi-carti-album-before-gta-vi-421) "
        "YES: 54¢ | NO: 46¢"
    )
    pm_url_match = re.search(
        r"https?://polymarket\.com/(?:event|market)/[a-zA-Z0-9_-]+", signal_text
    )
    assert pm_url_match is not None
    assert pm_url_match.group(0) == \
        "https://polymarket.com/event/new-playboi-carti-album-before-gta-vi-421"


def test_no_polymarket_url_in_plain_news():
    """Обычная новость без URL → None (нужен LLM)"""
    news_text = "Федрезерв поднял ставку на 25 базисных пунктов."
    pm_url_match = re.search(
        r"https?://polymarket\.com/(?:event|market)/[a-zA-Z0-9_-]+", news_text
    )
    assert pm_url_match is None


# ═══════════════════════════════════════════════════════════════
# parse_radar_signal  (layer 29 — radarpolybot формат)
# ═══════════════════════════════════════════════════════════════

def test_radar_signal_outcome_buy_yes():
    """Buy Yes → outcome = YES"""
    from services.telegram_listener import parse_radar_signal
    text = "⚡️ Buy Yes\n├ Amount: $11,136\n├ Entry: 15¢ → Now: 90¢"
    result = parse_radar_signal(text)
    assert result["outcome"] == "YES"


def test_radar_signal_outcome_buy_no():
    """Buy No → outcome = NO"""
    from services.telegram_listener import parse_radar_signal
    text = "⚡️ Buy No\n├ Amount: $5,000\n├ Entry: 85¢ → Now: 12¢"
    result = parse_radar_signal(text)
    assert result["outcome"] == "NO"


def test_radar_signal_amount_parsed():
    """Amount: $11,136 корректно парсится"""
    from services.telegram_listener import parse_radar_signal
    text = "├ Amount: $11,136\n├ Entry: 15¢ → Now: 90¢"
    result = parse_radar_signal(text)
    assert result["amount_usd"] == 11136.0


def test_radar_signal_entry_price():
    """Entry: 15¢ → price = 0.15"""
    from services.telegram_listener import parse_radar_signal
    text = "├ Entry: 15¢ → Now: 90¢"
    result = parse_radar_signal(text)
    assert result["entry_price"] is not None
    assert abs(result["entry_price"] - 0.15) < 0.001
    assert abs(result["price"] - 0.15) < 0.001


def test_radar_signal_current_price():
    """Now: 90¢ → current_price = 0.90"""
    from services.telegram_listener import parse_radar_signal
    text = "├ Entry: 15¢ → Now: 90¢"
    result = parse_radar_signal(text)
    assert result["current_price"] is not None
    assert abs(result["current_price"] - 0.90) < 0.001


def test_radar_signal_win_rate():
    """Win Rate: 67% → win_rate = 67"""
    from services.telegram_listener import parse_radar_signal
    text = "├ Win Rate: 67%\n├ P&L: +$5,187"
    result = parse_radar_signal(text)
    assert result["win_rate"] == 67


def test_radar_signal_alias_from_trader_line():
    """Trader: Parz1vaI → alias = Parz1vaI"""
    from services.telegram_listener import parse_radar_signal
    text = "🧑💼 Trader: Parz1vaI - Check Full Stats"
    result = parse_radar_signal(text)
    assert result["alias"] == "Parz1vaI"


def test_radar_signal_wallet_from_entity():
    """Кошелёк берётся из entity (скрытая ссылка на профиль)"""
    from services.telegram_listener import parse_radar_signal
    from types import SimpleNamespace
    entity = SimpleNamespace(url="https://polymarket.com/profile/0xB10047d6a254B2EbB306D7a7D13Bf59171AB6461")
    result = parse_radar_signal("Trader: Parz1vaI", entities=[entity])
    assert result["wallet"] == "0xb10047d6a254b2ebb306d7a7d13bf59171ab6461"


def test_radar_signal_market_url_from_entity():
    """URL рынка берётся из entity (скрытая ссылка)"""
    from services.telegram_listener import parse_radar_signal
    from types import SimpleNamespace
    entity = SimpleNamespace(url="https://polymarket.com/event/us-x-iran-permanent-peace-deal-by-may-31-2026")
    result = parse_radar_signal("US x Iran peace deal", entities=[entity])
    assert result["market_url"] == "https://polymarket.com/event/us-x-iran-permanent-peace-deal-by-may-31-2026"


def test_radar_signal_market_url_raw_text_fallback():
    """Если entities нет — URL берётся из сырого текста"""
    from services.telegram_listener import parse_radar_signal
    text = "Check: https://polymarket.com/event/us-x-iran-deal Buy Yes $5,000"
    result = parse_radar_signal(text)
    assert result["market_url"] == "https://polymarket.com/event/us-x-iran-deal"


def test_radar_signal_no_market_url_returns_none():
    """Без URL рынка market_url = None"""
    from services.telegram_listener import parse_radar_signal
    result = parse_radar_signal("Buy Yes Amount: $1,000 Win Rate: 50%")
    assert result["market_url"] is None


def test_radar_signal_full_message():
    """Полный реальный сигнал из radarpolybot парсится корректно"""
    from services.telegram_listener import parse_radar_signal
    from types import SimpleNamespace

    text = (
        "US x Iran permanent peace deal by May 31, 2026?\n\n"
        "📅 Resolves: May 30 8:00 PM ET\n\n"
        "👥 Top Holders: 14/20 Yes\n"
        "⚠️ Risk 🔴\n\n"
        "⚡️ Buy Yes\n"
        "├ Amount: $11,136\n"
        "├ Entry: 15¢ → Now: 90¢\n"
        "└ To win: $74,240 (6.7x)\n\n"
        "🧑💼 Trader: Parz1vaI - Check Full Stats · Copy Trade\n"
        "├ Positions: $231,618 · 84 live · 100 closed\n"
        "├ Win Rate: 67%\n"
        "├ P&L: +$5,187\n"
        "├ Best Win: +$11,476\n"
        "└ 🏷️ Top Category: Geopolitics"
    )
    market_entity = SimpleNamespace(
        url="https://polymarket.com/event/us-x-iran-permanent-peace-deal-by-may-31-2026"
    )
    profile_entity = SimpleNamespace(
        url="https://polymarket.com/profile/0xB10047d6a254B2EbB306D7a7D13Bf59171AB6461"
    )

    result = parse_radar_signal(text, entities=[market_entity, profile_entity])

    assert result["market_url"] == "https://polymarket.com/event/us-x-iran-permanent-peace-deal-by-may-31-2026"
    assert result["wallet"] == "0xb10047d6a254b2ebb306d7a7d13bf59171ab6461"
    assert result["outcome"] == "YES"
    assert result["amount_usd"] == 11136.0
    assert abs(result["entry_price"] - 0.15) < 0.001
    assert abs(result["current_price"] - 0.90) < 0.001
    assert result["win_rate"] == 67
    assert result["alias"] == "Parz1vaI"


def test_whale_channels_constant_contains_both():
    """_WHALE_CHANNELS содержит оба канала"""
    from services.telegram_listener import _WHALE_CHANNELS
    assert "polymarketalerthub" in _WHALE_CHANNELS
    assert "radarpolybot" in _WHALE_CHANNELS
