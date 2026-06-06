import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from agents.shared.python.db import init_db, get_connection, add_blacklist_tag, remove_blacklist_tag, get_blacklist_tags
from core.models import Market
from agents.shared.python.market_selector import MarketSelector
from core.workflow import _prefilter_markets

@pytest.fixture(autouse=True)
def setup_db():
    """Изолируем состояние blacklist_tags для каждого теста."""
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM blacklist_tags")
    yield
    with get_connection() as conn:
        conn.execute("DELETE FROM blacklist_tags")


def test_db_methods():
    assert get_blacklist_tags() == []
    assert add_blacklist_tag("Tennis") is True
    assert get_blacklist_tags() == ["tennis"]
    assert add_blacklist_tag("tennis") is True
    assert get_blacklist_tags() == ["tennis"]  # дубль не добавляется
    assert remove_blacklist_tag("TENNIS") is True
    assert get_blacklist_tags() == []


def test_workflow_prefilter():
    add_blacklist_tag("nfl")
    future_close = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()

    markets = [
        {"id": "m1", "title": "Will Trump win?", "price": 0.5,
         "volume": 1_000_000, "tags": ["politics"], "close_time": future_close},
        {"id": "m2", "title": "Super Bowl winner", "price": 0.5,
         "volume": 1_000_000, "tags": ["NFL", "sports"], "close_time": future_close},
        {"id": "m3", "title": "Will SpaceX launch?", "price": 0.5,
         "volume": 1_000_000, "tags": ["science"], "close_time": future_close},
    ]

    # ✅ Мокаем конфиг чтобы тест не зависел от реальных порогов
    with patch("core.workflow.PRICE_RANGE_MIN", 0.1), \
         patch("core.workflow.PRICE_RANGE_MAX", 0.9), \
         patch("core.workflow.MIN_MARKET_VOLUME_USD", 100):

        filtered = _prefilter_markets(markets)
        filtered_ids = [m["id"] for m in filtered]

    assert "m1" in filtered_ids
    assert "m3" in filtered_ids
    assert "m2" not in filtered_ids, "NFL-рынок должен быть отфильтрован по blacklist"


def test_market_selector_filter():
    add_blacklist_tag("gaming")
    future_time = datetime.now(timezone.utc) + timedelta(hours=24)

    m1 = Market(
        id="m1", platform="polymarket", title="Dota 2 tournament",
        url="https://polymarket.com/event/gaming-dota-major",
        outcome="YES", price=0.5, close_time=future_time,
        event_slug="gaming-dota-major"
    )
    m2 = Market(
        id="m2", platform="polymarket", title="US election",
        url="https://polymarket.com/event/us-election",
        outcome="YES", price=0.5, close_time=future_time,
        event_slug="us-election"
    )

    # ✅ Изолируем от реальной БД cooldown и listed
    with patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=[]), \
         patch("agents.shared.python.market_selector.get_all_listed_market_ids",
               return_value={"ignored": set(), "watching": set()}), \
         patch("agents.shared.python.market_selector.get_last_analyzed_prices", return_value={}):

        selector = MarketSelector(None)

        filtered_normal = selector._filter([m1, m2], scan_category="arbitrage")
        ids_normal = [m.id for m in filtered_normal]
        assert "m2" in ids_normal
        assert "m1" not in ids_normal, "gaming-рынок должен быть отфильтрован"

        # penny_stocks — blacklist не применяется
        filtered_penny = selector._filter([m1, m2], scan_category="penny_stocks")
        ids_penny = [m.id for m in filtered_penny]
        assert "m1" in ids_penny
        assert "m2" in ids_penny
