import pytest
from datetime import datetime, timezone, timedelta
from agents.shared.python.db import init_db, get_connection, add_blacklist_tag, remove_blacklist_tag, get_blacklist_tags
from core.models import Market
from agents.shared.python.market_selector import MarketSelector
from core.workflow import _prefilter_markets

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM blacklist_tags")

def test_db_methods():
    # 1. Сначала черный список пуст
    assert get_blacklist_tags() == []
    
    # 2. Добавляем тег
    assert add_blacklist_tag("Tennis") is True
    # Проверяем, что добавился в нижнем регистре
    assert get_blacklist_tags() == ["tennis"]
    
    # Добавляем повторно
    assert add_blacklist_tag("tennis") is True
    assert get_blacklist_tags() == ["tennis"]
    
    # 3. Удаляем тег
    assert remove_blacklist_tag("TENNIS") is True
    assert get_blacklist_tags() == []

def test_workflow_prefilter():
    add_blacklist_tag("nfl")
    
    # Рынки с ценой в диапазоне PRICE_RANGE_MIN..PRICE_RANGE_MAX и volume >= MIN_MARKET_VOLUME_USD
    # и датой закрытия >= 3 дня.
    future_close = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    
    markets = [
        {
            "id": "m1",
            "title": "Will Trump win?",
            "price": 0.5,
            "volume": 1000000,
            "tags": ["politics"],
            "close_time": future_close
        },
        {
            "id": "m2",
            "title": "Super Bowl winner",
            "price": 0.5,
            "volume": 1000000,
            "tags": ["NFL", "sports"],
            "close_time": future_close
        },
        {
            "id": "m3",
            "title": "Will SpaceX launch?",
            "price": 0.5,
            "volume": 1000000,
            "tags": ["science"],
            "close_time": future_close
        }
    ]
    
    filtered = _prefilter_markets(markets)
    filtered_ids = [m["id"] for m in filtered]
    assert "m1" in filtered_ids
    assert "m3" in filtered_ids
    assert "m2" not in filtered_ids

def test_market_selector_filter():
    add_blacklist_tag("gaming")
    
    future_time = datetime.now(timezone.utc) + timedelta(hours=24)
    m1 = Market(
        id="m1",
        platform="polymarket",
        title="Dota 2 tournament",
        url="https://polymarket.com/event/gaming-dota-major",
        outcome="YES",
        price=0.5,
        close_time=future_time,
        event_slug="gaming-dota-major"
    )
    m2 = Market(
        id="m2",
        platform="polymarket",
        title="US election",
        url="https://polymarket.com/event/us-election",
        outcome="YES",
        price=0.5,
        close_time=future_time,
        event_slug="us-election"
    )
    
    selector = MarketSelector(None)
    
    # Обычный скан (не penny_stocks) — m1 (содержит "gaming" в event_slug) должен отсеяться
    filtered_normal = selector._filter([m1, m2], scan_category="arbitrage")
    ids_normal = [m.id for m in filtered_normal]
    assert "m2" in ids_normal
    assert "m1" not in ids_normal
    
    # Penny stocks скан — m1 не должен отсеяться
    filtered_penny = selector._filter([m1, m2], scan_category="penny_stocks")
    ids_penny = [m.id for m in filtered_penny]
    assert "m2" in ids_penny
    assert "m1" in ids_penny
