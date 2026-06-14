import asyncio
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


def make_market(close_time=None):
    m = MagicMock()
    m.id = "mkt-001"
    m.title = "Test Market"
    m.description = "Test Description"
    m.price = 0.14
    m.platform = "polymarket"
    m.close_time = close_time or datetime.now(tz=timezone.utc) + timedelta(days=30)
    return m


def make_context(market, news=None):
    ctx = MagicMock()
    ctx.market = market
    ctx.news_titles = news or []
    ctx.reddit_posts = []
    ctx.wiki_context = "Some Wikipedia article"
    ctx.trends_data = "0"
    ctx.hn_posts = []
    ctx.search_query = "test"
    ctx.grounded_context = "Grounding не выполнен."
    return ctx


@pytest.mark.asyncio
async def test_hard_block_low_asymmetry():
    """Тест HARD_BLOCK: асимметрия < 0.55 должна блокировать сделку (ignore) с детальным вердиктом."""
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market()
    ctx = make_context(market)
    agent = SwingAgent(api_key="test")

    # LLM возвращает buy, но асимметрия низкая
    llm_payload = {
        "target_outcome": "YES",
        "target_exit_price": 0.40,
        "reasoning": "Тестовое обоснование",
        "catalyst": "Тестовый катализатор",
        "catalyst_absence_reason": "—",
        "swing_risk": "Тестовый риск",
        "swing_verdict": "",
        "llm_confidence": 0.80,
        "llm_direction": "YES",
        "contrarian_case": "Тестовый контр-кейс",
        "asymmetry_score": 0.50, # < 0.55 -> HARD_BLOCK
        "has_hard_facts": False
    }

    mock_result = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(llm_payload)}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 0}
    }

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               return_value=(mock_result, "gemini-2.5-flash")), \
         patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""), \
         patch("agents.shared.utils.rag.get_rag_context", return_value=""), \
         patch("agents.shared.utils.prompt_guards.guard_news_with_age", return_value=""), \
         patch("agents.polymarket_swing_agent.src.agent.save_agent_episode") as mock_save_episode, \
         patch("agents.shared.utils.language_guard.validate_russian_fields", return_value=None):
        
        signal = await agent.estimate_market(ctx, price_history=[])
        
        assert signal is not None
        assert signal.recommendation == "ignore"
        assert "Асимметрия 0.50 < 0.55" in signal.swing_verdict
        # Должен быть полный вердикт с анализом
        assert "Анализ (не использован для входа)" in signal.swing_verdict
        assert "Тезис: Тестовый катализатор" in signal.swing_verdict
        assert "Reasoning: Тестовое обоснование" in signal.swing_verdict
        
        # Проверяем, что причины были переданы в save_agent_episode
        mock_save_episode.assert_called_once()
        call_ctx = mock_save_episode.call_args[1]["context"]
        assert "Асимметрия 0.50 < 0.55" in " | ".join(call_ctx["rejection_reasons"])


@pytest.mark.asyncio
async def test_soft_warn_low_confidence():
    """Тест SOFT_WARN: низкий confidence не блокирует сделку, а переводит в buy с пометкой WARNING."""
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market()
    ctx = make_context(market)
    agent = SwingAgent(api_key="test")

    # LLM возвращает buy, но confidence низкий для горизонта >24ч (min_confidence обычно 0.65)
    # При llm_confidence = 0.85, final = 0.5525 (которое >= 0.52 -> buy), но 0.5525 < 0.65 (min_confidence)
    llm_payload = {
        "target_outcome": "YES",
        "target_exit_price": 0.60,
        "reasoning": "Тестовое обоснование",
        "catalyst": "Тестовый катализатор",
        "catalyst_absence_reason": "—",
        "swing_risk": "Тестовый риск",
        "swing_verdict": "",
        "llm_confidence": 0.85, 
        "llm_direction": "YES",
        "contrarian_case": "Тестовый контр-кейс",
        "asymmetry_score": 0.75,
        "has_hard_facts": False
    }

    mock_result = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(llm_payload)}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 0}
    }

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               return_value=(mock_result, "gemini-2.5-flash")), \
         patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""), \
         patch("agents.shared.utils.rag.get_rag_context", return_value=""), \
         patch("agents.shared.utils.prompt_guards.guard_news_with_age", return_value=""), \
         patch("agents.polymarket_swing_agent.src.agent.save_agent_episode") as mock_save_episode, \
         patch("agents.shared.utils.language_guard.validate_russian_fields", return_value=None):
        
        signal = await agent.estimate_market(ctx, price_history=[])
        
        assert signal is not None
        assert signal.recommendation == "buy"
        assert "⚠️ ПРЕДУПРЕЖДЕНИЯ" in signal.swing_verdict
        assert "низкий confidence" in signal.swing_verdict
        assert "ВХОДИТЬ (с предупреждением)" in signal.swing_verdict

        # Проверяем warnings в save_agent_episode
        mock_save_episode.assert_called_once()
        call_ctx = mock_save_episode.call_args[1]["context"]
        assert any("низкий confidence" in w for w in call_ctx["warnings"])


@pytest.mark.asyncio
async def test_dummy_catalyst_when_no_data():
    """Тест DummyCatalystCheck: если grounded_context и news_block пусты, штрафа нет."""
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    # Рынок закроется через 48 часов (MEDIUM горизонт, min_confidence = 0.52)
    market = make_market(close_time=datetime.now(tz=timezone.utc) + timedelta(hours=48))
    ctx = make_context(market)
    ctx.grounded_context = "Grounding не выполнен."
    # news_block в тесте передается пустым
    
    agent = SwingAgent(api_key="test")

    # При llm_confidence=0.85, final = 0.553, что выше 0.52, поэтому предупреждения о низком confidence не будет
    llm_payload = {
        "target_outcome": "YES",
        "target_exit_price": 0.60,
        "reasoning": "Тестовое обоснование",
        "catalyst": "Тестовый катализатор",
        "catalyst_absence_reason": "—",
        "swing_risk": "Тестовый риск",
        "swing_verdict": "",
        "llm_confidence": 0.85,
        "llm_direction": "YES",
        "contrarian_case": "Тестовый контр-кейс",
        "asymmetry_score": 0.75,
        "has_hard_facts": False
    }

    mock_result = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(llm_payload)}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 0}
    }

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               return_value=(mock_result, "gemini-2.5-flash")), \
         patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""), \
         patch("agents.shared.utils.rag.get_rag_context", return_value=""), \
         patch("agents.shared.utils.prompt_guards.guard_news_with_age", return_value=""), \
         patch("agents.polymarket_swing_agent.src.agent.save_agent_episode"), \
         patch("agents.shared.utils.language_guard.validate_russian_fields", return_value=None), \
         patch("agents.shared.utils.catalyst_verifier.verify_catalyst") as mock_verify_catalyst:
        
        signal = await agent.estimate_market(ctx, price_history=[])
        
        assert signal is not None
        # Проверяем, что verify_catalyst не вызывался, т.к. использован DummyCatalystCheck
        mock_verify_catalyst.assert_not_called()
        assert signal.recommendation == "buy"
        assert "⚠️ ПРЕДУПРЕЖДЕНИЯ" not in signal.swing_verdict
