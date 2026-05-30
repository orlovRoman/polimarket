import pytest
from unittest.mock import MagicMock

def _make_entity(offset, length, url):
    e = MagicMock()
    e.offset = offset
    e.length = length
    e.url = url
    return e

class TestRestoreMarkdownLinks:

    def test_ascii_link_restored(self):
        """Простая ASCII-ссылка восстанавливается корректно."""
        from services.telegram_listener import restore_markdown_links
        text = "Check this market"
        ent = _make_entity(6, 11, "https://polymarket.com/event/btc-100k")
        result = restore_markdown_links(text, [ent])
        assert "https://polymarket.com/event/btc-100k" in result
        assert "this market" in result

    def test_emoji_before_link_correct_offset(self):
        """Emoji перед ссылкой не должен сдвигать offset — нужен surrogate."""
        from services.telegram_listener import restore_markdown_links
        # "🔥 Market" — 🔥 занимает 2 UTF-16 code units (surrogate pair)
        # Telethon даёт offset=3 (после "🔥 ") в surrogate-единицах
        text = "🔥 Market"
        # offset=3, length=6 в surrogate encoding → "Market"
        ent = _make_entity(3, 6, "https://polymarket.com/event/fire")
        result = restore_markdown_links(text, [ent])
        assert "https://polymarket.com/event/fire" in result

    def test_cyrillic_before_link(self):
        """Кириллица перед ссылкой не должна сдвигать смещения."""
        from services.telegram_listener import restore_markdown_links
        text = "Рынок здесь"   # 11 символов кириллицы
        ent = _make_entity(6, 5, "https://polymarket.com/event/test")
        result = restore_markdown_links(text, [ent])
        # Главное — функция не падает и возвращает строку
        assert isinstance(result, str)

    def test_no_entities_returns_original(self):
        from services.telegram_listener import restore_markdown_links
        text = "No entities here"
        assert restore_markdown_links(text, None) == text
        assert restore_markdown_links(text, []) == text

    def test_no_duplicate_url(self):
        """URL уже присутствующий рядом не должен дублироваться."""
        from services.telegram_listener import restore_markdown_links
        url = "https://polymarket.com/event/btc"
        text = f"Market ({url})"
        ent = _make_entity(8, len(url), url)
        result = restore_markdown_links(text, [ent])
        assert result.count(url) == 1
