# tests/test_penny_stocks_keyboard.py
"""Тесты для интеграции кнопки 'Проанализировать' и кэширования Penny Stocks."""
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from aiogram.types import InlineKeyboardMarkup

from telegram.bot import callback_analyze_market
from main import scheduled_penny_monitor


@pytest.mark.asyncio
@patch("main.bot.send_message", new_callable=AsyncMock)
@patch("agents.shared.python.db.get_active_penny_stocks")
@patch("agents.shared.python.db.update_penny_stock_price")
@patch("agents.shared.python.db.mark_penny_spike_sent")
@patch("core.singleton.get_core_engine")  # ✅ правильный путь
async def test_scheduled_penny_monitor_attaches_keyboard(
    mock_get_engine, mock_mark_sent, mock_update_price, mock_get_active, mock_send_message
):
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
    mock_get_engine.return_value.adapter.get_market.return_value = mock_m

    await scheduled_penny_monitor()  # ✅ прямой await

    mock_send_message.assert_called_once()
    _, kwargs = mock_send_message.call_args
    assert "reply_markup" in kwargs
    kb = kwargs["reply_markup"]
    assert isinstance(kb, InlineKeyboardMarkup)
    btns = [b for row in kb.inline_keyboard for b in row]
    assert any(b.callback_data == "analyze_mkt_penny_123" for b in btns)


@pytest.mark.asyncio
@patch("main.bot.send_message", new_callable=AsyncMock)
@patch("agents.shared.python.db.get_active_penny_stocks")
@patch("agents.shared.python.db.update_penny_stock_price")
@patch("agents.shared.python.db.mark_penny_spike_sent")
@patch("core.singleton.get_core_engine")  # ✅ правильный путь
async def test_scheduled_penny_monitor_no_outcome_attaches_keyboard(
    mock_get_engine, mock_mark_sent, mock_update_price, mock_get_active, mock_send_message
):
    mock_get_active.return_value = [{
        "market_id": "penny_456",
        "title": "Will eth drop below 2k?",
        "url": "https://polymarket.com/eth",
        "initial_price": 0.98,
        "predicted_outcome": "NO",
        "spike_alert_sent": 0
    }]
    mock_m = MagicMock()
    mock_m.price = 0.95
    mock_m.volume = 25000.0
    mock_m.close_time = None
    mock_get_engine.return_value.adapter.get_market.return_value = mock_m

    await scheduled_penny_monitor()

    mock_send_message.assert_called_once()
    args, kwargs = mock_send_message.call_args
    msg_text = args[1] if len(args) >= 2 else kwargs.get("text", "")
    assert msg_text, "send_message was called but text is empty"
    assert "(NO)" in msg_text, f"Expected '(NO)' in message text, got: {msg_text}"
    assert "reply_markup" in kwargs
    kb = kwargs["reply_markup"]
    assert isinstance(kb, InlineKeyboardMarkup)
    btns = [b for row in kb.inline_keyboard for b in row]
    assert any(b.callback_data == "analyze_mkt_penny_456" for b in btns)


@pytest.mark.asyncio
@patch("telegram.bot.get_market_discussions")
@patch("telegram.bot.get_market_from_db")
@patch("telegram.bot.get_core_engine")
async def test_callback_analyze_market_uses_cache(
    mock_get_engine, mock_get_mkt_db, mock_get_discussions
):
    mock_get_discussions.return_value = [
        {"agent_name": "SCOUT", "opinion": "Scout opinion", "confidence": 0.8, "agree": True},
        {"agent_name": "SWING", "opinion": "Swing opinion", "confidence": 0.9, "agree": True},
        {"agent_name": "SHADOW", "opinion": "Shadow opinion", "confidence": 0.95, "agree": True},
    ]
    mock_get_mkt_db.return_value = {
        "title": "Cached XRP Market",
        "url": "https://polymarket.com/xrp",
        "price": 0.03
    }
    mock_callback = AsyncMock()
    mock_callback.data = "analyze_mkt_penny_123"
    mock_callback.id = "cb_cached_123"

    await callback_analyze_market(mock_callback)

    mock_callback.answer.assert_called_once_with("📦 Восстанавливаю анализ из памяти...", show_alert=False)
    args, kwargs = mock_callback.message.answer.call_args
    assert "Архивное обсуждение рынка (из памяти)" in args[0]
    assert "Cached XRP Market" in args[0]
    mock_get_engine.return_value.run_team_discussion.assert_not_called()


@pytest.mark.asyncio
@patch("core.workflow.process_consensus")
@patch("core.workflow.run_agent_evaluation", new_callable=AsyncMock)
@patch("telegram.bot.get_market_discussions", return_value=[])
@patch("telegram.bot.get_core_engine")
@patch("telegram.bot._scan_lock.locked", return_value=False)
async def test_callback_analyze_market_no_cache_triggers_scan(
    mock_lock, mock_get_engine, mock_get_discussions, mock_run_eval, mock_consensus
):
    mock_run_eval.return_value = (MagicMock(), None, MagicMock())
    mock_engine = MagicMock()
    mock_engine._scan_lock.locked.return_value = False
    mock_engine._fetch_pre_orderbook.return_value = None
    
    from core.models import Market
    from datetime import datetime, timezone
    mock_market = Market(
        id="penny_123",
        platform="polymarket",
        title="Will doge reach 1$?",
        url="https://polymarket.com/doge",
        outcome="YES",
        price=0.02,
        close_time=datetime.now(timezone.utc),
        volume=15000.0,
        description="Test description"
    )
    mock_engine.adapter.get_market.return_value = mock_market
    mock_get_engine.return_value = mock_engine

    mock_callback = MagicMock()
    mock_callback.id = "cb_nocache_123"
    mock_callback.data = "analyze_mkt_penny_123"
    mock_callback.answer = AsyncMock()
    mock_callback.message = AsyncMock()

    await callback_analyze_market(mock_callback)
    await asyncio.sleep(0.1)  # Даем отработать фоновой задаче

    mock_callback.answer.assert_called_once_with("🔍 Запуск анализа рынка агентами...", show_alert=False)
    assert mock_callback.message.answer.called
    ans_args, _ = mock_callback.message.answer.call_args
    assert "Запуск ручного анализа рынка" in ans_args[0]
    assert "Will doge reach 1$?" in ans_args[0]
    mock_engine._run_shadow_analysis.assert_called_once()
    _, kwargs = mock_engine._run_shadow_analysis.call_args
    assert kwargs["m"].id == "penny_123"
