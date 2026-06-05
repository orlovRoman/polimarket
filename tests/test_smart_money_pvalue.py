import pytest
from unittest.mock import patch, MagicMock
from services.wallet_tracker import recalculate_win_rates
from core.stats import binomial_pvalue as calculate_binomial_p_value

def test_calculate_binomial_p_value():
    p_val_15_15 = calculate_binomial_p_value(15, 15)
    assert p_val_15_15 < 0.0001
    
    import math
    assert math.isclose(calculate_binomial_p_value(15, 0), 1.0, rel_tol=1e-9)
    
    p_val_15_7 = calculate_binomial_p_value(15, 7)
    assert 0.68 < p_val_15_7 < 0.72

    p_val_20_15 = calculate_binomial_p_value(20, 15)
    assert p_val_20_15 < 0.05

    p_val_20_13 = calculate_binomial_p_value(20, 13)
    assert p_val_20_13 > 0.05

def test_recalculate_win_rates_insider_flag():
    mock_rows = [
        {"wallet_address": "insider_addr", "total": 20, "wins": 15, "total_vol": 10000.0},
        {"wallet_address": "lucky_but_few_trades", "total": 5, "wins": 5, "total_vol": 500.0},
        {"wallet_address": "regular_addr", "total": 20, "wins": 10, "total_vol": 2000.0}
    ]
    
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchall.return_value = mock_rows
    
    with patch("services.wallet_tracker.get_connection", return_value=mock_conn), \
         patch("services.wallet_tracker.update_wallet_pvalue") as mock_update_pvalue, \
         patch("services.wallet_tracker.update_wallet_stats") as mock_update_stats:
         
        recalculate_win_rates()
        
        assert mock_update_stats.call_count == 3
        assert mock_update_pvalue.call_count == 3
        
        calls = mock_update_stats.call_args_list
        assert calls[0][0][0] == "insider_addr"
        assert calls[0][0][1] == 0.75
        assert calls[0][0][3] is True

def test_wallet_tracker_uses_stats_module():
    """recalculate_win_rates должен использовать core.stats.binomial_pvalue, а не свою реализацию."""
    import services.wallet_tracker as wt
    # Проверяем, что дублирующие функции отсутствуют
    assert not hasattr(wt, 'binomial_coefficient'), \
        "BUG-WT-01: дублирующий binomial_coefficient должен быть удалён из wallet_tracker"
    assert not hasattr(wt, 'calculate_binomial_p_value'), \
        "BUG-WT-01: дублирующий calculate_binomial_p_value должен быть удалён из wallet_tracker"

def test_large_n_no_underflow():
    """При n=2000 старая реализация давала 0.0 из-за float underflow."""
    from core.stats import binomial_pvalue

    # 1200 побед из 2000 (60%) — должен быть очень малый, но ненулевой p-value
    pv = binomial_pvalue(2000, 1200, p0=0.5)
    assert 0.0 < pv < 1e-15, \
        f"BUG-WT-01 legacy: p-value при n=2000 k=1200 должен быть ~0, получено {pv}"

    # Старая реализация через прямое умножение (демонстрация underflow):
    def old_pvalue(n, k, p_base=0.5):
        if n <= 0 or k <= 0: return 1.0
        if k > n: return 0.0
        total = 0.0
        for i in range(k, n + 1):
            coef = 1
            for j in range(min(i, n-i)):
                coef = coef * (n - j) // (j + 1)
            total += coef * (p_base ** i) * ((1.0 - p_base) ** (n - i))
        return total

    with pytest.raises(OverflowError):
        old_pvalue(2000, 1200)
    assert pv > 0.0

def test_recalculate_win_rates_writes_n_wins():
    """После recalculate_win_rates n_wins должен быть записан в wallets."""
    rows_written = []

    def fake_update_wallet_pvalue(address, n_trades, n_wins, p_value, is_insider):
        rows_written.append({"address": address, "n_wins": n_wins, "n_trades": n_trades})

    with patch("services.wallet_tracker.update_wallet_pvalue", fake_update_wallet_pvalue), \
         patch("services.wallet_tracker.update_wallet_stats"), \
         patch("services.wallet_tracker.get_connection") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"wallet_address": "0xAAA", "total": 20, "wins": 17, "total_vol": 5000.0}
        ]
        mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

        recalculate_win_rates()

    assert len(rows_written) == 1
    assert rows_written[0]["n_wins"] == 17, \
        f"BUG-WT-02: n_wins должен быть записан через update_wallet_pvalue, получено {rows_written[0]}"
