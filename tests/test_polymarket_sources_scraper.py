import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from agents.shared.utils.polymarket_sources_scraper import fetch_market_oracle_links, is_valid_external_url

def test_is_valid_external_url():
    assert is_valid_external_url("https://reuters.com/article/xyz") is True
    assert is_valid_external_url("https://polymarket.com/event/abc") is False
    assert is_valid_external_url("https://gamma-api.polymarket.com/markets/123") is False
    assert is_valid_external_url("invalid_url") is False

@pytest.mark.asyncio
async def test_fetch_market_oracle_links_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "12345",
        "resolutionSource": "https://apnews.com/article/1",
        "description": "This is a test. Check https://reuters.com/article/2 and https://polymarket.com/event/xyz for details."
    }

    from unittest.mock import ANY
    # Имитируем httpx.AsyncClient.get
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response) as mock_get:
        links = await fetch_market_oracle_links("12345")
        
        mock_get.assert_called_once_with("https://gamma-api.polymarket.com/markets/12345", headers=ANY)
        assert len(links) == 2
        assert links[0] == "https://apnews.com/article/1"
        assert links[1] == "https://reuters.com/article/2"

@pytest.mark.asyncio
async def test_fetch_market_oracle_links_fallback_on_error():
    mock_response = MagicMock()
    mock_response.status_code = 500

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
        links = await fetch_market_oracle_links(
            "12345", 
            market_description="Fallback link: https://bbc.com/news/1"
        )
        assert len(links) == 1
        assert links[0] == "https://bbc.com/news/1"
