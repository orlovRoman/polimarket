import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta, timezone
from telegram.bot import AuthMiddleware

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

def test_auth_middleware_stale_callback_queries():
    async def run_test():
        middleware = AuthMiddleware()
        
        # We patch types.CallbackQuery and types.Message in telegram.bot
        # so that isinstance checks in AuthMiddleware resolve to our mock classes
        with patch('telegram.bot.types.CallbackQuery', MockCallbackQuery), \
             patch('telegram.bot.types.Message', MockMessage):
             
            old_time = datetime.now(timezone.utc) - timedelta(minutes=20)
            
            # 1. Test clicking "Ignore" (ignore_mkt_XYZ) on an old message (should bypass stale check)
            event_ignore = MockCallbackQuery("ignore_mkt_market123", old_time)
            mock_handler = AsyncMock()
            mock_handler.return_value = "handler_called"
            
            # Auth checks bypass mock setup
            import telegram.bot
            original_auth_id = telegram.bot.AUTHORIZED_CHAT_ID
            telegram.bot.AUTHORIZED_CHAT_ID = "12345"
            
            try:
                res = await middleware(mock_handler, event_ignore, {})
                assert res == "handler_called"
                mock_handler.assert_called_once()
                event_ignore.answer.assert_not_called()
                
                # 2. Test clicking a different button on an old message (should be rejected as stale)
                mock_handler.reset_mock()
                event_other = MockCallbackQuery("other_btn_market123", old_time)
                
                res = await middleware(mock_handler, event_other, {})
                assert res is None
                mock_handler.assert_not_called()
                event_other.answer.assert_called_once_with(
                    "⚠️ Сессия устарела. Повторите команду заново.", 
                    show_alert=True
                )
                
            finally:
                telegram.bot.AUTHORIZED_CHAT_ID = original_auth_id

    asyncio.run(run_test())
