import pytest
from datetime import datetime
from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import PolyEvent, OutcomeMarket
from agents.polymarket_arbitrage_agent.src.synthetic.detector import find_violations

def test_find_violations():
    # Искусственные данные: Anthropic $1.5T vs $1.75T
    lower_market = OutcomeMarket(
        market_id="1",
        question="Anthropic > $1.5T",
        price_yes=0.80, # 80c
        price_no=0.20,
        volume=15000,
        end_date=datetime.now(),
        token_yes="t1",
        token_no="t2",
        numeric_level=1.5,
        level_unit="T"
    )
    
    upper_market = OutcomeMarket(
        market_id="2",
        question="Anthropic > $1.75T",
        price_yes=0.90, # 90c - НАШЛИ НАРУШЕНИЕ МОНОТОННОСТИ (бОльший порог стоит ДОРОЖЕ меньшего)
        price_no=0.10,
        volume=15000,
        end_date=datetime.now(),
        token_yes="t3",
        token_no="t4",
        numeric_level=1.75,
        level_unit="T"
    )
    
    event = PolyEvent(
        event_slug="anthropic-valuation",
        event_title="Anthropic Valuation",
        event_url="",
        markets=[lower_market, upper_market]
    )
    
    violations = find_violations([event], min_spread_pct=0.5, min_volume_both=1000)
    
    assert len(violations) == 1
    v = violations[0]
    
    assert v.lower.market_id == "1"
    assert v.upper.market_id == "2"
    
    # price_yes_lower = 0.80, price_no_upper = 0.10
    # cost = 0.90 -> 10% spread
    assert v.theoretical_cost == 0.90
    assert v.theoretical_spread_pct == 10.0
    
    assert v.pnl_above_upper == 0.10
    assert v.pnl_in_corridor == 1.10
    assert v.pnl_below_lower == 0.10
    assert v.guaranteed_pnl == 0.10


def test_no_violations():
    # Нормальный рынок
    lower_market = OutcomeMarket(
        market_id="1",
        question="Anthropic > $1.5T",
        price_yes=0.80, 
        price_no=0.20,
        volume=15000,
        end_date=datetime.now(),
        token_yes="t1",
        token_no="t2",
        numeric_level=1.5,
        level_unit="T"
    )
    
    upper_market = OutcomeMarket(
        market_id="2",
        question="Anthropic > $1.75T",
        price_yes=0.50, # 50c - Нормально, меньше чем 80c
        price_no=0.50,
        volume=15000,
        end_date=datetime.now(),
        token_yes="t3",
        token_no="t4",
        numeric_level=1.75,
        level_unit="T"
    )
    
    event = PolyEvent(
        event_slug="anthropic-valuation",
        event_title="Anthropic Valuation",
        event_url="",
        markets=[lower_market, upper_market]
    )
    
    violations = find_violations([event], min_spread_pct=0.5, min_volume_both=1000)
    
    assert len(violations) == 0


if __name__ == "__main__":
    pytest.main(["-v", __file__])
