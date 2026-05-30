import pytest
from datetime import datetime, timezone
from core.workflow import make_consensus, MarketContext
from core.models import Market, Signal, SwingSignal, AgentOpinion

def _make_market():
    return Market(
        id="test_market_123",
        platform="polymarket",
        title="Test Market",
        url="http://test",
        outcome="YES",
        price=0.5,
        close_time=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )

def test_consensus_with_all_none_agents():
    ctx = MarketContext(market=_make_market(), news_titles=[], reddit_posts=[], wiki_context=[])
    
    # Консенсус при None-результатах от всех агентов
    decision = make_consensus(ctx, signal=None, swing_signal=None, opinion_shadow=None)
    
    assert decision.status == "no_signal"
    assert decision.scout_signal is None
    assert decision.swing_signal is None
    assert decision.shadow_opinion is None

def test_consensus_when_scout_ok_but_shadow_none():
    ctx = MarketContext(market=_make_market(), news_titles=[], reddit_posts=[], wiki_context=[])
    fake_signal = Signal(
        id="sig-1", type="MISPRICING", market_id="m", platform="polymarket",
        edge=0.1, signal_cause="test", signal_risk="low", priority="medium", summary="test",
        signal_verdict="buy", target_outcome="YES", details="test", confidence=0.8
    )
    
    decision = make_consensus(ctx, signal=fake_signal, swing_signal=None, opinion_shadow=None)
    
    # Scout нашел идею, но Shadow вернул None (упал)
    assert decision.status == "no_consensus"

def test_consensus_when_all_agree():
    ctx = MarketContext(market=_make_market(), news_titles=[], reddit_posts=[], wiki_context=[])
    fake_signal = Signal(
        id="sig-1", type="MISPRICING", market_id="m", platform="polymarket",
        edge=0.1, signal_cause="test", signal_risk="low", priority="medium", summary="test",
        signal_verdict="buy", target_outcome="YES", details="test", confidence=0.8
    )
    fake_swing = SwingSignal(
        id="sw-1", market_id="m", platform="polymarket",
        recommendation="buy", catalyst="test", confidence=0.8, details="test",
        hype_potential=0.8, target_outcome="YES", target_exit_price=0.8, reasoning="test"
    )
    fake_shadow = AgentOpinion(
        agent_name="SHADOW", opinion="Looks good", confidence=0.9, agree=True
    )
    
    decision = make_consensus(ctx, signal=fake_signal, swing_signal=fake_swing, opinion_shadow=fake_shadow)
    
    assert decision.status == "saved"
