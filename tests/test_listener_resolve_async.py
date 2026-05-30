import inspect
import asyncio
from unittest.mock import MagicMock, patch
from services.telegram_listener import resolve_market_ids_from_url

def test_resolve_market_ids_is_coroutine():
    """Проверяет, что resolve_market_ids_from_url является корутиной"""
    assert inspect.iscoroutinefunction(resolve_market_ids_from_url)

def test_resolve_market_ids_adapter_success():
    """Проверяет успешное получение market_ids через адаптер (asyncio.to_thread)"""
    mock_market = MagicMock()
    mock_market.id = "market_123"
    mock_market.title = "Will Bitcoin hit 100k?"
    mock_market.price = 0.5

    with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
        adapter_instance = MockAdapter.return_value
        adapter_instance.get_event_by_slug.return_value = [mock_market]

        # Вызываем асинхронную функцию через asyncio.run
        result = asyncio.run(resolve_market_ids_from_url("https://polymarket.com/event/bitcoin-100k", "Bitcoin"))
        
        assert result == ["market_123"]
        # Проверяем, что get_event_by_slug вызвался с правильным слагом
        adapter_instance.get_event_by_slug.assert_called_once_with("bitcoin-100k")

def test_resolve_market_ids_fallback_http():
    """Проверяет fallback-логику через httpx.AsyncClient, если адаптер вернул None/пустой список"""
    mock_json_event = [
        {
            "markets": [
                {"id": "market_abc"}
            ]
        }
    ]

    with patch("services.telegram_listener.PolymarketAdapter") as MockAdapter:
        adapter_instance = MockAdapter.return_value
        adapter_instance.get_event_by_slug.return_value = [] # пустой результат

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_json_event
            
            # Настраиваем mock для асинхронного вызова client.get
            async def mock_get_coro(*args, **kwargs):
                return mock_response
            mock_get.side_effect = mock_get_coro

            result = asyncio.run(resolve_market_ids_from_url("https://polymarket.com/event/bitcoin-100k"))
            assert result == ["market_abc"]
            mock_get.assert_called_with("https://gamma-api.polymarket.com/events", params={"slug": "bitcoin-100k"})
