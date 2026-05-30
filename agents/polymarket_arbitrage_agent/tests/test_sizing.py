import pytest
from agents.polymarket_arbitrage_agent.src.synthetic.sizing import compute_sizing

def test_compute_sizing_profitable():
    # Ситуация: ask_yes = 0.8, ask_no = 0.1, budget = 200
    # real_cost = 0.9 (spread = 10%)
    res = compute_sizing(ask_yes_lower=0.8, ask_no_upper=0.1, budget=200)
    
    # Бюджет делится пополам: stake_lower=100, stake_upper=100.
    # contracts_lower = 100 / 0.8 = 125
    # contracts_upper = 100 / 0.1 = 1000
    # Берем min (125).
    # invested_lower = 125 * 0.8 = 100
    # invested_upper = 125 * 0.1 = 12.5
    # total_invested = 112.5
    
    assert res["contracts_lower"] == 125
    assert res["contracts_upper"] == 125
    assert res["stake_lower_usd"] == 100.0
    assert res["stake_upper_usd"] == 12.5
    assert res["total_invested_usd"] == 112.5
    
    # Сценарий: above
    # PnL = 125 * 1.0 - 112.5 = 12.5
    assert res["pnl_above_upper_usd"] == 12.5
    
    # Сценарий: below
    # PnL = 125 * 1.0 - 112.5 = 12.5
    assert res["pnl_below_lower_usd"] == 12.5
    
    # Сценарий: corridor
    # PnL = 250 * 1.0 - 112.5 = 137.5
    assert res["pnl_in_corridor_usd"] == 137.5
    
    assert res["min_guaranteed_usd"] == 12.5
    assert res["roi_min_pct"] == round((12.5 / 112.5) * 100, 2)


def test_compute_sizing_loss():
    # Ситуация, когда спреда нет (нет нарушения монотонности)
    # ask_yes = 0.8, ask_no = 0.3
    # cost = 1.1 (мы заплатим больше, чем получим при 1 исходе)
    res = compute_sizing(ask_yes_lower=0.8, ask_no_upper=0.3, budget=200)
    
    # stake = 100. contracts_lower = 125, contracts_upper = 333.3
    # target_contracts = 125
    # invested = 125 * 0.8 + 125 * 0.3 = 100 + 37.5 = 137.5
    assert res["contracts_lower"] == 125
    
    # PnL above: 125 - 137.5 = -12.5
    assert res["min_guaranteed_usd"] < 0
    assert res["roi_min_pct"] < 0

if __name__ == "__main__":
    pytest.main(["-v", __file__])
