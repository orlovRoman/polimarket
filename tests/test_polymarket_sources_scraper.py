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

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get.return_value = mock_response

    with patch(
        "agents.shared.utils.polymarket_sources_scraper.httpx.AsyncClient",
        return_value=mock_client,
    ):
        links = await fetch_market_oracle_links("12345")
        mock_client.get.assert_called_once()
        assert links == ["https://apnews.com/article/1", "https://reuters.com/article/2"]

@pytest.mark.asyncio
async def test_fetch_market_oracle_links_fallback_on_error():
    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get.return_value = mock_response

    with patch(
        "agents.shared.utils.polymarket_sources_scraper.httpx.AsyncClient",
        return_value=mock_client,
    ):
        links = await fetch_market_oracle_links(
            "12345", 
            market_description="Fallback link: https://bbc.com/news/1"
        )
        assert links == ["https://bbc.com/news/1"]


@pytest.mark.asyncio
async def test_fetch_market_oracle_links_empty_on_error_no_description():
    """Статус 500 и нет market_description → пустой список."""
    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get.return_value = mock_response

    with patch(
        "agents.shared.utils.polymarket_sources_scraper.httpx.AsyncClient",
        return_value=mock_client,
    ):
        links = await fetch_market_oracle_links("12345")
        assert links == []


@pytest.mark.asyncio
async def test_fetch_market_oracle_links_dedup_resolution_source_in_description():
    """resolutionSource дублируется в description → возвращается 2 уникальных ссылки."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "resolutionSource": "https://apnews.com/article/1",
        "description": "See https://apnews.com/article/1 and https://reuters.com/article/2",
    }

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get.return_value = mock_response

    with patch(
        "agents.shared.utils.polymarket_sources_scraper.httpx.AsyncClient",
        return_value=mock_client,
    ):
        links = await fetch_market_oracle_links("12345")
        # С дедупликацией вернется ['apnews', 'reuters']
        assert links == ["https://apnews.com/article/1", "https://reuters.com/article/2"]
