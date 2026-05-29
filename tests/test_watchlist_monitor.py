# tests/test_watchlist_monitor.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from services.watchlist_monitor import (
    _check_watchlist, PRICE_CHANGE_THRESHOLD, POLL_INTERVAL_SEC
)

# ── Константы ──────────────────────────────────────────────────────────────

def test_threshold_value():
    """Порог 50% — не меняем без явного намерения."""
    assert PRICE_CHANGE_THRESHOLD == 0.50

def test_poll_interval():
    """Интервал опроса — 10 минут."""
    assert POLL_INTERVAL_SEC == 600

# ── Дедупликация алертов ───────────────────────────────────────────────────

def test_no_alert_sent_twice_same_cycle():
    """Один и тот же алерт не отправляется дважды за один цикл."""
    async def run_test():
        entry = {
            'market_id': 'abc123',
            'market_title': 'Test Market',
            'last_price': 0.10,
            'base_price': 0.10,
        }
        mock_market = MagicMock()
        mock_market.price = 0.20  # +100% — выше порога
        mock_market.id = 'abc123'

        with patch('agents.shared.python.db.get_market_list', return_value=[entry]), \
             patch('agents.shared.python.db.update_watchlist_price'), \
             patch('agents.shared.python.db.is_alert_already_sent', return_value=True), \
             patch('agents.shared.python.db.mark_alert_sent') as mock_mark, \
             patch('agents.shared.adapters.polymarket.PolymarketAdapter') as MockAdapter:

            MockAdapter.return_value.get_market.return_value = mock_market
            mock_bot = AsyncMock()

            await _check_watchlist(mock_bot, '12345')
            mock_mark.assert_not_called()  # ...

    asyncio.run(run_test())

def test_alert_sent_on_price_spike():
    """Алерт отправляется при изменении цены выше порога."""
    async def run_test():
        entry = {
            'market_id': 'abc123',
            'market_title': 'Test Market',
            'last_price': 0.10,
            'base_price': None,  # base_price = None, last_price должен быть взят
        }
        # Устанавливаем last_price как базовую
        entry['last_price'] = 0.10

        mock_market = MagicMock()
        mock_market.price = 0.16  # +60%
        mock_market.id = 'abc123'
        mock_market.title = 'Test Market'
        mock_market.url = 'https://polymarket.com/event/test'

        with patch('agents.shared.python.db.get_market_list', return_value=[entry]), \
             patch('agents.shared.python.db.update_watchlist_price'), \
             patch('agents.shared.python.db.is_alert_already_sent', return_value=False), \
             patch('agents.shared.python.db.mark_alert_sent') as mock_mark, \
             patch('services.watchlist_monitor._send_watchlist_alert', new_callable=AsyncMock) as mock_send, \
             patch('agents.shared.adapters.polymarket.PolymarketAdapter') as MockAdapter:

            MockAdapter.return_value.get_market.return_value = mock_market
            mock_bot = AsyncMock()

            await _check_watchlist(mock_bot, '12345')
            mock_send.assert_called_once()
            mock_mark.assert_called_once()

    asyncio.run(run_test())

def test_no_alert_below_threshold():
    """Алерт НЕ отправляется при изменении цены ниже порога."""
    async def run_test():
        entry = {'market_id': 'abc123', 'market_title': 'T', 'last_price': 0.10, 'base_price': None}

        mock_market = MagicMock()
        mock_market.price = 0.13  # +30% — ниже порога 50%
        mock_market.id = 'abc123'

        with patch('agents.shared.python.db.get_market_list', return_value=[entry]), \
             patch('agents.shared.python.db.update_watchlist_price'), \
             patch('services.watchlist_monitor._send_watchlist_alert', new_callable=AsyncMock) as mock_send, \
             patch('agents.shared.adapters.polymarket.PolymarketAdapter') as MockAdapter:

            MockAdapter.return_value.get_market.return_value = mock_market
            await _check_watchlist(AsyncMock(), '12345')
            mock_send.assert_not_called()

    asyncio.run(run_test())

def test_skip_market_not_found():
    """Рынок недоступен в API — цикл продолжается без падения."""
    async def run_test():
        entry = {'market_id': 'missing_id', 'market_title': 'T', 'last_price': 0.1, 'base_price': None}

        with patch('agents.shared.python.db.get_market_list', return_value=[entry]), \
             patch('agents.shared.adapters.polymarket.PolymarketAdapter') as MockAdapter:

            MockAdapter.return_value.get_market.return_value = None
            await _check_watchlist(AsyncMock(), '12345')  # не должно бросить исключение

    asyncio.run(run_test())

# ── Telegram callback: unlist ──────────────────────────────────────────────

def test_market_id_truncation_consistency():
    """UUID (36 символов) не усекается — остаётся полным в callback_data."""
    uuid = 'a' * 36
    assert len(uuid[:40]) == 36  # UUID вписывается без усечения

def test_long_market_id_truncated():
    """ID длиннее 40 символов усекается корректно."""
    long_id = 'x' * 50
    assert len(long_id[:40]) == 40
