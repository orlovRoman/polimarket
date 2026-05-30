"""
Тест: Исключение в _run_trend_hunter_safe логируется именно в logger ("NexusPolyBot").
"""
import pytest
import asyncio
from unittest.mock import MagicMock, patch

def test_logging_channel_trend_hunter():
    callback_query = MagicMock()
    
    async def async_noop(*args, **kwargs):
        return None
    callback_query.answer.side_effect = async_noop
    callback_query.message.edit_text.side_effect = async_noop
    
    # 1. Замокаем run_trend_hunter, чтобы он бросал исключение
    with patch("services.trend_hunter.run_trend_hunter", side_effect=ValueError("Trend hunter test error")):
        # 2. Перехватываем logger
        with patch("telegram.bot.logger") as mock_logger:
            # Мокаем bot.send_message
            with patch("telegram.bot.bot.send_message") as mock_send:
                mock_send.side_effect = async_noop
                
                # Перехватываем создание таска
                original_create_task = asyncio.create_task
                created_tasks = []
                
                def fake_create_task(coro, *args, **kwargs):
                    task = original_create_task(coro, *args, **kwargs)
                    created_tasks.append(task)
                    return task
                    
                with patch("asyncio.create_task", side_effect=fake_create_task):
                    from telegram.bot import callback_trigger_trend_hunter
                    
                    asyncio.run(callback_trigger_trend_hunter(callback_query))
                    
                    # Ждем завершения фонового таска
                    assert len(created_tasks) == 1
                    asyncio.run(created_tasks[0])
                    
            # 3. Убеждаемся, что исключение попало в logger.error
            mock_logger.error.assert_called_once()
            log_msg = mock_logger.error.call_args[0][0]
            assert "[TrendHunter] Необработанное исключение: Trend hunter test error" in log_msg
