import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from telegram.bot import command_eval_history_handler, callback_eval_history_handler

@pytest.fixture
def user_message():
    msg = AsyncMock(spec=Message)
    msg.answer = AsyncMock()
    return msg

@pytest.fixture
def callback_query():
    cb = AsyncMock(spec=CallbackQuery)
    cb.id = "test_cb_id"
    cb.data = "evalhist_scout"
    cb.message = AsyncMock(spec=Message)
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb

@pytest.mark.asyncio
async def test_eval_history_shows_keyboard(user_message):
    """/eval_history без аргументов показывает inline-клавиатуру."""
    await command_eval_history_handler(user_message)
    
    user_message.answer.assert_called_once()
    args, kwargs = user_message.answer.call_args
    assert "История калибровок" in args[0]
    
    reply_markup = kwargs.get("reply_markup")
    assert reply_markup is not None
    callbacks = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
    assert "evalhist_scout" in callbacks
    assert "evalhist_whale" in callbacks

@pytest.mark.asyncio
async def test_eval_history_callback_works(callback_query):
    """Callback evalhist_scout возвращает историю, не ошибку."""
    with patch("core.eval.calibration_store.CalibrationStore") as mock_store_cls:
        mock_store = MagicMock()
        mock_store.get_strategy_history = AsyncMock(return_value=[])
        mock_store_cls.return_value = mock_store
        
        await callback_eval_history_handler(callback_query)
        
        callback_query.message.answer.assert_called_once()
        args, kwargs = callback_query.message.answer.call_args
        assert "❌" not in args[0]
        assert "пуста" in args[0]
