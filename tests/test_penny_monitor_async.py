# tests/test_penny_monitor_async.py
"""Проверяет что scheduled_penny_monitor вызывает sync IO в event loop."""
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_penny_monitor_uses_asyncio_to_thread():
    """_fetch_resolution должен вызываться через asyncio.to_thread, не напрямую."""
    called_via_thread = []

    async def fake_to_thread(fn, *args):
        called_via_thread.append(fn.__name__)
        return "YES"

    with patch("agents.shared.python.db.get_active_penny_stocks", return_value=[{
        "market_id": "p1", "title": "Test", "url": "http://x",
        "initial_price": 0.03, "current_price": 0.03,
        "spike_alert_sent": 0, "predicted_outcome": "YES"
    }]), \
    patch("asyncio.to_thread", side_effect=fake_to_thread), \
    patch("core.singleton.get_core_engine") as mock_eng, \
    patch("agents.shared.python.db.update_penny_stock_price"), \
    patch("agents.shared.python.db.resolve_penny_stock"), \
    patch("telegram.bot.bot.send_message", new_callable=AsyncMock):
        
        market_mock = MagicMock()
        market_mock.price = 0.05
        market_mock.volume = 500.0
        # Задаем close_time в прошлом, чтобы сработала проверка исхода
        from datetime import datetime, timezone, timedelta
        market_mock.close_time = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_eng.return_value.adapter.get_market.return_value = market_mock

        # Импортируем и вызываем функцию мониторинга
        from main import scheduled_penny_monitor
        await scheduled_penny_monitor()

        # Проверяем, что _fetch_resolution была вызвана через to_thread
        assert "_fetch_resolution" in called_via_thread, \
               "_fetch_resolution НЕ была вызвана через asyncio.to_thread в async контексте"
