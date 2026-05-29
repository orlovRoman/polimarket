import pytest
from core.models import Market, SwingSignal, AgentOpinion, MarketCorrelation
from datetime import datetime, timezone

def test_market_price_clamp_threshold():
    """Market: цена >= 2.0 конвертируется (центы), цена < 2.0 (например 1.5) зажимается до 1.0"""
    m1 = Market(id="1", platform="p", title="t", url="u", outcome="YES", price=1.5, close_time=datetime.now(timezone.utc))
    assert m1.price == 1.0

    m2 = Market(id="2", platform="p", title="t", url="u", outcome="YES", price=2.0, close_time=datetime.now(timezone.utc))
    assert m2.price == 0.02

    m3 = Market(id="3", platform="p", title="t", url="u", outcome="YES", price=65.0, close_time=datetime.now(timezone.utc))
    assert m3.price == 0.65

def test_swing_signal_confidence_clamp_threshold():
    """SwingSignal: confidence >= 2.0 конвертируется, < 2.0 (например 1.5) зажимается до 1.0"""
    sw1 = SwingSignal(
        id="1", market_id="m", platform="p", type="SWING", hype_potential=0.5,
        recommendation="buy", target_outcome="YES", target_exit_price=0.75,
        confidence=1.5, reasoning="test",
    )
    assert sw1.confidence == 1.0

    sw2 = SwingSignal(
        id="2", market_id="m", platform="p", type="SWING", hype_potential=0.5,
        recommendation="buy", target_outcome="YES", target_exit_price=0.75,
        confidence=2.0, reasoning="test",
    )
    assert sw2.confidence == 0.02

    sw3 = SwingSignal(
        id="3", market_id="m", platform="p", type="SWING", hype_potential=0.5,
        recommendation="buy", target_outcome="YES", target_exit_price=0.75,
        confidence=95.0, reasoning="test",
    )
    assert sw3.confidence == 0.95

def test_swing_signal_hype_potential_clamp_threshold():
    sw = SwingSignal(
        id="1", market_id="m", platform="p", type="SWING", hype_potential=1.5,
        recommendation="buy", target_outcome="YES", target_exit_price=0.75,
        confidence=0.5, reasoning="test",
    )
    assert sw.hype_potential == 1.0

def test_agent_opinion_confidence_clamp_threshold():
    """AgentOpinion: confidence >= 2.0 конвертируется, < 2.0 зажимается до 1.0"""
    op1 = AgentOpinion(
        agent_name="SHADOW", market_id="1", opinion="ok", confidence=1.5, agree=True
    )
    assert op1.confidence == 1.0

    op2 = AgentOpinion(
        agent_name="SHADOW", market_id="1", opinion="ok", confidence=2.0, agree=True
    )
    assert op2.confidence == 0.02

def test_market_correlation_confidence_clamp():
    """MarketCorrelation: confidence имеет валидатор и порог >= 2.0"""
    c1 = MarketCorrelation(
        market_id_a="1", market_id_b="2", title_a="A", title_b="B",
        correlation_type="thematic", description="desc", confidence=1.5
    )
    assert c1.confidence == 1.0

    c2 = MarketCorrelation(
        market_id_a="1", market_id_b="2", title_a="A", title_b="B",
        correlation_type="thematic", description="desc", confidence=2.0
    )
    assert c2.confidence == 0.02

    c3 = MarketCorrelation(
        market_id_a="1", market_id_b="2", title_a="A", title_b="B",
        correlation_type="thematic", description="desc", confidence=95.0
    )
    assert c3.confidence == 0.95
