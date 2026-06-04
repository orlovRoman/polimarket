import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram import types
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from telegram.bot import callback_scan_handler

@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["weather", "entertainment", "geopolitics", "health"])
async def test_callback_new_categories_handled(slug):
    """Новые категории корректно обрабатываются в callback_scan_handler без KeyError."""
    callback = AsyncMock()
    callback.data = f"scan_{slug}"
    callback.id = f"test_id_{slug}"
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    
    with patch("telegram.bot._scan_lock") as mock_lock, \
         patch("telegram.bot.get_core_engine") as mock_engine_func, \
         patch("telegram.bot.asyncio.create_task"), \
         patch("telegram.bot.asyncio.wait_for"):
         
        mock_lock.locked.return_value = False
        
        mock_engine = MagicMock()
        mock_engine._scan_lock.locked.return_value = False
        mock_engine_func.return_value = mock_engine
        
        try:
            await callback_scan_handler(callback)
        except Exception as e:
            pytest.fail(f"Handler raised an exception: {e}")
            
        assert callback.message.edit_text.called
