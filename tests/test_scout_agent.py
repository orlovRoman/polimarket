import asyncio
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from core.models import Market
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent

def make_market(title, price, mid="test"):
    return Market(
        id=mid, platform="polymarket", title=title,
        url="https://polymarket.com/test", outcome="YES", price=price,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

def test_edge_calculated_in_python():
    import inspect
    from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
    source = inspect.getsource(ScoutAgent.estimate_market)
    assert "edge_yes = est_prob - market.price" in source, \
        "edge должен считаться в Python, а не LLM"

def test_prompt_no_manual_math_instruction():
    import inspect
    from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
    source = inspect.getsource(ScoutAgent.estimate_market)
    assert "вычисления математического арбитража" not in source, \
        "Инструкция ручного вычисления должна быть удалена из промпта"

def test_prompt_contains_math_filter_block():
    from core.context import MarketContext
    agent = ScoutAgent(api_key="fake-key")
    market = make_market("Some prediction market", 0.50)

    mock_related = make_market("SpaceX IPO above $3T", 0.90, mid="related")

    mock_corr = [{
        "market_id_a": "test", "market_id_b": "related",
        "title_a": "Some prediction market", "title_b": "SpaceX IPO above $3T",
        "correlation_type": "thematic", "description": "test correlation"
    }]

    captured_prompt = []

    def mock_generate(api_key, payload, **kwargs):
        captured_prompt.append(payload["contents"][0]["parts"][0]["text"])
        return None, None
        
    class MockAdapter:
        def get_market(self, mid):
            return mock_related
            
    agent._adapter = MockAdapter()

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               side_effect=mock_generate):
        with patch("agents.shared.python.db.get_market_correlations",
                   return_value=mock_corr):
            with patch("agents.shared.python.db.get_memory", return_value=None):
                with patch("agents.shared.python.db.get_agent_episodes",
                           return_value=[]):
                    with patch("agents.shared.python.db.get_performance_summary",
                               return_value=""):
                        ctx = MarketContext(market=market)
                        asyncio.run(agent.estimate_market(ctx))

    if captured_prompt:
        assert any("MATH-FILTER" in p for p in captured_prompt), \
            "Ни один из промптов не содержит результатов math_pre_filter для корреляций"
