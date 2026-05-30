"""Тест: callback_delete_signal корректно обрабатывает невалидный номер страницы."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

def test_delete_signal_invalid_page_falls_back_to_zero():
    """Если parts[2] не число — page должен быть 0, а не ValueError."""
    from telegram.bot import callback_delete_signal
    from aiogram.types import CallbackQuery

    fake_cb = MagicMock(spec=CallbackQuery)
    # del_sig_NOTANUMBER_some-uuid-123
    fake_cb.data = "del_sig_NOTANUMBER_abc123"
    
    async def async_noop(*args, **kwargs):
        return None
        
    fake_cb.answer.side_effect = async_noop
    fake_cb.message = MagicMock()

    with patch("telegram.bot.archive_signal_by_id", return_value=True), \
         patch("telegram.bot.send_ideas_page", new_callable=AsyncMock) as mock_page:
         
        async def mock_send_ideas_page(message_or_callback, page=0):
            return None
        mock_page.side_effect = mock_send_ideas_page
        
        asyncio.run(callback_delete_signal(fake_cb))
        # page=0 используется как fallback
        mock_page.assert_called_once_with(fake_cb, page=0)
