import pytest
from unittest.mock import patch, MagicMock
from core.workflow import run_agent_evaluation
from core.checkpoint import get_checkpoint
from core.models import Market
from datetime import datetime, timezone

def get_fake_market(mid):
    return Market(
        id=mid,
        platform="polymarket",
        title="Test Market",
        url="http://test",
        outcome="YES",
        price=0.5,
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc)
    )

def test_scout_timeout_doesnt_block_pipeline():
    from core.guards import LLMUnavailableError
    mock_scout = MagicMock()
    mock_swing = MagicMock()
    
    # Эмулируем ошибку 429 / LLMUnavailableError
    mock_scout.estimate_market.side_effect = LLMUnavailableError("429 rate limit")
    mock_swing.estimate_market.return_value = MagicMock(recommendation="buy")
    
    m = get_fake_market("mkt-resilience-1")
    # Scout выбросит исключение и прервет run_agent_evaluation
    from core.workflow import _analyzed_in_session
    _analyzed_in_session.clear()
    
    with patch("core.workflow.get_memory", return_value=None), \
         patch("config.llm_health_gate.check_availability", return_value=True):
        with pytest.raises(LLMUnavailableError):
            run_agent_evaluation(
                m=m, scout=mock_scout, swing=mock_swing, update_state=lambda **k: None
            )
        
    cp = get_checkpoint(f"scout_{m.id}")
    assert cp["status"] == "llm_unavailable"

def test_scout_other_error_doesnt_block():
    mock_scout = MagicMock()
    mock_swing = MagicMock()
    
    # Обычное исключение - скаут падает, но свин работает
    mock_scout.estimate_market.side_effect = ValueError("Parse error")
    mock_swing.estimate_market.return_value = MagicMock(recommendation="buy")
    
    m = get_fake_market("mkt-resilience-2")
    
    from core.workflow import _analyzed_in_session
    _analyzed_in_session.clear()
    
    with patch("core.workflow.get_memory", return_value=None), \
         patch("config.llm_health_gate.check_availability", return_value=True):
        signal, swing, ctx = run_agent_evaluation(
            m=m, scout=mock_scout, swing=mock_swing, update_state=lambda **k: None
        )
    
    assert signal is None
    assert swing is not None
    
    cp_scout = get_checkpoint(f"scout_{m.id}")
    assert cp_scout["status"] == "error"
    
    cp_swing = get_checkpoint(f"swing_{m.id}")
    assert cp_swing["status"] == "ok"
