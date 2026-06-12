import pytest
from datetime import datetime, timezone, timedelta
from agents.polymarket_arbitrage_agent.src.temporal.loader import PolyEvent, EventMarket
from agents.polymarket_arbitrage_agent.src.temporal.detector import find_candidates, compute_quality_score

def test_find_candidates_hormuz_example():
    now = datetime.now(timezone.utc)
    
    # Ранний рынок (конец мая)
    early = EventMarket(
        market_id="1",
        question="Hormuz by May 31",
        price_yes=0.30,  # p_early
        price_no=0.70,
        volume=10000,
        close_time=now + timedelta(days=20),
        token_yes="t1", token_no="t2"
    )
    
    # Поздний рынок (конец июня)
    late = EventMarket(
        market_id="2",
        question="Hormuz by June 30",
        price_yes=0.20,  # p_late (цена YES(late) дешевле YES(early) — явная неэффективность)
        price_no=0.80,
        volume=10000,
        close_time=now + timedelta(days=50),
        token_yes="t3", token_no="t4"
    )
    
    event = PolyEvent(event_slug="hormuz-test", event_title="Hormuz closure", markets=[early, late])
    
    candidates = find_candidates(
        events=[event],
        min_date_gap_days=14,
        min_theoretical_spread_pct=1.0,
        min_volume=5000,
    )
    
    assert len(candidates) == 1
    c = candidates[0]
    
    assert c.date_gap_days == 30
    assert c.p_early == 0.30
    assert c.p_late == 0.20
    assert c.p_in_corridor == -0.10  # инверсия, но нас это устраивает для spread'а
    
    # no_cost = 1 - p_early = 0.70
    # yes_cost = p_late = 0.20
    # cost = 0.90
    assert c.no_cost == 0.70
    assert c.yes_cost == 0.20
    assert c.theoretical_cost == 0.90
    assert c.theoretical_spread_pct == 10.0  # 1.0 - 0.90 = 0.10 -> 10%


def test_quality_score():
    # Идеальный сигнал
    score = compute_quality_score(
        real_spread_pct=6.0,  # > 5.0 -> 1.0
        date_gap_days=45,     # [30, 90] -> 1.0
        executable_contracts=100.0, # > 50 -> 1.0
        p_in_corridor=0.4,    # > 0.3 -> 1.0
        min_executable=50.0   # явно передаем старый дефолт для прохождения теста
    )
    assert score == pytest.approx(1.0)
    
    # Средний сигнал
    score2 = compute_quality_score(
        real_spread_pct=2.5,  # 0.5
        date_gap_days=15,     # 15/30 = 0.5
        executable_contracts=25.0, # 0.5
        p_in_corridor=0.15,   # 0.5
        min_executable=50.0   # явно передаем старый дефолт для прохождения теста
    )
    # 0.35*0.5 + 0.25*0.5 + 0.25*0.5 + 0.15*0.5 = 0.5
    assert score2 == pytest.approx(0.5)

