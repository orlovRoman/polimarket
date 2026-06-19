import pytest
from agents.shared.python.db import _calculate_outcome_pnl, _calculate_whale_confidence_and_volume

def test_calculate_outcome_pnl_yes_win():
    # outcome YES, actual YES, v_bought=0.1, v_sold=1.0
    b_out, s_out, cents, percent = _calculate_outcome_pnl('YES', 0.1, 1.0)
    assert b_out == 0.1
    assert s_out == 1.0
    assert cents == 0.90
    assert percent == 900.0

def test_calculate_outcome_pnl_no_win():
    # outcome NO, actual NO, v_bought=0.9, v_sold=0.0
    b_out, s_out, cents, percent = _calculate_outcome_pnl('NO', 0.9, 0.0)
    # bought_outcome for NO is (1 - v_bought) = 0.1
    assert b_out == pytest.approx(0.1)
    # v_sold is what happened to YES share. NO won, so YES is 0. 
    # sold_outcome = 1 - 0 = 1.0
    assert s_out == 1.0
    assert cents == pytest.approx(0.90)
    assert percent == pytest.approx(900.0)

def test_calculate_outcome_pnl_yes_loss():
    b_out, s_out, cents, percent = _calculate_outcome_pnl('YES', 0.5, 0.0)
    assert b_out == 0.5
    assert s_out == 0.0
    assert cents == -0.50
    assert percent == -100.0

def test_calculate_outcome_pnl_no_loss():
    b_out, s_out, cents, percent = _calculate_outcome_pnl('NO', 0.2, 1.0)
    # bought_outcome for NO is (1 - 0.2) = 0.8
    assert b_out == pytest.approx(0.8)
    assert s_out == 0.0
    assert cents == -0.80
    assert percent == -100.0

def test_calculate_whale_confidence():
    dirs = [
        {"side": "YES", "amount_usd": 100},
        {"side": "NO", "amount_usd": 50},
        {"side": "YES", "amount_usd": 50}
    ]
    conf, yes_vol, no_vol = _calculate_whale_confidence_and_volume(dirs, 0.5)
    assert yes_vol == 150
    assert no_vol == 50
    # max(yes, no) / total = 150 / 200 = 0.75
    # Since ratio 0.75 >= base_conf 0.5, we just use it or something else? Wait. The logic is:
    # return max(0.5, ...). 
    # Let me just check the return values
    assert conf == pytest.approx(0.75)

def test_calculate_whale_confidence_empty():
    dirs = []
    conf, yes_vol, no_vol = _calculate_whale_confidence_and_volume(dirs, 0.5)
    assert yes_vol == 0
    assert no_vol == 0
    assert conf == 0.5
