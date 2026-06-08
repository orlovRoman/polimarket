# tests/test_onchain_trend_alerts_keyboard.py
"""Тесты для интеграции кнопки 'Проанализировать' под алертами всплесков объёма."""
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from telegram.bot import callback_analyze_market, AuthMiddleware
from main import job_onchain_alerts

# ── 1. Проверяем, что job_onchain_alerts прикрепляет Inline клавиатуру с кнопкой ───
@patch("main.bot.send_message", new_callable=AsyncMock)
@patch("main.asyncio.to_thread")
@patch("agents.shared.python.db.mark_alert_sent")
def test_job_onchain_alerts_attaches_keyboard(mock_mark, mock_thread, mock_send_message):
    fake_spikes = [{
        "market_id": "test_mkt_123",
        "title": "Will Trump win?",
        "url": "https://polymarket.com/event/trump-win",
        "price": 0.55,
        "vol_recent": 11000.0,
        "vol_prev": 1000.0,
        "yes_vol": 10000.0,
        "no_vol": 1000.0
    }]
    mock_thread.return_value = fake_spikes

    async def run_test():
        await job_onchain_alerts()

    asyncio.run(run_test())

    mock_send_message.assert_called_once()
    args, kwargs = mock_send_message.call_args
    assert "test_mkt_123" in kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    assert "analyze_mkt_" in kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    assert "Проанализировать рынок" in kwargs["reply_markup"].inline_keyboard[0][0].text

# ── 2. Проверяем, что callback_analyze_market запускает обсуждение ─────────
@patch("telegram.bot.get_market_discussions", return_value=[])
@patch("telegram.bot.get_core_engine")
@patch("telegram.bot._scan_lock.locked", return_value=False)
def test_callback_analyze_market_triggers_scan(mock_lock, mock_get_engine, mock_get_discussions):
    mock_engine = MagicMock()
    mock_engine._scan_lock.locked.return_value = False
    mock_engine._fetch_pre_orderbook.return_value = None
    
    from core.models import Market
    from datetime import datetime, timezone
    mock_market = Market(
        id="test_mkt_123",
        platform="polymarket",
        title="Will Trump win?",
        url="https://polymarket.com/event/trump-win",
        outcome="YES",
        price=0.55,
        close_time=datetime.now(timezone.utc),
        volume=15000.0,
        description="Test description"
    )
    mock_engine.adapter.get_market.return_value = mock_market
    mock_get_engine.return_value = mock_engine

    mock_callback = MagicMock()
    mock_callback.answer = AsyncMock()
    mock_callback.message = AsyncMock()
    mock_callback.id = "callback_id_123"
    mock_callback.data = "analyze_mkt_test_mkt_123"

    async def run_test():
        await callback_analyze_market(mock_callback)
        # Ждём завершения созданной фоновой задачи
        await asyncio.sleep(0.1)
        mock_engine._run_shadow_analysis.assert_called_once()
        args, kwargs = mock_engine._run_shadow_analysis.call_args
        assert kwargs["m"].id == "test_mkt_123"

    asyncio.run(run_test())
    mock_callback.answer.assert_called_once_with("🔍 Запуск анализа рынка агентами...", show_alert=False)
    assert mock_callback.message.answer.called
    ans_args, _ = mock_callback.message.answer.call_args
    assert "Запуск ручного анализа рынка" in ans_args[0]
    assert "Will Trump win?" in ans_args[0]

# ── 3. Проверяем, что при занятом сканировании выводится предупреждение ───────────
@patch("telegram.bot.get_market_discussions", return_value=[])
@patch("telegram.bot.get_core_engine")
@patch("telegram.bot._scan_lock.locked", return_value=True)
def test_callback_analyze_market_locked(mock_lock, mock_get_engine, mock_get_discussions):
    mock_engine = MagicMock()
    mock_engine._scan_lock.locked.return_value = True
    mock_get_engine.return_value = mock_engine

    mock_callback = AsyncMock()
    mock_callback.answer = AsyncMock()
    mock_callback.id = "callback_id_locked"
    mock_callback.data = "analyze_mkt_test_mkt_123"

    async def run_test():
        await callback_analyze_market(mock_callback)

    asyncio.run(run_test())
    mock_callback.answer.assert_called_once_with(
        "⚠️ Сканирование уже выполняется. Пожалуйста, подождите.",
        show_alert=True
    )

# ── 4. Проверяем, что кнопка analyze_mkt_ обходит проверку устаревания сессии ─────
class MockMessage:
    def __init__(self, date):
        self.date = date

class MockUser:
    id = 12345

class MockChat:
    id = 12345

class MockCallbackQuery:
    def __init__(self, data, date):
        self.data = data
        self.message = MockMessage(date)
        self.from_user = MockUser()
        self.chat = MockChat()
        self.answer = AsyncMock()

def test_auth_middleware_bypass_analyze_market():
    async def run_test():
        middleware = AuthMiddleware()
        from datetime import datetime, timedelta, timezone
        old_time = datetime.now(timezone.utc) - timedelta(minutes=20)

        # 1. Test clicking "Analyze" (analyze_mkt_XYZ) on an old message (should bypass stale check)
        event_analyze = MockCallbackQuery("analyze_mkt_test_mkt_123", old_time)
        mock_handler = AsyncMock()
        mock_handler.return_value = "handler_called"

        import telegram.bot
        original_auth_id = telegram.bot.AUTHORIZED_CHAT_ID
        telegram.bot.AUTHORIZED_CHAT_ID = "12345"

        try:
            res = await middleware(mock_handler, event_analyze, {})
            assert res == "handler_called"
            mock_handler.assert_called_once()
            event_analyze.answer.assert_not_called()
        finally:
            telegram.bot.AUTHORIZED_CHAT_ID = original_auth_id

    asyncio.run(run_test())
