"""Тест: send_or_edit не падает при TelegramBadRequest 'message not modified'."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.exceptions import TelegramBadRequest

def test_send_or_edit_ignores_not_modified():
    """edit_text поднимает TelegramBadRequest — send_or_edit должна поглотить ошибку."""
    from telegram.bot import send_or_edit
    from aiogram import types

    fake_callback = MagicMock(spec=types.CallbackQuery)
    fake_callback.message = MagicMock()
    
    # Настраиваем асинхронные моки
    async def async_bad_request(*args, **kwargs):
        raise TelegramBadRequest(method=MagicMock(), message="message is not modified")
    
    async def async_noop(*args, **kwargs):
        return None

    fake_callback.message.edit_text.side_effect = async_bad_request
    fake_callback.answer.side_effect = async_noop

    # Должна вернуться без исключения
    asyncio.run(send_or_edit(fake_callback, "Тестовый текст"))
