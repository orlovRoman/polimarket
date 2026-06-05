import pytest
from unittest.mock import patch, MagicMock
from services.wallet_tracker import calculate_binomial_p_value, recalculate_win_rates

def test_calculate_binomial_p_value():
    # p-value для 15 успехов из 15 испытаний при p_base=0.5 должно быть очень малым (0.5^15 = 3e-5)
    p_val_15_15 = calculate_binomial_p_value(15, 15)
    assert p_val_15_15 < 0.0001
    
    # p-value для 0 успехов из 15 испытаний (с k <= 0 возвращает 1.0)
    assert calculate_binomial_p_value(15, 0) == 1.0
    
    # p-value для 7 успехов из 15 испытаний (вероятность получить >= 7 успехов довольно велика, ~0.69)
    p_val_15_7 = calculate_binomial_p_value(15, 7)
    assert 0.68 < p_val_15_7 < 0.72

    # p-value для n=20, k=15
    p_val_20_15 = calculate_binomial_p_value(20, 15)
    # Сумма по i от 15 до 20 C(20, i) * 0.5^20
    # C(20, 15) = 15504, C(20, 16) = 4845, C(20,17) = 1140, C(20,18)=190, C(20,19)=20, C(20,20)=1
    # Сумма = 21700. 21700 / 2^20 = 21700 / 1048576 = 0.02069 < 0.05
    assert p_val_20_15 < 0.05

    # p-value для n=20, k=13
    # Сумма по i от 13 до 20 C(20, i) * 0.5^20
    # Будет значительно больше 0.05
    p_val_20_13 = calculate_binomial_p_value(20, 13)
    assert p_val_20_13 > 0.05

def test_recalculate_win_rates_insider_flag():
    # Мокаем get_connection, чтобы симулировать выдачу транзакций кошельков
    # Один кошелек с 20 сделками и 15 победами (должен получить is_insider = True)
    # Другой кошелек с 20 сделками и 10 победами (должен получить is_insider = False)
    # Третий кошелек с 5 сделками и 5 победами (win_rate 100%, но сделок < 15, должен получить is_insider = False)
    
    mock_rows = [
        {"wallet_address": "insider_addr", "total": 20, "wins": 15, "total_vol": 10000.0},
        {"wallet_address": "lucky_but_few_trades", "total": 5, "wins": 5, "total_vol": 500.0},
        {"wallet_address": "regular_addr", "total": 20, "wins": 10, "total_vol": 2000.0}
    ]
    
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchall.return_value = mock_rows
    
    with patch("services.wallet_tracker.get_connection", return_value=mock_conn), \
         patch("services.wallet_tracker.update_wallet_stats") as mock_update_stats:
         
        recalculate_win_rates()
        
        # Проверяем, что update_wallet_stats был вызван 3 раза с правильными параметрами is_insider
        assert mock_update_stats.call_count == 3
        
        calls = mock_update_stats.call_args_list
        # calls[i] = call(address, win_rate, total_vol, is_insider)
        
        # insider_addr
        assert calls[0][0][0] == "insider_addr"
        assert calls[0][0][1] == 0.75  # 15/20
        assert calls[0][0][2] == 10000.0
        assert calls[0][0][3] is True  # is_insider
        
        # lucky_but_few_trades
        assert calls[1][0][0] == "lucky_but_few_trades"
        assert calls[1][0][1] == 1.0
        assert calls[1][0][2] == 500.0
        assert calls[1][0][3] is False  # is_insider (trades < 15)
        
        # regular_addr
        assert calls[2][0][0] == "regular_addr"
        assert calls[2][0][1] == 0.50
        assert calls[2][0][2] == 2000.0
        assert calls[2][0][3] is False  # is_insider (p-value >= 0.05)
