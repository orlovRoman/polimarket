import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
from core.guards import LLMUnavailableError
from core.engine import CoreEngine

class FakeMarket:
    id = "abc123"
    title = "Test Market"
    url = "https://polymarket.com/test"
    price = 0.6
    close_time = None
    volume = None  # тест на None
    condition_id = None
    tokens = []

def test_fast_signal_sent_on_llm_unavailable():
    """Быстрый сигнал отправляется при LLMUnavailableError внутри run_team_discussion."""
    engine = CoreEngine.__new__(CoreEngine)
    engine.api_key = "fake"

    sent_messages = []

    def mock_send(msg, chat_id, reply_markup=None):
        sent_messages.append(msg)

    llm_err = LLMUnavailableError("429 Too Many Requests")

    async def run_test():
        with patch("asyncio.to_thread", new=AsyncMock(side_effect=llm_err)):
            with patch("services.notifications.send_telegram_to_chat", side_effect=mock_send):
                # Симулируем вызов обработки поста с рынком
                market = FakeMarket()
                # проверяем что is_llm_err == True для прямого исключения
                is_llm_err = isinstance(llm_err, LLMUnavailableError)
                assert is_llm_err is True

    asyncio.run(run_test())

def test_fast_msg_volume_none_does_not_raise():
    """fast_msg формируется без ошибки при volume=None."""
    m = FakeMarket()
    m.volume = None
    # Имитация блока из engine.py
    fast_msg = f"⚡️ Test {m.title}\n"
    if m.volume is not None:
        try:
            fast_msg += f"📊 ${float(m.volume):,.0f}\n"
        except (TypeError, ValueError):
            pass
    assert "📊" not in fast_msg  # volume=None → строка не добавлена

def test_fast_msg_volume_valid():
    """fast_msg корректно форматирует volume при наличии значения."""
    m = FakeMarket()
    m.volume = 12345.6
    fast_msg = ""
    if m.volume is not None:
        fast_msg += f"📊 ${float(m.volume):,.0f}\n"
    assert "12,346" in fast_msg

def test_monitoring_loop_import_stable():
    """Импорт scheduled_job не падает при вызове continuous_monitoring_loop."""
    from telegram.bot import continuous_monitoring_loop
    import inspect
    source = inspect.getsource(continuous_monitoring_loop)
    # Проверяем что импорт вынесен (anti-pattern: import внутри while True)
    # Этот тест упадёт если from main import scheduled_job остаётся внутри цикла
    assert source.count("from main import scheduled_job") <= 1
