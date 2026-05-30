from unittest.mock import MagicMock
from services.telegram_listener import restore_markdown_links

def _ent(offset, length, url):
    e = MagicMock(); e.offset = offset; e.length = length; e.url = url; return e

class TestRestoreMarkdownUrlWindow:

    def test_long_url_not_skipped(self):
        """Длинный URL (>80 символов) не должен пропускаться из-за малого окна."""
        long_url = "https://polymarket.com/event/" + "a" * 60  # 88 символов
        text = "Check this out"
        entities = [_ent(0, 5, long_url)]
        result = restore_markdown_links(text, entities)
        assert long_url in result, (
            f"Длинный URL не был вставлен — окно len(url)+10 слишком мало"
        )

    def test_url_already_adjacent_not_duplicated(self):
        """URL уже написан рядом с анкором — не дублируем."""
        url = "https://polymarket.com/event/btc"
        text = f"Click here {url}"
        # entity указывает на "Click here" (offset=0, length=10)
        entities = [_ent(0, 10, url)]
        result = restore_markdown_links(text, entities)
        assert result.count(url) == 1, (
            f"URL дублирован: count={result.count(url)}"
        )
