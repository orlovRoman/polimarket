import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta
from core.models import Market
from core.context import MarketContext
from agents.polymarket_swing_agent.src.agent import SwingAgent

@pytest.mark.asyncio
async def test_swing_agent_value_mode_buy():
    market = Market(
        id="mkt-value-test-1",
        platform="polymarket",
        title="Will Micron Q3 adjusted gross margin be below 75%?",
        url="https://polymarket.com/micron-test",
        outcome="",
        price=0.60,  # Высокая цена (больше 0.22) - обычный свинг отменил бы
        close_time=datetime.now(timezone.utc) + timedelta(days=10),
        condition_id="cond-value-test-1",
        description="Resolves via official report: https://example.com/micron-report"
    )

    # Инициализируем контекст с успешным скрапингом оракула (жесткие факты)
    context = MarketContext(
        market=market,
        oracle_page_text="Micron adjusted gross margin was 73.8% officially."
    )

    agent = SwingAgent(api_key="fake_key")

    # Мокаем вызовы к RAG, эпизодам и LLM
    with patch("agents.shared.utils.rag.get_rag_context", return_value="RAG context"), \
         patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value="Perf summary"), \
         patch("agents.shared.utils.gemini_client.generate_content_with_fallback") as mock_gen:
        
        # Мокаем ответ LLM, возвращающий JSON с has_hard_facts = True и высокой уверенностью
        mock_result = MagicMock()
        mock_response_text = """
        {
            "target_outcome": "YES",
            "target_exit_price": 0.99,
            "reasoning": "Официальные данные оракула подтверждают маржу 73.8%, что ниже 75%.",
            "catalyst": "Официальный отчет Micron",
            "catalyst_absence_reason": "",
            "swing_risk": "Низкий риск",
            "swing_verdict": "Покупаем YES, так как маржа гарантированно ниже 75%",
            "llm_confidence": 0.95,
            "llm_direction": "YES",
            "contrarian_case": "Ошибки в отчете отсутствуют.",
            "asymmetry_score": 0.90,
            "has_hard_facts": true
        }
        """
        # Мокаем extract_response_text
        with patch("agents.shared.utils.gemini_client.extract_response_text", return_value=mock_response_text):
            mock_gen.return_value = (mock_result, "gemini-2.5-flash")

            signal = await agent.estimate_market(context)

            assert signal is not None
            assert signal.recommendation == "buy"
            assert signal.confidence == 0.95
            assert signal.target_outcome == "YES"
            assert "ВХОДИТЬ" in signal.swing_verdict
            assert signal.hype_potential < 0.1  # хайп крайне низкий, но мы вошли благодаря Value-режиму!
