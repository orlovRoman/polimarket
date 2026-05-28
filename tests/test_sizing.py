import pytest
from agents.polymarket_arbitrage_agent.src.synthetic.sizing import compute_sizing

def test_sizing_symmetric_prices():
    # Симметричные цены, равные шансы на проигрыш одной из ног вне коридора
    # ask_yes_lower = 0.40, ask_no_upper = 0.40 -> real_cost = 0.80 -> spread = 20%
    result = compute_sizing(
        ask_yes_lower=0.40,
        ask_no_upper=0.40,
        budget=100.0,
        max_single_leg_pct=0.6
    )
    
    # Бюджет делится пополам -> по 50 USD на ногу
    # Contracts = 50 / 0.4 = 125
    # total_invested = 100 USD
    assert result["contracts_lower"] == 125.0
    assert result["contracts_upper"] == 125.0
    assert result["total_invested_usd"] == 100.0
    
    # PnL вне коридора должен быть одинаковым
    assert result["pnl_above_upper_usd"] == result["pnl_below_lower_usd"]
    # payout = 125 * 1.0 = 125, profit = 125 - 100 = 25
    assert result["pnl_above_upper_usd"] == 25.0
    assert result["min_guaranteed_usd"] == 25.0

def test_sizing_corridor_gives_maximum():
    # Цены, где коридор даёт максимум. Асимметричные цены.
    # lower стоит 0.20 (дешево), upper стоит 0.60 (дорого)
    # real_cost = 0.80
    result = compute_sizing(
        ask_yes_lower=0.20,
        ask_no_upper=0.60,
        budget=100.0,
        max_single_leg_pct=0.6
    )
    
    # Бюджет 100. stake_per_leg = 50. max_single_leg_pct=0.6 (60)
    # contracts_lower = 50 / 0.20 = 250
    # contracts_upper = 50 / 0.60 = 83.333
    # target_contracts = min(250, 83.333) = 83.333
    
    target = min(50/0.20, 50/0.60)
    expected_invested_lower = target * 0.20
    expected_invested_upper = target * 0.60
    total_invest = expected_invested_lower + expected_invested_upper
    
    assert result["contracts_lower"] == pytest.approx(target, 0.01)
    
    # Проверка выплат
    # pnl_above (YES lower wins)
    expected_pnl_above = target * 1.0 - total_invest
    
    # pnl_below (NO upper wins)
    expected_pnl_below = target * 1.0 - total_invest
    
    # pnl_corridor (BOTH win)
    expected_pnl_corridor = target * 2.0 - total_invest
    
    assert result["pnl_above_upper_usd"] == pytest.approx(expected_pnl_above, 0.01)
    assert result["pnl_below_lower_usd"] == pytest.approx(expected_pnl_below, 0.01)
    assert result["pnl_in_corridor_usd"] == pytest.approx(expected_pnl_corridor, 0.01)
    
    # Коридор дает максимальную прибыль
    assert result["max_win_usd"] == pytest.approx(expected_pnl_corridor, 0.01)
    assert result["max_win_usd"] > result["pnl_above_upper_usd"]
    
    # Гарантированный минимум - это любой из исходов вне коридора
    assert result["min_guaranteed_usd"] == pytest.approx(expected_pnl_below, 0.01)
