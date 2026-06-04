from unittest.mock import AsyncMock
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from telegram.bot import command_scan_handler, callback_scan_handler, build_scan_keyboard, _scan_lock

@pytest.fixture
def user_message():
    msg = AsyncMock(spec=Message)
    msg.answer = AsyncMock()
    return msg

@pytest.fixture
def callback_query():
    cb = AsyncMock(spec=CallbackQuery)
    cb.id = "test_cb_id"
    cb.data = "scan_politics"
    cb.message = AsyncMock(spec=Message)
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb

@pytest.mark.asyncio
async def test_scan_shows_menu_when_free(user_message):
    """При свободном lock — только меню, без busy-banner."""
    # Ensure lock is not held
    if _scan_lock.locked():
        _scan_lock.release()
    
    with patch("telegram.bot.get_core_engine") as mock_engine_cls:
        mock_engine = MagicMock()
        mock_engine._scan_lock = MagicMock()
        mock_engine._scan_lock.locked.return_value = False
        mock_engine_cls.return_value = mock_engine
        
        await command_scan_handler(user_message)
        
        user_message.answer.assert_called_once()
        args, kwargs = user_message.answer.call_args
        assert "Выберите категорию" in args[0]
        assert "запустится" not in args[0]
        assert "после завершения" not in args[0]

@pytest.mark.asyncio
async def test_scan_shows_menu_when_busy(user_message):
    """При занятом lock — статус + меню, без ложного обещания очереди."""
    with patch("telegram.bot.get_core_engine") as mock_engine_cls, \
         patch("telegram.bot.get_active_scan_status_text", return_value="BUSY_STATUS"):
        mock_engine = MagicMock()
        mock_engine._scan_lock = MagicMock()
        mock_engine._scan_lock.locked.return_value = True
        mock_engine_cls.return_value = mock_engine
        
        await command_scan_handler(user_message)
        
        user_message.answer.assert_called_once()
        args, kwargs = user_message.answer.call_args
        assert "BUSY_STATUS" in args[0]
        assert "Выберите категорию — запустится после окончания:" in args[0]
        assert kwargs.get("reply_markup") is not None

@pytest.mark.asyncio
async def test_callback_does_not_start_second_scan(callback_query):
    """При занятом lock нажатие на категорию не запускает второй scan."""
    with patch("telegram.bot.get_core_engine") as mock_engine_cls, \
         patch("telegram.bot.get_active_scan_status_text", return_value="BUSY_STATUS"):
        mock_engine = MagicMock()
        mock_engine._scan_lock = MagicMock()
        mock_engine._scan_lock.locked.return_value = True
        mock_engine_cls.return_value = mock_engine
        
        await callback_scan_handler(callback_query)
        
        callback_query.answer.assert_called_once()
        callback_query.message.answer.assert_called_once()
        args, _ = callback_query.message.answer.call_args
        assert "BUSY_STATUS" in args[0]

def test_all_categories_in_keyboard():
    """Все 12 callback должны быть в клавиатуре (включая scan_all)."""
    kb = build_scan_keyboard()
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    expected = ["scan_all","scan_politics","scan_crypto","scan_sports","scan_culture",
                "scan_science","scan_business","scan_weather","scan_entertainment",
                "scan_geopolitics","scan_health","scan_penny_stocks"]
    for cb in expected:
        assert cb in callbacks, f"{cb} отсутствует в клавиатуре"
