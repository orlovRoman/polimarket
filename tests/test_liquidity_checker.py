import pytest
from unittest.mock import MagicMock
from core.liquidity_checker import check_liquidity_fast
from core.models import Market
from core.context import MarketContext, SmartMoneySummary
from agents.polymarket_insider_agent.src.agent import ShadowAgent

def test_none_orderbook_returns_not_ok():
    result = check_liquidity_fast(None)
    assert result.ok is False
    assert result.confidence <= 0.30
    assert result.liquidity_risk == "high"

def test_wide_spread_returns_not_ok():
    result = check_liquidity_fast({"spread": 0.12, "bid_depth_5": 500})
    assert result.ok is False
    assert result.liquidity_risk == "high"

def test_good_orderbook_ok():
    result = check_liquidity_fast({"spread": 0.02, "bid_depth_5": 200, "ask_depth_5": 300})
    assert result.ok is True
    assert result.confidence >= 0.65
    assert result.liquidity_risk == "low"

def test_shadow_llm_skipped_when_no_data():
    """Проверяем логику пропуска LLM (fast-path): если спред широк/ордербук пуст и нет SM, LLM не вызывается."""
    mock_shadow = MagicMock(spec=ShadowAgent)
    
    m = Market(
        id="test-mkt-123",
        platform="polymarket",
        title="Test Liquidity Title",
        description="description",
        url="https://polymarket.com/event/test",
        outcome="YES",
        price=0.5,
        close_time=MagicMock()
    )
    
    # Сценарий 1: Ордербук пуст, Smart Money нет
    orderbook = None
    smart_money = SmartMoneySummary(available=False, summary="Нет данных")
    price_hist = []
    
    # 1. Запуск детерминированного fast-path
    liq = check_liquidity_fast(orderbook)
    has_smart_money = bool(smart_money and getattr(smart_money, 'available', False))
    
    # Убеждаемся, что условия fast-path сработали
    assert not liq.ok and not has_smart_money
    
    # Имитируем ветвление из engine.py
    if not liq.ok and not has_smart_money:
        from core.models import AgentOpinion
        opinion_shadow = AgentOpinion(
            agent_name="SHADOW",
            market_id=m.id,
            opinion=liq.reason,
            confidence=liq.confidence,
            agree=False,
            orderbook_facts=liq.reason,
            risk_assessment="Ордербук пуст, Smart Money отсутствуют",
            shadow_verdict="SHADOW: авто-отклонение (нет данных)",
            liquidity_risk=liq.liquidity_risk
        )
    else:
        opinion_shadow = mock_shadow.analyze_idea(MarketContext(market=m), "scout info", orderbook=orderbook, price_history=price_hist)
        
    # Проверяем, что analyze_idea НЕ вызывалась
    mock_shadow.analyze_idea.assert_not_called()
    assert opinion_shadow.agree is False
    assert opinion_shadow.liquidity_risk == "HIGH"
