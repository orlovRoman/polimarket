"""
Тесты _is_market_active без зависимости от MagicMock guard.
Атрибуты задаются явно, не через MagicMock.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import services.telegram_listener as tl

def _make_market(id="m1", closed=False, end_date_iso=None, close_time=None):
    return SimpleNamespace(
        id=id,
        closed=closed,
        end_date_iso=end_date_iso,
        endDate=None,
        end=None,
        close_time=close_time,
        title="Test market",
        price=0.5
    )

class TestIsMarketActive:

    def test_active_market_returns_true(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        m = _make_market(end_date_iso=future)
        
        with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
            adapter_instance = MockAdapter.return_value
            adapter_instance.get_event_by_slug.return_value = [m]
            
            result = asyncio.run(tl.resolve_market_ids_from_url("https://polymarket.com/event/test", "Test"))
            assert result == ["m1"]

    def test_closed_flag_returns_false(self):
        m = _make_market(closed=True)
        
        with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
            adapter_instance = MockAdapter.return_value
            adapter_instance.get_event_by_slug.return_value = [m]
            
            result = asyncio.run(tl.resolve_market_ids_from_url("https://polymarket.com/event/test", "Test"))
            assert result == []

    def test_expired_date_iso_returns_false(self):
        past = "2020-01-01T00:00:00Z"
        m = _make_market(end_date_iso=past)
        
        with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
            adapter_instance = MockAdapter.return_value
            adapter_instance.get_event_by_slug.return_value = [m]
            
            result = asyncio.run(tl.resolve_market_ids_from_url("https://polymarket.com/event/test", "Test"))
            assert result == []

    def test_naive_end_date_no_type_error(self):
        """Naive datetime не вызывает TypeError при сравнении."""
        naive = "2020-06-15T12:00:00"  # без timezone
        m = _make_market(end_date_iso=naive)
        
        with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
            adapter_instance = MockAdapter.return_value
            adapter_instance.get_event_by_slug.return_value = [m]
            
            result = asyncio.run(tl.resolve_market_ids_from_url("https://polymarket.com/event/test", "Test"))
            assert result == []  # Прошлое время, корректно отфильтровано без TypeError

    def test_no_mock_guard_needed(self):
        """Проверяем, что в коде функции resolve_market_ids_from_url нет MagicMock гварда."""
        import inspect
        src = inspect.getsource(tl.resolve_market_ids_from_url)
        # Очистим комментарии с MagicMock в исходнике, если они есть
        src_no_comments = "\n".join(line for line in src.splitlines() if not line.strip().startswith("#"))
        assert 'MagicMock' not in src_no_comments, (
            "Продакшн-код содержит MagicMock guard — это анти-паттерн!"
        )
