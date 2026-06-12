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
    assert c.is_guaranteed_arbitrage is True


def test_quality_score():
    # Идеальный сигнал
    score = compute_quality_score(
        real_spread_pct=6.0,  # > 5.0 -> 1.0
        date_gap_days=45,     # [30, 90] -> 1.0
        executable_contracts=100.0, # > 10 -> 1.0
        p_in_corridor=0.4     # > 0.3 -> 1.0
    )
    assert score == pytest.approx(1.0)
    
    # Средний сигнал
    score2 = compute_quality_score(
        real_spread_pct=2.5,  # 0.5
        date_gap_days=15,     # 15/30 = 0.5
        executable_contracts=5.0,  # 5/10 = 0.5
        p_in_corridor=0.15    # 0.5
    )
    # 0.35*0.5 + 0.25*0.5 + 0.25*0.5 + 0.15*0.5 = 0.5
    assert score2 == pytest.approx(0.5)

    # Безрисковый арбитраж (p_in_corridor < 0) -> corridor_score = 1.0
    score_arb = compute_quality_score(
        real_spread_pct=6.0,  # > 5.0 -> 1.0
        date_gap_days=45,     # [30, 90] -> 1.0
        executable_contracts=100.0, # > 10 -> 1.0
        p_in_corridor=-0.1    # < 0 -> 1.0 (guaranteed arb)
    )
    assert score_arb == pytest.approx(1.0)


def test_save_temporal_corridor():
    from agents.shared.python.db import init_db, save_temporal_corridor, get_connection
    from agents.polymarket_arbitrage_agent.src.temporal.models import TemporalCorridorSignal, TemporalLeg
    from datetime import datetime, timezone

    init_db()

    early_leg = TemporalLeg(
        market_id="early_test",
        question="early?",
        market_url="http://early",
        expiry=datetime.now(timezone.utc),
        price_yes=0.7,
        ask_price=0.3,
        side="NO",
        entry_cost=0.3,
        token_id="token_early",
        volume=5000.0
    )

    late_leg = TemporalLeg(
        market_id="late_test",
        question="late?",
        market_url="http://late",
        expiry=datetime.now(timezone.utc),
        price_yes=0.55,
        ask_price=0.55,
        side="YES",
        entry_cost=0.55,
        token_id="token_late",
        volume=5000.0
    )

    signal = TemporalCorridorSignal(
        signal_id="early_test__late_test",
        event_slug="test-event",
        event_title="Test Event",
        event_url="http://test",
        early_leg=early_leg,
        late_leg=late_leg,
        date_gap_days=30,
        p_early=0.7,
        p_late=0.55,
        p_in_corridor=-0.15,
        p_before_early=0.7,
        p_never=0.45,
        theoretical_cost=0.85,
        real_cost=0.85,
        theoretical_spread_pct=15.0,
        real_spread_pct=15.0,
        pnl_s1_before_early=10.0,
        pnl_s2_in_corridor=100.0,
        pnl_s3_never=10.0,
        early_stake_usd=50.0,
        late_stake_usd=50.0,
        early_contracts=100.0,
        late_contracts=100.0,
        ev_usd=15.0,
        roi_pct=15.0,
        quality_score=0.9,
        exit_rule="exit",
        is_guaranteed_arbitrage=True,
        created_at=datetime.now(timezone.utc)
    )

    # Должно выполниться без ProgrammingError
    save_temporal_corridor(signal)

    # Проверим, что сохранилось корректно
    with get_connection() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM temporal_corridors WHERE signal_id = 'early_test__late_test'").fetchone()
        assert row is not None
        assert row["is_guaranteed_arbitrage"] == 1
        assert row["event_title"] == "Test Event"


