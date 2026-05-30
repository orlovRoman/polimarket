import pytest
from agents.polymarket_arbitrage_agent.src.temporal.sizing import compute_sizing

def test_compute_sizing_temporal():
    # Цены
    # p_early = 0.30, p_late = 0.40 -> p_in_corridor = 0.10
    # ask_no_early = 0.70, ask_yes_late = 0.40
    # real_cost = 1.10 -> spread = -10% (нет арбитража, но математика должна работать)
    
    # Для арбитража возьмем: p_early = 0.50, p_late = 0.30
    # ask_no_early = 0.50 (1 - 0.50), ask_yes_late = 0.30
    # real_cost = 0.80 -> spread = 20%
    result = compute_sizing(
        ask_no_early=0.50,
        ask_yes_late=0.30,
        p_before=0.50,
        p_in_corridor=-0.20, # инверсия, маловероятный сценарий, берем как есть для теста
        p_never=0.70,
        budget=200.0
    )
    
    assert result["real_spread_pct"] == pytest.approx(20.0)
    
    # budget = 200 -> 100 per leg
    # contracts_no = 100 / 0.50 = 200
    # contracts_yes = 100 / 0.30 = 333.33
    # min contracts = 200
    
    assert result["early_contracts"] == pytest.approx(200.0)
    assert result["late_contracts"] == pytest.approx(200.0)
    
    # total_invested = 200 * 0.50 + 200 * 0.30 = 100 + 60 = 160
    assert result["total_invested"] == pytest.approx(160.0)
    
    # PnL S1 (анонс до early)
    # pnl_s1 = 200 * spread = 200 * 0.20 = 40.0
    assert result["pnl_s1_before_early"] == pytest.approx(40.0)
    
    # PnL S3 (нет анонса)
    # pnl_s3 = 200 * spread = 200 * 0.20 = 40.0
    assert result["pnl_s3_never"] == pytest.approx(40.0)
    
    # PnL S2 (коридор)
    # 200 * (2.0 - 0.80) = 240.0
    assert result["pnl_s2_in_corridor"] == pytest.approx(240.0)
    
    # Min / Max PnL
    assert result["min_pnl"] == pytest.approx(40.0)
    assert result["max_pnl"] == pytest.approx(240.0)
