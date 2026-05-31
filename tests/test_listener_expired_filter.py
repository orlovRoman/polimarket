import asyncio
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from services.telegram_listener import resolve_market_ids_from_url

def test_resolve_market_ids_filters_closed_and_expired():
    """Проверяет, что fallback-логика фильтрует рынки, которые закрыты или у которых дата окончания в прошлом"""
    with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
        adapter_instance = MockAdapter.return_value
        adapter_instance.get_event_by_slug.return_value = [] # заставляем сработать fallback

        with patch("httpx.AsyncClient.get") as mock_get:
            async def mock_get_coro(url, *args, **kwargs):
                mock_response = MagicMock()
                mock_response.status_code = 200
                if "events/" in str(url):
                    mock_response.json.return_value = {
                        "markets": [
                            {
                                "id": "market_closed",
                                "closed": True,
                                "endDate": "2099-01-01T00:00:00Z"
                            },
                            {
                                "id": "market_expired",
                                "closed": False,
                                "endDate": "2020-01-01T00:00:00Z"
                            },
                            {
                                "id": "market_active",
                                "closed": False,
                                "endDate": "2099-01-01T00:00:00Z"
                            }
                        ]
                    }
                else:
                    mock_response.json.return_value = [{"id": "event_123"}]
                return mock_response

            mock_get.side_effect = mock_get_coro

            result = asyncio.run(resolve_market_ids_from_url("https://polymarket.com/event/test-slug"))
            # Должен вернуться только активный рынок
            assert result == ["market_active"]

def test_resolve_market_ids_filters_closed_and_expired_main_path():
    """Проверяет, что основная логика адаптера фильтрует рынки, которые закрыты или у которых дата окончания в прошлом"""
    mock_market_closed = MagicMock()
    mock_market_closed.id = "market_closed"
    mock_market_closed.closed = True
    mock_market_closed.close_time = datetime(2099, 1, 1, tzinfo=timezone.utc)

    mock_market_expired = MagicMock()
    mock_market_expired.id = "market_expired"
    mock_market_expired.closed = False
    mock_market_expired.close_time = datetime(2020, 1, 1, tzinfo=timezone.utc)

    mock_market_active = MagicMock()
    mock_market_active.id = "market_active"
    mock_market_active.closed = False
    mock_market_active.close_time = datetime(2099, 1, 1, tzinfo=timezone.utc)

    with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
        adapter_instance = MockAdapter.return_value
        adapter_instance.get_event_by_slug.return_value = [
            mock_market_closed, mock_market_expired, mock_market_active
        ]

        result = asyncio.run(resolve_market_ids_from_url("https://polymarket.com/event/test-slug"))
        # Должен вернуться только активный рынок
        assert result == ["market_active"]

