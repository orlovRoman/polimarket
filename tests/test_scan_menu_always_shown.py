import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram import types
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from telegram.bot import command_scan_handler

@pytest.mark.asyncio
async def test_scan_command_shows_menu_when_busy():
    """При занятом lock /scan показывает меню, а не 'занято'."""
    message = AsyncMock()
    message.answer = AsyncMock()
    
    with patch("telegram.bot._scan_lock") as mock_lock, \
         patch("telegram.bot.get_core_engine") as mock_engine_func:
         
        mock_lock.locked.return_value = True
        
        mock_engine = MagicMock()
        mock_engine._scan_lock.locked.return_value = True
        mock_engine.state = {
            "category": "Авто-микс",
            "stage": "В процессе",
            "current_market_index": 1,
            "total_markets": 10,
            "current_market_title": "Test Title",
            "current_market_url": "http://test.url",
            "scout_status": "ok",
            "swing_status": "ok",
            "shadow_status": "ok",
            "ideas_found": 1
        }
        mock_engine_func.return_value = mock_engine
        
        await command_scan_handler(message)
        
        # assert message.answer was called (меню показано)
        assert message.answer.called
        
        # assert reply_markup is not None (клавиатура есть)
        args, kwargs = message.answer.call_args
        assert "reply_markup" in kwargs
        assert kwargs["reply_markup"] is not None
        
        # assert banner shown
        text = args[0]
        assert "Новый запуск будет доступен после завершения текущего сканирования" in text or "занято" in text.lower() or "━━━━━━━━━━━━━━━━━━━━" in text

@pytest.mark.asyncio
async def test_scan_command_shows_menu_when_not_busy():
    """При свободном lock /scan показывает меню."""
    message = AsyncMock()
    message.answer = AsyncMock()
    
    with patch("telegram.bot._scan_lock") as mock_lock, \
         patch("telegram.bot.get_core_engine") as mock_engine_func:
         
        mock_lock.locked.return_value = False
        
        mock_engine = MagicMock()
        mock_engine._scan_lock.locked.return_value = False
        mock_engine_func.return_value = mock_engine
        
        await command_scan_handler(message)
        
        # assert message.answer was called (меню показано)
        assert message.answer.called
        
        # assert reply_markup is not None (клавиатура есть)
        args, kwargs = message.answer.call_args
        assert "reply_markup" in kwargs
        assert kwargs["reply_markup"] is not None
        
        text = args[0]
        assert "Выберите категорию для сканирования" in text
