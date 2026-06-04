import asyncio
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from core.models import Market
from core.context import MarketContext
from agents.polymarket_swing_agent.src.agent import SwingAgent
from agents.polymarket_arbitrage_agent.src.agent import ArbitrageAgent

def make_market(title, price, close_time=None):
    if close_time is None:
        close_time = datetime(2026, 12, 31, tzinfo=timezone.utc)
    return Market(
        id="123",
        platform="polymarket",
        title=title,
        url="https://polymarket.com/test",
        outcome="YES",
        price=price,
        close_time=close_time,
    )

def test_swing_agent_prompt_contains_datetime():
    """Проверяет, что SwingAgent включает 'Сегодняшняя дата и время:' в промпт"""
    agent = SwingAgent(api_key="fake-key")
    market = make_market("Will Polymarket volume exceed 1B?", 0.80)
    context = MarketContext(
        market=market,
        news_titles=[],
        reddit_posts=[],
        wiki_context=[],
        trends_data="",
        hn_posts=[],
    )
    
    mock_result = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    # Мокаем with_retry / LLM вызов
    with patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""):
        with patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]):
            with patch("agents.shared.python.llm_wrapper.with_retry") as mock_retry:
                # Напрямую вызовем generate_content_with_fallback или сэмулируем
                with patch("agents.shared.utils.gemini_client.generate_content_with_fallback", return_value=(mock_result, "model")) as mock_gen:
                    asyncio.run(agent.estimate_market(context))
                    assert mock_gen.called
                    called_payload = mock_gen.call_args[1]["payload"]
                    prompt_text = called_payload["contents"][0]["parts"][0]["text"]
                    assert "Сегодняшняя дата и время:" in prompt_text

def test_arbitrage_agent_prompts_contain_datetime():
    """Проверяет, что ArbitrageAgent включает 'Сегодняшняя дата и время:' в оба промпта"""
    agent = ArbitrageAgent(api_key="fake-key")
    market_a = make_market("SpaceX IPO in 2026", 0.70)
    market_b = make_market("SpaceX IPO in 2026", 0.65)
    
    mock_result = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    
    with patch.object(agent, '_call_llm', return_value=(mock_result, "raw")) as mock_llm:
        # Для correlation с монотонностью (нужна неоднозначность, чтобы не сработал math filter)
        agent.analyze_correlation(market_a, market_b, correlation_type="threshold", score=80)
        assert mock_llm.called
        prompt_text = mock_llm.call_args[0][0]
        assert "Сегодняшняя дата и время:" in prompt_text
        assert "Закрытие:" in prompt_text
        
    mock_llm.reset_mock()
    
    with patch.object(agent, '_call_llm', return_value=(mock_result, "raw")) as mock_llm:
        # Для cross_platform
        agent.analyze_cross_platform(market_a, market_b, match_score=0.95)
        assert mock_llm.called
        prompt_text = mock_llm.call_args[0][0]
        assert "Сегодняшняя дата и время:" in prompt_text
