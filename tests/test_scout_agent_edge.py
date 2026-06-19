import asyncio
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from core.models import Market
from core.context import MarketContext
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent

def make_market(title, price, mid="test"):
    return Market(
        id=mid, platform="polymarket", title=title,
        url="https://polymarket.com/test", outcome="YES", price=price,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

@pytest.mark.asyncio
async def test_estimate_market_respects_min_edge():
    agent = ScoutAgent(api_key="fake-key")
    market = make_market("Some market", 0.50)
    ctx = MarketContext(market=market)

    # Мокаем generate_content_with_fallback, чтобы он возвращал фиксированный анализ
    mock_result = MagicMock()
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback", return_value=(mock_result, None)):
        with patch("agents.shared.utils.gemini_client.extract_response_text", return_value='{"estimate_probability": 0.6, "confidence": 0.8, "priority": "high", "reasoning": "test", "signal": "BUY", "cause": "test", "risk": "none", "oracle_risk": "low", "verdict": "yes"}'):
            with patch("agents.shared.python.db.get_memory", return_value="0.15"): # min_edge = 15%
                # Здесь edge будет 0.6 - 0.5 = 0.1 (10%), что ниже 0.15
                signal = await agent.estimate_market(ctx)
                assert signal is None
                
            with patch("agents.shared.python.db.get_memory", return_value="0.05"): # min_edge = 5%
                # Здесь edge 10% > 5%, значит сигнал должен быть создан
                signal = await agent.estimate_market(ctx)
                assert signal is not None
                assert signal.edge == pytest.approx(0.10)
