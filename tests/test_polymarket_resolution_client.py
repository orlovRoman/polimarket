import pytest
from unittest.mock import patch, MagicMock
from core.eval.polymarket_resolution_client import PolymarketResolutionClient

def test_fetch_resolved_yes():
    mock_data = {
        "closed": True,
        "tokens": [
            {"outcome": "YES", "price": "1"},
            {"outcome": "NO", "price": "0"},
        ]
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_data,
            raise_for_status=lambda: None
        )
        client = PolymarketResolutionClient()
        result = client.fetch_resolution("0xABC")
    assert result.is_resolved is True
    assert result.winning_outcome == "YES"
    assert result.resolution_price == 1.0

def test_fetch_not_resolved():
    mock_data = {"closed": False, "tokens": []}
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_data,
            raise_for_status=lambda: None
        )
        result = PolymarketResolutionClient().fetch_resolution("0xDEF")
    assert result.is_resolved is False
    assert result.winning_outcome is None

def test_fetch_network_error():
    with patch("requests.get", side_effect=Exception("timeout")):
        result = PolymarketResolutionClient().fetch_resolution("0xXYZ")
    assert result is None  # не крашимся, возвращаем None
