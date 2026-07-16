import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from core.models import Market, CrossArbitrageSignal
from agents.polymarket_arbitrage_agent.src.agent import ArbitrageAgent

def make_market(title, price, platform="polymarket", mid="1"):
    return Market(
        id=mid, platform=platform, title=title,
        url=f"https://{platform}.com/test",
        outcome="YES", price=price,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

@pytest.fixture
def agent():
    return ArbitrageAgent(api_key="fake-key")

import json

def test_correlation_monotonicity_calls_llm(agent):
    a = make_market("SpaceX IPO above $3T", 0.90, platform="polymarket", mid="1")
    b = make_market("SpaceX IPO above $1.8T", 0.60, platform="kalshi", mid="2")
    llm_resp_str = json.dumps({
        "has_arbitrage": True,
        "arbitrage_type": "logical_contradiction",
        "spread_percent": 30.0,
        "reasoning": "LLM says yes",
        "trade_instruction": "BUY",
        "action_a": "BUY_NO",
        "action_b": "BUY_YES",
        "risk_level": "LOW",
        "expected_pnl_pct": 28.0
    })
    mock_result = {"candidates": [{"content": {"parts": [{"text": llm_resp_str}]}}]}
    with patch.object(agent, '_call_llm', return_value=(mock_result, "raw")) as mock_llm:
        result = agent.analyze_correlation(a, b, "threshold", 80)
    mock_llm.assert_called_once()
    assert result is not None
    assert result.has_arbitrage is True
    assert result.spread_percent == pytest.approx(30.0, abs=0.1)

def test_correlation_spacex_bug_filtered_by_math(agent):
    a = make_market("SpaceX IPO above $3T", 0.12, platform="polymarket", mid="1")
    b = make_market("SpaceX IPO above $1.8T", 0.84, platform="kalshi", mid="2")
    with patch.object(agent, '_call_llm') as mock_llm:
        result = agent.analyze_correlation(a, b, "threshold", 80)
    mock_llm.assert_not_called()
    assert result is None

# Тест analyze_cross_platform удален

def test_spread_is_from_math_not_llm(agent):
    a = make_market("Democrat wins election", 0.60, platform="polymarket", mid="1")
    b = make_market("Republican wins election", 0.70, platform="polymarket", mid="2")
    with patch.object(agent, '_call_llm') as mock_llm:
        result = agent.analyze_correlation(a, b, "complementary", 80)
    assert result.spread_percent == pytest.approx(30.0, abs=0.1)
    mock_llm.assert_not_called()

def test_create_math_arbitrage_signal_skip_both(agent):
    a = make_market("Test Market A", 0.50, platform="polymarket", mid="1")
    b = make_market("Test Market B", 0.50, platform="polymarket", mid="2")
    
    class MockMathFilterResult:
        arbitrage_type = "price_divergence"
        spread_pct = 0.0
        reasoning = "Same prices"
        trade_instruction = "None"
        
    mf = MockMathFilterResult()
    result = agent._create_math_arbitrage_signal(a, b, mf, match_score=1.0)
    
    assert result.has_arbitrage is False
    assert result.arbitrage_type == "none"
    assert result.action_a == "SKIP"
    assert result.action_b == "SKIP"
