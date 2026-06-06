# tests/test_penny_stocks_keyboard.py
"""Тесты для интеграции кнопки 'Проанализировать' и кэширования Penny Stocks."""
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from telegram.bot import callback_analyze_market_handler, AuthMiddleware
from main import scheduled_penny_monitor

# ── 1. Проверяем, что scheduled_penny_monitor прикрепляет Inline клавиатуру к алерту ───
@patch("main.bot.send_message", new_callable=AsyncMock)
@patch("agents.shared.python.db.get_active_penny_stocks")
@patch("agents.shared.python.db.update_penny_stock_price")
@patch("agents.shared.python.db.mark_penny_spike_sent")
@patch("main.engine.adapter.get_market")
def test_scheduled_penny_monitor_attaches_keyboard(
    mock_get_market, mock_mark_sent, mock_update_price, mock_get_active, mock_send_message
):
    # Условия: начальная цена 0.02, текущая 0.05 (рост 150% >= 100%)
    mock_get_active.return_value = [{
        "market_id": "penny_123",
        "title": "Will doge reach 1$?",
        "url": "https://polymarket.com/doge",
        "initial_price": 0.02,
        "predicted_outcome": "YES",
        "spike_alert_sent": 0
    }]
    
    mock_m = MagicMock()
    mock_m.price = 0.05
    mock_m.volume = 15000.0
    mock_m.close_time = None
    mock_get_market.return_value = mock_m

    async def run_test():
        await scheduled_penny_monitor()

    asyncio.run(run_test())

    mock_send_message.assert_called_once()
    args, kwargs = mock_send_message.call_args
    assert "reply_markup" in kwargs
    kb = kwargs["reply_markup"]
    assert isinstance(kb, InlineKeyboardMarkup)
    assert kb.inline_keyboard[0][0].text == "🔍 Проанализировать рынок"
    assert kb.inline_keyboard[0][0].callback_data == "analyze_mkt_penny_123"

# ── 2. Проверяем, что при наличии мнений в базе возвращается архивный отчет ───
@patch("telegram.bot.get_market_discussions")
@patch("telegram.bot.get_market_from_db")
@patch("telegram.bot.get_core_engine")
def test_callback_analyze_market_handler_uses_cache(mock_get_engine, mock_get_mkt_db, mock_get_discussions):
    # Имитируем сохраненные мнения
    mock_get_discussions.return_value = [
        {"agent_name": "SCOUT", "opinion": "Scout opinion here", "confidence": 0.8, "agree": True},
        {"agent_name": "SWING", "opinion": "Swing opinion here", "confidence": 0.9, "agree": True},
        {"agent_name": "SHADOW", "opinion": "Shadow opinion here", "confidence": 0.95, "agree": True}
    ]
    
    mock_get_mkt_db.return_value = {
        "title": "Cached XRP Market",
        "url": "https://polymarket.com/xrp",
        "price": 0.03
    }

    mock_callback = AsyncMock()
    mock_callback.answer = AsyncMock()
    mock_callback.message = AsyncMock()
    mock_callback.id = "cb_cached_123"
    mock_callback.data = "analyze_mkt_penny_123"

    async def run_test():
        await callback_analyze_market_handler(mock_callback)

    asyncio.run(run_test())

    # Должен ответить на callback, загрузить данные и прислать отчет в message.answer
    mock_callback.answer.assert_called_once_with("📦 Восстанавливаю анализ из памяти...", show_alert=False)
    mock_callback.message.answer.assert_called_once()
    
    args, kwargs = mock_callback.message.answer.call_args
    summary_text = args[0]
    assert "Архивное обсуждение рынка (из памяти)" in summary_text
    assert "Cached XRP Market" in summary_text
    assert "SCOUT" in summary_text
    assert "SWING" in summary_text
    assert "SHADOW" in summary_text
    assert "Восстановлено из памяти (LLM не вызывался)" in summary_text
    assert "reply_markup" in kwargs
    
    # run_team_discussion не должен вызываться
    mock_get_engine.return_value.run_team_discussion.assert_not_called()

# ── 3. Проверяем, что при отсутствии мнений запускается сканирование ───
@patch("telegram.bot.get_market_discussions", return_value=[])
@patch("telegram.bot.get_core_engine")
@patch("telegram.bot._scan_lock.locked", return_value=False)
def test_callback_analyze_market_handler_no_cache_triggers_scan(mock_lock, mock_get_engine, mock_get_discussions):
    mock_engine = MagicMock()
    mock_engine._scan_lock.locked.return_value = False
    mock_get_engine.return_value = mock_engine

    mock_callback = AsyncMock()
    mock_callback.answer = AsyncMock()
    mock_callback.message = AsyncMock()
    mock_callback.id = "cb_nocache_123"
    mock_callback.data = "analyze_mkt_penny_123"

    async def run_test():
        await callback_analyze_market_handler(mock_callback)
        await asyncio.sleep(0.1) # Даем отработать фоновой задаче
        mock_engine.run_team_discussion.assert_called_once()
        args, kwargs = mock_engine.run_team_discussion.call_args
        assert kwargs["market_id"] == "penny_123"

    asyncio.run(run_test())
    mock_callback.answer.assert_called_once_with("🔍 Запуск анализа рынка NEXUS...", show_alert=False)
    mock_callback.message.answer.assert_called_once_with("🔍 <b>Запуск ручного анализа рынка:</b> <code>penny_123</code>")
