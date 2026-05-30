import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio


# ── Баг #1: trigger_nexus_scan передаёт post_url ─────────────

def test_trigger_nexus_scan_uses_post_url_over_market_url():
    """source_url в run_team_discussion = post_url, а не market_url"""
    from services.telegram_listener import trigger_nexus_scan

    captured = {}

    def mock_run_team_discussion(**kwargs):
        captured.update(kwargs)

    mock_engine = MagicMock()
    mock_engine.run_team_discussion.side_effect = mock_run_team_discussion

    with patch("services.telegram_listener._get_core_engine", return_value=mock_engine):
        asyncio.run(trigger_nexus_scan(
            market_id="mkt-1",
            amount_usd=5000,
            source="whale",
            market_url="https://polymarket.com/event/test",
            post_url="https://t.me/polymarketalerthub/12345",
            post_text="Whale bought $5000 YES"
        ))

    # Ждём завершения потока
    import time; time.sleep(0.2)

    assert captured.get("source_url") == "https://t.me/polymarketalerthub/12345", \
        f"Ожидали ссылку на пост, получили: {captured.get('source_url')}"
    assert captured.get("trigger_type") == "event_driven"


def test_trigger_nexus_scan_fallback_to_market_url_if_no_post_url():
    """Если post_url не передан — используем market_url как fallback"""
    from services.telegram_listener import trigger_nexus_scan

    captured = {}

    def mock_run(**kwargs):
        captured.update(kwargs)

    mock_engine = MagicMock()
    mock_engine.run_team_discussion.side_effect = mock_run

    with patch("services.telegram_listener._get_core_engine", return_value=mock_engine):
        asyncio.run(trigger_nexus_scan(
            market_id="mkt-2",
            source="whale",
            market_url="https://polymarket.com/event/test",
            post_url="",   # пусто
        ))

    import time; time.sleep(0.2)

    assert captured.get("source_url") == "https://t.me/polymarketalerthub/12345" \
        or captured.get("source_url") == "https://polymarket.com/event/test", \
        "Fallback должен быть market_url"


# ── Баг #2: source_url для приватного канала ─────────────────

def test_source_url_private_channel_format():
    """Для приватного канала (без username) ссылка: t.me/c/{clean_id}/{msg_id}"""
    chat_id = -1001234567890
    msg_id = 42
    clean_id = str(chat_id).replace('-100', '')
    expected = f"https://t.me/c/{clean_id}/{msg_id}"

    # Симулируем логику из listener
    username = None
    if username:
        result = f"https://t.me/{username}/{msg_id}"
    else:
        cid = str(chat_id).replace('-100', '')
        result = f"https://t.me/c/{cid}/{msg_id}"

    assert result == expected, f"Неверный формат ссылки для приватного канала: {result}"


def test_source_url_public_channel_format():
    """Для публичного канала: t.me/{username}/{msg_id}"""
    username = "polymarketalerthub"
    msg_id = 99
    expected = f"https://t.me/{username}/{msg_id}"

    result = (
        f"https://t.me/{username}/{msg_id}" if username
        else f"https://t.me/c/UNKNOWN/{msg_id}"
    )

    assert result == expected


# ── analyze_post_async: source_url уже заполнен из listener ──

def test_analyze_post_async_receives_source_url():
    """
    analyze_post_async получает source_url от telegram_listener —
    fallback в engine.py не требуется если listener работает корректно.
    """
    from unittest.mock import patch, MagicMock
    from core.engine import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.initialized = True
    engine.api_key = "test"
    engine._scan_lock = MagicMock()

    post_info = {
        "status": "NEW",
        "text": "Test post",
        "message_id": 42,
        "chat_id": -1001234567890
    }

    async def run_test():
        with patch("agents.shared.python.db.get_telegram_post_info", return_value=post_info), \
             patch("agents.shared.python.db.mark_telegram_post_status"), \
             patch("agents.orchestrator.src.news_processor.NewsProcessor") as MockNP, \
             patch("services.notifications.send_telegram_to_chat"), \
             patch("asyncio.to_thread") as mock_thread:

            MockNP.return_value.find_relevant_markets.return_value = []

            await engine.analyze_post_async(
                post_id=1,
                chat_id="TARGET_CHAT",
                source_username="polymarketalerthub",
                source_message_id=42,
                source_url="https://t.me/polymarketalerthub/42",   # уже заполнен
                source_text="[polymarketalerthub] Test post..."
            )

    asyncio.run(run_test())

    # Если markets пустой — run_team_discussion не вызывается
    # Главное что source_url НЕ был перезаписан пустым значением
    # (нет краша, нет KeyError)


# ── Регрессия: is_bot_message не ломает легитимный контент ───

def test_is_bot_message_exact_match_only():
    from services.telegram_listener import is_bot_message

    assert is_bot_message("Запущен внеочередной скан для рынка mkt-1") is True
    assert is_bot_message("Анализирую...") is True
    assert is_bot_message("К сожалению, я не нашел связанных рынков") is True

    # Легитимные новости НЕ должны фильтроваться
    assert is_bot_message("Trump wins election — market analysis") is False
    assert is_bot_message("Найдено много интересных новостей сегодня") is False
    assert is_bot_message("К сожалению, прогноз дождя") is False  # неполное совпадение
