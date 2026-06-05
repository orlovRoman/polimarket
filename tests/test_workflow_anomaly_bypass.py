import pytest
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from core.workflow import run_agent_evaluation, _analyzed_in_session
from core.models import Market

@pytest.mark.asyncio
async def test_workflow_dedup_bypass_on_anomaly():
    m = Market(
        id="test_m_anomaly",
        platform="polymarket",
        title="Will Bitcoin reach 100k?",
        description="Bitcoin reaching 100k",
        url="http://test",
        outcome="YES",
        price=0.5,
        close_time=datetime.now()
    )
    
    scout = MagicMock()
    scout.api_key = "test_key"
    scout.model = "gemini-2.5-flash"
    scout.estimate_market = MagicMock()
    
    swing = MagicMock()
    swing.api_key = "test_key"
    swing.model = "gemini-2.5-flash"
    swing.estimate_market = MagicMock()
    
    update_state = MagicMock()

    # Сценарий 1: Аномалии нет. Рынок уже есть в in-session кэше.
    # Должен сработать пропуск (return None, None, None).
    _analyzed_in_session.clear()
    dedup_key = f"{m.id}:scheduled"
    _analyzed_in_session[dedup_key] = time.monotonic()
    
    # Мокаем обнаружение аномалии: возвращаем has_anomaly = False
    mock_velocity_no_anomaly = MagicMock()
    mock_velocity_no_anomaly.has_anomaly = False
    mock_velocity_no_anomaly.annotation = "No anomaly"
    
    with patch("core.price_velocity.detect_velocity_anomaly", return_value=mock_velocity_no_anomaly):
        sig, swing_sig, ctx = await run_agent_evaluation(m, scout, swing, update_state)
        assert sig is None
        assert swing_sig is None
        assert ctx is None
        assert not scout.estimate_market.called

    # Сценарий 2: Аномалия есть. Рынок по-прежнему есть в in-session кэше.
    # Но из-за аномалии дедупликация должна быть пропущена, и оценка должна запуститься.
    mock_velocity_anomaly = MagicMock()
    mock_velocity_anomaly.has_anomaly = True
    mock_velocity_anomaly.annotation = "⚠️ ANOMALY!"
    mock_velocity_anomaly.suspicion = "PUMP"
    
    # Сбрасываем моки
    scout.estimate_market.reset_mock()
    swing.estimate_market.reset_mock()

    # Мокаем вызовы к внешним сервисам, чтобы тест прошел быстро и без реальных запросов
    def mock_get_memory(key, default=None):
        if key.startswith("last_analysis:"):
            return datetime.now(timezone.utc).isoformat()
        return default

    with patch("core.price_velocity.detect_velocity_anomaly", return_value=mock_velocity_anomaly), \
         patch("core.workflow.fetch_rss_news", return_value=["News"]), \
         patch("core.workflow.fetch_reddit_news", return_value=["Reddit"]), \
         patch("core.workflow._fetch_grounded_context", return_value="Grounded News"), \
         patch("core.workflow.fetch_google_trends", return_value=""), \
         patch("core.workflow.get_memory", side_effect=mock_get_memory), \
         patch("core.workflow.save_memory"):
        
        await run_agent_evaluation(m, scout, swing, update_state, price_history=[0.5, 0.6, 0.7])
        
        # Проверяем, что scout и swing были вызваны на оценку, несмотря на наличие в кэше
        assert scout.estimate_market.called, "Bypass failed: scout.estimate_market должен был вызваться из-за аномалии"
        assert swing.estimate_market.called, "Bypass failed: swing.estimate_market должен был вызваться из-за аномалии"
