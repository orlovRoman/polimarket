import pytest
import asyncio
import traceback
from unittest.mock import MagicMock, patch
from services.telegram_listener import trigger_nexus_scan

def test_trigger_scan_logs_traceback_on_error():
    """Проверяет, что при возникновении исключения в _trigger_scan выводится полный traceback в logger"""
    # Настраиваем мок для CoreEngine, чтобы он бросал ошибку при вызове run_team_discussion
    with patch("services.telegram_listener._get_core_engine") as mock_get_engine, \
         patch("services.telegram_listener.logger") as mock_logger, \
         patch("services.telegram_listener.send_telegram_notify") as mock_send:
         
        mock_engine = MagicMock()
        mock_engine.run_team_discussion.side_effect = Exception("Test engine error")
        mock_get_engine.return_value = mock_engine
        
        # Запускаем trigger_nexus_scan через asyncio.run
        asyncio.run(trigger_nexus_scan("market_test", amount_usd=15000.0, source="whale"))
        
        # Даем фоновому потоку время выполниться
        # Поскольку thread запускается асинхронно, мы немного поспим
        import time
        time.sleep(0.2)
        
        # Проверяем, что logger.error был вызван и содержит traceback ошибки
        error_calls = mock_logger.error.call_args_list
        assert len(error_calls) > 0
        
        # Проверяем, что в аргументах лога есть имя нашего исключения и слово traceback
        log_msg = error_calls[0][0][0]
        assert "Test engine error" in log_msg
        assert "traceback" in log_msg or "Traceback" in log_msg

def test_trigger_scan_news_amount_is_zero():
    """Проверяет, что при новостном триггере amount_usd равен 0.0 и передается корректно"""
    with patch("services.telegram_listener._get_core_engine") as mock_get_engine, \
         patch("services.telegram_listener.logger") as mock_logger, \
         patch("services.telegram_listener.send_telegram_notify") as mock_send:
         
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine
        
        # Запускаем новостной триггер через asyncio.run
        asyncio.run(trigger_nexus_scan("market_news_test", amount_usd=0.0, source="some_news_channel"))
        
        # Ждем выполнения потока
        import time
        time.sleep(0.2)
        
        # Проверяем, что run_team_discussion был вызван с правильным market_id и source_text
        mock_engine.run_team_discussion.assert_called_once_with(
            market_id="market_news_test",
            trigger_type="event_driven",
            source_url="",
            source_text="Triggered by: some_news_channel"
        )
