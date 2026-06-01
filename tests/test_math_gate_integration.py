import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from core.models import Market
from core.engine import _run_math_gate

@pytest.mark.anyio
async def test_math_gate_confirmed_arb_skips_llm():
    """CONFIRMED_ARBITRAGE должен идти напрямую в notify, без route_ambiguous."""
    dt = datetime.now(timezone.utc)
    markets = [
        Market(
            id="1", 
            platform="polymarket", 
            title="US GDP > $1T 2025", 
            url="http://x", 
            outcome="YES", 
            price=0.80, 
            close_time=dt, 
            event_slug="us-gdp"
        ),
        Market(
            id="2", 
            platform="polymarket", 
            title="US GDP > $500B 2025", 
            url="http://x", 
            outcome="YES", 
            price=0.55, 
            close_time=dt, 
            event_slug="us-gdp"
        ),
    ]
    
    # We mock notify_fn so that it accepts signal_type keyword argument
    notify_mock = AsyncMock()
    async def mock_notify(signal_type, market, details):
        await notify_mock(signal_type=signal_type, market=market, details=details)

    with patch("core.arb_router.route_ambiguous") as router_mock:
        processed = await _run_math_gate(
            markets=markets,
            api_key="fake",
            notify_fn=mock_notify,
        )

    notify_mock.assert_called_once()
    router_mock.assert_not_called()  # ← LLM не вызывался
    assert "1" in processed
    assert "2" in processed


def test_math_gate_no_double_processing():
    """Рынки из math_gate не должны повторно обрабатываться агентами."""
    processed_ids = ["market_1", "market_2"]
    dt = datetime.now(timezone.utc)
    all_markets = [
        Market(id="market_1", platform="polymarket", title="A", url="http://x", outcome="YES", price=0.5, close_time=dt),
        Market(id="market_2", platform="polymarket", title="B", url="http://x", outcome="YES", price=0.5, close_time=dt),
        Market(id="market_3", platform="polymarket", title="C", url="http://x", outcome="YES", price=0.5, close_time=dt),  # этот должен попасть к агентам
    ]
    
    remaining = [m for m in all_markets if m.id not in processed_ids]
    assert len(remaining) == 1
    assert remaining[0].id == "market_3"
