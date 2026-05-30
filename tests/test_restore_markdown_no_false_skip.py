from unittest.mock import MagicMock
from services.telegram_listener import restore_markdown_links

def _ent(offset, length, url):
    e = MagicMock()
    e.offset = offset
    e.length = length
    e.url = url
    return e

class TestRestoreMarkdownNoFalseSkip:

    def test_hidden_url_always_inserted(self):
        """Скрытая ссылка (не в тексте) всегда должна раскрываться."""
        text = "Buy this market now"    # URL не написан в тексте явно
        entities = [_ent(4, 4, "https://polymarket.com/event/btc-100k")]
        result = restore_markdown_links(text, entities)
        assert "polymarket.com/event/btc-100k" in result, (
            "Скрытая ссылка не была раскрыта — url_s in original_text_s дал false positive skip"
        )

    def test_raw_url_in_text_not_duplicated(self):
        """Если URL уже есть в тексте как сырой — entity дублирует его, seen_urls предотвращает."""
        url = "https://polymarket.com/event/btc-100k"
        text = f"Check {url}"
        entities = [_ent(6, len(url), url)]
        result = restore_markdown_links(text, entities)
        assert result.count(url) <= 2  # максимум оригинал + одна вставка (или seen_urls срабатывает)
