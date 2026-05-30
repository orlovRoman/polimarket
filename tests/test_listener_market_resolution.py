import pytest
from unittest.mock import MagicMock, patch
from core.models import Market
from datetime import datetime, timezone

from services.telegram_listener import resolve_market_ids_from_url

def _make_test_market(market_id: str, title: str, price: float = 0.5) -> Market:
    return Market(
        id=market_id,
        platform="polymarket",
        title=title,
        description="",
        url=f"https://polymarket.com/event/{market_id}",
        outcome="YES",
        price=price,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

@pytest.fixture
def mock_adapter():
    with patch('services.telegram_listener.PolymarketAdapter') as mock_class:
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance
        yield mock_instance

def test_resolve_market_ids_prioritizes_by_exact_match(mock_adapter):
    """
    Тест проверяет, что при совпадении заголовка рынка с текстом поста
    этот рынок имеет наивысший приоритет и возвращается первым.
    """
    markets = [
        _make_test_market("real-madrid-id", "Will Real Madrid win the Champions League?"),
        _make_test_market("arsenal-id", "Will Arsenal win the Champions League?"),
        _make_test_market("bayern-id", "Will Bayern Munich win the Champions League?"),
    ]
    mock_adapter.get_event_by_slug.return_value = markets

    import asyncio
    # Пост про Арсенал
    post_text = "Big News! Will Arsenal win the Champions League? Buy Yes now!"
    result = asyncio.run(resolve_market_ids_from_url(
        "https://polymarket.com/event/uefa-champions-league-winner",
        text=post_text
    ))

    assert result[0] == "arsenal-id", f"Ожидался 'arsenal-id' на первом месте, получен {result[0]}"
    assert len(result) == 3

def test_resolve_market_ids_prioritizes_by_word_overlap(mock_adapter):
    """
    Если текста поста нет, приоритизация идет по пересечению слов.
    """
    import asyncio
    markets = [
        _make_test_market("real-madrid-id", "Will Real Madrid win the Champions League?"),
        _make_test_market("arsenal-id", "Will Arsenal win the Champions League?"),
    ]
    mock_adapter.get_event_by_slug.return_value = markets

    # Пост содержит упоминание Madrid
    post_text = "Loveliest stuff in Madrid tonight, champions are here!"
    result = asyncio.run(resolve_market_ids_from_url(
        "https://polymarket.com/event/uefa-champions-league-winner",
        text=post_text
    ))

    assert result[0] == "real-madrid-id", f"Ожидался 'real-madrid-id' на первом месте, получен {result[0]}"

def test_resolve_market_ids_no_text_returns_default_order(mock_adapter):
    """
    Если текст поста не передан, возвращается порядок по умолчанию.
    """
    import asyncio
    markets = [
        _make_test_market("real-madrid-id", "Will Real Madrid win the Champions League?"),
        _make_test_market("arsenal-id", "Will Arsenal win the Champions League?"),
    ]
    mock_adapter.get_event_by_slug.return_value = markets

    result = asyncio.run(resolve_market_ids_from_url(
        "https://polymarket.com/event/uefa-champions-league-winner"
    ))

    assert result == ["real-madrid-id", "arsenal-id"]
