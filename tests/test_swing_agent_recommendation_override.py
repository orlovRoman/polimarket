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
async def test_hard_block_roi_sets_rejection_reason():
    """HARD_BLOCK ROI → rejection_reason содержит 'ROI', не 'Низкий потенциал хайпа'"""
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market()
    # Установим цену входа 0.19 (пройдет по is_cheap < 0.20)
    market.price = 0.19
    ctx = make_context(market)
    agent = SwingAgent(api_key="test")

    # Установим target_exit_price = 0.20, чтобы ROI-фильтр не прошел (ROI 5.3% < 40%)
    llm_payload = {
        "target_outcome": "YES",
        "target_exit_price": 0.20,
        "reasoning": "Тестовое обоснование",
        "catalyst": "Тестовый катализатор",
        "catalyst_absence_reason": "—",
        "swing_risk": "Тестовый риск",
        "swing_verdict": "",
        "llm_confidence": 0.80,
        "llm_direction": "YES",
        "contrarian_case": "Тестовый контр-кейс",
        "asymmetry_score": 0.80,
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
        mock_save_episode.assert_called_once()
        episode_ctx = mock_save_episode.call_args[1]["context"]
        assert any("ROI" in r for r in episode_ctx["rejection_reasons"])
        assert episode_ctx["rejection_reasons"][0] in signal.summary

@pytest.mark.asyncio
async def test_hard_block_asymmetry_sets_rejection_reason():
    """HARD_BLOCK asymmetry=0.48 → rejection_reason содержит 'Асимметрия'"""
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market()
    ctx = make_context(market)
    agent = SwingAgent(api_key="test")

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
        "asymmetry_score": 0.48,  # < 0.55 -> HARD_BLOCK
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
        mock_save_episode.assert_called_once()
        episode_ctx = mock_save_episode.call_args[1]["context"]
        assert any("Асимметрия" in r for r in episode_ctx["rejection_reasons"])

@pytest.mark.asyncio
async def test_recommendation_not_overwritten_after_hard_block():
    """Регрессионный: asymmetry=0.30 → HARD_BLOCK → signal.recommendation == 'ignore' (не 'buy')"""
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market()
    ctx = make_context(market)
    agent = SwingAgent(api_key="test")

    # Передаем в JSON recommendation: "buy" для имитации перезаписи
    llm_payload = {
        "recommendation": "buy",
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
        "asymmetry_score": 0.30,  # < 0.55 -> HARD_BLOCK
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
         patch("agents.shared.utils.language_guard.validate_russian_fields", return_value=None):
        
        signal = await agent.estimate_market(ctx, price_history=[])
        
        assert signal is not None
        assert signal.recommendation == "ignore"
        assert signal.summary.startswith("💤")

@pytest.mark.asyncio
async def test_warnings_passed_to_episode_context():
    """Поля warnings/rejection_reasons/hype_score присутствуют в save_agent_episode context"""
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market()
    ctx = make_context(market)
    agent = SwingAgent(api_key="test")

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
        "asymmetry_score": 0.80,
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
        mock_save_episode.assert_called_once()
        episode_ctx = mock_save_episode.call_args[1]["context"]
        assert "warnings" in episode_ctx
        assert "rejection_reasons" in episode_ctx
        assert "hype_score" in episode_ctx
