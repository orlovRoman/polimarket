"""
Тесты для отображения активного статуса сканирования и агентов в Telegram-боте.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from aiogram.types import LinkPreviewOptions
from telegram.bot import get_active_scan_status_text, command_scan_handler, callback_scan_handler, command_status_handler

def test_get_active_scan_status_text_empty():
    """Должен вернуть корректный шаблон при пустом стейте."""
    fake_state = {}
    with patch("core.engine.CoreEngine") as mock_engine_cls:
        mock_engine = MagicMock()
        mock_engine.state = fake_state
        mock_engine_cls.return_value = mock_engine
        
        status_text = get_active_scan_status_text()
        
    assert "Сканирование уже запущено" in status_text
    assert "Авто-микс" in status_text
    assert "В процессе" in status_text
    assert "🕵️‍♂️ <b>SCOUT:</b> ⏳ Ожидает" in status_text

def test_get_active_scan_status_text_filled():
    """Должен красиво отображать все заполненные поля стейта."""
    fake_state = {
        "category": "🏛 Политика",
        "stage": "Обсуждение (SCOUT + SWING + SHADOW)",
        "total_markets": 10,
        "current_market_index": 3,
        "current_market_title": "Will Trump build a wall in 2026?",
        "current_market_url": "https://polymarket.com/wall",
        "scout_status": "🟢 Edge (0.15)",
        "swing_status": "🚀 Ждет памп",
        "shadow_status": "✅ Согласен (Увер: 0.85)",
        "ideas_found": 2
    }
    with patch("core.engine.CoreEngine") as mock_engine_cls:
        mock_engine = MagicMock()
        mock_engine.state = fake_state
        mock_engine_cls.return_value = mock_engine
        
        status_text = get_active_scan_status_text()
        
    assert "🏛 Политика" in status_text
    assert "Обсуждение" in status_text
    assert "Рынок <code>3</code> из <code>10</code>" in status_text
    assert "<a href='https://polymarket.com/wall'>Will Trump build a wall in 2026?</a>" in status_text
    assert "🟢 Edge (0.15)" in status_text
    assert "🚀 Ждет памп" in status_text
    assert "✅ Согласен (Увер: 0.85)" in status_text
    assert "Найдено идей (консенсус): 2" in status_text

def test_command_scan_handler_locked():
    """command_scan_handler должен выводить детальный статус, если сканирование уже запущено."""
    async def run_test():
        mock_message = AsyncMock()
        
        with patch("telegram.bot._scan_lock.locked", return_value=True), \
             patch("telegram.bot.get_active_scan_status_text", return_value="FAKE_SCANNING_STATUS"):
            await command_scan_handler(mock_message)
            
        mock_message.answer.assert_called_once_with("FAKE_SCANNING_STATUS", parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))

    asyncio.run(run_test())

def test_callback_scan_handler_locked():
    """callback_scan_handler должен выводить детальный статус, если сканирование уже запущено."""
    async def run_test():
        mock_callback = AsyncMock()
        mock_callback.data = "scan_politics"
        mock_callback.message = AsyncMock()
        
        with patch("telegram.bot._scan_lock.locked", return_value=True), \
             patch("telegram.bot.get_active_scan_status_text", return_value="FAKE_CALLBACK_STATUS"):
            await callback_scan_handler(mock_callback)
            
        mock_callback.answer.assert_called_once()
        mock_callback.message.answer.assert_called_once_with("FAKE_CALLBACK_STATUS", parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))

    asyncio.run(run_test())

def test_command_status_handler_scanning():
    """command_status_handler должен прицеплять детали сканирования, если оно запущено."""
    async def run_test():
        mock_message = AsyncMock()
        
        fake_state = {
            "category": "⚽ Спорт",
            "stage": "Скрининг",
            "current_market_title": "Ronaldo retires?",
            "scout_status": "🟢 Edge (0.12)",
            "swing_status": "🚀 Ждет памп",
            "shadow_status": "⏳ Проверяет...",
            "ideas_found": 1,
        }
        
        # Мокаем БД и локи
        with patch("telegram.bot._scan_lock.locked", return_value=True), \
             patch("core.engine.CoreEngine") as mock_engine_cls, \
             patch("agents.shared.python.db.get_connection") as mock_conn, \
             patch("agents.shared.python.db.get_memory_stats", return_value={}), \
             patch("agents.shared.python.db.get_memory", return_value=None):
             
            mock_engine = MagicMock()
            mock_engine.state = fake_state
            mock_engine._scan_lock.locked.return_value = True
            mock_engine_cls.return_value = mock_engine
            
            await command_status_handler(mock_message)
            
        # Проверяем, что в ответе есть информация о запущенном сканировании
        sent_text = mock_message.answer.call_args[0][0]
        assert "Детали текущего сканирования" in sent_text
        assert "⚽ Спорт" in sent_text
        assert "Ronaldo retires?" in sent_text
        
        # Проверяем наличие всех агентов и статусов (BUG-3)
        assert "SCOUT" in sent_text
        assert "SWING" in sent_text
        assert "SHADOW" in sent_text
        assert "🟢 Edge (0.12)" in sent_text
        assert "🚀 Ждет памп" in sent_text
        assert "⏳ Проверяет..." in sent_text
        assert "найдено идей" in sent_text.lower()

    asyncio.run(run_test())
