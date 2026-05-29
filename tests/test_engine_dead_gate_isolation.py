import pytest
import time
from unittest.mock import MagicMock, patch
from core.guards import LLMUnavailableError, LLMHealthGate

def test_dead_gate_in_workflow_raises_not_silently_skips():
    """
    При DEAD gate run_agent_evaluation должен бросать LLMUnavailableError,
    а НЕ молча возвращать (None, None, None) — чтобы engine мог поймать
    и уведомить пользователя.
    """
    from core.guards import LLMHealthGate, LLMUnavailableError
    from core.workflow import _analyzed_in_session
    
    # Очищаем сессионный кэш перед тестом
    _analyzed_in_session.clear()

    dead_gate = LLMHealthGate()
    dead_gate._force_dead()

    with patch("config.llm_health_gate", dead_gate):
        from core.workflow import run_agent_evaluation
        from unittest.mock import MagicMock
        from datetime import datetime, timezone, timedelta
        from core.models import Market

        # Используем уникальный ID, чтобы гарантированно обойти БД-дедупликацию
        unique_id = f"test_{time.time()}_{int(time.monotonic() * 1000)}"
        m = Market(
            id=unique_id, platform="polymarket", title="Test unique title",
            description="", url="http://x", outcome="YES", price=0.5,
            close_time=datetime.now(timezone.utc) + timedelta(days=10)
        )
        scout = MagicMock(); swing = MagicMock()
        update_state = MagicMock()

        with pytest.raises(LLMUnavailableError):
            run_agent_evaluation(m, scout, swing, update_state)
