import pytest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError
from core.context import MarketContext
from core.models import Market

def _market():
    return Market(
        id="test-1", platform="polymarket", title="Test market",
        description="", url="http://x", outcome="YES", price=0.5,
        close_time=datetime.now(timezone.utc) + timedelta(days=10)
    )

@pytest.mark.parametrize("trigger", ["scheduled", "event_driven", "manual"])
def test_trigger_type_all_valid(trigger):
    """Все три trigger_type должны проходить валидацию Pydantic."""
    ctx = MarketContext(market=_market(), trigger_type=trigger)
    assert ctx.trigger_type == trigger

def test_trigger_type_invalid_raises():
    """Неизвестный trigger_type выбрасывает ValidationError."""
    with pytest.raises(ValidationError):
        MarketContext(market=_market(), trigger_type="unknown_trigger")

def test_trigger_type_default_is_scheduled():
    ctx = MarketContext(market=_market())
    assert ctx.trigger_type == "scheduled"
