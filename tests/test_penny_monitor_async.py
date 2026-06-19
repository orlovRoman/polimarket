# tests/test_penny_monitor_async.py
"""Проверяет работу scheduled_penny_monitor и авторезолюцию дешевых рынков."""
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_penny_monitor_uses_asyncio_to_thread():
    """_fetch_resolution должен вызываться через asyncio.to_thread, не напрямую."""
    with patch("agents.shared.python.db.get_active_penny_stocks", return_value=[{
        "market_id": "p1", "title": "Test", "url": "http://x",
        "initial_price": 0.03, "current_price": 0.03,
        "spike_alert_sent": 0, "predicted_outcome": "YES"
    }]), \
    patch("core.singleton.get_core_engine") as mock_eng, \
    patch("agents.shared.python.db.update_penny_stock_price"), \
    patch("agents.shared.python.penny_execution_service.resolve_penny_stock"), \
    patch("telegram.bot.bot.send_message", new_callable=AsyncMock), \
    patch("services.outcome_tracker._fetch_resolution", return_value="YES") as mock_fetch, \
    patch("agents.shared.python.penny_execution_service.asyncio.to_thread", wraps=asyncio.to_thread) as mock_thread:


        market_mock = MagicMock()
        market_mock.price = 0.05
        market_mock.volume = 500.0
        from datetime import datetime, timezone, timedelta
        market_mock.close_time = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_eng.return_value.adapter.get_market.return_value = market_mock

        from main import scheduled_penny_monitor
        await scheduled_penny_monitor()

        mock_thread.assert_any_call(mock_fetch, "p1")

@pytest.mark.asyncio
async def test_penny_monitor_fallback_when_market_obj_none():
    """scheduled_penny_monitor должен резолвить рынок через _fetch_resolution, даже если get_market вернул None."""
    with patch("agents.shared.python.db.get_active_penny_stocks", return_value=[{
        "market_id": "p1", "title": "Test", "url": "http://x",
        "initial_price": 0.03, "current_price": 0.03,
        "spike_alert_sent": 0, "predicted_outcome": "YES"
    }]), \
    patch("core.singleton.get_core_engine") as mock_eng, \
    patch("agents.shared.python.db.update_penny_stock_price"), \
    patch("agents.shared.python.penny_execution_service.resolve_penny_stock") as mock_resolve, \
    patch("telegram.bot.bot.send_message", new_callable=AsyncMock) as mock_send, \
    patch("services.outcome_tracker._fetch_resolution", return_value="YES") as mock_fetch, \
    patch("agents.shared.python.penny_execution_service.asyncio.to_thread", wraps=asyncio.to_thread) as mock_thread:


        mock_eng.return_value.adapter.get_market.return_value = None

        from main import scheduled_penny_monitor
        await scheduled_penny_monitor()

        mock_resolve.assert_called_once_with("p1", "YES")
        mock_send.assert_called_once()
        mock_thread.assert_any_call(mock_fetch, "p1")

@pytest.mark.asyncio
async def test_penny_monitor_no_double_fetch():
    """При market_obj=None _fetch_resolution вызывается ровно 1 раз."""
    with patch("agents.shared.python.db.get_active_penny_stocks", return_value=[{
        "market_id": "p1", "title": "Test", "url": "http://x",
        "initial_price": 0.03, "current_price": 0.03,
        "spike_alert_sent": 0, "predicted_outcome": "YES"
    }]), \
    patch("core.singleton.get_core_engine") as mock_eng, \
    patch("agents.shared.python.db.update_penny_stock_price"), \
    patch("agents.shared.python.penny_execution_service.resolve_penny_stock"), \
    patch("telegram.bot.bot.send_message", new_callable=AsyncMock), \
    patch("services.outcome_tracker._fetch_resolution", return_value="YES") as mock_fetch:

        mock_eng.return_value.adapter.get_market.return_value = None

        from main import scheduled_penny_monitor
        await scheduled_penny_monitor()

        assert mock_fetch.call_count == 1

@pytest.mark.asyncio
async def test_penny_monitor_open_market_no_fetch():
    """При открытом рынке (close_time в будущем) _fetch_resolution НЕ вызывается."""
    with patch("agents.shared.python.db.get_active_penny_stocks", return_value=[{
        "market_id": "p1", "title": "Test", "url": "http://x",
        "initial_price": 0.03, "current_price": 0.03,
        "spike_alert_sent": 0, "predicted_outcome": "YES"
    }]), \
    patch("core.singleton.get_core_engine") as mock_eng, \
    patch("agents.shared.python.db.update_penny_stock_price"), \
    patch("agents.shared.python.penny_execution_service.resolve_penny_stock") as mock_resolve, \
    patch("telegram.bot.bot.send_message", new_callable=AsyncMock), \
    patch("services.outcome_tracker._fetch_resolution", return_value=None) as mock_fetch:

        market_mock = MagicMock()
        market_mock.price = 0.05
        market_mock.volume = 500.0
        from datetime import datetime, timezone, timedelta
        market_mock.close_time = datetime.now(timezone.utc) + timedelta(hours=10)
        mock_eng.return_value.adapter.get_market.return_value = market_mock

        from main import scheduled_penny_monitor
        await scheduled_penny_monitor()

        assert mock_fetch.call_count == 0
        mock_resolve.assert_not_called()
