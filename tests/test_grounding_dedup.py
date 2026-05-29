import pytest
from unittest.mock import MagicMock, patch
from core.models import Market
from core.context import MarketContext
from core.workflow import _fetch_grounded_context, run_agent_evaluation
from datetime import datetime

def _make_market():
    return Market(
        id="test-mkt-123",
        platform="polymarket",
        title="Test Grounding Title",
        description="description",
        url="https://polymarket.com/event/test",
        outcome="YES",
        price=0.5,
        close_time=datetime.now()
    )

def test_grounding_called_once_per_market():
    """LLM вызывается ровно 1 раз для grounding на рынок."""
    import core.workflow
    core.workflow._analyzed_in_session.clear()
    
    m = _make_market()
    
    # Мокаем generate_content_with_fallback
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback") as mock_llm, \
         patch("core.workflow.get_memory", return_value=None), \
         patch("core.workflow.save_memory"), \
         patch("core.workflow.fetch_rss_news", return_value=[]), \
         patch("core.workflow.fetch_reddit_news", return_value=[]), \
         patch("agents.shared.utils.web_search.fetch_wikipedia_context", return_value=[]), \
         patch("core.workflow.fetch_hackernews", return_value=[]), \
         patch("core.workflow.fetch_google_trends", return_value="Trends: 0"), \
         patch("core.workflow.get_market_correlations", return_value=[]), \
         patch("config.llm_health_gate") as mock_gate, \
         patch("core.workflow.build_search_query", return_value="Test Grounding Title"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("agents.polymarket_mispricing_agent.src.agent.ScoutAgent.estimate_market") as mock_scout, \
         patch("agents.polymarket_swing_agent.src.agent.SwingAgent.estimate_market") as mock_swing:
             
        mock_gate.check_availability.return_value = True
        mock_llm.return_value = ({"candidates": [{"content": {"parts": [{"text": "Search summary results"}]}}]}, "model-a")
        
        scout_agent = MagicMock()
        scout_agent.api_key = "fake-key"
        scout_agent.model = "gemini-2.5-flash"
        
        swing_agent = MagicMock()
        swing_agent.api_key = "fake-key"
        swing_agent.model = "gemini-2.5-flash"
        
        update_state = MagicMock()
        
        run_agent_evaluation(m, scout_agent, swing_agent, update_state)
        
        # Считаем вызовы с agent_name='GROUNDING' — должно быть ровно 1
        grounding_calls = [
            c for c in mock_llm.call_args_list 
            if c.kwargs.get('agent_name') == 'GROUNDING'
        ]
        assert len(grounding_calls) == 1
