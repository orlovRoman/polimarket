import pytest
from core.market_scorer import score_market, screen_markets_code
from agents.orchestrator.src.agent import NexusAgent

def test_high_uncertainty_scores_higher():
    m_05 = {"id": "a", "p": 0.50, "vol": 10000, "end": "2026-06-20T00:00:00Z"}
    m_09 = {"id": "b", "p": 0.95, "vol": 10000, "end": "2026-06-20T00:00:00Z"}
    assert score_market(m_05) > score_market(m_09)

def test_high_volume_scores_higher():
    m_big  = {"id": "a", "p": 0.5, "vol": 500000, "end": "2026-06-20T00:00:00Z"}
    m_small = {"id": "b", "p": 0.5, "vol": 1000,  "end": "2026-06-20T00:00:00Z"}
    assert score_market(m_big) > score_market(m_small)

def test_screen_returns_top_n():
    markets = [{"id": str(i), "p": 0.5, "vol": i*1000, "end": "2026-06-20T00:00:00Z"} 
               for i in range(100)]
    result = screen_markets_code(markets, top_n=10)
    assert len(result) == 10

def test_no_llm_call(monkeypatch):
    """Убеждаемся, что LLM не вызывается."""
    monkeypatch.setattr("agents.orchestrator.src.agent.NexusAgent.screen_markets", 
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("LLM was called")))
    result = screen_markets_code([{"id":"x","p":0.5,"vol":5000,"end":"2026-07-01T00:00:00Z"}])
    assert result == ["x"]
