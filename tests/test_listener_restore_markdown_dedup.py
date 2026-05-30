import pytest
from unittest.mock import MagicMock

def _ent(offset, length, url):
    e = MagicMock()
    e.offset = offset
    e.length = length
    e.url = url
    return e

class TestRestoreMarkdownDedup:

    def test_two_different_urls_both_inserted(self):
        """Два разных URL в одном сообщении — оба вставляются."""
        from services.telegram_listener import restore_markdown_links
        text = "MarketA MarketB"
        entities = [
            _ent(0, 7,  "https://polymarket.com/event/market-a"),
            _ent(8, 7,  "https://polymarket.com/event/market-b"),
        ]
        result = restore_markdown_links(text, entities)
        assert "polymarket.com/event/market-a" in result
        assert "polymarket.com/event/market-b" in result

    def test_same_url_not_duplicated(self):
        """Один и тот же URL в двух entities — вставляется только один раз."""
        from services.telegram_listener import restore_markdown_links
        url = "https://polymarket.com/event/btc-100k"
        text = "Buy here and here"
        entities = [
            _ent(0, 8,  url),
            _ent(9, 8,  url),
        ]
        result = restore_markdown_links(text, entities)
        assert result.count(url) == 1, (
            f"URL вставлен {result.count(url)} раз — должен быть ровно 1"
        )

    def test_entities_with_emoji_prefix(self):
        """Emoji перед entity — surrogate encoding не ломает offset."""
        from services.telegram_listener import restore_markdown_links
        # "🔥 Go" — emoji занимает 2 UTF-16 code units
        # surrogate offset=3 → "Go"
        text = "🔥 Go"
        entities = [_ent(3, 2, "https://polymarket.com/event/test")]
        result = restore_markdown_links(text, entities)
        assert isinstance(result, str)
        assert "polymarket.com/event/test" in result
