"""Unit-тесты для логики фильтрации рынков."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from core.models import Market
from agents.shared.python.market_selector import MarketSelector


def make_market(price=0.5, days_to_close=7, market_id="test-1"):
    return Market(
        id=market_id,
        platform="polymarket",
        title="Test Market",
        description="desc",
        url="https://polymarket.com/test",
        outcome="YES",
        price=price,
        close_time=datetime.now(timezone.utc) + timedelta(days=days_to_close)
    )


def test_filter_removes_expired_markets():
    """Рынки с close_time в прошлом должны быть отфильтрованы."""
    adapter = MagicMock()
    selector = MarketSelector(adapter)
    
    expired = make_market(days_to_close=-1)
    active = make_market(days_to_close=7)
    
    with patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=set()), \
         patch("agents.shared.python.market_selector.get_all_listed_market_ids", return_value={'ignored': set(), 'watching': set()}):
        result = selector._filter([expired, active])
    
    assert len(result) == 1
    assert result[0].id == active.id


def test_filter_removes_cooldown_markets_regardless_of_price_change():
    """Рынки на кулдауне должны быть отфильтрованы всегда, даже при изменении цены."""
    adapter = MagicMock()
    selector = MarketSelector(adapter)
    
    market = make_market(price=0.55)
    
    with patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value={market.id}), \
         patch("agents.shared.python.market_selector.get_last_analyzed_prices", return_value={market.id: 0.50}), \
         patch("agents.shared.python.market_selector.get_all_listed_market_ids", return_value={'ignored': set(), 'watching': set()}):
        result = selector._filter([market])
    
    assert len(result) == 0


def test_filter_removes_dead_prices():
    """Рынки с ценой < 0.01 или > 0.99 должны быть отфильтрованы."""
    adapter = MagicMock()
    selector = MarketSelector(adapter)
    
    sure_yes = make_market(price=0.995)
    sure_no = make_market(price=0.003, market_id="test-2")
    active = make_market(price=0.5, market_id="test-3")
    
    with patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=set()), \
         patch("agents.shared.python.market_selector.get_all_listed_market_ids", return_value={'ignored': set(), 'watching': set()}):
        result = selector._filter([sure_yes, sure_no, active])
    
    assert len(result) == 1
    assert result[0].id == "test-3"


def test_score_market_favors_uncertainty():
    """Рынки в зоне максимальной неопределённости должны иметь высший скор."""
    adapter = MagicMock()
    selector = MarketSelector(adapter)
    
    uncertain = make_market(price=0.50)
    biased = make_market(price=0.80)
    
    assert selector._score_market(uncertain) >= selector._score_market(biased)


def test_prefilter_keeps_markets_in_price_range():
    from core.workflow import _prefilter_markets
    from unittest.mock import patch

    ok = {"id": "ok", "price": 0.5, "volume": 1000.0, "close_time": "2099-01-01T00:00:00Z"}
    low_price = {"id": "low", "price": 0.01, "volume": 1000.0, "close_time": "2099-01-01T00:00:00Z"}
    closing_soon = {"id": "closing", "price": 0.5, "volume": 1000.0, "close_time": "2024-01-01T00:00:00Z"}

    with patch("core.workflow.PRICE_RANGE_MIN", 0.05), \
         patch("core.workflow.PRICE_RANGE_MAX", 0.95), \
         patch("core.workflow.MIN_MARKET_VOLUME_USD", 500.0):
        result = _prefilter_markets([ok, low_price, closing_soon])

    assert len(result) == 1
    assert result[0]["id"] == "ok"


def test_prefilter_handles_close_time_parse_error():
    """Рынки с невалидным close_time не должны вызывать исключение."""
    from core.workflow import _prefilter_markets
    from unittest.mock import patch

    market = {"id": "bad-date", "price": 0.5, "volume": 1000.0, "close_time": "not-a-date"}

    with patch("core.workflow.PRICE_RANGE_MIN", 0.05), \
         patch("core.workflow.PRICE_RANGE_MAX", 0.95), \
         patch("core.workflow.MIN_MARKET_VOLUME_USD", 500.0):
        result = _prefilter_markets([market])

    assert result[0]


def test_prefilter_excludes_zero_volume():
    from core.workflow import _prefilter_markets
    from unittest.mock import patch

    markets = [
        {"id": "a", "price": 0.5, "volume": None, "vol": None, "close_time": "2099-01-01T00:00:00Z"},
        {"id": "b", "price": 0.5, "volume": 5000.0, "close_time": "2099-01-01T00:00:00Z"},
    ]
    with patch("core.workflow.PRICE_RANGE_MIN", 0.05), \
         patch("core.workflow.PRICE_RANGE_MAX", 0.95), \
         patch("core.workflow.MIN_MARKET_VOLUME_USD", 100.0):
        result = _prefilter_markets(markets)
    ids = [m["id"] for m in result]
    assert "a" not in ids, "Рынок с volume=None должен быть отфильтрован"
    assert "b" in ids
