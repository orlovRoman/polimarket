import pytest
from unittest.mock import AsyncMock, patch
from agents.shared.utils.resolution_extractor import scrape_url_text, get_resolution_source
from core.models import Market

@pytest.mark.asyncio
async def test_scrape_url_text_success():
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = """
        <html>
            <head><title>Test Oracle</title></head>
            <body>
                <style>body { color: black; }</style>
                <div class="content">
                    <h1>Resolution Report</h1>
                    <p>The final value of Micron adjusted gross margin is 73.8%.</p>
                    <script>console.log("hello");</script>
                </div>
            </body>
        </html>
        """
        mock_get.return_value = mock_response

        text = await scrape_url_text("https://example.com/oracle-report")
        assert text is not None
        assert "Resolution Report" in text
        assert "The final value of Micron adjusted gross margin is 73.8%" in text
        assert "console.log" not in text
        assert "color: black" not in text

@pytest.mark.asyncio
async def test_scrape_url_text_failure():
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = AsyncMock()
        mock_response.status_code = 403
        mock_get.return_value = mock_response

        text = await scrape_url_text("https://example.com/blocked")
        assert text is None

@pytest.mark.asyncio
async def test_scrape_url_text_unsupported_content_type():
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/pdf"}
        mock_response.text = "%PDF-1.4 binary garbage..."
        mock_get.return_value = mock_response

        text = await scrape_url_text("https://example.com/document.pdf")
        assert text is None

@pytest.mark.asyncio
async def test_get_resolution_source():
    api_key = "fake_key"
    market_desc = "This market resolves based on the official data published here: https://example.com/uma-micron-report"
    market_title = "Will Micron Q3 adjusted gross margin be below 75%?"

    res = await get_resolution_source(market_desc, market_title, api_key)
    assert res is not None
    assert res.raw_url == "https://example.com/uma-micron-report"
    assert res.domain == "example.com"
