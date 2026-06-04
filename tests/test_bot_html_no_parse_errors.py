import pytest
from unittest.mock import patch, MagicMock
import sys
import os
from html.parser import HTMLParser

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from telegram.bot import get_active_scan_status_text

class StrictHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        
    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        
    def handle_endtag(self, tag):
        if not self.tags or self.tags[-1] != tag:
            raise ValueError(f"Unclosed or mismatched tag: </{tag}>. Opened tags: {self.tags}")
        self.tags.pop()

def test_scan_status_banner_no_html_errors():
    """Статус-баннер не содержит незакрытых HTML-тегов, спецсимволы экранированы."""
    with patch("telegram.bot.get_core_engine") as mock_engine_func:
         
        mock_engine = MagicMock()
        # Mock state with malicious characters
        mock_engine.state = {
            "category": "Science & Tech",
            "stage": "Reading <script>alert(1)</script>",
            "current_market_index": 1,
            "total_markets": 10,
            "current_market_title": "Will S&P 500 hit 6000? <wow>",
            "current_market_url": "http://test.url?a=1&b=2",
            "scout_status": "Error: <Response [429]> & Too many requests",
            "swing_status": "ok",
            "shadow_status": "ok",
            "ideas_found": 1
        }
        mock_engine_func.return_value = mock_engine
        
        status_text = get_active_scan_status_text()
        
        # Verify it can be parsed as HTML without errors
        parser = StrictHTMLParser()
        try:
            parser.feed(status_text)
            if parser.tags:
                pytest.fail(f"Unclosed tags remaining: {parser.tags}")
        except Exception as e:
            pytest.fail(f"HTML parsing failed: {e}")
            
        # Specific checks
        assert "&amp; Tech" in status_text
        assert "&lt;script&gt;" in status_text
        assert "&lt;Response [429]&gt;" in status_text
